# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils.nestedset import NestedSet


class Warehouse(NestedSet):
	nsm_parent_field = "parent_warehouse"

	def validate(self):
		if self.parent_warehouse and self.parent_warehouse == self.name:
			frappe.throw(_("Warehouse cannot be its own parent."))

		if self.parent_warehouse and not frappe.db.get_value(
			"Warehouse", self.parent_warehouse, "is_group"
		):
			frappe.throw(
				_("{0} is not a group warehouse, so it cannot have children.").format(
					self.parent_warehouse
				)
			)

	def on_update(self):
		super().on_update()

	def on_trash(self):
		if self.has_stock_ledger_entries():
			frappe.throw(
				_("Cannot delete {0} because stock ledger entries exist against it.").format(self.name)
			)

		# NestedSet.on_trash refuses to delete a group that still has children.
		super().on_trash()

	def has_stock_ledger_entries(self) -> bool:
		return bool(frappe.db.exists("Stock Ledger Entry", {"warehouse": self.name, "is_cancelled": 0}))


@frappe.whitelist()
def get_children(doctype=None, parent=None, is_root=False, **kwargs):
	"""Feed the tree view in the desk UI."""
	parent = parent or kwargs.get("parent_warehouse")
	if parent == "Warehouse" or not parent:
		parent = ""

	return frappe.get_all(
		"Warehouse",
		fields=["name as value", "is_group as expandable", "parent_warehouse as parent"],
		filters={"ifnull(parent_warehouse, '')": parent},
		order_by="name",
	)


@frappe.whitelist()
def add_node():
	from frappe.desk.treeview import make_tree_args

	args = make_tree_args(**frappe.form_dict)
	if args.parent_warehouse == "All Warehouses":
		args.parent_warehouse = None

	frappe.get_doc(args).insert()
