"""Unit tests for SourceReference and EvidenceRecord contracts."""

import hashlib
import pytest
from datetime import date
from pydantic import ValidationError

from opendeal.core.types import SourceType
from opendeal.evidence.models import SourceReference, EvidenceRecord, compute_sha256


class TestEvidenceModels:
    """Test EvidenceRecord and SourceReference immutability, hashing, and provenance."""

    def test_source_reference_creation(self):
        """SourceReference correctly captures primary document properties."""
        source = SourceReference(
            source_type=SourceType.REGULATORY_FILING,
            source_url="https://data.sec.gov/submissions/CIK0000320193.json",
            publisher="US Securities and Exchange Commission",
            document_id="0000320193-23-000106",
            publication_date=date(2023, 10, 27),
            content_hash="abc123sha256fakehash",
            metadata={"form": "10-K"},
        )
        assert source.source_id.startswith("src_")
        assert source.source_type == SourceType.REGULATORY_FILING
        assert source.document_id == "0000320193-23-000106"
        assert source.publication_date == date(2023, 10, 27)

    def test_evidence_record_factory_and_sha256_hash(self):
        """EvidenceRecord.create automatically computes SHA-256 digest of raw content."""
        raw_text = '{"val": 383285000000, "fy": 2023, "tag": "Revenues"}'
        expected_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

        evidence = EvidenceRecord.create(
            source_id="src_test123",
            raw_content=raw_text,
            extraction_method="XBRL_TAG",
            document_name="10-K",
            page_number=45,
            tag_name="us-gaap:Revenues",
            confidence_score=0.99,
        )

        assert evidence.evidence_id.startswith("ev_")
        assert evidence.source_id == "src_test123"
        assert evidence.raw_content == raw_text
        assert evidence.content_hash == expected_hash
        assert evidence.page_number == 45
        assert evidence.tag_name == "us-gaap:Revenues"
        assert evidence.confidence_score == 0.99

    def test_evidence_record_strictly_immutable(self):
        """EvidenceRecord is frozen; attempting to mutate any attribute raises ValidationError or TypeError."""
        evidence = EvidenceRecord.create(
            source_id="src_immutability",
            raw_content="Original Audited Statement",
            extraction_method="PDF_PARSER",
        )

        with pytest.raises(ValidationError):
            evidence.raw_content = "Tampered Fact"  # type: ignore

        with pytest.raises(ValidationError):
            evidence.confidence_score = 0.50  # type: ignore

        with pytest.raises(ValidationError):
            evidence.page_number = 999  # type: ignore

    def test_compute_sha256_deterministic(self):
        """compute_sha256 produces exact deterministic hex digest."""
        assert compute_sha256("hello world") == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
