"""Valuation input acceptance engine and domain validation rules."""

from __future__ import annotations

from typing import List, Optional, Tuple
from opendeal.core.types import Currency, ValidationStatus
from opendeal.facts.models import FactRecord
from opendeal.valuation.contracts import ValuationCellDefinition, ValuationInputPayload
from opendeal.valuation.exceptions import (
    AmbiguousMappingError,
    UnauthorizedCellWriteError,
    ValuationInputValidationError,
)
from opendeal.valuation.mapping import PEAKInputRegistry


class ValuationInputAcceptanceEngine:
    """Validates financial facts before they can enter the PEAK valuation sandbox."""

    NON_NEGATIVE_METRICS = {
        "debt_current",
        "financial_debt_current",
        "financial_debt_prev",
        "financial_debt_prev_prev",
        "cash_current",
        "cash_prev",
        "cash_prev_prev",
        "shares_outstanding",
        "depreciation_current",
        "working_capital_receivables_days",
        "working_capital_inventory_days",
        "working_capital_payables_days",
    }

    @classmethod
    def validate_fact_for_valuation(
        cls,
        fact: FactRecord,
        target_entity_id: str,
        registry: PEAKInputRegistry,
        expected_currency: Currency = Currency.USD,
        target_fiscal_year: Optional[int] = None,
    ) -> ValuationInputPayload:
        """Validate a FactRecord against PEAK acceptance rules.

        Raises ValuationInputValidationError if any rule fails.
        """
        # 1. Entity identity check
        if fact.entity_id != target_entity_id:
            raise ValuationInputValidationError(
                f"Entity mismatch: fact belongs to '{fact.entity_id}', expected target '{target_entity_id}'"
            )

        # 2. Validation status check
        if fact.validation_status != ValidationStatus.VALID:
            raise ValuationInputValidationError(
                f"Fact '{fact.fact_id}' has validation status '{fact.validation_status}', must be VALID"
            )

        # 3. Source evidence provenance check
        if not fact.evidence_ids and not fact.metadata.get("source_reference_id"):
            raise ValuationInputValidationError(
                f"Fact '{fact.fact_id}' lacks source evidence or provenance record."
            )

        # 4. Missing value rule: Missing data must remain missing, never substituted
        from opendeal.core.types import FactState, Sign

        is_zero = fact.sign == Sign.ZERO or (fact.normalized_value == 0.0 and fact.reported_value == 0.0)
        if fact.fact_state == FactState.MISSING_FACT or (fact.normalized_value is None and not is_zero):
            raise ValuationInputValidationError(
                f"Fact '{fact.fact_id}' for '{fact.canonical_variable}' is missing or null. "
                "Missing data cannot be silently substituted with zero or estimates."
            )

        value = fact.normalized_value if fact.normalized_value is not None else 0.0

        # 5. Currency compatibility
        if fact.currency != expected_currency:
            raise ValuationInputValidationError(
                f"Currency mismatch for '{fact.canonical_variable}': fact has '{fact.currency}', "
                f"expected '{expected_currency}'. Explicit currency conversion is required."
            )

        # 6. Fiscal year alignment (if target specified)
        if target_fiscal_year is not None and fact.fiscal_year is not None:
            # For historical multi-year facts (e.g. prev, prev_prev), allow corresponding offsets
            is_prev = "prev" in fact.canonical_variable
            if not is_prev and fact.fiscal_year != target_fiscal_year:
                raise ValuationInputValidationError(
                    f"Fiscal year mismatch: fact FY{fact.fiscal_year} does not match target FY{target_fiscal_year}"
                )

        # 7. Mapping approval check
        cell_defn: Optional[ValuationCellDefinition] = registry.get_by_variable(fact.canonical_variable)
        if not cell_defn:
            raise UnauthorizedCellWriteError(
                f"No authorized PEAK mapping exists for canonical variable '{fact.canonical_variable}'"
            )

        # 8. Domain constraints
        if fact.canonical_variable in cls.NON_NEGATIVE_METRICS and value < 0:
            raise ValuationInputValidationError(
                f"Domain constraint violation: '{fact.canonical_variable}' value {value} cannot be negative."
            )

        return ValuationInputPayload(
            canonical_variable=fact.canonical_variable,
            target_sheet=cell_defn.sheet,
            target_cell=cell_defn.cell,
            value=value,
            entity_id=fact.entity_id,
            currency=fact.currency,
            fiscal_year=fact.fiscal_year,
            fiscal_period=fact.fiscal_period,
            fact_id=fact.fact_id,
            source_evidence_id=fact.evidence_ids[0] if fact.evidence_ids else None,
            is_reported_zero=is_zero,
            transformation_notes="Accepted through ValuationInputAcceptanceEngine",
        )

    @classmethod
    def validate_batch(
        cls,
        facts: List[FactRecord],
        target_entity_id: str,
        registry: PEAKInputRegistry,
        expected_currency: Currency = Currency.USD,
        target_fiscal_year: Optional[int] = None,
    ) -> Tuple[List[ValuationInputPayload], List[str]]:
        """Validate a batch of facts, collecting accepted inputs and error messages."""
        accepted = []
        errors = []
        for fact in facts:
            try:
                payload = cls.validate_fact_for_valuation(
                    fact=fact,
                    target_entity_id=target_entity_id,
                    registry=registry,
                    expected_currency=expected_currency,
                    target_fiscal_year=target_fiscal_year,
                )
                accepted.append(payload)
            except Exception as e:
                errors.append(f"Fact {fact.fact_id} ({fact.canonical_variable}): {str(e)}")
        return accepted, errors
