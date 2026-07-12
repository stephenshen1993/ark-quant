# Trading Algorithm Safety Implementation Plan

> **For agentic workers:** Follow the Superpowers executing-plans style: implement task-by-task, update checkbox status as work completes, and verify each safety property with tests before declaring done.

**Goal:** Make current trading-order sizing fail closed for unsafe cash and data-source edge cases without restarting the larger immutable plan-version project.

**Architecture:** Keep the current `/api/plan` and `/api/plan/{strategy}/size-orders` shape. Tighten the service-layer algorithm around server-derived cash, quote completeness, confirmed empty position snapshots, and response summaries.

**Tech Stack:** FastAPI, SQLite, pandas, Python unittest, Alpine.js.

---

### Task 1: Tests for Unsafe Sizing Boundaries

**Files:**
- Modify: `tests/test_api_plans.py`

- [x] Add coverage proving a strategy account cannot size orders when required cash release exceeds sellable holdings.
- [x] Add coverage proving convertible-bond partial quote failures return a structured HTTP error.
- [x] Add coverage proving intentionally empty position snapshots are valid plan inputs while missing snapshots still fail.
- [x] Add coverage proving plan summaries report transfer deltas even when no transfer step is generated.

### Task 2: Fail Closed in Plan Sizing

**Files:**
- Modify: `app/routers/plans.py`

- [x] Convert convertible-bond partial quote gaps into structured HTTP errors before calling `size_rebalance`.
- [x] Reject sized orders before persistence when `summary.cash_left` is negative.
- [x] Convert sizing `SystemExit` failures into structured HTTP errors.

### Task 3: Preserve Empty Snapshot Semantics

**Files:**
- Modify: `app/routers/plans.py`

- [x] Resolve plan positions from snapshot metadata, not compatibility item lists.
- [x] Treat a found snapshot with `items=[]` as confirmed empty.
- [x] Keep missing snapshots as a blocking plan-input error.

### Task 4: Summary Consistency

**Files:**
- Modify: `app/routers/plans.py`

- [x] Make order summaries display the same transfer delta used for server-side sizing, regardless of whether a textual transfer step was emitted.

### Task 5: Verification

- [x] Run `python -m unittest tests.test_api_plans`.
- [x] Run focused core tests: `tests.test_api_accounts tests.test_api_rankings tests.test_db tests.test_youzhiyouxing`.
- [x] Run full `python -m unittest discover tests`.
- [x] Review git diff and leave unrelated workspace files untouched.
