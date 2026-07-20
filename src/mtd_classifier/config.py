"""Application configuration, constants and the multi-user registry.

The app can host accounts for multiple users (e.g. two people in a
household each doing their own tax return). Each user gets an isolated
data directory - database, attachments, saved account tabs and column
mappings - so nothing can leak between two people's tax figures.

``users.json`` in APP_DIR lists the users. The first ("Default") user
points at APP_DIR itself, so a database that predates multi-user support
simply belongs to the first user with no file moves. Further users live
under APP_DIR/users/<slug>/.

The module-level path constants below always describe the ACTIVE user and
are mutated by ``activate_user``. Consumers must read them lazily
(``config.DB_URL`` at call time) - a ``from config import DB_URL`` copy
taken at import time would go stale on user switch.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

APP_DIR = Path.home() / ".mtd-classifier"

# Paths for the ACTIVE user - see module docstring. Defaults describe the
# legacy single-user layout (the "Default" user).
DB_PATH = APP_DIR / "mtd.db"
DB_URL = f"sqlite:///{DB_PATH}"
ATTACHMENTS_DIR = APP_DIR / "attachments"
ACTIVE_USER: str | None = None


def ensure_app_dir() -> Path:
    """Create the application directory if it doesn't exist."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    return APP_DIR


def _users_file() -> Path:
    return APP_DIR / "users.json"


def _load_registry() -> dict:
    path = _users_file()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("users"), list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    # First run (or unreadable registry): the pre-multi-user data in
    # APP_DIR itself becomes the first user's.
    return {"users": [{"name": "Default", "dir": "."}], "last_user": "Default"}


def _save_registry(registry: dict) -> None:
    ensure_app_dir()
    _users_file().write_text(json.dumps(registry, indent=2), encoding="utf-8")


def list_users() -> list[str]:
    """User names, registry order. Creates the registry on first call."""
    registry = _load_registry()
    if not _users_file().exists():
        _save_registry(registry)
    return [u["name"] for u in registry["users"]]


def last_user() -> str | None:
    return _load_registry().get("last_user")


def user_data_dir(name: str) -> Path:
    """The data directory for a user (absolute)."""
    for entry in _load_registry()["users"]:
        if entry["name"] == name:
            return (APP_DIR / entry["dir"]).resolve()
    raise KeyError(f"Unknown user: {name}")


def add_user(name: str) -> None:
    """Register a new user with a fresh, isolated data directory."""
    name = name.strip()
    if not name:
        raise ValueError("User name cannot be empty")
    registry = _load_registry()
    if any(u["name"].lower() == name.lower() for u in registry["users"]):
        raise ValueError(f"User {name!r} already exists")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "user"
    directory = f"users/{slug}"
    n = 2
    while any(u["dir"] == directory for u in registry["users"]):
        directory = f"users/{slug}-{n}"
        n += 1
    registry["users"].append({"name": name, "dir": directory})
    _save_registry(registry)


def rename_user(old: str, new: str) -> None:
    """Rename a user (the data directory stays put)."""
    new = new.strip()
    if not new:
        raise ValueError("User name cannot be empty")
    registry = _load_registry()
    if any(u["name"].lower() == new.lower() for u in registry["users"]):
        raise ValueError(f"User {new!r} already exists")
    for entry in registry["users"]:
        if entry["name"] == old:
            entry["name"] = new
            if registry.get("last_user") == old:
                registry["last_user"] = new
            _save_registry(registry)
            return
    raise KeyError(f"Unknown user: {old}")


def activate_user(name: str) -> None:
    """Point every per-user path at this user's data directory.

    Must be called before get_engine()/init_db() for the paths to take
    effect; switching users at runtime requires resetting the engine (done
    here) and rebuilding anything that holds an open session.
    """
    global DB_PATH, DB_URL, ATTACHMENTS_DIR, ACTIVE_USER

    directory = user_data_dir(name)
    directory.mkdir(parents=True, exist_ok=True)

    DB_PATH = directory / "mtd.db"
    DB_URL = f"sqlite:///{DB_PATH}"
    ATTACHMENTS_DIR = directory / "attachments"
    ACTIVE_USER = name

    # Late imports: these modules import config themselves.
    from mtd_classifier.importers import column_mapping
    from mtd_classifier.storage.database import reset_engine

    column_mapping.MAPPINGS_PATH = directory / "column_mappings.json"
    reset_engine()

    registry = _load_registry()
    registry["last_user"] = name
    _save_registry(registry)


def active_user_dir() -> Path:
    """Data directory of the currently active user."""
    return DB_PATH.parent
