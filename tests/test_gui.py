"""GUI tests: real Tk widgets, dialogs patched out. Skipped without a display."""

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

tkinter = pytest.importorskip("tkinter")
from tkinter import ttk

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mtd_classifier.models.base import Base
from mtd_classifier.storage.repository import Repository

FIXTURES = Path(__file__).parent / "fixtures"


def _all_tree_rows(tree):
    """Flatten a (possibly hierarchical) Treeview into (label, values) tuples."""
    def walk(item):
        yield item
        for child in tree.get_children(item):
            yield from walk(child)

    rows = []
    for top in tree.get_children(""):
        for item in walk(top):
            rows.append((tree.item(item, "text"), tree.item(item, "values")))
    return rows


@pytest.fixture(autouse=True)
def isolated_app_dir_files(tmp_path):
    """Keep tests away from the user's real accounts file and tour marker,
    and suppress the interactive coverage-confirmation dialog (a modal
    Toplevel that would block the test run; None keeps the estimate)."""
    from mtd_classifier.gui import app as app_module
    from mtd_classifier.gui import walkthrough as wt

    with patch.object(app_module, "ACCOUNTS_FILE", tmp_path / "accounts.json"), \
         patch.object(wt, "WALKTHROUGH_MARKER", tmp_path / "walkthrough_done"), \
         patch.object(app_module, "prompt_confirm_period", lambda *a, **k: None):
        yield


@pytest.fixture
def repo():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield Repository(session)
    session.close()


@pytest.fixture
def seeded_repo(repo):
    log = repo.create_import_log(
        filename="seed.csv", file_hash="gui_seed", bank="firstdirect", fmt="csv",
        record_count=3,
    )
    rows = [
        ("First Direct Current", date(2026, 4, 15), "INVOICE PAID", 2500.00),
        ("First Direct Current", date(2026, 5, 1), "PLUMBER J SMITH", -180.00),
        ("First Direct Current", date(2026, 5, 9), "PLUMBER REFUND", 180.00),
        ("Santander Rental", date(2026, 4, 30), "RENT RECEIVED", 950.00),
    ]
    ids = []
    for i, (account, d, desc, amount) in enumerate(rows):
        txn = repo.create_raw_transaction(
            import_log_id=log.id,
            bank="firstdirect" if "First" in account else "santander",
            txn_date=d,
            description=desc,
            amount=amount,
            dedup_hash=f"gui_hash_{i}",
            account=account,
            raw_data=f"raw line {i}: {desc}",
        )
        ids.append(txn.id)
    repo.commit()
    return repo, ids


def make_app(repo, tmp_path):
    from mtd_classifier.gui import app as app_module

    try:
        app = app_module.MtdApp(repo, show_walkthrough=False)
    except tkinter.TclError as e:
        pytest.skip(f"Tk unavailable: {e}")
    app.update_idletasks()
    return app


def test_tabs_created_per_account_plus_summary(seeded_repo, tmp_path):
    repo, _ = seeded_repo
    app = make_app(repo, tmp_path)
    try:
        assert set(app.account_tabs) == {"First Direct Current", "Santander Rental"}
        tab_texts = [app.notebook.tab(t, "text") for t in app.notebook.tabs()]
        assert tab_texts[-2] == "Summary & Bridging"
        assert tab_texts[-1] == "Tax Calculation"
        fd_tab = app.account_tabs["First Direct Current"]
        assert len(fd_tab.tree.get_children()) == 3
    finally:
        app.destroy()


def test_apply_category_sets_categorisation_and_learns_rule(seeded_repo, tmp_path):
    repo, ids = seeded_repo
    app = make_app(repo, tmp_path)
    try:
        tab = app.account_tabs["First Direct Current"]
        tab.tree.selection_set(str(ids[0]))
        tab.category_var.set("Self-Employed Income")
        tab.apply_category()
        cat = repo.get_categorisation(ids[0])
        assert cat is not None
        assert cat.category == "self_employed_income"
        assert repo.get_exact_rule("invoice paid") is not None
    finally:
        app.destroy()


def test_refund_panel_and_mark_refund(seeded_repo, tmp_path):
    repo, ids = seeded_repo
    # Categorise the original expense first
    repo.set_categorisation(
        raw_transaction_id=ids[1], category="property_repairs",
        confidence="manual", changed_by="test",
    )
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        tab = app.account_tabs["First Direct Current"]
        tab.tree.selection_set(str(ids[2]))  # the +180 refund
        tab.on_select()
        # Statement extract shown, refund candidate offered
        assert "raw line 2" in tab.extract_var.get()
        assert tab._refund_candidates
        assert "PLUMBER J SMITH" in tab.refund_combo.get()
        tab.mark_refund()
        cat = repo.get_categorisation(ids[2])
        assert cat is not None
        assert cat.category == "property_repairs"
        assert cat.confidence == "refund"
    finally:
        app.destroy()


def test_attach_open_remove_file_via_gui(seeded_repo, tmp_path):
    repo, ids = seeded_repo
    app = make_app(repo, tmp_path)
    try:
        tab = app.account_tabs["First Direct Current"]
        tab.tree.selection_set(str(ids[1]))  # the -180 plumber expense
        tab.on_select()
        assert tab.attachments_var.get() == "(none)"
        assert str(tab.attach_btn["state"]) == "normal"
        assert str(tab.open_attachment_btn["state"]) == "disabled"

        receipt = tmp_path / "receipt.pdf"
        receipt.write_bytes(b"%PDF-1.4 fake receipt")
        with patch("mtd_classifier.gui.app.filedialog.askopenfilename", return_value=str(receipt)):
            tab.attach_file()

        assert tab.attachments_var.get() == "receipt.pdf"
        assert str(tab.open_attachment_btn["state"]) == "normal"
        attachments = repo.get_attachments_for_transaction(ids[1])
        assert len(attachments) == 1
        assert attachments[0].content_type == "pdf"

        from mtd_classifier.services import attachments as attachments_service
        with patch.object(attachments_service, "open_attachment") as opener:
            tab.open_attachment()
        opener.assert_called_once()

        with patch("mtd_classifier.gui.app.messagebox.askyesno", return_value=True):
            tab.remove_attachment()
        assert tab.attachments_var.get() == "(none)"
        assert repo.get_attachments_for_transaction(ids[1]) == []
    finally:
        app.destroy()


def test_attach_rejects_unsupported_file_type(seeded_repo, tmp_path):
    repo, ids = seeded_repo
    app = make_app(repo, tmp_path)
    try:
        tab = app.account_tabs["First Direct Current"]
        tab.tree.selection_set(str(ids[1]))
        tab.on_select()

        bad_file = tmp_path / "notes.txt"
        bad_file.write_bytes(b"not a receipt")
        with patch("mtd_classifier.gui.app.filedialog.askopenfilename", return_value=str(bad_file)), \
             patch("mtd_classifier.gui.app.messagebox.showinfo") as info:
            tab.attach_file()
        assert info.called
        assert repo.get_attachments_for_transaction(ids[1]) == []
    finally:
        app.destroy()


def test_attach_accepts_word_documents(seeded_repo, tmp_path):
    repo, ids = seeded_repo
    app = make_app(repo, tmp_path)
    try:
        tab = app.account_tabs["First Direct Current"]
        tab.tree.selection_set(str(ids[1]))
        tab.on_select()

        invoice = tmp_path / "invoice.docx"
        invoice.write_bytes(b"PK fake docx")
        with patch("mtd_classifier.gui.app.filedialog.askopenfilename", return_value=str(invoice)):
            tab.attach_file()
        attachments = repo.get_attachments_for_transaction(ids[1])
        assert len(attachments) == 1
        assert attachments[0].content_type == "document"
    finally:
        app.destroy()


def test_batch_import_via_file_dialog(repo, tmp_path):
    app = make_app(repo, tmp_path)
    try:
        with patch("mtd_classifier.gui.app.filedialog.askopenfilenames",
                   return_value=(str(FIXTURES / "first_direct_sample.csv"),)), \
             patch("mtd_classifier.gui.app.messagebox.showinfo"), \
             patch("mtd_classifier.gui.app.AddAccountDialog") as dialog_cls:
            dialog_cls.return_value.result = ("My Current Account", None)
            # wait_window returns immediately for an already-destroyed window;
            # simulate by calling the underlying pieces directly
            tab = app._add_account_tab("My Current Account", bank=None, account="My Current Account")
            tab.import_files()
        assert len(tab.tree.get_children()) == 10
        # transactions were stamped with the tab's account label
        txns = tab._transactions()
        assert all(t.account == "My Current Account" for t in txns)
        # the detected bank was remembered on the tab
        assert tab.bank == "firstdirect"
    finally:
        app.destroy()


def test_category_dropdown_filters_by_direction(seeded_repo, tmp_path):
    repo, ids = seeded_repo
    app = make_app(repo, tmp_path)
    try:
        tab = app.account_tabs["First Direct Current"]
        # ids[0] = +2500 income line
        tab.tree.selection_set(str(ids[0]))
        tab.on_select()
        values = list(tab.category_combo["values"])
        assert "Self-Employed Income" in values
        assert "Rental Income" in values
        assert "Personal" in values
        assert "SE Travel" not in values
        # ids[1] = -180 expense line
        tab.tree.selection_set(str(ids[1]))
        tab.on_select()
        values = list(tab.category_combo["values"])
        assert "SE Travel" in values
        assert "SE Telecoms" in values
        assert "Rental Income" not in values
        # Mixed selection: only direction-free categories remain, under
        # their group header
        tab.tree.selection_set(str(ids[0]), str(ids[1]))
        tab.on_select()
        values = list(tab.category_combo["values"])
        assert values == [
            "—— Year-end / not reported ——",
            "Tax Payment (HMRC)", "Personal", "Transfer",
        ]
    finally:
        app.destroy()


def test_category_picker_grouped_with_headers(seeded_repo, tmp_path):
    """SA103 boxes first, then SA105, then year-end - with divider headers
    that bounce back to empty if picked."""
    from mtd_classifier.gui.app import CATEGORY_GROUP_HEADERS

    repo, ids = seeded_repo
    app = make_app(repo, tmp_path)
    try:
        tab = app.account_tabs["First Direct Current"]
        tab.tree.selection_set(str(ids[1]))  # expense line
        tab.on_select()
        values = list(tab.category_combo["values"])
        sa103_at = values.index(CATEGORY_GROUP_HEADERS["SA103"])
        sa105_at = values.index(CATEGORY_GROUP_HEADERS["SA105"])
        other_at = values.index(CATEGORY_GROUP_HEADERS["OTHER"])
        assert sa103_at < values.index("SE Travel") < sa105_at
        assert sa105_at < values.index("Property Repairs") < other_at
        assert other_at < values.index("Tax Payment (HMRC)")

        # Picking a header clears back to no selection
        tab.category_var.set(CATEGORY_GROUP_HEADERS["SA105"])
        tab._on_category_picked()
        assert tab.category_var.get() == ""
        # ...and Apply refuses politely rather than crashing
        tab.category_var.set(CATEGORY_GROUP_HEADERS["SA105"])
        with patch("mtd_classifier.gui.app.messagebox.showinfo") as info:
            tab.apply_category()
        assert info.called
    finally:
        app.destroy()


def test_every_category_has_hmrc_guidance():
    from mtd_classifier.models.category import CATEGORY_GUIDANCE, TransactionCategory

    for cat in TransactionCategory:
        assert cat in CATEGORY_GUIDANCE, f"{cat} has no picker guidance"
        assert len(CATEGORY_GUIDANCE[cat]) > 20


def test_folder_import_respects_selected_tax_year(repo, tmp_path):
    # Folder with one statement inside tax year 2026-27 and one from 2025-26
    folder = tmp_path / "statements"
    folder.mkdir()
    (folder / "april_2026.csv").write_text(
        "Date,Description,Amount,Balance\n"
        "15/04/2026,INVOICE PAID,2500.00,5000.00\n"
        "20/04/2026,TESCO,-45.00,4955.00\n"
    )
    (folder / "march_2026.csv").write_text(
        "Date,Description,Amount,Balance\n"
        "10/03/2026,OLD INVOICE,1000.00,2000.00\n"
    )
    # Statements inside subfolders are found too
    nested = folder / "2026" / "may"
    nested.mkdir(parents=True)
    (nested / "may_2026.csv").write_text(
        "Date,Description,Amount,Balance\n"
        "12/05/2026,TRAIN TICKET,-30.00,4925.00\n"
    )
    app = make_app(repo, tmp_path)
    try:
        app.tax_year_var.set("2026-27")
        app._on_tax_year_change()
        tab = app._add_account_tab("Current", bank=None, account="Current")
        with patch("mtd_classifier.gui.app.filedialog.askdirectory",
                   return_value=str(folder)), \
             patch("mtd_classifier.gui.app.messagebox.showinfo") as info:
            tab.import_folder()
        # The 2026-27 transactions came in, including the one in the nested
        # subfolder; March 2026 ignored
        txns = tab._transactions()
        assert len(txns) == 3
        assert all(t.date >= date(2026, 4, 6) for t in txns)
        assert any(t.description == "TRAIN TICKET" for t in txns)
        msg = info.call_args[0][1]
        assert "1 transactions outside tax year 2026-27" in msg
        assert "march_2026.csv" in msg  # flagged as having nothing in-year

        # Re-scanning the same folder is idempotent
        with patch("mtd_classifier.gui.app.filedialog.askdirectory",
                   return_value=str(folder)), \
             patch("mtd_classifier.gui.app.messagebox.showinfo") as info2:
            tab.import_folder()
        assert len(tab._transactions()) == 3
        assert "Imported 0 new transactions" in info2.call_args[0][1]
    finally:
        app.destroy()


def test_bank_mismatch_warning_on_import(repo, tmp_path):
    app = make_app(repo, tmp_path)
    try:
        tab = app._add_account_tab("FD Current", bank="firstdirect", account="FD Current")
        # Import a Santander file into the First Direct account: user says No
        with patch("mtd_classifier.gui.app.filedialog.askopenfilenames",
                   return_value=(str(FIXTURES / "santander_sample.txt"),)), \
             patch("mtd_classifier.gui.app.messagebox.askyesno", return_value=False) as ask, \
             patch("mtd_classifier.gui.app.messagebox.showinfo") as info:
            tab.import_files()
        assert ask.called
        assert "looks like a santander statement" in ask.call_args[0][1]
        assert len(tab._transactions()) == 0
        assert "skipped: statement from a different bank" in info.call_args[0][1]

        # User says Yes: transactions land in this account tab
        with patch("mtd_classifier.gui.app.filedialog.askopenfilenames",
                   return_value=(str(FIXTURES / "santander_sample.txt"),)), \
             patch("mtd_classifier.gui.app.messagebox.askyesno", return_value=True), \
             patch("mtd_classifier.gui.app.messagebox.showinfo"):
            tab.import_files()
        assert len(tab._transactions()) == 5
    finally:
        app.destroy()


def _quarter_menu_entries(app):
    """(label, state) for each entry of the global 'Up to quarter' menu."""
    menu = app.quarter_menu
    end = menu.index("end")
    if end is None:
        return []
    return [
        (menu.entrycget(i, "label"), str(menu.entrycget(i, "state")))
        for i in range(end + 1)
    ]


def test_tax_year_selector_updates_quarter_options(repo, tmp_path):
    app = make_app(repo, tmp_path)
    try:
        assert app.tax_year == 2026  # today is inside tax year 2026-27
        labels = [label for label, _ in _quarter_menu_entries(app)]
        assert [l.split()[0] for l in labels] == [
            "Q1-2026", "Q2-2026", "Q3-2026", "Q4-2026",
        ]
        # Empty repo: no quarter has data, so the selector is fully greyed out
        assert all(state == "disabled" for _, state in _quarter_menu_entries(app))
        assert str(app.quarter_btn["state"]) == "disabled"
        assert app.export_quarter is None

        app.tax_year_var.set("2025-26")
        app._on_tax_year_change()
        assert app.tax_year == 2025
        labels = [label for label, _ in _quarter_menu_entries(app)]
        assert [l.split()[0] for l in labels] == [
            "Q1-2025", "Q2-2025", "Q3-2025", "Q4-2025",
        ]
        start, end = app.tax_year_range()
        assert (start.isoformat(), end.isoformat()) == ("2025-04-06", "2026-04-05")
        assert app.concrete_tax_year == 2025

        # Switching to "All transactions" greys out the selector entirely
        app.tax_year_var.set("All transactions")
        app._on_tax_year_change()
        assert app.tax_year is None
        assert app.concrete_tax_year == 2025
        assert str(app.quarter_btn["state"]) == "disabled"
        assert app.export_quarter is None
    finally:
        app.destroy()


def test_quarter_options_future_quarters_greyed_unverified_warns(repo, tmp_path):
    """An elapsed quarter with data is selectable (with a warning if its
    completeness couldn't be verified); future quarters stay greyed out."""
    from datetime import date as date_cls

    log = repo.create_import_log(
        filename="ytd.csv", file_hash="cover_h1", bank="firstdirect", fmt="csv",
        record_count=1, account="First Direct Current",
        period_start=date_cls(2026, 4, 6), period_end=date_cls(2026, 7, 10),
    )
    repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date_cls(2026, 4, 20),
        description="INVOICE", amount=100.0, dedup_hash="cover_t1",
        account="First Direct Current",
    )
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        entries = dict(
            (label.split()[0], (label, state))
            for label, state in _quarter_menu_entries(app)
        )
        # Q1-2026 (6 Apr - 5 Jul) has elapsed and has data - selectable, but
        # no balance chain / second source, so it carries a warning marker
        q1_label, q1_state = entries["Q1-2026"]
        assert q1_state == "normal"
        assert "!" in q1_label
        # Later quarters haven't ended yet
        assert entries["Q2-2026"][1] == "disabled"
        assert entries["Q4-2026"][1] == "disabled"
        assert str(app.quarter_btn["state"]) == "normal"
        assert app.export_quarter is not None
        assert app.export_quarter.label == "Q1-2026"
    finally:
        app.destroy()


def test_quarter_verified_by_balance_chain_has_no_warning(repo, tmp_path):
    """An unbroken running-balance chain spanning the YTD period counts as
    verified completeness - no warning marker on that quarter."""
    from datetime import date as date_cls

    log = repo.create_import_log(
        filename="chain.csv", file_hash="chain_h1", bank="firstdirect", fmt="csv",
        record_count=3, account="First Direct Current",
    )
    rows = [
        (date_cls(2026, 4, 1), 100.0, 1100.0),   # anchor before 6 April
        (date_cls(2026, 5, 1), 50.0, 1150.0),
        (date_cls(2026, 7, 10), -10.0, 1140.0),  # anchor after Q1 end
    ]
    for i, (d, amount, balance) in enumerate(rows):
        repo.create_raw_transaction(
            import_log_id=log.id, bank="firstdirect", txn_date=d,
            description=f"TXN {i}", amount=amount, dedup_hash=f"chain_t{i}",
            account="First Direct Current", balance=balance,
        )
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        entries = dict(
            (label.split()[0], (label, state))
            for label, state in _quarter_menu_entries(app)
        )
        q1_label, q1_state = entries["Q1-2026"]
        assert q1_state == "normal"
        assert "!" not in q1_label
        assert app.export_quarter.label == "Q1-2026"
    finally:
        app.destroy()


def test_account_tab_excludes_transactions_outside_selected_tax_year(repo, tmp_path):
    """Choosing tax year 2025-26 must not show 2024-25 or 2026-27 transactions."""
    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=3
    )
    rows = [
        (date(2024, 12, 1), "OLD YEAR", 100.0, "ty_old"),
        (date(2025, 6, 1), "IN YEAR", 200.0, "ty_in"),
        (date(2026, 5, 1), "NEW YEAR", 300.0, "ty_new"),
    ]
    for d, desc, amount, h in rows:
        repo.create_raw_transaction(
            import_log_id=log.id, bank="firstdirect", txn_date=d,
            description=desc, amount=amount, dedup_hash=h,
            account="First Direct Current",
        )
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        tab = app._add_account_tab("First Direct Current", bank="firstdirect", account="First Direct Current")
        app.tax_year_var.set("2025-26")
        app._on_tax_year_change()

        descriptions = [t.description for t in tab._transactions()]
        assert descriptions == ["IN YEAR"]
        assert "OLD YEAR" not in descriptions
        assert "NEW YEAR" not in descriptions
    finally:
        app.destroy()


def test_all_transactions_option_lifts_the_filter(repo, tmp_path):
    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=3
    )
    rows = [
        (date(2024, 12, 1), "OLD YEAR", 100.0, "all_old"),
        (date(2025, 6, 1), "IN YEAR", 200.0, "all_in"),
        (date(2026, 5, 1), "NEW YEAR", 300.0, "all_new"),
    ]
    for d, desc, amount, h in rows:
        repo.create_raw_transaction(
            import_log_id=log.id, bank="firstdirect", txn_date=d,
            description=desc, amount=amount, dedup_hash=h,
            account="First Direct Current",
        )
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        tab = app._add_account_tab("First Direct Current", bank="firstdirect", account="First Direct Current")

        app.tax_year_var.set("2025-26")
        app._on_tax_year_change()
        assert len(tab._transactions()) == 1

        app.tax_year_var.set("All transactions")
        app._on_tax_year_change()
        assert app.tax_year is None
        descriptions = {t.description for t in tab._transactions()}
        assert descriptions == {"OLD YEAR", "IN YEAR", "NEW YEAR"}

        # Summary tab keeps using the last concrete tax year, unaffected
        assert app.concrete_tax_year == 2025
        # ...but the quarter selector is greyed out while browsing everything
        assert str(app.quarter_btn["state"]) == "disabled"
    finally:
        app.destroy()


def test_switching_tax_year_refreshes_account_tabs_immediately(repo, tmp_path):
    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=2
    )
    repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2025, 6, 1),
        description="2025 TXN", amount=100.0, dedup_hash="refresh_2025",
        account="First Direct Current",
    )
    repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 6, 1),
        description="2026 TXN", amount=100.0, dedup_hash="refresh_2026",
        account="First Direct Current",
    )
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        tab = app._add_account_tab("First Direct Current", bank="firstdirect", account="First Direct Current")
        tab.refresh()
        # Default tax year is 2026 (today is 2026-07-10) - only the 2026 txn shows
        assert len(tab.tree.get_children()) == 1
        assert tab.tree.item(tab.tree.get_children()[0], "values")[1] == "2026 TXN"

        app.tax_year_var.set("2025-26")
        app._on_tax_year_change()  # must refresh the tab without an explicit tab.refresh() call
        assert len(tab.tree.get_children()) == 1
        assert tab.tree.item(tab.tree.get_children()[0], "values")[1] == "2025 TXN"
    finally:
        app.destroy()


def test_employment_income_apply_reminds_in_status_not_modal(seeded_repo, tmp_path):
    """Categorising as Employment Income must NOT interrupt with a modal
    warning - just a status-bar reminder about the year-end confirmation."""
    repo, ids = seeded_repo
    app = make_app(repo, tmp_path)
    try:
        tab = app.account_tabs["First Direct Current"]
        tab.tree.selection_set(str(ids[0]))
        tab.category_var.set("Employment Income")
        with patch("mtd_classifier.gui.app.messagebox.showwarning") as warn:
            tab.apply_category()
        assert not warn.called
        status = app.status_var.get()
        assert "reminder" in status.lower()
        assert "gross figure" in status
    finally:
        app.destroy()


def test_bridging_reminds_about_unconfirmed_employment_income(repo, tmp_path):
    from mtd_classifier.periods import TaxQuarter

    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=2
    )
    for i, (d, desc, amount, cat) in enumerate([
        (date(2026, 4, 20), "INVOICE", 2500.0, "self_employed_income"),
        (date(2026, 4, 28), "SALARY", 2400.0, "employment_income"),
    ]):
        txn = repo.create_raw_transaction(
            import_log_id=log.id, bank="firstdirect", txn_date=d,
            description=desc, amount=amount, dedup_hash=f"bridge_rem_{i}",
            account="First Direct Current",
        )
        repo.set_categorisation(raw_transaction_id=txn.id, category=cat,
                                 confidence="manual", changed_by="test")
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        app.concrete_tax_year = 2026
        app.export_quarter = TaxQuarter(2026, 1)
        out = tmp_path / "bridging"
        out.mkdir()
        with patch("mtd_classifier.gui.app.filedialog.askdirectory", return_value=str(out)), \
             patch("mtd_classifier.gui.app.messagebox.askyesno", return_value=True), \
             patch("mtd_classifier.gui.app.messagebox.showinfo") as info:
            app.summary_tab.generate_bridging()
        # Files ARE generated despite the unconfirmed employment income...
        assert list(out.glob("*.xlsx"))
        msg = info.call_args[0][1]
        # ...and the success dialog carries the reminder
        assert "Reminder: Employment Income" in msg
        assert "fine to upload" in msg

        # Once confirmed, the reminder disappears
        repo.set_employment_income(2026, 38000.0, source="manual")
        repo.commit()
        with patch("mtd_classifier.gui.app.filedialog.askdirectory", return_value=str(out)), \
             patch("mtd_classifier.gui.app.messagebox.askyesno", return_value=True), \
             patch("mtd_classifier.gui.app.messagebox.showinfo") as info2:
            app.summary_tab.generate_bridging()
        assert "Reminder: Employment Income" not in info2.call_args[0][1]
    finally:
        app.destroy()


def test_other_category_apply_does_not_warn(seeded_repo, tmp_path):
    repo, ids = seeded_repo
    app = make_app(repo, tmp_path)
    try:
        tab = app.account_tabs["First Direct Current"]
        tab.tree.selection_set(str(ids[0]))
        tab.category_var.set("Self-Employed Income")
        with patch("mtd_classifier.gui.app.messagebox.showwarning") as warn:
            tab.apply_category()
        assert not warn.called
    finally:
        app.destroy()


def test_summary_shows_unconfirmed_employment_income_row(repo, tmp_path):
    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
    )
    txn = repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 4, 20),
        description="SALARY", amount=2000.0, dedup_hash="gui_emp_1",
    )
    repo.set_categorisation(
        raw_transaction_id=txn.id, category="employment_income",
        confidence="manual", changed_by="test",
    )
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        app.summary_tab.refresh()
        rows = _all_tree_rows(app.summary_tab.tree)
        _, values = next(r for r in rows if r[0] == "Employment Income")
        assert values[0] == "UNCONFIRMED"   # Q1 column
        assert values[4] == "UNCONFIRMED"   # Total column
    finally:
        app.destroy()


def _seed_salary_deposits(repo, tax_year=2026):
    """Two salary deposits categorised as Employment Income."""
    log = repo.create_import_log(
        filename="sal.csv", file_hash=f"sal_h_{tax_year}", bank="firstdirect",
        fmt="csv", record_count=2,
    )
    ids = []
    for i, (d, amount) in enumerate([
        (date(tax_year, 4, 28), 2400.00), (date(tax_year, 5, 28), 2450.00),
    ]):
        txn = repo.create_raw_transaction(
            import_log_id=log.id, bank="firstdirect", txn_date=d,
            description=f"ACME PAYROLL {i}", amount=amount,
            dedup_hash=f"sal_{tax_year}_{i}", account="First Direct Current",
        )
        repo.set_categorisation(
            raw_transaction_id=txn.id, category="employment_income",
            confidence="manual", changed_by="test",
        )
        ids.append(txn.id)
    repo.commit()
    return ids


def test_employment_dialog_lists_deposits_and_confirms_amended_grosses(repo, tmp_path):
    from mtd_classifier.gui.app import EmploymentIncomeDialog

    ids = _seed_salary_deposits(repo)
    app = make_app(repo, tmp_path)
    try:
        dialog = EmploymentIncomeDialog(app, 2026)
        # Both net deposits are listed
        assert [r["txn"].id for r in dialog._rows] == ids
        # Amend each with its pre-tax gross amount and confirm
        dialog._rows[0]["var"].set("3100.00")
        dialog._rows[1]["var"].set("3150.00")
        with patch("mtd_classifier.gui.app.messagebox.askyesno", return_value=True), \
             patch("mtd_classifier.gui.app.messagebox.showinfo"):
            dialog._confirm_breakdown()
        record = repo.get_employment_income(2026)
        assert record.gross_amount == 6250.00
        assert record.source == "statement_breakdown"
        # The per-deposit grosses persist for the next visit
        entries = repo.get_employment_gross_entries(ids)
        assert entries[ids[0]].gross_amount == 3100.00
        assert entries[ids[1]].gross_amount == 3150.00
        assert entries[ids[0]].source == "manual"
        dialog.destroy()
    finally:
        app.destroy()


def test_employment_dialog_paid_gross_accepts_deposit_amounts(repo, tmp_path):
    from mtd_classifier.gui.app import EmploymentIncomeDialog

    ids = _seed_salary_deposits(repo)
    app = make_app(repo, tmp_path)
    try:
        dialog = EmploymentIncomeDialog(app, 2026)
        with patch("mtd_classifier.gui.app.messagebox.askyesno", return_value=True), \
             patch("mtd_classifier.gui.app.messagebox.showinfo"):
            dialog._confirm_paid_gross()
        record = repo.get_employment_income(2026)
        assert record.gross_amount == 4850.00  # 2400 + 2450, as-is
        assert record.source == "paid_gross"
        entries = repo.get_employment_gross_entries(ids)
        assert entries[ids[0]].gross_amount == 2400.00
        assert entries[ids[0]].source == "paid_gross"
        dialog.destroy()
    finally:
        app.destroy()


def test_employment_dialog_payslip_fills_one_deposit(repo, tmp_path):
    from types import SimpleNamespace
    from mtd_classifier.gui.app import EmploymentIncomeDialog

    ids = _seed_salary_deposits(repo)
    app = make_app(repo, tmp_path)
    try:
        dialog = EmploymentIncomeDialog(app, 2026)
        candidate = SimpleNamespace(
            label="Gross pay", amount=3100.00, is_annual=False,
            line="Gross Pay 3100.00",
        )
        with patch.object(dialog, "_pick_pdf_candidate",
                          return_value=(candidate, "april_payslip.pdf")):
            dialog._fill_from_payslip(dialog._rows[0])
        assert dialog._rows[0]["var"].get() == "3100.00"
        assert dialog._rows[0]["source"] == "payslip_pdf"
        assert "april_payslip.pdf" in dialog._rows[0]["note"]

        dialog._rows[1]["var"].set("3150.00")
        with patch("mtd_classifier.gui.app.messagebox.askyesno", return_value=True), \
             patch("mtd_classifier.gui.app.messagebox.showinfo"):
            dialog._confirm_breakdown()
        entries = repo.get_employment_gross_entries(ids)
        assert entries[ids[0]].source == "payslip_pdf"
        assert entries[ids[1]].source == "manual"
        dialog.destroy()
    finally:
        app.destroy()


def test_employment_income_dialog_annual_figure_confirm(repo, tmp_path):
    from mtd_classifier.gui.app import EmploymentIncomeDialog

    app = make_app(repo, tmp_path)
    try:
        dialog = EmploymentIncomeDialog(app, app.concrete_tax_year)
        dialog.annual_var.set("45000.00")
        with patch("mtd_classifier.gui.app.messagebox.askyesno", return_value=True), \
             patch("mtd_classifier.gui.app.messagebox.showinfo"):
            dialog._confirm_annual()
        record = repo.get_employment_income(app.concrete_tax_year)
        assert record is not None
        assert record.gross_amount == 45000.00
        assert record.source == "manual"
        dialog.destroy()
    finally:
        app.destroy()


def test_employment_income_dialog_updates_summary(repo, tmp_path):
    from mtd_classifier.gui.app import EmploymentIncomeDialog

    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
    )
    txn = repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 4, 20),
        description="SALARY", amount=2000.0, dedup_hash="gui_emp_dialog_1",
    )
    repo.set_categorisation(
        raw_transaction_id=txn.id, category="employment_income",
        confidence="manual", changed_by="test",
    )
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        app.summary_tab.refresh()
        rows_before = _all_tree_rows(app.summary_tab.tree)
        _, values_before = next(r for r in rows_before if r[0] == "Employment Income")
        assert values_before[0] == "UNCONFIRMED"

        dialog = EmploymentIncomeDialog(app, app.concrete_tax_year)
        dialog.annual_var.set("45000.00")
        with patch("mtd_classifier.gui.app.messagebox.askyesno", return_value=True), \
             patch("mtd_classifier.gui.app.messagebox.showinfo"):
            dialog._confirm_annual()
        dialog.destroy()

        app.summary_tab.refresh()
        rows_after = _all_tree_rows(app.summary_tab.tree)
        _, values_after = next(r for r in rows_after if r[0] == "Employment Income")
        assert values_after[0] == "45,000.00"  # Q1 column
        assert values_after[4] == "45,000.00"  # Total column (shown once, not summed)
    finally:
        app.destroy()


def test_summary_tab_shows_quarter_totals(seeded_repo, tmp_path):
    repo, ids = seeded_repo
    repo.set_categorisation(
        raw_transaction_id=ids[0], category="self_employed_income",
        confidence="manual", changed_by="test",
    )
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        app.summary_tab.refresh()
        rows = _all_tree_rows(app.summary_tab.tree)
        _, se_values = next(r for r in rows if r[0] == "Self-Employed Income")
        assert se_values[0] == "2,500.00"  # Q1 column, thousands-separated
        _, income_values = next(r for r in rows if r[0] == "Income")
        assert income_values[0] == "2,500.00"
    finally:
        app.destroy()


def test_summary_matrix_per_quarter_columns(repo, tmp_path):
    """Each quarter's OWN transactions show in its own column - not cumulative."""
    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=2
    )
    q1_txn = repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 4, 20),
        description="INVOICE Q1", amount=1000.0, dedup_hash="matrix_q1",
    )
    repo.set_categorisation(
        raw_transaction_id=q1_txn.id, category="self_employed_income",
        confidence="manual", changed_by="test",
    )
    q3_txn = repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 11, 15),
        description="INVOICE Q3", amount=500.0, dedup_hash="matrix_q3",
    )
    repo.set_categorisation(
        raw_transaction_id=q3_txn.id, category="self_employed_income",
        confidence="manual", changed_by="test",
    )
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        app.summary_tab.refresh()
        rows = _all_tree_rows(app.summary_tab.tree)
        _, values = next(r for r in rows if r[0] == "Self-Employed Income")
        # Q1 column has the Q1 invoice, Q2 empty, Q3 has the Q3 invoice, Q4 empty
        assert values[0] == "1,000.00"
        assert values[1] == ""
        assert values[2] == "500.00"
        assert values[3] == ""
        # Total column is unconditional: sums all 4 quarters
        assert values[4] == "1,500.00"
    finally:
        app.destroy()


def test_summary_matrix_annual_row_not_double_counted_in_total(repo, tmp_path):
    """Employment Income is an annual (P60) figure - transactions spanning
    multiple quarters must not multiply the confirmed total in the Total column."""
    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=2
    )
    q1_txn = repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 4, 20),
        description="SALARY Q1", amount=2000.0, dedup_hash="annual_q1",
    )
    repo.set_categorisation(
        raw_transaction_id=q1_txn.id, category="employment_income",
        confidence="manual", changed_by="test",
    )
    q2_txn = repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 8, 1),
        description="SALARY Q2", amount=2000.0, dedup_hash="annual_q2",
    )
    repo.set_categorisation(
        raw_transaction_id=q2_txn.id, category="employment_income",
        confidence="manual", changed_by="test",
    )
    repo.set_employment_income(2026, 45000.0, source="manual")
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        app.summary_tab.refresh()
        rows = dict((r[0], r[1]) for r in _all_tree_rows(app.summary_tab.tree))
        # Transactions in both Q1 and Q2 - Total must show the confirmed
        # figure ONCE, not 45000*2
        assert rows["Employment Income"][4] == "45,000.00"
    finally:
        app.destroy()


def test_summary_fy_estimate_scales_from_data_quarters(repo, tmp_path):
    # Tax year 2020-21 is safely in the past regardless of when tests run,
    # so every quarter counts as "fully elapsed" without mocking today().
    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
    )
    txn = repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2020, 4, 20),
        description="INVOICE Q1 ONLY", amount=1000.0, dedup_hash="fy_q1_only",
    )
    repo.set_categorisation(
        raw_transaction_id=txn.id, category="self_employed_income",
        confidence="manual", changed_by="test",
    )
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        app.tax_year = app.concrete_tax_year = 2020
        app.summary_tab.refresh()
        assert app.summary_tab._data_quarters == 1
        rows = dict((r[0], r[1]) for r in _all_tree_rows(app.summary_tab.tree))
        # Only Q1 has data -> FY Est. = Q1 total x4
        assert rows["Self-Employed Income"][5] == "4,000.00"
        assert rows["Income"][5] == "4,000.00"
    finally:
        app.destroy()


def test_summary_fy_estimate_cumulative_to_q3(repo, tmp_path):
    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=3
    )
    for i, (year, month, day) in enumerate([(2020, 4, 20), (2020, 7, 20), (2020, 10, 20)]):
        txn = repo.create_raw_transaction(
            import_log_id=log.id, bank="firstdirect", txn_date=date(year, month, day),
            description=f"INVOICE {i}", amount=300.0, dedup_hash=f"fy_q3_{i}",
        )
        repo.set_categorisation(
            raw_transaction_id=txn.id, category="self_employed_income",
            confidence="manual", changed_by="test",
        )
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        app.tax_year = app.concrete_tax_year = 2020
        app.summary_tab.refresh()
        assert app.summary_tab._data_quarters == 3
        rows = dict((r[0], r[1]) for r in _all_tree_rows(app.summary_tab.tree))
        # Q1+Q2+Q3 = 900.00, scaled by 4/3 = 1200.00
        assert rows["Income"][5] == "1,200.00"
    finally:
        app.destroy()


def test_summary_fy_estimate_excludes_in_progress_quarter(repo, tmp_path):
    """A quarter still in progress must not count toward the FY Est. scaling,
    even if it already has some transactions - its total is necessarily
    incomplete and would distort the projection."""
    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=2
    )
    # Q1 2020 is fully elapsed; Q2 2020 also fully elapsed by 2026 "today",
    # so use the CURRENT tax year instead, where Q1 has ended but Q2 (today
    # falls within it, per the fixed system date 2026-07-10) has not.
    txn1 = repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 4, 20),
        description="Q1 INVOICE", amount=1000.0, dedup_hash="fy_inprogress_q1",
    )
    repo.set_categorisation(
        raw_transaction_id=txn1.id, category="self_employed_income",
        confidence="manual", changed_by="test",
    )
    txn2 = repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 7, 8),
        description="Q2 PARTIAL INVOICE", amount=50.0, dedup_hash="fy_inprogress_q2",
    )
    repo.set_categorisation(
        raw_transaction_id=txn2.id, category="self_employed_income",
        confidence="manual", changed_by="test",
    )
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        app.tax_year = app.concrete_tax_year = 2026
        app.summary_tab.refresh()
        # Q2-2026 (ends 2026-10-05) has NOT elapsed yet relative to today
        # (2026-07-10 or later) - only Q1 should count, despite Q2 having data
        assert app.summary_tab._data_quarters == 1
        rows = dict((r[0], r[1]) for r in _all_tree_rows(app.summary_tab.tree))
        # FY Est. = Q1's 1000.00 x4, NOT (1000+50)/2 x4
        assert rows["Income"][5] == "4,000.00"
    finally:
        app.destroy()


def test_summary_fy_estimate_no_data_shows_na(repo, tmp_path):
    app = make_app(repo, tmp_path)
    try:
        app.tax_year = app.concrete_tax_year = 2020
        app.summary_tab.refresh()
        assert app.summary_tab._data_quarters == 0
    finally:
        app.destroy()


def test_summary_has_no_tax_estimate_rows(repo, tmp_path):
    """Tax estimates live ONLY on the Tax Calculation tab - the Summary tab
    ends with business net profit (plus the not-deducted finance costs), so
    two different 'estimated tax' figures can never coexist."""
    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=2
    )
    for i, (d, desc, amount, cat) in enumerate([
        (date(2026, 4, 20), "INVOICE", 20000.0, "self_employed_income"),
        (date(2026, 5, 1), "MORTGAGE", -800.0, "mortgage_interest"),
    ]):
        txn = repo.create_raw_transaction(
            import_log_id=log.id, bank="firstdirect", txn_date=d,
            description=desc, amount=amount, dedup_hash=f"tax_row_{i}",
        )
        repo.set_categorisation(raw_transaction_id=txn.id, category=cat,
                                 confidence="manual", changed_by="test")
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        app.summary_tab.refresh()
        rows = dict((r[0], r[1]) for r in _all_tree_rows(app.summary_tab.tree))
        assert not any(label.startswith("Estimated") for label in rows)
        assert rows["Net Profit from Property & SE"][4] == "20,000.00"
        # Finance costs still shown (not deducted), pointing at the Tax
        # Calculation tab for the relief
        assert rows["Mortgage int. / finance costs (not deducted)"][4] == "800.00"
        assert "Tax Calculation tab" in app.summary_tab._row_meta["finance_costs"]
    finally:
        app.destroy()


def test_summary_scope_label_clarifies_export_scope(repo, tmp_path):
    from mtd_classifier.periods import TaxQuarter

    app = make_app(repo, tmp_path)
    try:
        # Empty repo: no selectable quarter, the label explains why
        app.summary_tab.refresh()
        assert "greyed out" in app.summary_tab.scope_var.get()

        app.export_quarter = TaxQuarter(app.concrete_tax_year, 1)
        app.summary_tab.refresh()
        text = app.summary_tab.scope_var.get()
        assert "don't affect what's exported" in text
    finally:
        app.destroy()


def test_no_refresh_button_and_auto_refresh_on_tab_open(seeded_repo, tmp_path):
    repo, ids = seeded_repo
    app = make_app(repo, tmp_path)
    try:
        # No manual Refresh button anywhere in the summary toolbar
        def find_buttons(widget):
            found = []
            for child in widget.winfo_children():
                if isinstance(child, ttk.Button):
                    found.append(child.cget("text"))
                found.extend(find_buttons(child))
            return found

        button_texts = find_buttons(app.summary_tab)
        assert "Refresh" not in button_texts

        # Categorise a new transaction, then switch tabs to trigger auto-refresh
        repo.set_categorisation(
            raw_transaction_id=ids[1], category="mortgage_interest",
            confidence="manual", changed_by="test",
        )
        repo.commit()
        app.notebook.select(app.account_tabs["First Direct Current"])
        app.update()
        app.notebook.select(app.summary_tab)
        app.update()

        rows = _all_tree_rows(app.summary_tab.tree)
        assert any(r[0] == "Mortgage Interest" for r in rows)
    finally:
        app.destroy()


def test_tax_calculation_tab_full_computation(repo, tmp_path):
    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=8
    )
    rows = [
        (date(2024, 4, 20), "INVOICE 1", 40000.00, "self_employed_income"),
        (date(2024, 5, 21), "PLUMBER", -1200.00, "property_repairs"),
        (date(2024, 4, 25), "RENT RECEIVED", 15000.00, "rental_income"),
        (date(2024, 6, 1), "MORTGAGE PAYMENT", -4000.00, "mortgage_interest"),
        (date(2024, 4, 22), "BANK INTEREST", 300.00, "interest_income"),
        (date(2024, 4, 22), "SHARE DIVIDEND", 800.00, "dividend_income"),
        (date(2024, 4, 22), "OXFAM MONTHLY", -400.00, "gift_aid"),
        (date(2024, 4, 20), "OFFICE SUPPLIES", -600.00, "se_office_costs"),
    ]
    for i, (d, desc, amount, cat) in enumerate(rows):
        txn = repo.create_raw_transaction(
            import_log_id=log.id, bank="firstdirect", txn_date=d,
            description=desc, amount=amount, dedup_hash=f"taxcalc_{i}",
            account="First Direct Current",
        )
        repo.set_categorisation(raw_transaction_id=txn.id, category=cat,
                                 confidence="manual", changed_by="test")
    repo.set_employment_income(2024, 20000.0, source="manual", note="P60")
    repo.commit()

    app = make_app(repo, tmp_path)
    try:
        from mtd_classifier.periods import TaxQuarter

        app.concrete_tax_year = 2024
        # Up to Q4 = the full year, so YTD equals the old whole-year figures
        # and the FY estimate (scale x1) matches it exactly.
        app.export_quarter = TaxQuarter(2024, 4)
        app.tax_calc_tab.refresh()
        all_rows = _all_tree_rows(app.tax_calc_tab.tree)
        rows_out = dict((r[0].strip(), r[1][1] if r[1] else "") for r in all_rows)  # YTD column
        fy_out = dict((r[0].strip(), r[1][0] if r[1] else "") for r in all_rows)    # FY column
        assert rows_out["Self-employment profit"] == "39,400.00"
        assert rows_out["UK property profit"] == "13,800.00"
        assert rows_out["Employment income (confirmed annual figure)"] == "20,000.00"
        assert rows_out["UK interest"] == "300.00"
        assert rows_out["UK dividends"] == "800.00"
        assert rows_out["Total income"] == "74,300.00"
        assert rows_out["Personal allowance"] == "-12,570.00"
        assert rows_out["Taxable income"] == "61,730.00"
        assert rows_out["Gross tax"] == "16,713.25"
        assert rows_out["Income Tax liability"] == "15,913.25"
        assert rows_out["Class 4 NIC"] == "1,609.80"
        assert rows_out["Total Tax and NIC due"] == "17,523.05"
        # To Q4 the FY estimate is the same year (scale x1.00)
        assert fy_out["Total Tax and NIC due"] == "17,523.05"
        assert rows_out["Basic/additional rate bands widened for Gift Aid"] == "500.00"
        # Class 2 NIC was abolished by 2024-25 - must not appear
        assert "Class 2 NIC" not in rows_out
    finally:
        app.destroy()


def test_tax_calculation_fy_estimate_scales_ytd_quarter(repo, tmp_path):
    """Up to Q1: YTD shows the quarter's actual figures; FY Estimate
    projects them x4 (Employment income would stay unscaled)."""
    from mtd_classifier.periods import TaxQuarter

    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
    )
    txn = repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2024, 5, 1),
        description="INVOICE", amount=10000.0, dedup_hash="taxcalc_scale",
        account="First Direct Current",
    )
    repo.set_categorisation(raw_transaction_id=txn.id, category="self_employed_income",
                             confidence="manual", changed_by="test")
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        app.concrete_tax_year = 2024
        app.export_quarter = TaxQuarter(2024, 1)
        app.tax_calc_tab.refresh()
        all_rows = _all_tree_rows(app.tax_calc_tab.tree)
        ytd = dict((r[0].strip(), r[1][1] if r[1] else "") for r in all_rows)
        fy = dict((r[0].strip(), r[1][0] if r[1] else "") for r in all_rows)
        assert ytd["Total income"] == "10,000.00"
        assert fy["Total income"] == "40,000.00"  # x4
        assert "Q1-2024" in app.tax_calc_tab.tree.heading("ytd")["text"]
        assert "x4.00" in app.tax_calc_tab.basis_var.get()
    finally:
        app.destroy()


def test_tax_calculation_tab_unconfirmed_employment_income(repo, tmp_path):
    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
    )
    txn = repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2024, 5, 1),
        description="SALARY", amount=2000.0, dedup_hash="taxcalc_unconf",
        account="First Direct Current",
    )
    repo.set_categorisation(raw_transaction_id=txn.id, category="employment_income",
                             confidence="manual", changed_by="test")
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        from mtd_classifier.periods import TaxQuarter

        app.concrete_tax_year = 2024
        app.export_quarter = TaxQuarter(2024, 4)
        app.tax_calc_tab.refresh()
        rows = _all_tree_rows(app.tax_calc_tab.tree)
        assert any("UNCONFIRMED" in r[0] for r in rows)
    finally:
        app.destroy()


def test_tax_calculation_tab_no_data_shows_zero(repo, tmp_path):
    from mtd_classifier.periods import TaxQuarter

    app = make_app(repo, tmp_path)
    try:
        app.concrete_tax_year = 2024
        app.export_quarter = TaxQuarter(2024, 4)
        app.tax_calc_tab.refresh()
        rows = dict((r[0].strip(), r[1][1] if r[1] else "") for r in _all_tree_rows(app.tax_calc_tab.tree))
        assert rows["Total income"] == "0.00"
        assert rows["Total Tax and NIC due"] == "0.00"
    finally:
        app.destroy()


def test_tax_calculation_tab_needs_a_selectable_quarter(repo, tmp_path):
    app = make_app(repo, tmp_path)
    try:
        app.concrete_tax_year = 2024
        app.export_quarter = None
        app.tax_calc_tab.refresh()
        assert "complete cumulative data" in app.tax_calc_tab.status_var.get()
        assert len(app.tax_calc_tab.tree.get_children()) == 0
    finally:
        app.destroy()


def test_tax_calculation_tab_missing_year_shows_message(repo, tmp_path):
    app = make_app(repo, tmp_path)
    try:
        app.concrete_tax_year = 1999  # not in the (placeholder) rates table
        app.tax_calc_tab.refresh()
        assert "No tax rates available" in app.tax_calc_tab.status_var.get()
        assert len(app.tax_calc_tab.tree.get_children()) == 0
    finally:
        app.destroy()


def test_tax_calculation_tab_flags_unverified_placeholder_rates(repo, tmp_path):
    """If a year's rates are ever added as an unverified placeholder (its
    source citation says so), the tab must surface a visible warning rather
    than silently presenting it as authoritative."""
    from mtd_classifier import tax_year_rates as ty_rates_module
    from mtd_classifier.tax_year_rates import TaxYearRates

    synthetic = TaxYearRates(
        tax_year=2024,
        personal_allowance=12570.0, pa_taper_threshold=100000.0, pa_taper_divisor=2.0,
        basic_rate=0.20, higher_rate=0.40, additional_rate=0.45,
        higher_rate_threshold=50270.0, additional_rate_threshold=125140.0,
        dividend_allowance=500.0, dividend_basic_rate=0.0875,
        dividend_higher_rate=0.3375, dividend_additional_rate=0.3935,
        personal_savings_allowance_basic=1000.0, personal_savings_allowance_higher=500.0,
        starting_rate_for_savings_band=5000.0, starting_rate_for_savings_pct=0.0,
        ni_class2_weekly_rate=None, ni_class2_small_profits_threshold=6725.0,
        ni_class4_lower_profits_limit=12570.0, ni_class4_upper_profits_limit=50270.0,
        ni_class4_main_rate=0.06, ni_class4_upper_rate=0.02,
        mortgage_relief_expense_fraction=0.0,
        source="UNVERIFIED - placeholder for this test",
    )
    app = make_app(repo, tmp_path)
    try:
        from mtd_classifier.periods import TaxQuarter

        with patch.dict(ty_rates_module.TAX_YEAR_RATES, {2024: synthetic}):
            app.concrete_tax_year = 2024
            app.export_quarter = TaxQuarter(2024, 4)
            app.tax_calc_tab.refresh()
            assert "unverified" in app.tax_calc_tab.status_var.get().lower()
    finally:
        app.destroy()


def test_tax_calculation_tab_2024_25_rates_are_verified(repo, tmp_path):
    """The real 2024-25 entry must not carry the UNVERIFIED flag - guards
    against a future edit accidentally reverting it to placeholder status."""
    app = make_app(repo, tmp_path)
    try:
        from mtd_classifier.periods import TaxQuarter

        app.concrete_tax_year = 2024
        app.export_quarter = TaxQuarter(2024, 4)
        app.tax_calc_tab.refresh()
        assert app.tax_calc_tab.status_var.get() == ""
    finally:
        app.destroy()


def test_tax_calculation_tab_year_switch_updates_computation(repo, tmp_path):
    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
    )
    txn = repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2024, 5, 1),
        description="INVOICE", amount=30000.0, dedup_hash="taxcalc_switch",
        account="First Direct Current",
    )
    repo.set_categorisation(raw_transaction_id=txn.id, category="self_employed_income",
                             confidence="manual", changed_by="test")
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        from mtd_classifier.periods import TaxQuarter

        app.concrete_tax_year = 2024
        app.export_quarter = TaxQuarter(2024, 4)
        app.tax_calc_tab.refresh()
        rows_2024 = dict((r[0].strip(), r[1][1] if r[1] else "") for r in _all_tree_rows(app.tax_calc_tab.tree))
        assert rows_2024["Total income"] == "30,000.00"

        # No selector on the tab itself - switching the year uses the main
        # toolbar's "Tax year" selector.
        app.tax_year_var.set("2020-21")
        app._on_tax_year_change()
        assert app.concrete_tax_year == 2020
        app.export_quarter = TaxQuarter(2020, 4)
        app.tax_calc_tab.refresh()
        rows_2020 = dict((r[0].strip(), r[1][1] if r[1] else "") for r in _all_tree_rows(app.tax_calc_tab.tree))
        # Same income, no transactions in 2020-21 window -> zero
        assert rows_2020["Total income"] == "0.00"
    finally:
        app.destroy()


def test_tax_calculation_auto_refreshes_on_tab_open(repo, tmp_path):
    app = make_app(repo, tmp_path)
    try:
        app.concrete_tax_year = 2024
        # Full-year statement coverage so Q4-2024 is selectable automatically
        log = repo.create_import_log(
            filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv",
            record_count=1, account="First Direct Current",
            period_start=date(2024, 4, 6), period_end=date(2025, 4, 5),
        )
        txn = repo.create_raw_transaction(
            import_log_id=log.id, bank="firstdirect", txn_date=date(2024, 5, 1),
            description="INVOICE", amount=5000.0, dedup_hash="taxcalc_autoref",
            account="First Direct Current",
        )
        repo.set_categorisation(raw_transaction_id=txn.id, category="self_employed_income",
                                 confidence="manual", changed_by="test")
        repo.commit()
        app.notebook.select(app.tax_calc_tab)  # triggers _on_tab_changed -> refresh
        app.update()
        # _on_tab_changed re-derived the quarter options: Q4-2024 selected
        assert app.export_quarter is not None
        assert app.export_quarter.label == "Q4-2024"
        rows = dict((r[0].strip(), r[1][1] if r[1] else "") for r in _all_tree_rows(app.tax_calc_tab.tree))
        assert rows["Total income"] == "5,000.00"
    finally:
        app.destroy()


def test_walkthrough_demo_data_highlights_and_cleanup(seeded_repo, tmp_path):
    from mtd_classifier.gui import walkthrough as wt
    from mtd_classifier.gui.demo import DEMO_TAB_LABEL

    repo, _ = seeded_repo
    app = make_app(repo, tmp_path)
    try:
        assert wt.walkthrough_pending()
        dialog = wt.WalkthroughDialog(app)
        steps = len(dialog.steps)
        assert steps >= 6

        # Demo mode active: example tab added, repo swapped to in-memory data
        assert DEMO_TAB_LABEL in app.account_tabs
        assert app.repo is not repo
        demo_tab = app.tour_tab
        assert len(demo_tab.tree.get_children()) == 8

        # Step 2 (account tabs) spotlights widgets
        dialog.next()
        app.update_idletasks()
        assert dialog.highlighter.frames

        # The refund step selects the example refund line and fills the panel
        refund_index = next(
            i for i, s in enumerate(dialog.steps) if "refund" in s.title.lower()
        )
        while dialog.index < refund_index:
            dialog.next()
        assert demo_tab._refund_candidates
        assert "PLUMBER" in demo_tab.refund_combo.get()

        # Finish the tour: marker written, demo data gone, real repo restored
        while dialog.index < steps - 1:
            dialog.next()
        dialog.next()  # finish
        assert not wt.walkthrough_pending()
        assert DEMO_TAB_LABEL not in app.account_tabs
        assert app.repo is repo
        # Real data still intact in the visible tabs
        assert len(app.account_tabs["First Direct Current"].tree.get_children()) == 3
    finally:
        app.destroy()


def test_summary_fy_estimate_scales_ytd_basis_employment_income(repo, tmp_path):
    """A paid-gross/breakdown confirmation is a to-date deposit sum, so the
    FY Est. column must scale it up - unlike an annual (P60/manual) figure."""
    _seed_salary_deposits(repo, tax_year=2020)  # Q1 2020-21 deposits, long past
    repo.set_employment_income(2020, 4850.0, source="paid_gross")
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        app.tax_year = app.concrete_tax_year = 2020
        app.summary_tab.refresh()
        assert app.summary_tab._data_quarters == 1
        rows = dict((r[0], r[1]) for r in _all_tree_rows(app.summary_tab.tree))
        # Total shows the confirmed to-date figure once...
        assert rows["Employment Income"][4] == "4,850.00"
        # ...but FY Est. projects it: 4850 x 4/1
        assert rows["Employment Income"][5] == "19,400.00"

        # An annual-basis figure is NOT scaled
        repo.set_employment_income(2020, 45000.0, source="manual")
        repo.commit()
        app.summary_tab.refresh()
        rows = dict((r[0], r[1]) for r in _all_tree_rows(app.summary_tab.tree))
        assert rows["Employment Income"][5] == "45,000.00"
    finally:
        app.destroy()


def test_tax_calculation_ytd_basis_employment_scales_and_is_pro_forma(repo, tmp_path):
    from mtd_classifier.periods import TaxQuarter

    ids = _seed_salary_deposits(repo, tax_year=2024)
    for txn_id, amount in zip(ids, (2400.0, 2450.0)):
        repo.set_employment_gross_entry(txn_id, amount, source="paid_gross")
    repo.set_employment_income(2024, 4850.0, source="paid_gross")
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        app.concrete_tax_year = 2024
        app.export_quarter = TaxQuarter(2024, 1)
        app.tax_calc_tab.refresh()
        all_rows = _all_tree_rows(app.tax_calc_tab.tree)
        fy = dict((r[0].strip(), r[1][0] if r[1] else "") for r in all_rows)
        ytd = dict((r[0].strip(), r[1][1] if r[1] else "") for r in all_rows)
        # FY: the Q1 deposit sum scaled x4; YTD: pro-forma FY x 1/4
        assert fy["Employment income (confirmed annual figure)"] == "19,400.00"
        assert ytd["Employment income (confirmed annual figure)"] == "4,850.00"
        # Pro-forma: quarterly tax figures are the FY figure divided by 4
        fy_total = float(fy["Total Tax and NIC due"].replace(",", ""))
        ytd_total = float(ytd["Total Tax and NIC due"].replace(",", ""))
        assert ytd_total == pytest.approx(fy_total / 4, abs=0.01)
    finally:
        app.destroy()


def test_summary_double_click_total_reveals_source_rows(seeded_repo, tmp_path):
    repo, ids = seeded_repo
    repo.set_categorisation(
        raw_transaction_id=ids[0], category="self_employed_income",
        confidence="manual", changed_by="test",
    )
    repo.set_categorisation(
        raw_transaction_id=ids[3], category="rental_income",
        confidence="manual", changed_by="test",
    )
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        app.summary_tab.refresh()
        # One category's rows
        dialog = app.summary_tab._show_source_rows("income:self_employed_income", None)
        assert dialog is not None
        assert "Self-Employed Income" in dialog.title()
        tree = next(
            w for w in dialog.winfo_children()[1].winfo_children()
            if isinstance(w, ttk.Treeview)
        )
        rows = [tree.item(i, "values") for i in tree.get_children()]
        assert len(rows) == 1
        assert rows[0][2] == "INVOICE PAID"
        dialog.destroy()

        # The Income parent row aggregates both income categories
        dialog = app.summary_tab._show_source_rows("income", None)
        tree = next(
            w for w in dialog.winfo_children()[1].winfo_children()
            if isinstance(w, ttk.Treeview)
        )
        descriptions = {tree.item(i, "values")[2] for i in tree.get_children()}
        assert descriptions == {"INVOICE PAID", "RENT RECEIVED"}
        dialog.destroy()

        # Quarter scoping: Q3 has none of these transactions
        dialog = app.summary_tab._show_source_rows("income", 3)
        tree = next(
            w for w in dialog.winfo_children()[1].winfo_children()
            if isinstance(w, ttk.Treeview)
        )
        assert len(tree.get_children()) == 0
        dialog.destroy()
    finally:
        app.destroy()


def test_context_toggle_marks_expense_disallowable(seeded_repo, tmp_path):
    repo, ids = seeded_repo
    repo.set_categorisation(
        raw_transaction_id=ids[1], category="property_repairs",
        confidence="manual", changed_by="test",
    )
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        tab = app.account_tabs["First Direct Current"]
        tab._set_disallowable([ids[1]], True)
        assert repo.get_categorisation(ids[1]).disallowable is True
        # The list shows the flag on the category label
        values = tab.tree.item(str(ids[1]), "values")
        assert "(disallowable)" in values[3]
        tab._set_disallowable([ids[1]], False)
        assert repo.get_categorisation(ids[1]).disallowable is False
        values = tab.tree.item(str(ids[1]), "values")
        assert "(disallowable)" not in values[3]
    finally:
        app.destroy()


def test_summary_drill_down_period_scopes_clicked_cell(repo, tmp_path):
    """The drill-down period follows the cell that was double-clicked:
    quarter columns give that quarter's own range; the name/Total/FY Est.
    columns give the whole tax year. Ranges must never be inverted."""
    app = make_app(repo, tmp_path)
    try:
        app.concrete_tax_year = 2026
        s = app.summary_tab
        assert s._drill_down_period(1) == (date(2026, 4, 6), date(2026, 7, 5), "Q1-2026")
        assert s._drill_down_period(2) == (date(2026, 7, 6), date(2026, 10, 5), "Q2-2026")
        assert s._drill_down_period(4) == (date(2027, 1, 6), date(2027, 4, 5), "Q4-2026")
        start, end, label = s._drill_down_period(None)
        assert (start, end) == (date(2026, 4, 6), date(2027, 4, 5))
        assert label == "tax year 2026-27"
        for quarter in (1, 2, 3, 4, None):
            q_start, q_end, _ = s._drill_down_period(quarter)
            assert q_start < q_end
    finally:
        app.destroy()


def test_summary_double_click_employment_row_opens_confirmation_dialog(repo, tmp_path):
    from types import SimpleNamespace

    _seed_salary_deposits(repo)
    app = make_app(repo, tmp_path)
    try:
        app.summary_tab.refresh()
        with patch.object(app.summary_tab, "open_employment_dialog") as opener, \
             patch.object(app.summary_tab.tree, "identify_row",
                          return_value="income:employment_income"):
            app.summary_tab._on_double_click(SimpleNamespace(x=5, y=5))
        opener.assert_called_once()
    finally:
        app.destroy()


def test_submission_guide_steps_and_warnings(repo, tmp_path):
    from mtd_classifier.gui.walkthrough import SUBMISSION_STEPS, SubmissionGuideDialog

    # The content the user must not miss
    all_text = " ".join(title + " " + body for title, body, _ in SUBMISSION_STEPS)
    assert "Register your businesses with HMRC" in all_text
    assert "Business income" in all_text
    assert "Bridging mode" in all_text
    assert "6 April to 5 April" in all_text
    assert "Obligations" in all_text
    assert "defaults to TODAY'S DATE" in all_text
    assert "CANNOT BE AMENDED" in all_text
    # Links to the relevant sites
    links = [url for _, _, step_links in SUBMISSION_STEPS for _, url in step_links]
    assert any("gov.uk" in u for u in links)
    assert any("mytaxdigital.co.uk" in u for u in links)

    app = make_app(repo, tmp_path)
    try:
        dialog = SubmissionGuideDialog(app)
        assert "Register your businesses" in dialog.step_title.cget("text")
        assert str(dialog.back_btn["state"]) == "disabled"
        # Walk forward to the 'to'-date step: warning shown in brick red
        for _ in range(4):
            dialog._next()
        assert "CURRENT DATE" in dialog.warning_label.cget("text")
        dialog._next()
        assert "No amendments" in dialog.warning_label.cget("text")
        assert dialog.next_btn.cget("text") == "Done"
        dialog._back()
        assert "TO' DATE" in dialog.step_title.cget("text").upper()
        dialog.destroy()
    finally:
        app.destroy()


def _generate_bridging_with_patches(app, out_dir):
    with patch("mtd_classifier.gui.app.filedialog.askdirectory", return_value=str(out_dir)), \
         patch("mtd_classifier.gui.app.messagebox.askyesno", return_value=True), \
         patch("mtd_classifier.gui.app.messagebox.showinfo"), \
         patch("mtd_classifier.gui.app.SubmissionGuideDialog") as guide:
        app.summary_tab.generate_bridging()
    return guide


def test_submission_guide_auto_opens_after_bridging(repo, tmp_path):
    from mtd_classifier.periods import TaxQuarter

    log = repo.create_import_log(
        filename="s.csv", file_hash="h1", bank="firstdirect", fmt="csv", record_count=1
    )
    txn = repo.create_raw_transaction(
        import_log_id=log.id, bank="firstdirect", txn_date=date(2026, 4, 20),
        description="INVOICE", amount=2500.0, dedup_hash="guide_auto_1",
        account="First Direct Current",
    )
    repo.set_categorisation(raw_transaction_id=txn.id, category="self_employed_income",
                             confidence="manual", changed_by="test")
    repo.commit()
    app = make_app(repo, tmp_path)
    try:
        app.concrete_tax_year = 2026
        app.export_quarter = TaxQuarter(2026, 1)
        out = tmp_path / "bridge_out"
        out.mkdir()

        guide = _generate_bridging_with_patches(app, out)
        guide.assert_called_once_with(app, auto_opened=True)

        # Opting out is persisted, and stops the auto-open
        app.set_submission_guide_auto(False)
        guide = _generate_bridging_with_patches(app, out)
        assert not guide.called
        from mtd_classifier.gui.app import load_settings
        assert load_settings()["submission_guide_auto"] is False

        # Opting back in restores it
        app.set_submission_guide_auto(True)
        guide = _generate_bridging_with_patches(app, out)
        assert guide.called
    finally:
        app.destroy()


def test_auto_opened_guide_has_skip_and_optout(repo, tmp_path):
    from mtd_classifier.gui.walkthrough import SubmissionGuideDialog

    app = make_app(repo, tmp_path)
    try:
        dialog = SubmissionGuideDialog(app, auto_opened=True)
        assert dialog.skip_btn.cget("text") == "Skip Guide"
        assert dialog.auto_var.get() is True
        # Unticking the checkbox persists the opt-out on the app
        dialog.auto_var.set(False)
        app.set_submission_guide_auto(dialog.auto_var.get())
        assert app.submission_guide_auto is False
        dialog.destroy()

        # The manually-opened guide stays plain
        manual = SubmissionGuideDialog(app)
        assert not hasattr(manual, "skip_btn")
        manual.destroy()
    finally:
        app.destroy()


def test_summary_tab_has_submission_guide_button(repo, tmp_path):
    app = make_app(repo, tmp_path)
    try:
        assert "How to Submit" in app.summary_tab.submission_guide_btn.cget("text")
        with patch("mtd_classifier.gui.app.SubmissionGuideDialog") as dlg:
            app.summary_tab.show_submission_guide()
        dlg.assert_called_once_with(app)
    finally:
        app.destroy()


def test_user_indicator_and_switch(seeded_repo, tmp_path):
    from mtd_classifier.gui import app as app_module

    repo, _ = seeded_repo
    try:
        app = app_module.MtdApp(repo, show_walkthrough=False, user="Carla")
    except tkinter.TclError as e:
        pytest.skip(f"Tk unavailable: {e}")
    try:
        assert "[Carla]" in app.title()

        def walk(w):
            for child in w.winfo_children():
                yield child
                yield from walk(child)

        labels = [w for w in walk(app) if isinstance(w, ttk.Label)
                  and "User: Carla" in str(w.cget("text"))]
        assert labels, "active-user indicator label missing"
        switch = [w for w in walk(app) if isinstance(w, ttk.Button)
                  and "Switch User" in str(w.cget("text"))]
        assert switch, "Switch User button missing"

        switch[0].invoke()  # closes the window and flags the switch
        assert app.switch_user_requested
    finally:
        try:
            app.destroy()
        except tkinter.TclError:
            pass  # already destroyed by the switch


def test_no_user_ui_without_user(seeded_repo, tmp_path):
    repo, _ = seeded_repo
    app = make_app(repo, tmp_path)
    try:
        def walk(w):
            for child in w.winfo_children():
                yield child
                yield from walk(child)
        assert not [w for w in walk(app) if isinstance(w, ttk.Button)
                    and "Switch User" in str(w.cget("text"))]
    finally:
        app.destroy()
