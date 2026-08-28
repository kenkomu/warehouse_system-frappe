# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

"""Stock Balance: quantity and value on a date.

Quantities come from the ledger in a single grouped query. Value does not:
every method is path dependent, so the value of what is on hand is replayed
from the movements themselves. Moving Average recalculates on acquisition
against the value already held, and FIFO and LIFO track which layers are still
there. None of the three reduces to an aggregate.

Set the `consolidate` filter to collapse warehouses and report one row per item.
"""

import frappe
from frappe import _
from frappe.utils import flt, today

from warehouse_management.warehouse_management.valuation import (
	MOVING_AVERAGE,
	ValuationState,
	get_item_valuation_method,
)


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

	# One grouped query for the quantities; value is replayed below.
	rows = frappe.db.sql(
		f"""
		SELECT
			sle.item,
			{warehouse_select}
			SUM(sle.actual_qty) AS balance_qty,
			SUM(CASE WHEN sle.actual_qty > 0 THEN sle.actual_qty ELSE 0 END) AS in_qty,
			SUM(CASE WHEN sle.actual_qty < 0 THEN -sle.actual_qty ELSE 0 END) AS out_qty
		FROM `tabStock Ledger Entry` sle
		WHERE {condition_sql}
		GROUP BY {group_by}
		ORDER BY sle.item{'' if consolidated else ', sle.warehouse'}
		""",
		values,
		as_dict=True,
	)

	methods = {}
	for row in rows:
		if row.item not in methods:
			methods[row.item] = get_item_valuation_method(row.item)

	values_by_key = get_replayed_values(filters, methods, consolidated)

	data = []
	for row in rows:
		balance_qty = flt(row.balance_qty)
		key = row.item if consolidated else (row.item, row.warehouse)
		balance_value = flt(values_by_key.get(key, 0.0))

		entry = {
			"item": row.item,
			"balance_qty": balance_qty,
			"valuation_rate": (balance_value / balance_qty) if balance_qty else 0.0,
			"balance_value": balance_value,
			"in_qty": flt(row.in_qty),
			"out_qty": flt(row.out_qty),
		}
		if not consolidated:
			entry["warehouse"] = row.warehouse

		data.append(entry)

	return data


def get_replayed_values(filters, methods, consolidated=False) -> dict:
	"""Stock value per group, replayed from the movements.

	Valuation is always computed per warehouse, because both the average and the
	layers belong to a warehouse. Consolidation then sums those values rather
	than re-deriving one figure across warehouses, which is what keeps a transfer
	between two warehouses value neutral even when they hold the same item at
	different costs.
	"""
	if not methods:
		return {}

	conditions = ["sle.is_cancelled = 0", "sle.posting_datetime <= %(to_date)s"]
	values = {"to_date": f"{filters.to_date} 23:59:59"}

	if filters.get("item"):
		conditions.append("sle.item = %(item)s")
		values["item"] = filters.item

	if filters.get("warehouse"):
		conditions.append("sle.warehouse = %(warehouse)s")
		values["warehouse"] = filters.warehouse

	rows = frappe.db.sql(
		f"""
		SELECT sle.item, sle.warehouse, sle.actual_qty, sle.incoming_rate
		FROM `tabStock Ledger Entry` sle
		WHERE {" AND ".join(conditions)}
		ORDER BY sle.posting_datetime ASC, sle.creation ASC
		""",
		values,
		as_dict=True,
	)

	states = {}
	for row in rows:
		key = (row.item, row.warehouse)
		state = states.setdefault(
			key, ValuationState(methods.get(row.item, MOVING_AVERAGE))
		)
		state.add(row.actual_qty, row.incoming_rate)

	totals = {}
	for (item, warehouse), state in states.items():
		key = item if consolidated else (item, warehouse)
		totals[key] = flt(totals.get(key, 0.0)) + flt(state.value)

	return totals
