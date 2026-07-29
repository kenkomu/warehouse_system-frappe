# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, format_datetime, get_datetime, getdate

from warehouse_management.warehouse_management.doctype.stock_ledger_entry.stock_ledger_entry import (
	cancel_sles,
	get_first_negative_balance,
	get_moving_average_rate,
	get_stock_balance,
	make_sle,
)

RECEIPT = "Receipt"
CONSUME = "Consume"
TRANSFER = "Transfer"


class StockEntry(Document):
	def validate(self):
		self.validate_items()
		self.validate_warehouses()
		self.validate_rates()
		self.set_amounts_and_totals()

	def on_submit(self):
		self.validate_stock_availability()
		self.validate_timeline_stays_positive()
		self.make_stock_ledger_entries()

	def on_cancel(self):
		self.validate_timeline_stays_positive(cancelling=True)
		cancel_sles(self.doctype, self.name)

	# --- validation -----------------------------------------------------

	@property
	def posting_datetime(self):
		return get_datetime(f"{getdate(self.posting_date)} {self.posting_time or '00:00:00'}")

	def validate_items(self):
		if not self.items:
			frappe.throw(_("At least one item row is required."))

		for row in self.items:
			if flt(row.qty) <= 0:
				frappe.throw(_("Row {0}: Qty must be greater than zero.").format(row.idx))

	def validate_warehouses(self):
		"""Each purpose dictates which warehouse fields are meaningful."""
		for row in self.items:
			if self.purpose == RECEIPT:
				if not row.target_warehouse:
					frappe.throw(_("Row {0}: Target Warehouse is required for a Receipt.").format(row.idx))
				row.source_warehouse = None

			elif self.purpose == CONSUME:
				if not row.source_warehouse:
					frappe.throw(_("Row {0}: Source Warehouse is required for a Consume.").format(row.idx))
				row.target_warehouse = None

			elif self.purpose == TRANSFER:
				if not row.source_warehouse or not row.target_warehouse:
					frappe.throw(
						_("Row {0}: Both Source and Target Warehouse are required for a Transfer.").format(
							row.idx
						)
					)
				if row.source_warehouse == row.target_warehouse:
					frappe.throw(
						_("Row {0}: Source and Target Warehouse cannot be the same.").format(row.idx)
					)

			for field in ("source_warehouse", "target_warehouse"):
				warehouse = row.get(field)
				if warehouse and frappe.db.get_value("Warehouse", warehouse, "is_group"):
					frappe.throw(
						_("Row {0}: {1} is a group warehouse and cannot hold stock.").format(
							row.idx, warehouse
						)
					)

	def validate_rates(self):
		for row in self.items:
			if self.purpose == RECEIPT:
				if flt(row.rate) <= 0:
					frappe.throw(_("Row {0}: Rate is required for a Receipt.").format(row.idx))
			else:
				# Outgoing stock is valued from the ledger, not from user input.
				row.rate = 0

	def validate_stock_availability(self):
		"""Block submission that would drive any warehouse negative."""
		for row in self.items:
			if not row.source_warehouse:
				continue

			available = get_stock_balance(row.item, row.source_warehouse, self.posting_datetime)
			if flt(row.qty) > flt(available):
				frappe.throw(
					_("Row {0}: Only {1} of {2} available in {3}, cannot issue {4}.").format(
						row.idx, flt(available), row.item, row.source_warehouse, flt(row.qty)
					),
					title=_("Insufficient Stock"),
				)

	def validate_timeline_stays_positive(self, cancelling: bool = False):
		"""Reject a change that would leave stock negative at any point in time.

		This is the guard `validate_stock_availability` cannot provide: that one
		asks "is there enough right now", which stays true when a backdated issue
		steals stock a later entry was relying on, or when cancelling a receipt
		that has since been drawn down.

		Checked against the projected timeline rather than the written one, so a
		rejected change never has to be unwound.
		"""
		pending = [] if cancelling else self.planned_ledger_rows()
		exclude = (self.doctype, self.name) if cancelling else None

		for item, warehouse in self.affected_stock_keys():
			extra_rows = [
				row for row in pending if row["item"] == item and row["warehouse"] == warehouse
			]
			negative = get_first_negative_balance(
				item, warehouse, extra_rows=extra_rows, exclude_voucher=exclude
			)
			if not negative:
				continue

			frappe.throw(
				_("{0} in {1} would fall to {2} on {3}.").format(
					item,
					warehouse,
					flt(negative["balance"]),
					format_datetime(negative["posting_datetime"]),
				),
				title=_("Negative Stock"),
			)

	def affected_stock_keys(self) -> set[tuple[str, str]]:
		"""Every item/warehouse pair this entry touches."""
		keys = set()
		for row in self.items:
			for warehouse in (row.source_warehouse, row.target_warehouse):
				if warehouse:
					keys.add((row.item, warehouse))

		return keys

	def set_amounts_and_totals(self):
		total_qty = 0.0
		total_value = 0.0

		for row in self.items:
			row.amount = flt(row.qty) * flt(row.rate)
			total_qty += flt(row.qty)
			total_value += flt(row.amount)

		self.total_qty = total_qty
		self.total_value = total_value

	# --- ledger ---------------------------------------------------------

	def planned_ledger_rows(self) -> list[dict]:
		"""The ledger rows this entry implies, described but not written.

		Receipt  -> one incoming row
		Consume  -> one outgoing row
		Transfer -> an outgoing row and an incoming row carrying the source's
		            moving average rate, so value follows the stock.

		Kept separate from writing so the same rows can be validated first.
		"""
		posting_datetime = self.posting_datetime
		planned = []

		for row in self.items:
			if row.source_warehouse:
				planned.append(
					{
						"item": row.item,
						"warehouse": row.source_warehouse,
						"posting_datetime": posting_datetime,
						"actual_qty": -flt(row.qty),
						"incoming_rate": 0,
						"voucher_detail_no": row.name,
					}
				)

			if row.target_warehouse:
				if self.purpose == TRANSFER:
					# Carry the source valuation across so a transfer never creates
					# or destroys value.
					incoming_rate = get_moving_average_rate(
						row.item, row.source_warehouse, posting_datetime
					)
				else:
					incoming_rate = flt(row.rate)

				planned.append(
					{
						"item": row.item,
						"warehouse": row.target_warehouse,
						"posting_datetime": posting_datetime,
						"actual_qty": flt(row.qty),
						"incoming_rate": incoming_rate,
						"voucher_detail_no": row.name,
					}
				)

		return planned

	def make_stock_ledger_entries(self):
		"""Write the planned ledger rows."""
		for row in self.planned_ledger_rows():
			make_sle(voucher_type=self.doctype, voucher_no=self.name, **row)
