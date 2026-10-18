"""Gate C Acceptance Tests: Source Manifest Methodology, Validation, and Snapshot Assembly."""

import json
from pathlib import Path
import pytest

from opendeal.manifest import (
    AUTHENTICITY_DISCLAIMER,
    create_verified_source_snapshot,
    generate_source_manifest,
    validate_relative_path,
    verify_source_manifest,
)


class TestGateCSourceManifestMethodology:
    """Rigorous tests for Gate C requirements."""

    @pytest.fixture
    def repo_root(self):
        return Path(__file__).resolve().parents[2]

    def test_manifest_structure_and_categories(self, repo_root):
        """Verify manifest contains all required sections and disclaimer."""
        manifest = generate_source_manifest(repo_root)

        # Check required top-level keys
        assert "build_provenance" in manifest
        assert "release_metadata_and_build_config" in manifest
        assert "runtime_package_resources" in manifest
        assert "package_build_inputs" in manifest
        assert "test_and_audit_provenance" in manifest
        assert "excluded_patterns" in manifest

        # Check disclaimer
        disclaimer = manifest["build_provenance"]["authenticity_disclaimer"]
        assert disclaimer == AUTHENTICITY_DISCLAIMER
        assert "does not establish" in disclaimer

        # Check build tools
        tools = manifest["build_provenance"]["build_tools"]
        assert "build" in tools
        assert "setuptools" in tools
        assert "wheel" in tools

        # Check package version
        assert manifest["build_provenance"]["version"] == "0.11.0rc1"

    def test_ci_workflow_included_in_release_inputs(self, repo_root):
        """Verify .github/workflows/ci.yml is explicitly captured in release configuration."""
        manifest = generate_source_manifest(repo_root)
        config_entries = manifest["release_metadata_and_build_config"]
        assert ".github/workflows/ci.yml" in config_entries
        assert ".env.example" in config_entries
        assert "pyproject.toml" in config_entries
        assert "MANIFEST.in" in config_entries

    def test_runtime_resources_distinguished_and_paths_py_included(self, repo_root):
        """Verify runtime resources contain paths.py and other critical components."""
        manifest = generate_source_manifest(repo_root)
        runtime = manifest["runtime_package_resources"]
        assert "src/opendeal/config/paths.py" in runtime
        assert "src/opendeal/manifest.py" in runtime
        assert "src/opendeal/valuation/verified_mappings.json" in runtime
        assert "src/opendeal/ui/templates/base.html" in runtime

        # Verify egg-info and pycache are excluded
        for p in runtime.keys():
            assert "egg-info" not in p
            assert "__pycache__" not in p
            assert not p.endswith(".pyc")

    def test_active_source_manifest_file_verifies_cleanly(self, repo_root):
        """Verify audit/SOURCE_MANIFEST.json matches current working tree exactly."""
        manifest_file = repo_root / "audit" / "SOURCE_MANIFEST.json"
        assert manifest_file.exists(), "audit/SOURCE_MANIFEST.json must exist"

        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        is_valid, errors = verify_source_manifest(repo_root, manifest)
        assert is_valid, f"SOURCE_MANIFEST.json failed verification:\n" + "\n".join(errors)

    def test_missing_declared_file_fails_verification(self, repo_root):
        """Verify missing declared input triggers failure."""
        manifest = generate_source_manifest(repo_root)
        # Add non-existent file
        manifest["runtime_package_resources"]["src/opendeal/nonexistent_module.py"] = (
            "0000000000000000000000000000000000000000000000000000000000000000"
        )
        is_valid, errors = verify_source_manifest(repo_root, manifest)
        assert not is_valid
        assert any("Missing declared file: 'src/opendeal/nonexistent_module.py'" in err for err in errors)

    def test_altered_file_content_fails_verification(self, repo_root):
        """Verify altered file content triggers hash mismatch failure."""
        manifest = generate_source_manifest(repo_root)
        # Alter expected hash for pyproject.toml
        manifest["release_metadata_and_build_config"]["pyproject.toml"] = (
            "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        )
        is_valid, errors = verify_source_manifest(repo_root, manifest)
        assert not is_valid
        assert any("Checksum mismatch for 'pyproject.toml'" in err for err in errors)

    def test_path_escaping_and_absolute_paths_rejected(self):
        """Verify absolute paths and directory traversal sequences are rejected."""
        with pytest.raises(ValueError, match="Absolute or root-prefixed paths not permitted"):
            validate_relative_path("/etc/passwd")

        with pytest.raises(ValueError, match="Escaping path segments"):
            validate_relative_path("../outside.py")

        with pytest.raises(ValueError, match="Escaping path segments"):
            validate_relative_path("src/../../outside.py")

        with pytest.raises(ValueError, match="Absolute or root-prefixed paths not permitted"):
            validate_relative_path("/src/opendeal/cli.py")

    def test_source_snapshot_assembly_cleanliness(self, repo_root, tmp_path):
        """Verify clean snapshot assembly creates verified directory strictly from manifest."""
        manifest = generate_source_manifest(repo_root)
        snapshot_dir = tmp_path / "source_snapshot"

        create_verified_source_snapshot(repo_root, snapshot_dir, manifest, include_tests=False)

        # Check required files present in snapshot
        assert (snapshot_dir / "pyproject.toml").exists()
        assert (snapshot_dir / "src/opendeal/cli.py").exists()
        assert (snapshot_dir / "src/opendeal/config/paths.py").exists()
        assert (snapshot_dir / "src/opendeal/valuation/verified_mappings.json").exists()

        # Check exclusions
        assert not (snapshot_dir / ".git").exists()
        assert not (snapshot_dir / "opendeal.egg-info").exists()
        assert not (snapshot_dir / "tests").exists()
        assert not (snapshot_dir / "opendeal.db").exists()
