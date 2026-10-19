"""Global Securities Regulators & Corporate Registries Directory."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Dict, List, Optional, Tuple

from opendeal.registries.models import FilingSourceResolution, JurisdictionAuthority


# Regex patterns for common statutory enterprise identifiers
REGEX_INDIAN_CIN = re.compile(r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$", re.IGNORECASE)
REGEX_INDIAN_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$", re.IGNORECASE)
REGEX_UK_CRN = re.compile(r"^(?:[A-Z]{2})?[0-9]{6,8}$", re.IGNORECASE)
REGEX_US_CIK = re.compile(r"^\d{1,10}$")
REGEX_GLOBAL_LEI = re.compile(r"^[0-9A-Z]{18}[0-9]{2}$")
REGEX_FRENCH_SIREN = re.compile(r"^\d{9}$")


class GlobalRegistryDirectory:
    """Directory and resolution engine for international securities regulators and corporate registries."""

    _instance: Optional[GlobalRegistryDirectory] = None

    def __init__(self, data_file: Optional[Path] = None):
        if data_file is None:
            data_file = Path(__file__).parent / "global_authorities.json"
        self._data_file = data_file
        self._authorities: Dict[str, JurisdictionAuthority] = {}
        self._by_country_name: Dict[str, JurisdictionAuthority] = {}
        self._load()

    def _load(self) -> None:
        """Load and index regulatory authorities from JSON."""
        if not self._data_file.exists():
            return

        with open(self._data_file, "r", encoding="utf-8") as f:
            records = json.load(f)

        for rec in records:
            auth = JurisdictionAuthority(**rec)
            self._authorities[auth.country_code.upper()] = auth
            self._by_country_name[auth.jurisdiction.lower()] = auth

    @classmethod
    def get_instance(cls) -> GlobalRegistryDirectory:
        """Obtain singleton instance of GlobalRegistryDirectory."""
        if cls._instance is None:
            cls._instance = GlobalRegistryDirectory()
        return cls._instance

    def get_authority(self, country_code: str) -> Optional[JurisdictionAuthority]:
        """Lookup regulatory authority by ISO country code (case-insensitive)."""
        code = country_code.strip().upper()
        if code in self._authorities:
            return self._authorities[code]
        # Check by jurisdiction name
        return self._by_country_name.get(country_code.strip().lower())

    def list_all(self) -> List[JurisdictionAuthority]:
        """Return all registered global authorities ordered by region and country."""
        return sorted(list(self._authorities.values()), key=lambda a: (a.region, a.jurisdiction))

    def list_by_region(self, region: str) -> List[JurisdictionAuthority]:
        """Return authorities filtered by economic region."""
        region_clean = region.strip().lower()
        return [
            a for a in self._authorities.values()
            if a.region.lower() == region_clean or region_clean in a.region.lower()
        ]

    def list_regions(self) -> List[str]:
        """Return distinct list of supported global regions."""
        regions = {a.region for a in self._authorities.values()}
        return sorted(list(regions))

    def search(self, query: str) -> List[JurisdictionAuthority]:
        """Search authorities across country, regulator, registry, or exchange."""
        q = query.strip().lower()
        if not q:
            return self.list_all()

        matches = []
        for a in self._authorities.values():
            if (
                q in a.country_code.lower()
                or q in a.jurisdiction.lower()
                or q in a.securities_regulator.lower()
                or q in a.corporate_registry.lower()
                or q in a.stock_exchange.lower()
                or q in a.official_disclosure_portal.lower()
            ):
                matches.append(a)
        return matches

    def detect_identifier(self, identifier: str) -> Tuple[Optional[str], Optional[str]]:
        """Detect identifier scheme and probable country code from token format.

        Returns (scheme, country_code) or (None, None).
        """
        ident = identifier.strip().upper()

        if REGEX_INDIAN_CIN.match(ident):
            return "CIN", "IN"
        if REGEX_INDIAN_PAN.match(ident):
            return "PAN", "IN"
        if REGEX_GLOBAL_LEI.match(ident):
            return "LEI", "GLOBAL"
        if REGEX_FRENCH_SIREN.match(ident):
            return "SIREN", "FR"
        if ident.startswith("HRB") or ident.startswith("HRA"):
            return "HRB", "DE"
        # Check standard UK Companies House prefixes or 8 digits
        if REGEX_UK_CRN.match(ident) and (len(ident) == 8 or ident[:2] in ("SC", "NI", "OC", "SO", "R0")):
            return "COMPANIES_HOUSE", "GB"
        if REGEX_US_CIK.match(ident) and 1 <= len(ident) <= 10:
            return "CIK", "US"

        return None, None

    def resolve_filing_source(
        self,
        query: str,
        country_hint: Optional[str] = None,
    ) -> FilingSourceResolution:
        """Resolve the authoritative filing source and statutory regulatory hierarchy.

        Strictly enforces the rule:
        When official data cannot be verified or country is unsupported,
        emits DATA_NOT_FOUND rather than silently substituting an unverified figure.
        """
        clean_query = query.strip()
        detected_scheme, detected_country = self.detect_identifier(clean_query)

        target_country = country_hint or detected_country
        authority = None

        if target_country:
            authority = self.get_authority(target_country)

        if authority is None and clean_query:
            # Try looking up query as country name or code
            authority = self.get_authority(clean_query)

        if authority is not None:
            return FilingSourceResolution(
                is_supported=True,
                authority=authority,
                detected_scheme=detected_scheme,
                query_term=clean_query,
                resolution_status="VERIFIED_STATUTORY_AUTHORITY",
                recommended_filing_portal=authority.official_api_or_download_url,
                guidance=(
                    f"Statutory financial statements must be sourced from {authority.corporate_registry} "
                    f"({authority.financial_statement_source}) or public disclosures from "
                    f"{authority.securities_regulator} ({authority.official_disclosure_portal}). "
                    f"Accounting Framework: {', '.join(authority.accounting_standards)}."
                ),
            )

        # Country / Identifier cannot be reliably verified
        return FilingSourceResolution(
            is_supported=False,
            authority=None,
            detected_scheme=detected_scheme,
            query_term=clean_query,
            resolution_status="DATA_NOT_FOUND",
            recommended_filing_portal=None,
            guidance=(
                f"Statutory authority for '{clean_query}' could not be verified against official registries. "
                "Per OpenDeal epistemic integrity guarantees, unverified estimates are rejected. "
                "Please verify the company's official corporate registration or ISO jurisdiction code."
            ),
        )


def get_global_registry() -> GlobalRegistryDirectory:
    """Convenience helper to obtain global registry singleton."""
    return GlobalRegistryDirectory.get_instance()
