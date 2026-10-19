from __future__ import annotations

import copy
import logging
from typing import Any, ClassVar

from opendeal.connectors.sec.parser import SECParser
from opendeal.core.types import (
    FactState,
    MissingDataReason,
    Sign,
    UnitScale,
    ValidationSeverity,
    ValidationStatus,
)
from opendeal.entities.models import CompanyEntity
from opendeal.evidence.models import EvidenceRecord, SourceReference
from opendeal.facts.models import FactRecord, TransformationStep, ValidationResult
from opendeal.storage.base import StorageInterface

logger = logging.getLogger(__name__)


class FactStandardizer:
    """Standardizes raw extracted facts into base canonical units and normalized scales."""

    @classmethod
    def standardize(cls, raw_fact: FactRecord) -> FactRecord:
        """Create a separate standardized fact record without altering raw fact or evidence."""
        # Deepcopy to ensure original raw fact is strictly preserved
        std_fact = copy.deepcopy(raw_fact)
        std_fact.fact_state = FactState.STANDARDIZED_FACT

        if std_fact.reported_value is None:
            std_fact.fact_state = FactState.MISSING_FACT
            std_fact.validation_status = ValidationStatus.DATA_NOT_FOUND
            if std_fact.missing_reason == MissingDataReason.NONE:
                std_fact.missing_reason = MissingDataReason.NOT_REPORTED
            return std_fact

        # Convert numeric values
        try:
            val_float = float(std_fact.reported_value)
        except (ValueError, TypeError):
            std_fact.fact_state = FactState.MISSING_FACT
            std_fact.validation_status = ValidationStatus.INVALID
            std_fact.missing_reason = MissingDataReason.EXTRACTION_FAILED
            return std_fact

        # Apply multiplier from unit scale
        multiplier = std_fact.unit_scale.multiplier
        normalized_val = val_float * multiplier

        if multiplier != 1.0 or std_fact.normalized_value != normalized_val:
            step = TransformationStep(
                step_name="UNIT_SCALE_STANDARDIZATION",
                input_value=val_float,
                output_value=normalized_val,
                rationale=f"Multiplied by {multiplier:.0f} to normalize {std_fact.unit_scale.value} to base exact unit",
            )
            std_fact.transformation_history.append(step)

        std_fact.normalized_value = normalized_val
        std_fact.unit_scale = UnitScale.EXACT
        std_fact.scale = 0
        std_fact.sign = Sign.from_value(normalized_val)

        return std_fact


class FactValidator:
    """Validates standardized facts against economic constraints and domain invariants."""

    # Validation rules: (min_bound, max_bound, severity)
    NUMERIC_BOUNDS: ClassVar[dict[str, tuple[float | None, float | None, ValidationSeverity]]] = {
        "revenue": (-1e12, 1e15, ValidationSeverity.WARNING),      # Extreme or negative revenue flags warning
        "total_assets": (0.0, 1e16, ValidationSeverity.ERROR),     # Assets cannot be negative
        "shares_outstanding": (0.0, 1e14, ValidationSeverity.ERROR),
        "capital_expenditures": (0.0, 1e14, ValidationSeverity.WARNING),
    }

    @classmethod
    def validate(cls, fact: FactRecord) -> tuple[FactRecord, list[ValidationResult]]:
        """Evaluate validation rules against a fact record."""
        validated_fact = copy.deepcopy(fact)
        results: list[ValidationResult] = []

        # 1. Check Missing Fact consistency
        if validated_fact.fact_state == FactState.MISSING_FACT or validated_fact.normalized_value is None:
            if validated_fact.missing_reason == MissingDataReason.NONE:
                res = ValidationResult(
                    target_id=validated_fact.fact_id,
                    rule_name="missing_data_reason_specified",
                    passed=False,
                    severity=ValidationSeverity.ERROR,
                    observed_value=None,
                    expected_range="missing_reason != NONE",
                    message="Missing fact must specify an explicit missing data reason (DATA NOT FOUND).",
                )
                results.append(res)
                validated_fact.validation_status = ValidationStatus.INVALID
            else:
                validated_fact.validation_status = ValidationStatus.DATA_NOT_FOUND
            return validated_fact, results

        val = validated_fact.normalized_value
        var_name = validated_fact.canonical_variable

        # 2. Check Sign Invariant
        expected_sign = Sign.from_value(val)
        if validated_fact.sign != expected_sign:
            res = ValidationResult(
                target_id=validated_fact.fact_id,
                rule_name="sign_consistency",
                passed=False,
                severity=ValidationSeverity.ERROR,
                observed_value=validated_fact.sign.value,
                expected_range=expected_sign.value,
                message=f"Sign '{validated_fact.sign.value}' does not match normalized value {val}",
            )
            results.append(res)
            validated_fact.sign = expected_sign

        # 3. Check Period Chronology Invariant (for duration facts)
        if (
            not validated_fact.is_instant
            and validated_fact.period_start
            and validated_fact.period_end
            and validated_fact.period_start > validated_fact.period_end
        ):
            res = ValidationResult(
                target_id=validated_fact.fact_id,
                rule_name="period_chronology",
                passed=False,
                severity=ValidationSeverity.ERROR,
                observed_value=f"{validated_fact.period_start} > {validated_fact.period_end}",
                expected_range="period_start <= period_end",
                message=f"Reporting period start ({validated_fact.period_start}) cannot be after end ({validated_fact.period_end})",
            )
            results.append(res)

        # 4. Check Domain Bounds
        if var_name in cls.NUMERIC_BOUNDS:
            min_val, max_val, severity = cls.NUMERIC_BOUNDS[var_name]
            if min_val is not None and val < min_val:
                res = ValidationResult(
                    target_id=validated_fact.fact_id,
                    rule_name=f"{var_name}_minimum_bound",
                    passed=False,
                    severity=severity,
                    observed_value=val,
                    expected_range=f">= {min_val}",
                    message=f"Value {val} for '{var_name}' is below expected minimum {min_val}",
                )
                results.append(res)
            if max_val is not None and val > max_val:
                res = ValidationResult(
                    target_id=validated_fact.fact_id,
                    rule_name=f"{var_name}_maximum_bound",
                    passed=False,
                    severity=severity,
                    observed_value=val,
                    expected_range=f"<= {max_val}",
                    message=f"Value {val} for '{var_name}' exceeds plausible maximum {max_val}",
                )
                results.append(res)

        # 4. Determine overall validation status
        has_error = any(r.severity in (ValidationSeverity.ERROR, ValidationSeverity.CRITICAL) and not r.passed for r in results)
        has_warning = any(r.severity == ValidationSeverity.WARNING and not r.passed for r in results)

        if has_error:
            validated_fact.validation_status = ValidationStatus.INVALID
        elif has_warning:
            validated_fact.validation_status = ValidationStatus.WARNING
            validated_fact.fact_state = FactState.VALIDATED_FACT
        else:
            validated_fact.validation_status = ValidationStatus.VALID
            validated_fact.fact_state = FactState.VALIDATED_FACT

        return validated_fact, results


class FactPipeline:
    """Coordinates the full vertical pipeline: Raw Extraction -> Standardization -> Validation -> Persistence."""

    def __init__(self, storage: StorageInterface):
        self.storage = storage

    def ingest_sec_facts(
        self,
        facts_data: dict[str, Any],
        entity: CompanyEntity,
        source_url: str,
        payload_hash: str | None = None,
    ) -> dict[str, Any]:
        """Execute full vertical slice from SEC XBRL data into validated storage."""
        # 1. Parse SEC facts, generating SourceReference, EvidenceRecords, and FactRecords
        source, evidence_records, raw_facts = SECParser.parse_company_facts_to_records(
            facts_data=facts_data,
            entity=entity,
            source_url=source_url,
            payload_hash=payload_hash,
        )

        # 2. Persist Entity & Source
        self.storage.save_entity(entity)
        self.storage.save_source(source)

        # 3. Persist Evidence (strictly immutable)
        for ev in evidence_records:
            self.storage.save_evidence(ev)

        # 4. Standardize and Validate each fact
        validated_facts: list[FactRecord] = []
        all_validation_results: list[ValidationResult] = []

        for raw in raw_facts:
            std_fact = FactStandardizer.standardize(raw)
            val_fact, results = FactValidator.validate(std_fact)

            self.storage.save_fact(val_fact)
            for res in results:
                self.storage.save_validation_result(res)

            validated_facts.append(val_fact)
            all_validation_results.extend(results)

        return {
            "entity_id": entity.entity_id,
            "source_id": source.source_id,
            "evidence_count": len(evidence_records),
            "total_facts": len(validated_facts),
            "validated_count": sum(1 for f in validated_facts if f.validation_status == ValidationStatus.VALID),
            "warning_count": sum(1 for f in validated_facts if f.validation_status == ValidationStatus.WARNING),
            "invalid_count": sum(1 for f in validated_facts if f.validation_status == ValidationStatus.INVALID),
            "validation_results_count": len(all_validation_results),
        }

    def ingest_statutory_facts(
        self,
        entity: CompanyEntity,
        source: SourceReference,
        evidence_records: list[EvidenceRecord],
        raw_facts: list[FactRecord],
        regulatory_authority: str | None = None,
    ) -> dict[str, Any]:
        """Execute vertical ingestion for facts from any global statutory authority (e.g. MCA, ESMA, BaFin, Companies House, SEC)."""
        # 1. Persist Entity & Source
        self.storage.save_entity(entity)
        self.storage.save_source(source)

        # 2. Persist Evidence
        for ev in evidence_records:
            self.storage.save_evidence(ev)

        # 3. Standardize and Validate facts
        validated_facts: list[FactRecord] = []
        all_validation_results: list[ValidationResult] = []

        for raw in raw_facts:
            std_fact = FactStandardizer.standardize(raw)
            val_fact, results = FactValidator.validate(std_fact)

            self.storage.save_fact(val_fact)
            for res in results:
                self.storage.save_validation_result(res)

            validated_facts.append(val_fact)
            all_validation_results.extend(results)

        return {
            "entity_id": entity.entity_id,
            "source_id": source.source_id,
            "regulatory_authority": regulatory_authority or "STATUTORY",
            "evidence_count": len(evidence_records),
            "total_facts": len(validated_facts),
            "validated_count": sum(1 for f in validated_facts if f.validation_status == ValidationStatus.VALID),
            "warning_count": sum(1 for f in validated_facts if f.validation_status == ValidationStatus.WARNING),
            "invalid_count": sum(1 for f in validated_facts if f.validation_status == ValidationStatus.INVALID),
            "validation_results_count": len(all_validation_results),
        }
