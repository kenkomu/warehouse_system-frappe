# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

import frappe
from warehouse_management.warehouse_management.base_test_case import WarehouseTestCase

from warehouse_management.warehouse_management.doctype.stock_ledger_entry.stock_ledger_entry import (
	get_moving_average_rate,
	get_stock_balance,
)
from warehouse_management.warehouse_management.sample_data import (
	consume,
	create_item,
	create_warehouse,
	make_stock_entry,
	receipt,
	transfer,
)


class TestStockEntry(WarehouseTestCase):
	def setUp(self):
		self.item = create_item("SE-ITEM")
		self.wh_a = create_warehouse("SE Store A")
		self.wh_b = create_warehouse("SE Store B")

	# --- receipt --------------------------------------------------------

	def test_receipt_increases_balance(self):
		receipt(self.item, self.wh_a, qty=10, rate=100)

		self.assertEqual(get_stock_balance(self.item, self.wh_a), 10)

	def test_receipt_creates_one_incoming_ledger_entry(self):
		entry = receipt(self.item, self.wh_a, qty=4, rate=25)

		sles = frappe.get_all(
			"Stock Ledger Entry",
			filters={"voucher_no": entry.name, "is_cancelled": 0},
			fields=["warehouse", "actual_qty", "incoming_rate"],
		)

		self.assertEqual(len(sles), 1)
		self.assertEqual(sles[0].warehouse, self.wh_a)
		self.assertEqual(sles[0].actual_qty, 4)
		self.assertEqual(sles[0].incoming_rate, 25)

	def test_receipt_requires_rate(self):
		with self.assertRaises(frappe.ValidationError):
			make_stock_entry(
				"Receipt",
				[{"item": self.item, "qty": 5, "rate": 0, "target_warehouse": self.wh_a}],
			)

	def test_receipt_requires_target_warehouse(self):
		with self.assertRaises(frappe.ValidationError):
			make_stock_entry("Receipt", [{"item": self.item, "qty": 5, "rate": 10}])

	# --- consume --------------------------------------------------------

	def test_consume_decreases_balance(self):
		receipt(self.item, self.wh_a, qty=10, rate=100)
		consume(self.item, self.wh_a, qty=4)

		self.assertEqual(get_stock_balance(self.item, self.wh_a), 6)

	def test_consume_writes_negative_ledger_entry(self):
		receipt(self.item, self.wh_a, qty=10, rate=100)
		entry = consume(self.item, self.wh_a, qty=3)

		sles = frappe.get_all(
			"Stock Ledger Entry",
			filters={"voucher_no": entry.name, "is_cancelled": 0},
			fields=["actual_qty", "incoming_rate"],
		)

		self.assertEqual(len(sles), 1)
		self.assertEqual(sles[0].actual_qty, -3)
		self.assertEqual(sles[0].incoming_rate, 0)

	def test_consume_requires_source_warehouse(self):
		with self.assertRaises(frappe.ValidationError):
			make_stock_entry("Consume", [{"item": self.item, "qty": 1}])

	def test_cannot_consume_more_than_available(self):
		receipt(self.item, self.wh_a, qty=2, rate=10)

		with self.assertRaises(frappe.ValidationError):
			consume(self.item, self.wh_a, qty=5)

	def test_cannot_consume_from_empty_warehouse(self):
		with self.assertRaises(frappe.ValidationError):
			consume(self.item, self.wh_b, qty=1)

	# --- transfer -------------------------------------------------------

	def test_transfer_moves_quantity(self):
		receipt(self.item, self.wh_a, qty=10, rate=50)
		transfer(self.item, self.wh_a, self.wh_b, qty=4)

		self.assertEqual(get_stock_balance(self.item, self.wh_a), 6)
		self.assertEqual(get_stock_balance(self.item, self.wh_b), 4)

	def test_transfer_writes_two_ledger_entries(self):
		receipt(self.item, self.wh_a, qty=10, rate=50)
		entry = transfer(self.item, self.wh_a, self.wh_b, qty=4)

		sles = frappe.get_all(
			"Stock Ledger Entry",
			filters={"voucher_no": entry.name, "is_cancelled": 0},
			fields=["warehouse", "actual_qty"],
			order_by="actual_qty asc",
		)

		self.assertEqual(len(sles), 2)
		self.assertEqual(sles[0].warehouse, self.wh_a)
		self.assertEqual(sles[0].actual_qty, -4)
		self.assertEqual(sles[1].warehouse, self.wh_b)
		self.assertEqual(sles[1].actual_qty, 4)

	def test_transfer_carries_valuation_across(self):
		receipt(self.item, self.wh_a, qty=10, rate=50)
		transfer(self.item, self.wh_a, self.wh_b, qty=4)

		# Value must follow the stock rather than being created at the target.
		self.assertEqual(get_moving_average_rate(self.item, self.wh_b), 50)

	def test_transfer_rejects_same_source_and_target(self):
		receipt(self.item, self.wh_a, qty=5, rate=10)

		with self.assertRaises(frappe.ValidationError):
			transfer(self.item, self.wh_a, self.wh_a, qty=1)

	def test_transfer_requires_both_warehouses(self):
		with self.assertRaises(frappe.ValidationError):
			make_stock_entry(
				"Transfer",
				[{"item": self.item, "qty": 1, "source_warehouse": self.wh_a}],
			)

	# --- general validation --------------------------------------------

	def test_qty_must_be_positive(self):
		with self.assertRaises(frappe.ValidationError):
			make_stock_entry(
				"Receipt",
				[{"item": self.item, "qty": 0, "rate": 10, "target_warehouse": self.wh_a}],
			)

	def test_entry_requires_at_least_one_row(self):
		with self.assertRaises(frappe.ValidationError):
			make_stock_entry("Receipt", [])

	def test_totals_are_computed(self):
		entry = make_stock_entry(
			"Receipt",
			[
				{"item": self.item, "qty": 3, "rate": 10, "target_warehouse": self.wh_a},
				{"item": self.item, "qty": 2, "rate": 20, "target_warehouse": self.wh_a},
			],
		)

		self.assertEqual(entry.total_qty, 5)
		self.assertEqual(entry.total_value, 70)

	# --- cancellation ---------------------------------------------------

	def test_cancel_reverses_balance(self):
		entry = receipt(self.item, self.wh_a, qty=10, rate=100)
		self.assertEqual(get_stock_balance(self.item, self.wh_a), 10)

		entry.cancel()

		self.assertEqual(get_stock_balance(self.item, self.wh_a), 0)

	def test_cancel_marks_ledger_entries_cancelled_without_deleting(self):
		entry = receipt(self.item, self.wh_a, qty=10, rate=100)
		entry.cancel()

		all_sles = frappe.get_all("Stock Ledger Entry", filters={"voucher_no": entry.name})
		live_sles = frappe.get_all(
			"Stock Ledger Entry", filters={"voucher_no": entry.name, "is_cancelled": 0}
		)

		# The audit trail survives; only the live view changes.
		self.assertEqual(len(all_sles), 1)
		self.assertEqual(len(live_sles), 0)

	def test_cancelled_transfer_reverses_both_sides(self):
		receipt(self.item, self.wh_a, qty=10, rate=50)
		entry = transfer(self.item, self.wh_a, self.wh_b, qty=4)
		entry.cancel()

		self.assertEqual(get_stock_balance(self.item, self.wh_a), 10)
		self.assertEqual(get_stock_balance(self.item, self.wh_b), 0)

	def test_cannot_cancel_receipt_whose_stock_was_consumed(self):
		entry = receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-01-01")
		consume(self.item, self.wh_a, qty=8, posting_date="2026-06-01")

		# Removing the receipt would leave the June consumption overdrawn.
		with self.assertRaises(frappe.ValidationError):
			entry.cancel()

		self.assertEqual(get_stock_balance(self.item, self.wh_a), 2)

	def test_can_cancel_receipt_whose_stock_is_untouched(self):
		entry = receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-01-01")
		entry.cancel()

		self.assertEqual(get_stock_balance(self.item, self.wh_a), 0)

	# --- backdating -----------------------------------------------------

	def test_backdated_consume_cannot_overdraw_a_later_entry(self):
		receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-01-01")
		consume(self.item, self.wh_a, qty=10, posting_date="2026-06-01")

		# There were 10 on hand in March, but June already spends all of them.
		with self.assertRaises(frappe.ValidationError):
			consume(self.item, self.wh_a, qty=10, posting_date="2026-03-01")

		self.assertEqual(get_stock_balance(self.item, self.wh_a), 0)

	def test_backdated_consume_is_allowed_when_stock_covers_it(self):
		receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-01-01")
		consume(self.item, self.wh_a, qty=4, posting_date="2026-06-01")

		consume(self.item, self.wh_a, qty=6, posting_date="2026-03-01")

		self.assertEqual(get_stock_balance(self.item, self.wh_a), 0)

	def test_backdated_receipt_is_allowed(self):
		receipt(self.item, self.wh_a, qty=5, rate=100, posting_date="2026-06-01")
		receipt(self.item, self.wh_a, qty=7, rate=100, posting_date="2026-01-01")

		self.assertEqual(get_stock_balance(self.item, self.wh_a), 12)

	def test_backdated_transfer_cannot_overdraw_the_source(self):
		receipt(self.item, self.wh_a, qty=10, rate=100, posting_date="2026-01-01")
		consume(self.item, self.wh_a, qty=10, posting_date="2026-06-01")

		with self.assertRaises(frappe.ValidationError):
			transfer(self.item, self.wh_a, self.wh_b, qty=10, posting_date="2026-03-01")

		self.assertEqual(get_stock_balance(self.item, self.wh_b), 0)
