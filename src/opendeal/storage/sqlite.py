"""SQLite implementation of the OpenDeal storage interface."""

import json
import logging
from pathlib import Path
import sqlite3
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
    EntityRelationship,
    LegalIdentifier,
    RelationshipType,
)
from opendeal.evidence.models import EvidenceRecord, SourceReference
from opendeal.facts.models import FactRecord, TransformationStep, ValidationResult
from opendeal.storage.base import StorageInterface
from opendeal.storage.migrations import apply_migrations

logger = logging.getLogger(__name__)


def _date_to_str(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def _datetime_to_str(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _str_to_date(s: Optional[str]) -> Optional[date]:
    return date.fromisoformat(s) if s else None


def _str_to_datetime(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


class SQLiteStorage(StorageInterface):
    """SQLite-backed storage repository with transaction safety and schema versioning."""

    def __init__(self, database_path: str = ":memory:"):
        self.database_path = database_path
        self._conn: Optional[sqlite3.Connection] = None

    def initialize(self) -> None:
        """Establish connection, enable foreign keys, and apply schema migrations."""
        if self._conn is None:
            connect_target = self.database_path
            if self.database_path != ":memory:":
                from opendeal.config.paths import is_default_database_path
                p = Path(self.database_path).expanduser()
                connect_target = str(p)
                db_parent = p.parent
                if is_default_database_path(self.database_path):
                    db_parent.mkdir(parents=True, exist_ok=True)
                else:
                    if not db_parent.exists():
                        raise FileNotFoundError(
                            f"Database parent directory does not exist: {db_parent.resolve()}.\n"
                            f"Action: Create the directory or set OPENDEAL_DATABASE_PATH to an accessible path."
                        )
            try:
                self._conn = sqlite3.connect(
                    connect_target,
                    check_same_thread=False,
                    detect_types=sqlite3.PARSE_DECLTYPES,
                    timeout=30.0,
                )
            except sqlite3.OperationalError as exc:
                raise IOError(
                    f"Unable to open or create SQLite database at '{self.database_path}': {exc}"
                ) from exc
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA busy_timeout = 30000;")
            self._conn.execute("PRAGMA foreign_keys = ON;")
            apply_migrations(self._conn)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.initialize()
        return self._conn  # type: ignore

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Entity Operations ───────────────────────────────────────────────────

    def save_entity(self, entity: CompanyEntity) -> None:
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO companies (
                    entity_id, legal_name, jurisdiction, entity_type, ticker,
                    primary_exchange, incorporation_date, is_active, primary_country,
                    website, sic_code, sic_description, created_at, updated_at,
                    aliases_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id) DO UPDATE SET
                    legal_name=excluded.legal_name,
                    jurisdiction=excluded.jurisdiction,
                    entity_type=excluded.entity_type,
                    ticker=excluded.ticker,
                    primary_exchange=excluded.primary_exchange,
                    incorporation_date=excluded.incorporation_date,
                    is_active=excluded.is_active,
                    primary_country=excluded.primary_country,
                    website=excluded.website,
                    sic_code=excluded.sic_code,
                    sic_description=excluded.sic_description,
                    updated_at=excluded.updated_at,
                    aliases_json=excluded.aliases_json,
                    metadata_json=excluded.metadata_json
                """,
                (
                    entity.entity_id,
                    entity.legal_name,
                    entity.jurisdiction,
                    entity.entity_type.value,
                    entity.ticker,
                    entity.primary_exchange,
                    _date_to_str(entity.incorporation_date),
                    1 if entity.is_active else 0,
                    entity.primary_country,
                    entity.website,
                    entity.sic_code,
                    entity.sic_description,
                    _datetime_to_str(entity.created_at),
                    _datetime_to_str(entity.updated_at),
                    json.dumps(entity.aliases),
                    json.dumps({
                        **entity.metadata,
                        "__former_names": entity.former_names,
                        "__entity_nature": entity.entity_nature,
                    }),
                ),
            )

            # Persist legal identifiers
            for ident in entity.legal_identifiers:
                cursor.execute(
                    """
                    INSERT INTO company_identifiers (entity_id, scheme, identifier, jurisdiction, is_primary)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(scheme, identifier) DO UPDATE SET
                        entity_id=excluded.entity_id,
                        jurisdiction=excluded.jurisdiction,
                        is_primary=excluded.is_primary
                    """,
                    (
                        entity.entity_id,
                        ident.scheme.upper(),
                        ident.identifier,
                        ident.jurisdiction,
                        1 if ident.is_primary else 0,
                    ),
                )

    def get_entity(self, entity_id: str) -> Optional[CompanyEntity]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM companies WHERE entity_id = ?", (entity_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._build_entity_from_row(row)

    def find_entity_by_identifier(self, scheme: str, identifier: str) -> Optional[CompanyEntity]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT c.* FROM companies c
            JOIN company_identifiers i ON c.entity_id = i.entity_id
            WHERE i.scheme = ? AND i.identifier = ?
            """,
            (scheme.upper(), identifier.strip()),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return self._build_entity_from_row(row)

    def find_entity_by_ticker(self, ticker: str) -> Optional[CompanyEntity]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM companies WHERE UPPER(ticker) = ?", (ticker.upper().strip(),))
        row = cursor.fetchone()
        if not row:
            return None
        return self._build_entity_from_row(row)

    def _build_entity_from_row(self, row: sqlite3.Row) -> CompanyEntity:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT scheme, identifier, jurisdiction, is_primary FROM company_identifiers WHERE entity_id = ?",
            (row["entity_id"],),
        )
        identifiers = [
            LegalIdentifier(
                scheme=r["scheme"],
                identifier=r["identifier"],
                jurisdiction=r["jurisdiction"],
                is_primary=bool(r["is_primary"]),
            )
            for r in cursor.fetchall()
        ]

        meta = json.loads(row["metadata_json"])
        former_names = meta.pop("__former_names", [])
        entity_nature = meta.pop("__entity_nature", "LEGAL_ENTITY")

        return CompanyEntity(
            entity_id=row["entity_id"],
            legal_name=row["legal_name"],
            jurisdiction=row["jurisdiction"],
            entity_type=EntityType(row["entity_type"]),
            entity_nature=entity_nature,
            legal_identifiers=identifiers,
            aliases=json.loads(row["aliases_json"]),
            former_names=former_names,
            ticker=row["ticker"],
            primary_exchange=row["primary_exchange"],
            incorporation_date=_str_to_date(row["incorporation_date"]),
            is_active=bool(row["is_active"]),
            primary_country=row["primary_country"],
            website=row["website"],
            sic_code=row["sic_code"],
            sic_description=row["sic_description"],
            created_at=_str_to_datetime(row["created_at"]),
            updated_at=_str_to_datetime(row["updated_at"]),
            metadata=meta,
        )

    def list_entities(self, limit: int = 100) -> List[CompanyEntity]:
        """List company entities up to the specified limit."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM companies ORDER BY legal_name ASC LIMIT ?", (limit,))
        return [self._build_entity_from_row(row) for row in cursor.fetchall()]

    def get_legal_identifiers(self, entity_id: str) -> List[LegalIdentifier]:
        """Retrieve all legal identifiers for a company entity."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT scheme, identifier, jurisdiction, is_primary FROM company_identifiers WHERE entity_id = ?",
            (entity_id,),
        )
        return [
            LegalIdentifier(
                scheme=r["scheme"],
                identifier=r["identifier"],
                jurisdiction=r["jurisdiction"],
                is_primary=bool(r["is_primary"]),
            )
            for r in cursor.fetchall()
        ]

    def save_relationship(self, relationship: EntityRelationship) -> None:
        """Persist an evidence-backed corporate relationship."""
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO entity_relationships (
                    relationship_id, source_entity_id, target_entity_id,
                    relationship_type, ownership_percentage, effective_date,
                    end_date, evidence_ids_json, source_reference, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relationship_id) DO UPDATE SET
                    relationship_type=excluded.relationship_type,
                    ownership_percentage=excluded.ownership_percentage,
                    effective_date=excluded.effective_date,
                    end_date=excluded.end_date,
                    evidence_ids_json=excluded.evidence_ids_json,
                    source_reference=excluded.source_reference,
                    notes=excluded.notes
                """,
                (
                    relationship.relationship_id,
                    relationship.source_entity_id,
                    relationship.target_entity_id,
                    relationship.relationship_type.value,
                    relationship.ownership_percentage,
                    _date_to_str(relationship.effective_date),
                    _date_to_str(relationship.end_date),
                    json.dumps(relationship.evidence_ids),
                    relationship.source_reference,
                    relationship.notes,
                    _datetime_to_str(relationship.created_at),
                ),
            )

    def get_relationships(
        self, entity_id: str, relationship_type: Optional[RelationshipType] = None
    ) -> List[EntityRelationship]:
        """Retrieve all relationships where entity_id is either source or target."""
        cursor = self.conn.cursor()
        if relationship_type:
            cursor.execute(
                """
                SELECT * FROM entity_relationships
                WHERE (source_entity_id = ? OR target_entity_id = ?)
                  AND relationship_type = ?
                ORDER BY created_at DESC
                """,
                (entity_id, entity_id, relationship_type.value),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM entity_relationships
                WHERE source_entity_id = ? OR target_entity_id = ?
                ORDER BY created_at DESC
                """,
                (entity_id, entity_id),
            )

        relationships = []
        for row in cursor.fetchall():
            relationships.append(
                EntityRelationship(
                    relationship_id=row["relationship_id"],
                    source_entity_id=row["source_entity_id"],
                    target_entity_id=row["target_entity_id"],
                    relationship_type=RelationshipType(row["relationship_type"]),
                    ownership_percentage=row["ownership_percentage"],
                    effective_date=_str_to_date(row["effective_date"]),
                    end_date=_str_to_date(row["end_date"]),
                    evidence_ids=json.loads(row["evidence_ids_json"]),
                    source_reference=row["source_reference"],
                    notes=row["notes"],
                    created_at=_str_to_datetime(row["created_at"]),
                )
            )
        return relationships

    get_relationships_for_entity = get_relationships

    # ── Source & Evidence Operations ────────────────────────────────────────

    def save_source(self, source: SourceReference) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO sources (
                    source_id, source_type, source_url, publisher, document_id,
                    publication_date, retrieved_at, content_hash, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    source_url=excluded.source_url,
                    document_id=excluded.document_id,
                    content_hash=excluded.content_hash,
                    metadata_json=excluded.metadata_json
                """,
                (
                    source.source_id,
                    source.source_type.value,
                    source.source_url,
                    source.publisher,
                    source.document_id,
                    _date_to_str(source.publication_date),
                    _datetime_to_str(source.retrieved_at),
                    source.content_hash,
                    json.dumps(source.metadata),
                ),
            )

    def get_source(self, source_id: str) -> Optional[SourceReference]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return SourceReference(
            source_id=row["source_id"],
            source_type=SourceType(row["source_type"]),
            source_url=row["source_url"],
            publisher=row["publisher"],
            document_id=row["document_id"],
            publication_date=_str_to_date(row["publication_date"]),
            retrieved_at=_str_to_datetime(row["retrieved_at"]),
            content_hash=row["content_hash"],
            metadata=json.loads(row["metadata_json"]),
        )

    def save_evidence(self, evidence: EvidenceRecord) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO evidence (
                    evidence_id, source_id, raw_content, content_hash, extraction_method,
                    document_name, page_number, line_number, section_name, tag_name,
                    confidence_score, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO NOTHING
                """,
                (
                    evidence.evidence_id,
                    evidence.source_id,
                    evidence.raw_content,
                    evidence.content_hash,
                    evidence.extraction_method,
                    evidence.document_name,
                    evidence.page_number,
                    evidence.line_number,
                    evidence.section_name,
                    evidence.tag_name,
                    evidence.confidence_score,
                    _datetime_to_str(evidence.ingested_at),
                ),
            )

    def _row_to_evidence(self, row: sqlite3.Row) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=row["evidence_id"],
            source_id=row["source_id"],
            raw_content=row["raw_content"],
            content_hash=row["content_hash"],
            extraction_method=row["extraction_method"],
            document_name=row["document_name"],
            page_number=row["page_number"],
            line_number=row["line_number"],
            section_name=row["section_name"],
            tag_name=row["tag_name"],
            confidence_score=row["confidence_score"],
            ingested_at=_str_to_datetime(row["ingested_at"]),
        )

    def get_evidence(self, evidence_id: str) -> Optional[EvidenceRecord]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM evidence WHERE evidence_id = ?", (evidence_id,))
        row = cursor.fetchone()
        return self._row_to_evidence(row) if row else None

    # ── Fact Operations ─────────────────────────────────────────────────────

    def save_fact(self, fact: FactRecord) -> None:
        """Persist fact record and maintain evidence links and competing fact references."""
        with self.conn:
            cursor = self.conn.cursor()

            # Check for existing facts for the same entity, variable, year, and period
            cursor.execute(
                """
                SELECT fact_id, reported_value, normalized_value FROM facts
                WHERE entity_id = ? AND canonical_variable = ?
                  AND fiscal_year = ? AND fiscal_period = ?
                  AND fact_id != ?
                """,
                (fact.entity_id, fact.canonical_variable, fact.fiscal_year, fact.fiscal_period.value, fact.fact_id),
            )
            existing_competing = cursor.fetchall()
            if existing_competing:
                for comp_row in existing_competing:
                    comp_id = comp_row["fact_id"]
                    if comp_id not in fact.competing_fact_ids:
                        fact.competing_fact_ids.append(comp_id)

                    # Update the existing fact's competing list to cross-reference this new fact
                    cursor.execute("SELECT competing_fact_ids_json FROM facts WHERE fact_id = ?", (comp_id,))
                    comp_json_row = cursor.fetchone()
                    if comp_json_row:
                        comp_list = json.loads(comp_json_row["competing_fact_ids_json"])
                        if fact.fact_id not in comp_list:
                            comp_list.append(fact.fact_id)
                            cursor.execute(
                                "UPDATE facts SET competing_fact_ids_json = ? WHERE fact_id = ?",
                                (json.dumps(comp_list), comp_id),
                            )

            cursor.execute(
                """
                INSERT INTO facts (
                    fact_id, entity_id, canonical_variable, fact_state, reported_value,
                    normalized_value, currency, unit_scale, scale, sign, fiscal_year,
                    fiscal_period, period_start, period_end, is_instant,
                    accounting_standard, validation_status, missing_reason, conflict_note,
                    parent_fact_ids_json, competing_fact_ids_json, transformation_history_json,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fact_id) DO UPDATE SET
                    fact_state=excluded.fact_state,
                    normalized_value=excluded.normalized_value,
                    validation_status=excluded.validation_status,
                    transformation_history_json=excluded.transformation_history_json,
                    competing_fact_ids_json=excluded.competing_fact_ids_json,
                    metadata_json=excluded.metadata_json
                """,
                (
                    fact.fact_id,
                    fact.entity_id,
                    fact.canonical_variable,
                    fact.fact_state.value,
                    str(fact.reported_value) if fact.reported_value is not None else None,
                    fact.normalized_value,
                    fact.currency.value,
                    fact.unit_scale.value,
                    fact.scale,
                    fact.sign.value,
                    fact.fiscal_year,
                    fact.fiscal_period.value,
                    _date_to_str(fact.period_start),
                    _date_to_str(fact.period_end),
                    1 if fact.is_instant else 0,
                    fact.accounting_standard.value,
                    fact.validation_status.value,
                    fact.missing_reason.value,
                    fact.conflict_note,
                    json.dumps(fact.parent_fact_ids),
                    json.dumps(fact.competing_fact_ids),
                    json.dumps([t.model_dump(mode="json") for t in fact.transformation_history]),
                    json.dumps(fact.metadata),
                    _datetime_to_str(fact.created_at),
                ),
            )

            # Link evidence
            for ev_id in fact.evidence_ids:
                cursor.execute(
                    "INSERT OR IGNORE INTO fact_evidence (fact_id, evidence_id) VALUES (?, ?)",
                    (fact.fact_id, ev_id),
                )

    def get_fact(self, fact_id: str) -> Optional[FactRecord]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM facts WHERE fact_id = ?", (fact_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._build_fact_from_row(row)

    def get_facts_for_entity(
        self,
        entity_id: str,
        canonical_variable: Optional[str] = None,
        fiscal_year: Optional[int] = None,
    ) -> List[FactRecord]:
        query = "SELECT * FROM facts WHERE entity_id = ?"
        params: List[Any] = [entity_id]

        if canonical_variable:
            query += " AND canonical_variable = ?"
            params.append(canonical_variable.lower().strip())
        if fiscal_year:
            query += " AND fiscal_year = ?"
            params.append(fiscal_year)

        query += " ORDER BY fiscal_year DESC, fiscal_period DESC"
        cursor = self.conn.cursor()
        cursor.execute(query, tuple(params))
        return [self._build_fact_from_row(row) for row in cursor.fetchall()]

    def list_facts(
        self,
        entity_id: Optional[str] = None,
        canonical_variable: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[FactRecord]:
        """Query facts across or within entities with optional filtering."""
        query = "SELECT * FROM facts WHERE 1=1"
        params: List[Any] = []
        if entity_id:
            query += " AND entity_id = ?"
            params.append(entity_id)
        if canonical_variable:
            query += " AND canonical_variable = ?"
            params.append(canonical_variable.lower().strip())
        query += " ORDER BY rowid DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cursor = self.conn.cursor()
        cursor.execute(query, tuple(params))
        return [self._build_fact_from_row(row) for row in cursor.fetchall()]

    def get_system_stats(self) -> Dict[str, int]:
        """Get counts of indexed records across tables."""
        cursor = self.conn.cursor()
        table_mappings = [
            ("entities", "companies"),
            ("facts", "facts"),
            ("evidence", "evidence"),
            ("funding_rounds", "funding_rounds"),
            ("transactions", "transactions"),
            ("target_lists", "target_lists"),
            ("research_briefs", "research_briefs"),
        ]
        stats: Dict[str, int] = {}
        for key, tbl in table_mappings:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {tbl}")
                stats[key] = cursor.fetchone()[0]
            except Exception:
                stats[key] = 0
        return stats

    def _build_fact_from_row(self, row: sqlite3.Row) -> FactRecord:
        cursor = self.conn.cursor()
        cursor.execute("SELECT evidence_id FROM fact_evidence WHERE fact_id = ?", (row["fact_id"],))
        evidence_ids = [r["evidence_id"] for r in cursor.fetchall()]

        th_data = json.loads(row["transformation_history_json"])
        transformations = [
            TransformationStep(
                step_name=t["step_name"],
                input_value=t["input_value"],
                output_value=t["output_value"],
                rationale=t["rationale"],
                timestamp=_str_to_datetime(t["timestamp"]),
            )
            for t in th_data
        ]

        reported_val_raw = row["reported_value"]
        reported_val: Optional[Union[float, int, str]] = None
        if reported_val_raw is not None:
            try:
                reported_val = float(reported_val_raw)
                if reported_val.is_integer():
                    reported_val = int(reported_val)
            except ValueError:
                reported_val = reported_val_raw

        return FactRecord(
            fact_id=row["fact_id"],
            entity_id=row["entity_id"],
            canonical_variable=row["canonical_variable"],
            fact_state=FactState(row["fact_state"]),
            reported_value=reported_val,
            normalized_value=row["normalized_value"],
            currency=Currency(row["currency"]),
            unit_scale=UnitScale(row["unit_scale"]),
            scale=row["scale"],
            sign=Sign(row["sign"]),
            fiscal_year=row["fiscal_year"],
            fiscal_period=FiscalPeriodType(row["fiscal_period"]),
            period_start=_str_to_date(row["period_start"]),
            period_end=_str_to_date(row["period_end"]),
            is_instant=bool(row["is_instant"]),
            accounting_standard=AccountingStandard(row["accounting_standard"]),
            evidence_ids=evidence_ids,
            parent_fact_ids=json.loads(row["parent_fact_ids_json"]),
            competing_fact_ids=json.loads(row["competing_fact_ids_json"]),
            transformation_history=transformations,
            validation_status=ValidationStatus(row["validation_status"]),
            missing_reason=MissingDataReason(row["missing_reason"]),
            conflict_note=row["conflict_note"],
            created_at=_str_to_datetime(row["created_at"]),
            metadata=json.loads(row["metadata_json"]),
        )

    # ── Validation Operations ───────────────────────────────────────────────

    def save_validation_result(self, result: ValidationResult) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO validation_results (
                    result_id, target_id, target_type, rule_name, passed,
                    severity, observed_value, expected_range, message, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(result_id) DO NOTHING
                """,
                (
                    result.result_id,
                    result.target_id,
                    result.target_type,
                    result.rule_name,
                    1 if result.passed else 0,
                    result.severity.value,
                    str(result.observed_value) if result.observed_value is not None else None,
                    result.expected_range,
                    result.message,
                    _datetime_to_str(result.timestamp),
                ),
            )

    def get_validation_results(self, target_id: str) -> List[ValidationResult]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM validation_results WHERE target_id = ? ORDER BY timestamp DESC",
            (target_id,),
        )
        return [
            ValidationResult(
                result_id=row["result_id"],
                target_id=row["target_id"],
                target_type=row["target_type"],
                rule_name=row["rule_name"],
                passed=bool(row["passed"]),
                severity=ValidationSeverity(row["severity"]),
                observed_value=row["observed_value"],
                expected_range=row["expected_range"],
                message=row["message"],
                timestamp=_str_to_datetime(row["timestamp"]),
            )
            for r in cursor.fetchall()
            for row in [r]
        ]

    # ── Research Operations ─────────────────────────────────────────────────

    def save_research_brief(self, brief: Dict[str, Any]) -> None:
        created_at_str = brief.get("created_at")
        if isinstance(created_at_str, datetime):
            created_at_str = _datetime_to_str(created_at_str)
        elif not created_at_str:
            created_at_str = _datetime_to_str(datetime.now())

        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO research_briefs (
                    research_id, entity_id, brief_title, executive_summary, status,
                    claims_json, financial_trends_json, data_gaps_json, risk_indicators_json,
                    cited_evidence_ids_json, valuation_summary_json, model_provider, model_id,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(research_id) DO UPDATE SET
                    brief_title=excluded.brief_title,
                    executive_summary=excluded.executive_summary,
                    status=excluded.status,
                    claims_json=excluded.claims_json,
                    financial_trends_json=excluded.financial_trends_json,
                    data_gaps_json=excluded.data_gaps_json,
                    risk_indicators_json=excluded.risk_indicators_json,
                    cited_evidence_ids_json=excluded.cited_evidence_ids_json,
                    valuation_summary_json=excluded.valuation_summary_json,
                    model_provider=excluded.model_provider,
                    model_id=excluded.model_id,
                    metadata_json=excluded.metadata_json,
                    created_at=excluded.created_at
                """,
                (
                    brief["research_id"],
                    brief["entity_id"],
                    brief.get("brief_title", ""),
                    brief.get("executive_summary", ""),
                    brief.get("status", "COMPLETED"),
                    json.dumps(brief.get("claims", []), default=str),
                    json.dumps(brief.get("financial_trends", []), default=str),
                    json.dumps(brief.get("data_gaps", []), default=str),
                    json.dumps(brief.get("risk_indicators", []), default=str),
                    json.dumps(brief.get("cited_evidence_ids", [])),
                    json.dumps(brief.get("valuation_summary"), default=str) if brief.get("valuation_summary") is not None else None,
                    brief.get("model_provider", "mock"),
                    brief.get("model_id"),
                    json.dumps(brief.get("metadata", {}), default=str),
                    created_at_str,
                ),
            )

    def _row_to_brief_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        val_summary = None
        if row["valuation_summary_json"]:
            try:
                val_summary = json.loads(row["valuation_summary_json"])
            except Exception:
                val_summary = None

        return {
            "research_id": row["research_id"],
            "entity_id": row["entity_id"],
            "brief_title": row["brief_title"],
            "executive_summary": row["executive_summary"],
            "status": row["status"],
            "claims": json.loads(row["claims_json"]),
            "financial_trends": json.loads(row["financial_trends_json"]),
            "data_gaps": json.loads(row["data_gaps_json"]),
            "risk_indicators": json.loads(row["risk_indicators_json"]),
            "cited_evidence_ids": json.loads(row["cited_evidence_ids_json"]),
            "valuation_summary": val_summary,
            "model_provider": row["model_provider"],
            "model_id": row["model_id"],
            "metadata": json.loads(row["metadata_json"]),
            "created_at": row["created_at"],
        }

    def get_research_brief(self, research_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM research_briefs WHERE research_id = ?", (research_id,))
        row = cursor.fetchone()
        return self._row_to_brief_dict(row) if row else None

    def get_research_briefs_for_entity(self, entity_id: str) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM research_briefs WHERE entity_id = ? ORDER BY created_at DESC",
            (entity_id,),
        )
        return [self._row_to_brief_dict(r) for r in cursor.fetchall()]

    def get_evidence_for_entity(self, entity_id: str) -> List[EvidenceRecord]:
        cursor = self.conn.cursor()
        # Find all evidence linked to this entity's facts
        cursor.execute(
            """
            SELECT DISTINCT e.*
            FROM evidence e
            JOIN fact_evidence fe ON e.evidence_id = fe.evidence_id
            JOIN facts f ON fe.fact_id = f.fact_id
            WHERE f.entity_id = ?
            ORDER BY e.ingested_at DESC
            """,
            (entity_id,),
        )
        records: List[EvidenceRecord] = []
        seen_ids = set()
        for row in cursor.fetchall():
            ev = self._row_to_evidence(row)
            if ev.evidence_id not in seen_ids:
                seen_ids.add(ev.evidence_id)
                records.append(ev)

        # Also find evidence referenced in relationships
        relationships = self.get_relationships(entity_id)
        for r in relationships:
            for ev_id in r.evidence_ids:
                if ev_id not in seen_ids:
                    ev = self.get_evidence(ev_id)
                    if ev:
                        seen_ids.add(ev.evidence_id)
                        records.append(ev)

        return records

    # ── JSON Export ─────────────────────────────────────────────────────────

    def export_facts_json(self, entity_id: str) -> str:
        """Export all facts and evidence for an entity as formatted JSON."""
        entity = self.get_entity(entity_id)
        facts = self.get_facts_for_entity(entity_id)

        evidence_ids = {ev_id for f in facts for ev_id in f.evidence_ids}
        evidence_records = [self.get_evidence(ev_id) for ev_id in evidence_ids if self.get_evidence(ev_id)]

        payload = {
            "entity": entity.model_dump(mode="json") if entity else None,
            "facts": [f.model_dump(mode="json") for f in facts],
            "evidence": [ev.model_dump(mode="json") for ev in evidence_records if ev],
        }
        return json.dumps(payload, indent=2)

    # ── Target Discovery & Watchlist Persistence (Phase 6) ──────────────────

    def save_discovery_run(self, result: Any) -> None:
        """Persist a target discovery run and its screened results."""
        from opendeal.discovery.contracts import ScreeningResult

        if isinstance(result, ScreeningResult):
            meta_dict = result.metadata.model_dump(mode="json")
            crit_dict = result.metadata.criteria.model_dump(mode="json")
            results_dict = [t.model_dump(mode="json") for t in result.targets]
            run_id = result.metadata.run_id
            created_at = _datetime_to_str(result.metadata.created_at)
        else:
            meta_dict = result.get("metadata", {})
            crit_dict = meta_dict.get("criteria", {})
            results_dict = result.get("targets", [])
            run_id = meta_dict.get("run_id", "")
            created_at = meta_dict.get("created_at", _datetime_to_str(datetime.now()))

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO discovery_runs (run_id, created_at, criteria_json, metadata_json, results_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    criteria_json=excluded.criteria_json,
                    metadata_json=excluded.metadata_json,
                    results_json=excluded.results_json
                """,
                (
                    run_id,
                    created_at,
                    json.dumps(crit_dict, default=str),
                    json.dumps(meta_dict, default=str),
                    json.dumps(results_dict, default=str),
                ),
            )

    def get_discovery_run(self, run_id: str) -> Optional[Any]:
        """Retrieve a discovery run by its unique run_id."""
        from opendeal.discovery.contracts import ScreeningResult

        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM discovery_runs WHERE run_id = ?", (run_id,))
        row = cursor.fetchone()
        if not row:
            return None

        meta_dict = json.loads(row["metadata_json"])
        results_list = json.loads(row["results_json"])

        try:
            return ScreeningResult.model_validate({
                "metadata": meta_dict,
                "targets": results_list,
            })
        except Exception as e:
            logger.warning("Error deserializing ScreeningResult: %s", e)
            return {
                "metadata": meta_dict,
                "targets": results_list,
            }

    def list_discovery_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List recent discovery screening runs."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT run_id, created_at, metadata_json FROM discovery_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        runs = []
        for row in cursor.fetchall():
            runs.append({
                "run_id": row["run_id"],
                "created_at": row["created_at"],
                "metadata": json.loads(row["metadata_json"]),
            })
        return runs

    def save_target_list(self, target_list: Any) -> None:
        """Persist or update a user-defined target list."""
        from opendeal.discovery.contracts import TargetList

        if isinstance(target_list, TargetList):
            lid = target_list.list_id
            name = target_list.name
            desc = target_list.description
            c_at = _datetime_to_str(target_list.created_at)
            u_at = _datetime_to_str(target_list.updated_at)
        else:
            lid = target_list["list_id"]
            name = target_list["name"]
            desc = target_list.get("description")
            c_at = target_list.get("created_at")
            u_at = target_list.get("updated_at")

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO target_lists (list_id, name, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(list_id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    updated_at=excluded.updated_at
                """,
                (lid, name, desc, c_at, u_at),
            )

    def get_target_list(self, list_id: str) -> Optional[Any]:
        """Retrieve a target list by list_id."""
        from opendeal.discovery.contracts import TargetList

        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT t.*, COUNT(m.entity_id) as member_count
            FROM target_lists t
            LEFT JOIN target_list_members m ON t.list_id = m.list_id
            WHERE t.list_id = ?
            GROUP BY t.list_id
            """,
            (list_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        return TargetList(
            list_id=row["list_id"],
            name=row["name"],
            description=row["description"],
            created_at=_str_to_datetime(row["created_at"]),
            updated_at=_str_to_datetime(row["updated_at"]),
            member_count=row["member_count"],
        )

    def list_target_lists(self) -> List[Any]:
        """List all saved target lists with member counts."""
        from opendeal.discovery.contracts import TargetList

        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT t.*, COUNT(m.entity_id) as member_count
            FROM target_lists t
            LEFT JOIN target_list_members m ON t.list_id = m.list_id
            GROUP BY t.list_id
            ORDER BY t.created_at DESC
            """
        )
        results = []
        for row in cursor.fetchall():
            results.append(
                TargetList(
                    list_id=row["list_id"],
                    name=row["name"],
                    description=row["description"],
                    created_at=_str_to_datetime(row["created_at"]),
                    updated_at=_str_to_datetime(row["updated_at"]),
                    member_count=row["member_count"],
                )
            )
        return results

    def delete_target_list(self, list_id: str) -> bool:
        """Delete a target list and its memberships."""
        with self.conn:
            cursor = self.conn.execute("DELETE FROM target_lists WHERE list_id = ?", (list_id,))
            return cursor.rowcount > 0

    def save_target_list_member(self, item: Any, history: Any) -> None:
        """Add a company entity to a target list and record initial audit history."""
        from opendeal.discovery.contracts import TargetListItem, TargetListHistory

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO target_list_members (list_id, entity_id, status, notes, source_run_id, added_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.list_id,
                    item.entity_id,
                    item.status.value,
                    item.notes,
                    item.source_run_id,
                    _datetime_to_str(item.added_at),
                    _datetime_to_str(item.updated_at),
                ),
            )
            self.conn.execute(
                """
                INSERT INTO target_list_history (history_id, list_id, entity_id, old_status, new_status, note, changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history.history_id,
                    history.list_id,
                    history.entity_id,
                    history.old_status.value if history.old_status else None,
                    history.new_status.value,
                    history.note,
                    _datetime_to_str(history.changed_at),
                ),
            )
            # Update updated_at timestamp on list
            self.conn.execute(
                "UPDATE target_lists SET updated_at = ? WHERE list_id = ?",
                (_datetime_to_str(item.updated_at), item.list_id),
            )

    def update_target_list_member(self, item: Any, history: Any) -> None:
        """Update a member's status/notes and record audit history."""
        with self.conn:
            self.conn.execute(
                """
                UPDATE target_list_members
                SET status = ?, notes = ?, updated_at = ?
                WHERE list_id = ? AND entity_id = ?
                """,
                (
                    item.status.value,
                    item.notes,
                    _datetime_to_str(item.updated_at),
                    item.list_id,
                    item.entity_id,
                ),
            )
            self.conn.execute(
                """
                INSERT INTO target_list_history (history_id, list_id, entity_id, old_status, new_status, note, changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history.history_id,
                    history.list_id,
                    history.entity_id,
                    history.old_status.value if history.old_status else None,
                    history.new_status.value,
                    history.note,
                    _datetime_to_str(history.changed_at),
                ),
            )
            self.conn.execute(
                "UPDATE target_lists SET updated_at = ? WHERE list_id = ?",
                (_datetime_to_str(item.updated_at), item.list_id),
            )

    def remove_target_list_member(self, list_id: str, entity_id: str) -> bool:
        """Remove a member from a target list."""
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM target_list_members WHERE list_id = ? AND entity_id = ?",
                (list_id, entity_id),
            )
            deleted = cursor.rowcount > 0
            if deleted:
                self.conn.execute(
                    "UPDATE target_lists SET updated_at = ? WHERE list_id = ?",
                    (_datetime_to_str(datetime.now()), list_id),
                )
            return deleted

    def get_target_list_members(self, list_id: str) -> List[Any]:
        """Retrieve all members of a target list."""
        from opendeal.discovery.contracts import TargetListItem, TargetListStatus

        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT m.*, c.legal_name as entity_name
            FROM target_list_members m
            JOIN companies c ON m.entity_id = c.entity_id
            WHERE m.list_id = ?
            ORDER BY m.added_at ASC
            """,
            (list_id,),
        )
        members = []
        for row in cursor.fetchall():
            members.append(
                TargetListItem(
                    list_id=row["list_id"],
                    entity_id=row["entity_id"],
                    entity_name=row["entity_name"],
                    status=TargetListStatus(row["status"]),
                    notes=row["notes"],
                    source_run_id=row["source_run_id"],
                    added_at=_str_to_datetime(row["added_at"]),
                    updated_at=_str_to_datetime(row["updated_at"]),
                )
            )
        return members

    def is_target_list_member(self, list_id: str, entity_id: str) -> bool:
        """Check if an entity is already a member of a target list."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT 1 FROM target_list_members WHERE list_id = ? AND entity_id = ?",
            (list_id, entity_id),
        )
        return cursor.fetchone() is not None

    def get_target_list_history(self, list_id: str, entity_id: Optional[str] = None) -> List[Any]:
        """Retrieve audit history records for a target list or specific member."""
        from opendeal.discovery.contracts import TargetListHistory, TargetListStatus

        query = "SELECT * FROM target_list_history WHERE list_id = ?"
        params: List[Any] = [list_id]
        if entity_id:
            query += " AND entity_id = ?"
            params.append(entity_id)

        query += " ORDER BY changed_at DESC"
        cursor = self.conn.cursor()
        cursor.execute(query, tuple(params))
        history_records = []
        for row in cursor.fetchall():
            history_records.append(
                TargetListHistory(
                    history_id=row["history_id"],
                    list_id=row["list_id"],
                    entity_id=row["entity_id"],
                    old_status=TargetListStatus(row["old_status"]) if row["old_status"] else None,
                    new_status=TargetListStatus(row["new_status"]),
                    note=row["note"],
                    changed_at=_str_to_datetime(row["changed_at"]),
                )
            )
        return history_records

    # ── Funding & Transaction Persistence (Phase 7) ─────────────────────────

    def save_funding_round(self, funding_round: Any) -> None:
        """Persist or update a funding round and its participant links."""
        from opendeal.deals.contracts import FundingRound

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO funding_rounds (
                    round_id, company_id, round_type, announced_date, closing_date,
                    disclosed_amount, currency, amount_kind, pre_money_valuation,
                    post_money_valuation, instrument_type, lead_investor_ids_json,
                    participant_ids_json, source_reference_ids_json, evidence_ids_json,
                    verification_status, conflict_notes, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(round_id) DO UPDATE SET
                    round_type=excluded.round_type,
                    announced_date=excluded.announced_date,
                    closing_date=excluded.closing_date,
                    disclosed_amount=excluded.disclosed_amount,
                    currency=excluded.currency,
                    amount_kind=excluded.amount_kind,
                    pre_money_valuation=excluded.pre_money_valuation,
                    post_money_valuation=excluded.post_money_valuation,
                    instrument_type=excluded.instrument_type,
                    lead_investor_ids_json=excluded.lead_investor_ids_json,
                    participant_ids_json=excluded.participant_ids_json,
                    source_reference_ids_json=excluded.source_reference_ids_json,
                    evidence_ids_json=excluded.evidence_ids_json,
                    verification_status=excluded.verification_status,
                    conflict_notes=excluded.conflict_notes,
                    metadata_json=excluded.metadata_json
                """,
                (
                    funding_round.round_id,
                    funding_round.company_id,
                    funding_round.round_type.value,
                    _date_to_str(funding_round.announced_date),
                    _date_to_str(funding_round.closing_date),
                    funding_round.disclosed_amount,
                    funding_round.currency.value,
                    funding_round.amount_kind.value,
                    funding_round.pre_money_valuation,
                    funding_round.post_money_valuation,
                    funding_round.instrument_type,
                    json.dumps(funding_round.lead_investor_ids),
                    json.dumps(funding_round.participant_ids),
                    json.dumps(funding_round.source_reference_ids),
                    json.dumps(funding_round.evidence_ids),
                    funding_round.verification_status.value,
                    funding_round.conflict_notes,
                    json.dumps(funding_round.metadata),
                    _datetime_to_str(funding_round.created_at),
                ),
            )

            # Persist any participants
            for p in funding_round.participants:
                self.save_funding_participant(p)

    def _row_to_funding_round(self, row: sqlite3.Row) -> Any:
        from opendeal.deals.contracts import (
            AmountKind,
            DealVerificationStatus,
            FundingRound,
            FundingRoundType,
        )

        participants = self.get_funding_participants_for_round(row["round_id"])
        conflicts = self.get_deal_conflicts_for_event(row["round_id"])

        return FundingRound(
            round_id=row["round_id"],
            company_id=row["company_id"],
            round_type=FundingRoundType(row["round_type"]),
            announced_date=_str_to_date(row["announced_date"]),
            closing_date=_str_to_date(row["closing_date"]),
            disclosed_amount=row["disclosed_amount"],
            currency=Currency(row["currency"]),
            amount_kind=AmountKind(row["amount_kind"]),
            pre_money_valuation=row["pre_money_valuation"],
            post_money_valuation=row["post_money_valuation"],
            instrument_type=row["instrument_type"],
            lead_investor_ids=json.loads(row["lead_investor_ids_json"]),
            participant_ids=json.loads(row["participant_ids_json"]),
            participants=participants,
            source_reference_ids=json.loads(row["source_reference_ids_json"]),
            evidence_ids=json.loads(row["evidence_ids_json"]),
            verification_status=DealVerificationStatus(row["verification_status"]),
            conflict_notes=row["conflict_notes"],
            conflicts=conflicts,
            metadata=json.loads(row["metadata_json"]),
            created_at=_str_to_datetime(row["created_at"]),
        )

    def get_funding_round(self, round_id: str) -> Optional[Any]:
        """Retrieve a funding round by round_id."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM funding_rounds WHERE round_id = ?", (round_id,))
        row = cursor.fetchone()
        return self._row_to_funding_round(row) if row else None

    def get_funding_rounds_for_company(self, company_id: str) -> List[Any]:
        """Retrieve all funding rounds for a company."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM funding_rounds WHERE company_id = ? ORDER BY announced_date DESC, created_at DESC",
            (company_id,),
        )
        return [self._row_to_funding_round(r) for r in cursor.fetchall()]

    def save_funding_participant(self, participant: Any) -> None:
        """Persist or update a funding participant commitment record."""
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO funding_participants (
                    participant_id, round_id, investor_id, fund_id, investor_name, is_lead, investor_type,
                    disclosed_commitment, currency, shares_acquired, implied_ownership_pct,
                    ownership_assumptions, evidence_ids_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(participant_id) DO UPDATE SET
                    fund_id=excluded.fund_id,
                    investor_name=excluded.investor_name,
                    is_lead=excluded.is_lead,
                    investor_type=excluded.investor_type,
                    disclosed_commitment=excluded.disclosed_commitment,
                    currency=excluded.currency,
                    shares_acquired=excluded.shares_acquired,
                    implied_ownership_pct=excluded.implied_ownership_pct,
                    ownership_assumptions=excluded.ownership_assumptions,
                    evidence_ids_json=excluded.evidence_ids_json
                """,
                (
                    participant.participant_id,
                    participant.round_id,
                    participant.investor_id,
                    getattr(participant, "fund_id", None),
                    getattr(participant, "investor_name", None),
                    1 if participant.is_lead else 0,
                    participant.investor_type.value,
                    participant.disclosed_commitment,
                    participant.currency.value if participant.currency else None,
                    participant.shares_acquired,
                    participant.implied_ownership_pct,
                    participant.ownership_assumptions,
                    json.dumps(participant.evidence_ids),
                    _datetime_to_str(participant.created_at),
                ),
            )

    def get_funding_participants_for_round(self, round_id: str) -> List[Any]:
        """Retrieve all participants for a specific funding round."""
        from opendeal.deals.contracts import FundingParticipant, InvestorType

        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM funding_participants WHERE round_id = ?", (round_id,))
        participants = []
        for row in cursor.fetchall():
            row_keys = row.keys()
            participants.append(
                FundingParticipant(
                    participant_id=row["participant_id"],
                    round_id=row["round_id"],
                    investor_id=row["investor_id"],
                    fund_id=row["fund_id"] if "fund_id" in row_keys else None,
                    investor_name=row["investor_name"] if "investor_name" in row_keys else None,
                    is_lead=bool(row["is_lead"]),
                    investor_type=InvestorType(row["investor_type"]),
                    disclosed_commitment=row["disclosed_commitment"],
                    currency=Currency(row["currency"]) if row["currency"] else None,
                    shares_acquired=row["shares_acquired"],
                    implied_ownership_pct=row["implied_ownership_pct"],
                    ownership_assumptions=row["ownership_assumptions"],
                    evidence_ids=json.loads(row["evidence_ids_json"]),
                    created_at=_str_to_datetime(row["created_at"]),
                )
            )
        return participants

    def get_funding_participants_for_investor(self, investor_id: str) -> List[Any]:
        """Retrieve all investment participations for an investor."""
        from opendeal.deals.contracts import FundingParticipant, InvestorType

        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM funding_participants WHERE investor_id = ?", (investor_id,))
        participants = []
        for row in cursor.fetchall():
            row_keys = row.keys()
            participants.append(
                FundingParticipant(
                    participant_id=row["participant_id"],
                    round_id=row["round_id"],
                    investor_id=row["investor_id"],
                    fund_id=row["fund_id"] if "fund_id" in row_keys else None,
                    investor_name=row["investor_name"] if "investor_name" in row_keys else None,
                    is_lead=bool(row["is_lead"]),
                    investor_type=InvestorType(row["investor_type"]),
                    disclosed_commitment=row["disclosed_commitment"],
                    currency=Currency(row["currency"]) if row["currency"] else None,
                    shares_acquired=row["shares_acquired"],
                    implied_ownership_pct=row["implied_ownership_pct"],
                    ownership_assumptions=row["ownership_assumptions"],
                    evidence_ids=json.loads(row["evidence_ids_json"]),
                    created_at=_str_to_datetime(row["created_at"]),
                )
            )
        return participants

    def save_transaction(self, transaction: Any) -> None:
        """Persist or update an M&A, divestiture, or corporate control transaction."""
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO transactions (
                    transaction_id, transaction_type, acquirer_id, acquirer_name,
                    target_id, target_name, seller_id, seller_name,
                    announced_date, expected_completion_date, actual_completion_date,
                    status, disclosed_value, currency, consideration_type,
                    consideration_details_json, assets_or_business_unit, regulatory_approvals,
                    termination_reason, source_reference_ids_json, evidence_ids_json,
                    verification_status, conflict_notes, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(transaction_id) DO UPDATE SET
                    transaction_type=excluded.transaction_type,
                    acquirer_id=excluded.acquirer_id,
                    acquirer_name=excluded.acquirer_name,
                    target_id=excluded.target_id,
                    target_name=excluded.target_name,
                    seller_id=excluded.seller_id,
                    seller_name=excluded.seller_name,
                    announced_date=excluded.announced_date,
                    expected_completion_date=excluded.expected_completion_date,
                    actual_completion_date=excluded.actual_completion_date,
                    status=excluded.status,
                    disclosed_value=excluded.disclosed_value,
                    currency=excluded.currency,
                    consideration_type=excluded.consideration_type,
                    consideration_details_json=excluded.consideration_details_json,
                    assets_or_business_unit=excluded.assets_or_business_unit,
                    regulatory_approvals=excluded.regulatory_approvals,
                    termination_reason=excluded.termination_reason,
                    source_reference_ids_json=excluded.source_reference_ids_json,
                    evidence_ids_json=excluded.evidence_ids_json,
                    verification_status=excluded.verification_status,
                    conflict_notes=excluded.conflict_notes,
                    metadata_json=excluded.metadata_json
                """,
                (
                    transaction.transaction_id,
                    transaction.transaction_type.value,
                    transaction.acquirer_id,
                    getattr(transaction, "acquirer_name", None),
                    transaction.target_id,
                    getattr(transaction, "target_name", None),
                    transaction.seller_id,
                    getattr(transaction, "seller_name", None),
                    _date_to_str(transaction.announced_date),
                    _date_to_str(transaction.expected_completion_date),
                    _date_to_str(transaction.actual_completion_date),
                    transaction.status.value,
                    transaction.disclosed_value,
                    transaction.currency.value,
                    transaction.consideration_type.value,
                    json.dumps(transaction.consideration_details),
                    transaction.assets_or_business_unit,
                    transaction.regulatory_approvals,
                    transaction.termination_reason,
                    json.dumps(transaction.source_reference_ids),
                    json.dumps(transaction.evidence_ids),
                    transaction.verification_status.value,
                    transaction.conflict_notes,
                    json.dumps(transaction.metadata),
                    _datetime_to_str(transaction.created_at),
                ),
            )

    def _row_to_transaction(self, row: sqlite3.Row) -> Any:
        from opendeal.deals.contracts import (
            ConsiderationType,
            DealVerificationStatus,
            TransactionEvent,
            TransactionStatus,
            TransactionType,
        )

        conflicts = self.get_deal_conflicts_for_event(row["transaction_id"])
        row_keys = row.keys()

        return TransactionEvent(
            transaction_id=row["transaction_id"],
            transaction_type=TransactionType(row["transaction_type"]),
            acquirer_id=row["acquirer_id"],
            acquirer_name=row["acquirer_name"] if "acquirer_name" in row_keys else None,
            target_id=row["target_id"],
            target_name=row["target_name"] if "target_name" in row_keys else None,
            seller_id=row["seller_id"],
            seller_name=row["seller_name"] if "seller_name" in row_keys else None,
            announced_date=_str_to_date(row["announced_date"]),
            expected_completion_date=_str_to_date(row["expected_completion_date"]),
            actual_completion_date=_str_to_date(row["actual_completion_date"]),
            status=TransactionStatus(row["status"]),
            disclosed_value=row["disclosed_value"],
            currency=Currency(row["currency"]),
            consideration_type=ConsiderationType(row["consideration_type"]),
            consideration_details=json.loads(row["consideration_details_json"]),
            assets_or_business_unit=row["assets_or_business_unit"],
            regulatory_approvals=row["regulatory_approvals"],
            termination_reason=row["termination_reason"],
            source_reference_ids=json.loads(row["source_reference_ids_json"]),
            evidence_ids=json.loads(row["evidence_ids_json"]),
            verification_status=DealVerificationStatus(row["verification_status"]),
            conflict_notes=row["conflict_notes"],
            conflicts=conflicts,
            metadata=json.loads(row["metadata_json"]),
            created_at=_str_to_datetime(row["created_at"]),
        )

    def get_transaction(self, transaction_id: str) -> Optional[Any]:
        """Retrieve a transaction event by transaction_id."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE transaction_id = ?", (transaction_id,))
        row = cursor.fetchone()
        return self._row_to_transaction(row) if row else None

    def get_transactions_for_target(self, target_id: str) -> List[Any]:
        """Retrieve transactions where entity was the target."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM transactions WHERE target_id = ? ORDER BY announced_date DESC, created_at DESC",
            (target_id,),
        )
        return [self._row_to_transaction(r) for r in cursor.fetchall()]

    def get_transactions_for_acquirer(self, acquirer_id: str) -> List[Any]:
        """Retrieve transactions where entity was the acquirer."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM transactions WHERE acquirer_id = ? ORDER BY announced_date DESC, created_at DESC",
            (acquirer_id,),
        )
        return [self._row_to_transaction(r) for r in cursor.fetchall()]

    def get_transactions_for_seller(self, seller_id: str) -> List[Any]:
        """Retrieve transactions where entity was the seller."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM transactions WHERE seller_id = ? ORDER BY announced_date DESC, created_at DESC",
            (seller_id,),
        )
        return [self._row_to_transaction(r) for r in cursor.fetchall()]

    def get_transactions_for_entity(self, entity_id: str) -> List[Any]:
        """Retrieve all transactions involving an entity as acquirer, target, or seller."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM transactions
            WHERE acquirer_id = ? OR target_id = ? OR seller_id = ?
            ORDER BY announced_date DESC, created_at DESC
            """,
            (entity_id, entity_id, entity_id),
        )
        return [self._row_to_transaction(r) for r in cursor.fetchall()]

    def save_deal_conflict(self, conflict: Any) -> None:
        """Persist an audit log entry for a transaction data conflict."""
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO deal_conflicts (
                    conflict_id, target_event_id, event_category, field_name,
                    source_a_id, observed_value_a, source_b_id, observed_value_b,
                    resolution_status, resolution_notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conflict_id) DO UPDATE SET
                    resolution_status=excluded.resolution_status,
                    resolution_notes=excluded.resolution_notes
                """,
                (
                    conflict.conflict_id,
                    conflict.target_event_id,
                    conflict.event_category,
                    conflict.field_name,
                    conflict.source_a_id,
                    str(conflict.observed_value_a),
                    conflict.source_b_id,
                    str(conflict.observed_value_b),
                    conflict.resolution_status,
                    conflict.resolution_notes,
                    _datetime_to_str(conflict.created_at),
                ),
            )

    def get_deal_conflicts_for_event(self, target_event_id: str) -> List[Any]:
        """Retrieve recorded conflicting data points for a specific transaction/round."""
        from opendeal.deals.contracts import DealConflict

        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM deal_conflicts WHERE target_event_id = ?", (target_event_id,))
        conflicts = []
        for row in cursor.fetchall():
            conflicts.append(
                DealConflict(
                    conflict_id=row["conflict_id"],
                    target_event_id=row["target_event_id"],
                    event_category=row["event_category"],
                    field_name=row["field_name"],
                    source_a_id=row["source_a_id"],
                    observed_value_a=row["observed_value_a"],
                    source_b_id=row["source_b_id"],
                    observed_value_b=row["observed_value_b"],
                    resolution_status=row["resolution_status"],
                    resolution_notes=row["resolution_notes"],
                    created_at=_str_to_datetime(row["created_at"]),
                )
            )
        return conflicts

    def search_funding_rounds(
        self,
        company_id: Optional[str] = None,
        min_amount: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> List[Any]:
        """Search funding rounds by company or minimum disclosed amount."""
        query = "SELECT * FROM funding_rounds WHERE 1=1"
        params: List[Any] = []

        if company_id:
            query += " AND company_id = ?"
            params.append(company_id)
        if min_amount is not None:
            query += " AND disclosed_amount >= ?"
            params.append(min_amount)

        query += " ORDER BY announced_date DESC, created_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        cursor = self.conn.cursor()
        cursor.execute(query, tuple(params))
        return [self._row_to_funding_round(r) for r in cursor.fetchall()]

    def search_transactions(
        self,
        company_id: Optional[str] = None,
        status: Optional[str] = None,
        min_value: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> List[Any]:
        """Search transactions by target/acquirer, status, or minimum disclosed value."""
        query = "SELECT * FROM transactions WHERE 1=1"
        params: List[Any] = []

        if company_id:
            query += " AND (target_id = ? OR acquirer_id = ?)"
            params.extend([company_id, company_id])
        if status:
            query += " AND status = ?"
            params.append(status.upper())
        if min_value is not None:
            query += " AND disclosed_value >= ?"
            params.append(min_value)

        query += " ORDER BY announced_date DESC, created_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        cursor = self.conn.cursor()
        cursor.execute(query, tuple(params))
        return [self._row_to_transaction(r) for r in cursor.fetchall()]

    # ── Workflow Orchestration & Agent Auditing (Phase 9) ───────────────────

    def save_workflow_definition(self, defn: Any) -> None:
        """Persist or update a registered workflow definition."""
        d = defn.model_dump() if hasattr(defn, "model_dump") else dict(defn)
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO workflow_definitions (
                    workflow_id, name, version, description,
                    required_permissions_json, tasks_schema_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_id) DO UPDATE SET
                    name=excluded.name,
                    version=excluded.version,
                    description=excluded.description,
                    required_permissions_json=excluded.required_permissions_json,
                    tasks_schema_json=excluded.tasks_schema_json,
                    updated_at=excluded.updated_at
                """,
                (
                    d.get("workflow_id"),
                    d.get("name"),
                    d.get("version"),
                    d.get("description"),
                    json.dumps(d.get("required_permissions", []), default=str),
                    json.dumps(d.get("tasks") or d.get("tasks_schema", []), default=str),
                    d.get("created_at") or _datetime_to_str(datetime.now(timezone.utc)),
                    d.get("updated_at") or _datetime_to_str(datetime.now(timezone.utc)),
                ),
            )

    def get_workflow_definition(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a workflow definition by its unique workflow_id."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM workflow_definitions WHERE workflow_id = ?", (workflow_id,))
        row = cursor.fetchone()
        if not row:
            return None
        res = dict(row)
        res["required_permissions"] = json.loads(res.get("required_permissions_json", "[]"))
        tasks = json.loads(res.get("tasks_schema_json", "[]"))
        res["tasks"] = tasks
        res["tasks_schema"] = tasks
        return res

    def list_workflow_definitions(self) -> List[Dict[str, Any]]:
        """List all registered workflow definitions."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM workflow_definitions ORDER BY name ASC")
        results = []
        for row in cursor.fetchall():
            res = dict(row)
            res["required_permissions"] = json.loads(res.get("required_permissions_json", "[]"))
            tasks = json.loads(res.get("tasks_schema_json", "[]"))
            res["tasks"] = tasks
            res["tasks_schema"] = tasks
            results.append(res)
        return results

    def save_workflow_run(self, run: Any) -> None:
        """Persist or update a workflow execution run record."""
        d = run.model_dump() if hasattr(run, "model_dump") else dict(run)
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO workflow_runs (
                    run_id, workflow_id, version, state, initiator,
                    inputs_json, outputs_json, error_message, idempotency_key,
                    created_at, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    state=excluded.state,
                    outputs_json=excluded.outputs_json,
                    error_message=excluded.error_message,
                    started_at=excluded.started_at,
                    completed_at=excluded.completed_at
                WHERE workflow_runs.state NOT IN ('SUCCEEDED', 'PARTIALLY_SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT')
                """,
                (
                    d.get("run_id"),
                    d.get("workflow_id"),
                    d.get("version", "1.0.0"),
                    d.get("state", "DRAFT"),
                    d.get("initiator", "system"),
                    json.dumps(d.get("inputs", {}), default=str),
                    json.dumps(d.get("outputs", {}), default=str),
                    d.get("error_message"),
                    d.get("idempotency_key"),
                    d.get("created_at") or _datetime_to_str(datetime.now(timezone.utc)),
                    d.get("started_at"),
                    d.get("completed_at"),
                ),
            )

    def get_workflow_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a workflow run record by its run_id."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,))
        row = cursor.fetchone()
        if not row:
            return None
        res = dict(row)
        res["inputs"] = json.loads(res.get("inputs_json", "{}"))
        res["outputs"] = json.loads(res.get("outputs_json", "{}"))
        return res

    def claim_queued_workflow_run(self, run_id: str, started_at: str) -> bool:
        """Atomically claim an eligible queued workflow run. Returns True if claimed, False otherwise."""
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                UPDATE workflow_runs
                SET state = 'RUNNING', started_at = ?
                WHERE run_id = ? AND state = 'QUEUED'
                """,
                (started_at, run_id),
            )
            return cursor.rowcount == 1

    def claim_approval_workflow_run(self, run_id: str) -> bool:
        """Atomically claim a workflow run paused in WAITING_FOR_APPROVAL to resume execution."""
        with self.conn:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                UPDATE workflow_runs
                SET state = 'RUNNING'
                WHERE run_id = ? AND state = 'WAITING_FOR_APPROVAL'
                """,
                (run_id,),
            )
            return cursor.rowcount == 1

    def list_workflow_runs(
        self,
        workflow_id: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List recent workflow runs, optionally filtered by workflow or state."""
        query = "SELECT * FROM workflow_runs WHERE 1=1"
        params: List[Any] = []
        if workflow_id:
            query += " AND workflow_id = ?"
            params.append(workflow_id)
        if state:
            query += " AND state = ?"
            params.append(state)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = self.conn.cursor()
        cursor.execute(query, tuple(params))
        runs = []
        for row in cursor.fetchall():
            res = dict(row)
            res["inputs"] = json.loads(res.get("inputs_json", "{}"))
            res["outputs"] = json.loads(res.get("outputs_json", "{}"))
            runs.append(res)
        return runs

    def save_task_run(self, task: Any) -> None:
        """Persist or update a task execution run record."""
        d = task.model_dump() if hasattr(task, "model_dump") else dict(task)
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO task_runs (
                    task_run_id, run_id, task_name, agent_name, state,
                    attempt, max_retries, dependencies_json, inputs_json,
                    outputs_json, error_message, error_classification,
                    started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_run_id) DO UPDATE SET
                    state=excluded.state,
                    attempt=excluded.attempt,
                    outputs_json=excluded.outputs_json,
                    error_message=excluded.error_message,
                    error_classification=excluded.error_classification,
                    started_at=excluded.started_at,
                    completed_at=excluded.completed_at
                WHERE task_runs.state NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT')
                """,
                (
                    d.get("task_run_id"),
                    d.get("run_id"),
                    d.get("task_name"),
                    d.get("agent_name"),
                    d.get("state", "QUEUED"),
                    d.get("attempt", 1),
                    d.get("max_retries", 0),
                    json.dumps(d.get("dependencies", []), default=str),
                    json.dumps(d.get("inputs", {}), default=str),
                    json.dumps(d.get("outputs", {}), default=str),
                    d.get("error_message"),
                    d.get("error_classification"),
                    d.get("started_at"),
                    d.get("completed_at"),
                ),
            )

    def get_task_runs_for_workflow(self, run_id: str) -> List[Dict[str, Any]]:
        """Retrieve all task run records associated with a workflow run."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM task_runs WHERE run_id = ? ORDER BY started_at ASC, task_run_id ASC", (run_id,))
        tasks = []
        for row in cursor.fetchall():
            res = dict(row)
            res["dependencies"] = json.loads(res.get("dependencies_json", "[]"))
            res["inputs"] = json.loads(res.get("inputs_json", "{}"))
            res["outputs"] = json.loads(res.get("outputs_json", "{}"))
            tasks.append(res)
        return tasks

    def save_approval_request(self, approval: Any) -> None:
        """Persist or update a human-in-the-loop approval request."""
        d = approval.model_dump() if hasattr(approval, "model_dump") else dict(approval)
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO workflow_approvals (
                    approval_id, run_id, action_type, action_payload_json,
                    scope, status, requested_at, decided_at, decided_by, decision_rationale
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(approval_id) DO UPDATE SET
                    status=excluded.status,
                    decided_at=excluded.decided_at,
                    decided_by=excluded.decided_by,
                    decision_rationale=excluded.decision_rationale
                """,
                (
                    d.get("approval_id"),
                    d.get("run_id"),
                    d.get("action_type"),
                    json.dumps(d.get("action_payload", {}), default=str),
                    d.get("scope", "GLOBAL"),
                    d.get("status", "PENDING"),
                    d.get("requested_at") or _datetime_to_str(datetime.now(timezone.utc)),
                    d.get("decided_at"),
                    d.get("decided_by"),
                    d.get("decision_rationale"),
                ),
            )

    def get_approval_request(self, approval_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve an approval request by its unique approval_id."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM workflow_approvals WHERE approval_id = ?", (approval_id,))
        row = cursor.fetchone()
        if not row:
            return None
        res = dict(row)
        res["action_payload"] = json.loads(res.get("action_payload_json", "{}"))
        return res

    def list_pending_approvals(self, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List pending approval requests requiring user intervention."""
        query = "SELECT * FROM workflow_approvals WHERE status = 'PENDING'"
        params: List[Any] = []
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        query += " ORDER BY requested_at ASC"

        cursor = self.conn.cursor()
        cursor.execute(query, tuple(params))
        approvals = []
        for row in cursor.fetchall():
            res = dict(row)
            res["action_payload"] = json.loads(res.get("action_payload_json", "{}"))
            approvals.append(res)
        return approvals

    def record_workflow_audit_event(self, event: Any) -> None:
        """Append an immutable audit event to the workflow execution trail."""
        d = event.model_dump() if hasattr(event, "model_dump") else dict(event)
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO workflow_audit_events (
                    event_id, run_id, timestamp, event_type,
                    initiator, agent_or_task, details_json, is_mutation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    d.get("event_id"),
                    d.get("run_id"),
                    d.get("timestamp") or _datetime_to_str(datetime.now(timezone.utc)),
                    d.get("event_type"),
                    d.get("initiator", "system"),
                    d.get("agent_or_task"),
                    json.dumps(d.get("details", {}), default=str),
                    1 if d.get("is_mutation") else 0,
                ),
            )

    def get_workflow_audit_trail(self, run_id: str) -> List[Dict[str, Any]]:
        """Retrieve the complete chronological audit trail for a workflow run."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM workflow_audit_events WHERE run_id = ? ORDER BY timestamp ASC, event_id ASC",
            (run_id,),
        )
        events = []
        for row in cursor.fetchall():
            res = dict(row)
            res["details"] = json.loads(res.get("details_json", "{}"))
            events.append(res)
        return events

    save_audit_event = record_workflow_audit_event
    get_audit_events_for_run = get_workflow_audit_trail

    def save_workflow_snapshot(self, snapshot: Any) -> None:
        """Persist a dataset snapshot for monitoring and drift detection."""
        d = snapshot.model_dump() if hasattr(snapshot, "model_dump") else dict(snapshot)
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO workflow_snapshots (
                    snapshot_id, dataset_key, snapshot_timestamp,
                    record_counts_json, content_hashes_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    record_counts_json=excluded.record_counts_json,
                    content_hashes_json=excluded.content_hashes_json
                """,
                (
                    d.get("snapshot_id"),
                    d.get("dataset_key"),
                    d.get("snapshot_timestamp") or _datetime_to_str(datetime.now(timezone.utc)),
                    json.dumps(d.get("record_counts", {}), default=str),
                    json.dumps(d.get("content_hashes", {}), default=str),
                    d.get("created_at") or _datetime_to_str(datetime.now(timezone.utc)),
                ),
            )

    def get_latest_workflow_snapshot(self, dataset_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve the most recent dataset snapshot for change comparison."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM workflow_snapshots WHERE dataset_key = ? ORDER BY snapshot_timestamp DESC LIMIT 1",
            (dataset_key,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        res = dict(row)
        res["record_counts"] = json.loads(res.get("record_counts_json", "{}"))
        res["content_hashes"] = json.loads(res.get("content_hashes_json", "{}"))
        return res



