# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from warehouse_management.warehouse_management.valuation import DEFAULT_VALUATION_METHOD


class Item(Document):
	def validate(self):
		if not self.item_name:
			self.item_name = self.item_code

		if not self.valuation_method:
			self.valuation_method = DEFAULT_VALUATION_METHOD

		self.validate_locked_fields()

	def validate_locked_fields(self):
		"""Neither the stock unit nor the costing method may change under stock.

		Both restate history rather than change the future. The ledger records
		quantities in the stock UOM, and value is replayed under whichever method
		is set at read time, so changing either silently rewrites figures that
		have already been reported.
		"""
		if not flt_qty_exists(self.name):
			return

		locked = (
			("stock_uom", _("Stock UOM")),
			("valuation_method", _("Valuation Method")),
		)

		for fieldname, label in locked:
			if self.has_value_changed(fieldname):
				frappe.throw(
					_("Cannot change {0} for {1} because stock ledger entries already exist.").format(
						label, self.name
					)
				)


def flt_qty_exists(item: str) -> bool:
	"""True if any stock ledger entry references this item."""
	if not item:
		return False

	return bool(
		frappe.db.exists("Stock Ledger Entry", {"item": item, "is_cancelled": 0})
	)
