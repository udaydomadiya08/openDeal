"""Evidence and provenance contracts.

All evidence records are strictly immutable (frozen) to guarantee an unbreakable audit trail.
"""

import hashlib
import uuid
from datetime import date, datetime, timezone
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field, ConfigDict

from opendeal.core.types import SourceType


def compute_sha256(content: str) -> str:
    """Compute standard SHA-256 hex digest for a string payload."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class SourceReference(BaseModel):
    """Reference to an authoritative external primary publication or registry."""
    source_id: str = Field(
        default_factory=lambda: f"src_{uuid.uuid4().hex[:12]}",
        description="Unique identifier for this specific source document reference",
    )
    source_type: SourceType = Field(
        description="Category of the issuing authority or registry",
    )
    source_url: str = Field(
        description="Full URL or authoritative URI where the publication was obtained",
    )
    publisher: str = Field(
        description="Official authority or entity publishing the data (e.g. 'SEC EDGAR', 'Companies House')",
    )
    document_id: Optional[str] = Field(
        default=None,
        description="Unique document identifier (e.g. SEC Accession Number, Companies House filing ID)",
    )
    publication_date: Optional[date] = Field(
        default=None,
        description="Official publication or filing date if disclosed",
    )
    retrieved_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Exact timestamp when the document was retrieved in UTC",
    )
    content_hash: Optional[str] = Field(
        default=None,
        description="SHA-256 cryptographic digest of the complete raw document payload",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific headers, filing types, or query metadata",
    )


class EvidenceRecord(BaseModel):
    """An immutable atomic piece of verifiable evidence extracted from a source document.
    
    Guaranteed immutable (frozen) to prevent post-ingestion tampering.
    """
    model_config = ConfigDict(frozen=True)

    evidence_id: str = Field(
        default_factory=lambda: f"ev_{uuid.uuid4().hex[:12]}",
        description="Unique immutable evidence identifier",
    )
    source_id: str = Field(
        description="Identifier of the parent SourceReference",
    )
    raw_content: str = Field(
        description="Verbatim text excerpt, raw XBRL fact value, or JSON payload snippet",
    )
    content_hash: str = Field(
        description="SHA-256 hex digest of the raw content string",
    )
    extraction_method: str = Field(
        description="Extraction mechanism (e.g. 'XBRL_TAG', 'PDF_PARSER', 'REGISTRY_API')",
    )
    document_name: Optional[str] = Field(
        default=None,
        description="Original document filename or title (e.g. 'aapl-20230930.htm')",
    )
    page_number: Optional[int] = Field(
        default=None,
        description="1-based page number where the fact was located (for PDFs/reports)",
    )
    line_number: Optional[int] = Field(
        default=None,
        description="Line number if applicable",
    )
    section_name: Optional[str] = Field(
        default=None,
        description="Section heading (e.g. 'Consolidated Statements of Operations')",
    )
    tag_name: Optional[str] = Field(
        default=None,
        description="Formal ontology or XBRL tag name (e.g. 'us-gaap:Revenues')",
    )
    confidence_score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Extraction precision score (1.0 for exact structured API/XBRL values)",
    )
    ingested_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Ingestion timestamp in UTC",
    )

    @property
    def verbatim_extract(self) -> str:
        """Alias for raw_content for presentation compatibility."""
        return self.raw_content

    @property
    def locator(self) -> str:
        """Human-readable location identifier."""
        parts = []
        if self.section_name:
            parts.append(self.section_name)
        if self.page_number:
            parts.append(f"p. {self.page_number}")
        if self.line_number:
            parts.append(f"line {self.line_number}")
        return ", ".join(parts) if parts else (self.document_name or "Document Reference")


    @classmethod
    def create(
        cls,
        source_id: str,
        raw_content: str,
        extraction_method: str,
        document_name: Optional[str] = None,
        page_number: Optional[int] = None,
        line_number: Optional[int] = None,
        section_name: Optional[str] = None,
        tag_name: Optional[str] = None,
        confidence_score: float = 1.0,
    ) -> "EvidenceRecord":
        """Factory method that automatically computes content_hash from raw_content."""
        return cls(
            source_id=source_id,
            raw_content=raw_content,
            content_hash=compute_sha256(raw_content),
            extraction_method=extraction_method,
            document_name=document_name,
            page_number=page_number,
            line_number=line_number,
            section_name=section_name,
            tag_name=tag_name,
            confidence_score=confidence_score,
            ingested_at=datetime.now(timezone.utc),
        )
