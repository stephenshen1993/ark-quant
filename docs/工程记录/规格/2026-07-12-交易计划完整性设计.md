# Trading Plan Integrity Design

## Goal

Make every executable trading plan reproducible from immutable, exact inputs. A plan must never combine stale, missing, silently copied, or same-date-but-different-version data.

## Chosen Approach

Use immutable plan versions on top of the existing SQLite application. This is safer than adding more date comparisons and much smaller than introducing a full event-sourced ledger.

Each plan version references:

- one market-temperature record;
- the latest exact-date value snapshot for each of the five accounts;
- one exact-date position snapshot for stocks and one for convertible bonds;
- one complete stock ranking run and one complete convertible-bond ranking run;
- the persisted transfer calculation derived from those inputs.

Orders belong to the plan version, not directly to a strategy run. Regenerating a strategy or saving an account again on the same date creates a different input fingerprint, so old orders cannot appear in the new plan.

## Data Model

### Position snapshots

Add `position_snapshots` and `position_snapshot_items`. Every save appends a header, including saves of an intentionally empty portfolio. This distinguishes an empty snapshot from a missing snapshot and preserves prior versions.

Legacy `cb_positions` and `stock_positions` remain readable during migration. On database initialization, the latest legacy rows for each strategy/date are copied once into immutable snapshots.

### Strategy runs

Add `status` to `strategy_runs`. New ranking results are stored by one database function using one transaction for the run header and ranking rows. Only `complete` runs with at least one ranking row are eligible plan inputs.

### Plan versions

Add `plan_versions` with an input fingerprint and exact foreign-key identifiers. Account snapshot IDs are stored as canonical JSON because the account set is fixed and small. Persist transfer targets, deltas, and steps as JSON.

Add `plan_orders`, keyed by `(plan_version_id, strategy, code)`. Re-sizing replaces orders only within that immutable input version. A new input fingerprint receives a new plan version with no inherited orders.

## Temperature Freshness

Normal reads reuse a temperature record only while its `fetched_at` is within a one-hour TTL. After the TTL, the application checks the official source once and refreshes `fetched_at` when the source value is unchanged. If the source cannot be checked after expiry, executable plan generation fails with a structured 503; the UI may still display the cached value as stale context.

A source response with the same source timestamp but a different value is treated as a conflict instead of mutating a historical input.

## Plan Resolution

Plan generation resolves all exact inputs in one SQLite read transaction. Validation requires:

- all five account snapshots on the temperature source date;
- exact-date stock and convertible-bond position snapshot headers;
- complete nonempty stock and convertible-bond ranking runs;
- no newer account snapshot with the same plan date being substituted after resolution.

The resolver calculates a SHA-256 fingerprint from the exact input IDs. It then returns the existing plan version for that fingerprint or creates one with persisted transfer results.

Transfer calculation errors fail with a structured 500. An empty transfer list is valid only when calculation succeeds and all deltas are below the existing action threshold.

## Order Sizing

The frontend submits `plan_id`, never cash. The backend loads the persisted plan version, derives available cash from its account and transfer snapshots, verifies that it is still the current fingerprint, and sizes orders against the ranking and position snapshots referenced by that plan.

Market prices used for executable orders remain current quotes and are stored on each order. Historical account totals are never recalculated from current quotes.

## Account Workflow

The account page loads exact-date positions. If none exist, it shows an explicit missing state; it does not load `asof` rows into editable current-date data. Saving positions and the associated account value occurs through one API call and one transaction.

The page never fabricates an update timestamp. A missing factual `updated_at` displays as `未更新`. Saves are disabled when plan context cannot be loaded.

## Trading Calendar

The temperature source date is authoritative for plan alignment. Strategy runs must return their actual source data date and may only become eligible when it equals the current plan date. `trade_date` uses AkShare's A-share calendar with a local database cache; weekend-only logic remains only as a fail-closed fallback and cannot produce an executable plan without calendar confirmation.

## Error Handling

- Missing or mismatched inputs: HTTP 409 with per-input details.
- Stale temperature that cannot be refreshed: HTTP 503.
- Transfer or persistence failure: HTTP 500; never an incomplete 200.
- Stale plan ID or changed input fingerprint during sizing: HTTP 409.
- Invalid dates, negative totals/cash/shares, or unknown strategies: HTTP 422/400 before external calls.

## Acceptance Criteria

1. Re-saving any same-date account or rerunning either strategy invalidates previously generated orders for the current view.
2. Missing, empty-but-unconfirmed, or older fallback positions cannot produce a plan.
3. An intentionally empty saved position snapshot is valid and distinguishable from missing data.
4. A ghost strategy run with no ranking rows cannot satisfy validation.
5. Normal temperature requests perform at most one official-source check per TTL window.
6. An expired cache plus source failure cannot generate an executable plan.
7. Order sizing cannot accept client-provided cash and cannot bind to a different strategy run during execution.
8. Account update timestamps always come from persisted snapshot creation times.
9. Full automated tests pass, including adversarial date/version/concurrency cases.
10. Account-page refresh sends no plan or ranking requests; trading-page initialization sends one request set.

