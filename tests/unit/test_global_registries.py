"""Unit tests for OpenDeal Global Securities Regulators & Corporate Registries Directory."""

from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from opendeal.cli import cli
from opendeal.entities.models import CompanyEntity
from opendeal.entities.resolution import EntityResolver, ResolutionOutcome
from opendeal.registries import (
    GlobalRegistryDirectory,
    get_global_registry,
)
from opendeal.ui.app import create_app


class TestGlobalRegistryDirectory:
    """Tests for multi-jurisdiction regulatory authority directory coverage."""

    @pytest.fixture
    def registry(self) -> GlobalRegistryDirectory:
        return get_global_registry()

    def test_directory_initialization_and_count(self, registry: GlobalRegistryDirectory):
        """Directory loads comprehensive international authorities across all regions."""
        authorities = registry.list_all()
        assert len(authorities) >= 30, "Must contain at least 30 international jurisdictions"

        regions = registry.list_regions()
        expected_regions = [
            "Africa",
            "Asia-Pacific",
            "Central Asia",
            "Europe",
            "Global",
            "Latin America",
            "Middle East",
            "North America",
        ]
        for r in expected_regions:
            assert r in regions, f"Expected region '{r}' not found in registry"

    def test_india_mca_sebi_distinction(self, registry: GlobalRegistryDirectory):
        """Crucial requirement: Verify Indian corporate registry (MCA) is distinguished from SEBI."""
        india = registry.get_authority("IN")
        assert india is not None
        assert india.jurisdiction == "India"
        assert "SEBI" in india.securities_regulator
        assert "Ministry of Corporate Affairs" in india.corporate_registry
        assert "MCA" in india.corporate_registry
        assert "NSE" in india.stock_exchange and "BSE" in india.stock_exchange
        assert "AOC-4" in india.financial_statement_source
        assert "MGT-7" in india.ownership_disclosure_source
        assert "IND-AS" in india.accounting_standards
        assert "CIN" in india.primary_identifiers
        assert india.currency == "INR"

    def test_european_union_and_national_regulators(self, registry: GlobalRegistryDirectory):
        """Verify pan-European ESMA coordination and major national European authorities."""
        eu = registry.get_authority("EU")
        assert eu is not None
        assert "ESMA" in eu.securities_regulator

        germany = registry.get_authority("DE")
        assert germany is not None
        assert "BaFin" in germany.securities_regulator
        assert "Handelsregister" in germany.corporate_registry or "Handelsregister" in str(germany.primary_identifiers) or "HRB" in germany.primary_identifiers
        assert "Bundesanzeiger" in germany.official_disclosure_portal

        france = registry.get_authority("FR")
        assert france is not None
        assert "AMF" in france.securities_regulator
        assert "SIREN" in france.primary_identifiers

        uk = registry.get_authority("GB")
        assert uk is not None
        assert "FCA" in uk.securities_regulator
        assert "Companies House" in uk.corporate_registry
        assert "COMPANIES_HOUSE" in uk.primary_identifiers

    def test_north_america_coverage(self, registry: GlobalRegistryDirectory):
        """Verify US, Canada (CSA/provincial), and Mexico (CNBV)."""
        us = registry.get_authority("US")
        assert us is not None
        assert "SEC" in us.securities_regulator
        assert "EDGAR" in us.official_disclosure_portal

        ca = registry.get_authority("CA")
        assert ca is not None
        assert "CSA" in ca.securities_regulator
        assert "SEDAR+" in ca.official_disclosure_portal

        mx = registry.get_authority("MX")
        assert mx is not None
        assert "CNBV" in mx.securities_regulator

    def test_middle_east_coverage(self, registry: GlobalRegistryDirectory):
        """Verify Saudi Arabia and UAE multi-zone framework (SCA, DFSA, FSRA)."""
        sa = registry.get_authority("SA")
        assert sa is not None
        assert "CMA" in sa.securities_regulator
        assert "Tadawul" in sa.stock_exchange

        uae = registry.get_authority("AE")
        assert uae is not None
        assert "SCA" in uae.securities_regulator
        assert "DFSA" in uae.securities_regulator or "DFSA" in uae.notes
        assert "FSRA" in uae.securities_regulator or "FSRA" in uae.notes

    def test_search_authorities(self, registry: GlobalRegistryDirectory):
        """Free-text search matches country, regulator acronym, or market."""
        sebi_matches = registry.search("SEBI")
        assert any(a.country_code == "IN" for a in sebi_matches)

        bafin_matches = registry.search("BaFin")
        assert any(a.country_code == "DE" for a in bafin_matches)

        esma_matches = registry.search("ESMA")
        assert any(a.country_code == "EU" for a in esma_matches)


class TestIdentifierDetectionAndResolution:
    """Tests confirming automated identification and transparent missing data handling."""

    @pytest.fixture
    def registry(self) -> GlobalRegistryDirectory:
        return get_global_registry()

    def test_detect_indian_cin(self, registry: GlobalRegistryDirectory):
        """Indian 21-digit CIN auto-detects scheme CIN and country IN."""
        scheme, country = registry.detect_identifier("U72200KA2015PTC084999")
        assert scheme == "CIN"
        assert country == "IN"

    def test_detect_indian_pan(self, registry: GlobalRegistryDirectory):
        """Indian 10-char PAN auto-detects scheme PAN and country IN."""
        scheme, country = registry.detect_identifier("ABCDE1234F")
        assert scheme == "PAN"
        assert country == "IN"

    def test_detect_global_lei(self, registry: GlobalRegistryDirectory):
        """20-character LEI auto-detects scheme LEI."""
        scheme, country = registry.detect_identifier("5493006MHB84DD0ZWV18")
        assert scheme == "LEI"
        assert country == "GLOBAL"

    def test_detect_french_siren(self, registry: GlobalRegistryDirectory):
        """9-digit SIREN auto-detects scheme SIREN and country FR."""
        scheme, country = registry.detect_identifier("123456789")
        assert scheme == "SIREN"
        assert country == "FR"

    def test_resolve_filing_source_success(self, registry: GlobalRegistryDirectory):
        """Resolving valid Indian CIN returns verified MCA/SEBI statutory guidance."""
        res = registry.resolve_filing_source("U72200KA2015PTC084999")
        assert res.is_supported is True
        assert res.resolution_status == "VERIFIED_STATUTORY_AUTHORITY"
        assert res.authority.country_code == "IN"
        assert "Ministry of Corporate Affairs" in res.guidance
        assert "AOC-4" in res.guidance

    def test_resolve_filing_source_unverified_returns_data_not_found(
        self, registry: GlobalRegistryDirectory
    ):
        """Unverified queries strictly emit DATA_NOT_FOUND without fabricating figures."""
        res = registry.resolve_filing_source("UNKNOWN_COMPANY_NONEXISTENT_99999")
        assert res.is_supported is False
        assert res.resolution_status == "DATA_NOT_FOUND"
        assert res.authority is None
        assert "could not be verified" in res.guidance


class TestEntityResolutionWithGlobalIdentifiers:
    """Tests confirming EntityResolver correctly resolves multi-jurisdiction identifiers."""

    def test_resolve_exact_cin_match(self):
        """Indian company resolved exactly via Corporate Identification Number (CIN)."""
        entity = CompanyEntity(legal_name="Nexus Technologies India Pvt Ltd", jurisdiction="IN")
        entity.add_identifier("CIN", "U72200KA2015PTC084999")

        resolver = EntityResolver()
        result = resolver.resolve(query="U72200KA2015PTC084999", candidates=[entity])

        assert result.outcome == ResolutionOutcome.EXACT_IDENTIFIER_MATCH
        assert result.match_strategy == "EXACT_CIN"
        assert result.entity.legal_name == "Nexus Technologies India Pvt Ltd"

    def test_resolve_exact_lei_match(self):
        """Company resolved exactly via Global Legal Entity Identifier (LEI)."""
        entity = CompanyEntity(legal_name="Apex Global Holdings SA", jurisdiction="LU")
        entity.add_identifier("LEI", "5493006MHB84DD0ZWV18")

        resolver = EntityResolver()
        result = resolver.resolve(query="5493006MHB84DD0ZWV18", candidates=[entity])

        assert result.outcome == ResolutionOutcome.EXACT_IDENTIFIER_MATCH
        assert result.match_strategy == "EXACT_LEI"
        assert result.entity.legal_name == "Apex Global Holdings SA"


class TestCLIAndUIIntegration:
    """Tests confirming CLI commands and UI endpoints render cleanly."""

    def test_cli_registries_list_and_regions(self):
        runner = CliRunner()
        res_regions = runner.invoke(cli, ["registries", "regions"])
        assert res_regions.exit_code == 0
        assert "Asia-Pacific" in res_regions.output
        assert "Europe" in res_regions.output

        res_list = runner.invoke(cli, ["registries", "list", "--region", "Asia-Pacific"])
        assert res_list.exit_code == 0
        assert "India" in res_list.output
        assert "SEBI" in res_list.output

    def test_cli_registries_get_and_resolve(self):
        runner = CliRunner()
        res_get = runner.invoke(cli, ["registries", "get", "IN"])
        assert res_get.exit_code == 0
        assert "Ministry of Corporate Affairs" in res_get.output

        res_resolve = runner.invoke(cli, ["registries", "resolve", "U72200KA2015PTC084999"])
        assert res_resolve.exit_code == 0
        assert "VERIFIED_STATUTORY_AUTHORITY" in res_resolve.output

    def test_ui_registries_page_renders(self, tmp_path: Path):
        db_path = tmp_path / "test_ui_registries.db"
        app = create_app(db_path=str(db_path), demo_mode=True, allowed_hosts=["testserver"])
        client = TestClient(app)

        resp = client.get("/registries")
        assert resp.status_code == 200
        html = resp.text
        assert "Global Securities Regulators" in html
        assert "Corporate Registries" in html
        assert "Ministry of Corporate Affairs" in html
        assert "ESMA" in html

        # Test resolution query via UI
        resp_res = client.get("/registries?resolve_id=U72200KA2015PTC084999")
        assert resp_res.status_code == 200
        assert "VERIFIED_STATUTORY_AUTHORITY" in resp_res.text
        assert "Detected Scheme: CIN" in resp_res.text
