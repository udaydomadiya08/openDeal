"""Unit tests for financial fact pipeline edge cases, restatements, and provenance."""

import json
from datetime import date
import pytest

from opendeal.connectors.sec.parser import SECParser
from opendeal.core.types import (
    Currency,
    FactState,
    FiscalPeriodType,
    MissingDataReason,
    Sign,
    ValidationSeverity,
    ValidationStatus,
)
from opendeal.entities.models import CompanyEntity
from opendeal.facts.models import FactRecord
from opendeal.pipeline.stages import FactPipeline, FactStandardizer, FactValidator
from opendeal.storage.sqlite import SQLiteStorage


class TestFactPipelineEdgeCases:
    """Rigorous regression tests for edge cases in financial-fact processing."""

    def test_negative_net_income_preserved(self):
        """Negative net income must be preserved as negative with Sign.NEGATIVE, never coerced to positive."""
        raw_fact = FactRecord(
            entity_id="ent_loss_maker",
            canonical_variable="net_income",
            reported_value=-150000000.0,
            currency=Currency.USD,
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.ANNUAL,
        )

        std_fact = FactStandardizer.standardize(raw_fact)
        assert std_fact.normalized_value == -150000000.0
        assert std_fact.sign == Sign.NEGATIVE

        val_fact, results = FactValidator.validate(std_fact)
        assert val_fact.normalized_value == -150000000.0
        assert val_fact.sign == Sign.NEGATIVE
        assert val_fact.validation_status == ValidationStatus.VALID

    def test_zero_debt_is_valid(self):
        """Zero debt for a debt-free company is legitimate and must not be flagged as missing."""
        raw_fact = FactRecord(
            entity_id="ent_debt_free",
            canonical_variable="total_debt",
            reported_value=0.0,
            currency=Currency.USD,
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.INSTANT,
            is_instant=True,
        )

        std_fact = FactStandardizer.standardize(raw_fact)
        assert std_fact.normalized_value == 0.0
        assert std_fact.sign == Sign.ZERO

        val_fact, results = FactValidator.validate(std_fact)
        assert val_fact.normalized_value == 0.0
        assert val_fact.sign == Sign.ZERO
        assert val_fact.validation_status == ValidationStatus.VALID

    def test_negative_assets_rejected(self):
        """Total assets cannot be negative; validator must flag as ERROR."""
        raw_fact = FactRecord(
            entity_id="ent_corrupted",
            canonical_variable="total_assets",
            reported_value=-5000.0,
            normalized_value=-5000.0,
            currency=Currency.USD,
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.INSTANT,
            is_instant=True,
        )

        val_fact, results = FactValidator.validate(raw_fact)
        assert val_fact.validation_status == ValidationStatus.INVALID
        errors = [r for r in results if r.severity == ValidationSeverity.ERROR]
        assert any("total_assets_minimum_bound" in r.rule_name for r in errors)

    def test_period_chronology_start_after_end_rejected(self):
        """If period start is chronologically after period end, validator marks INVALID."""
        fact = FactRecord(
            entity_id="ent_inverted_dates",
            canonical_variable="revenue",
            reported_value=100.0,
            normalized_value=100.0,
            currency=Currency.USD,
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.ANNUAL,
            period_start=date(2023, 12, 31),
            period_end=date(2023, 1, 1),
            is_instant=False,
        )

        val_fact, results = FactValidator.validate(fact)
        assert val_fact.validation_status == ValidationStatus.INVALID
        chron_errors = [r for r in results if r.rule_name == "period_chronology"]
        assert len(chron_errors) == 1
        assert chron_errors[0].severity == ValidationSeverity.ERROR

    def test_non_calendar_fiscal_year_end_preserved(self):
        """Entities with non-calendar fiscal years (e.g. October to September) are preserved."""
        entity = CompanyEntity(
            legal_name="Apple Inc.",
            jurisdiction="US-CA",
            metadata={"fiscalYearEnd": "0930"},
        )
        entity.add_identifier("CIK", "0000320193")

        # Apple FY 2023 spans 2022-10-01 to 2023-09-30
        fact = FactRecord(
            entity_id=entity.entity_id,
            canonical_variable="revenue",
            reported_value=383285000000.0,
            normalized_value=383285000000.0,
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.ANNUAL,
            period_start=date(2022, 10, 1),
            period_end=date(2023, 9, 30),
        )

        std = FactStandardizer.standardize(fact)
        val, _ = FactValidator.validate(std)
        assert val.fiscal_year == 2023
        assert val.period_end == date(2023, 9, 30)
        assert val.validation_status == ValidationStatus.VALID

    def test_restatement_and_amendment_flagged(self):
        """Filing form 10-K/A or 10-Q/A is detected and flagged as an amendment."""
        amended_payload = {
            "cik": 320193,
            "entityName": "Restated Corp",
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "label": "Revenues",
                        "units": {
                            "USD": [
                                {
                                    "end": "2023-09-30",
                                    "val": 400000000000,
                                    "fy": 2023,
                                    "fp": "FY",
                                    "form": "10-K/A",
                                    "filed": "2024-03-01",
                                    "accn": "0000320193-24-000001",
                                    "start": "2022-10-01",
                                }
                            ]
                        },
                    }
                }
            },
        }

        entity = CompanyEntity(legal_name="Restated Corp", jurisdiction="US")
        entity.add_identifier("CIK", "0000320193")

        source, evidence, facts = SECParser.parse_company_facts_to_records(
            facts_data=amended_payload,
            entity=entity,
            source_url="https://sec.gov",
        )

        assert len(facts) == 1
        assert facts[0].metadata.get("is_amendment") is True
        assert facts[0].metadata.get("form") == "10-K/A"

    def test_competing_evidence_preservation_in_storage(self):
        """Original fact and restated fact for same period are both retained in storage."""
        store = SQLiteStorage(":memory:")
        store.initialize()

        entity = CompanyEntity(legal_name="Duel Facts Corp", jurisdiction="US")
        store.save_entity(entity)

        # Original filing
        fact1 = FactRecord(
            entity_id=entity.entity_id,
            canonical_variable="revenue",
            reported_value=100.0,
            normalized_value=100.0,
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.ANNUAL,
            conflict_note="Original 10-K",
        )
        store.save_fact(fact1)

        # Restated filing
        fact2 = FactRecord(
            entity_id=entity.entity_id,
            canonical_variable="revenue",
            reported_value=105.0,
            normalized_value=105.0,
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.ANNUAL,
            conflict_note="Restatement 10-K/A",
        )
        store.save_fact(fact2)

        facts = store.get_facts_for_entity(entity.entity_id, canonical_variable="revenue")
        assert len(facts) == 2
        fact1_ret = store.get_fact(fact1.fact_id)
        fact2_ret = store.get_fact(fact2.fact_id)

        assert fact2.fact_id in fact1_ret.competing_fact_ids
        assert fact1.fact_id in fact2_ret.competing_fact_ids
        store.close()

    def test_pipeline_determinism(self):
        """Processing the exact same raw data repeatedly produces mathematically identical results."""
        raw_fact = FactRecord(
            entity_id="ent_deterministic",
            canonical_variable="operating_cash_flow",
            reported_value=50000.0,
            currency=Currency.USD,
            fiscal_year=2023,
            fiscal_period=FiscalPeriodType.ANNUAL,
        )

        std1 = FactStandardizer.standardize(raw_fact)
        val1, res1 = FactValidator.validate(std1)

        std2 = FactStandardizer.standardize(raw_fact)
        val2, res2 = FactValidator.validate(std2)

        assert val1.normalized_value == val2.normalized_value
        assert val1.sign == val2.sign
        assert val1.validation_status == val2.validation_status
        assert len(res1) == len(res2)
