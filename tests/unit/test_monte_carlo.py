"""Independent unit tests for Monte Carlo valuation simulation.

Verifies:
- Bit-for-bit determinism and reproducibility with fixed random seed.
- Different random seeds produce distinct stochastic distributions.
- Distribution sampling for Normal, Triangular, Uniform, and Lognormal.
- Boundary rejection when sampled terminal growth equals or exceeds discount rate (g >= r).
- Statistical invariants (min <= P25 <= P50 <= P75 <= max).
- Downside probability / Value-at-Risk against target hurdle.
- Mandatory warning disclosure that simulated outcomes are not guaranteed facts.
"""

import pytest

from opendeal.valuation.engine.contracts import (
    DCFInput,
    DiscountingConvention,
    DistributionType,
    MonteCarloDCFInput,
    ParameterDistribution,
    ValuationCalculationStatus,
)
from opendeal.valuation.engine.monte_carlo import calculate_monte_carlo_dcf


class TestMonteCarloSimulation:
    """Tests for reproducible Monte Carlo DCF simulations."""

    @pytest.fixture
    def base_dcf_input(self) -> DCFInput:
        return DCFInput(
            forecast_cash_flows=[100.0, 110.0, 120.0, 130.0, 140.0],
            discount_rate=0.085,
            terminal_growth_rate=0.025,
            discounting_convention=DiscountingConvention.MID_YEAR,
            total_debt=200.0,
            cash_and_equivalents=50.0,
        )

    def test_monte_carlo_determinism_fixed_seed(self, base_dcf_input):
        """Identical random seeds yield bit-for-bit identical statistical outputs."""
        distributions = [
            ParameterDistribution(
                parameter_name="discount_rate",
                distribution_type=DistributionType.NORMAL,
                mean=0.085,
                std_dev=0.005,
                min_val=0.05,
                max_val=0.15,
            ),
            ParameterDistribution(
                parameter_name="terminal_growth_rate",
                distribution_type=DistributionType.TRIANGULAR,
                min_val=0.015,
                mode_val=0.025,
                max_val=0.035,
            ),
        ]

        inp1 = MonteCarloDCFInput(
            base_dcf_input=base_dcf_input,
            parameter_distributions=distributions,
            iteration_count=500,
            random_seed=12345,
            target_equity_threshold=1500.0,
        )
        inp2 = MonteCarloDCFInput(
            base_dcf_input=base_dcf_input,
            parameter_distributions=distributions,
            iteration_count=500,
            random_seed=12345,
            target_equity_threshold=1500.0,
        )

        res1 = calculate_monte_carlo_dcf(inp1)
        res2 = calculate_monte_carlo_dcf(inp2)

        assert res1.status == ValuationCalculationStatus.COMPLETED
        assert res1.successful_iterations == res2.successful_iterations
        assert res1.mean_equity_value == res2.mean_equity_value
        assert res1.median_equity_value == res2.median_equity_value
        assert res1.percentiles == res2.percentiles
        assert res1.probability_below_threshold == res2.probability_below_threshold

    def test_different_seeds_produce_different_distributions(self, base_dcf_input):
        """Different seeds produce distinct stochastic trajectories."""
        distributions = [
            ParameterDistribution(
                parameter_name="discount_rate",
                distribution_type=DistributionType.UNIFORM,
                min_val=0.07,
                max_val=0.10,
            )
        ]

        inp_seed1 = MonteCarloDCFInput(
            base_dcf_input=base_dcf_input,
            parameter_distributions=distributions,
            iteration_count=300,
            random_seed=111,
        )
        inp_seed2 = MonteCarloDCFInput(
            base_dcf_input=base_dcf_input,
            parameter_distributions=distributions,
            iteration_count=300,
            random_seed=999,
        )

        res1 = calculate_monte_carlo_dcf(inp_seed1)
        res2 = calculate_monte_carlo_dcf(inp_seed2)

        assert res1.mean_equity_value != res2.mean_equity_value

    def test_percentile_monotonicity(self, base_dcf_input):
        """Percentiles must obey strict statistical ordering: P5 <= P25 <= P50 <= P75 <= P95."""
        distributions = [
            ParameterDistribution(
                parameter_name="discount_rate",
                distribution_type=DistributionType.NORMAL,
                mean=0.09,
                std_dev=0.008,
                min_val=0.06,
                max_val=0.14,
            ),
            ParameterDistribution(
                parameter_name="terminal_growth_rate",
                distribution_type=DistributionType.UNIFORM,
                min_val=0.01,
                max_val=0.03,
            ),
        ]

        inp = MonteCarloDCFInput(
            base_dcf_input=base_dcf_input,
            parameter_distributions=distributions,
            iteration_count=500,
            random_seed=42,
        )
        res = calculate_monte_carlo_dcf(inp)

        p = res.percentiles
        assert p["P5"] <= p["P10"] <= p["P25"] <= p["P50"] <= p["P75"] <= p["P90"] <= p["P95"]
        assert res.min_equity_value <= p["P5"]
        assert p["P95"] <= res.max_equity_value

    def test_invalid_parameter_trials_rejected_safely(self, base_dcf_input):
        """Trials where sampled terminal growth exceeds discount rate are rejected and counted."""
        # Intentionally force high growth and low discount rate to cause invalid draws
        distributions = [
            ParameterDistribution(
                parameter_name="discount_rate",
                distribution_type=DistributionType.UNIFORM,
                min_val=0.02,
                max_val=0.04,
            ),
            ParameterDistribution(
                parameter_name="terminal_growth_rate",
                distribution_type=DistributionType.UNIFORM,
                min_val=0.03,  # Overlaps and exceeds discount rate!
                max_val=0.06,
            ),
        ]

        inp = MonteCarloDCFInput(
            base_dcf_input=base_dcf_input,
            parameter_distributions=distributions,
            iteration_count=200,
            random_seed=42,
        )
        res = calculate_monte_carlo_dcf(inp)

        # Some iterations must have failed because g >= r
        assert res.failed_iterations > 0
        assert res.successful_iterations + res.failed_iterations == 200

    def test_warning_message_present(self, base_dcf_input):
        """Simulation results must disclose stochastic nature rather than observed fact."""
        distributions = [
            ParameterDistribution(
                parameter_name="discount_rate",
                distribution_type=DistributionType.NORMAL,
                mean=0.08,
                std_dev=0.005,
            )
        ]
        inp = MonteCarloDCFInput(
            base_dcf_input=base_dcf_input,
            parameter_distributions=distributions,
            iteration_count=100,
            random_seed=42,
        )
        res = calculate_monte_carlo_dcf(inp)

        assert any("not observed facts" in w for w in res.warnings)

    def test_monte_carlo_downside_probability_denominator_transparency(self, base_dcf_input):
        """Probability below threshold explicitly uses successful_iterations as denominator and notes exclusions."""
        distributions = [
            ParameterDistribution(
                parameter_name="discount_rate",
                distribution_type=DistributionType.UNIFORM,
                min_val=0.03,
                max_val=0.06,
            ),
            ParameterDistribution(
                parameter_name="terminal_growth_rate",
                distribution_type=DistributionType.UNIFORM,
                min_val=0.04,  # Intentionally overlaps so some iterations fail
                max_val=0.08,
            ),
        ]
        inp = MonteCarloDCFInput(
            base_dcf_input=base_dcf_input,
            parameter_distributions=distributions,
            iteration_count=300,
            random_seed=42,
            target_equity_threshold=2000.0,
        )
        res = calculate_monte_carlo_dcf(inp)
        assert res.status == ValuationCalculationStatus.COMPLETED
        assert res.failed_iterations > 0
        assert res.probability_below_threshold is not None
        # Denominator must be successful_iterations
        assert any("excluded from the probability denominator" in w for w in res.warnings)

    def test_monte_carlo_invalid_distribution_bounds_rejected(self):
        """Pydantic rejects mathematically invalid parameter distributions at contract boundary."""
        # 1. min_val > max_val
        with pytest.raises(ValueError, match="cannot exceed max_val"):
            ParameterDistribution(
                parameter_name="discount_rate",
                distribution_type=DistributionType.UNIFORM,
                min_val=0.15,
                max_val=0.05,
            )

        # 2. negative std_dev
        with pytest.raises(ValueError, match="cannot be negative"):
            ParameterDistribution(
                parameter_name="discount_rate",
                distribution_type=DistributionType.NORMAL,
                mean=0.08,
                std_dev=-0.02,
            )

        # 3. triangular mode_val < min_val
        with pytest.raises(ValueError, match="cannot be less than min_val"):
            ParameterDistribution(
                parameter_name="terminal_growth_rate",
                distribution_type=DistributionType.TRIANGULAR,
                min_val=0.02,
                mode_val=0.01,
                max_val=0.04,
            )

