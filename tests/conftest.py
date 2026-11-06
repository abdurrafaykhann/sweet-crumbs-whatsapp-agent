"""Test database: a fresh seeded database per session. Set TEST_DATABASE_URL to run against PostgreSQL."""
import os
import tempfile

import pytest

from app import database, seed
from app import models as m


@pytest.fixture(scope="session")
def seeded():
    url = os.environ.get("TEST_DATABASE_URL") or "sqlite:///" + os.path.join(tempfile.mkdtemp(), "sc.db").replace("\\", "/")
    database.reset_engine(url)
    report = seed.seed_database()
    yield report


@pytest.fixture()
def session(seeded):
    with database.session_scope() as s:
        yield s
        s.rollback()


@pytest.fixture(scope="session")
def areas(seeded):
    from app import repo
    with database.session_scope() as s:
        return repo.areas_as_dicts(s)


def variant(s, sku, label="each"):
    return s.query(m.Variant).join(m.Product).filter(m.Product.sku == sku, m.Variant.label == label).one()


@pytest.fixture()
def db(seeded):
    """A freshly seeded database for tests that change data (orders, stock, drafts)."""
    seed.seed_database()
    yield
