# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

"""Stock Balance: quantity and value on a date.

Quantities are derived from the ledger in a single grouped query, and so is
value for items on Moving Average. FIFO and LIFO cannot be expressed as an
aggregate -- which units you are still holding decides what they are worth --
so those items get a second pass that replays their movements. The fast path is
kept because most items sit on the default.

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

	methods = {}
	for row in rows:
		if row.item not in methods:
			methods[row.item] = get_item_valuation_method(row.item)

	layered = [item for item, method in methods.items() if method != MOVING_AVERAGE]
	layered_values = get_layered_values(filters, layered, methods, consolidated)

	data = []
	for row in rows:
		balance_qty = flt(row.balance_qty)

		if methods[row.item] == MOVING_AVERAGE:
			valuation_rate = (
				flt(row.in_value) / flt(row.in_qty_for_rate) if flt(row.in_qty_for_rate) else 0.0
			)
			balance_value = balance_qty * valuation_rate
		else:
			# Value comes from the layers still on hand; the rate is what that
			# implies per unit rather than something averaged separately.
			key = row.item if consolidated else (row.item, row.warehouse)
			balance_value = flt(layered_values.get(key, 0.0))
			valuation_rate = (balance_value / balance_qty) if balance_qty else 0.0

		entry = {
			"item": row.item,
			"balance_qty": balance_qty,
			"valuation_rate": valuation_rate,
			"balance_value": balance_value,
			"in_qty": flt(row.in_qty),
			"out_qty": flt(row.out_qty),
		}
		if not consolidated:
			entry["warehouse"] = row.warehouse

		data.append(entry)

	return data


def get_layered_values(filters, items, methods, consolidated=False) -> dict:
	"""Stock value for FIFO/LIFO items, replayed from their movements.

	Layers live in a warehouse, so valuation is always computed per warehouse.
	Consolidation then sums those values instead of re-averaging the incoming
	rows, which is what keeps a transfer between warehouses value neutral: the
	receiving row is priced at what left the source, so the two cancel.
	"""
	if not items:
		return {}

	conditions = [
		"sle.is_cancelled = 0",
		"sle.posting_datetime <= %(to_date)s",
		"sle.item IN %(items)s",
	]
	values = {"to_date": f"{filters.to_date} 23:59:59", "items": tuple(items)}

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
		state = states.setdefault(key, ValuationState(methods[row.item]))
		state.add(row.actual_qty, row.incoming_rate)

	totals = {}
	for (item, warehouse), state in states.items():
		key = item if consolidated else (item, warehouse)
		totals[key] = flt(totals.get(key, 0.0)) + flt(state.value)

	return totals
