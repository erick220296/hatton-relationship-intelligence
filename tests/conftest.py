import os

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "db: needs the running Postgres container")


def pytest_collection_modifyitems(config, items):
    if os.getenv("DATABASE_URL", "").find("@db:") == -1:
        skip = pytest.mark.skip(reason="no database container")
        for it in items:
            if "db" in it.keywords:
                it.add_marker(skip)
