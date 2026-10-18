"""Parser for UK Companies House API responses."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from opendeal.entities.models import CompanyEntity


class CompaniesHouseParser:
    """Parses official UK Companies House API responses into OpenDeal domain models.

    Adheres strictly to the reality of UK private company filings:
    - Micro-entities and small private companies often file unaudited/abridged balance sheets.
    - Full detailed P&L statements are not required for most private companies.
    - Explicitly tags accounts availability and filing classification.
    """

    @classmethod
    def parse_company_profile(
        cls,
        data: Dict[str, Any],
        raw_evidence_id: Optional[str] = None,
    ) -> CompanyEntity:
        """Parse a Companies House company profile response into a CompanyEntity."""
        company_number = str(data.get("company_number", "")).strip().upper().zfill(8)
        company_name = data.get("company_name", "").strip()
        if not company_name:
            company_name = f"UK Company {company_number}"

        jurisdiction_raw = data.get("jurisdiction", "united-kingdom").lower()
        jurisdiction = "GB"

        sic_codes = data.get("sic_codes", [])
        primary_sic = sic_codes[0] if sic_codes else None

        entity = CompanyEntity(
            legal_name=company_name,
            jurisdiction=jurisdiction,
            sic_code=primary_sic,
            description=f"UK Registered Entity ({data.get('type', 'company').upper()})",
        )

        # Add official UK Company Registration Number (CRN)
        entity.add_identifier(
            scheme="COMPANIES_HOUSE",
            identifier=company_number,
            jurisdiction="GB",
            is_primary=True,
        )

        # Address formatting
        addr_dict = data.get("registered_office_address", {})
        addr_parts = [
            addr_dict.get("premises"),
            addr_dict.get("address_line_1"),
            addr_dict.get("address_line_2"),
            addr_dict.get("locality"),
            addr_dict.get("postal_code"),
            addr_dict.get("country"),
        ]
        formatted_address = ", ".join([p for p in addr_parts if p])

        # Accounts metadata
        accounts_info = data.get("accounts", {})
        last_accounts = accounts_info.get("last_accounts", {})
        account_type = last_accounts.get("type", "unknown")

        entity.metadata.update(
            {
                "crn": company_number,
                "company_status": data.get("company_status", "unknown"),
                "company_type": data.get("type", "unknown"),
                "date_of_creation": data.get("date_of_creation"),
                "registered_office_address": formatted_address,
                "has_insolvency_history": data.get("has_insolvency_history", False),
                "has_charges": data.get("has_charges", False),
                "account_type": account_type,
                "last_accounts_made_up_to": last_accounts.get("made_up_to"),
                "accounts_next_due": accounts_info.get("next_due"),
                "public_filing_statement_availability": cls._determine_statement_availability(account_type),
                "attribution": "Crown copyright, licensed under the Open Government Licence v3.0",
            }
        )

        return entity

    @classmethod
    def _determine_statement_availability(cls, account_type: str) -> str:
        """Accurately classify availability of financial statements for UK entities."""
        account_type = account_type.lower()
        if "micro" in account_type:
            return "MICRO_ENTITY: Only simplified balance sheet filed; no detailed P&L or notes available."
        elif "small" in account_type or "total-exemption" in account_type:
            return "SMALL_COMPANY: Abridged or unaudited balance sheet filed; full income statement not publicly available."
        elif "dormant" in account_type:
            return "DORMANT: Company not trading; nominal balance sheet only."
        elif "full" in account_type or "group" in account_type or "audited" in account_type:
            return "FULL_ACCOUNTS: Full balance sheet, income statement, and notes available in public filings."
        return "UNKNOWN: Filing requirements depend on company size thresholds under UK Companies Act 2006."

    @classmethod
    def parse_filing_history(cls, data: Dict[str, Any], entity_id: str) -> List[Dict[str, Any]]:
        """Parse filing history items into structured filing records."""
        items = data.get("items", [])
        filings: List[Dict[str, Any]] = []

        for item in items:
            filing = {
                "entity_id": entity_id,
                "transaction_id": item.get("transaction_id") or item.get("barcode", ""),
                "filing_date": item.get("date"),
                "category": item.get("category", "other"),
                "type": item.get("type", ""),
                "description": item.get("description", ""),
                "action_date": item.get("action_date"),
                "pages": item.get("pages"),
                "document_metadata_link": item.get("links", {}).get("document_metadata"),
                "has_associated_filing": "associated_filings" in item,
            }
            filings.append(filing)

        return filings
