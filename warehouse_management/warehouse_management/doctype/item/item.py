# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class Item(Document):
	def validate(self):
		if not self.item_name:
			self.item_name = self.item_code

		if flt_qty_exists(self.name) and self.has_value_changed("stock_uom"):
			frappe.throw(
				_("Cannot change Stock UOM for {0} because stock ledger entries already exist.").format(
					self.name
				)
			)


def flt_qty_exists(item: str) -> bool:
	"""True if any stock ledger entry references this item."""
	if not item:
		return False

	return bool(
		frappe.db.exists("Stock Ledger Entry", {"item": item, "is_cancelled": 0})
	)
