# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime


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


def get_stock_value(item: str, warehouse: str, upto: str | None = None) -> float:
	"""Balance qty valued at the moving average rate."""
	return flt(get_stock_balance(item, warehouse, upto)) * flt(
		get_moving_average_rate(item, warehouse, upto)
	)


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
