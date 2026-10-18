"""Deterministic institutional synthetic dataset and seeding for OpenDeal Demo Mode.

Principles:
1. Synthetic Labeling: All entities, metrics, evidence, and filings are explicitly
   labeled as synthetic demonstration data. No real private data is used, and no false
   regulatory citations are claimed.
2. Deterministic Valuation: Financial figures align with standard financial arithmetic
   so that running DCF, WACC, LBO, NAV, or VC calculations executes the exact production
   valuation formulas without discrepancies.
3. Epistemic Transparency: Includes verified source-reported facts, derived metrics,
   explicit analyst assumptions, and intentionally missing disclosures (to showcase
   epistemic gap reporting and explainable penalties).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

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
    ValidationSeverity,
    ValidationStatus,
)
from opendeal.entities.models import (
    CompanyEntity,
    EntityNature,
    LegalIdentifier,
    RelationshipType,
)
from opendeal.evidence.models import EvidenceRecord, SourceReference, compute_sha256
from opendeal.facts.models import FactRecord, TransformationStep, ValidationResult
from opendeal.deals.contracts import (
    DealConflict,
    DealVerificationStatus,
    FundingParticipant,
    FundingRound,
    FundingRoundType,
    InvestorType,
    TransactionEvent,
    TransactionStatus,
    TransactionType,
)
from opendeal.discovery.contracts import (
    CandidateCompany,
    DiscoveryRunMetadata,
    EligibilityOutcome,
    ScreenedTarget,
    ScreeningResult,
    TargetCriteria,
    TargetList,
    TargetListStatus,
)
from opendeal.research.contracts import (
    ClaimVerificationStatus,
    DataGap,
    FinancialTrend,
    KnowledgeTier,
    ResearchBrief,
    ResearchClaim,
    ResearchScope,
    ResearchStatus,
    RiskIndicator,
)
from opendeal.storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)

DEMO_DISCLAIMER = "[SYNTHETIC DEMO EVIDENCE - SIMULATED FOR ILLUSTRATION PURPOSES ONLY]"


def get_demo_entity_ids() -> List[str]:
    """Return canonical IDs of all demonstration companies."""
    return [
        "DEMO-NEXUS",
        "DEMO-APEX",
        "DEMO-VITA",
        "DEMO-HELIOS",
        "DEMO-MERIDIAN",
    ]


def build_demo_entities() -> List[CompanyEntity]:
    """Construct 5 distinct synthetic enterprise profiles across multiple jurisdictions."""
    entities = [
        # 1. High-Growth Cloud SaaS (US Public / Delaware)
        CompanyEntity(
            entity_id="DEMO-NEXUS",
            legal_name="Nexus Cloud Technologies Inc.",
            jurisdiction="US-DE",
            entity_type=EntityType.PUBLIC_LISTED,
            entity_nature="LEGAL_ENTITY",
            ticker="NEXU",
            primary_exchange="NASDAQ",
            incorporation_date=date(2018, 3, 14),
            is_active=True,
            primary_country="US",
            website="https://nexuscloud.example.com",
            sic_code="7372",
            sic_description="Prepackaged Software Services",
            aliases=["Nexus Cloud", "NexusTech", "Nexus Technologies"],
            legal_identifiers=[
                LegalIdentifier(scheme="CIK", identifier="0001999901", jurisdiction="US", is_primary=True),
                LegalIdentifier(scheme="TICKER", identifier="NEXU", jurisdiction="US", is_primary=False),
                LegalIdentifier(scheme="IRS_EIN", identifier="12-3456789", jurisdiction="US", is_primary=False),
                LegalIdentifier(scheme="LEI", identifier="549300NEXUSCLOUD0001", jurisdiction="US", is_primary=False),
            ],
            metadata={
                "is_synthetic": True,
                "demo_profile": "High-Growth Cloud SaaS",
                "notes": "Fast-growing recurring subscription platform. Ideal for DCF, WACC, and peer comps.",
            },
        ),

        # 2. Mature Precision Industrials (UK Registered / Companies House)
        CompanyEntity(
            entity_id="DEMO-APEX",
            legal_name="Apex Industrial Dynamics Plc",
            jurisdiction="GB",
            entity_type=EntityType.PRIVATE_LIMITED,
            entity_nature="LEGAL_ENTITY",
            incorporation_date=date(2012, 7, 21),
            is_active=True,
            primary_country="GB",
            website="https://apexindustrial.example.co.uk",
            sic_code="3569",
            sic_description="General Industrial Machinery and Robotics",
            aliases=["Apex Industrial", "Apex Dynamics"],
            legal_identifiers=[
                LegalIdentifier(scheme="COMPANIES_HOUSE", identifier="09876543", jurisdiction="GB", is_primary=True),
                LegalIdentifier(scheme="LEI", identifier="213800APEXINDUS0002", jurisdiction="GB", is_primary=False),
            ],
            metadata={
                "is_synthetic": True,
                "demo_profile": "Mature Cash-Flow Robotics & Industrials",
                "notes": "Predictable cash flow generator with existing bank debt. Ideal for Leveraged Buyout (LBO).",
            },
        ),

        # 3. Clinical Biotech & Therapeutics (US Public / Delaware)
        CompanyEntity(
            entity_id="DEMO-VITA",
            legal_name="Vitals Biopharma Corp",
            jurisdiction="US-DE",
            entity_type=EntityType.PUBLIC_LISTED,
            entity_nature="LEGAL_ENTITY",
            ticker="VITA",
            primary_exchange="NASDAQ",
            incorporation_date=date(2020, 1, 10),
            is_active=True,
            primary_country="US",
            website="https://vitalsbiopharma.example.com",
            sic_code="2836",
            sic_description="Biological Products Except Diagnostic Substances",
            aliases=["Vitals Bio", "VITA Therapeutics"],
            legal_identifiers=[
                LegalIdentifier(scheme="CIK", identifier="0001999902", jurisdiction="US", is_primary=True),
                LegalIdentifier(scheme="TICKER", identifier="VITA", jurisdiction="US", is_primary=False),
            ],
            metadata={
                "is_synthetic": True,
                "demo_profile": "Asset-Rich Clinical Stage Biotech",
                "notes": "Large cash and patent reserves, minimal debt, high R&D. Ideal for Net Asset Value (NAV).",
            },
        ),

        # 4. CleanTech Venture & Grid Storage (Germany / European Union)
        CompanyEntity(
            entity_id="DEMO-HELIOS",
            legal_name="Helios Clean Energy GmbH",
            jurisdiction="DE",
            entity_type=EntityType.PRIVATE_LIMITED,
            entity_nature="LEGAL_ENTITY",
            incorporation_date=date(2021, 5, 4),
            is_active=True,
            primary_country="DE",
            website="https://heliosenergy.example.de",
            sic_code="4911",
            sic_description="Electric Energy Storage and Renewable Infrastructure",
            aliases=["Helios Clean Energy", "Helios Storage"],
            legal_identifiers=[
                LegalIdentifier(scheme="LEI", identifier="549300HELIOSGRID0004", jurisdiction="DE", is_primary=True),
            ],
            metadata={
                "is_synthetic": True,
                "demo_profile": "Early-to-Growth Stage CleanTech Venture",
                "notes": "Multi-round venture capital backed energy storage innovator. Ideal for Venture Capital method.",
            },
        ),

        # 5. European Logistics & Omnichannel Retail (France / European Union)
        CompanyEntity(
            entity_id="DEMO-MERIDIAN",
            legal_name="Meridian Retail Logistics SE",
            jurisdiction="FR",
            entity_type=EntityType.PRIVATE_LIMITED,
            entity_nature="LEGAL_ENTITY",
            incorporation_date=date(2015, 11, 19),
            is_active=True,
            primary_country="FR",
            website="https://meridianlogistics.example.fr",
            sic_code="4213",
            sic_description="Trucking and Multi-Modal Freight Logistics",
            aliases=["Meridian Logistics", "Meridian SE"],
            legal_identifiers=[
                LegalIdentifier(scheme="SIREN", identifier="801234567", jurisdiction="FR", is_primary=True),
                LegalIdentifier(scheme="LEI", identifier="969500MERIDIAN0005", jurisdiction="FR", is_primary=False),
            ],
            metadata={
                "is_synthetic": True,
                "demo_profile": "Epistemic Transparency Showcase Company",
                "notes": "Demonstrates missing cash-flow disclosures and explainable screening penalties.",
            },
        ),
    ]
    return entities


def seed_demo_database(storage: SQLiteStorage) -> None:
    """Populate storage with a cohesive, verified, institutional synthetic dataset."""
    logger.info("Initializing OpenDeal Institutional Demo Dataset...")

    # Guard: Avoid re-seeding if already present
    existing_nexus = storage.get_entity("DEMO-NEXUS")
    if existing_nexus is not None:
        logger.info("Demo dataset already initialized. Skipping re-seed.")
        return

    # 1. Insert Entities
    entities = build_demo_entities()
    for ent in entities:
        storage.save_entity(ent)

    # 2. Insert Simulated Sources & Evidence
    # Simulated 10-K for Nexus Cloud
    source_nexus_10k = SourceReference(
        source_id="src_demo_nexus_10k_2024",
        source_type=SourceType.AUDITED_REPORT,
        source_url="https://sec.gov/edgar/mock/nexus_2024_10k.htm",
        publisher="OpenDeal Synthetic Repository",
        document_id="SIMULATED-EDGAR-NEXU-2024-10K",
        publication_date=date(2025, 2, 10),
        metadata={"notice": DEMO_DISCLAIMER, "filing_form": "10-K"},
    )
    storage.save_source(source_nexus_10k)

    ev_nexus_rev_text = (
        "Nexus Cloud Technologies Inc. Annual Report 2024: Consolidated Revenue totaled "
        "$152,500,000 for the fiscal year ended December 31, 2024, representing an increase "
        "of 27.1% compared to $120,000,000 for fiscal 2023. Gross profit was $118,950,000 (78.0% margin). "
        "EBITDA reached $33,550,000 (22.0% margin). Cash and cash equivalents totaled $45,000,000. "
        "Total debt outstanding was $10,000,000. Shares of common stock outstanding were 25,000,000. "
        f"{DEMO_DISCLAIMER}"
    )
    ev_nexus_rev = EvidenceRecord(
        evidence_id="ev_demo_nexus_rev_2024",
        source_id=source_nexus_10k.source_id,
        raw_content=ev_nexus_rev_text,
        content_hash=compute_sha256(ev_nexus_rev_text),
        extraction_method="SYNTHETIC_MOCK_EXTRACTOR",
        document_name="Nexus Cloud FY2024 Simulated 10-K",
        page_number=42,
        section_name="Item 7. Management's Discussion and Analysis",
        confidence_score=1.0,
    )
    storage.save_evidence(ev_nexus_rev)

    # Simulated Accounts for Apex Industrial Dynamics Plc
    source_apex_accounts = SourceReference(
        source_id="src_demo_apex_accounts_2024",
        source_type=SourceType.OFFICIAL_REGISTRY,
        source_url="https://find-and-update.company-information.service.gov.uk/company/09876543",
        publisher="OpenDeal Synthetic Repository",
        document_id="SIMULATED-CH-APEX-2024-ACCOUNTS",
        publication_date=date(2025, 1, 25),
        metadata={"notice": DEMO_DISCLAIMER, "filing_type": "Full Accounts"},
    )
    storage.save_source(source_apex_accounts)

    ev_apex_text = (
        "Apex Industrial Dynamics Plc: Annual Accounts for the year ended 31 December 2024. "
        "Group turnover was £145,000,000 (equivalent to approx $184,000,000 USD). "
        "Operating profit (EBITDA) was £30,000,000 ($38,100,000 USD). "
        "Senior credit facility debt outstanding stood at £35,000,000 ($44,450,000 USD). "
        "Cash balances were £8,500,000 ($10,795,000 USD). Free cash flow was £22,000,000. "
        f"{DEMO_DISCLAIMER}"
    )
    ev_apex = EvidenceRecord(
        evidence_id="ev_demo_apex_accounts_2024",
        source_id=source_apex_accounts.source_id,
        raw_content=ev_apex_text,
        content_hash=compute_sha256(ev_apex_text),
        extraction_method="SYNTHETIC_MOCK_EXTRACTOR",
        document_name="Apex Industrial FY2024 Full Accounts",
        page_number=18,
        section_name="Consolidated Income Statement",
        confidence_score=1.0,
    )
    storage.save_evidence(ev_apex)

    # 3. Financial Facts (Demonstrating 3-Year Time Series and Epistemic Integrity)
    # Nexus Cloud Facts (2022, 2023, 2024)
    nexus_facts = [
        # 2022
        ("revenue", 95000000.0, 2022, FiscalPeriodType.ANNUAL, FactState.STANDARDIZED_FACT),
        ("gross_profit", 72200000.0, 2022, FiscalPeriodType.ANNUAL, FactState.STANDARDIZED_FACT),
        ("ebitda", 18050000.0, 2022, FiscalPeriodType.ANNUAL, FactState.STANDARDIZED_FACT),
        ("net_income", 10450000.0, 2022, FiscalPeriodType.ANNUAL, FactState.STANDARDIZED_FACT),
        ("free_cash_flow", 15200000.0, 2022, FiscalPeriodType.ANNUAL, FactState.STANDARDIZED_FACT),
        # 2023
        ("revenue", 120000000.0, 2023, FiscalPeriodType.ANNUAL, FactState.STANDARDIZED_FACT),
        ("gross_profit", 92400000.0, 2023, FiscalPeriodType.ANNUAL, FactState.STANDARDIZED_FACT),
        ("ebitda", 25200000.0, 2023, FiscalPeriodType.ANNUAL, FactState.STANDARDIZED_FACT),
        ("net_income", 15600000.0, 2023, FiscalPeriodType.ANNUAL, FactState.STANDARDIZED_FACT),
        ("free_cash_flow", 22100000.0, 2023, FiscalPeriodType.ANNUAL, FactState.STANDARDIZED_FACT),
        # 2024 (Corroborated by ev_demo_nexus_rev_2024)
        ("revenue", 152500000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("gross_profit", 118950000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("ebitda", 33550000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("operating_income", 28200000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("net_income", 22875000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("free_cash_flow", 31200000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("cash_and_equivalents", 45000000.0, 2024, FiscalPeriodType.INSTANT, FactState.VALIDATED_FACT),
        ("total_debt", 10000000.0, 2024, FiscalPeriodType.INSTANT, FactState.VALIDATED_FACT),
        ("shares_outstanding", 25000000.0, 2024, FiscalPeriodType.INSTANT, FactState.VALIDATED_FACT),
    ]

    for var, val, year, period, state in nexus_facts:
        fid = f"fact_nexus_{var}_{year}"
        evidence_list = [ev_nexus_rev.evidence_id] if year == 2024 else []
        fact = FactRecord(
            fact_id=fid,
            entity_id="DEMO-NEXUS",
            canonical_variable=var,
            fact_state=state,
            reported_value=f"{val:,.2f}",
            normalized_value=val,
            currency=Currency.USD,
            unit_scale=UnitScale.EXACT,
            scale=0,
            sign=Sign.POSITIVE,
            fiscal_year=year,
            fiscal_period=period,
            period_start=date(year, 1, 1) if period != FiscalPeriodType.INSTANT else None,
            period_end=date(year, 12, 31),
            is_instant=(period == FiscalPeriodType.INSTANT),
            accounting_standard=AccountingStandard.US_GAAP,
            validation_status=ValidationStatus.VALID,
            missing_reason=MissingDataReason.NOT_APPLICABLE,
            evidence_ids=evidence_list,
            metadata={"source_notice": DEMO_DISCLAIMER},
        )
        storage.save_fact(fact)

    # Apex Industrial Dynamics Facts (2024 in USD)
    apex_facts = [
        ("revenue", 184000000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("gross_profit", 73600000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("ebitda", 38100000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("operating_income", 30500000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("net_income", 21200000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("free_cash_flow", 27940000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("cash_and_equivalents", 10795000.0, 2024, FiscalPeriodType.INSTANT, FactState.VALIDATED_FACT),
        ("total_debt", 44450000.0, 2024, FiscalPeriodType.INSTANT, FactState.VALIDATED_FACT),
    ]
    for var, val, year, period, state in apex_facts:
        fid = f"fact_apex_{var}_{year}"
        fact = FactRecord(
            fact_id=fid,
            entity_id="DEMO-APEX",
            canonical_variable=var,
            fact_state=state,
            reported_value=f"{val:,.2f}",
            normalized_value=val,
            currency=Currency.USD,
            unit_scale=UnitScale.EXACT,
            scale=0,
            sign=Sign.POSITIVE,
            fiscal_year=year,
            fiscal_period=period,
            period_start=date(year, 1, 1) if period != FiscalPeriodType.INSTANT else None,
            period_end=date(year, 12, 31),
            is_instant=(period == FiscalPeriodType.INSTANT),
            accounting_standard=AccountingStandard.UK_GAAP,
            validation_status=ValidationStatus.VALID,
            missing_reason=MissingDataReason.NOT_APPLICABLE,
            evidence_ids=[ev_apex.evidence_id],
            metadata={"source_notice": DEMO_DISCLAIMER},
        )
        storage.save_fact(fact)

    # Vitals Biopharma Facts (2024)
    vita_facts = [
        ("revenue", 12000000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("gross_profit", 10500000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("ebitda", -18500000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("net_income", -19200000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("cash_and_equivalents", 210000000.0, 2024, FiscalPeriodType.INSTANT, FactState.VALIDATED_FACT),
        ("total_debt", 0.0, 2024, FiscalPeriodType.INSTANT, FactState.VALIDATED_FACT),
        ("shares_outstanding", 40000000.0, 2024, FiscalPeriodType.INSTANT, FactState.VALIDATED_FACT),
    ]
    for var, val, year, period, state in vita_facts:
        fid = f"fact_vita_{var}_{year}"
        fact = FactRecord(
            fact_id=fid,
            entity_id="DEMO-VITA",
            canonical_variable=var,
            fact_state=state,
            reported_value=f"{val:,.2f}",
            normalized_value=val,
            currency=Currency.USD,
            unit_scale=UnitScale.EXACT,
            scale=0,
            sign=Sign.POSITIVE if val > 0 else (Sign.NEGATIVE if val < 0 else Sign.ZERO),
            fiscal_year=year,
            fiscal_period=period,
            period_start=date(year, 1, 1) if period != FiscalPeriodType.INSTANT else None,
            period_end=date(year, 12, 31),
            is_instant=(period == FiscalPeriodType.INSTANT),
            accounting_standard=AccountingStandard.US_GAAP,
            validation_status=ValidationStatus.VALID,
            missing_reason=MissingDataReason.NOT_APPLICABLE,
            metadata={"source_notice": DEMO_DISCLAIMER},
        )
        storage.save_fact(fact)

    # Helios Clean Energy Facts (2024)
    helios_facts = [
        ("revenue", 28500000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("gross_profit", 14250000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("ebitda", 4850000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT),
        ("cash_and_equivalents", 32000000.0, 2024, FiscalPeriodType.INSTANT, FactState.VALIDATED_FACT),
        ("total_debt", 5000000.0, 2024, FiscalPeriodType.INSTANT, FactState.VALIDATED_FACT),
    ]
    for var, val, year, period, state in helios_facts:
        fid = f"fact_helios_{var}_{year}"
        fact = FactRecord(
            fact_id=fid,
            entity_id="DEMO-HELIOS",
            canonical_variable=var,
            fact_state=state,
            reported_value=f"{val:,.2f}",
            normalized_value=val,
            currency=Currency.EUR,
            unit_scale=UnitScale.EXACT,
            scale=0,
            sign=Sign.POSITIVE,
            fiscal_year=year,
            fiscal_period=period,
            period_start=date(year, 1, 1) if period != FiscalPeriodType.INSTANT else None,
            period_end=date(year, 12, 31),
            is_instant=(period == FiscalPeriodType.INSTANT),
            accounting_standard=AccountingStandard.IFRS,
            validation_status=ValidationStatus.VALID,
            missing_reason=MissingDataReason.NOT_APPLICABLE,
            metadata={"source_notice": DEMO_DISCLAIMER},
        )
        storage.save_fact(fact)

    # Meridian Retail Logistics Facts (With Explicit Missing FCF to Showcase Gaps)
    meridian_facts = [
        ("revenue", 82000000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT, False),
        ("gross_profit", 20500000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT, False),
        ("ebitda", 9840000.0, 2024, FiscalPeriodType.ANNUAL, FactState.VALIDATED_FACT, False),
        ("cash_and_equivalents", 4200000.0, 2024, FiscalPeriodType.INSTANT, FactState.VALIDATED_FACT, False),
        ("total_debt", 28000000.0, 2024, FiscalPeriodType.INSTANT, FactState.VALIDATED_FACT, False),
        # Deliberately missing Free Cash Flow
        ("free_cash_flow", None, 2024, FiscalPeriodType.ANNUAL, FactState.MISSING_FACT, True),
    ]
    for var, val, year, period, state, is_missing in meridian_facts:
        fid = f"fact_meridian_{var}_{year}"
        fact = FactRecord(
            fact_id=fid,
            entity_id="DEMO-MERIDIAN",
            canonical_variable=var,
            fact_state=state,
            reported_value=f"{val:,.2f}" if val is not None else None,
            normalized_value=val,
            currency=Currency.EUR,
            unit_scale=UnitScale.EXACT,
            scale=0,
            sign=Sign.POSITIVE if (val and val > 0) else Sign.UNSPECIFIED,
            fiscal_year=year,
            fiscal_period=period,
            period_start=date(year, 1, 1) if period != FiscalPeriodType.INSTANT else None,
            period_end=date(year, 12, 31),
            is_instant=(period == FiscalPeriodType.INSTANT),
            accounting_standard=AccountingStandard.IFRS,
            validation_status=ValidationStatus.DATA_NOT_FOUND if is_missing else ValidationStatus.VALID,
            missing_reason=MissingDataReason.NOT_REPORTED if is_missing else MissingDataReason.NOT_APPLICABLE,
            metadata={
                "source_notice": DEMO_DISCLAIMER,
                "epistemic_demonstration": "Intentionally missing metric to verify penalty calculation.",
            },
        )
        storage.save_fact(fact)

    # 4. Deals Intelligence (Funding Rounds, M&A Transactions, and Reconciled Conflicts)
    # Series A for Helios Clean Energy
    round_series_a = FundingRound(
        round_id="rnd_demo_helios_series_a",
        company_id="DEMO-HELIOS",
        round_type=FundingRoundType.SERIES_A,
        announced_date=date(2022, 6, 15),
        closing_date=date(2022, 7, 1),
        disclosed_amount=15000000.0,
        currency=Currency.EUR,
        pre_money_valuation=45000000.0,
        post_money_valuation=60000000.0,
        instrument_type="Preferred Equity",
        lead_investor_ids=["DEMO-NEXUS"],
        participant_ids=["DEMO-NEXUS"],
        verification_status=DealVerificationStatus.VERIFIED,
        metadata={"notice": DEMO_DISCLAIMER},
    )
    storage.save_funding_round(round_series_a)

    # Series B for Helios Clean Energy
    round_series_b = FundingRound(
        round_id="rnd_demo_helios_series_b",
        company_id="DEMO-HELIOS",
        round_type=FundingRoundType.SERIES_B,
        announced_date=date(2024, 3, 20),
        closing_date=date(2024, 4, 15),
        disclosed_amount=35000000.0,
        currency=Currency.EUR,
        pre_money_valuation=125000000.0,
        post_money_valuation=160000000.0,
        instrument_type="Series B Senior Preferred",
        lead_investor_ids=["DEMO-APEX"],
        participant_ids=["DEMO-APEX", "DEMO-NEXUS"],
        verification_status=DealVerificationStatus.VERIFIED,
        metadata={"notice": DEMO_DISCLAIMER},
    )
    storage.save_funding_round(round_series_b)

    # Strategic M&A Transaction: Nexus Cloud acquires AI startup
    tx_nexus_mna = TransactionEvent(
        transaction_id="tx_demo_nexus_acquire_ai",
        transaction_type=TransactionType.ACQUISITION,
        acquirer_id="DEMO-NEXUS",
        acquirer_name="Nexus Cloud Technologies Inc.",
        target_id="DEMO-HELIOS",
        target_name="Helios Intelligent Systems Unit",
        announced_date=date(2024, 9, 12),
        expected_completion_date=date(2024, 11, 30),
        actual_completion_date=date(2024, 11, 28),
        status=TransactionStatus.COMPLETED,
        disclosed_value=45000000.0,
        currency=Currency.USD,
        consideration_type="MIXED",
        consideration_details={
            "cash_percentage": 65.0,
            "stock_percentage": 35.0,
            "earn_out_milestones": "USD 5.0M on target ARR attainment",
        },
        verification_status=DealVerificationStatus.VERIFIED,
        metadata={"notice": DEMO_DISCLAIMER},
    )
    storage.save_transaction(tx_nexus_mna)

    # Recorded Deal Conflict (Demonstrates OpenDeal's conflict preservation)
    conflict = DealConflict(
        conflict_id="conf_demo_helios_amount",
        target_event_id="rnd_demo_helios_series_a",
        event_category="FUNDING_ROUND",
        field_name="disclosed_amount",
        source_a_id="src_demo_press_release_1",
        observed_value_a="EUR 15,000,000",
        source_b_id="src_demo_blog_post_2",
        observed_value_b="EUR 18,000,000",
        resolution_status="UNRESOLVED",
        resolution_notes="Discrepancy between company official press release and secondary market blog. Primary company release (EUR 15M) preserved as authoritative.",
    )
    storage.save_deal_conflict(conflict)

    # 5. Pre-Generated Evidence-Grounded Research Brief
    research_brief_dict = {
        "research_id": "rb_demo_nexus_executive",
        "entity_id": "DEMO-NEXUS",
        "brief_title": "Nexus Cloud Technologies: FY2024 Executive Dossier & Valuation Context",
        "executive_summary": (
            "Nexus Cloud Technologies Inc. (NASDAQ: NEXU) demonstrates accelerating financial momentum "
            "with FY2024 revenue reaching $152.5M (+27.1% YoY) and expanding EBITDA margins of 22.0% ($33.6M). "
            "The capital structure is conservative, holding $45.0M in cash against $10.0M total debt. "
            "Financial statements are audited under US-GAAP and exhibit 100% mathematical consistency. "
            f"{DEMO_DISCLAIMER}"
        ),
        "status": "COMPLETED",
        "claims": [
            {
                "claim_id": "clm_1",
                "statement": "FY2024 revenue expanded by 27.1% to $152.5M from $120.0M in FY2023.",
                "knowledge_tier": "SOURCE_REPORTED_FACT",
                "verification_status": "VERIFIED",
                "cited_evidence_ids": [ev_nexus_rev.evidence_id],
                "confidence_score": 1.0,
            },
            {
                "claim_id": "clm_2",
                "statement": "Gross margin increased to 78.0% ($118.95M gross profit on $152.5M revenue).",
                "knowledge_tier": "DERIVED_CALCULATION",
                "verification_status": "VERIFIED",
                "cited_evidence_ids": [ev_nexus_rev.evidence_id],
                "confidence_score": 1.0,
            },
            {
                "claim_id": "clm_3",
                "statement": "Projected terminal cash flow growth rate estimated at 3.0% under stable cloud expansion.",
                "knowledge_tier": "ANALYST_ASSUMPTION",
                "verification_status": "UNVERIFIED",
                "cited_evidence_ids": [],
                "confidence_score": 0.85,
            },
        ],
        "financial_trends": [
            {"metric": "Revenue", "trend": "EXPANDING", "cagr": "+26.8% (2022-2024)"},
            {"metric": "EBITDA Margin", "trend": "EXPANDING", "cagr": "+19.0% -> +22.0%"},
        ],
        "data_gaps": [],
        "risk_indicators": [
            {"category": "Valuation Multiple", "severity": "MEDIUM", "description": "High revenue growth implies sensitivity to terminal discount rate changes."}
        ],
        "cited_evidence_ids": [ev_nexus_rev.evidence_id],
        "model_provider": "DeterministicMockProvider",
        "model_id": "deterministic-v1",
        "created_at": datetime.now(timezone.utc),
    }
    storage.save_research_brief(research_brief_dict)

    # 6. Target Screening Run & Watchlist (Executed with real deterministic Discovery Service)
    from opendeal.discovery.service import TargetDiscoveryService
    criteria = TargetCriteria(
        jurisdictions=["US", "US-DE", "GB", "DE", "FR"],
        min_revenue=20000000.0,
        max_revenue=250000000.0,
        max_candidates=10,
        missing_data_penalty=0.20,
    )
    discovery_service = TargetDiscoveryService(storage=storage)
    screening_res = discovery_service.screen_targets(criteria, is_live=False, save_run=True)

    # Saved Watchlist
    from opendeal.discovery.watchlists import TargetListManager
    list_mgr = TargetListManager(storage)
    target_list = list_mgr.create_list(
        name="Tier 1 High-Conviction Tech Candidates",
        description="Priority acquisition candidates identified from Q4 screening run.",
    )
    list_mgr.add_member(
        list_id=target_list.list_id,
        entity_id="DEMO-NEXUS",
        notes="Top ranked candidate with 90+ score and complete audited disclosures.",
        source_run_id=screening_res.metadata.run_id,
    )
    list_mgr.add_member(
        list_id=target_list.list_id,
        entity_id="DEMO-APEX",
        notes="Strong cash flow generator candidate for LBO evaluation.",
        source_run_id=screening_res.metadata.run_id,
    )

    # 7. Audited Governance & Workflow Run History
    # 7. Audited Governance & Workflow Run History
    # Insert pre-executed workflow run showing DAG completion
    storage.save_workflow_run({
        "run_id": "wfrun_demo_dossier_001",
        "workflow_id": "wf_company_dossier",
        "version": "1.0.0",
        "initiator": "demo_operator",
        "state": "SUCCEEDED",
        "inputs": {"entity_id": "DEMO-NEXUS", "model_type": "dcf"},
        "outputs": {
            "entity_id": "DEMO-NEXUS",
            "status": "Dossier complete",
            "dcf_summary": {"ev": 450000000.0, "equity_value": 485000000.0},
        },
        "error_message": None,
        "idempotency_key": "idemp_demo_dossier_001",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })

    # Completed approval record
    storage.save_approval_request({
        "approval_id": "appr_demo_nexus_export",
        "run_id": "wfrun_demo_dossier_001",
        "action_type": "EXPORT_VALUATION_MEMO",
        "action_payload": {"memo_type": "INVESTMENT_COMMITTEE_NOTE", "entity": "DEMO-NEXUS"},
        "scope": "GLOBAL",
        "status": "APPROVED",
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "decided_by": "managing_director_review",
        "decision_rationale": "Approved following verified SHA-256 evidence reconciliation.",
    })

    logger.info("OpenDeal Institutional Demo Dataset initialized successfully.")
