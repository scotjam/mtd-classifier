"""Tests for receipt/invoice attachments: repository, storage service, CLI."""

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mtd_classifier.models.base import Base
from mtd_classifier.storage.repository import Repository
from mtd_classifier.services.attachments import (
    ALLOWED_EXTENSIONS,
    UnsupportedAttachmentType,
    attach_file,
    attachment_path,
    delete_attachment,
    open_attachment,
)


@pytest.fixture
def repo():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield Repository(session)
    session.close()


@pytest.fixture
def txn(repo):
    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
    )
    return repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 4, 20),
        description="PLUMBER J SMITH", amount=-180.0, dedup_hash="attach_txn_1",
    )


@pytest.fixture
def attachments_dir(tmp_path):
    with patch("mtd_classifier.config.ATTACHMENTS_DIR", tmp_path / "attachments"):
        yield tmp_path / "attachments"


class TestAttachFile:
    def test_attach_pdf_copies_file_and_creates_record(self, repo, txn, attachments_dir, tmp_path):
        source = tmp_path / "receipt.pdf"
        source.write_bytes(b"%PDF-1.4 fake receipt")

        attachment = attach_file(repo, txn.id, source)
        repo.commit()

        assert attachment.original_filename == "receipt.pdf"
        assert attachment.content_type == "pdf"
        stored = attachment_path(attachment)
        assert stored.exists()
        assert stored.read_bytes() == b"%PDF-1.4 fake receipt"
        # Original file untouched (copied, not moved)
        assert source.exists()

    def test_attach_image_formats(self, repo, txn, attachments_dir, tmp_path):
        for ext in (".png", ".jpg", ".jpeg", ".heic"):
            source = tmp_path / f"receipt{ext}"
            source.write_bytes(b"fake image data")
            attachment = attach_file(repo, txn.id, source)
            assert attachment.content_type == "image"
        repo.commit()

    def test_unsupported_extension_rejected(self, repo, txn, attachments_dir, tmp_path):
        source = tmp_path / "receipt.txt"
        source.write_bytes(b"not a receipt format")
        with pytest.raises(UnsupportedAttachmentType):
            attach_file(repo, txn.id, source)
        assert repo.get_attachments_for_transaction(txn.id) == []

    def test_word_documents_accepted(self, repo, txn, attachments_dir, tmp_path):
        source = tmp_path / "invoice.docx"
        source.write_bytes(b"PK fake docx")
        attachment = attach_file(repo, txn.id, source)
        assert attachment.content_type == "document"

    def test_missing_source_file_raises(self, repo, txn, attachments_dir, tmp_path):
        with pytest.raises(FileNotFoundError):
            attach_file(repo, txn.id, tmp_path / "does_not_exist.pdf")

    def test_duplicate_filename_disambiguated(self, repo, txn, attachments_dir, tmp_path):
        source = tmp_path / "receipt.pdf"
        source.write_bytes(b"version 1")
        first = attach_file(repo, txn.id, source)

        source.write_bytes(b"version 2")
        second = attach_file(repo, txn.id, source)
        repo.commit()

        assert first.stored_filename != second.stored_filename
        assert attachment_path(first).read_bytes() == b"version 1"
        assert attachment_path(second).read_bytes() == b"version 2"

    def test_two_transactions_dont_collide(self, repo, attachments_dir, tmp_path):
        log = repo.create_import_log(
            filename="s.csv", file_hash="h2", bank="firstdirect", fmt="csv", record_count=1
        )
        txn2 = repo.create_raw_transaction(
            import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 4, 21),
            description="OTHER", amount=-50.0, dedup_hash="attach_txn_2",
        )
        log2 = repo.create_import_log(
            filename="s2.csv", file_hash="h3", bank="firstdirect", fmt="csv", record_count=1
        )
        txn1 = repo.create_raw_transaction(
            import_log_id=log2.id, bank="firstdirect", txn_date=date(2026, 4, 20),
            description="ONE", amount=-10.0, dedup_hash="attach_txn_3",
        )
        source = tmp_path / "receipt.pdf"
        source.write_bytes(b"content")
        a1 = attach_file(repo, txn1.id, source)
        a2 = attach_file(repo, txn2.id, source)
        repo.commit()
        assert attachment_path(a1) != attachment_path(a2)
        assert attachment_path(a1).exists() and attachment_path(a2).exists()


class TestRepositoryQueries:
    def test_get_attachments_for_transaction(self, repo, txn, attachments_dir, tmp_path):
        source = tmp_path / "a.pdf"
        source.write_bytes(b"x")
        attach_file(repo, txn.id, source)
        repo.commit()
        results = repo.get_attachments_for_transaction(txn.id)
        assert len(results) == 1
        assert results[0].original_filename == "a.pdf"

    def test_count_attachments_for_transactions(self, repo, txn, attachments_dir, tmp_path):
        source = tmp_path / "a.pdf"
        source.write_bytes(b"x")
        attach_file(repo, txn.id, source)
        source2 = tmp_path / "b.pdf"
        source2.write_bytes(b"y")
        attach_file(repo, txn.id, source2)
        repo.commit()
        counts = repo.count_attachments_for_transactions([txn.id, 9999])
        assert counts == {txn.id: 2}


class TestDeleteAttachment:
    def test_delete_removes_file_and_record(self, repo, txn, attachments_dir, tmp_path):
        source = tmp_path / "receipt.pdf"
        source.write_bytes(b"content")
        attachment = attach_file(repo, txn.id, source)
        repo.commit()
        path = attachment_path(attachment)
        assert path.exists()

        delete_attachment(repo, attachment)
        repo.commit()
        assert not path.exists()
        assert repo.get_attachment(attachment.id) is None


class TestOpenAttachment:
    def test_open_missing_file_raises(self, repo, txn, attachments_dir, tmp_path):
        source = tmp_path / "receipt.pdf"
        source.write_bytes(b"content")
        attachment = attach_file(repo, txn.id, source)
        repo.commit()
        attachment_path(attachment).unlink()
        with pytest.raises(FileNotFoundError):
            open_attachment(attachment)

    def test_open_calls_platform_opener(self, repo, txn, attachments_dir, tmp_path):
        source = tmp_path / "receipt.pdf"
        source.write_bytes(b"content")
        attachment = attach_file(repo, txn.id, source)
        repo.commit()
        with patch("mtd_classifier.services.attachments.sys.platform", "linux"), \
             patch("mtd_classifier.services.attachments.subprocess.run") as run:
            open_attachment(attachment)
        run.assert_called_once()
        assert run.call_args[0][0][0] == "xdg-open"
