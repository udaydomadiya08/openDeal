"""Deterministic, reproducible Monte Carlo valuation simulation engine.

Features:
- Fixed pseudo-random seed generator (standard random.Random) ensuring bit-for-bit repeatability.
- Transparent parametric distribution sampling (Normal, Triangular, Uniform, Lognormal).
- Robust boundary enforcement: Discards mathematically invalid iterations (e.g. g >= r).
- Comprehensive statistical summary: Mean, Median, StdDev, Min, Max, and Percentiles (P5 to P95).
- Downside probability / Value-at-Risk evaluation against user-defined hurdle thresholds.
- Zero external heavy dependencies; pure Python standard library math & statistics.
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Dict, List, Optional

from opendeal.valuation.engine.contracts import (
    DCFInput,
    DistributionType,
    MonteCarloDCFInput,
    MonteCarloDCFOutput,
    ParameterDistribution,
    ValuationCalculationStatus,
)
from opendeal.valuation.engine.primitives import calculate_dcf


def _sample_parameter(dist: ParameterDistribution, rng: random.Random) -> float:
    """Sample a single value from a specified parametric distribution using a dedicated RNG instance."""
    dtype = dist.distribution_type

    if dtype == DistributionType.NORMAL:
        mean = dist.mean if dist.mean is not None else 0.0
        std = dist.std_dev if dist.std_dev is not None else 1.0
        val = rng.gauss(mean, std)
        if dist.min_val is not None:
            val = max(dist.min_val, val)
        if dist.max_val is not None:
            val = min(dist.max_val, val)
        return val

    elif dtype == DistributionType.TRIANGULAR:
        low = dist.min_val if dist.min_val is not None else 0.0
        high = dist.max_val if dist.max_val is not None else 1.0
        mode = dist.mode_val if dist.mode_val is not None else (low + high) / 2.0
        return rng.triangular(low, high, mode)

    elif dtype == DistributionType.UNIFORM:
        low = dist.min_val if dist.min_val is not None else 0.0
        high = dist.max_val if dist.max_val is not None else 1.0
        return rng.uniform(low, high)

    elif dtype == DistributionType.LOGNORMAL:
        mean = dist.mean if dist.mean is not None else 0.0
        std = dist.std_dev if dist.std_dev is not None else 1.0
        val = rng.lognormvariate(mean, std)
        if dist.min_val is not None:
            val = max(dist.min_val, val)
        if dist.max_val is not None:
            val = min(dist.max_val, val)
        return val

    raise ValueError(f"Unsupported distribution type: {dtype}")


def calculate_monte_carlo_dcf(inp: MonteCarloDCFInput) -> MonteCarloDCFOutput:
    """Execute reproducible Monte Carlo simulation over a DCF valuation."""
    rng = random.Random(inp.random_seed)
    warnings: List[str] = [
        "Monte Carlo outputs represent stochastic simulated scenarios under chosen distributions, "
        "not observed facts or guaranteed financial forecasts."
    ]

    successful_results: List[float] = []
    failed_count = 0

    # Index parameter distributions by name
    dist_map: Dict[str, ParameterDistribution] = {d.parameter_name.lower(): d for d in inp.parameter_distributions}

    for _ in range(inp.iteration_count):
        # 1. Sample discount rate (WACC)
        if "discount_rate" in dist_map:
            sim_r = _sample_parameter(dist_map["discount_rate"], rng)
        else:
            sim_r = inp.base_dcf_input.discount_rate

        # 2. Sample terminal growth rate
        if "terminal_growth_rate" in dist_map:
            sim_g = _sample_parameter(dist_map["terminal_growth_rate"], rng)
        else:
            sim_g = inp.base_dcf_input.terminal_growth_rate

        # Boundary validity check: r must strictly exceed g
        if sim_r <= sim_g or sim_r <= 0.0:
            failed_count += 1
            continue

        # 3. Sample cash flow adjustments if specified
        if "fcf_multiplier" in dist_map:
            multiplier = _sample_parameter(dist_map["fcf_multiplier"], rng)
            sim_cash_flows = [cf * multiplier for cf in inp.base_dcf_input.forecast_cash_flows]
        else:
            sim_cash_flows = list(inp.base_dcf_input.forecast_cash_flows)

        # 4. Execute deterministic DCF iteration
        trial_dcf_input = inp.base_dcf_input.model_copy(
            update={
                "discount_rate": sim_r,
                "terminal_growth_rate": sim_g,
                "forecast_cash_flows": sim_cash_flows,
            }
        )
        trial_res = calculate_dcf(trial_dcf_input)

        if trial_res.status == ValuationCalculationStatus.COMPLETED and trial_res.equity_value is not None:
            successful_results.append(trial_res.equity_value)
        else:
            failed_count += 1

    if not successful_results:
        return MonteCarloDCFOutput(
            status=ValuationCalculationStatus.MATHEMATICALLY_INVALID,
            iteration_count=inp.iteration_count,
            successful_iterations=0,
            failed_iterations=failed_count,
            mean_equity_value=0.0,
            median_equity_value=0.0,
            std_dev_equity_value=0.0,
            min_equity_value=0.0,
            max_equity_value=0.0,
            percentiles={},
            random_seed=inp.random_seed,
            warnings=warnings + ["All simulation iterations failed mathematical validity constraints."],
        )

    # 5. Statistical Aggregations
    successful_results.sort()
    n_success = len(successful_results)

    mean_val = statistics.mean(successful_results)
    median_val = statistics.median(successful_results)
    std_dev_val = statistics.stdev(successful_results) if n_success > 1 else 0.0
    min_val = successful_results[0]
    max_val = successful_results[-1]

    # Percentiles calculation
    percentiles_dict: Dict[str, float] = {}
    p_targets = [("P5", 0.05), ("P10", 0.10), ("P25", 0.25), ("P50", 0.50), ("P75", 0.75), ("P90", 0.90), ("P95", 0.95)]
    for p_name, p_frac in p_targets:
        idx = int(p_frac * (n_success - 1))
        percentiles_dict[p_name] = round(successful_results[idx], 2)

    prob_below = None
    if inp.target_equity_threshold is not None:
        below_count = sum(1 for val in successful_results if val < inp.target_equity_threshold)
        prob_below = round(below_count / n_success, 4)
        if failed_count > 0:
            warnings.append(
                f"Downside risk probability ({prob_below:.2%}) evaluated over {n_success} valid iterations "
                f"({failed_count} unviable iterations where g >= r were excluded from the probability denominator)."
            )

    return MonteCarloDCFOutput(
        status=ValuationCalculationStatus.COMPLETED,
        iteration_count=inp.iteration_count,
        successful_iterations=n_success,
        failed_iterations=failed_count,
        mean_equity_value=round(mean_val, 2),
        median_equity_value=round(median_val, 2),
        std_dev_equity_value=round(std_dev_val, 2),
        min_equity_value=round(min_val, 2),
        max_equity_value=round(max_val, 2),
        percentiles=percentiles_dict,
        probability_below_threshold=prob_below,
        random_seed=inp.random_seed,
        warnings=warnings,
    )
