"""CLI user-awareness tests: selection, --user, isolation, shell session."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import mtd_classifier.cli as cli_module
import mtd_classifier.config as config
from mtd_classifier.cli import app
from mtd_classifier.importers import column_mapping
from mtd_classifier.storage.database import reset_engine

runner = CliRunner()

STMT = "Date,Description,Amount,Balance\n15/04/2026,ACME,-10.00,990.00\n"


@pytest.fixture(autouse=True)
def isolated_users(tmp_path, monkeypatch):
    """Real (temp) APP_DIR with real per-user databases - no DB mocking."""
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mtd.db")
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{tmp_path / 'mtd.db'}")
    monkeypatch.setattr(config, "ATTACHMENTS_DIR", tmp_path / "attachments")
    monkeypatch.setattr(config, "ACTIVE_USER", None)
    monkeypatch.setattr(column_mapping, "MAPPINGS_PATH", tmp_path / "column_mappings.json")
    monkeypatch.setattr(cli_module, "_user_resolved", False)
    reset_engine()
    yield
    reset_engine()


def _write_stmt(tmp_path) -> Path:
    f = tmp_path / "stmt.csv"
    f.write_text(STMT)
    return f


def test_single_user_auto_selected_without_prompt(tmp_path):
    f = _write_stmt(tmp_path)
    result = runner.invoke(app, ["import-file", str(f), "--account", "Acc"])
    assert result.exit_code == 0, result.stdout
    assert "Select user" not in result.stdout
    assert (tmp_path / "mtd.db").exists()  # Default user's data in APP_DIR


def test_multiple_users_prompt_and_isolation(tmp_path):
    config.add_user("James")
    config.add_user("Carla")

    f = _write_stmt(tmp_path)
    # Interactive selection by number: 2 = James
    result = runner.invoke(
        app, ["import-file", str(f), "--account", "Acc"], input="2\n",
    )
    assert result.exit_code == 0, result.stdout
    assert "Select user" in result.stdout
    assert "Active user: James" in result.stdout
    assert (tmp_path / "users" / "james" / "mtd.db").exists()
    assert not (tmp_path / "users" / "carla" / "mtd.db").exists()


def test_user_option_skips_prompt(tmp_path):
    config.add_user("James")
    config.add_user("Carla")

    f = _write_stmt(tmp_path)
    result = runner.invoke(
        app, ["--user", "carla", "import-file", str(f), "--account", "Acc"],
    )
    assert result.exit_code == 0, result.stdout
    assert "Select user" not in result.stdout
    assert (tmp_path / "users" / "carla" / "mtd.db").exists()


def test_unknown_user_rejected(tmp_path):
    result = runner.invoke(app, ["--user", "nobody", "users", "list"])
    assert result.exit_code == 1
    assert "Unknown user" in result.stdout


def test_unattended_run_with_multiple_users_aborts(tmp_path):
    config.add_user("James")
    f = _write_stmt(tmp_path)
    # No input available -> must refuse rather than guess whose data
    result = runner.invoke(app, ["import-file", str(f), "--account", "Acc"])
    assert result.exit_code == 1
    assert "MTD_USER" in result.stdout


def test_users_management_commands(tmp_path):
    result = runner.invoke(app, ["users", "add", "Carla"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["users", "rename", "Carla", "Carla B"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["users", "list"])
    assert "Default" in result.stdout and "Carla B" in result.stdout


def test_shell_selects_once_and_runs_commands(tmp_path):
    config.add_user("James")
    config.add_user("Carla")
    # Session: select James (2), run 'users list', exit. No re-prompt
    # between commands - the session's user is known from launch.
    result = runner.invoke(app, ["shell"], input="2\nusers list\nexit\n")
    assert result.exit_code == 0, result.stdout
    assert result.stdout.count("Select user") == 1
    assert "Continuous session as James" in result.stdout
    assert "Default (last used)" in result.stdout  # users list output... last_user is James now
    assert "mtd [James]>" in result.stdout


def test_shell_switch_user(tmp_path):
    config.add_user("James")
    config.add_user("Carla")
    result = runner.invoke(
        app, ["shell"], input="2\nswitch-user\n3\nexit\n",
    )
    assert result.exit_code == 0, result.stdout
    assert "Continuous session as James" in result.stdout
    assert "mtd [Carla]>" in result.stdout
