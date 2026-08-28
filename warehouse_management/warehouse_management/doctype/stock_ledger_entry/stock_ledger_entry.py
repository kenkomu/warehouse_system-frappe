# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime, now_datetime

from warehouse_management.warehouse_management.valuation import (
	MOVING_AVERAGE,
	get_item_valuation_method,
	replay,
)


class StockLedgerEntry(Document):
	def validate(self):
		if not self.posting_datetime:
			self.posting_datetime = now_datetime()

		if not flt(self.actual_qty):
			frappe.throw(_("Actual Qty cannot be zero."))

		if flt(self.actual_qty) < 0:
			# Outgoing rows are valued from the moving average, never from an input rate.
			self.incoming_rate = 0

		if frappe.db.get_value("Warehouse", self.warehouse, "is_group"):
			frappe.throw(
				_("{0} is a group warehouse. Stock cannot be booked against it.").format(self.warehouse)
			)


def get_stock_balance(item: str, warehouse: str, upto: str | None = None) -> float:
	"""Balance quantity for an item/warehouse, optionally as of a datetime.

	Stateless: derived by summing the ledger rather than reading a stored balance.
	"""
	values = {"item": item, "warehouse": warehouse}
	upto_clause = ""
	if upto:
		upto_clause = "AND posting_datetime <= %(upto)s"
		values["upto"] = upto

	row = frappe.db.sql(
		f"""
		SELECT SUM(actual_qty) AS qty
		FROM `tabStock Ledger Entry`
		WHERE item = %(item)s
			AND warehouse = %(warehouse)s
			AND is_cancelled = 0
			{upto_clause}
		""",
		values,
		as_dict=True,
	)

	return flt(row[0].qty) if row else 0.0


def get_moving_average_rate(item: str, warehouse: str, upto: str | None = None) -> float:
	"""Moving average valuation rate for an item/warehouse as of a datetime.

	The weighted average cost of everything received so far:

	    rate = SUM(actual_qty * incoming_rate) / SUM(actual_qty)   over incoming rows

	This is the whole calculation, in one query -- no running state to maintain and
	nothing to rewrite when an entry is posted out of order.
	"""
	values = {"item": item, "warehouse": warehouse}
	upto_clause = ""
	if upto:
		upto_clause = "AND posting_datetime <= %(upto)s"
		values["upto"] = upto

	row = frappe.db.sql(
		f"""
		SELECT
			CASE
				WHEN SUM(actual_qty) > 0
				THEN SUM(actual_qty * incoming_rate) / SUM(actual_qty)
				ELSE 0
			END AS valuation_rate
		FROM `tabStock Ledger Entry`
		WHERE item = %(item)s
			AND warehouse = %(warehouse)s
			AND is_cancelled = 0
			AND actual_qty > 0
			{upto_clause}
		""",
		values,
		as_dict=True,
	)

	return flt(row[0].valuation_rate) if row else 0.0


def get_first_negative_balance(
	item: str,
	warehouse: str,
	extra_rows: list[dict] | None = None,
	exclude_voucher: tuple[str, str] | None = None,
) -> dict | None:
	"""First point in time where an item/warehouse runs a negative balance.

	Returns the offending posting datetime and balance, or None if the timeline
	is sound throughout.

	A balance taken at a single moment cannot catch this: backdating an issue, or
	cancelling a receipt that has since been drawn down, leaves *later* entries
	overdrawn while every individual entry still looked valid when it was posted.
	Walking the whole timeline is what closes that gap.

	`extra_rows` folds in movements that are not in the ledger yet and
	`exclude_voucher` drops one voucher's rows, so a caller can test the shape of
	a change before committing to it.
	"""
	values = {"item": item, "warehouse": warehouse}
	exclude_clause = ""
	if exclude_voucher:
		exclude_clause = "AND NOT (voucher_type = %(voucher_type)s AND voucher_no = %(voucher_no)s)"
		values["voucher_type"], values["voucher_no"] = exclude_voucher

	rows = frappe.db.sql(
		f"""
		SELECT posting_datetime, actual_qty
		FROM `tabStock Ledger Entry`
		WHERE item = %(item)s
			AND warehouse = %(warehouse)s
			AND is_cancelled = 0
			{exclude_clause}
		ORDER BY posting_datetime ASC, creation ASC
		""",
		values,
		as_dict=True,
	)

	timeline = [
		{"posting_datetime": get_datetime(row.posting_datetime), "actual_qty": flt(row.actual_qty)}
		for row in rows
	]
	timeline += [
		{"posting_datetime": get_datetime(row["posting_datetime"]), "actual_qty": flt(row["actual_qty"])}
		for row in extra_rows or []
	]

	# Stable, so rows already in the ledger keep their place against pending rows
	# posted at the same instant.
	timeline.sort(key=lambda row: row["posting_datetime"])

	balance = 0.0
	for row in timeline:
		balance += row["actual_qty"]
		if balance < 0:
			return {"posting_datetime": row["posting_datetime"], "balance": balance}

	return None


def get_ledger_rows(item: str, warehouse: str, upto: str | None = None) -> list[dict]:
	"""Live ledger rows for an item/warehouse in posting order.

	FIFO and LIFO cannot be reduced to an aggregate the way a weighted average
	can, because which units are still on hand decides what they are worth. They
	need the movements themselves, replayed in order.
	"""
	values = {"item": item, "warehouse": warehouse}
	upto_clause = ""
	if upto:
		upto_clause = "AND posting_datetime <= %(upto)s"
		values["upto"] = upto

	return frappe.db.sql(
		f"""
		SELECT actual_qty, incoming_rate
		FROM `tabStock Ledger Entry`
		WHERE item = %(item)s
			AND warehouse = %(warehouse)s
			AND is_cancelled = 0
			{upto_clause}
		ORDER BY posting_datetime ASC, creation ASC
		""",
		values,
		as_dict=True,
	)


def get_valuation_rate(
	item: str, warehouse: str, upto: str | None = None, method: str | None = None
) -> float:
	"""Cost per unit of the stock on hand, under the item's configured method.

	Moving Average keeps its single-query path; the layered methods replay.
	"""
	method = method or get_item_valuation_method(item)

	if method == MOVING_AVERAGE:
		return get_moving_average_rate(item, warehouse, upto)

	return flt(replay(get_ledger_rows(item, warehouse, upto), method).rate)


def get_outgoing_rate(
	item: str, warehouse: str, qty, upto: str | None = None, method: str | None = None
) -> float:
	"""Cost per unit of removing qty from a warehouse.

	This is not the same question as `get_valuation_rate`. Under Moving Average
	it is, because every unit leaves at the average. Under FIFO and LIFO the cost
	of what leaves is decided by the layers the draw reaches, which is generally
	not the rate of what stays behind.
	"""
	method = method or get_item_valuation_method(item)

	if method == MOVING_AVERAGE:
		return get_moving_average_rate(item, warehouse, upto)

	state = replay(get_ledger_rows(item, warehouse, upto), method)
	return flt(state.outgoing_rate(qty))


def get_stock_value(item: str, warehouse: str, upto: str | None = None) -> float:
	"""Value of the stock on hand, under the item's configured method."""
	method = get_item_valuation_method(item)

	if method == MOVING_AVERAGE:
		return flt(get_stock_balance(item, warehouse, upto)) * flt(
			get_moving_average_rate(item, warehouse, upto)
		)

	return flt(replay(get_ledger_rows(item, warehouse, upto), method).value)


def make_sle(**kwargs) -> "StockLedgerEntry":
	"""Create and insert a Stock Ledger Entry, bypassing user permissions."""
	sle = frappe.get_doc({"doctype": "Stock Ledger Entry", **kwargs})
	sle.flags.ignore_permissions = True
	sle.insert(ignore_permissions=True)
	return sle


def cancel_sles(voucher_type: str, voucher_no: str) -> None:
	"""Mark every ledger entry for a voucher cancelled.

	Rows are kept rather than deleted so the audit trail survives, and every
	balance/valuation query filters on is_cancelled = 0.
	"""
	frappe.db.set_value(
		"Stock Ledger Entry",
		{"voucher_type": voucher_type, "voucher_no": voucher_no},
		"is_cancelled",
		1,
		update_modified=False,
	)
