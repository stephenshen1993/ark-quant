# Trading Plan Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make trading plans immutable, version-bound, fail-closed, and reproducible from exact temperature, account, position, ranking, and transfer inputs.

**Architecture:** Add immutable position and plan-version tables while retaining legacy reads for migration. Resolve exact input IDs into a fingerprint, persist transfer results on the plan version, and bind orders to that plan instead of accepting cash from the browser.

**Tech Stack:** FastAPI, Pydantic, SQLite, pandas, Alpine.js, Python unittest.

---

### Task 1: Temperature freshness and validation

**Files:**
- Modify: `datasource/youzhiyouxing.py`
- Modify: `datasource/db.py`
- Create: `tests/test_youzhiyouxing.py`

- [ ] Write tests for fresh cache reuse, TTL refresh, expired-cache failure, malformed HTML, out-of-range values, and same-timestamp conflicts.
- [ ] Run `python -m unittest tests.test_youzhiyouxing` and confirm the new tests fail for the intended missing behavior.
- [ ] Implement one-hour freshness checks, atomic refresh metadata updates, value/range validation, and immutable conflict handling.
- [ ] Re-run the focused tests and `tests.test_api_plans`.

### Task 2: Immutable position snapshots and atomic account saves

**Files:**
- Modify: `datasource/db.py`
- Modify: `app/routers/accounts.py`
- Modify: `app/routers/positions.py`
- Modify: `tests/test_db.py`
- Modify: `tests/test_api_accounts.py`

- [ ] Write failing tests that distinguish missing from intentionally empty snapshots, preserve multiple same-date versions, and roll back an account save when either value or positions fail.
- [ ] Add `position_snapshots` and `position_snapshot_items`, plus idempotent legacy migration.
- [ ] Add exact-date snapshot read APIs and one transactional account-and-positions write API.
- [ ] Validate ISO dates, nonnegative totals/cash/shares, and normalized security codes.
- [ ] Run account and database tests.

### Task 3: Transactional complete strategy runs

**Files:**
- Modify: `datasource/db.py`
- Modify: `strategies/cb_rotation/run.py`
- Modify: `strategies/stock_smallcap/run.py`
- Modify: `app/routers/rankings.py`
- Modify: `tests/test_api_rankings.py`
- Modify: `tests/test_db.py`

- [ ] Write failing tests proving a run header without rankings is ineligible and persistence failure propagates.
- [ ] Add run status migration and one-transaction ranking-result writers.
- [ ] Return the newly persisted run ID from each strategy and from the ranking API.
- [ ] Make ranking-date queries include only complete, nonempty runs.
- [ ] Run ranking, stock-write, and convertible-bond-write tests.

### Task 4: Immutable plan versions and server-derived sizing

**Files:**
- Modify: `datasource/db.py`
- Modify: `app/routers/plans.py`
- Modify: `tests/test_api_plans.py`

- [ ] Write failing tests for exact input resolution, same-date account/ranking invalidation, missing positions, empty confirmed positions, ghost runs, transfer failures, stale plan IDs, and client cash rejection.
- [ ] Add `plan_versions` and `plan_orders` tables.
- [ ] Resolve exact IDs in one transaction and compute a canonical SHA-256 input fingerprint.
- [ ] Persist transfer targets/deltas/steps and fail closed on calculation errors.
- [ ] Change sizing input to `plan_id`; derive cash and referenced datasets entirely on the server.
- [ ] Persist and retrieve orders by plan version.
- [ ] Run all plan API tests.

### Task 5: Exact-date frontend state and truthful timestamps

**Files:**
- Modify: `app/static/index.html`
- Modify: `tests/test_frontend_contract.py`

- [ ] Add contract tests proving account loads use exact dates, sizing submits `plan_id`, no UTC fallback exists, and timestamp fallback cannot use plan date.
- [ ] Load exact positions without `asof=true`; show missing state and require explicit save.
- [ ] Use the atomic account save endpoint and stop recalculating historical totals from live quotes.
- [ ] Disable save until plan context succeeds and display `未更新` without factual timestamps.
- [ ] Reset all date-bound trading state when refreshed temperature changes the plan date.
- [ ] Run frontend contract and API tests.

### Task 6: Calendar safety, regression suite, and browser acceptance

**Files:**
- Modify: `datasource/trade_calendar.py`
- Modify: `strategies/stock_smallcap/run.py`
- Modify: `datasource/db.py`
- Modify: `tests/test_trade_calendar.py`
- Modify: `README.md`
- Modify: `docs/操作/日常看板流程.md`

- [ ] Add tests for weekday holidays, pre-open, post-close, weekends, and unconfirmed calendar data.
- [ ] Cache the AkShare A-share trade calendar and require confirmed dates for executable plans.
- [ ] Run `python -m unittest discover -s tests` and require zero failures/errors.
- [ ] Run compile and diff checks.
- [ ] Verify account and trading pages in a real browser, including request counts, refresh state, 409/503 messages, and no duplicate initialization.
- [ ] Perform a final independent code review against the design acceptance criteria.
