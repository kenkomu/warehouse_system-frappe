# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime, getdate

from warehouse_management.warehouse_management.doctype.stock_ledger_entry.stock_ledger_entry import (
	cancel_sles,
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
		self.make_stock_ledger_entries()

	def on_cancel(self):
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

	def make_stock_ledger_entries(self):
		"""Write the ledger rows this entry implies.

		Receipt  -> one incoming row
		Consume  -> one outgoing row
		Transfer -> an outgoing row and an incoming row carrying the source's
		            moving average rate, so value follows the stock.
		"""
		posting_datetime = self.posting_datetime

		for row in self.items:
			if row.source_warehouse:
				make_sle(
					item=row.item,
					warehouse=row.source_warehouse,
					posting_datetime=posting_datetime,
					actual_qty=-flt(row.qty),
					incoming_rate=0,
					voucher_type=self.doctype,
					voucher_no=self.name,
					voucher_detail_no=row.name,
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

				make_sle(
					item=row.item,
					warehouse=row.target_warehouse,
					posting_datetime=posting_datetime,
					actual_qty=flt(row.qty),
					incoming_rate=incoming_rate,
					voucher_type=self.doctype,
					voucher_no=self.name,
					voucher_detail_no=row.name,
				)
