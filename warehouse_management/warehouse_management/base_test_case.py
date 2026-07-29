# Copyright (c) 2026, Ken Njoroge and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


class WarehouseTestCase(IntegrationTestCase):
	"""Base case that rolls the database back after every test.

	Frappe's IntegrationTestCase registers its rollback with addClassCleanup, so it
	only runs once the whole class has finished. Stock tests assert on balances, so
	documents leaking from one test into the next makes them fail in ways that depend
	on execution order. Rolling back per test keeps each one isolated.
	"""

	def tearDown(self) -> None:
		frappe.db.rollback()
		super().tearDown()
