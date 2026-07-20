"""Shared test fixtures."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from mtd_classifier.models.base import Base
from mtd_classifier.storage.repository import Repository


@pytest.fixture
def engine():
    """Create an in-memory SQLite engine with all tables."""
    eng = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    """Create a database session for testing."""
    SessionLocal = sessionmaker(bind=engine)
    sess = SessionLocal()
    yield sess
    sess.close()


@pytest.fixture
def repo(session):
    """Create a repository backed by in-memory database."""
    return Repository(session)


@pytest.fixture(scope="session")
def bridging_templates():
    """My Tax Digital's bridging templates, downloaded to the app cache on
    first use (they are the vendor's own files and are not bundled with
    this repo). Tests that fill templates skip cleanly when offline."""
    from mtd_classifier.export.template_fetch import TemplateError, get_template

    try:
        return {form: get_template(form) for form in ("SA103", "SA105")}
    except TemplateError as e:
        pytest.skip(f"bridging templates unavailable (offline?): {e}")
