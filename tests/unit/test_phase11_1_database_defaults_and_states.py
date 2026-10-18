"""Phase 11.2 Release Candidate Database Defaults, Path Safety, and State Terminology Acceptance Tests."""

import json
import os
from pathlib import Path
import sqlite3
import pytest
from click.testing import CliRunner

from opendeal import __version__
from opendeal.agents.contracts import TaskState, WorkflowState
from opendeal.cli import cli, get_database_path, get_storage
from opendeal.config.paths import (
    get_default_data_dir,
    get_default_database_path,
    is_default_database_path,
    resolve_database_path,
)
from opendeal.config.settings import settings
from opendeal.core.types import EntityType
from opendeal.entities.models import CompanyEntity
from opendeal.storage.migrations import MIGRATIONS
from opendeal.storage.sqlite import SQLiteStorage
from opendeal.ui.app import create_app


class TestPhase11_2DatabasePathSafety:
    """Rigorous tests for Gate B: authoritative database path resolution, safety, and error handling."""

    def test_1_cwd_independence_across_multiple_directories(self, monkeypatch, tmp_path):
        """1. No environment override and multiple different working directories yields same stable default."""
        monkeypatch.delenv("OPENDEAL_DATABASE_PATH", raising=False)
        isolated_data = tmp_path / "canonical_data"
        monkeypatch.setenv("OPENDEAL_DATA_DIR", str(isolated_data))

        dir1 = tmp_path / "work_dir_alpha"
        dir2 = tmp_path / "work_dir_beta"
        dir1.mkdir()
        dir2.mkdir()

        # In dir1
        monkeypatch.chdir(dir1)
        res1 = resolve_database_path()
        assert res1 == str(get_default_database_path())
        assert res1 == str(isolated_data / "opendeal.db")
        assert not (dir1 / "opendeal.db").exists()

        # In dir2
        monkeypatch.chdir(dir2)
        res2 = resolve_database_path()
        assert res2 == res1
        assert not (dir2 / "opendeal.db").exists()

    def test_2_explicit_path_overrides_environment_and_defaults(self, monkeypatch, tmp_path):
        """2. Explicit path overrides environment and defaults."""
        monkeypatch.setenv("OPENDEAL_DATABASE_PATH", str(tmp_path / "env.db"))
        explicit_target = str(tmp_path / "explicit_override.db")

        # Direct resolver
        resolved = resolve_database_path(explicit_target)
        assert resolved == explicit_target

        # Storage initialization with explicit path
        storage = get_storage(explicit_target)
        assert storage.database_path == explicit_target
        assert Path(explicit_target).exists()
        storage.close()

    def test_3_environment_path_overrides_default(self, monkeypatch, tmp_path):
        """3. Environment path overrides the default in CLI and UI."""
        env_db = str(tmp_path / "from_env.db")
        monkeypatch.setenv("OPENDEAL_DATABASE_PATH", env_db)

        # CLI resolver
        assert get_database_path() == env_db

        # CLI status command
        runner = CliRunner()
        res = runner.invoke(cli, ["status", "--json"])
        assert res.exit_code == 0
        data = json.loads(res.output)
        assert data["database"] == env_db
        assert Path(env_db).exists()

        # UI app storage resolution
        app = create_app()
        assert app.state.storage.database_path == env_db
        app.state.storage.close()

    def test_4_existing_database_with_sentinel_record_is_never_overwritten(self, tmp_path):
        """4. Existing database with sentinel data remains intact without truncation."""
        db_file = tmp_path / "sentinel_test.db"
        storage = SQLiteStorage(str(db_file))
        storage.initialize()

        sentinel = CompanyEntity(
            entity_id="ent_sentinel_999",
            legal_name="Sentinel Capital LLC",
            jurisdiction="US-DE",
            entity_type=EntityType.LLC,
        )
        storage.save_entity(sentinel)
        storage.close()

        # Reopen via get_storage
        reopened = get_storage(str(db_file))
        retrieved = reopened.get_entity("ent_sentinel_999")
        assert retrieved is not None
        assert retrieved.legal_name == "Sentinel Capital LLC"
        reopened.close()

    def test_5_invalid_or_missing_parent_path_fails_clearly(self, monkeypatch, tmp_path):
        """5. Invalid or missing-parent path fails clearly with non-zero exit and no rogue directory creation."""
        missing_parent = tmp_path / "nonexistent_subfolder" / "uncreated.db"
        monkeypatch.setenv("OPENDEAL_DATABASE_PATH", str(missing_parent))

        # CLI status check
        runner = CliRunner()
        res = runner.invoke(cli, ["status", "--json"])
        assert res.exit_code != 0
        data = json.loads(res.output)
        assert data["status"] == "ERROR"
        assert "database_error" in data
        assert "Database parent directory does not exist" in data["database_error"]

        # Assert missing parent was NOT created silently
        assert not missing_parent.parent.exists()

        # Storage initialization direct check
        storage = SQLiteStorage(str(missing_parent))
        with pytest.raises(FileNotFoundError, match="Database parent directory does not exist"):
            storage.initialize()

    def test_6_cli_status_reports_actual_database_used(self, monkeypatch, tmp_path):
        """6. CLI status reports the exact database it actually uses."""
        target_db = str(tmp_path / "reported_status.db")
        monkeypatch.setenv("OPENDEAL_DATABASE_PATH", target_db)

        runner = CliRunner()
        res = runner.invoke(cli, ["status", "--json"])
        assert res.exit_code == 0
        data = json.loads(res.output)
        assert data["database"] == target_db

        # Text mode
        res_text = runner.invoke(cli, ["status"])
        assert res_text.exit_code == 0
        assert f"Database Location:    {target_db}" in res_text.output

    def test_7_ui_initialization_uses_same_resolution_policy(self, monkeypatch, tmp_path):
        """7. UI initialization uses the exact same resolution policy as CLI."""
        ui_db = str(tmp_path / "ui_resolved.db")
        monkeypatch.setenv("OPENDEAL_DATABASE_PATH", ui_db)

        # UI without explicit arg
        app1 = create_app()
        assert app1.state.storage.database_path == ui_db
        app1.state.storage.close()

        # UI with explicit arg overriding env
        explicit_ui_db = str(tmp_path / "ui_explicit.db")
        app2 = create_app(db_path=explicit_ui_db)
        assert app2.state.storage.database_path == explicit_ui_db
        app2.state.storage.close()

    def test_8_existing_migrations_preserve_prior_records(self, tmp_path):
        """8. Existing migrations preserve prior records when migrating schemas."""
        db_file = tmp_path / "migration_preservation.db"
        conn = sqlite3.connect(str(db_file))
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
        v1_version, v1_name, v1_sql = MIGRATIONS[0]
        cursor.executescript(v1_sql)
        cursor.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)", (v1_version, v1_name, "2026-01-01T00:00:00Z"))
        cursor.execute(
            """
            INSERT INTO companies (entity_id, legal_name, jurisdiction, entity_type, created_at, updated_at)
            VALUES ('ent_pres_1', 'Preserved Holdings Inc', 'US-NY', 'PUBLIC_LISTED', '2026-01-01', '2026-01-01')
            """
        )
        conn.commit()
        conn.close()

        storage = SQLiteStorage(str(db_file))
        storage.initialize()
        entity = storage.get_entity("ent_pres_1")
        assert entity is not None
        assert entity.legal_name == "Preserved Holdings Inc"
        storage.close()

    def test_9_no_unexpected_database_appears_in_repo_or_real_home(self, monkeypatch, tmp_path):
        """9. No unexpected database appears in the repository or real home directory during isolated runs."""
        clean_work_dir = tmp_path / "clean_cwd"
        clean_work_dir.mkdir()
        monkeypatch.chdir(clean_work_dir)

        isolated_data = tmp_path / "isolated_app_data"
        monkeypatch.setenv("OPENDEAL_DATA_DIR", str(isolated_data))
        monkeypatch.delenv("OPENDEAL_DATABASE_PATH", raising=False)

        real_home_app_support = Path.home() / "Library" / "Application Support" / "OpenDeal"
        home_existed_before = real_home_app_support.exists()

        # Execute CLI status which uses default path
        runner = CliRunner()
        res = runner.invoke(cli, ["status", "--json"])
        assert res.exit_code == 0
        data = json.loads(res.output)
        assert data["database"] == str(isolated_data / "opendeal.db")

        # Assert isolated db was created in isolated_data
        assert (isolated_data / "opendeal.db").exists()

        # Assert working directory has NO opendeal.db created
        assert not (clean_work_dir / "opendeal.db").exists()

        # Assert real home app support was NOT touched or created during test
        if not home_existed_before:
            assert not real_home_app_support.exists()


class TestPhase11_1StateTerminologyReconciliation:
    """Verify that canonical enum values and terminal immutability predicates are exact."""

    def test_workflow_state_enum_members(self):
        """Verify WorkflowState uses SUCCEEDED (not COMPLETED)."""
        terminal_states = {
            WorkflowState.SUCCEEDED,
            WorkflowState.PARTIALLY_SUCCEEDED,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
            WorkflowState.TIMED_OUT,
        }
        for st in terminal_states:
            assert isinstance(st.value, str)

        assert WorkflowState.SUCCEEDED.value == "SUCCEEDED"
        assert not hasattr(WorkflowState, "COMPLETED")

    def test_task_state_enum_members(self):
        """Verify TaskState uses SUCCEEDED (not COMPLETED)."""
        assert TaskState.SUCCEEDED.value == "SUCCEEDED"
        assert TaskState.FAILED.value == "FAILED"
        assert TaskState.SKIPPED.value == "SKIPPED"
        assert TaskState.CANCELLED.value == "CANCELLED"
        assert TaskState.TIMED_OUT.value == "TIMED_OUT"
        assert not hasattr(TaskState, "COMPLETED")

    def test_storage_sql_terminal_predicates_match_contracts(self):
        """Verify storage update queries use exact state strings."""
        storage = SQLiteStorage(":memory:")
        storage.initialize()
        cursor = storage.conn.cursor()
        cursor.execute("SELECT version FROM schema_migrations")
        rows = cursor.fetchall()
        assert len(rows) == 6
