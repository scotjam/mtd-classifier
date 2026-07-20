"""Tests for the multi-user registry and per-user data isolation."""

from datetime import date
from pathlib import Path

import pytest

import mtd_classifier.config as config
from mtd_classifier.importers import column_mapping
from mtd_classifier.storage.database import get_engine, get_session, init_db, reset_engine
from mtd_classifier.storage.repository import Repository
from mtd_classifier.services.import_service import import_statement


@pytest.fixture(autouse=True)
def isolated_app_dir(tmp_path, monkeypatch):
    """Point APP_DIR (and the derived active-user paths) at a temp dir."""
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mtd.db")
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{tmp_path / 'mtd.db'}")
    monkeypatch.setattr(config, "ATTACHMENTS_DIR", tmp_path / "attachments")
    monkeypatch.setattr(config, "ACTIVE_USER", None)
    monkeypatch.setattr(column_mapping, "MAPPINGS_PATH", tmp_path / "column_mappings.json")
    reset_engine()
    yield
    reset_engine()


def test_registry_starts_with_default_user_owning_legacy_dir(tmp_path):
    assert config.list_users() == ["Default"]
    assert config.user_data_dir("Default") == tmp_path.resolve()
    assert (tmp_path / "users.json").exists()


def test_add_user_gets_isolated_directory(tmp_path):
    config.add_user("Carla")
    assert config.list_users() == ["Default", "Carla"]
    assert config.user_data_dir("Carla") == (tmp_path / "users" / "carla").resolve()


def test_add_duplicate_user_rejected():
    config.add_user("Carla")
    with pytest.raises(ValueError):
        config.add_user("carla")  # case-insensitive


def test_rename_user_keeps_directory(tmp_path):
    config.add_user("Carla")
    config.rename_user("Carla", "Carla B")
    assert "Carla B" in config.list_users()
    assert config.user_data_dir("Carla B") == (tmp_path / "users" / "carla").resolve()


def test_activate_user_switches_all_paths(tmp_path):
    config.add_user("Carla")
    config.activate_user("Carla")
    user_dir = (tmp_path / "users" / "carla").resolve()
    assert config.DB_PATH == user_dir / "mtd.db"
    assert config.ATTACHMENTS_DIR == user_dir / "attachments"
    assert column_mapping.MAPPINGS_PATH == user_dir / "column_mappings.json"
    assert config.ACTIVE_USER == "Carla"
    assert config.last_user() == "Carla"
    assert user_dir.exists()


def test_users_have_isolated_databases(tmp_path):
    """A transaction imported for one user must be invisible to another."""
    csv = tmp_path / "stmt.csv"
    csv.write_text(
        "Date,Description,Amount,Balance\n15/04/2026,ACME,-10.00,990.00\n"
    )

    config.add_user("James")
    config.add_user("Carla")

    config.activate_user("James")
    engine = get_engine()
    init_db(engine)
    repo = Repository(get_session(engine))
    import_statement(repo, csv, account="Acc")
    repo.session.commit()
    assert repo.count_transactions() == 1
    repo.session.close()

    config.activate_user("Carla")
    engine2 = get_engine()
    assert str(engine2.url) != str(engine.url)
    init_db(engine2)
    repo2 = Repository(get_session(engine2))
    assert repo2.count_transactions() == 0

    # And James still has his transaction on switching back
    config.activate_user("James")
    engine3 = get_engine()
    init_db(engine3)
    repo3 = Repository(get_session(engine3))
    assert repo3.count_transactions() == 1
