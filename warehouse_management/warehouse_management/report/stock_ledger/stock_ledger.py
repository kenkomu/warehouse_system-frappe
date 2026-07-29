# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

"""Stock Ledger: every stock movement, one row per ledger entry.

The running balance and running valuation are accumulated over the returned
rows rather than read from stored state, keeping the ledger stateless.
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{
			"label": _("Posting Datetime"),
			"fieldname": "posting_datetime",
			"fieldtype": "Datetime",
			"width": 180,
		},
		{
			"label": _("Item"),
			"fieldname": "item",
			"fieldtype": "Link",
			"options": "Item",
			"width": 140,
		},
		{
			"label": _("Warehouse"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 140,
		},
		{"label": _("Qty Change"), "fieldname": "actual_qty", "fieldtype": "Float", "width": 110},
		{"label": _("Balance Qty"), "fieldname": "balance_qty", "fieldtype": "Float", "width": 110},
		{
			"label": _("Incoming Rate"),
			"fieldname": "incoming_rate",
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"label": _("Valuation Rate"),
			"fieldname": "valuation_rate",
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"label": _("Balance Value"),
			"fieldname": "balance_value",
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"label": _("Voucher Type"),
			"fieldname": "voucher_type",
			"fieldtype": "Link",
			"options": "DocType",
			"width": 120,
		},
		{
			"label": _("Voucher No"),
			"fieldname": "voucher_no",
			"fieldtype": "Dynamic Link",
			"options": "voucher_type",
			"width": 140,
		},
	]


def get_conditions(filters):
	conditions = ["sle.is_cancelled = 0"]
	values = {}

	if filters.get("from_date"):
		conditions.append("sle.posting_datetime >= %(from_date)s")
		values["from_date"] = f"{filters.from_date} 00:00:00"

	if filters.get("to_date"):
		conditions.append("sle.posting_datetime <= %(to_date)s")
		values["to_date"] = f"{filters.to_date} 23:59:59"

	if filters.get("item"):
		conditions.append("sle.item = %(item)s")
		values["item"] = filters.item

	if filters.get("warehouse"):
		conditions.append("sle.warehouse = %(warehouse)s")
		values["warehouse"] = filters.warehouse

	return " AND ".join(conditions), values


def get_data(filters):
	condition_sql, values = get_conditions(filters)

	entries = frappe.db.sql(
		f"""
		SELECT
			sle.name,
			sle.posting_datetime,
			sle.item,
			sle.warehouse,
			sle.actual_qty,
			sle.incoming_rate,
			sle.voucher_type,
			sle.voucher_no
		FROM `tabStock Ledger Entry` sle
		WHERE {condition_sql}
		ORDER BY sle.posting_datetime ASC, sle.creation ASC
		""",
		values,
		as_dict=True,
	)

	# Running balance/valuation per item+warehouse across the filtered window.
	running = {}
	for row in entries:
		key = (row.item, row.warehouse)
		state = running.setdefault(key, {"qty": 0.0, "in_qty": 0.0, "in_value": 0.0})

		state["qty"] += flt(row.actual_qty)
		if flt(row.actual_qty) > 0:
			state["in_qty"] += flt(row.actual_qty)
			state["in_value"] += flt(row.actual_qty) * flt(row.incoming_rate)

		valuation_rate = (state["in_value"] / state["in_qty"]) if state["in_qty"] else 0.0

		row.balance_qty = state["qty"]
		row.valuation_rate = valuation_rate
		row.balance_value = state["qty"] * valuation_rate

	return entries
