## Warehouse Management

Stock tracking for X Electronics. It's a standalone Frappe app, so it doesn't
need ERPNext installed.

![Warehouse Management workspace](docs/screenshots/workspace.png)

### Why there's no balance column anywhere

This is the decision that shapes everything else, so it's worth explaining up
front. Nothing in this app stores a running balance or a cached valuation rate.
There's no `Bin` table. Every quantity and every value you see gets worked out
by summing `Stock Ledger Entry` rows at the moment you ask for it.

ERPNext does the opposite. It keeps `qty_after_transaction` and `valuation_rate`
on each ledger row. Reading is cheap that way, but a backdated entry means it
has to walk forward and rewrite every row after the one you inserted. A fair
amount of ERPNext's stock complexity lives in that rewrite.

Because I never store those numbers, there's nothing to rewrite. A backdated
entry is just another row and the next read accounts for it. Reads cost more in
exchange, which felt like the right way round for a ledger that gets reported on
far more often than it gets written to.

### DocTypes

| DocType | Notes |
|---|---|
| Item | Named by `item_code`. Stock UOM locks once ledger entries exist. |
| Warehouse | Tree (`NestedSet`, lft/rgt). Group nodes can't hold stock, and you can't delete one that ledger entries still point at. |
| Stock Entry | Submittable voucher. Purpose is Receipt, Consume or Transfer. |
| Stock Entry Detail | Child table: item, qty, rate, source and target warehouse. |
| Stock Ledger Entry | Append-only. Signed `actual_qty`, `incoming_rate`, `is_cancelled`, and a link back to the voucher. |

![Item list](docs/screenshots/item-list.png)

Warehouses are a real tree, not a flat list with a parent field bolted on. Group
nodes (cities, regions) organise the stock-holding leaves under them.

![Warehouse tree view](docs/screenshots/warehouse-tree.png)

The ledger is the entire state of the system. Signed quantities, a posting
datetime, and a pointer to whatever wrote the row. Nothing else.

![Stock Ledger Entry list](docs/screenshots/stock-ledger-entry-list.png)

### Moving average valuation

The brief said this is one SQL query in practice, and it is. It's the weighted
average cost of everything received up to a point in time:

```
rate = SUM(actual_qty * incoming_rate) / SUM(actual_qty)    over incoming rows only
```

That's `get_moving_average_rate` in
`warehouse_management/doctype/stock_ledger_entry/stock_ledger_entry.py`.

Outgoing rows never carry a rate, and I enforce that in two places rather than
one. Stock Entry zeroes the rate on anything that isn't a Receipt, and Stock
Ledger Entry zeroes `incoming_rate` on any negative row no matter what it was
handed. So consumption can't drag the valuation around. Only receipts move it.

### What each purpose writes

| Purpose | Ledger rows |
|---|---|
| Receipt | one `+qty` row at the rate you entered |
| Consume | one `−qty` row at rate 0 |
| Transfer | a `−qty` row at the source, and a `+qty` row at the target carrying the source's moving average |

Carrying the source valuation across is what keeps a transfer value-neutral.
Moving stock between warehouses shouldn't create or destroy value, and
`test_transfer_carries_valuation_across` checks it directly while
`test_transfer_is_value_neutral_when_consolidated` checks it across the whole
system.

A Receipt needs a rate and a target warehouse:

![Stock Entry, Receipt](docs/screenshots/stock-entry-receipt.png)

A Transfer needs both warehouses and no rate, since the value comes from the
source rather than from you:

![Stock Entry, Transfer](docs/screenshots/stock-entry-transfer.png)

All three purposes together, plus a cancelled entry:

![Stock Entry list](docs/screenshots/stock-entry-list.png)

### Negative stock

There are two guards here. The first, `validate_stock_availability`, asks
whether there's enough on hand at the moment you're posting. It handles the
ordinary case and gives a useful per-row error message.

The second one, `validate_timeline_stays_positive`, exists because the first is
blind to a problem that a stateless ledger invites. If you backdate an issue, or
cancel a receipt whose stock has since been used, you leave an entry that
already exists sitting overdrawn. Every one of those entries looked fine when it
was posted. Both cases are easy to reproduce:

```
receipt 10 @ Jan, consume 10 @ Jun, then consume 10 backdated to Mar → balance -10
receipt 10, consume 8, then cancel the receipt                       → balance -8
```

So `get_first_negative_balance` walks the timeline and returns the first point
where the running balance goes below zero. It accepts rows that aren't in the
ledger yet and a voucher to leave out, which lets both submit and cancel check
the timeline they're about to create before they create it. Nothing that gets
rejected ever needs unwinding.

I found this by writing the two cases above and watching them fail, so it's
worth saying the original code shipped with the bug.

### Cancellation

Cancelling sets `is_cancelled = 1` instead of deleting anything, so the audit
trail stays intact. Every balance, valuation and report query filters on
`is_cancelled = 0`, so only the live view changes.

### Reports

**Stock Ledger** gives one row per movement with a running balance and running
valuation across whatever window you filter to. Negative quantities show in red.
Filters are date range, item and warehouse.

![Stock Ledger report](docs/screenshots/report-stock-ledger.png)

The TV rows show the moving average working. Ten arrive on 02-06 at KES 85,000,
five more on 15-06 at KES 95,000, and the rate becomes KES 88,333.33, which is
`(10 × 85,000 + 5 × 95,000) / 15`. The consumption on 01-07 changes the balance
and leaves the rate alone, because only incoming rows count toward the average.

**Stock Balance** gives quantity, valuation rate and value as on a date, in one
grouped query. Filters are as-on date, item, warehouse and consolidate.

![Stock Balance report](docs/screenshots/report-stock-balance.png)

Ticking `Consolidate Warehouses` collapses it to one row per item. PB-ANKER-20K
is the interesting one, since it's stocked in both Nairobi and Mombasa at
different rates and blends to KES 2,556.25.

![Stock Balance report, consolidated](docs/screenshots/report-stock-balance-consolidated.png)

The numbers in these screenshots are demo data I seeded to make the reports
worth looking at. The app installs empty.

### Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app git@github.com:kenkomu/warehouse_system-frappe.git --branch main
bench install-app warehouse_management
```

It ships its own workspace, sidebar and desk icon, so it turns up on the desk
landing screen and `/app/warehouse-management` is a real home page rather than a
list of loose DocTypes.

One thing to know: the DocTypes grant permissions to `Stock Manager` and
`Stock User`, but the app doesn't create those roles yet. On a fresh install
only System Manager can get in. Create the two roles by hand, or see the gaps
section below.

### Tests

```bash
bench --site $YOUR_SITE run-tests --app warehouse_management
```

69 tests, about 7 seconds. The brief asked for all non-report functionality to
be covered and said reports could have unit tests too, so both are in there:

| Suite | Tests |
|---|---|
| Stock Entry | 26 |
| Stock Ledger Entry | 13 |
| Stock Balance report | 10 |
| Stock Ledger report | 9 |
| Warehouse | 6 |
| Item | 5 |

`WarehouseTestCase` overrides `tearDown` so each test rolls back on its own.
Frappe's `IntegrationTestCase` registers its rollback through `addClassCleanup`,
which only fires once the whole class has finished. These tests assert on stock
balances, so documents leaking from one test into the next made them fail
depending on what order they ran in.

### Known gaps

- `disabled` on Item and Warehouse is in the schema but nothing acts on it.
- The `Stock Manager` and `Stock User` roles aren't created on install, so a
  fresh install only works for System Manager.
- `get_children` and `add_node` (the tree view endpoints) aren't tested, and
  neither is document amendment.
- The workspace, sidebar and desk icon ship as fixtures with no tests behind
  them. The screenshots above are the only thing verifying they render.

### Contributing

Formatting and linting go through `pre-commit` (ruff, eslint, prettier,
pyupgrade):

```bash
cd apps/warehouse_management
pre-commit install
```

### License

mit
