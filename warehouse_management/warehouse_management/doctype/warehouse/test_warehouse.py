# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

import frappe
from warehouse_management.warehouse_management.base_test_case import WarehouseTestCase

from warehouse_management.warehouse_management.sample_data import (
	create_item,
	create_warehouse,
	receipt,
)


class TestWarehouse(WarehouseTestCase):
	def setUp(self):
		self.root = create_warehouse("WH Root", is_group=1)

	def test_create_leaf_warehouse(self):
		name = create_warehouse("WH Leaf A", parent=self.root)
		doc = frappe.get_doc("Warehouse", name)

		self.assertEqual(doc.parent_warehouse, self.root)
		self.assertEqual(doc.is_group, 0)

	def test_nested_set_bounds_are_maintained(self):
		child_a = create_warehouse("WH NSM A", parent=self.root)
		child_b = create_warehouse("WH NSM B", parent=self.root)

		root = frappe.get_doc("Warehouse", self.root)
		a = frappe.get_doc("Warehouse", child_a)
		b = frappe.get_doc("Warehouse", child_b)

		# Children must sit strictly inside the parent's interval.
		self.assertLess(root.lft, a.lft)
		self.assertGreater(root.rgt, a.rgt)
		self.assertLess(root.lft, b.lft)
		self.assertGreater(root.rgt, b.rgt)

		# Siblings must not overlap.
		self.assertTrue(a.rgt < b.lft or b.rgt < a.lft)

	def test_cannot_parent_to_non_group(self):
		leaf = create_warehouse("WH Not Group", parent=self.root)

		with self.assertRaises(frappe.ValidationError):
			create_warehouse("WH Orphan", parent=leaf)

	def test_cannot_be_its_own_parent(self):
		name = create_warehouse("WH Self", parent=self.root)
		doc = frappe.get_doc("Warehouse", name)
		doc.parent_warehouse = doc.name

		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_cannot_delete_warehouse_with_ledger_entries(self):
		item = create_item("WH-DEL-ITEM")
		warehouse = create_warehouse("WH With Stock", parent=self.root)
		receipt(item, warehouse, qty=5, rate=10)

		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Warehouse", warehouse)

	def test_group_warehouse_rejects_stock(self):
		item = create_item("WH-GRP-ITEM")

		with self.assertRaises(frappe.ValidationError):
			receipt(item, self.root, qty=1, rate=5)
