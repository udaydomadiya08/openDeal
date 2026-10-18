"""Connector-neutral ingestion workflow orchestrating retrieval, provenance, validation, and storage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from opendeal.config.paths import resolve_database_path
from opendeal.config.settings import settings
from opendeal.connectors.companies_house.client import CompaniesHouseClient
from opendeal.connectors.companies_house.parser import CompaniesHouseParser
from opendeal.connectors.sec.client import SECClient
from opendeal.connectors.sec.parser import SECParser
from opendeal.core.types import SourceType, ValidationStatus
from opendeal.entities.models import CompanyEntity
from opendeal.evidence.models import EvidenceRecord, SourceReference
from opendeal.facts.models import FactRecord
from opendeal.pipeline.stages import FactPipeline, FactStandardizer, FactValidator
from opendeal.storage.base import StorageInterface
from opendeal.storage.sqlite import SQLiteStorage

FIXTURES_DIR = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures"


class IngestionWorkflowResult(BaseModel):
    """Structured result of an ingestion orchestration execution."""
    success: bool
    entity: Optional[CompanyEntity] = None
    facts_count: int = 0
    filings_count: int = 0
    evidence_records_count: int = 0
    validation_summary: Dict[str, int] = Field(default_factory=dict)
    is_live: bool = False
    source_url: str = ""
    warnings: List[str] = Field(default_factory=list)
    error_message: Optional[str] = None


class IngestionOrchestrator:
    """Coordinates retrieval, evidence logging, fact extraction, validation, and storage.

    Maintains source-specific parsing within connectors while exposing unified workflows.
    """

    def __init__(self, storage: Optional[StorageInterface] = None) -> None:
        self.storage = storage or SQLiteStorage(resolve_database_path())
        self.storage.initialize()

    def ingest_sec(
        self,
        ticker_or_cik: str,
        live: bool = False,
        client: Optional[SECClient] = None,
    ) -> IngestionWorkflowResult:
        """Run SEC ingestion workflow for a US public company."""
        warnings: List[str] = []
        try:
            if live:
                sec_client = client or SECClient()
                # Resolve CIK if ticker provided
                if not ticker_or_cik.isdigit():
                    # For live ticker resolution, get submissions or facts
                    cik = ticker_or_cik  # SEC client supports CIK/tickers via its API
                else:
                    cik = ticker_or_cik

                sub_data, sub_url, sub_hash = sec_client.get_submissions(cik)
                facts_data, facts_url, facts_hash = sec_client.get_company_facts(cik)
            else:
                # Offline deterministic fixture mode
                sub_path = FIXTURES_DIR / "sec" / "apple_submissions.json"
                facts_path = FIXTURES_DIR / "sec" / "apple_companyfacts_sample.json"
                if not sub_path.exists() or not facts_path.exists():
                    return IngestionWorkflowResult(
                        success=False,
                        error_message="Offline SEC fixtures not found in tests/fixtures/sec/",
                        is_live=False,
                    )
                with open(sub_path, "r", encoding="utf-8") as f:
                    sub_data = json.load(f)
                with open(facts_path, "r", encoding="utf-8") as f:
                    facts_data = json.load(f)

                sub_url = f"https://data.sec.gov/submissions/CIK{str(sub_data.get('cik', '0000320193')).zfill(10)}.json"
                sub_hash = "mock_sec_submissions_sha256"
                facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{str(sub_data.get('cik', '0000320193')).zfill(10)}.json"
                facts_hash = "mock_sec_facts_sha256"

            # 1. Parse entity
            entity = SECParser.parse_submissions_to_entity(sub_data)

            # 2. Ingest via FactPipeline (handles Evidence, Facts, Standardization, Validation, Storage)
            pipeline = FactPipeline(storage=self.storage)
            metrics = pipeline.ingest_sec_facts(
                facts_data=facts_data,
                entity=entity,
                source_url=facts_url,
                payload_hash=facts_hash,
            )

            validation_summary = {
                "VALID": metrics.get("validated_count", 0),
                "WARNING": metrics.get("warning_count", 0),
                "INVALID": metrics.get("invalid_count", 0),
            }

            return IngestionWorkflowResult(
                success=True,
                entity=entity,
                facts_count=metrics.get("total_facts", 0),
                evidence_records_count=metrics.get("evidence_count", 0),
                validation_summary=validation_summary,
                is_live=live,
                source_url=facts_url,
                warnings=warnings,
            )

        except Exception as e:
            return IngestionWorkflowResult(
                success=False,
                error_message=f"SEC ingestion workflow failed: {str(e)}",
                is_live=live,
                warnings=warnings,
            )

    def ingest_companies_house(
        self,
        company_number: str,
        live: bool = False,
        client: Optional[CompaniesHouseClient] = None,
    ) -> IngestionWorkflowResult:
        """Run UK Companies House ingestion workflow for a UK company."""
        warnings: List[str] = []
        try:
            if live:
                ch_client = client or CompaniesHouseClient()
                prof_data, prof_url, prof_hash = ch_client.get_company_profile(company_number)
                filing_data, filing_url, _ = ch_client.get_filing_history(company_number)
            else:
                prof_path = FIXTURES_DIR / "companies_house" / "company_profile_sample.json"
                filing_path = FIXTURES_DIR / "companies_house" / "filing_history_sample.json"
                if not prof_path.exists() or not filing_path.exists():
                    return IngestionWorkflowResult(
                        success=False,
                        error_message="Offline Companies House fixtures not found.",
                        is_live=False,
                    )
                with open(prof_path, "r", encoding="utf-8") as f:
                    prof_data = json.load(f)
                with open(filing_path, "r", encoding="utf-8") as f:
                    filing_data = json.load(f)

                prof_url = f"https://api.company-information.service.gov.uk/company/{company_number.zfill(8)}"
                prof_hash = "mock_ch_profile_sha256"

            # 1. Parse entity
            entity = CompaniesHouseParser.parse_company_profile(prof_data)
            self.storage.save_entity(entity)

            # 2. Store Evidence Record
            source_ref = SourceReference(
                source_type=SourceType.OFFICIAL_REGISTRY,
                source_url=prof_url,
                publisher="Companies House (UK Government Executive Agency)",
                content_hash=prof_hash,
            )
            self.storage.save_source(source_ref)

            evidence = EvidenceRecord.create(
                source_id=source_ref.source_id,
                raw_content=json.dumps(prof_data, sort_keys=True),
                extraction_method="COMPANIES_HOUSE_PROFILE_JSON",
            )
            self.storage.save_evidence(evidence)

            # 3. Parse filing history
            filings = CompaniesHouseParser.parse_filing_history(filing_data, entity_id=entity.entity_id)

            if "SMALL_COMPANY" in entity.metadata.get("public_filing_statement_availability", ""):
                warnings.append(
                    "UK Small Company Exemption applies: Full income statements are not publicly available."
                )

            return IngestionWorkflowResult(
                success=True,
                entity=entity,
                facts_count=0,  # Private filing profile
                filings_count=len(filings),
                evidence_records_count=1,
                validation_summary={"PROFILE_VALID": 1},
                is_live=live,
                source_url=prof_url,
                warnings=warnings,
            )

        except Exception as e:
            return IngestionWorkflowResult(
                success=False,
                error_message=f"Companies House ingestion workflow failed: {str(e)}",
                is_live=live,
                warnings=warnings,
            )
