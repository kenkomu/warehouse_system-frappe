# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

from warehouse_management.warehouse_management.base_test_case import WarehouseTestCase

from warehouse_management.warehouse_management.report.stock_balance.stock_balance import execute
from warehouse_management.warehouse_management.sample_data import (
	consume,
	create_item,
	create_warehouse,
	receipt,
	transfer,
)


class TestStockBalanceReport(WarehouseTestCase):
	def setUp(self):
		self.item = create_item("RPT-BALANCE-ITEM")
		self.wh_a = create_warehouse("Balance Store A")
		self.wh_b = create_warehouse("Balance Store B")
		self.filters = {"to_date": "2026-12-31", "item": self.item}

	def test_returns_expected_columns(self):
		columns, _rows = execute(self.filters)
		fieldnames = [c["fieldname"] for c in columns]

		for expected in ("item", "warehouse", "balance_qty", "valuation_rate", "balance_value"):
			self.assertIn(expected, fieldnames)

	def test_consolidated_view_drops_warehouse_column(self):
		columns, _rows = execute({**self.filters, "consolidate": 1})
		fieldnames = [c["fieldname"] for c in columns]

		self.assertNotIn("warehouse", fieldnames)

	def test_empty_when_no_movement(self):
		_columns, rows = execute(self.filters)
		self.assertEqual(rows, [])

	def test_one_row_per_item_warehouse(self):
		receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-02-01")
		receipt(self.item, self.wh_b, qty=4, rate=250, posting_date="2026-02-01")

		_columns, rows = execute(self.filters)

		self.assertEqual(len(rows), 2)
		by_warehouse = {r["warehouse"]: r for r in rows}
		self.assertEqual(by_warehouse[self.wh_a]["balance_qty"], 10)
		self.assertEqual(by_warehouse[self.wh_b]["balance_qty"], 4)

	def test_in_and_out_quantities(self):
		receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-02-01")
		consume(self.item, self.wh_a, qty=3, posting_date="2026-02-02")

		_columns, rows = execute(self.filters)

		self.assertEqual(rows[0]["in_qty"], 10)
		self.assertEqual(rows[0]["out_qty"], 3)
		self.assertEqual(rows[0]["balance_qty"], 7)

	def test_valuation_and_value(self):
		receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-02-01")
		receipt(self.item, self.wh_a, qty=30, rate=200, posting_date="2026-02-02")
		consume(self.item, self.wh_a, qty=20, posting_date="2026-02-03")

		_columns, rows = execute(self.filters)

		self.assertEqual(rows[0]["valuation_rate"], 175)
		self.assertEqual(rows[0]["balance_qty"], 20)
		self.assertEqual(rows[0]["balance_value"], 3500)

	def test_consolidation_sums_across_warehouses(self):
		receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-02-01")
		receipt(self.item, self.wh_b, qty=6, rate=100, posting_date="2026-02-01")

		_columns, rows = execute({**self.filters, "consolidate": 1})

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["balance_qty"], 16)
		self.assertNotIn("warehouse", rows[0])

	def test_transfer_is_value_neutral_when_consolidated(self):
		receipt(self.item, self.wh_a, qty=10, rate=50, posting_date="2026-02-01")
		transfer(self.item, self.wh_a, self.wh_b, qty=4, posting_date="2026-02-02")

		_columns, rows = execute({**self.filters, "consolidate": 1})

		# Moving stock between warehouses must not create or destroy value.
		self.assertEqual(rows[0]["balance_qty"], 10)
		self.assertEqual(rows[0]["balance_value"], 500)

	def test_as_on_date_excludes_later_movements(self):
		receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-02-01")
		receipt(self.item, self.wh_a, qty=99, rate=100, posting_date="2026-09-01")

		_columns, rows = execute({"to_date": "2026-03-01", "item": self.item})

		self.assertEqual(rows[0]["balance_qty"], 10)

	def test_cancelled_entries_are_excluded(self):
		entry = receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-02-01")
		entry.cancel()

		_columns, rows = execute(self.filters)

		self.assertEqual(rows, [])
