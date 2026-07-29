# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

import frappe
from warehouse_management.warehouse_management.base_test_case import WarehouseTestCase
from frappe.utils import add_to_date, now_datetime

from warehouse_management.warehouse_management.doctype.stock_ledger_entry.stock_ledger_entry import (
	get_moving_average_rate,
	get_stock_balance,
	get_stock_value,
)
from warehouse_management.warehouse_management.sample_data import (
	consume,
	create_item,
	create_warehouse,
	receipt,
)


class TestStockLedgerEntry(WarehouseTestCase):
	def setUp(self):
		self.item = create_item("SLE-ITEM")
		self.warehouse = create_warehouse("SLE Store")

	# --- balance --------------------------------------------------------

	def test_balance_is_zero_without_entries(self):
		self.assertEqual(get_stock_balance(self.item, self.warehouse), 0)

	def test_balance_sums_signed_quantities(self):
		receipt(self.item, self.warehouse, qty=10, rate=5)
		receipt(self.item, self.warehouse, qty=5, rate=5)
		consume(self.item, self.warehouse, qty=3)

		self.assertEqual(get_stock_balance(self.item, self.warehouse), 12)

	def test_balance_respects_as_of_datetime(self):
		receipt(self.item, self.warehouse, qty=10, rate=5, posting_date="2026-01-01")
		receipt(self.item, self.warehouse, qty=7, rate=5, posting_date="2026-06-01")

		self.assertEqual(
			get_stock_balance(self.item, self.warehouse, "2026-03-01 23:59:59"), 10
		)
		self.assertEqual(
			get_stock_balance(self.item, self.warehouse, "2026-12-31 23:59:59"), 17
		)

	# --- moving average valuation ---------------------------------------

	def test_valuation_of_single_receipt(self):
		receipt(self.item, self.warehouse, qty=10, rate=100)

		self.assertEqual(get_moving_average_rate(self.item, self.warehouse), 100)

	def test_moving_average_weights_by_quantity(self):
		# 10 @ 100 and 30 @ 200 -> (1000 + 6000) / 40 = 175
		receipt(self.item, self.warehouse, qty=10, rate=100)
		receipt(self.item, self.warehouse, qty=30, rate=200)

		self.assertEqual(get_moving_average_rate(self.item, self.warehouse), 175)

	def test_consumption_does_not_change_valuation_rate(self):
		receipt(self.item, self.warehouse, qty=10, rate=100)
		receipt(self.item, self.warehouse, qty=30, rate=200)
		consume(self.item, self.warehouse, qty=35)

		# Issuing stock at cost leaves the average cost untouched.
		self.assertEqual(get_moving_average_rate(self.item, self.warehouse), 175)

	def test_valuation_is_zero_without_receipts(self):
		self.assertEqual(get_moving_average_rate(self.item, self.warehouse), 0)

	def test_stock_value_is_qty_times_rate(self):
		receipt(self.item, self.warehouse, qty=10, rate=100)
		receipt(self.item, self.warehouse, qty=30, rate=200)
		consume(self.item, self.warehouse, qty=20)

		# 20 remaining @ 175
		self.assertEqual(get_stock_value(self.item, self.warehouse), 3500)

	def test_valuation_respects_as_of_datetime(self):
		receipt(self.item, self.warehouse, qty=10, rate=100, posting_date="2026-01-01")
		receipt(self.item, self.warehouse, qty=30, rate=200, posting_date="2026-06-01")

		self.assertEqual(
			get_moving_average_rate(self.item, self.warehouse, "2026-03-01 23:59:59"), 100
		)
		self.assertEqual(
			get_moving_average_rate(self.item, self.warehouse, "2026-12-31 23:59:59"), 175
		)

	# --- ledger entry validation ----------------------------------------

	def test_zero_qty_entry_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Stock Ledger Entry",
					"item": self.item,
					"warehouse": self.warehouse,
					"posting_datetime": now_datetime(),
					"actual_qty": 0,
				}
			).insert(ignore_permissions=True)

	def test_outgoing_entry_cannot_carry_an_incoming_rate(self):
		receipt(self.item, self.warehouse, qty=10, rate=100)

		sle = frappe.get_doc(
			{
				"doctype": "Stock Ledger Entry",
				"item": self.item,
				"warehouse": self.warehouse,
				"posting_datetime": now_datetime(),
				"actual_qty": -1,
				"incoming_rate": 999,
			}
		)
		sle.insert(ignore_permissions=True)

		self.assertEqual(sle.incoming_rate, 0)

	def test_cancelled_entries_are_excluded_from_balance(self):
		entry = receipt(self.item, self.warehouse, qty=10, rate=100)
		self.assertEqual(get_stock_balance(self.item, self.warehouse), 10)

		entry.cancel()

		self.assertEqual(get_stock_balance(self.item, self.warehouse), 0)
		self.assertEqual(get_moving_average_rate(self.item, self.warehouse), 0)

	def test_balances_are_isolated_per_warehouse(self):
		other = create_warehouse("SLE Other Store")
		receipt(self.item, self.warehouse, qty=10, rate=100)
		receipt(self.item, other, qty=4, rate=250)

		self.assertEqual(get_stock_balance(self.item, self.warehouse), 10)
		self.assertEqual(get_stock_balance(self.item, other), 4)
		self.assertEqual(get_moving_average_rate(self.item, other), 250)
