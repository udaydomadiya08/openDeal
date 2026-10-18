"""Repeatable offline release artifact reproducibility verification harness.

Usage:
    python3 audit/verify_reproducibility.py
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
from typing import Any, Dict, List, Tuple
import zipfile

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root / "src") not in sys.path:
    sys.path.insert(0, str(repo_root / "src"))

from opendeal.manifest import (
    build_release_package,
    create_verified_source_snapshot,
    generate_source_manifest,
    get_build_tool_versions,
    verify_source_manifest,
)


def compute_hash(path: Path) -> str:
    """Compute standard SHA-256 hex digest of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_wheel(whl_path: Path) -> Tuple[List[str], List[str]]:
    """Inspect wheel members and verify absence of forbidden files."""
    violations: List[str] = []
    with zipfile.ZipFile(whl_path, "r") as zf:
        names = zf.namelist()
        for name in names:
            if name.endswith(".xlsx"):
                violations.append(f"Forbidden workbook: {name}")
            if name.endswith(".db") or name.endswith(".sqlite"):
                violations.append(f"Forbidden database: {name}")
            if ".env" in name and not name.endswith(".env.example"):
                violations.append(f"Forbidden environment file: {name}")
            if name.startswith("tests/"):
                violations.append(f"Forbidden test file: {name}")
    return sorted(names), violations


def inspect_sdist_members(sdist_path: Path) -> Tuple[List[str], Dict[str, Tuple[int, int, int, str]]]:
    """Extract and record member paths, order, size, mode, mtime, and content SHA-256."""
    member_names_ordered: List[str] = []
    members_info: Dict[str, Tuple[int, int, int, str]] = {}

    with tarfile.open(sdist_path, "r:gz") as tar:
        for m in tar.getmembers():
            member_names_ordered.append(m.name)
            if m.isfile():
                content = tar.extractfile(m).read()  # type: ignore
                content_hash = hashlib.sha256(content).hexdigest()
                members_info[m.name] = (m.size, m.mode, m.mtime, content_hash)
            else:
                members_info[m.name] = (0, m.mode, m.mtime, "DIRECTORY")

    return member_names_ordered, members_info


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    manifest_path = repo_root / "audit" / "SOURCE_MANIFEST.json"

    print("================================================================================")
    print("OPENDEAL RELEASE CANDIDATE REPRODUCIBILITY VERIFICATION")
    print("================================================================================")

    # 1. Environment & Build Tools
    tools = get_build_tool_versions()
    py_version = sys.version.replace("\n", " ")
    epoch = 1700000000
    print(f"Python Version:       {py_version}")
    print(f"Build Tool Versions:  build={tools.get('build')}, setuptools={tools.get('setuptools')}, wheel={tools.get('wheel')}")
    print(f"Fixed Epoch:          SOURCE_DATE_EPOCH={epoch}")

    # 2. Source Manifest Check
    if not manifest_path.exists():
        print(f"ERROR: Manifest missing at {manifest_path}")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_hash = compute_hash(manifest_path)
    is_valid, errors = verify_source_manifest(repo_root, manifest)
    if not is_valid:
        print("ERROR: Source manifest verification failed against working tree:")
        for err in errors:
            print(f"  - {err}")
        return 1
    print(f"Source Manifest:      VALID ({manifest_hash[:16]}...)")
    print(f"Declared Inputs:      {len(manifest.get('package_build_inputs', {}))} package inputs, {len(manifest.get('test_and_audit_provenance', {}))} repo provenance files")

    # 3. Two-Build Reproducibility
    with tempfile.TemporaryDirectory(prefix="opendeal_repro_") as tmp_dir:
        tmp = Path(tmp_dir)
        snap_a = tmp / "snapshot_A"
        snap_b = tmp / "snapshot_B"
        dist_a = tmp / "dist_A"
        dist_b = tmp / "dist_B"
        dist_a.mkdir()
        dist_b.mkdir()

        print("\n[Step 1/4] Assembling equivalent manifest-verified source snapshots...")
        create_verified_source_snapshot(repo_root, snap_a, manifest, include_tests=False)
        create_verified_source_snapshot(repo_root, snap_b, manifest, include_tests=False)

        print("[Step 2/4] Executing independent canonical Build A...")
        whl_a, sdist_a = build_release_package(snap_a, dist_a, epoch=epoch)

        print("[Step 3/4] Executing independent canonical Build B...")
        whl_b, sdist_b = build_release_package(snap_b, dist_b, epoch=epoch)

        print("[Step 4/4] Analyzing release artifact comparisons...")

        hash_whl_a = compute_hash(whl_a)
        hash_whl_b = compute_hash(whl_b)
        hash_sdist_a = compute_hash(sdist_a)
        hash_sdist_b = compute_hash(sdist_b)

        # Compare Wheels
        whl_match = (hash_whl_a == hash_whl_b)
        whl_names, whl_violations = inspect_wheel(whl_a)

        # Compare Sdist Members
        order_a, members_a = inspect_sdist_members(sdist_a)
        order_b, members_b = inspect_sdist_members(sdist_b)

        member_order_match = (order_a == order_b)
        content_diffs: List[str] = []
        metadata_diffs: List[str] = []

        for name, info_a in members_a.items():
            info_b = members_b.get(name)
            if info_b is None:
                content_diffs.append(f"Missing in B: {name}")
            else:
                if info_a[3] != info_b[3]:
                    content_diffs.append(f"Content hash mismatch in {name}: {info_a[3]} vs {info_b[3]}")
                if (info_a[0], info_a[1], info_a[2]) != (info_b[0], info_b[1], info_b[2]):
                    metadata_diffs.append(f"Metadata mismatch in {name}: {info_a[:3]} vs {info_b[:3]}")

        sdist_match = (hash_sdist_a == hash_sdist_b)

        print("\n--- WHEEL REPRODUCIBILITY RESULTS ---")
        print(f"Artifact Name:        {whl_a.name}")
        print(f"Build A SHA-256:      {hash_whl_a} ({whl_a.stat().st_size:,} bytes)")
        print(f"Build B SHA-256:      {hash_whl_b} ({whl_b.stat().st_size:,} bytes)")
        print(f"Byte-for-Byte Match:  {whl_match}")
        print(f"Archive Member Count: {len(whl_names)}")
        print(f"Hygiene Violations:   {len(whl_violations)}")
        if whl_violations:
            for v in whl_violations:
                print(f"  - {v}")

        print("\n--- SDIST REPRODUCIBILITY RESULTS ---")
        print(f"Artifact Name:        {sdist_a.name}")
        print(f"Build A SHA-256:      {hash_sdist_a} ({sdist_a.stat().st_size:,} bytes)")
        print(f"Build B SHA-256:      {hash_sdist_b} ({sdist_b.stat().st_size:,} bytes)")
        print(f"Final Tar.gz Match:   {sdist_match}")
        print(f"Archive Member Count: {len(order_a)}")
        print(f"Member Order Match:   {member_order_match}")
        print(f"Metadata Diffs Count: {len(metadata_diffs)}")
        print(f"Content Diffs Count:  {len(content_diffs)}")

        print("\n================================================================================")
        if whl_match and sdist_match and member_order_match and len(content_diffs) == 0 and len(metadata_diffs) == 0 and len(whl_violations) == 0:
            print("REPRODUCIBILITY CERTIFIED: Both Wheel AND Source Distribution (.tar.gz)")
            print("are 100% byte-for-byte identical across independent builds.")
            print("================================================================================")
            return 0
        else:
            print("REPRODUCIBILITY FAILED: Discrepancies detected between independent builds.")
            if not whl_match:
                print(f"  - Wheel SHA-256 mismatch: {hash_whl_a} != {hash_whl_b}")
            if not sdist_match:
                print(f"  - Sdist SHA-256 mismatch: {hash_sdist_a} != {hash_sdist_b}")
            if not member_order_match:
                print("  - Sdist member order mismatch")
            if content_diffs:
                print(f"  - Sdist content diffs: {content_diffs}")
            if metadata_diffs:
                print(f"  - Sdist metadata diffs: {metadata_diffs}")
            print("================================================================================")
            return 1


if __name__ == "__main__":
    sys.exit(main())
