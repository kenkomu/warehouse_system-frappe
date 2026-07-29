# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

import frappe
from warehouse_management.warehouse_management.base_test_case import WarehouseTestCase

from warehouse_management.warehouse_management.sample_data import (
	create_item,
	create_warehouse,
	receipt,
)


class TestItem(WarehouseTestCase):
	def test_item_is_named_by_item_code(self):
		name = create_item("ITEM-NAMING-1", item_name="Widget")
		self.assertEqual(name, "ITEM-NAMING-1")
		self.assertEqual(frappe.db.get_value("Item", name, "item_name"), "Widget")

	def test_item_name_defaults_to_code(self):
		doc = frappe.get_doc(
			{"doctype": "Item", "item_code": "ITEM-NO-NAME", "stock_uom": "Nos"}
		)
		doc.item_name = None
		doc.insert(ignore_permissions=True)

		self.assertEqual(doc.item_name, "ITEM-NO-NAME")

	def test_item_code_must_be_unique(self):
		create_item("ITEM-DUPE")

		with self.assertRaises(frappe.exceptions.DuplicateEntryError):
			frappe.get_doc(
				{"doctype": "Item", "item_code": "ITEM-DUPE", "item_name": "x", "stock_uom": "Nos"}
			).insert(ignore_permissions=True)

	def test_uom_locked_once_stock_exists(self):
		item = create_item("ITEM-UOM-LOCK")
		warehouse = create_warehouse("UOM Lock WH")
		receipt(item, warehouse, qty=3, rate=7)

		doc = frappe.get_doc("Item", item)
		doc.stock_uom = "Kg"

		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_uom_changeable_before_any_stock(self):
		item = create_item("ITEM-UOM-FREE")
		doc = frappe.get_doc("Item", item)
		doc.stock_uom = "Kg"
		doc.save()

		self.assertEqual(frappe.db.get_value("Item", item, "stock_uom"), "Kg")
