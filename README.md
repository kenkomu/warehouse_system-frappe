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
| Item | Named by `item_code`. Carries the `valuation_method`. Stock UOM and valuation method both lock once ledger entries exist. |
| Warehouse | Tree (`NestedSet`, lft/rgt). Group nodes can't hold stock, and you can't delete one that ledger entries still point at. |
| Stock Entry | Submittable voucher. Purpose is Receipt, Consume or Transfer. |
| Stock Entry Detail | Child table: item, qty, rate, source and target warehouse. |
| Stock Ledger Entry | Append-only. Signed `actual_qty`, `incoming_rate`, `is_cancelled`, and a link back to the voucher. |

![Item list](docs/screenshots/item-list.png)

Valuation Method is the field worth pointing at. The next section covers what
each setting changes.

Warehouses are a real tree, not a flat list with a parent field bolted on. Group
nodes (cities, regions) organise the stock-holding leaves under them.

![Warehouse tree view](docs/screenshots/warehouse-tree.png)

The ledger is the entire state of the system. Signed quantities, a posting
datetime, and a pointer to whatever wrote the row. Nothing else.

![Stock Ledger Entry list](docs/screenshots/stock-ledger-entry-list.png)

### Valuation methods

Every item has a `valuation_method`: Moving Average by default, or FIFO or LIFO.
The choice never changes the quantity on hand, only what it's worth.

Take three movements in one warehouse: 10 @ 100, then 30 @ 200, then 20 out.
Twenty units are left whichever one you pick.

| Method | Rate | Value of the 20 left |
|---|---|---|
| Moving Average | 175 | 3,500 |
| FIFO | 200 | 4,000 |
| LIFO | 150 | 3,000 |

Moving Average blends everything into one cost. FIFO issues the oldest stock
first, so the newer, dearer units are what's left over. LIFO does the opposite.
That's a thousand shillings of difference on identical stock, which is why the
field locks once an item has ledger entries. Changing it wouldn't affect what
happens next, it would restate figures you've already reported.

Moving Average follows the definition the brief links to. The rate is worked out
against the stock you're actually holding:

```
rate = value of stock on hand / quantity on hand
```

That isn't the same as averaging every receipt ever posted. Buy 10 @ 100, sell
all ten, then buy 10 @ 200, and what you're holding is worth 200. Averaging all
the receipts would say 150, still carrying the cost of stock that's gone. My
first version did that, and `TestMatchesErpnextDefinition` now pins it down
against the worked examples on the linked page.

Transfers stay value-neutral under all three, because the receiving warehouse is
charged whatever left the source. Under FIFO that's the cost of the layers the
draw reached, which isn't the rate of what stayed behind, so the two questions
need separate answers.

It all lives in `warehouse_management/valuation.py`. `ValuationState` takes
ledger rows in posting order, and its `qty`, `value` and `rate` are right after
every row. The reports, transfer pricing and point-in-time helpers all drive
that one class, so the three methods can't drift apart.

One consequence worth being straight about. The brief notes that moving average
is a single SQL query in practice, and in ERPNext it is, because ERPNext stores
the rate on every ledger row. This ledger stores nothing, and all three methods
depend on what came before, so valuation is a replay in every case. Quantities
are still one grouped query. That's the price of the stateless ledger, and it
buys the thing the design is for: a backdated entry never means rewriting
history.

### What each purpose writes

| Purpose | Ledger rows |
|---|---|
| Receipt | one `+qty` row at the rate you entered |
| Consume | one `−qty` row at rate 0 |
| Transfer | a `−qty` row at the source, and a `+qty` row at the target carrying the cost of the stock that left |

Carrying the source valuation across is what keeps a transfer value-neutral.
Moving stock between warehouses shouldn't create or destroy value, and the tests
check that under all three methods.

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
and leaves the rate alone, because stock issued at the prevailing average
doesn't disturb it.

**Stock Balance** gives quantity, valuation rate and value as on a date.
Quantities come from one grouped query, value from replaying the movements.
Filters are as-on date, item, warehouse and consolidate.

![Stock Balance report](docs/screenshots/report-stock-balance.png)

Ticking `Consolidate Warehouses` collapses it to one row per item. PB-ANKER-20K
is the interesting one, held in both Nairobi and Mombasa at different rates. The
consolidated figure sums what each warehouse holds rather than averaging across
them, which is what keeps a transfer between the two from moving the total.

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

102 tests, about 10 seconds. The brief asked for all non-report functionality to
be covered and said reports could have unit tests too, so both are in there:

| Suite | Tests |
|---|---|
| Stock Entry | 26 |
| Stock Ledger Entry | 13 |
| Valuation | 33 |
| Stock Balance report | 10 |
| Stock Ledger report | 9 |
| Warehouse | 6 |
| Item | 5 |

The valuation suite runs the same movements under each method and asserts the
three different answers, through the helpers, real Stock Entries and both
reports.

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
