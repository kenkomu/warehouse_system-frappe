## Warehouse Management

Warehouse management system for X Electronics — a self-contained Frappe app for
tracking stock across a warehouse tree, built without ERPNext.

![Warehouse Management workspace](docs/screenshots/workspace.png)

### The design in one idea

**The ledger is stateless.** There is no `Bin` table, no stored balance, no
cached valuation column anywhere in the schema. Every quantity and value in the
system is derived by aggregating `Stock Ledger Entry` at read time.

This is the deliberate departure from ERPNext, and it buys one specific thing:
**entries posted out of order need no recalculation.** ERPNext maintains running
`qty_after_transaction` and `valuation_rate` columns on each ledger row, so
inserting a backdated entry forces it to walk forward and rewrite every
subsequent row. Here there is nothing to rewrite — a backdated entry is just
another row, and the next read accounts for it.

The cost is paid on read instead of write, which is the right trade for a
ledger that is written far less often than it is reported on.

### DocTypes

| DocType | Notes |
|---|---|
| **Item** | Named by `item_code`. Stock UOM locks once ledger entries exist. |
| **Warehouse** | Tree (`NestedSet`, lft/rgt). Group nodes cannot hold stock. Cannot be deleted while ledger entries reference it. |
| **Stock Entry** | Submittable voucher. Purpose: Receipt, Consume, or Transfer. |
| **Stock Entry Detail** | Child table: item, qty, rate, source/target warehouse. |
| **Stock Ledger Entry** | Append-only. Signed `actual_qty`, `incoming_rate`, `is_cancelled`, voucher backlink. |

![Item list](docs/screenshots/item-list.png)

Warehouses are a genuine tree — group nodes (cities, regions) organise the
stock-holding leaves beneath them:

![Warehouse tree view](docs/screenshots/warehouse-tree.png)

The ledger itself is the whole state of the system. Signed quantities, a
posting datetime, and a backlink to the voucher that wrote each row — nothing
else is stored:

![Stock Ledger Entry list](docs/screenshots/stock-ledger-entry-list.png)

### Moving average valuation

The whole calculation is a single SQL query — the weighted average cost of
everything received so far:

```
rate = SUM(actual_qty * incoming_rate) / SUM(actual_qty)    over incoming rows only
```

See `get_moving_average_rate` in
`warehouse_management/doctype/stock_ledger_entry/stock_ledger_entry.py`.

Outgoing rows never carry a rate. This is enforced twice, independently: the
Stock Entry zeroes the rate on any non-Receipt row, and the Stock Ledger Entry
zeroes `incoming_rate` on any negative row regardless of what it was handed.
Consumption therefore cannot move the valuation rate — only receipts can.

### How each purpose writes to the ledger

| Purpose | Ledger rows |
|---|---|
| **Receipt** | one `+qty` row at the entered rate |
| **Consume** | one `−qty` row at rate 0 |
| **Transfer** | a `−qty` row at the source, plus a `+qty` row at the target carrying the **source's** moving average |

Carrying the source valuation across is what makes a transfer value-neutral:
moving stock between warehouses never creates or destroys value. Asserted
directly in `test_transfer_carries_valuation_across` and system-wide in
`test_transfer_is_value_neutral_when_consolidated`.

A Receipt takes a rate and a target warehouse:

![Stock Entry — Receipt](docs/screenshots/stock-entry-receipt.png)

A Transfer takes both warehouses and no rate — the value is read from the
source, not entered:

![Stock Entry — Transfer](docs/screenshots/stock-entry-transfer.png)

All three purposes, and a cancelled entry, in the list view:

![Stock Entry list](docs/screenshots/stock-entry-list.png)

### Negative stock

Two guards, because one is not enough:

1. **`validate_stock_availability`** — is there enough on hand at the moment
   being posted? Gives a precise per-row error for the common case.
2. **`validate_timeline_stays_positive`** — does the *whole timeline* for each
   affected item/warehouse stay non-negative?

The second exists because the first is blind to a class of bug that a stateless
ledger specifically invites. Backdating an issue, or cancelling a receipt whose
stock has since been drawn down, leaves an *already-existing later* entry
overdrawn while every individual entry still looked valid when it was posted:

```
receipt 10 @ Jan, consume 10 @ Jun, then consume 10 backdated to Mar → balance -10
receipt 10, consume 8, then cancel the receipt                       → balance -8
```

`get_first_negative_balance` walks the timeline and reports the first point the
running balance dips below zero. It takes pending rows and a voucher to exclude,
so submit and cancel both test the *projected* timeline before writing to it —
a rejected change never has to be unwound.

### Cancellation

Cancelling flags `is_cancelled = 1` rather than deleting rows, so the audit
trail survives. Every balance, valuation and report query filters on
`is_cancelled = 0`; only the live view changes.

### Reports

**Stock Ledger** — one row per movement, with running balance and running
valuation accumulated across the filtered window. Negative quantities render
red. Filters: date range, item, warehouse.

![Stock Ledger report](docs/screenshots/report-stock-ledger.png)

The TV rows are the moving average doing its work: 10 land on 02-06 at
KES 85,000, then 5 more on 15-06 at KES 95,000, and the valuation rate becomes
KES 88,333.33 — `(10 × 85,000 + 5 × 95,000) / 15`. The consumption on 01-07
moves the balance but leaves the rate untouched, because only incoming rows
enter the average.

**Stock Balance** — quantity, valuation rate and value as on a date, in a
single grouped query. Filters: as-on date, item, warehouse, consolidate.

![Stock Balance report](docs/screenshots/report-stock-balance.png)

`Consolidate Warehouses` collapses the same data to one row per item — note
PB-ANKER-20K, stocked in both Nairobi and Mombasa at different rates, resolving
to a single blended KES 2,556.25:

![Stock Balance report, consolidated](docs/screenshots/report-stock-balance-consolidated.png)

### Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app git@github.com:kenkomu/warehouse_system-frappe.git --branch main
bench install-app warehouse_management
```

The app installs its own workspace, sidebar and desk icon, so it appears on the
desk landing screen and `/app/warehouse-management` is a real home page.

> **Note:** the DocTypes grant permissions to `Stock Manager` and `Stock User`,
> but the app does not yet create those roles. On a fresh install only
> System Manager has access — create the two roles manually, or see
> *Known gaps* below.

### Tests

```bash
bench --site $YOUR_SITE run-tests --app warehouse_management
```

69 tests, ~7s. Coverage is per the brief — all non-report functionality, plus
unit tests for both reports:

| Suite | Tests |
|---|---|
| Stock Entry | 26 |
| Stock Ledger Entry | 13 |
| Warehouse | 6 |
| Item | 5 |
| Stock Balance report | 10 |
| Stock Ledger report | 9 |

`WarehouseTestCase` overrides `tearDown` to roll back per test. Frappe's
`IntegrationTestCase` registers its rollback with `addClassCleanup`, so it only
fires once the whole class finishes — and stock tests assert on balances, so
documents leaking between tests make them fail in execution-order-dependent
ways.

### Known gaps

Honest list of what is not done:

- `disabled` on Item and Warehouse is schema-only — nothing enforces it.
- `Stock Manager` / `Stock User` roles are not created on install, so a fresh
  install is usable only by System Manager.
- `get_children` / `add_node` (tree view endpoints) and document amendment are
  untested.
- The workspace, sidebar and desk icon are shipped as fixtures but have no
  tests; they are verified only by the screenshots above.

### Screenshots

The figures shown are demo data, not fixtures — the app installs empty. All
screenshots live in [`docs/screenshots/`](docs/screenshots).

### Contributing

This app uses `pre-commit` for formatting and linting (ruff, eslint, prettier,
pyupgrade):

```bash
cd apps/warehouse_management
pre-commit install
```

### License

mit
