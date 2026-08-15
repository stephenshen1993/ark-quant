"""Shared exact discrete equal-weight optimizer for account strategies."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from fractions import Fraction
from math import gcd, inf

import numpy as np

from strategies.discrete_target_sizing import (
    DiscreteSizingError,
    FeeSchedule,
    score_targets,
)

MONEY_SCALE = 1_000
CENT_MILLS = 10
MAX_STATES_PER_CODE = 10_000
MAX_EXACT_FLOAT_INTEGER = 2**53


@dataclass(frozen=True)
class EqualWeightTargetResult:
    targets: dict[str, int]
    summary: dict


@dataclass(frozen=True)
class _TargetState:
    code: str
    shares: int
    value_mills: int
    turnover_mills: int
    sell_turnover_mills: int
    is_trade: bool


@dataclass(frozen=True)
class _Problem:
    target_codes: list[str]
    holdings: dict[str, int]
    prices: dict[str, float]
    lot: int
    price_mills: dict[str, int]
    cash_mills: int
    holdings_value_mills: int
    total_value_mills: int
    cap_value_mills: int
    threshold_mills: int
    allow_target_sells: bool
    fee_schedule: FeeSchedule
    mandatory_exit_codes: list[str]
    mandatory_turnover_mills: int
    mandatory_sell_turnover_mills: int
    mandatory_order_count: int
    risk_targets: dict[str, int]


@dataclass(frozen=True)
class _StateSpace:
    states: list[_TargetState]
    state_indices_by_code: list[list[int]]
    next_state_by_index: dict[int, int]


@dataclass(frozen=True)
class _VariableLayout:
    fee_cents: int
    raw_fee_cents: int
    has_trade: int
    minimum_fee_regime: int
    target_value_mills: int
    max_deviation: int
    variable_count: int


class _MilpModel:
    def __init__(
        self,
        *,
        lower: np.ndarray,
        upper: np.ndarray,
        integrality: np.ndarray,
        state_count: int,
    ) -> None:
        self.lower = lower
        self.upper = upper
        self.integrality = integrality
        self.state_count = state_count
        self.rows: list[dict[int, float]] = []
        self.lower_bounds: list[float] = []
        self.upper_bounds: list[float] = []
        self.solve_count = 0
        self.presolve_retry_count = 0
        self.mip_gaps: list[float] = []
        self.node_count = 0

    def add_constraint(
        self,
        coefficients: dict[int, float],
        *,
        lower: float = -inf,
        upper: float = inf,
    ) -> None:
        self.rows.append({index: value for index, value in coefficients.items() if value})
        self.lower_bounds.append(lower)
        self.upper_bounds.append(upper)

    def solve(self, objective: np.ndarray, *, presolve: bool = True):
        try:
            from scipy.optimize import Bounds, LinearConstraint, milp
            from scipy.sparse import coo_matrix
        except ImportError as exc:
            raise DiscreteSizingError(
                "SOLVER_UNAVAILABLE",
                "缺少 SciPy/HiGHS，无法生成可证明最优的离散交易计划",
            ) from exc

        row_indices: list[int] = []
        column_indices: list[int] = []
        values: list[float] = []
        for row_index, row in enumerate(self.rows):
            for column_index, value in row.items():
                row_indices.append(row_index)
                column_indices.append(column_index)
                values.append(value)
        matrix = coo_matrix(
            (values, (row_indices, column_indices)),
            shape=(len(self.rows), len(self.lower)),
        ).tocsc()
        matrix.indices = matrix.indices.astype(np.int32, copy=False)
        matrix.indptr = matrix.indptr.astype(np.int32, copy=False)
        constraints = LinearConstraint(matrix, self.lower_bounds, self.upper_bounds)

        def run(*, use_presolve: bool):
            self.solve_count += 1
            return milp(
                c=np.asarray(objective, dtype=float),
                integrality=self.integrality,
                bounds=Bounds(self.lower, self.upper),
                constraints=constraints,
                options={
                    "disp": False,
                    "mip_rel_gap": 0.0,
                    "presolve": use_presolve,
                },
            )

        def violations(result) -> list[tuple[int, float, float, float]]:
            if result.x is None:
                return []
            found = []
            for row_index, (row, lower_bound, upper_bound) in enumerate(
                zip(self.rows, self.lower_bounds, self.upper_bounds)
            ):
                value = sum(
                    coefficient * result.x[index]
                    for index, coefficient in row.items()
                )
                if value < lower_bound - 1e-4 or value > upper_bound + 1e-4:
                    found.append((row_index, value, lower_bound, upper_bound))
            return found

        result = run(use_presolve=presolve)
        found = violations(result)
        if presolve and (result.status in {2, 4} or found):
            self.presolve_retry_count += 1
            result = run(use_presolve=False)
            found = violations(result)
        gap = getattr(result, "mip_gap", None)
        if gap is not None and np.isfinite(gap):
            self.mip_gaps.append(float(gap))
        nodes = getattr(result, "mip_node_count", None)
        if nodes is not None:
            self.node_count += int(nodes)
        if result.status != 0 or result.x is None or (gap is not None and gap > 1e-9):
            raise DiscreteSizingError(
                "SOLVER_NOT_OPTIMAL",
                "离散等权求解器未证明全局最优",
                solver_status=int(result.status),
                solver_message=str(result.message),
                mip_gap=gap,
            )
        if found:
            raise DiscreteSizingError(
                "SOLVER_NOT_OPTIMAL",
                "离散等权求解结果未通过约束复核",
                violations=found[:5],
            )
        return result


def _to_mills(value: float, *, floor: bool = False) -> int:
    rounding = ROUND_FLOOR if floor else ROUND_HALF_UP
    return int(
        (Decimal(str(value)) * MONEY_SCALE).quantize(
            Decimal("1"),
            rounding=rounding,
        )
    )


def _equal_weight_risk_target_shares(
    *,
    current_shares: int,
    equal_weight_value_mills: int,
    price_mills: int,
    lot: int,
) -> int:
    remainder = current_shares % lot
    current_lots = (current_shares - remainder) // lot
    minimum_lots = 0 if remainder else 1
    maximum_lots = current_lots - 1
    lot_cost_mills = price_mills * lot
    value_after_remainder = max(
        0,
        equal_weight_value_mills - remainder * price_mills,
    )
    lower_lots = value_after_remainder // lot_cost_mills
    candidate_lots = {
        min(maximum_lots, max(minimum_lots, lower_lots)),
        min(maximum_lots, max(minimum_lots, lower_lots + 1)),
    }
    return int(
        min(
            (remainder + lots * lot for lots in candidate_lots),
            key=lambda shares: (
                abs(shares * price_mills - equal_weight_value_mills),
                -shares,
            ),
        )
    )


def _prepare_problem(
    *,
    target_codes: list[str],
    holdings: dict[str, int],
    prices: dict[str, float],
    cash: float,
    lot: int,
    max_single_weight: float,
    ordinary_order_threshold: float,
    allow_target_sells: bool,
    fee_schedule: FeeSchedule,
) -> _Problem:
    target_codes = list(dict.fromkeys(target_codes))
    holdings = {
        code: int(shares)
        for code, shares in holdings.items()
        if int(shares) > 0
    }
    all_codes = set(target_codes) | set(holdings)
    missing = sorted(
        code
        for code in all_codes
        if code not in prices
        or not np.isfinite(prices[code])
        or prices[code] <= 0
    )
    if missing:
        raise DiscreteSizingError("MISSING_QUOTE", "缺少报价，无法定额", codes=missing)
    if not target_codes:
        raise DiscreteSizingError("CAPACITY_CONFLICT", "目标集合为空，无法定额")
    if lot <= 0:
        raise ValueError("lot must be positive")
    if not 0 < max_single_weight <= 1:
        raise ValueError("max_single_weight must be in (0, 1]")

    price_mills = {code: _to_mills(prices[code]) for code in all_codes}
    normalized_prices = {
        code: price_mills[code] / MONEY_SCALE
        for code in all_codes
    }
    cash_mills = _to_mills(cash, floor=True)
    holdings_value_mills = sum(
        holdings[code] * price_mills[code]
        for code in holdings
    )
    total_value_mills = holdings_value_mills + cash_mills
    if total_value_mills <= 0:
        raise DiscreteSizingError(
            "CAPACITY_CONFLICT",
            "可执行预算不足以形成目标组合",
            total_value=total_value_mills / MONEY_SCALE,
        )
    cap_value_mills = int(
        (Decimal(total_value_mills) * Decimal(str(max_single_weight))).quantize(
            Decimal("1"),
            rounding=ROUND_FLOOR,
        )
    )
    target_set = set(target_codes)
    mandatory_exit_codes = sorted(set(holdings) - target_set)
    mandatory_turnover_mills = sum(
        holdings[code] * price_mills[code]
        for code in mandatory_exit_codes
    )
    risk_targets: dict[str, int] = {}
    equal_weight_value_mills = total_value_mills // len(target_codes)
    for code in target_codes:
        current = holdings.get(code, 0)
        if current * price_mills[code] > cap_value_mills:
            risk_targets[code] = _equal_weight_risk_target_shares(
                current_shares=current,
                equal_weight_value_mills=equal_weight_value_mills,
                price_mills=price_mills[code],
                lot=lot,
            )
    mandatory_risk_turnover = sum(
        (holdings[code] - target_shares) * price_mills[code]
        for code, target_shares in risk_targets.items()
    )
    return _Problem(
        target_codes=target_codes,
        holdings=holdings,
        prices=normalized_prices,
        lot=lot,
        price_mills=price_mills,
        cash_mills=cash_mills,
        holdings_value_mills=holdings_value_mills,
        total_value_mills=total_value_mills,
        cap_value_mills=cap_value_mills,
        threshold_mills=_to_mills(ordinary_order_threshold),
        allow_target_sells=allow_target_sells,
        fee_schedule=fee_schedule,
        mandatory_exit_codes=mandatory_exit_codes,
        mandatory_turnover_mills=mandatory_turnover_mills + mandatory_risk_turnover,
        mandatory_sell_turnover_mills=mandatory_turnover_mills + mandatory_risk_turnover,
        mandatory_order_count=len(mandatory_exit_codes) + len(risk_targets),
        risk_targets=risk_targets,
    )


def _build_state_space(problem: _Problem) -> _StateSpace:
    states: list[_TargetState] = []
    state_indices_by_code: list[list[int]] = []
    next_state_by_index: dict[int, int] = {}
    for code in problem.target_codes:
        current = problem.holdings.get(code, 0)
        price_mills = problem.price_mills[code]
        maximum_shares = problem.cap_value_mills // price_mills
        if code in problem.risk_targets:
            candidates = [problem.risk_targets[code]]
        elif problem.allow_target_sells:
            remainder = current % problem.lot
            first = remainder or problem.lot
            candidates = [
                shares
                for shares in range(first, int(maximum_shares) + 1, problem.lot)
                if shares == current
                or abs(shares - current) * price_mills >= problem.threshold_mills
            ]
        else:
            candidates = [current] if current > 0 else []
            maximum_steps = (maximum_shares - current) // problem.lot
            candidates.extend(
                current + step * problem.lot
                for step in range(1, int(maximum_steps) + 1)
                if step * problem.lot * price_mills >= problem.threshold_mills
            )
        candidates = sorted(set(int(shares) for shares in candidates if shares > 0))
        if len(candidates) > MAX_STATES_PER_CODE:
            raise DiscreteSizingError(
                "CAPACITY_CONFLICT",
                "目标标的可行状态过多，报价或账户规模异常",
                code=code,
                state_count=len(candidates),
            )
        indices = []
        for shares in candidates:
            delta = shares - current
            is_mandatory_risk_state = code in problem.risk_targets
            index = len(states)
            indices.append(index)
            states.append(
                _TargetState(
                    code=code,
                    shares=shares,
                    value_mills=shares * price_mills,
                    turnover_mills=(
                        0 if is_mandatory_risk_state else abs(delta) * price_mills
                    ),
                    sell_turnover_mills=(
                        0
                        if is_mandatory_risk_state
                        else max(0, -delta) * price_mills
                    ),
                    is_trade=delta != 0 and not is_mandatory_risk_state,
                )
            )
        if not indices:
            raise DiscreteSizingError(
                "CAPACITY_CONFLICT",
                "目标标的没有满足整手、订单门槛和风险上限的可行状态",
                instrument_code=code,
            )
        for index, next_index in zip(indices, indices[1:]):
            next_state_by_index[index] = next_index
        state_indices_by_code.append(indices)
    return _StateSpace(
        states=states,
        state_indices_by_code=state_indices_by_code,
        next_state_by_index=next_state_by_index,
    )


def _selected_indices(solution: np.ndarray, state_space: _StateSpace) -> list[int]:
    selected = []
    for indices in state_space.state_indices_by_code:
        index = max(indices, key=lambda candidate: solution[candidate])
        if solution[index] < 0.5:
            raise DiscreteSizingError(
                "SOLVER_NOT_OPTIMAL",
                "离散等权求解结果不是有效的整数目标态",
            )
        selected.append(index)
    return selected


def _selected_targets(solution: np.ndarray, state_space: _StateSpace) -> dict[str, int]:
    return {
        state_space.states[index].code: state_space.states[index].shares
        for index in _selected_indices(solution, state_space)
    }


def _fee_basis_mills(state: _TargetState, schedule: FeeSchedule) -> int:
    return (
        state.sell_turnover_mills
        if schedule.proportional_basis == "sell"
        else state.turnover_mills
    )


def _build_model(
    problem: _Problem,
    state_space: _StateSpace,
) -> tuple[_MilpModel, _VariableLayout]:
    state_count = len(state_space.states)
    layout = _VariableLayout(
        fee_cents=state_count,
        raw_fee_cents=state_count + 1,
        has_trade=state_count + 2,
        minimum_fee_regime=state_count + 3,
        target_value_mills=state_count + 4,
        max_deviation=state_count + 5,
        variable_count=state_count + 6,
    )
    maximum_turnover_mills = problem.mandatory_turnover_mills + sum(
        max(state_space.states[index].turnover_mills for index in indices)
        for indices in state_space.state_indices_by_code
    )
    maximum_orders = problem.mandatory_order_count + len(problem.target_codes)
    maximum_fee_cents = max(
        problem.fee_schedule.minimum_fee_cents,
        problem.fee_schedule.fixed_per_order_cents * maximum_orders
        + problem.fee_schedule.proportional_fee_cents(maximum_turnover_mills)
        + 1,
    )
    lower = np.zeros(layout.variable_count)
    upper = np.full(layout.variable_count, np.inf)
    upper[:state_count] = 1.0
    upper[layout.fee_cents] = maximum_fee_cents
    upper[layout.raw_fee_cents] = maximum_fee_cents
    upper[layout.has_trade] = 1.0
    upper[layout.minimum_fee_regime] = 1.0
    upper[layout.target_value_mills] = problem.total_value_mills
    integrality = np.zeros(layout.variable_count, dtype=int)
    integrality[:state_count] = 1
    integrality[layout.fee_cents] = 1
    integrality[layout.raw_fee_cents] = 1
    integrality[layout.has_trade] = 1
    integrality[layout.minimum_fee_regime] = 1
    model = _MilpModel(
        lower=lower,
        upper=upper,
        integrality=integrality,
        state_count=state_count,
    )

    for indices in state_space.state_indices_by_code:
        model.add_constraint(
            {index: 1.0 for index in indices},
            lower=1.0,
            upper=1.0,
        )
    target_value_row = {
        index: state.value_mills
        for index, state in enumerate(state_space.states)
    }
    target_value_row[layout.target_value_mills] = -1.0
    model.add_constraint(target_value_row, lower=0.0, upper=0.0)

    trade_row = {
        index: 1.0
        for index, state in enumerate(state_space.states)
        if state.is_trade
    }
    if problem.mandatory_order_count:
        model.add_constraint({layout.has_trade: 1.0}, lower=1.0, upper=1.0)
    elif trade_row:
        model.add_constraint(
            {**trade_row, layout.has_trade: -1.0},
            lower=0.0,
        )
        model.add_constraint(
            {
                **trade_row,
                layout.has_trade: -float(len(problem.target_codes)),
            },
            upper=0.0,
        )
    else:
        model.add_constraint({layout.has_trade: 1.0}, lower=0.0, upper=0.0)

    schedule = problem.fee_schedule
    rate = schedule.proportional_cents_per_mill
    fee_rounding_row = {
        index: 2.0
        * (
            rate.denominator * schedule.fixed_per_order_cents
            * int(state.is_trade)
            + rate.numerator * _fee_basis_mills(state, schedule)
        )
        for index, state in enumerate(state_space.states)
        if state.is_trade or _fee_basis_mills(state, schedule)
    }
    fee_rounding_row[layout.raw_fee_cents] = -2.0 * rate.denominator
    mandatory_basis = (
        problem.mandatory_sell_turnover_mills
        if schedule.proportional_basis == "sell"
        else problem.mandatory_turnover_mills
    )
    rounding_constant = 2 * (
        rate.denominator
        * schedule.fixed_per_order_cents
        * problem.mandatory_order_count
        + rate.numerator * mandatory_basis
    )
    model.add_constraint(
        fee_rounding_row,
        lower=float(-rate.denominator - rounding_constant),
        upper=float(rate.denominator - 1 - rounding_constant),
    )

    if schedule.minimum_fee_cents:
        big_m = float(maximum_fee_cents)
        model.add_constraint(
            {layout.fee_cents: 1.0, layout.raw_fee_cents: -1.0},
            lower=0.0,
        )
        model.add_constraint(
            {
                layout.fee_cents: 1.0,
                layout.has_trade: -float(schedule.minimum_fee_cents),
            },
            lower=0.0,
        )
        model.add_constraint(
            {
                layout.fee_cents: 1.0,
                layout.raw_fee_cents: -1.0,
                layout.minimum_fee_regime: -big_m,
            },
            upper=0.0,
        )
        model.add_constraint(
            {
                layout.fee_cents: 1.0,
                layout.has_trade: -float(schedule.minimum_fee_cents),
                layout.minimum_fee_regime: big_m,
            },
            upper=big_m,
        )
    else:
        model.add_constraint(
            {layout.fee_cents: 1.0, layout.raw_fee_cents: -1.0},
            lower=0.0,
            upper=0.0,
        )
        model.add_constraint(
            {layout.minimum_fee_regime: 1.0},
            lower=0.0,
            upper=0.0,
        )

    model.add_constraint(
        {
            layout.target_value_mills: 1.0,
            layout.fee_cents: float(CENT_MILLS),
        },
        upper=float(problem.total_value_mills),
    )
    target_count = len(problem.target_codes)
    for indices in state_space.state_indices_by_code:
        model.add_constraint(
            {
                layout.max_deviation: 1.0,
                **{
                    index: -float(
                        abs(
                            target_count * state_space.states[index].value_mills
                            - problem.total_value_mills
                        )
                    )
                    for index in indices
                },
            },
            lower=0.0,
        )
    model.add_constraint(
        {
            layout.max_deviation: 1.0,
            layout.target_value_mills: float(target_count),
            layout.fee_cents: float(target_count * CENT_MILLS),
        },
        lower=float(target_count * problem.total_value_mills),
    )
    return model, layout


def _exact_values(
    solution: np.ndarray,
    *,
    problem: _Problem,
    state_space: _StateSpace,
    layout: _VariableLayout,
) -> tuple[int, int, int, int, int, int]:
    selected = _selected_indices(solution, state_space)
    target_count = len(problem.target_codes)
    fee_cents = int(round(solution[layout.fee_cents]))
    target_value_mills = sum(state_space.states[index].value_mills for index in selected)
    cash_mills = problem.total_value_mills - target_value_mills - fee_cents * CENT_MILLS
    deviations = [
        abs(
            target_count * state_space.states[index].value_mills
            - problem.total_value_mills
        )
        for index in selected
    ]
    tracking_error_numerator = sum(deviation**2 for deviation in deviations)
    cash_numerator = target_count * cash_mills
    maximum_deviation = max(*deviations, cash_numerator)
    total_deviation = sum(deviations) + cash_numerator
    order_count = problem.mandatory_order_count + sum(
        state_space.states[index].is_trade
        for index in selected
    )
    return (
        tracking_error_numerator,
        maximum_deviation,
        total_deviation,
        cash_mills,
        fee_cents,
        order_count,
    )


def _solve_lexicographically(
    problem: _Problem,
    state_space: _StateSpace,
    model: _MilpModel,
    layout: _VariableLayout,
):
    target_count = len(problem.target_codes)

    squared = {
        index: (
            target_count * state.value_mills - problem.total_value_mills
        )
        ** 2
        for index, state in enumerate(state_space.states)
    }
    minimum_constant = 0
    reduced = {}
    divisor = 0
    for indices in state_space.state_indices_by_code:
        minimum = min(squared[index] for index in indices)
        minimum_constant += minimum
        for index in indices:
            cost = squared[index] - minimum
            reduced[index] = cost
            divisor = gcd(divisor, cost)
    divisor = max(1, divisor)
    tracking_row = {
        index: cost // divisor
        for index, cost in reduced.items()
        if cost
    }
    maximum_scaled_error = sum(
        max((tracking_row.get(index, 0) for index in indices), default=0)
        for indices in state_space.state_indices_by_code
    )
    if maximum_scaled_error > MAX_EXACT_FLOAT_INTEGER:
        raise DiscreteSizingError(
            "SOLVER_NOT_OPTIMAL",
            "等权误差整数缩放超出求解器精确表示范围",
        )
    objective = np.zeros(layout.variable_count)
    for index, value in tracking_row.items():
        objective[index] = value
    result = model.solve(objective)
    tracking_error = _exact_values(
        result.x,
        problem=problem,
        state_space=state_space,
        layout=layout,
    )[0]
    reduced_optimum, remainder = divmod(
        tracking_error - minimum_constant,
        divisor,
    )
    if remainder:
        raise DiscreteSizingError(
            "SOLVER_NOT_OPTIMAL",
            "等权误差未落在精确整数目标网格",
        )
    model.add_constraint(
        tracking_row,
        lower=float(reduced_optimum),
        upper=float(reduced_optimum),
    )

    objective = np.zeros(layout.variable_count)
    objective[layout.max_deviation] = 1.0
    result = model.solve(objective)
    maximum_deviation = _exact_values(
        result.x,
        problem=problem,
        state_space=state_space,
        layout=layout,
    )[1]
    model.add_constraint(
        {layout.max_deviation: 1.0},
        upper=float(maximum_deviation),
    )

    total_row = {
        index: float(
            abs(target_count * state.value_mills - problem.total_value_mills)
        )
        for index, state in enumerate(state_space.states)
    }
    total_row[layout.target_value_mills] = -float(target_count)
    total_row[layout.fee_cents] = -float(target_count * CENT_MILLS)
    objective = np.zeros(layout.variable_count)
    for index, value in total_row.items():
        objective[index] = value
    result = model.solve(objective)
    total_deviation = _exact_values(
        result.x,
        problem=problem,
        state_space=state_space,
        layout=layout,
    )[2]
    total_row_optimum = total_deviation - target_count * problem.total_value_mills
    model.add_constraint(
        total_row,
        lower=float(total_row_optimum),
        upper=float(total_row_optimum),
    )

    cash_row = {
        layout.target_value_mills: -1.0,
        layout.fee_cents: -float(CENT_MILLS),
    }
    objective = np.zeros(layout.variable_count)
    for index, value in cash_row.items():
        objective[index] = value
    result = model.solve(objective)
    cash_mills = _exact_values(
        result.x,
        problem=problem,
        state_space=state_space,
        layout=layout,
    )[3]
    cash_row_optimum = cash_mills - problem.total_value_mills
    model.add_constraint(
        cash_row,
        lower=float(cash_row_optimum),
        upper=float(cash_row_optimum),
    )

    objective = np.zeros(layout.variable_count)
    objective[layout.fee_cents] = 1.0
    result = model.solve(objective)
    fee_cents = _exact_values(
        result.x,
        problem=problem,
        state_space=state_space,
        layout=layout,
    )[4]
    model.add_constraint(
        {layout.fee_cents: 1.0},
        lower=float(fee_cents),
        upper=float(fee_cents),
    )

    order_row = {
        index: 1.0
        for index, state in enumerate(state_space.states)
        if state.is_trade
    }
    objective = np.zeros(layout.variable_count)
    for index in order_row:
        objective[index] = 1.0
    result = model.solve(objective)
    order_count = _exact_values(
        result.x,
        problem=problem,
        state_space=state_space,
        layout=layout,
    )[5]
    variable_order_count = order_count - problem.mandatory_order_count
    model.add_constraint(
        order_row,
        lower=float(variable_order_count),
        upper=float(variable_order_count),
    )

    for indices in state_space.state_indices_by_code[:-1]:
        shares_row = {
            index: float(state_space.states[index].shares)
            for index in indices
        }
        objective = np.zeros(layout.variable_count)
        for index, value in shares_row.items():
            objective[index] = -value
        result = model.solve(objective)
        selected = _selected_targets(result.x, state_space)
        code = state_space.states[indices[0]].code
        shares = selected[code]
        model.add_constraint(
            shares_row,
            lower=float(shares),
            upper=float(shares),
        )
    return result


def _cash_residual_reason(
    targets: dict[str, int],
    *,
    problem: _Problem,
    state_space: _StateSpace,
    cash_mills: int,
) -> str:
    state_lookup = {
        (state.code, state.shares): index
        for index, state in enumerate(state_space.states)
    }
    current_error = sum(
        (
            len(problem.target_codes)
            * targets[code]
            * problem.price_mills[code]
            - problem.total_value_mills
        )
        ** 2
        for code in problem.target_codes
    )
    next_steps = []
    for code in problem.target_codes:
        index = state_lookup[(code, targets[code])]
        next_index = state_space.next_state_by_index.get(index)
        if next_index is None:
            continue
        trial = targets.copy()
        trial[code] = state_space.states[next_index].shares
        fee = problem.fee_schedule.estimate(
            trial,
            problem.holdings,
            problem.target_codes,
            problem.prices,
        )
        target_value = sum(
            trial[target_code] * problem.price_mills[target_code]
            for target_code in problem.target_codes
        )
        trial_cash = problem.total_value_mills - target_value - _to_mills(fee)
        trial_error = sum(
            (
                len(problem.target_codes)
                * trial[target_code]
                * problem.price_mills[target_code]
                - problem.total_value_mills
            )
            ** 2
            for target_code in problem.target_codes
        )
        value_increase = (
            state_space.states[next_index].value_mills
            - state_space.states[index].value_mills
        )
        next_steps.append((trial_cash, trial_error, value_increase))
    if not next_steps:
        return "at_cap_or_no_feasible_buy"
    if any(trial_cash >= 0 and trial_error >= current_error for trial_cash, trial_error, _ in next_steps):
        return "equal_weight_priority"
    if any(value <= cash_mills for _, _, value in next_steps):
        return "fees"
    return "insufficient_for_next_feasible_state"


def solve_equal_weight_targets(
    *,
    target_codes: list[str],
    holdings: dict[str, int],
    prices: dict[str, float],
    cash: float,
    lot: int,
    max_single_weight: float,
    ordinary_order_threshold: float = 0.0,
    allow_target_sells: bool,
    fee_schedule: FeeSchedule,
) -> EqualWeightTargetResult:
    """Prove the lexicographically best executable equal-weight target state."""
    problem = _prepare_problem(
        target_codes=target_codes,
        holdings=holdings,
        prices=prices,
        cash=cash,
        lot=lot,
        max_single_weight=max_single_weight,
        ordinary_order_threshold=ordinary_order_threshold,
        allow_target_sells=allow_target_sells,
        fee_schedule=fee_schedule,
    )
    state_space = _build_state_space(problem)
    minimum_targets = {
        problem.target_codes[index]: state_space.states[indices[0]].shares
        for index, indices in enumerate(state_space.state_indices_by_code)
    }
    minimum_fee = problem.fee_schedule.estimate(
        minimum_targets,
        problem.holdings,
        problem.target_codes,
        problem.prices,
    )
    minimum_required_mills = sum(
        minimum_targets[code] * problem.price_mills[code]
        for code in problem.target_codes
    ) + _to_mills(minimum_fee)
    if minimum_required_mills > problem.total_value_mills:
        raise DiscreteSizingError(
            "CAPACITY_CONFLICT",
            "可执行预算无法支持目标组合的最低可行状态及交易费用",
            minimum_required=round(minimum_required_mills / MONEY_SCALE, 2),
            total_value=round(problem.total_value_mills / MONEY_SCALE, 2),
        )
    model, layout = _build_model(problem, state_space)
    result = _solve_lexicographically(problem, state_space, model, layout)
    targets = _selected_targets(result.x, state_space)
    (
        tracking_error_numerator,
        maximum_deviation,
        total_deviation,
        cash_mills,
        fee_cents,
        order_count,
    ) = _exact_values(
        result.x,
        problem=problem,
        state_space=state_space,
        layout=layout,
    )
    official_fee = problem.fee_schedule.estimate(
        targets,
        problem.holdings,
        problem.target_codes,
        problem.prices,
    )
    official_fee_mills = _to_mills(official_fee)
    if official_fee_mills != fee_cents * CENT_MILLS or cash_mills < 0:
        raise DiscreteSizingError(
            "SOLVER_NOT_OPTIMAL",
            "求解结果未通过正式费用与现金方程复核",
            cash_mills=cash_mills,
            model_fee_mills=fee_cents * CENT_MILLS,
            official_fee_mills=official_fee_mills,
        )

    cash_left = cash_mills / MONEY_SCALE
    total_value = problem.total_value_mills / MONEY_SCALE
    target_count = len(problem.target_codes)
    metrics = score_targets(
        targets,
        target_codes=problem.target_codes,
        prices=problem.prices,
        cash_left=cash_left,
        total_value=total_value,
    )
    mandatory_sale_mills = problem.mandatory_sell_turnover_mills
    return EqualWeightTargetResult(
        targets=targets,
        summary={
            "total_value": round(total_value, 2),
            "holdings_value": round(problem.holdings_value_mills / MONEY_SCALE, 2),
            "cash_in": round(problem.cash_mills / MONEY_SCALE, 2),
            "per_target": round(total_value / target_count, 2),
            "cash_left": round(cash_left, 2),
            "cash_left_before_fees": round(cash_left + official_fee, 2),
            "estimated_fees": official_fee,
            "n_target": target_count,
            "target_slot_count": target_count,
            "risk_cap_value": round(problem.cap_value_mills / MONEY_SCALE, 2),
            "risk_cap_basis": "pre_fee_strategy_budget",
            "max_overall_deviation": metrics[0],
            "target_tracking_error": float(
                Fraction(
                    tracking_error_numerator,
                    target_count**2 * problem.total_value_mills**2,
                )
            ),
            "total_overall_deviation": metrics[1],
            "uninvestable_cash": round(cash_left, 2),
            "mandatory_sale_proceeds": round(mandatory_sale_mills / MONEY_SCALE, 2),
            "allocation_method": "equal_weight_lexicographic_milp",
            "solver": "scipy.optimize.milp/HiGHS",
            "solver_decomposition": "shared_fee_schedule",
            "solver_status": "OPTIMAL",
            "solver_mip_gap": max(model.mip_gaps, default=0.0),
            "solver_node_count": model.node_count,
            "solver_solve_count": model.solve_count,
            "solver_presolve_retry_count": model.presolve_retry_count,
            "fee_schedule": problem.fee_schedule.name,
            "allow_target_sells": allow_target_sells,
            "solver_objectives": {
                "target_tracking_error": float(
                    Fraction(
                        tracking_error_numerator,
                        target_count**2 * problem.total_value_mills**2,
                    )
                ),
                "max_overall_deviation": float(
                    Fraction(maximum_deviation, target_count * problem.total_value_mills)
                ),
                "total_overall_deviation": float(
                    Fraction(total_deviation, target_count * problem.total_value_mills)
                ),
                "cash_left": round(cash_left, 3),
                "estimated_fees": official_fee,
                "order_count": order_count,
            },
            "cash_residual_reason": _cash_residual_reason(
                targets,
                problem=problem,
                state_space=state_space,
                cash_mills=cash_mills,
            ),
        },
    )
