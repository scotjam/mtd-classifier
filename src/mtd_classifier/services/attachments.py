"""Store, open and remove receipt/invoice files attached to a transaction.

Files are copied into ATTACHMENTS_DIR/<raw_transaction_id>/ so the original
file the user selected can move or be deleted without breaking the link, and
so a re-import of the same statement never touches attachments (they're
keyed off the transaction's database id, not its content).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import mtd_classifier.config as config
from mtd_classifier.models.attachment import Attachment
from mtd_classifier.storage.repository import Repository

ALLOWED_EXTENSIONS: dict[str, str] = {
    ".pdf": "pdf",
    ".doc": "document",
    ".docx": "document",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".bmp": "image",
    ".tif": "image",
    ".tiff": "image",
    ".webp": "image",
    ".heic": "image",
}


class UnsupportedAttachmentType(Exception):
    """The file's extension is not a supported receipt/invoice format."""


def _transaction_dir(raw_transaction_id: int) -> Path:
    # Lazy read: activate_user() repoints ATTACHMENTS_DIR per user
    return config.ATTACHMENTS_DIR / str(raw_transaction_id)


def _unique_name(directory: Path, filename: str) -> str:
    """Disambiguate filename if one with the same name already exists."""
    candidate = Path(filename)
    stem, suffix = candidate.stem, candidate.suffix
    name = filename
    i = 2
    while (directory / name).exists():
        name = f"{stem} ({i}){suffix}"
        i += 1
    return name


def attach_file(
    repo: Repository, raw_transaction_id: int, source_path: Path
) -> Attachment:
    """Copy a receipt/invoice file and link it to a transaction.

    Raises UnsupportedAttachmentType for anything but PDF or a common image
    format, and FileNotFoundError if source_path doesn't exist.
    """
    source_path = Path(source_path)
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    ext = source_path.suffix.lower()
    content_type = ALLOWED_EXTENSIONS.get(ext)
    if content_type is None:
        raise UnsupportedAttachmentType(
            f"{ext or '(no extension)'} is not supported - use PDF, a Word "
            f"document or an image ({', '.join(sorted(ALLOWED_EXTENSIONS))})"
        )

    directory = _transaction_dir(raw_transaction_id)
    directory.mkdir(parents=True, exist_ok=True)
    stored_name = _unique_name(directory, source_path.name)
    shutil.copy2(source_path, directory / stored_name)

    attachment = repo.create_attachment(
        raw_transaction_id=raw_transaction_id,
        original_filename=source_path.name,
        stored_filename=stored_name,
        content_type=content_type,
    )
    return attachment


def attachment_path(attachment: Attachment) -> Path:
    """Where an attachment's file actually lives on disk."""
    return _transaction_dir(attachment.raw_transaction_id) / attachment.stored_filename


def open_attachment(attachment: Attachment) -> None:
    """Open an attachment with the OS default viewer for its file type."""
    path = attachment_path(attachment)
    if not path.exists():
        raise FileNotFoundError(path)
    if sys.platform == "win32":
        import os

        os.startfile(path)  # noqa: S606 - user-selected local file, not untrusted input
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def delete_attachment(repo: Repository, attachment: Attachment) -> None:
    """Remove an attachment's file and its database record."""
    path = attachment_path(attachment)
    if path.exists():
        path.unlink()
    repo.delete_attachment(attachment.id)
