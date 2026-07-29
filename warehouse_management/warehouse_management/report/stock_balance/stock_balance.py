# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

"""Stock Balance: quantity and value on a date.

Everything is derived from the ledger in a single grouped query. Set the
`consolidate` filter to collapse warehouses and report one row per item.
"""

import frappe
from frappe import _
from frappe.utils import flt, today


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("to_date"):
		filters.to_date = today()

	consolidated = bool(int(filters.get("consolidate") or 0))
	columns = get_columns(consolidated)
	data = get_data(filters, consolidated)
	return columns, data


def get_columns(consolidated=False):
	columns = [
		{
			"label": _("Item"),
			"fieldname": "item",
			"fieldtype": "Link",
			"options": "Item",
			"width": 160,
		}
	]

	if not consolidated:
		columns.append(
			{
				"label": _("Warehouse"),
				"fieldname": "warehouse",
				"fieldtype": "Link",
				"options": "Warehouse",
				"width": 160,
			}
		)

	columns += [
		{"label": _("Balance Qty"), "fieldname": "balance_qty", "fieldtype": "Float", "width": 120},
		{
			"label": _("Valuation Rate"),
			"fieldname": "valuation_rate",
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"label": _("Balance Value"),
			"fieldname": "balance_value",
			"fieldtype": "Currency",
			"width": 140,
		},
		{"label": _("In Qty"), "fieldname": "in_qty", "fieldtype": "Float", "width": 100},
		{"label": _("Out Qty"), "fieldname": "out_qty", "fieldtype": "Float", "width": 100},
	]

	return columns


def get_data(filters, consolidated=False):
	conditions = ["sle.is_cancelled = 0", "sle.posting_datetime <= %(to_date)s"]
	values = {"to_date": f"{filters.to_date} 23:59:59"}

	if filters.get("item"):
		conditions.append("sle.item = %(item)s")
		values["item"] = filters.item

	if filters.get("warehouse"):
		conditions.append("sle.warehouse = %(warehouse)s")
		values["warehouse"] = filters.warehouse

	condition_sql = " AND ".join(conditions)
	group_by = "sle.item" if consolidated else "sle.item, sle.warehouse"
	warehouse_select = "" if consolidated else "sle.warehouse,"

	# One grouped query does the whole job: net balance, in/out quantities, and
	# the weighted-average cost of everything received.
	rows = frappe.db.sql(
		f"""
		SELECT
			sle.item,
			{warehouse_select}
			SUM(sle.actual_qty) AS balance_qty,
			SUM(CASE WHEN sle.actual_qty > 0 THEN sle.actual_qty ELSE 0 END) AS in_qty,
			SUM(CASE WHEN sle.actual_qty < 0 THEN -sle.actual_qty ELSE 0 END) AS out_qty,
			SUM(CASE WHEN sle.actual_qty > 0 THEN sle.actual_qty * sle.incoming_rate ELSE 0 END)
				AS in_value,
			SUM(CASE WHEN sle.actual_qty > 0 THEN sle.actual_qty ELSE 0 END) AS in_qty_for_rate
		FROM `tabStock Ledger Entry` sle
		WHERE {condition_sql}
		GROUP BY {group_by}
		ORDER BY sle.item{'' if consolidated else ', sle.warehouse'}
		""",
		values,
		as_dict=True,
	)

	data = []
	for row in rows:
		valuation_rate = (
			flt(row.in_value) / flt(row.in_qty_for_rate) if flt(row.in_qty_for_rate) else 0.0
		)
		entry = {
			"item": row.item,
			"balance_qty": flt(row.balance_qty),
			"valuation_rate": valuation_rate,
			"balance_value": flt(row.balance_qty) * valuation_rate,
			"in_qty": flt(row.in_qty),
			"out_qty": flt(row.out_qty),
		}
		if not consolidated:
			entry["warehouse"] = row.warehouse

		data.append(entry)

	return data
