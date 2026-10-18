"""Parsers for official SEC EDGAR submissions and XBRL financial facts."""

import json
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from opendeal.core.types import (
    AccountingStandard,
    Currency,
    EntityType,
    FactState,
    FiscalPeriodType,
    MissingDataReason,
    Sign,
    SourceType,
    UnitScale,
    ValidationStatus,
)
from opendeal.entities.models import CompanyEntity, LegalIdentifier
from opendeal.evidence.models import EvidenceRecord, SourceReference
from opendeal.facts.models import FactRecord, TransformationStep

# Mapping from canonical variables to preferred US-GAAP XBRL tags (in priority order)
US_GAAP_TAG_MAPPING: Dict[str, List[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "TotalRevenuesAndOtherIncome",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "gross_profit": [
        "GrossProfit",
    ],
    "total_assets": [
        "Assets",
    ],
    "total_liabilities": [
        "Liabilities",
    ],
    "stockholders_equity": [
        "StockholdersEquity",
        "CommonStockholdersEquity",
    ],
    "cash_and_cash_equivalents": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "long_term_debt": [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
    ],
    "capital_expenditures": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "shares_outstanding": [
        "CommonStockSharesOutstanding",
    ],
}

# Tags that represent point-in-time Balance Sheet snapshots (Instant)
INSTANT_TAGS = {
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "CommonStockholdersEquity",
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    "LongTermDebtNoncurrent",
    "LongTermDebt",
    "CommonStockSharesOutstanding",
}


class SECParser:
    """Parses official SEC EDGAR JSON payloads into canonical OpenDeal models."""

    @classmethod
    def parse_submissions_to_entity(cls, submissions_data: Dict[str, Any]) -> CompanyEntity:
        """Parse SEC submissions.json into a CompanyEntity."""
        cik_raw = str(submissions_data.get("cik", ""))
        cik_normalized = cik_raw.zfill(10)

        legal_name = submissions_data.get("name", "").strip()
        if not legal_name:
            legal_name = f"SEC Issuer CIK {cik_normalized}"

        state_inc = submissions_data.get("stateOfIncorporation", "").strip()
        jurisdiction = f"US-{state_inc}" if state_inc else "US"

        tickers = submissions_data.get("tickers", [])
        primary_ticker = tickers[0] if tickers else None

        exchanges = submissions_data.get("exchanges", [])
        primary_exchange = exchanges[0] if exchanges else None

        entity_type = EntityType.PUBLIC_LISTED if primary_ticker else EntityType.UNKNOWN

        entity = CompanyEntity(
            legal_name=legal_name,
            jurisdiction=jurisdiction,
            entity_type=entity_type,
            ticker=primary_ticker,
            primary_exchange=primary_exchange,
            primary_country="US",
            sic_code=str(submissions_data.get("sic", "")),
            sic_description=submissions_data.get("sicDescription"),
            website=submissions_data.get("website"),
            metadata={
                "fiscalYearEnd": submissions_data.get("fiscalYearEnd"),
                "ein": submissions_data.get("ein"),
                "stateOfIncorporation": state_inc,
            },
        )

        entity.add_identifier("CIK", cik_normalized, jurisdiction="US", is_primary=True)
        if primary_ticker:
            entity.add_identifier("TICKER", primary_ticker, is_primary=True)

        ein = submissions_data.get("ein")
        if ein:
            entity.add_identifier("IRS_EIN", str(ein), jurisdiction="US")

        return entity

    @classmethod
    def parse_company_facts_to_records(
        cls,
        facts_data: Dict[str, Any],
        entity: CompanyEntity,
        source_url: str,
        payload_hash: Optional[str] = None,
    ) -> Tuple[SourceReference, List[EvidenceRecord], List[FactRecord]]:
        """Parse SEC companyfacts.json into SourceReference, EvidenceRecords, and FactRecords.
        
        Accurately differentiates:
        - Instant (Balance Sheet) vs. Duration (Income/Cash Flow Statement).
        - Single-quarter (3-month) vs. Cumulative Year-to-Date (6-month / 9-month) vs. Annual.
        - Reported Zero vs. Missing Data (never fabricates 0 for omitted tags).
        """
        cik = str(facts_data.get("cik", "")).zfill(10)
        source = SourceReference(
            source_type=SourceType.REGULATORY_FILING,
            source_url=source_url,
            publisher="US Securities and Exchange Commission (SEC EDGAR)",
            document_id=f"CIK{cik}",
            content_hash=payload_hash,
            metadata={"entityName": facts_data.get("entityName")},
        )

        evidence_records: List[EvidenceRecord] = []
        fact_records: List[FactRecord] = []

        us_gaap_facts = facts_data.get("facts", {}).get("us-gaap", {})
        if not us_gaap_facts:
            return source, evidence_records, fact_records

        for canonical_var, tag_candidates in US_GAAP_TAG_MAPPING.items():
            selected_tag: Optional[str] = None
            tag_data: Optional[Dict[str, Any]] = None

            for candidate in tag_candidates:
                if candidate in us_gaap_facts:
                    selected_tag = candidate
                    tag_data = us_gaap_facts[candidate]
                    break

            if not selected_tag or not tag_data:
                continue

            is_instant_tag = selected_tag in INSTANT_TAGS
            units_dict = tag_data.get("units", {})

            for unit_key, fact_items in units_dict.items():
                currency = Currency.USD if unit_key.upper() == "USD" else Currency.UNKNOWN
                unit_scale = UnitScale.EXACT

                for item in fact_items:
                    raw_val = item.get("val")
                    if raw_val is None:
                        continue

                    # Extract temporal properties
                    fy = item.get("fy")
                    fp = item.get("fp", "").upper()
                    form = item.get("form", "").upper()
                    end_str = item.get("end")
                    start_str = item.get("start")
                    accn = item.get("accn", "")
                    filed_str = item.get("filed")

                    if not fy or not end_str:
                        continue

                    period_end = date.fromisoformat(end_str)
                    period_start = date.fromisoformat(start_str) if start_str else None

                    # Deduce exact fiscal period type (single quarter vs YTD vs annual)
                    fiscal_period = cls._deduce_fiscal_period(
                        is_instant=is_instant_tag,
                        fp=fp,
                        form=form,
                        period_start=period_start,
                        period_end=period_end,
                    )

                    # Create EvidenceRecord
                    raw_snippet = json.dumps(item, sort_keys=True)
                    evidence = EvidenceRecord.create(
                        source_id=source.source_id,
                        raw_content=raw_snippet,
                        extraction_method="XBRL_TAG",
                        document_name=f"{accn}_{form}",
                        tag_name=f"us-gaap:{selected_tag}",
                        confidence_score=1.0,
                    )
                    evidence_records.append(evidence)

                    # Compute sign and normalized value
                    num_val = float(raw_val)
                    sign = Sign.from_value(num_val)

                    # Build FactRecord
                    transformation = TransformationStep(
                        step_name="XBRL_RAW_EXTRACTION",
                        input_value=raw_val,
                        output_value=num_val,
                        rationale=f"Extracted verbatim from SEC XBRL tag us-gaap:{selected_tag} ({unit_key})",
                    )

                    is_amendment = form.endswith("/A")
                    is_segmented = bool(item.get("segment") or item.get("axis") or item.get("member"))

                    fact = FactRecord(
                        entity_id=entity.entity_id,
                        canonical_variable=canonical_var,
                        fact_state=FactState.STANDARDIZED_FACT,
                        reported_value=num_val,
                        normalized_value=num_val,
                        currency=currency,
                        unit_scale=unit_scale,
                        scale=0,
                        sign=sign,
                        fiscal_year=int(fy),
                        fiscal_period=fiscal_period,
                        period_start=period_start,
                        period_end=period_end,
                        is_instant=is_instant_tag,
                        accounting_standard=AccountingStandard.US_GAAP,
                        evidence_ids=[evidence.evidence_id],
                        transformation_history=[transformation],
                        validation_status=ValidationStatus.VALID,
                        missing_reason=MissingDataReason.NONE,
                        metadata={
                            "accn": accn,
                            "form": form,
                            "tag": selected_tag,
                            "filed": filed_str,
                            "frame": item.get("frame"),
                            "is_amendment": is_amendment,
                            "is_segmented": is_segmented,
                        },
                    )
                    fact_records.append(fact)

        return source, evidence_records, fact_records

    @staticmethod
    def _deduce_fiscal_period(
        is_instant: bool,
        fp: str,
        form: str,
        period_start: Optional[date],
        period_end: date,
    ) -> FiscalPeriodType:
        """Deduce standard fiscal period type from XBRL duration and form attributes."""
        if is_instant:
            return FiscalPeriodType.INSTANT

        if form == "10-K" or fp == "FY":
            return FiscalPeriodType.ANNUAL

        if period_start:
            days = (period_end - period_start).days
            if 340 <= days <= 380:
                return FiscalPeriodType.ANNUAL
            if 70 <= days <= 110:
                if fp in ("Q1", "Q2", "Q3", "Q4"):
                    return FiscalPeriodType(fp)
                return FiscalPeriodType.Q1
            if 150 <= days <= 210:
                return FiscalPeriodType.YTD_Q2
            if 240 <= days <= 300:
                return FiscalPeriodType.YTD_Q3

        if fp in ("Q1", "Q2", "Q3", "Q4"):
            return FiscalPeriodType(fp)

        return FiscalPeriodType.OTHER
