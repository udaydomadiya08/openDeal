"""Deterministic entity resolution engine enforcing strict provenance and no-silent-merge rules."""

from __future__ import annotations

import re
from typing import List, Optional
from opendeal.entities.models import (
    CompanyEntity,
    EntityResolutionQuery,
    EntityResolutionResult,
    ResolutionOutcome,
)


def normalize_corporate_name(name: str) -> str:
    """Normalize corporate names by removing standard legal suffixes and punctuation."""
    if not name:
        return ""
    cleaned = name.upper()
    # Strip common punctuation
    cleaned = re.sub(r"[,\.\'\-\(\)]", " ", cleaned)
    # Strip common statutory legal suffixes
    suffixes = [
        r"\bINCORPORATED\b",
        r"\bINC\b",
        r"\bCORPORATION\b",
        r"\bCORP\b",
        r"\bLIMITED\b",
        r"\bLTD\b",
        r"\bPUBLIC LIMITED COMPANY\b",
        r"\bPLC\b",
        r"\bLLC\b",
        r"\bL L C\b",
        r"\bLLP\b",
        r"\bCOMPANY\b",
        r"\bCO\b",
        r"\bHOLDINGS\b",
        r"\bGROUP\b",
    ]
    for pattern in suffixes:
        cleaned = re.sub(pattern, "", cleaned)
    # Collapse whitespace
    return re.sub(r"\s+", " ", cleaned).strip()


class EntityResolver:
    """Resolves corporate queries against known entities.

    Enforces the core rule:
    - Never merge entities solely because their names are similar.
    - Conflicting or ambiguous matches produce POTENTIAL_MATCH_REQUIRES_REVIEW.
    """

    # pyrefly: ignore[unknown-name]
    def __init__(self, storage: Optional[Any] = None) -> None:
        self.storage = storage

    def resolve(
        self,
        # pyrefly: ignore[unknown-name]
        query: Union[str, EntityResolutionQuery],
        jurisdiction: Optional[str] = None,
        candidates: Optional[List[CompanyEntity]] = None,
    ) -> EntityResolutionResult:
        """Resolve a query against known entities or storage using a strict waterfall."""
        if isinstance(query, str):
            res_query = EntityResolutionQuery(query=query, jurisdiction=jurisdiction)
        else:
            res_query = query

        if candidates is None:
            if self.storage is not None:
                candidates = self.storage.list_entities(limit=500)
            else:
                candidates = []

        return self._resolve_waterfall(res_query, candidates)

    @classmethod
    def resolve_candidates(
        cls, query: EntityResolutionQuery, candidates: List[CompanyEntity]
    ) -> EntityResolutionResult:
        """Classmethod helper to resolve query directly against candidate list."""
        return cls._resolve_waterfall(query, candidates)

    @staticmethod
    def _resolve_waterfall(
        query: EntityResolutionQuery, candidates: List[CompanyEntity]
    ) -> EntityResolutionResult:
        """Resolve a query against candidate entities using a strict waterfall."""
        search_term = query.query.strip()
        if not search_term or not candidates:
            return EntityResolutionResult(
                outcome=ResolutionOutcome.NO_MATCH,
                entity=None,
                match_score=0.0,
                match_strategy="EMPTY_INPUT",
                matched_term=search_term,
                candidate_entities=[],
                resolution_notes="No search term or candidate entities provided.",
            )

        term_upper = search_term.upper()

        # 1. Exact Identifier Match (CIK, CRN, Ticker, LEI)
        ident_matches: List[CompanyEntity] = []
        matching_strategy = "EXACT_IDENTIFIER"

        for cand in candidates:
            # Check CIK
            cik = cand.get_identifier("CIK")
            if cik and (cik == search_term or cik.lstrip("0") == search_term.lstrip("0")):
                ident_matches.append(cand)
                matching_strategy = "EXACT_CIK"
                continue

            # Check UK CRN
            crn = cand.get_identifier("COMPANIES_HOUSE")
            if crn and (crn.upper() == term_upper or crn.lstrip("0") == term_upper.lstrip("0")):
                ident_matches.append(cand)
                matching_strategy = "EXACT_CRN"
                continue

            # Check Ticker
            if cand.ticker and cand.ticker.upper() == term_upper:
                # If jurisdiction is specified, verify country/jurisdiction
                if not query.jurisdiction or cand.jurisdiction.upper().startswith(query.jurisdiction.upper()):
                    ident_matches.append(cand)
                    matching_strategy = "EXACT_TICKER"
                    continue

        if len(ident_matches) == 1:
            return EntityResolutionResult(
                outcome=ResolutionOutcome.EXACT_IDENTIFIER_MATCH,
                entity=ident_matches[0],
                match_score=1.0,
                match_strategy=matching_strategy,
                matched_term=search_term,
                candidate_entities=[],
                resolution_notes=f"Exact match on primary legal identifier ({matching_strategy}).",
            )
        elif len(ident_matches) > 1:
            # Multiple entities sharing the same identifier indicates conflict or shared ticker across markets
            return EntityResolutionResult(
                outcome=ResolutionOutcome.POTENTIAL_MATCH_REQUIRES_REVIEW,
                entity=None,
                match_score=0.75,
                match_strategy=f"{matching_strategy}_COLLISION",
                matched_term=search_term,
                candidate_entities=ident_matches,
                resolution_notes="Multiple entities match this identifier. Automatic merge rejected.",
            )

        # 2. Strong Name & Jurisdiction Match
        norm_query = normalize_corporate_name(search_term)
        exact_legal_matches: List[CompanyEntity] = []

        for cand in candidates:
            norm_cand = normalize_corporate_name(cand.legal_name)
            if norm_query == norm_cand:
                # Check jurisdiction if specified in query
                if query.jurisdiction:
                    if cand.jurisdiction.upper().startswith(query.jurisdiction.upper()):
                        exact_legal_matches.append(cand)
                else:
                    exact_legal_matches.append(cand)

        if len(exact_legal_matches) == 1:
            return EntityResolutionResult(
                outcome=ResolutionOutcome.STRONG_NAME_AND_JURISDICTION_MATCH,
                entity=exact_legal_matches[0],
                match_score=0.95,
                match_strategy="STRONG_NAME_AND_JURISDICTION",
                matched_term=search_term,
                candidate_entities=[],
                resolution_notes="Normalized legal name matched unique entity in target jurisdiction.",
            )
        elif len(exact_legal_matches) > 1:
            return EntityResolutionResult(
                outcome=ResolutionOutcome.POTENTIAL_MATCH_REQUIRES_REVIEW,
                entity=None,
                match_score=0.70,
                match_strategy="MULTIPLE_NAME_MATCHES",
                matched_term=search_term,
                candidate_entities=exact_legal_matches,
                resolution_notes="Multiple entities share this corporate name across jurisdictions.",
            )

        # 2b. Partial / Token / Substring Name Match across entities
        token_matches: List[CompanyEntity] = []
        for cand in candidates:
            norm_cand = normalize_corporate_name(cand.legal_name)
            cand_tokens = norm_cand.split()
            if norm_query in cand_tokens or norm_cand.startswith(norm_query) or (len(norm_query) >= 4 and norm_query in norm_cand):
                if query.jurisdiction:
                    if cand.jurisdiction.upper().startswith(query.jurisdiction.upper()):
                        token_matches.append(cand)
                else:
                    token_matches.append(cand)

        if len(token_matches) > 1:
            return EntityResolutionResult(
                outcome=ResolutionOutcome.POTENTIAL_MATCH_REQUIRES_REVIEW,
                entity=None,
                match_score=0.70,
                match_strategy="MULTIPLE_NAME_MATCHES",
                matched_term=search_term,
                candidate_entities=token_matches,
                resolution_notes="Multiple entities match this name or prefix. Review required.",
            )
        elif len(token_matches) == 1:
            return EntityResolutionResult(
                outcome=ResolutionOutcome.POTENTIAL_MATCH_REQUIRES_REVIEW,
                entity=None,
                match_score=0.70,
                match_strategy="PARTIAL_NAME_MATCH",
                matched_term=search_term,
                candidate_entities=token_matches,
                resolution_notes="Partial name match requires manual confirmation.",
            )

        # 3. Former Name & Alias Check (Requires Manual Review)
        alias_matches: List[CompanyEntity] = []
        for cand in candidates:
            # Former names
            for fn in cand.former_names:
                if norm_query == normalize_corporate_name(fn):
                    alias_matches.append(cand)
                    break
            # Trade names / Aliases
            for al in cand.aliases:
                if norm_query == normalize_corporate_name(al):
                    if cand not in alias_matches:
                        alias_matches.append(cand)
                    break

        if alias_matches:
            return EntityResolutionResult(
                outcome=ResolutionOutcome.POTENTIAL_MATCH_REQUIRES_REVIEW,
                entity=None,
                match_score=0.65,
                match_strategy="FORMER_NAME_OR_ALIAS",
                matched_term=search_term,
                candidate_entities=alias_matches,
                resolution_notes="Matched historical former name or commercial alias. Review required.",
            )

        # 4. No Match
        return EntityResolutionResult(
            outcome=ResolutionOutcome.NO_MATCH,
            entity=None,
            match_score=0.0,
            match_strategy="NO_MATCH_FOUND",
            matched_term=search_term,
            candidate_entities=[],
            resolution_notes="No matching legal entity or identifier found.",
        )
