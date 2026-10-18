"""Unit tests for FactRecord, fact states, transformation tracking, and validation models."""

from datetime import date
from opendeal.core.types import (
    AccountingStandard,
    Currency,
    FactState,
    FiscalPeriodType,
    MissingDataReason,
    Sign,
    UnitScale,
    ValidationSeverity,
    ValidationStatus,
)
from opendeal.facts.models import (
    FactRecord,
    TransformationStep,
    ValidationResult,
)


class TestFactRecord:
    """Test FactRecord lifecycle, zero vs missing distinction, and transformation history."""

    def test_fact_creation_reported_positive(self):
        """Standard reported positive financial fact."""
        fact = FactRecord(
            entity_id="ent_test1",
            canonical_variable="revenue",
            reported_value=383285.0,
            normalized_value=383285000000.0,
            currency=Currency.USD,
            unit_scale=UnitScale.MILLIONS,
            scale=6,
            sign=Sign.POSITIVE,
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.ANNUAL,
            period_start=date(2022, 10, 1),
            period_end=date(2023, 9, 30),
            accounting_standard=AccountingStandard.US_GAAP,
            evidence_ids=["ev_123456"],
        )

        assert fact.fact_id.startswith("fact_")
        assert fact.canonical_variable == "revenue"
        assert fact.reported_value == 383285.0
        assert fact.normalized_value == 383285000000.0
        assert fact.sign == Sign.POSITIVE
        assert fact.unit_scale.multiplier == 1_000_000.0
        assert fact.fiscal_year == 2023
        assert fact.fiscal_period == FiscalPeriodType.ANNUAL
        assert fact.is_instant is False

    def test_reported_zero_is_explicitly_zero(self):
        """When an issuer reports a legitimate zero, normalized_value is 0.0 with sign ZERO."""
        fact = FactRecord(
            entity_id="ent_test2",
            canonical_variable="long_term_debt",
            reported_value=0.0,
            normalized_value=0.0,
            currency=Currency.USD,
            unit_scale=UnitScale.EXACT,
            sign=Sign.ZERO,
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.INSTANT,
            is_instant=True,
            validation_status=ValidationStatus.VALID,
        )

        assert fact.reported_value == 0.0
        assert fact.normalized_value == 0.0
        assert fact.sign == Sign.ZERO
        assert fact.validation_status == ValidationStatus.VALID
        assert fact.fact_state != FactState.MISSING_FACT

    def test_missing_fact_factory_never_substitutes_zero(self):
        """Missing facts record DATA NOT FOUND with an explicit reason, never 0.0."""
        missing_fact = FactRecord.create_missing(
            entity_id="ent_test3",
            canonical_variable="research_and_development",
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.ANNUAL,
            missing_reason=MissingDataReason.NOT_REPORTED,
            conflict_note="Filing does not segregate R&D expenses",
        )

        assert missing_fact.reported_value is None
        assert missing_fact.normalized_value is None
        assert missing_fact.fact_state == FactState.MISSING_FACT
        assert missing_fact.validation_status == ValidationStatus.DATA_NOT_FOUND
        assert missing_fact.missing_reason == MissingDataReason.NOT_REPORTED
        assert missing_fact.conflict_note == "Filing does not segregate R&D expenses"

    def test_transformation_history_preserved(self):
        """Transformation steps record an explicit audit trail."""
        step = TransformationStep(
            step_name="UNIT_MULTIPLIER",
            input_value=150.0,
            output_value=150_000_000.0,
            rationale="Converted millions to exact currency unit",
        )

        fact = FactRecord(
            entity_id="ent_trans",
            canonical_variable="ebitda",
            reported_value=150.0,
            normalized_value=150_000_000.0,
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.ANNUAL,
            transformation_history=[step],
        )

        assert len(fact.transformation_history) == 1
        assert fact.transformation_history[0].step_name == "UNIT_MULTIPLIER"
        assert fact.transformation_history[0].output_value == 150_000_000.0

    def test_competing_fact_ids_preserves_contradictory_evidence(self):
        """Competing fact IDs link contradictory sources rather than silently overwriting."""
        fact_sec = FactRecord(
            entity_id="ent_dual",
            canonical_variable="revenue",
            reported_value=1000.0,
            normalized_value=1000.0,
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.ANNUAL,
            competing_fact_ids=["fact_audit_report_alt"],
            conflict_note="SEC 10-K reported 1000, Press Release reported 1050",
        )

        assert "fact_audit_report_alt" in fact_sec.competing_fact_ids
        assert fact_sec.conflict_note is not None

    def test_validation_result_model(self):
        """ValidationResult captures rule outcome and severity."""
        result = ValidationResult(
            target_id="fact_test_123",
            target_type="fact",
            rule_name="positive_assets_check",
            passed=False,
            severity=ValidationSeverity.ERROR,
            observed_value=-500.0,
            expected_range=">= 0.0",
            message="Total assets cannot be negative",
        )

        assert result.result_id.startswith("val_")
        assert result.passed is False
        assert result.severity == ValidationSeverity.ERROR
        assert result.observed_value == -500.0
