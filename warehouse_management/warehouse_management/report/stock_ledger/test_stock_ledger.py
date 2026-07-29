# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

from warehouse_management.warehouse_management.base_test_case import WarehouseTestCase

from warehouse_management.warehouse_management.report.stock_ledger.stock_ledger import execute
from warehouse_management.warehouse_management.sample_data import (
	consume,
	create_item,
	create_warehouse,
	receipt,
	transfer,
)


class TestStockLedgerReport(WarehouseTestCase):
	def setUp(self):
		self.item = create_item("RPT-LEDGER-ITEM")
		self.wh_a = create_warehouse("Ledger Store A")
		self.wh_b = create_warehouse("Ledger Store B")
		self.filters = {
			"from_date": "2026-01-01",
			"to_date": "2026-12-31",
			"item": self.item,
		}

	def test_returns_expected_columns(self):
		columns, _rows = execute(self.filters)
		fieldnames = [c["fieldname"] for c in columns]

		for expected in (
			"posting_datetime",
			"item",
			"warehouse",
			"actual_qty",
			"balance_qty",
			"valuation_rate",
			"balance_value",
		):
			self.assertIn(expected, fieldnames)

	def test_empty_when_no_movement(self):
		_columns, rows = execute(self.filters)
		self.assertEqual(rows, [])

	def test_one_row_per_movement(self):
		receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-02-01")
		consume(self.item, self.wh_a, qty=3, posting_date="2026-02-02")

		_columns, rows = execute(self.filters)

		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[0].actual_qty, 10)
		self.assertEqual(rows[1].actual_qty, -3)

	def test_running_balance_accumulates(self):
		receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-02-01")
		receipt(self.item, self.wh_a, qty=5, rate=100, posting_date="2026-02-02")
		consume(self.item, self.wh_a, qty=4, posting_date="2026-02-03")

		_columns, rows = execute(self.filters)

		self.assertEqual([r.balance_qty for r in rows], [10, 15, 11])

	def test_running_valuation_uses_weighted_average(self):
		receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-02-01")
		receipt(self.item, self.wh_a, qty=30, rate=200, posting_date="2026-02-02")

		_columns, rows = execute(self.filters)

		self.assertEqual(rows[0].valuation_rate, 100)
		self.assertEqual(rows[1].valuation_rate, 175)
		self.assertEqual(rows[1].balance_value, 40 * 175)

	def test_running_balance_is_tracked_per_warehouse(self):
		receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-02-01")
		transfer(self.item, self.wh_a, self.wh_b, qty=4, posting_date="2026-02-02")

		_columns, rows = execute(self.filters)
		by_warehouse = {}
		for row in rows:
			by_warehouse.setdefault(row.warehouse, []).append(row.balance_qty)

		self.assertEqual(by_warehouse[self.wh_a][-1], 6)
		self.assertEqual(by_warehouse[self.wh_b][-1], 4)

	def test_date_filter_excludes_outside_movements(self):
		receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-02-01")
		receipt(self.item, self.wh_a, qty=99, rate=100, posting_date="2026-09-01")

		_columns, rows = execute(
			{"from_date": "2026-01-01", "to_date": "2026-03-01", "item": self.item}
		)

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].actual_qty, 10)

	def test_warehouse_filter_narrows_rows(self):
		receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-02-01")
		receipt(self.item, self.wh_b, qty=7, rate=100, posting_date="2026-02-01")

		_columns, rows = execute({**self.filters, "warehouse": self.wh_b})

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].warehouse, self.wh_b)

	def test_cancelled_entries_are_excluded(self):
		entry = receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-02-01")
		entry.cancel()

		_columns, rows = execute(self.filters)

		self.assertEqual(rows, [])
