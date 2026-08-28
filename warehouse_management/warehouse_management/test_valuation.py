# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

"""Valuation method tests.

The same three movements are replayed under each method throughout: 10 @ 100,
then 30 @ 200, then 20 out. That leaves 20 units on hand worth 3,500 under
Moving Average, 4,000 under FIFO and 3,000 under LIFO, which is the clearest
statement of what choosing a method actually does.
"""

import frappe
from frappe.utils import flt

from warehouse_management.warehouse_management.base_test_case import WarehouseTestCase
from warehouse_management.warehouse_management.doctype.stock_ledger_entry.stock_ledger_entry import (
	get_outgoing_rate,
	get_stock_balance,
	get_stock_value,
	get_valuation_rate,
)
from warehouse_management.warehouse_management.report.stock_balance.stock_balance import (
	execute as stock_balance,
)
from warehouse_management.warehouse_management.report.stock_ledger.stock_ledger import (
	execute as stock_ledger,
)
from warehouse_management.warehouse_management.sample_data import (
	consume,
	create_item,
	create_warehouse,
	make_stock_entry,
	receipt,
	transfer,
)
from warehouse_management.warehouse_management.valuation import (
	FIFO,
	LIFO,
	MOVING_AVERAGE,
	ValuationState,
	get_item_valuation_method,
)


class TestValuationState(WarehouseTestCase):
	"""The engine on its own, with no ledger behind it."""

	def feed(self, method):
		state = ValuationState(method)
		state.add(10, 100)
		state.add(30, 200)
		state.add(-20)
		return state

	def test_moving_average_holds_the_weighted_average(self):
		state = self.feed(MOVING_AVERAGE)
		self.assertEqual(state.qty, 20)
		self.assertEqual(state.rate, 175)
		self.assertEqual(state.value, 3500)

	def test_fifo_keeps_the_newest_layers(self):
		state = self.feed(FIFO)
		# The 10 @ 100 went first, then 10 of the 200s. 20 @ 200 remain.
		self.assertEqual(state.qty, 20)
		self.assertEqual(state.rate, 200)
		self.assertEqual(state.value, 4000)

	def test_lifo_keeps_the_oldest_layers(self):
		state = self.feed(LIFO)
		# 20 came off the 200 layer, leaving 10 @ 100 and 10 @ 200.
		self.assertEqual(state.qty, 20)
		self.assertEqual(state.rate, 150)
		self.assertEqual(state.value, 3000)

	def test_outgoing_rate_does_not_disturb_the_state(self):
		state = ValuationState(FIFO)
		state.add(10, 100)
		state.add(10, 200)

		self.assertEqual(state.outgoing_rate(10), 100)
		self.assertEqual(state.outgoing_rate(10), 100)
		self.assertEqual(state.value, 3000)

	def test_outgoing_rate_blends_layers_it_spans(self):
		state = ValuationState(FIFO)
		state.add(10, 100)
		state.add(10, 200)

		# 10 @ 100 plus 5 @ 200 is 2,000 for 15 units.
		self.assertAlmostEqual(state.outgoing_rate(15), 2000 / 15)

	def test_moving_average_issues_everything_at_the_average(self):
		state = self.feed(MOVING_AVERAGE)
		self.assertEqual(state.outgoing_rate(5), 175)
		self.assertEqual(state.outgoing_rate(20), 175)

	def test_methods_agree_when_every_receipt_shares_a_rate(self):
		for method in (MOVING_AVERAGE, FIFO, LIFO):
			state = ValuationState(method)
			state.add(10, 50)
			state.add(10, 50)
			state.add(-5)

			self.assertEqual(state.value, 750, msg=method)
			self.assertEqual(state.rate, 50, msg=method)

	def test_draw_larger_than_the_layers_empties_them(self):
		state = ValuationState(FIFO)
		state.add(5, 100)
		state.add(-8)

		# Guards make this unreachable through Stock Entry, but valuation must
		# not raise if it is ever reached another way.
		self.assertEqual(state.value, 0)
		self.assertEqual(state.rate, 0)


class TestItemValuationMethod(WarehouseTestCase):
	def setUp(self):
		self.warehouse = create_warehouse("VM Store")

	def test_moving_average_is_the_default(self):
		item = create_item("VM-DEFAULT")

		self.assertEqual(frappe.db.get_value("Item", item, "valuation_method"), MOVING_AVERAGE)
		self.assertEqual(get_item_valuation_method(item), MOVING_AVERAGE)

	def test_blank_method_falls_back_to_moving_average(self):
		item = create_item("VM-BLANK")
		frappe.db.set_value("Item", item, "valuation_method", None, update_modified=False)

		self.assertEqual(get_item_valuation_method(item), MOVING_AVERAGE)

	def test_method_is_changeable_before_any_stock(self):
		item = create_item("VM-FREE")
		doc = frappe.get_doc("Item", item)
		doc.valuation_method = FIFO
		doc.save()

		self.assertEqual(frappe.db.get_value("Item", item, "valuation_method"), FIFO)

	def test_method_is_locked_once_stock_exists(self):
		item = create_item("VM-LOCKED")
		receipt(item, self.warehouse, qty=3, rate=7)

		doc = frappe.get_doc("Item", item)
		doc.valuation_method = LIFO

		with self.assertRaises(frappe.ValidationError):
			doc.save()


class TestValuationThroughTheLedger(WarehouseTestCase):
	"""The same movements through real Stock Entries, per method."""

	def setUp(self):
		self.warehouse = create_warehouse("VL Store")
		self.items = {
			MOVING_AVERAGE: create_item("VL-AVG", valuation_method=MOVING_AVERAGE),
			FIFO: create_item("VL-FIFO", valuation_method=FIFO),
			LIFO: create_item("VL-LIFO", valuation_method=LIFO),
		}

	def build(self, method):
		item = self.items[method]
		receipt(item, self.warehouse, qty=10, rate=100, posting_date="2026-02-01")
		receipt(item, self.warehouse, qty=30, rate=200, posting_date="2026-02-02")
		consume(item, self.warehouse, qty=20, posting_date="2026-02-03")
		return item

	def test_quantity_is_the_same_under_every_method(self):
		for method in self.items:
			item = self.build(method)
			self.assertEqual(get_stock_balance(item, self.warehouse), 20, msg=method)

	def test_value_differs_by_method(self):
		expected = {MOVING_AVERAGE: 3500, FIFO: 4000, LIFO: 3000}

		for method, value in expected.items():
			item = self.build(method)
			self.assertEqual(get_stock_value(item, self.warehouse), value, msg=method)

	def test_rate_differs_by_method(self):
		expected = {MOVING_AVERAGE: 175, FIFO: 200, LIFO: 150}

		for method, rate in expected.items():
			item = self.build(method)
			self.assertEqual(get_valuation_rate(item, self.warehouse), rate, msg=method)

	def test_outgoing_rate_reflects_the_layers_reached(self):
		# 10 @ 100 and 10 @ 200, so the average is 150 rather than the 175 the
		# other scenarios in this class use.
		expected = {MOVING_AVERAGE: 150, FIFO: 100, LIFO: 200}

		for method, rate in expected.items():
			item = self.items[method]
			receipt(item, self.warehouse, qty=10, rate=100, posting_date="2026-02-01")
			receipt(item, self.warehouse, qty=10, rate=200, posting_date="2026-02-02")

			# Moving Average issues at the average, FIFO at the oldest layer,
			# LIFO at the newest.
			self.assertEqual(get_outgoing_rate(item, self.warehouse, 10), rate, msg=method)

	def test_valuation_respects_as_of_datetime(self):
		item = self.build(FIFO)

		# Before the issue, both layers are still on hand: 1,000 + 6,000.
		self.assertEqual(get_stock_value(item, self.warehouse, "2026-02-02 23:59:59"), 7000)
		self.assertEqual(get_stock_value(item, self.warehouse, "2026-12-31 23:59:59"), 4000)

	def test_backdating_a_receipt_reorders_the_fifo_queue(self):
		item = self.items[FIFO]
		receipt(item, self.warehouse, qty=10, rate=200, posting_date="2026-06-01")
		receipt(item, self.warehouse, qty=10, rate=100, posting_date="2026-01-01")
		consume(item, self.warehouse, qty=10, posting_date="2026-07-01")

		# The backdated receipt is now the oldest layer, so it is what leaves.
		# Nothing had to be rewritten for that to be true.
		self.assertEqual(get_stock_value(item, self.warehouse), 2000)
		self.assertEqual(get_valuation_rate(item, self.warehouse), 200)

	def test_cancelled_entries_drop_out_of_layered_valuation(self):
		item = self.items[FIFO]
		first = receipt(item, self.warehouse, qty=10, rate=100, posting_date="2026-02-01")
		receipt(item, self.warehouse, qty=10, rate=200, posting_date="2026-02-02")

		first.cancel()

		self.assertEqual(get_stock_value(item, self.warehouse), 2000)


class TestTransferValuation(WarehouseTestCase):
	def setUp(self):
		self.wh_a = create_warehouse("VT Store A")
		self.wh_b = create_warehouse("VT Store B")

	def stock_up(self, method):
		item = create_item(f"VT-{method.replace(' ', '-')}", valuation_method=method)
		receipt(item, self.wh_a, qty=10, rate=100, posting_date="2026-02-01")
		receipt(item, self.wh_a, qty=30, rate=200, posting_date="2026-02-02")
		return item

	def test_transfer_prices_at_the_layers_that_leave(self):
		expected = {MOVING_AVERAGE: 175, FIFO: 150, LIFO: 200}

		for method, rate in expected.items():
			item = self.stock_up(method)
			transfer(item, self.wh_a, self.wh_b, qty=20, posting_date="2026-03-01")

			# FIFO sends 10 @ 100 and 10 @ 200, so 3,000 for 20 units.
			self.assertEqual(get_valuation_rate(item, self.wh_b), rate, msg=method)

	def test_transfer_is_value_neutral_under_every_method(self):
		for method in (MOVING_AVERAGE, FIFO, LIFO):
			item = self.stock_up(method)
			before = get_stock_value(item, self.wh_a)

			transfer(item, self.wh_a, self.wh_b, qty=20, posting_date="2026-03-01")
			after = get_stock_value(item, self.wh_a) + get_stock_value(item, self.wh_b)

			self.assertEqual(before, 7000, msg=method)
			self.assertEqual(after, 7000, msg=method)

	def test_rows_in_one_entry_draw_from_the_same_layers(self):
		item = create_item("VT-MULTIROW", valuation_method=FIFO)
		receipt(item, self.wh_a, qty=10, rate=100, posting_date="2026-02-01")
		receipt(item, self.wh_a, qty=10, rate=200, posting_date="2026-02-02")

		# Two rows moving 10 each: the first takes the 100 layer, the second the
		# 200 layer. If the rows were priced independently both would say 100.
		entry = make_stock_entry(
			"Transfer",
			[
				{
					"item": item,
					"qty": 10,
					"source_warehouse": self.wh_a,
					"target_warehouse": self.wh_b,
				},
				{
					"item": item,
					"qty": 10,
					"source_warehouse": self.wh_a,
					"target_warehouse": self.wh_b,
				},
			],
			posting_date="2026-03-01",
		)

		rates = sorted(
			flt(r.incoming_rate)
			for r in frappe.get_all(
				"Stock Ledger Entry",
				filters={"voucher_no": entry.name, "is_cancelled": 0, "actual_qty": [">", 0]},
				fields=["incoming_rate"],
			)
		)

		self.assertEqual(rates, [100, 200])
		self.assertEqual(get_stock_value(item, self.wh_b), 3000)
		self.assertEqual(get_stock_value(item, self.wh_a), 0)


class TestReportsRespectValuationMethod(WarehouseTestCase):
	def setUp(self):
		self.wh_a = create_warehouse("VR Store A")
		self.wh_b = create_warehouse("VR Store B")

	def stock_up(self, method, code):
		item = create_item(code, valuation_method=method)
		receipt(item, self.wh_a, qty=10, rate=100, posting_date="2026-02-01")
		receipt(item, self.wh_a, qty=30, rate=200, posting_date="2026-02-02")
		consume(item, self.wh_a, qty=20, posting_date="2026-02-03")
		return item

	def test_stock_balance_values_each_method_correctly(self):
		expected = {MOVING_AVERAGE: (175, 3500), FIFO: (200, 4000), LIFO: (150, 3000)}

		for method, (rate, value) in expected.items():
			item = self.stock_up(method, f"VR-{method.replace(' ', '-')}")
			_columns, rows = stock_balance({"to_date": "2026-12-31", "item": item})

			self.assertEqual(rows[0]["balance_qty"], 20, msg=method)
			self.assertEqual(rows[0]["valuation_rate"], rate, msg=method)
			self.assertEqual(rows[0]["balance_value"], value, msg=method)

	def test_stock_balance_in_and_out_are_method_independent(self):
		for method in (MOVING_AVERAGE, FIFO, LIFO):
			item = self.stock_up(method, f"VR-IO-{method.replace(' ', '-')}")
			_columns, rows = stock_balance({"to_date": "2026-12-31", "item": item})

			self.assertEqual(rows[0]["in_qty"], 40, msg=method)
			self.assertEqual(rows[0]["out_qty"], 20, msg=method)

	def test_consolidated_fifo_sums_warehouse_values(self):
		item = create_item("VR-CONSOL", valuation_method=FIFO)
		receipt(item, self.wh_a, qty=10, rate=100, posting_date="2026-02-01")
		receipt(item, self.wh_b, qty=10, rate=300, posting_date="2026-02-01")

		_columns, rows = stock_balance(
			{"to_date": "2026-12-31", "item": item, "consolidate": 1}
		)

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["balance_qty"], 20)
		self.assertEqual(rows[0]["balance_value"], 4000)
		self.assertEqual(rows[0]["valuation_rate"], 200)

	def test_consolidated_transfer_is_value_neutral_across_mixed_rates(self):
		"""The case the Moving Average consolidation gets wrong.

		Two warehouses holding the same item at different costs, then a transfer
		between them. Summing real layers keeps the total; re-averaging the
		incoming rows would not.
		"""
		item = create_item("VR-MIXED", valuation_method=FIFO)
		receipt(item, self.wh_a, qty=200, rate=2500, posting_date="2026-02-01")
		receipt(item, self.wh_b, qty=120, rate=2650, posting_date="2026-02-02")

		filters = {"to_date": "2026-12-31", "item": item, "consolidate": 1}
		_columns, before = stock_balance(filters)

		transfer(item, self.wh_a, self.wh_b, qty=100, posting_date="2026-03-01")
		_columns, after = stock_balance(filters)

		self.assertEqual(before[0]["balance_value"], 818000)
		self.assertEqual(after[0]["balance_value"], 818000)

	def test_stock_ledger_running_valuation_follows_the_method(self):
		expected = {MOVING_AVERAGE: 175, FIFO: 200, LIFO: 150}

		for method, rate in expected.items():
			item = self.stock_up(method, f"VR-RUN-{method.replace(' ', '-')}")
			_columns, rows = stock_ledger(
				{"from_date": "2026-01-01", "to_date": "2026-12-31", "item": item}
			)

			# First receipt is 100 under every method; the last row is where they part.
			self.assertEqual(rows[0].valuation_rate, 100, msg=method)
			self.assertEqual(rows[-1].valuation_rate, rate, msg=method)
			self.assertEqual(rows[-1].balance_qty, 20, msg=method)
