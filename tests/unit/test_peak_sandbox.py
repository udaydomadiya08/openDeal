"""Unit tests for PEAK sandbox runner, mapping registry, and valuation acceptance rules."""

from pathlib import Path
import tempfile
import pytest

from opendeal.core.types import Currency, FiscalPeriodType, Sign, ValidationStatus
from opendeal.facts.models import FactRecord
from opendeal.valuation.acceptance import ValuationInputAcceptanceEngine
from opendeal.valuation.contracts import (
    CalculationMode,
    CalculationStatus,
    ValuationCellDefinition,
    ValuationInputPayload,
)
from opendeal.valuation.exceptions import (
    AmbiguousMappingError,
    FormulaOverwriteProhibitedError,
    UnauthorizedCellWriteError,
    ValuationInputValidationError,
)
from opendeal.valuation.mapping import PEAKInputRegistry
from opendeal.valuation.peak_sandbox import PEAKSandboxRunner, compute_file_sha256

MASTER_WORKBOOK = Path("/Users/uday/Documents/uday_projects/tester1/PEAK_Ultimate_Institutional_Valuation_System_FIXED.xlsx")
EXPECTED_MASTER_SHA256 = "ae2343527fb355ae7454f3fd4354289ff51ced8f5a2a8e62aac0ec0675bcbfa5"


class TestPEAKInputRegistry:
    """Test registry and mapping whitelist behavior."""

    def test_default_registry_loads_verified_mappings(self):
        registry = PEAKInputRegistry.default()
        assert registry.total_authorized_cells >= 134
        assert registry.is_authorized("Assumptions", "O12")  # Revenue Current
        assert registry.is_authorized("Assumptions", "G34")  # Equity Weight
        assert not registry.is_authorized("Assumptions", "G35")  # Formula: =1-G34
        assert not registry.is_authorized("Assumptions", "M25")  # Formula: Interest

    def test_reject_registering_formula_cell(self):
        with pytest.raises(FormulaOverwriteProhibitedError) as exc_info:
            PEAKInputRegistry(
                [
                    ValuationCellDefinition(
                        canonical_variable="debt_weight",
                        sheet="Assumptions",
                        cell="G35",
                        is_formula=True,
                    )
                ]
            )
        assert "contains a formula" in str(exc_info.value)

    def test_reject_ambiguous_duplicate_coordinate(self):
        with pytest.raises(AmbiguousMappingError) as exc_info:
            PEAKInputRegistry(
                [
                    ValuationCellDefinition(
                        canonical_variable="revenue_current",
                        sheet="Assumptions",
                        cell="O12",
                    ),
                    ValuationCellDefinition(
                        canonical_variable="gross_sales",
                        sheet="Assumptions",
                        cell="O12",
                    ),
                ]
            )
        assert "Ambiguous mapping collision" in str(exc_info.value)

    @pytest.mark.skipif(not MASTER_WORKBOOK.exists(), reason="Requires local PEAK master workbook on developer machine")
    def test_verify_registry_against_master_workbook(self):
        """Confirm that none of the default registered cells collide with formulas in PEAK."""
        registry = PEAKInputRegistry.default()
        valid, errors = registry.verify_against_workbook(MASTER_WORKBOOK)
        assert valid is True
        assert len(errors) == 0


class TestValuationInputAcceptance:
    """Test valuation input acceptance rules."""

    def test_entity_mismatch_rejected(self):
        registry = PEAKInputRegistry.default()
        fact = FactRecord(
            entity_id="ent_other",
            canonical_variable="revenue_current",
            reported_value=1000000.0,
            normalized_value=1000000.0,
            fiscal_year=2023,
            validation_status=ValidationStatus.VALID,
            evidence_ids=["evi_123"],
        )
        with pytest.raises(ValuationInputValidationError) as exc:
            ValuationInputAcceptanceEngine.validate_fact_for_valuation(
                fact=fact, target_entity_id="ent_target", registry=registry
            )
        assert "Entity mismatch" in str(exc.value)

    def test_invalid_validation_status_rejected(self):
        registry = PEAKInputRegistry.default()
        fact = FactRecord(
            entity_id="ent_target",
            canonical_variable="revenue_current",
            reported_value=1000000.0,
            normalized_value=1000000.0,
            fiscal_year=2023,
            validation_status=ValidationStatus.INVALID,
            evidence_ids=["evi_123"],
        )
        with pytest.raises(ValuationInputValidationError) as exc:
            ValuationInputAcceptanceEngine.validate_fact_for_valuation(
                fact=fact, target_entity_id="ent_target", registry=registry
            )
        assert "validation status" in str(exc.value)

    def test_missing_data_never_converted_to_zero(self):
        """Missing values must remain missing and cannot enter PEAK as 0."""
        registry = PEAKInputRegistry.default()
        fact = FactRecord(
            entity_id="ent_target",
            canonical_variable="revenue_current",
            reported_value=None,
            normalized_value=None,
            fiscal_year=2023,
            validation_status=ValidationStatus.VALID,
            evidence_ids=["evi_123"],
        )
        with pytest.raises(ValuationInputValidationError) as exc:
            ValuationInputAcceptanceEngine.validate_fact_for_valuation(
                fact=fact, target_entity_id="ent_target", registry=registry
            )
        assert "Missing data cannot be silently substituted" in str(exc.value)

    def test_explicit_reported_zero_is_accepted(self):
        """A reported zero (e.g. debt = 0) is valid."""
        registry = PEAKInputRegistry.default()
        fact = FactRecord(
            entity_id="ent_target",
            canonical_variable="financial_debt_current",
            reported_value=0.0,
            normalized_value=0.0,
            sign=Sign.ZERO,
            fiscal_year=2023,
            validation_status=ValidationStatus.VALID,
            evidence_ids=["evi_123"],
        )
        payload = ValuationInputAcceptanceEngine.validate_fact_for_valuation(
            fact=fact, target_entity_id="ent_target", registry=registry
        )
        assert payload.value == 0.0
        assert payload.target_sheet == "Assumptions"
        assert payload.target_cell == "O49"
        assert payload.is_reported_zero is True

    def test_negative_debt_domain_constraint_rejected(self):
        registry = PEAKInputRegistry.default()
        fact = FactRecord(
            entity_id="ent_target",
            canonical_variable="financial_debt_current",
            reported_value=-1000.0,
            normalized_value=-1000.0,
            fiscal_year=2023,
            validation_status=ValidationStatus.VALID,
            evidence_ids=["evi_123"],
        )
        with pytest.raises(ValuationInputValidationError) as exc:
            ValuationInputAcceptanceEngine.validate_fact_for_valuation(
                fact=fact, target_entity_id="ent_target", registry=registry
            )
        assert "cannot be negative" in str(exc.value)

    def test_unauthorized_canonical_variable_rejected(self):
        registry = PEAKInputRegistry.default()
        fact = FactRecord(
            entity_id="ent_target",
            canonical_variable="random_unmapped_metric",
            reported_value=123.0,
            normalized_value=123.0,
            fiscal_year=2023,
            validation_status=ValidationStatus.VALID,
            evidence_ids=["evi_123"],
        )
        with pytest.raises(UnauthorizedCellWriteError) as exc:
            ValuationInputAcceptanceEngine.validate_fact_for_valuation(
                fact=fact, target_entity_id="ent_target", registry=registry
            )
        assert "No authorized PEAK mapping exists" in str(exc.value)


@pytest.mark.skipif(not MASTER_WORKBOOK.exists(), reason="Requires local PEAK master workbook on developer machine")
class TestPEAKSandboxRunner:
    """Test PEAK sandbox runner execution and integrity validation."""

    def test_golden_master_initial_hash_verified(self):
        assert compute_file_sha256(MASTER_WORKBOOK) == EXPECTED_MASTER_SHA256

    def test_unauthorized_cell_write_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = PEAKSandboxRunner(master_workbook_path=MASTER_WORKBOOK, runs_root_dir=tmpdir)
            unauthorized_input = ValuationInputPayload(
                canonical_variable="unauthorized_var",
                target_sheet="Assumptions",
                target_cell="Z99",
                value=12345,
                entity_id="ent_test",
            )
            with pytest.raises(UnauthorizedCellWriteError) as exc:
                runner.execute_run(inputs=[unauthorized_input], entity_id="ent_test")
            assert "not authorized as a PEAK input cell" in str(exc.value)

            # Golden master must remain unchanged
            assert compute_file_sha256(MASTER_WORKBOOK) == EXPECTED_MASTER_SHA256

    def test_formula_overwrite_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = PEAKSandboxRunner(master_workbook_path=MASTER_WORKBOOK, runs_root_dir=tmpdir)
            # Create a mock registry allowing G35 to test runner formula protection
            test_registry = PEAKInputRegistry(
                [
                    ValuationCellDefinition(
                        canonical_variable="debt_weight",
                        sheet="Assumptions",
                        cell="G35",
                        is_formula=False,
                    )
                ]
            )
            runner.registry = test_registry

            formula_write_input = ValuationInputPayload(
                canonical_variable="debt_weight",
                target_sheet="Assumptions",
                target_cell="G35",  # Has formula =1-G34 in master workbook
                value=0.35,
                entity_id="ent_test",
            )
            with pytest.raises(FormulaOverwriteProhibitedError) as exc:
                runner.execute_run(inputs=[formula_write_input], entity_id="ent_test")
            assert "contains formula" in str(exc.value)

            # Golden master must remain unchanged
            assert compute_file_sha256(MASTER_WORKBOOK) == EXPECTED_MASTER_SHA256

    def test_successful_sandbox_execution_and_formula_preservation(self):
        """Execute a valid sandbox run, verify formulas, hash stability, and manifest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = PEAKSandboxRunner(master_workbook_path=MASTER_WORKBOOK, runs_root_dir=tmpdir)

            valid_inputs = [
                ValuationInputPayload(
                    canonical_variable="revenue_current",
                    target_sheet="Assumptions",
                    target_cell="O12",
                    value=6500000.0,
                    entity_id="ent_apple",
                    fact_id="fact_rev_001",
                ),
                ValuationInputPayload(
                    canonical_variable="equity_weight",
                    target_sheet="Assumptions",
                    target_cell="G34",
                    value=0.70,
                    entity_id="ent_apple",
                    fact_id="fact_wacc_001",
                ),
            ]

            manifest = runner.execute_run(
                inputs=valid_inputs,
                entity_id="ent_apple",
                run_id="run_test_001",
                mode=CalculationMode.HEADLESS,
            )

            # Check manifest properties
            assert manifest.run_id == "run_test_001"
            assert manifest.calculation_status == CalculationStatus.NOT_CALCULATED_HEADLESS
            assert len(manifest.inputs_applied) == 2
            assert manifest.integrity_report is not None
            assert manifest.integrity_report.formulas_identical is True
            assert manifest.integrity_report.formula_count_pre == 6875
            assert manifest.integrity_report.formula_count_post == 6875
            assert manifest.integrity_report.original_workbook_unmodified is True

            # Verify manifest was written to disk
            manifest_file = Path(tmpdir) / "run_test_001" / "run_manifest.json"
            assert manifest_file.exists()

            # Verify sandbox workbook has updated inputs
            import openpyxl
            sandbox_wb = Path(tmpdir) / "run_test_001" / "valuation_sandbox.xlsx"
            wb = openpyxl.load_workbook(sandbox_wb, data_only=False)
            assert wb["Assumptions"]["O12"].value == 6500000.0
            assert wb["Assumptions"]["G34"].value == 0.70
            assert wb["Assumptions"]["G35"].value == "=1-G34"  # Formula preserved!
            wb.close()

            # Golden master MUST remain untouched
            assert compute_file_sha256(MASTER_WORKBOOK) == EXPECTED_MASTER_SHA256
