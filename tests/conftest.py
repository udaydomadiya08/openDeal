"""Global pytest fixtures and environment isolation."""

from __future__ import annotations

import os
from pathlib import Path
import pytest


@pytest.fixture(autouse=True, scope="session")
def isolate_test_environment(tmp_path_factory):
    """Ensure all test runs use isolated application data and database locations.

    Guarantees no tests ever touch the user's real home directory or real databases.
    """
    temp_dir = tmp_path_factory.mktemp("opendeal_test_isolation")
    app_data = temp_dir / "app_data"
    app_data.mkdir()

    old_data_dir = os.environ.get("OPENDEAL_DATA_DIR")
    old_db_path = os.environ.get("OPENDEAL_DATABASE_PATH")

    os.environ["OPENDEAL_DATA_DIR"] = str(app_data)
    os.environ.setdefault("OPENDEAL_DATABASE_PATH", str(app_data / "test_session.db"))

    yield

    if old_data_dir is not None:
        os.environ["OPENDEAL_DATA_DIR"] = old_data_dir
    else:
        os.environ.pop("OPENDEAL_DATA_DIR", None)

    if old_db_path is not None:
        os.environ["OPENDEAL_DATABASE_PATH"] = old_db_path
    else:
        os.environ.pop("OPENDEAL_DATABASE_PATH", None)
