# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

"""Helpers shared across the test suites."""

import frappe
from frappe.utils import nowtime, today


def create_item(item_code: str, item_name: str | None = None, stock_uom: str = "Nos") -> str:
	if frappe.db.exists("Item", item_code):
		return item_code

	doc = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code,
			"item_name": item_name or item_code,
			"stock_uom": stock_uom,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def create_warehouse(warehouse_name: str, parent: str | None = None, is_group: int = 0) -> str:
	if frappe.db.exists("Warehouse", warehouse_name):
		return warehouse_name

	doc = frappe.get_doc(
		{
			"doctype": "Warehouse",
			"warehouse_name": warehouse_name,
			"parent_warehouse": parent,
			"is_group": is_group,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def make_stock_entry(
	purpose: str,
	items: list[dict],
	posting_date: str | None = None,
	posting_time: str | None = None,
	submit: bool = True,
):
	"""Build a Stock Entry.

	Each item dict accepts: item, qty, rate, source_warehouse, target_warehouse.
	"""
	doc = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"purpose": purpose,
			"posting_date": posting_date or today(),
			"posting_time": posting_time or nowtime(),
			"items": items,
		}
	)
	doc.insert(ignore_permissions=True)

	if submit:
		doc.submit()

	return doc


def receipt(item: str, warehouse: str, qty: float, rate: float, **kwargs):
	return make_stock_entry(
		"Receipt",
		[{"item": item, "qty": qty, "rate": rate, "target_warehouse": warehouse}],
		**kwargs,
	)


def consume(item: str, warehouse: str, qty: float, **kwargs):
	return make_stock_entry(
		"Consume",
		[{"item": item, "qty": qty, "source_warehouse": warehouse}],
		**kwargs,
	)


def transfer(item: str, source: str, target: str, qty: float, **kwargs):
	return make_stock_entry(
		"Transfer",
		[{"item": item, "qty": qty, "source_warehouse": source, "target_warehouse": target}],
		**kwargs,
	)
