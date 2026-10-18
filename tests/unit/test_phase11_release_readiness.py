"""Phase 11 Release Engineering & Deployment Readiness Validation Suite."""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import zipfile
import pytest
from click.testing import CliRunner

from opendeal import __version__
from opendeal.cli import cli, MASTER_PEAK_PATH, BASELINE_PEAK_HASH
from opendeal.storage.sqlite import SQLiteStorage
from opendeal.storage.migrations import MIGRATIONS, apply_migrations
from opendeal.ui.app import create_app, TEMPLATES_DIR, STATIC_DIR
from opendeal.valuation.mapping import PEAKInputRegistry


class TestPhase11ReleaseReadiness:
    """Rigorous acceptance tests for OpenDeal Phase 11 release candidate."""

    def test_version_consistency_across_subsystems(self):
        """Verify version consistency across __init__, pyproject.toml, CLI, and UI."""
        assert __version__ == "0.11.0rc1"

        # Check pyproject.toml
        repo_root = Path(__file__).resolve().parents[2]
        pyproject_path = repo_root / "pyproject.toml"
        assert pyproject_path.exists(), "pyproject.toml must exist"
        pyproject_text = pyproject_path.read_text()
        assert 'version = "0.11.0rc1"' in pyproject_text

        # Check CLI --version output
        runner = CliRunner()
        res = runner.invoke(cli, ["--version"])
        assert res.exit_code == 0
        assert "0.11.0rc1" in res.output

        # Check CLI status command
        res = runner.invoke(cli, ["status", "--json"])
        assert res.exit_code == 0
        data = json.loads(res.output)
        assert data["version"] == "0.11.0rc1"

        # Check FastAPI app version
        storage = SQLiteStorage(":memory:")
        storage.initialize()
        app = create_app(storage=storage)
        assert app.version == "0.11.0rc1"

    def test_runtime_resources_and_assets_present(self):
        """Verify verified mappings, templates, and static assets exist and load."""
        registry = PEAKInputRegistry.default()
        assert registry.total_authorized_cells > 0
        defs = registry.all_definitions()
        assert len(defs) > 0

        assert TEMPLATES_DIR.exists()
        assert (TEMPLATES_DIR / "base.html").exists()
        base_html = (TEMPLATES_DIR / "base.html").read_text()
        assert "{{ app_version }}" in base_html

        assert STATIC_DIR.exists()
        assert (STATIC_DIR / "css" / "app.css").exists()
        assert (STATIC_DIR / "js" / "app.js").exists()

    def test_ui_loopback_boundary_enforcement(self):
        """Verify that CLI ui command strictly prohibits non-loopback binds unless explicitly authorized."""
        runner = CliRunner()
        # Non-loopback without allow-remote must fail
        res = runner.invoke(cli, ["ui", "--host", "0.0.0.0"])
        assert res.exit_code != 0
        assert "Security Policy Rejection" in res.output or "requires the '--allow-remote' flag" in res.output

        # Loopback without allow-remote should proceed past host validation
        # (will fail or stop on port/app run, but host rejection will NOT trigger)
        res_loopback = runner.invoke(cli, ["ui", "--host", "127.0.0.1", "--port", "99999"])
        assert "Security Policy Rejection" not in res_loopback.output

    def test_database_stepwise_migration_and_recovery(self, tmp_path):
        """Verify incremental schema migration, data preservation, and backup/restore."""
        db_file = tmp_path / "migration_test.db"
        conn = sqlite3.connect(str(db_file))

        # 1. Apply only V1 migration
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
        v1_version, v1_name, v1_sql = MIGRATIONS[0]
        cursor.executescript(v1_sql)
        cursor.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)", (v1_version, v1_name, "2026-01-01T00:00:00Z"))
        conn.commit()

        # Insert a company record into V1 schema
        cursor.execute(
            """
            INSERT INTO companies (entity_id, legal_name, jurisdiction, entity_type, created_at, updated_at)
            VALUES ('ent_test1', 'Test Corp', 'US-DE', 'PRIVATE_LIMITED', '2026-01-01', '2026-01-01')
            """
        )
        conn.commit()
        conn.close()

        # 2. Open with SQLiteStorage which triggers apply_migrations (applying V2..V6)
        storage = SQLiteStorage(str(db_file))
        storage.initialize()

        # Verify company record survived migration
        retrieved = storage.get_entity("ent_test1")
        assert retrieved is not None
        assert retrieved.legal_name == "Test Corp"

        # Verify new tables from V2-V6 now exist
        cursor = storage.conn.cursor()
        cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
        applied = [row[0] for row in cursor.fetchall()]
        assert applied == [1, 2, 3, 4, 5, 6]

        # 3. Test backup and restore
        backup_file = tmp_path / "backup_test.db"
        backup_conn = sqlite3.connect(str(backup_file))
        storage.conn.backup(backup_conn)
        backup_conn.close()
        storage.close()

        # Reopen restored backup and verify data
        restored_storage = SQLiteStorage(str(backup_file))
        restored_storage.initialize()
        restored_entity = restored_storage.get_entity("ent_test1")
        assert restored_entity is not None
        assert restored_entity.legal_name == "Test Corp"
        restored_storage.close()

    def test_artifact_hygiene_in_dist(self):
        """Verify release candidate packages in dist/ adhere strictly to artifact hygiene rules."""
        repo_root = Path(__file__).resolve().parents[2]
        dist_dir = repo_root / "dist"
        if not dist_dir.exists() or not list(dist_dir.glob("*.whl")):
            pytest.skip("dist/ directory or wheels do not exist yet (run python -m build first)")

        wheels = list(dist_dir.glob("*.whl"))
        sdists = list(dist_dir.glob("*.tar.gz"))
        assert len(wheels) > 0, "No wheel built in dist/"
        assert len(sdists) > 0, "No sdist built in dist/"

        # Inspect wheel contents
        with zipfile.ZipFile(wheels[0], "r") as whl:
            names = whl.namelist()
            # Forbidden: no excel workbooks, local databases, or test files in wheel
            for n in names:
                assert not n.endswith(".xlsx"), f"Excel workbook found in wheel: {n}"
                assert not n.endswith(".db"), f"Database file found in wheel: {n}"
                assert not n.endswith(".sqlite"), f"SQLite file found in wheel: {n}"
                assert not n.startswith("tests/"), f"Test file found in wheel: {n}"
                assert ".env" not in n, f"Environment/secret file found in wheel: {n}"

            # Required assets: templates, css, mappings, license
            assert any("templates/base.html" in n for n in names)
            assert any("static/css/app.css" in n for n in names)
            assert any("verified_mappings.json" in n for n in names)
            assert any("LICENSE" in n for n in names)

    @pytest.mark.skipif(not MASTER_PEAK_PATH.exists(), reason="Requires local PEAK master workbook on developer machine")
    def test_protected_peak_master_integrity(self):
        """Confirm bit-for-bit immutability of the protected PEAK master workbook."""
        assert MASTER_PEAK_PATH.exists(), f"Master PEAK workbook missing at {MASTER_PEAK_PATH}"
        with open(MASTER_PEAK_PATH, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        assert digest == BASELINE_PEAK_HASH, (
            f"PEAK master workbook SHA-256 corrupted!\n"
            f"Expected: {BASELINE_PEAK_HASH}\n"
            f"Observed: {digest}"
        )
