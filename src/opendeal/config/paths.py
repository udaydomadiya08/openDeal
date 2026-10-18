"""Authoritative filesystem paths and operating-system conventions for OpenDeal."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Optional


def get_default_data_dir() -> Path:
    """Return the stable, per-user application data directory following OS standards.

    Resolution Precedence for base directory:
    1. OPENDEAL_DATA_DIR environment variable (if non-empty).
    2. macOS: ~/Library/Application Support/OpenDeal
    3. Windows: %APPDATA%/OpenDeal (or ~/AppData/Roaming/OpenDeal)
    4. Linux/POSIX: ${XDG_DATA_HOME:-~/.local/share}/opendeal
    """
    override = os.environ.get("OPENDEAL_DATA_DIR")
    if override is not None and override.strip():
        return Path(override.strip()).expanduser()

    home = Path.home()

    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "OpenDeal"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata is not None and appdata.strip():
            return Path(appdata.strip()) / "OpenDeal"
        return home / "AppData" / "Roaming" / "OpenDeal"
    else:
        # Linux and other UNIX standards (XDG Base Directory specification)
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data is not None and xdg_data.strip():
            return Path(xdg_data.strip()) / "opendeal"
        return home / ".local" / "share" / "opendeal"


def get_default_database_path() -> Path:
    """Return the canonical, stable, per-user default database path."""
    return get_default_data_dir() / "opendeal.db"


def is_default_database_path(path_str: str) -> bool:
    """Check if the given path string points to the default per-user database path."""
    try:
        candidate = Path(path_str).expanduser().resolve()
        default_p = get_default_database_path().expanduser().resolve()
        return candidate == default_p
    except Exception:
        return False


def resolve_database_path(explicit_path: Optional[str] = None) -> str:
    """Authoritative database path resolver across CLI, UI, and Storage.

    Enforces the mandatory precedence policy:
    1. Explicit database path supplied by the caller (if non-empty).
    2. OPENDEAL_DATABASE_PATH environment variable (if non-empty).
    3. Configured storage database_path from settings/environment (if explicitly set and non-empty).
    4. Stable, documented, per-user application-data location:
       - macOS: ~/Library/Application Support/OpenDeal/opendeal.db
       - Linux: ~/.local/share/opendeal/opendeal.db
       - Windows: %APPDATA%/OpenDeal/opendeal.db
    """
    if explicit_path is not None and str(explicit_path).strip():
        return str(explicit_path).strip()

    env_path = os.environ.get("OPENDEAL_DATABASE_PATH")
    if env_path is not None and env_path.strip():
        return env_path.strip()

    # Import lazily to avoid circular imports during bootstrap
    try:
        from opendeal.config.settings import settings
        if settings.storage.database_path and settings.storage.database_path.strip():
            return settings.storage.database_path.strip()
    except Exception:
        pass

    return str(get_default_database_path())
