# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

"""Valuation methods for the stock ledger.

The ledger stores no valuation state, so every method here is a replay: feed the
ledger rows for one item/warehouse in posting order and read the answer off the
end. `ValuationState` is that replay, and it is the only implementation of
valuation in the app -- the reports, the transfer pricing and the point-in-time
helpers all drive this same class.

Moving Average keeps one running value, recalculated whenever stock is acquired
and drawn down at the prevailing average when stock leaves. FIFO and LIFO keep
the cost layers still on hand, because which units you are holding decides what
they are worth.

All three are path dependent, so none of them is a single aggregate query. That
is the price of the stateless ledger: ERPNext gets a one-query read because it
stores the rate on every row, and storing it is exactly what this app does not
do.
"""

import frappe
from frappe.utils import flt

MOVING_AVERAGE = "Moving Average"
FIFO = "FIFO"
LIFO = "LIFO"

VALUATION_METHODS = (MOVING_AVERAGE, FIFO, LIFO)
DEFAULT_VALUATION_METHOD = MOVING_AVERAGE


def get_item_valuation_method(item: str) -> str:
	"""The method configured on an Item, falling back to the default.

	Items created before the field existed hold NULL, so the fallback is load
	bearing rather than defensive.
	"""
	if not item:
		return DEFAULT_VALUATION_METHOD

	method = frappe.db.get_value("Item", item, "valuation_method")
	return method if method in VALUATION_METHODS else DEFAULT_VALUATION_METHOD


class ValuationState:
	"""Running valuation for one item in one warehouse.

	Feed it rows in posting order with `add`. `qty`, `value` and `rate` are
	correct after every row, which is what lets the Stock Ledger report show a
	running valuation and the point-in-time helpers read a single answer.
	"""

	def __init__(self, method: str = DEFAULT_VALUATION_METHOD):
		self.method = method if method in VALUATION_METHODS else DEFAULT_VALUATION_METHOD

		# Signed running balance, authoritative for qty regardless of method.
		self.qty = 0.0

		# Moving Average: the running value of the stock on hand.
		self.average_value = 0.0

		# FIFO / LIFO: cost layers still on hand, oldest first, as [qty, rate].
		self.layers: list[list[float]] = []

	# --- feeding --------------------------------------------------------

	def add(self, actual_qty, incoming_rate=0) -> None:
		actual_qty = flt(actual_qty)
		incoming_rate = flt(incoming_rate)

		if self.method == MOVING_AVERAGE:
			# Recalculated on acquisition against the value already on hand, and
			# reduced at the prevailing average on issue. Reading the rate before
			# moving the quantity is what makes the issue leave at the old average
			# rather than one it helped create.
			if actual_qty > 0:
				self.average_value += actual_qty * incoming_rate
			else:
				self.average_value += actual_qty * self.rate

			self.qty += actual_qty

			if self.qty <= 0:
				# Nothing on hand carries no value, and this keeps float residue
				# from a full issue out of the next receipt's average.
				self.average_value = 0.0

			return

		self.qty += actual_qty

		if actual_qty > 0:
			self.layers.append([actual_qty, incoming_rate])
		elif actual_qty < 0:
			self._consume(-actual_qty)

	def _consume(self, qty: float) -> float:
		"""Remove qty from the layers and return what it cost.

		FIFO takes from the oldest layer, LIFO from the newest. A draw larger
		than the layers hold empties them and reports only what was actually
		there; the negative stock guards make that unreachable through Stock
		Entry, but valuation should not raise if it is ever reached another way.
		"""
		remaining = flt(qty)
		cost = 0.0

		while remaining > 0 and self.layers:
			index = 0 if self.method == FIFO else -1
			layer = self.layers[index]

			taken = min(layer[0], remaining)
			cost += taken * layer[1]
			layer[0] -= taken
			remaining -= taken

			if layer[0] <= 0:
				self.layers.pop(index)

		return cost

	# --- reading --------------------------------------------------------

	@property
	def value(self) -> float:
		"""Value of the stock on hand."""
		if self.method == MOVING_AVERAGE:
			return self.average_value

		return sum(layer[0] * layer[1] for layer in self.layers)

	@property
	def rate(self) -> float:
		"""Cost per unit of the stock on hand."""
		if self.method == MOVING_AVERAGE:
			return (self.average_value / self.qty) if self.qty else 0.0

		layer_qty = sum(layer[0] for layer in self.layers)
		if not layer_qty:
			return 0.0

		return sum(layer[0] * layer[1] for layer in self.layers) / layer_qty

	def outgoing_rate(self, qty) -> float:
		"""Cost per unit of removing qty, without changing this state.

		Under Moving Average every unit leaves at the same average cost. Under
		FIFO and LIFO the answer depends on which layers the draw would reach,
		so this replays the consumption against a copy.
		"""
		qty = flt(qty)
		if qty <= 0:
			return 0.0

		if self.method == MOVING_AVERAGE:
			return self.rate

		probe = ValuationState(self.method)
		probe.layers = [list(layer) for layer in self.layers]
		cost = probe._consume(qty)

		return cost / qty if qty else 0.0


def replay(rows, method: str = DEFAULT_VALUATION_METHOD) -> ValuationState:
	"""Build a ValuationState from ledger rows already in posting order."""
	state = ValuationState(method)
	for row in rows:
		state.add(row.get("actual_qty"), row.get("incoming_rate"))

	return state
