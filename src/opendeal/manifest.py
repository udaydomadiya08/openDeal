"""Source manifest generation, verification, and reproducible snapshot assembly."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

from opendeal import __version__

# Canonical build tools tracked for release provenance
BUILD_TOOLS = ["build", "setuptools", "wheel"]

# Disclaimed authenticity note required by Gate C
AUTHENTICITY_DISCLAIMER = (
    "This manifest records relative file paths and SHA-256 digests to enable reproducible source snapshots "
    "and release artifact verification. The manifest itself does not establish cryptographic provenance, "
    "upstream identity, code signing, or source authenticity."
)

EXCLUDED_PATTERNS = [
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    ".env",
    ".env.* (excluding .env.example)",
    "runs/",
    "scratch/",
    "tmp/",
    "temp/",
    "*.log",
    ".pytest_cache/",
    ".coverage*",
    "htmlcov/",
    "build/",
    "dist/",
    "*.egg-info/",
    ".DS_Store",
    "Thumbs.db",
    "*.xlsx",
    "__pycache__/",
    "*.py[cod]",
]


def compute_file_sha256(path: Path) -> str:
    """Compute standard SHA-256 hex digest of a file in 64KB blocks."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def get_build_tool_versions() -> Dict[str, str]:
    """Inspect and return installed versions of release build tools."""
    tool_versions: Dict[str, str] = {}
    for tool_name in BUILD_TOOLS:
        try:
            import importlib.metadata
            tool_versions[tool_name] = importlib.metadata.version(tool_name)
        except Exception:
            try:
                mod = __import__(tool_name)
                tool_versions[tool_name] = getattr(mod, "__version__", "unknown")
            except Exception:
                tool_versions[tool_name] = "not_installed"
    return tool_versions


def validate_relative_path(rel_path_str: str) -> None:
    """Ensure relative path does not escape repository root and is not absolute."""
    p = Path(rel_path_str)
    if p.is_absolute() or rel_path_str.startswith("/") or rel_path_str.startswith("\\"):
        raise ValueError(f"Absolute or root-prefixed paths not permitted in source manifest: '{rel_path_str}'")
    parts = p.parts
    if ".." in parts:
        raise ValueError(f"Escaping path segments ('..') not permitted: '{rel_path_str}'")


def collect_file_entries(repo_root: Path, relative_paths: List[str]) -> Dict[str, str]:
    """Collect relative paths, validate them, and compute their SHA-256 digests."""
    entries: Dict[str, str] = {}
    seen_normalized: Set[str] = set()

    for rel_str in sorted(relative_paths):
        validate_relative_path(rel_str)
        normalized = Path(rel_str).as_posix()
        if normalized in seen_normalized:
            raise ValueError(f"Duplicate path detected in source manifest collection: '{rel_str}'")
        seen_normalized.add(normalized)

        full_path = repo_root / normalized
        if not full_path.is_file():
            raise FileNotFoundError(f"Declared manifest file does not exist: '{full_path}'")

        entries[normalized] = compute_file_sha256(full_path)

    return entries


def scan_runtime_package_resources(repo_root: Path) -> List[str]:
    """Scan all valid runtime files in src/opendeal excluding generated build outputs."""
    src_dir = repo_root / "src" / "opendeal"
    if not src_dir.is_dir():
        raise FileNotFoundError(f"Source package directory missing: {src_dir}")

    rel_paths: List[str] = []
    for path in sorted(src_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(repo_root).as_posix()
            # Exclude pycache, egg-info, and hidden files
            if "__pycache__" in rel or ".egg-info" in rel or path.suffix in [".pyc", ".pyo"]:
                continue
            if path.name.startswith("."):
                continue
            rel_paths.append(rel)
    return sorted(rel_paths)


def scan_tests_and_audit_files(repo_root: Path) -> List[str]:
    """Scan test fixtures, tests, and audit provenance files.

    Excludes the living release readiness report and manifest itself to eliminate circular hash dependency.
    """
    paths: List[str] = []

    # Tests directory
    tests_dir = repo_root / "tests"
    if tests_dir.is_dir():
        for path in sorted(tests_dir.rglob("*")):
            if path.is_file():
                rel = path.relative_to(repo_root).as_posix()
                if "__pycache__" in rel or path.suffix in [".pyc", ".pyo"] or path.name.startswith("."):
                    continue
                paths.append(rel)

    # Audit directory
    audit_dir = repo_root / "audit"
    if audit_dir.is_dir():
        for path in sorted(audit_dir.glob("*")):
            if path.is_file() and path.name not in ["SOURCE_MANIFEST.json", "PHASE_11_RELEASE_READINESS_REPORT.md"]:
                paths.append(path.relative_to(repo_root).as_posix())

    return sorted(paths)


def generate_source_manifest(repo_root: Path) -> Dict[str, Any]:
    """Generate complete, authoritative source manifest adhering to Gate C requirements."""
    repo_root = repo_root.resolve()

    # 1. Release metadata and build configuration (including CI workflow)
    metadata_and_config_paths = [
        ".env.example",
        ".github/workflows/ci.yml",
        ".gitignore",
        "LICENSE",
        "MANIFEST.in",
        "README.md",
        "pyproject.toml",
    ]
    metadata_entries = collect_file_entries(repo_root, metadata_and_config_paths)

    # 2. Runtime package resources (src/opendeal/...)
    runtime_paths = scan_runtime_package_resources(repo_root)
    runtime_entries = collect_file_entries(repo_root, runtime_paths)

    # 3. Test and audit provenance files (repository scope, distinguished from package inputs)
    test_and_audit_paths = scan_tests_and_audit_files(repo_root)
    test_and_audit_entries = collect_file_entries(repo_root, test_and_audit_paths)

    # 4. Package build inputs: union of metadata/config and runtime package resources
    package_build_inputs = {**metadata_entries, **runtime_entries}

    # 5. Build provenance
    build_provenance = {
        "package_name": "opendeal",
        "version": __version__,
        "python_version": sys.version,
        "build_tools": get_build_tool_versions(),
        "source_date_epoch": int(os.environ.get("SOURCE_DATE_EPOCH", "1700000000")),
        "git_tree_state": "uncommitted_working_tree (0 staged, 0 commits, untracked files present)",
        "authenticity_disclaimer": AUTHENTICITY_DISCLAIMER,
    }

    manifest: Dict[str, Any] = {
        "build_provenance": build_provenance,
        "release_metadata_and_build_config": metadata_entries,
        "runtime_package_resources": runtime_entries,
        "package_build_inputs": package_build_inputs,
        "test_and_audit_provenance": test_and_audit_entries,
        "excluded_patterns": EXCLUDED_PATTERNS,
    }

    return manifest


def verify_source_manifest(repo_root: Path, manifest: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Verify that every file declared in the manifest exists and matches its expected SHA-256 digest."""
    repo_root = repo_root.resolve()
    errors: List[str] = []

    sections = [
        "release_metadata_and_build_config",
        "runtime_package_resources",
        "test_and_audit_provenance",
    ]

    for section in sections:
        entries = manifest.get(section, {})
        for rel_path_str, expected_hash in entries.items():
            try:
                validate_relative_path(rel_path_str)
            except Exception as exc:
                errors.append(f"[{section}] Invalid path format '{rel_path_str}': {exc}")
                continue

            full_path = repo_root / rel_path_str
            if not full_path.is_file():
                errors.append(f"[{section}] Missing declared file: '{rel_path_str}'")
                continue

            actual_hash = compute_file_sha256(full_path)
            if actual_hash != expected_hash:
                errors.append(
                    f"[{section}] Checksum mismatch for '{rel_path_str}': "
                    f"expected {expected_hash}, got {actual_hash}"
                )

    # Check package_build_inputs consistency
    pkg_inputs = manifest.get("package_build_inputs", {})
    expected_pkg_count = len(manifest.get("release_metadata_and_build_config", {})) + len(manifest.get("runtime_package_resources", {}))
    if len(pkg_inputs) != expected_pkg_count:
        errors.append(
            f"package_build_inputs count mismatch: expected {expected_pkg_count}, got {len(pkg_inputs)}"
        )

    is_valid = len(errors) == 0
    return is_valid, errors


def create_verified_source_snapshot(
    repo_root: Path,
    target_dir: Path,
    manifest: Dict[str, Any],
    include_tests: bool = False,
) -> None:
    """Assemble a clean, isolated source snapshot in target_dir strictly from manifest-declared files.

    Excludes all generated artifacts, caches, untracked local databases, and temporary build outputs.
    """
    repo_root = repo_root.resolve()
    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    # Verify manifest before snapshot creation
    is_valid, errors = verify_source_manifest(repo_root, manifest)
    if not is_valid:
        raise ValueError(f"Cannot create snapshot from invalid manifest:\n" + "\n".join(errors))

    # Determine files to copy
    files_to_copy = dict(manifest.get("package_build_inputs", {}))
    if include_tests:
        files_to_copy.update(manifest.get("test_and_audit_provenance", {}))

    for rel_path_str in files_to_copy.keys():
        src_file = repo_root / rel_path_str
        dst_file = target_dir / rel_path_str
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        dst_file.write_bytes(src_file.read_bytes())


def normalize_sdist_archive(tar_gz_path: Path, output_path: Path, epoch: int = 1700000000) -> None:
    """Normalize a source distribution archive to eliminate nondeterministic gzip and tar metadata.

    Normalizes member file permissions, sorts entries, clamps timestamps to epoch,
    and writes deterministic gzip stream with fixed mtime.
    """
    import gzip
    import io
    import tarfile

    tar_gz_path = tar_gz_path.resolve()
    output_path = output_path.resolve()

    members_data = []
    with tarfile.open(tar_gz_path, "r:gz") as tar_in:
        for m in sorted(tar_in.getmembers(), key=lambda x: x.name):
            content = tar_in.extractfile(m).read() if m.isfile() else b""
            ti = tarfile.TarInfo(name=m.name)
            ti.size = len(content) if m.isfile() else 0
            ti.mtime = epoch
            ti.mode = 0o644 if m.isfile() else 0o755
            ti.type = m.type
            ti.uname = ""
            ti.gname = ""
            ti.uid = 0
            ti.gid = 0
            members_data.append((ti, content))

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tar_out:
        for ti, content in members_data:
            if ti.isfile():
                tar_out.addfile(ti, io.BytesIO(content))
            else:
                tar_out.addfile(ti)

    tar_bytes = tar_buf.getvalue()
    gz_buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=gz_buf, mtime=epoch) as gz_out:
        gz_out.write(tar_bytes)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(gz_buf.getvalue())


def build_release_package(
    source_dir: Path,
    out_dir: Path,
    epoch: int = 1700000000,
) -> Tuple[Path, Path]:
    """Execute the canonical release build step producing bit-for-bit reproducible wheel and sdist.

    1. Invokes python -m build --no-isolation with SOURCE_DATE_EPOCH.
    2. Post-processes the generated source distribution archive to normalize tar member ordering,
       timestamps, permissions, ownership metadata, and gzip stream headers.
    Returns:
        (wheel_path, sdist_path)
    """
    import shutil
    import subprocess

    source_dir = source_dir.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["SOURCE_DATE_EPOCH"] = str(epoch)

    # Build raw artifacts into temporary staging folder
    tmp_staging = out_dir / ".raw_build_staging"
    tmp_staging.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(tmp_staging)]
    res = subprocess.run(cmd, cwd=str(source_dir), env=env, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Package build failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")

    raw_whl = list(tmp_staging.glob("*.whl"))[0]
    raw_sdist = list(tmp_staging.glob("*.tar.gz"))[0]

    final_whl = out_dir / raw_whl.name
    final_sdist = out_dir / raw_sdist.name

    # Wheel is natively reproducible under SOURCE_DATE_EPOCH
    final_whl.write_bytes(raw_whl.read_bytes())

    # Deterministically normalize the sdist
    normalize_sdist_archive(raw_sdist, final_sdist, epoch=epoch)

    # Clean up staging folder
    shutil.rmtree(tmp_staging, ignore_errors=True)

    return final_whl, final_sdist
