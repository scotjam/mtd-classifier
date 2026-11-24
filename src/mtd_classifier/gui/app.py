"""Main Tkinter application: per-account tabs, batch import, consolidation.

Launch with ``mtd gui`` or ``mtd-gui``.
"""

from __future__ import annotations

import json
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from types import SimpleNamespace

import mtd_classifier.config as config
from mtd_classifier.config import APP_DIR, ensure_app_dir
from mtd_classifier.classifier.rule_engine import RuleBasedClassifier
from mtd_classifier.export.csv_exporter import export_to_csv
from mtd_classifier.export.mtd_summary import summarise_quarter
from mtd_classifier.export.mytaxdigital import export_bridging_templates
from mtd_classifier.gui.walkthrough import (
    SubmissionGuideDialog,
    WalkthroughDialog,
    walkthrough_pending,
)
from mtd_classifier.gui import theme
from mtd_classifier.gui.table import LedgerTable
from mtd_classifier.gui.theme import (
    apply_theme,
    attach_tab_indicator,
    close_window,
    fade_in,
    gradient_band,
    settle_status,
    startup_sweep,
    tab_dot,
)
from mtd_classifier.disclaimer import DISCLAIMER
from mtd_classifier.models.category import (
    TransactionCategory,
    ALWAYS_DISALLOWABLE_CATEGORIES,
    CATEGORY_BOX_MAPPING,
    CATEGORY_GUIDANCE,
    CATEGORY_LABELS,
    CATEGORIES_REQUIRING_CONFIRMATION,
    EXPENSE_CATEGORIES,
    FINANCE_COST_CATEGORIES,
    INCOME_CATEGORIES,
    NEUTRAL_CATEGORIES,
    NON_REPORTABLE_CATEGORIES,
    TAX_CREDIT_CATEGORIES,
    categories_for_amount,
    category_group,
)
from mtd_classifier.periods import TaxQuarter, quarter_for_date, tax_year_for_date
from mtd_classifier.services.import_service import (
    AlreadyImportedError,
    BankMismatchError,
    DayConflictResolution,
    UnknownFormatError,
    file_hash,
    import_statement,
    normalize_description,
    statement_source_path,
)
from mtd_classifier.services.verification import verification_report, verify_cross_format
from mtd_classifier.importers.column_mapping import (
    ColumnMapping,
    guess_header_signature,
    parse_with_mapping,
)
from mtd_classifier.importers.base import ParsedTransaction
from mtd_classifier.storage.database import get_engine, get_session, init_db
from mtd_classifier.storage.repository import ImportOutOfOrderError, Repository

ACCOUNTS_FILE = APP_DIR / "gui_accounts.json"


def _activate_user_paths(name: str) -> None:
    """Switch every per-user path (config-level plus this module's
    accounts file) to the given user."""
    global ACCOUNTS_FILE
    config.activate_user(name)
    ACCOUNTS_FILE = config.active_user_dir() / "gui_accounts.json"

LABEL_TO_CATEGORY = {label: cat.value for cat, label in CATEGORY_LABELS.items()}
BANK_CHOICES = ["auto-detect", "firstdirect", "santander"]
ALL_TRANSACTIONS_LABEL = "All transactions"

# Category pickers are grouped: quarterly SA103/SA105 boxes first, then the
# year-end / not-reported categories. In the combobox the headers are
# rendered as non-selectable divider rows (picking one just clears back).
CATEGORY_GROUP_ORDER = ("SA103", "SA105", "OTHER")
CATEGORY_GROUP_TITLES = {
    "SA103": "SA103 · Self-employment",
    "SA105": "SA105 · UK property",
    "OTHER": "Year-end / not reported",
}
CATEGORY_GROUP_HEADERS = {
    key: f"—— {title} ——" for key, title in CATEGORY_GROUP_TITLES.items()
}


def grouped_category_labels(allowed: list[TransactionCategory]) -> list[str]:
    """Combobox values: a divider header before each group's labels."""
    values: list[str] = []
    for key in CATEGORY_GROUP_ORDER:
        group = [CATEGORY_LABELS[c] for c in allowed if category_group(c) == key]
        if group:
            values.append(CATEGORY_GROUP_HEADERS[key])
            values.extend(group)
    return values


class MenuTooltip:
    """HMRC-guidance bubble for a menu's highlighted entry.

    tk menus have no native tooltips, but <<MenuSelect>> reports the active
    entry - a small borderless Toplevel follows the pointer with the
    guidance text for the hovered category.
    """

    def __init__(self, menu: tk.Menu, texts: dict[int, str]) -> None:
        self.menu = menu
        self.texts = texts
        self.tip: tk.Toplevel | None = None
        self.label: tk.Label | None = None
        menu.bind("<<MenuSelect>>", self._on_select)
        menu.bind("<Unmap>", lambda e: self._hide())
        menu.bind("<Destroy>", lambda e: self._hide())

    def _on_select(self, _event=None) -> None:
        try:
            active = self.menu.index("active")
        except tk.TclError:
            active = None
        text = self.texts.get(active) if active is not None else None
        if text:
            self._show(text)
        else:
            self._hide()

    def _show(self, text: str) -> None:
        try:
            if self.tip is None:
                self.tip = tk.Toplevel(self.menu)
                self.tip.wm_overrideredirect(True)
                try:
                    self.tip.attributes("-topmost", True)
                except tk.TclError:
                    pass
                self.label = tk.Label(
                    self.tip, text="", justify="left", wraplength=340,
                    bg=theme.BUTTER, fg=theme.INK, relief="solid",
                    borderwidth=1, font=theme.fnt(9), padx=8, pady=6,
                )
                self.label.pack()
            self.label.configure(text=text)
            x = self.menu.winfo_pointerx() + 18
            y = self.menu.winfo_pointery() + 14
            self.tip.wm_geometry(f"+{x}+{y}")
            self.tip.deiconify()
        except tk.TclError:
            pass

    def _hide(self) -> None:
        if self.tip is not None:
            try:
                self.tip.withdraw()
            except tk.TclError:
                pass


def load_settings() -> dict:
    if ACCOUNTS_FILE.exists():
        try:
            data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {}


def save_settings(settings: dict) -> None:
    ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def load_saved_accounts() -> list[dict]:
    return load_settings().get("accounts", [])


def prompt_ambiguous_sign(parent: tk.Widget, txn: ParsedTransaction) -> str:
    """Ask whether an unsignable PDF import line is money in, money out, or
    should be left out of the database entirely.

    Returns "positive", "negative" or "skip". Closing the dialog counts as
    "skip" - safer than silently guessing a sign for money that couldn't be
    verified against the statement's balance movement.
    """
    dialog = tk.Toplevel(parent)
    dialog.title("Ambiguous statement line")
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(False, False)

    amount = abs(txn.amount)
    ttk.Label(
        dialog,
        text=(
            "Could not tell whether this line is money in or out - it didn't "
            "reconcile against the statement's balance movement.\n\n"
            f"{txn.date}   {amount:,.2f}   {txn.description}\n\n"
            f"Statement line:\n{txn.raw_data or '(not available)'}"
        ),
        justify="left", wraplength=420,
    ).pack(padx=16, pady=(16, 10), anchor="w")

    result: list[str] = []

    def choose(value: str) -> None:
        result.append(value)
        close_window(dialog)

    fade_in(dialog)

    button_row = ttk.Frame(dialog)
    button_row.pack(padx=16, pady=(0, 16), fill="x")
    ttk.Button(
        button_row, text=f"Money in (+{amount:,.2f})",
        command=lambda: choose("positive"),
    ).pack(side="left", expand=True, fill="x", padx=(0, 4))
    ttk.Button(
        button_row, text=f"Money out (-{amount:,.2f})",
        command=lambda: choose("negative"),
    ).pack(side="left", expand=True, fill="x", padx=4)
    ttk.Button(
        button_row, text="Skip - I'll add it manually",
        command=lambda: choose("skip"),
    ).pack(side="left", expand=True, fill="x", padx=(4, 0))

    dialog.protocol("WM_DELETE_WINDOW", lambda: choose("skip"))
    dialog.wait_window()
    return result[0] if result else "skip"


def prompt_day_conflict(
    parent: tk.Widget, txn_date, identity: str, existing, incoming,
) -> DayConflictResolution | None:
    """Ask which transactions should end up in the database for a day where
    existing and incoming statement data disagree in a way that couldn't be
    resolved automatically (see import_service's day-level reconciliation).

    Returns None if the dialog is closed without confirming - the caller
    then leaves that day exactly as it was (nothing added, nothing removed)
    so it can be revisited later.
    """
    dialog = tk.Toplevel(parent)
    dialog.title(f"Choose transactions for {txn_date}")
    dialog.transient(parent)
    dialog.grab_set()
    dialog.geometry("580x480")
    dialog.minsize(480, 360)
    dialog.columnconfigure(0, weight=1)
    dialog.rowconfigure(1, weight=1)

    header = ttk.Frame(dialog, padding=(14, 14, 14, 0))
    header.grid(row=0, column=0, sticky="ew")
    ttk.Label(
        header, text=f"{identity}  -  {txn_date}",
        font=theme.fnt_semibold(11),
    ).pack(anchor="w")
    ttk.Label(
        header,
        text="Couldn't automatically tell which of these describe the same "
        "real transaction(s). Tick everything that should end up in the "
        "database, then confirm.",
        wraplength=540, foreground=theme.INK_MUTED,
    ).pack(anchor="w", pady=(2, 0))

    body = ttk.Frame(dialog, padding=14)
    body.grid(row=1, column=0, sticky="nsew")
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)

    canvas = tk.Canvas(body, highlightthickness=0, bg=theme.PAPER)
    scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
    inner = ttk.Frame(canvas)
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")

    ttk.Label(
        inner, text="Already recorded (untick to remove):",
        font=theme.fnt_semibold(9),
    ).pack(anchor="w", pady=(0, 4))
    existing_vars: list[tuple[tk.BooleanVar, object]] = []
    for t in existing:
        var = tk.BooleanVar(value=True)
        existing_vars.append((var, t))
        src = t.import_log.filename if t.import_log is not None else "unknown file"
        ttk.Checkbutton(
            inner, variable=var,
            text=f"{t.amount:,.2f}   {t.description}   (from {src})",
        ).pack(anchor="w")

    ttk.Label(
        inner, text="In the file being imported (tick to add):",
        font=theme.fnt_semibold(9),
    ).pack(anchor="w", pady=(12, 4))
    incoming_vars: list[tuple[tk.BooleanVar, object]] = []
    for t in incoming:
        var = tk.BooleanVar(value=False)
        incoming_vars.append((var, t))
        ttk.Checkbutton(
            inner, variable=var,
            text=f"{t.amount:,.2f}   {t.description}",
        ).pack(anchor="w")

    result: list[DayConflictResolution] = []

    def confirm() -> None:
        result.append(DayConflictResolution(
            keep_incoming=[t for var, t in incoming_vars if var.get()],
            delete_existing_ids=[t.id for var, t in existing_vars if not var.get()],
        ))
        close_window(dialog)

    button_row = ttk.Frame(dialog, padding=14)
    button_row.grid(row=2, column=0, sticky="ew")
    ttk.Button(button_row, text="Confirm selection", command=confirm).pack(side="left")
    ttk.Button(
        button_row, text="Skip this day for now",
        command=lambda: close_window(dialog),
    ).pack(side="right")

    fade_in(dialog)
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.wait_window()
    return result[0] if result else None


def prompt_same_transaction(
    parent: tk.Widget, incoming: ParsedTransaction, existing, incoming_file: Path,
) -> tuple[bool, bool]:
    """Ask whether an incoming line and an already-recorded line - same day,
    same amount, different description - are the same real transaction.

    Shows the two side by side, each with a link that opens its source
    statement. Returns (same, apply_to_rest_of_file). Closing the dialog
    counts as "different": the line is imported, as it would have been
    before this check existed, and can be removed with Undo Import.
    """
    from mtd_classifier.services.attachments import open_file

    dialog = tk.Toplevel(parent, bg=theme.PAPER)
    dialog.title("Same transaction?")
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(True, False)
    dialog.columnconfigure((0, 1), weight=1, uniform="side")

    ttk.Label(
        dialog, text=f"{incoming.date}    {incoming.amount:,.2f}",
        font=theme.fnt_semibold(12),
    ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(16, 2))
    ttk.Label(
        dialog,
        text="A transaction already recorded for this day has the same amount "
        "but a different description. Are they the same real transaction?",
        wraplength=620, foreground=theme.INK_MUTED, justify="left",
    ).grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 12))

    log = existing.import_log
    sides = (
        ("Already recorded", existing.description, existing.raw_data,
         log.filename if log is not None else None,
         statement_source_path(log, near=incoming_file)),
        ("In the file being imported", incoming.description, incoming.raw_data,
         incoming_file.name, incoming_file),
    )

    def opener(path: Path):
        def handler(_event=None) -> None:
            try:
                open_file(path)
            except OSError as e:
                messagebox.showerror("Can't open file", f"{path}\n\n{e}", parent=dialog)
        return handler

    for col, (heading, desc, raw, filename, path) in enumerate(sides):
        box = tk.Frame(
            dialog, bg=theme.PAPER_RAISED, highlightthickness=1,
            highlightbackground=theme.RULE, padx=12, pady=10,
        )
        box.grid(row=2, column=col, sticky="nsew",
                 padx=(16, 6) if col == 0 else (6, 16))
        bg = {"bg": theme.PAPER_RAISED}
        tk.Label(box, text=heading.upper(), font=theme.fnt_semibold(8),
                 fg=theme.INK_MUTED, **bg).pack(anchor="w")
        tk.Label(box, text=desc, font=theme.fnt_semibold(11), fg=theme.INK,
                 wraplength=280, justify="left", **bg).pack(anchor="w", pady=(4, 8))
        tk.Label(box, text="Statement line:", font=theme.fnt(8),
                 fg=theme.INK_MUTED, **bg).pack(anchor="w")
        tk.Label(box, text=raw or "(not available)", font=("Consolas", 9),
                 fg=theme.INK, wraplength=280, justify="left", **bg,
                 ).pack(anchor="w", pady=(0, 8))
        tk.Label(box, text=f"From: {filename or 'unknown file'}", font=theme.fnt(9),
                 fg=theme.INK, wraplength=280, justify="left", **bg).pack(anchor="w")
        if path is not None:
            link = tk.Label(
                box, text="Open statement ↗", cursor="hand2",
                font=theme.fnt(9) + ("underline",), fg=theme.SKY_INK, **bg,
            )
            link.pack(anchor="w", pady=(2, 0))
            link.bind("<Button-1>", opener(path))
            tk.Label(box, text=str(path.parent), font=theme.fnt(8),
                     fg=theme.INK_MUTED, wraplength=280, justify="left", **bg,
                     ).pack(anchor="w")
        else:
            tk.Label(
                box, text="File location not known - it was imported before "
                "locations were recorded, or has since moved.",
                font=theme.fnt(8), fg=theme.INK_MUTED, wraplength=280,
                justify="left", **bg,
            ).pack(anchor="w", pady=(2, 0))

    apply_rest = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        dialog, variable=apply_rest,
        text=f"Use the same answer for the rest of {incoming_file.name}",
    ).grid(row=3, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 0))

    result: list[bool] = []

    def choose(same: bool) -> None:
        result.append(same)
        close_window(dialog)

    buttons = ttk.Frame(dialog)
    buttons.grid(row=4, column=0, columnspan=2, sticky="ew", padx=16, pady=16)
    ttk.Button(
        buttons, text="Same transaction - don't import it again",
        command=lambda: choose(True),
    ).pack(side="left", expand=True, fill="x", padx=(0, 4))
    ttk.Button(
        buttons, text="Different transactions - import it",
        command=lambda: choose(False),
    ).pack(side="left", expand=True, fill="x", padx=(4, 0))

    fade_in(dialog)
    dialog.protocol("WM_DELETE_WINDOW", lambda: choose(False))
    dialog.wait_window()
    return (result[0] if result else False), apply_rest.get()


def select_user(preselect: str | None = None) -> str | None:
    """Standalone user picker shown before the main window exists (on
    startup and again after a Switch User). Returns the chosen user's
    name, or None if the window is closed (caller exits).
    """
    from tkinter import simpledialog

    root = tk.Tk()
    root.title("MTD Classifier - Choose User")
    root.resizable(False, False)
    apply_theme(root)

    ttk.Label(
        root, text="Who is using MTD Classifier?",
        font=theme.fnt_semibold(12),
    ).pack(padx=20, pady=(18, 4), anchor="w")
    ttk.Label(
        root, text="Each user has their own accounts, transactions and "
        "attachments, kept completely separate.",
        wraplength=340, foreground=theme.INK_MUTED,
    ).pack(padx=20, anchor="w")

    listbox = tk.Listbox(root, height=6, width=38)
    theme.style_listbox(listbox)
    listbox.pack(padx=20, pady=10, fill="both")

    def reload_users(select_name: str | None = None) -> None:
        listbox.delete(0, "end")
        users = config.list_users()
        for name in users:
            listbox.insert("end", name)
        target = select_name or preselect
        index = users.index(target) if target in users else 0
        listbox.selection_set(index)
        listbox.see(index)

    result: list[str] = []

    def open_selected(event=None) -> None:
        selection = listbox.curselection()
        if not selection:
            return
        result.append(listbox.get(selection[0]))
        close_window(root)

    def add_user() -> None:
        name = simpledialog.askstring("Add user", "Name for the new user:", parent=root)
        if not name:
            return
        try:
            config.add_user(name)
        except ValueError as e:
            messagebox.showerror("Add user", str(e), parent=root)
            return
        reload_users(select_name=name.strip())

    def rename_user() -> None:
        selection = listbox.curselection()
        if not selection:
            return
        old = listbox.get(selection[0])
        new = simpledialog.askstring(
            "Rename user", f"New name for {old}:", parent=root, initialvalue=old,
        )
        if not new or new == old:
            return
        try:
            config.rename_user(old, new)
        except ValueError as e:
            messagebox.showerror("Rename user", str(e), parent=root)
            return
        reload_users(select_name=new.strip())

    listbox.bind("<Double-Button-1>", open_selected)
    listbox.bind("<Return>", open_selected)

    button_row = ttk.Frame(root)
    button_row.pack(padx=20, pady=(0, 18), fill="x")
    ttk.Button(button_row, text="Open", style="Accent.TButton",
               command=open_selected).pack(side="left")
    ttk.Button(button_row, text="Add User...", command=add_user).pack(side="left", padx=6)
    ttk.Button(button_row, text="Rename...", command=rename_user).pack(side="left")

    reload_users()
    listbox.focus_set()
    fade_in(root, 200)
    root.mainloop()
    return result[0] if result else None


COLUMN_ROLES = (
    "Ignore", "Date", "Description", "Amount (signed)",
    "Paid out", "Paid in", "Balance",
)


def prompt_column_mapping(
    parent: tk.Widget, file_path: Path, rows: list,
) -> ColumnMapping | None:
    """Ask which columns hold which values in an unrecognised tabular file,
    showing real rows from the file as examples.

    Returns a validated ColumnMapping, or None if the user cancels (the
    file is then reported as format-not-recognised, same as before).
    """
    dialog = tk.Toplevel(parent)
    dialog.title(f"Map columns - {file_path.name}")
    dialog.transient(parent)
    dialog.grab_set()
    dialog.geometry("760x560")
    dialog.minsize(620, 420)
    dialog.columnconfigure(0, weight=1)
    dialog.rowconfigure(1, weight=1)

    header = ttk.Frame(dialog, padding=(14, 14, 14, 0))
    header.grid(row=0, column=0, sticky="ew")
    ttk.Label(
        header, text=f"{file_path.name} isn't a recognised statement format",
        font=theme.fnt_semibold(11),
    ).pack(anchor="w")
    ttk.Label(
        header,
        text="Tell the app which column holds which value - the rows below "
        "are real data from this file. Pick either one signed Amount column, "
        "or a Paid out / Paid in pair. Extra Description columns are joined "
        "together. The mapping is remembered for future files with this layout.",
        wraplength=700, foreground=theme.INK_MUTED,
    ).pack(anchor="w", pady=(2, 0))

    n_cols = max((len(r) for r in rows[:30]), default=0)
    preview_rows = [r for r in rows[:30] if any(str(c or "").strip() for c in r)][:12]

    body = ttk.Frame(dialog, padding=14)
    body.grid(row=1, column=0, sticky="nsew")
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=2)
    body.rowconfigure(1, weight=1)

    # Real-data preview
    tree_frame = ttk.Frame(body)
    tree_frame.grid(row=0, column=0, sticky="nsew")
    tree_frame.columnconfigure(0, weight=1)
    tree_frame.rowconfigure(0, weight=1)
    columns = [f"c{i}" for i in range(n_cols)]
    tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=8)
    for i, col in enumerate(columns):
        tree.heading(col, text=f"Column {i + 1}")
        tree.column(col, width=110, stretch=True)
    for r in preview_rows:
        values = [str(r[i]) if i < len(r) and r[i] is not None else "" for i in range(n_cols)]
        tree.insert("", "end", values=values)
    tree.grid(row=0, column=0, sticky="nsew")
    xscroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
    tree.configure(xscrollcommand=xscroll.set)
    xscroll.grid(row=1, column=0, sticky="ew")

    # Role pickers, one per column, with a sample value alongside
    picker_frame = ttk.Frame(body)
    picker_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
    canvas = tk.Canvas(picker_frame, highlightthickness=0, bg=theme.PAPER)
    scrollbar = ttk.Scrollbar(picker_frame, orient="vertical", command=canvas.yview)
    inner = ttk.Frame(canvas)
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    picker_frame.columnconfigure(0, weight=1)
    picker_frame.rowconfigure(0, weight=1)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")

    def samples_for(col: int) -> str:
        seen = []
        for r in rows:
            if col < len(r) and str(r[col] or "").strip():
                seen.append(str(r[col]).strip()[:28])
            if len(seen) == 3:
                break
        return " | ".join(seen)

    role_vars: list[tk.StringVar] = []
    for i in range(n_cols):
        row_frame = ttk.Frame(inner)
        row_frame.pack(fill="x", pady=1)
        ttk.Label(row_frame, text=f"Column {i + 1}", width=10).pack(side="left")
        var = tk.StringVar(value="Ignore")
        role_vars.append(var)
        ttk.Combobox(
            row_frame, textvariable=var, values=COLUMN_ROLES,
            state="readonly", width=16,
        ).pack(side="left", padx=(0, 8))
        ttk.Label(
            row_frame, text=samples_for(i), foreground=theme.INK_MUTED,
        ).pack(side="left", fill="x")

    status_var = tk.StringVar(value="")
    result: list[ColumnMapping] = []

    def build_mapping() -> "ColumnMapping | str":
        """Returns a mapping, or an error message string."""
        roles: dict[str, list[int]] = {}
        for i, var in enumerate(role_vars):
            roles.setdefault(var.get(), []).append(i)
        for unique in ("Date", "Amount (signed)", "Paid out", "Paid in", "Balance"):
            if len(roles.get(unique, [])) > 1:
                return f"Only one column can be {unique}."
        if "Date" not in roles:
            return "Pick a Date column."
        if not roles.get("Description"):
            return "Pick at least one Description column."
        has_amount = "Amount (signed)" in roles
        has_pair = "Paid out" in roles or "Paid in" in roles
        if not has_amount and not has_pair:
            return "Pick an Amount column, or Paid out / Paid in."
        if has_amount and has_pair:
            return "Pick either Amount or the Paid out/in pair - not both."
        mapping = ColumnMapping(
            date_col=roles["Date"][0],
            description_cols=roles["Description"],
            amount_col=roles["Amount (signed)"][0] if has_amount else None,
            debit_col=roles.get("Paid out", [None])[0],
            credit_col=roles.get("Paid in", [None])[0],
            balance_col=roles.get("Balance", [None])[0],
        )
        parsed = list(parse_with_mapping(rows, mapping))
        if not parsed:
            return "That mapping doesn't parse any transactions - check the Date column."
        mapping.header_signature = guess_header_signature(rows, mapping)
        mapping.name = file_path.stem
        return mapping

    def confirm() -> None:
        outcome = build_mapping()
        if isinstance(outcome, str):
            status_var.set(outcome)
            return
        parsed = list(parse_with_mapping(rows, outcome))
        first = parsed[0]
        if not messagebox.askyesno(
            "Confirm mapping",
            f"{len(parsed)} transactions parsed. First one:\n\n"
            f"{first.date}   {first.amount:,.2f}   {first.description}\n\n"
            "Does that look right?",
            parent=dialog,
        ):
            return
        result.append(outcome)
        close_window(dialog)

    footer = ttk.Frame(dialog, padding=14)
    footer.grid(row=2, column=0, sticky="ew")
    ttk.Label(footer, textvariable=status_var, foreground=theme.BRICK).pack(side="left")
    ttk.Button(
        footer, text="Cancel", command=lambda: close_window(dialog),
    ).pack(side="right")
    fade_in(dialog)
    ttk.Button(footer, text="Import with this mapping", command=confirm).pack(
        side="right", padx=(0, 8)
    )

    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.wait_window()
    return result[0] if result else None


def prompt_confirm_period(
    parent: tk.Widget, file_path: Path, period, account_label: str,
):
    """Ask the user to confirm the date range an export covers, when it
    could only be estimated from its first and last transactions (quiet
    days at the edges are invisible to that estimate).

    Returns a (start, end) date tuple, or None to keep the estimate.
    """
    dialog = tk.Toplevel(parent)
    dialog.title(f"Confirm export date range - {file_path.name}")
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(False, False)

    start_guess, end_guess = period if period else ("", "")
    ttk.Label(
        dialog,
        text=(
            f"Please confirm the date range you exported that is represented "
            f"by these transactions on {account_label}.\n\n"
            f"{file_path.name}: the first transaction is {start_guess} and the "
            f"last is {end_guess}, but the export may cover extra days on "
            "which nothing happened. Knowing the real range matters when "
            "checking a quarter's data is complete before HMRC output."
        ),
        justify="left", wraplength=460,
    ).pack(padx=16, pady=(16, 10), anchor="w")

    entry_row = ttk.Frame(dialog)
    entry_row.pack(padx=16, anchor="w")
    ttk.Label(entry_row, text="Covers from (YYYY-MM-DD):").grid(row=0, column=0, sticky="w")
    start_var = tk.StringVar(value=str(start_guess))
    ttk.Entry(entry_row, textvariable=start_var, width=14).grid(row=0, column=1, padx=(6, 0))
    ttk.Label(entry_row, text="to:").grid(row=1, column=0, sticky="w", pady=(4, 0))
    end_var = tk.StringVar(value=str(end_guess))
    ttk.Entry(entry_row, textvariable=end_var, width=14).grid(row=1, column=1, padx=(6, 0), pady=(4, 0))

    status_var = tk.StringVar(value="")
    ttk.Label(dialog, textvariable=status_var, foreground=theme.BRICK).pack(padx=16, anchor="w")

    result: list = []

    def confirm() -> None:
        try:
            start = date.fromisoformat(start_var.get().strip())
            end = date.fromisoformat(end_var.get().strip())
        except ValueError:
            status_var.set("Enter dates as YYYY-MM-DD.")
            return
        if start > end:
            status_var.set("The start date is after the end date.")
            return
        if period and (start > period[0] or end < period[1]):
            status_var.set(
                f"The range must include all transactions ({period[0]} to {period[1]})."
            )
            return
        result.append((start, end))
        close_window(dialog)

    button_row = ttk.Frame(dialog)
    button_row.pack(padx=16, pady=(10, 16), fill="x")
    ttk.Button(button_row, text="Confirm range", command=confirm).pack(side="left")
    ttk.Button(
        button_row, text="Keep transaction dates",
        command=lambda: close_window(dialog),
    ).pack(side="right")
    fade_in(dialog)

    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.wait_window()
    return result[0] if result else None


def prompt_refund_pair(parent: tk.Widget, txn, candidates, repo):
    """Pick the other half of a refund pair for ``txn`` from ``candidates``
    (already filtered to the opposite sign). Exact opposite-amount matches
    are listed first, then everything else newest first.

    Returns the chosen transaction, or None if the dialog is cancelled.
    """
    dialog = tk.Toplevel(parent)
    dialog.title("Select the other half of the refund pair")
    dialog.transient(parent)
    dialog.grab_set()
    dialog.geometry("720x440")
    dialog.minsize(560, 320)
    dialog.columnconfigure(0, weight=1)
    dialog.rowconfigure(1, weight=1)

    direction = "money out" if txn.amount >= 0 else "money in"
    header = ttk.Frame(dialog, padding=(14, 14, 14, 0))
    header.grid(row=0, column=0, sticky="ew")
    ttk.Label(
        header, text=f"{txn.date}   {txn.amount:,.2f}   {txn.description}",
        font=theme.fnt_semibold(10),
    ).pack(anchor="w")
    ttk.Label(
        header,
        text=f"Pick the {direction} transaction this pairs with. The later "
        "transaction of the pair is recorded as a refund and inherits the "
        "earlier one's category, so they net against the same HMRC box. "
        "Exact opposite amounts are listed first.",
        wraplength=660, foreground=theme.INK_MUTED,
    ).pack(anchor="w", pady=(2, 0))

    def sort_key(c):
        exact = abs(c.amount + txn.amount) < 0.005
        return (not exact, -c.date.toordinal(), -c.id)

    ordered = sorted(candidates, key=sort_key)

    tree_frame = ttk.Frame(dialog, padding=14)
    tree_frame.grid(row=1, column=0, sticky="nsew")
    tree_frame.columnconfigure(0, weight=1)
    tree_frame.rowconfigure(0, weight=1)
    columns = ("date", "description", "amount", "category")
    tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
    tree.heading("date", text="Date")
    tree.heading("description", text="Description")
    tree.heading("amount", text="Amount")
    tree.heading("category", text="Category")
    tree.column("date", width=90, anchor="w", stretch=False)
    tree.column("description", width=330, anchor="w")
    tree.column("amount", width=90, anchor="e", stretch=False)
    tree.column("category", width=150, anchor="w", stretch=False)
    tree.tag_configure("exact", background=theme.MINT)
    by_iid = {}
    for c in ordered:
        cat = repo.get_categorisation(c.id)
        cat_label = ""
        if cat and cat.category in [x.value for x in TransactionCategory]:
            cat_label = CATEGORY_LABELS[TransactionCategory(cat.category)]
        elif cat:
            cat_label = cat.category
        tags = ("exact",) if abs(c.amount + txn.amount) < 0.005 else ()
        iid = str(c.id)
        by_iid[iid] = c
        tree.insert(
            "", "end", iid=iid,
            values=(c.date.isoformat(), c.description, f"{c.amount:,.2f}", cat_label),
            tags=tags,
        )
    tree.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.grid(row=0, column=1, sticky="ns")
    if ordered:
        tree.selection_set(str(ordered[0].id))

    result: list = []

    def confirm(_event=None) -> None:
        selection = tree.selection()
        if not selection:
            return
        result.append(by_iid[selection[0]])
        close_window(dialog)

    tree.bind("<Double-Button-1>", confirm)

    button_row = ttk.Frame(dialog, padding=(14, 0, 14, 14))
    button_row.grid(row=2, column=0, sticky="ew")
    ttk.Button(
        button_row, text="Pair as refund", style="Accent.TButton", command=confirm,
    ).pack(side="left")
    ttk.Button(
        button_row, text="Cancel", command=lambda: close_window(dialog),
    ).pack(side="right")

    fade_in(dialog)
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.wait_window()
    return result[0] if result else None


class AccountTab(ttk.Frame):
    """One bank account: batch import plus line-item consolidation."""

    def __init__(self, notebook: ttk.Notebook, app: "MtdApp",
                 label: str, bank: str | None, account: str | None) -> None:
        super().__init__(notebook)
        self.app = app
        self.label = label
        self.bank = bank          # bank hint for the importer, None = auto-detect
        self.account = account    # account label stored on transactions

        # --- toolbar ---
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=6, pady=6)
        self.import_btn = ttk.Button(
            toolbar, text="Import Statements...", command=self.import_files,
            style="Accent.TButton",
        )
        self.import_btn.pack(side="left")
        self.import_folder_btn = ttk.Button(
            toolbar, text="Import Folder...", command=self.import_folder,
            style="Peach.TButton",
        )
        self.import_folder_btn.pack(side="left", padx=(6, 0))
        self.autoclassify_btn = ttk.Button(
            toolbar, text="Auto-classify", command=self.auto_classify,
            style="Sage.TButton",
        )
        self.autoclassify_btn.pack(side="left", padx=(6, 0))
        self.import_history_btn = ttk.Button(
            toolbar, text="Import History/Undo Imports...", command=self.show_import_history,
            style="Sky.TButton",
        )
        self.import_history_btn.pack(side="left", padx=(6, 0))

        # Categorisation controls get their own row so nothing clips at
        # narrow window widths
        filter_row = ttk.Frame(self)
        filter_row.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(filter_row, text="Category:").pack(side="left")
        self.category_var = tk.StringVar()
        self.category_combo = ttk.Combobox(
            filter_row, textvariable=self.category_var, state="readonly", width=28,
            values=grouped_category_labels(list(TransactionCategory)),
        )
        self.category_combo.pack(side="left", padx=4)
        self.category_combo.bind("<<ComboboxSelected>>", self._on_category_picked)
        self.apply_btn = ttk.Button(
            filter_row, text="Apply to Selected", command=self.apply_category,
            style="Accent.TButton",
        )
        self.apply_btn.pack(side="left")
        self.uncategorised_only = tk.BooleanVar(value=False)
        self.uncat_check = ttk.Checkbutton(
            filter_row, text="Show uncategorised only", variable=self.uncategorised_only,
            command=self.refresh,
        )
        self.uncat_check.pack(side="right", padx=(10, 4))

        # Live search: the list re-filters on every keystroke. The context
        # menu's "Filter transactions like this" fills this same box.
        ttk.Label(filter_row, text="Search:").pack(side="left", padx=(14, 0))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh())
        self.search_entry = ttk.Entry(filter_row, textvariable=self.search_var, width=24)
        self.search_entry.pack(side="left", padx=4)
        self.clear_search_btn = ttk.Button(
            filter_row, text="Clear", command=lambda: self.search_var.set(""),
        )
        self.clear_search_btn.pack(side="left")

        # --- transaction list ---
        self._headings = {
            "date": "Date", "description": "Description", "amount": "Amount",
            "category": "Category", "confidence": "How",
        }
        self._sort_column: str | None = None
        self._sort_reverse = False
        columns = tuple(self._headings)
        tree_frame = ttk.Frame(self)
        tree_frame.pack(side="top", fill="both", expand=True, padx=(6, 0))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        # Canvas-drawn table: faint hairlines between rows, which
        # ttk.Treeview can't draw (see gui/table.py)
        self.tree = LedgerTable(tree_frame, columns=columns)
        for col in columns:
            self.tree.heading(
                col, text=self._headings[col],
                command=lambda c=col: self.sort_by(c),
            )
        self.tree.column("date", width=90, anchor="w", stretch=False)
        self.tree.column("description", width=380, anchor="w")
        self.tree.column("amount", width=90, anchor="e", stretch=False)
        self.tree.column("category", width=170, anchor="w", stretch=False)
        self.tree.column("confidence", width=70, anchor="w", stretch=False)
        self.tree.tag_configure("uncategorised", background=theme.STRAW)
        self.tree.tag_configure("refund", background=theme.MINT)
        self.tree.tag_configure("money_in", background=theme.SAGE)

        # The scrollbar lives in the same frame as the list it scrolls, so
        # it starts and ends exactly at the list's top and bottom edges.
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Button-3>", self._show_context_menu)

        # --- details / refund panel ---
        details = ttk.LabelFrame(self, text="Selected line: statement extract & refunds")
        details.pack(fill="x", padx=6, pady=6)
        self.details_frame = details
        self.extract_var = tk.StringVar(value="Select a transaction to see its original statement line.")
        ttk.Label(
            details, textvariable=self.extract_var, wraplength=900,
            font=getattr(app, "ledger_font_small", None),
        ).pack(anchor="w", padx=6, pady=(4, 2))
        refund_row = ttk.Frame(details)
        refund_row.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(refund_row, text="Possible refund/reversal of:").pack(side="left")
        self.refund_var = tk.StringVar()
        self.refund_combo = ttk.Combobox(
            refund_row, textvariable=self.refund_var, state="disabled", width=60
        )
        self.refund_combo.pack(side="left", padx=6)
        self.refund_btn = ttk.Button(
            refund_row, text="Mark as Refund", command=self.mark_refund, state="disabled"
        )
        self.refund_btn.pack(side="left")
        self._refund_candidates: list = []

        attach_row = ttk.Frame(details)
        attach_row.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(attach_row, text="Receipts/invoices:").pack(side="left")
        self.attachments_var = tk.StringVar(value="")
        ttk.Label(attach_row, textvariable=self.attachments_var, foreground=theme.INK_MUTED).pack(
            side="left", padx=6
        )
        self.attach_btn = ttk.Button(
            attach_row, text="Attach File...", command=self.attach_file, state="disabled"
        )
        self.attach_btn.pack(side="left", padx=(0, 4))
        self.open_attachment_btn = ttk.Button(
            attach_row, text="Open...", command=self.open_attachment, state="disabled"
        )
        self.open_attachment_btn.pack(side="left", padx=(0, 4))
        self.remove_attachment_btn = ttk.Button(
            attach_row, text="Remove...", command=self.remove_attachment, state="disabled"
        )
        self.remove_attachment_btn.pack(side="left")
        self._attachments: list = []

        self.refresh()

    # --- data ---

    def _transactions(self):
        """Transactions for this account, scoped to the app's selected tax
        year (6 April to 5 April) - never bleeds in adjacent tax years.
        app.tax_year is None for "All transactions", which lifts the filter."""
        repo = self.app.repo
        start = end = None
        if self.app.tax_year is not None:
            start = date(self.app.tax_year, 4, 6)
            end = date(self.app.tax_year + 1, 4, 5)
        if self.account is not None or self.bank is not None:
            return repo.get_transactions_for_account(
                bank=self.bank, account=self.account, start_date=start, end_date=end,
            )
        return repo.get_all_transactions_for_review(start_date=start, end_date=end)

    def refresh(self) -> None:
        repo = self.app.repo
        self.tree.delete(*self.tree.get_children())
        query = self.search_var.get().strip().lower()
        rows: list[tuple] = []
        for txn in self._transactions():
            cat = repo.get_categorisation(txn.id)
            if self.uncategorised_only.get() and cat is not None:
                continue
            if cat is None:
                cat_label, confidence, tags = "", "", ("uncategorised",)
            else:
                known = cat.category in [c.value for c in TransactionCategory]
                cat_label = CATEGORY_LABELS.get(
                    TransactionCategory(cat.category), cat.category
                ) if known else cat.category
                if getattr(cat, "disallowable", False) or (
                    known
                    and TransactionCategory(cat.category) in ALWAYS_DISALLOWABLE_CATEGORIES
                ):
                    cat_label += " (disallowable)"
                confidence = cat.confidence
                tags = ("refund",) if cat.confidence == "refund" else ()
            if query:
                haystack = " ".join((
                    txn.date.isoformat(), txn.description.lower(),
                    normalize_description(txn.description),
                    f"{txn.amount:,.2f}", cat_label.lower(), confidence,
                ))
                if query not in haystack:
                    continue
            if not tags and txn.amount > 0:
                tags = ("money_in",)
            rows.append((txn, cat_label, confidence, tags))

        if self._sort_column is not None:
            sort_keys = {
                "date": lambda r: (r[0].date, r[0].id),
                "description": lambda r: r[0].description.lower(),
                "amount": lambda r: r[0].amount,
                "category": lambda r: r[1].lower(),
                "confidence": lambda r: r[2],
            }
            rows.sort(key=sort_keys[self._sort_column], reverse=self._sort_reverse)

        for txn, cat_label, confidence, tags in rows:
            self.tree.insert(
                "", "end", iid=str(txn.id),
                values=(txn.date.isoformat(), txn.description,
                        f"{txn.amount:,.2f}", cat_label, confidence),
                tags=tags,
            )
        status = f"{self.label}: {len(rows)} transactions shown"
        if query:
            status += f' (filtered on "{self.search_var.get().strip()}")'
        self.app.set_status(status)

    def sort_by(self, column: str) -> None:
        """Heading click: sort by that column; a second click reverses it."""
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False
        for col, text in self._headings.items():
            suffix = ""
            if col == self._sort_column:
                suffix = "  ▼" if self._sort_reverse else "  ▲"
            self.tree.heading(col, text=text + suffix)
        self.refresh()

    # --- import ---

    def import_files(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self,
            title=f"Select ALL monthly statements for {self.label}",
            filetypes=[
                ("Bank statements", "*.csv *.txt *.ofx *.xlsx *.xls *.pdf"),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return
        self._import_paths([Path(p) for p in paths], folder_mode=False)

    def import_folder(self) -> None:
        """Scan a whole folder, keeping only the selected tax year's transactions."""
        folder = filedialog.askdirectory(
            parent=self,
            title=f"Folder of statements for {self.label}",
        )
        if not folder:
            return
        paths = sorted(
            p for p in Path(folder).rglob("*")
            if p.is_file()
            and p.suffix.lower() in (".csv", ".txt", ".ofx", ".xlsx", ".xls", ".pdf")
        )
        if not paths:
            messagebox.showinfo(
                "No statements found",
                "No .csv, .txt, .ofx, .xlsx, .xls or .pdf files in that folder "
                "or its subfolders.",
                parent=self,
            )
            return
        self._import_paths(paths, folder_mode=True)

    def _import_paths(self, paths: list[Path], folder_mode: bool) -> None:
        repo = self.app.repo
        imported = dups = already = failed = mismatched = out_of_range = 0
        ambiguous_skipped = 0
        day_conflicts = day_replaced = same_confirmed = 0
        skipped_empty: list[str] = []
        errors: list[str] = []

        # Folder mode pulls in only the selected tax year. Files are re-scanned
        # with force=True so statements spanning a tax-year boundary can
        # contribute their remaining transactions when the year changes -
        # transaction-level dedup stops anything importing twice.
        date_range = self.app.tax_year_range() if folder_mode else None

        def on_duplicate(txn, previous) -> bool:
            prev_line = previous.raw_data if previous is not None else "(not available)"
            prev_src = ""
            if previous is not None and previous.import_log is not None:
                prev_src = f"\n(recorded from {previous.import_log.filename})"
            return messagebox.askyesno(
                "Potential duplicate",
                f"{txn.date}  {txn.amount:,.2f}  {txn.description}\n\n"
                f"Statement line being imported:\n{txn.raw_data or '(not available)'}\n\n"
                f"Identical line already recorded:{prev_src}\n{prev_line}\n\n"
                "Import this as a separate transaction?\n"
                "(Yes = it's a genuine second transaction, No = skip as duplicate)",
                parent=self,
            )

        current_file = {"name": ""}

        def on_bank_mismatch(detected: str, expected: str) -> bool:
            return messagebox.askyesno(
                "Different bank detected",
                f"{current_file['name']} looks like a {detected} statement, "
                f"but this account is set up for {expected}.\n\n"
                "Import it into this account anyway?",
                parent=self,
            )

        def on_ambiguous_sign(txn) -> str:
            return prompt_ambiguous_sign(self, txn)

        def on_day_conflict(txn_date, identity, existing, incoming):
            return prompt_day_conflict(self, txn_date, identity, existing, incoming)

        def column_mapping_provider(file_path, rows):
            return prompt_column_mapping(self, file_path, rows)

        # "Use the same answer for the rest of this file" - reset per file
        same_answer_for_file: dict[str, bool] = {}

        def on_same_amount(incoming, existing, incoming_file) -> bool:
            name = current_file["name"]
            if name in same_answer_for_file:
                return same_answer_for_file[name]
            same, apply_rest = prompt_same_transaction(
                self, incoming, existing, incoming_file,
            )
            if apply_rest:
                same_answer_for_file[name] = same
            return same

        def on_confirm_period(file_path, period, transactions):
            # Only interrupt when the estimate looks like partial-quarter
            # coverage - a file spanning its quarters' full extent (up to
            # today) is fine as estimated.
            if period is not None:
                start, end = period
                today = date.today()
                starts_short = start > quarter_for_date(start).start_date
                ends_short = end < min(quarter_for_date(end).end_date, today)
                if not starts_short and not ends_short:
                    return None
            return prompt_confirm_period(self, file_path, period, self.label)

        # Picking files that were imported before: offer to re-scan them
        # rather than silently skipping. Re-scanning never duplicates
        # (transaction-level dedup), but it does pick up lines an earlier
        # import missed and records that this file backs existing lines.
        rescan: set[Path] = set()
        if not folder_mode:
            seen = [p for p in paths if repo.get_import_log_by_hash(file_hash(p))]
            if seen and messagebox.askyesno(
                "Already imported",
                f"{len(seen)} of the selected file(s) were imported before.\n\n"
                "Re-scan them? Nothing will be duplicated - this only picks "
                "up lines an earlier import missed and marks which "
                "transactions this statement backs.\n\n"
                "(No = skip them)",
                parent=self,
            ):
                rescan = set(seen)

        for path in paths:
            current_file["name"] = path.name
            try:
                outcome = import_statement(
                    repo, path,
                    account=self.account,
                    force=folder_mode or path in rescan,
                    on_duplicate=on_duplicate,
                    date_range=date_range,
                    expected_bank=self.bank,
                    on_bank_mismatch=on_bank_mismatch,
                    on_ambiguous_sign=on_ambiguous_sign,
                    on_day_conflict=on_day_conflict,
                    column_mapping_provider=column_mapping_provider,
                    on_confirm_period=on_confirm_period,
                    on_same_amount=on_same_amount,
                )
                imported += outcome.new_count
                same_confirmed += outcome.same_amount_confirmed_count
                dups += outcome.duplicate_count
                out_of_range += outcome.out_of_range_count
                ambiguous_skipped += outcome.ambiguous_skipped_count
                day_conflicts += outcome.day_conflicts
                day_replaced += outcome.day_replaced_count
                if folder_mode and outcome.parsed_count == outcome.out_of_range_count:
                    skipped_empty.append(path.name)
                if self.bank is None:
                    # Remember the detected bank so future imports are
                    # checked against it
                    self.bank = outcome.bank
                    self.app.persist_accounts()
            except AlreadyImportedError:
                already += 1
            except BankMismatchError:
                mismatched += 1
            except UnknownFormatError:
                failed += 1
                errors.append(f"{path.name}: format not recognised")
            except Exception as e:  # a corrupt file must not abort the batch
                failed += 1
                errors.append(f"{path.name}: {e}")

        repo.commit()
        self.refresh()
        self.app.update_quarter_options()

        summary = (
            f"Imported {imported} new transactions from {len(paths)} file(s).\n"
            f"{dups} duplicate transactions skipped, "
            f"{already} file(s) already imported before."
        )
        if folder_mode:
            start, end = date_range
            summary += (
                f"\n{out_of_range} transactions outside tax year "
                f"{self.app.tax_year_var.get()} ({start} to {end}) were ignored."
            )
            if skipped_empty:
                summary += (
                    f"\n{len(skipped_empty)} file(s) had nothing in this tax year: "
                    + ", ".join(skipped_empty)
                )
        if mismatched:
            summary += f"\n{mismatched} file(s) skipped: statement from a different bank."
        if ambiguous_skipped:
            summary += (
                f"\n{ambiguous_skipped} line(s) skipped as ambiguous - "
                "add them manually after checking the paper statement."
            )
        if same_confirmed:
            summary += (
                f"\n{same_confirmed} of those duplicates had a different "
                "description - you confirmed they were the same transaction."
            )
        if day_replaced:
            summary += (
                f"\n{day_replaced} earlier transaction(s) replaced by a fuller "
                "same-day statement from another file format."
            )
        if day_conflicts:
            summary += (
                f"\n{day_conflicts} day(s) need manual review - a file's "
                "transactions for that day didn't match what's already "
                "recorded and weren't imported. Re-import that file to resolve."
            )
        if errors:
            summary += "\n\nProblems:\n" + "\n".join(errors)
        (messagebox.showwarning if errors else messagebox.showinfo)(
            "Import finished", summary, parent=self
        )

    # --- import history / undo ---

    def show_import_history(self) -> None:
        ImportHistoryDialog(self)

    # --- categorisation ---

    def _selected_ids(self) -> list[int]:
        return [int(iid) for iid in self.tree.selection()]

    def apply_category(self) -> None:
        label = self.category_var.get()
        if not label or label not in LABEL_TO_CATEGORY:
            messagebox.showinfo("Pick a category", "Choose a category first.", parent=self)
            return
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo("Nothing selected", "Select one or more lines first.", parent=self)
            return
        self._apply_category(ids, label)

    def _apply_category(self, ids: list[int], label: str) -> None:
        """Set a category on the given lines and save an exact rule for each."""
        repo = self.app.repo
        category = LABEL_TO_CATEGORY[label]
        by_id = {t.id: t for t in self._transactions()}
        # Classifying a highlighted (uncategorised) line moves on to the next
        # highlighted line, so a run of them can be worked through quickly.
        iids = [str(i) for i in ids]
        shown = set(self.tree.get_children())
        was_uncategorised = any(
            i in shown and "uncategorised" in self.tree.item(i, "tags")
            for i in iids
        )
        rows_after = self._rows_after(iids)
        for txn_id in ids:
            repo.set_categorisation(
                raw_transaction_id=txn_id,
                category=category,
                confidence="manual",
                changed_by="user",
            )
            repo.upsert_exact_rule(by_id[txn_id].description, category)
        repo.commit()
        self.refresh()
        moved_to = None
        if was_uncategorised:
            moved_to = self._select_next_uncategorised(rows_after)
        message = f"Set {len(ids)} line(s) to {label} (rule saved for next time)"
        if was_uncategorised:
            # Say where the selection went: the list may have scrolled (or
            # wrapped to the top), taking the line just categorised out of view.
            remaining = sum(
                "uncategorised" in self.tree.item(i, "tags")
                for i in self.tree.get_children()
            )
            if moved_to is None:
                message += " - no uncategorised lines left"
            else:
                d, desc, amount = self.tree.item(moved_to, "values")[:3]
                message += (
                    f" - moved to next uncategorised line: {d} {desc[:40]} "
                    f"{amount} ({remaining} left)"
                )
        if TransactionCategory(category) in CATEGORIES_REQUIRING_CONFIRMATION:
            # Gentle nudge only: employment income never enters quarterly
            # updates, so nothing is blocked - it just needs its gross figure
            # confirmed before the year-end final declaration.
            message += (
                " - reminder: deposits are net pay; confirm the gross figure "
                "on the Summary tab before the year-end final declaration"
            )
        self.app.set_status(message)

    def _rows_after(self, iids: list[str]) -> list[str]:
        """Row ids in display order after the last of `iids`, then wrapping
        round to the top. Taken before a refresh so the order is the one the
        user was looking at (a category sort would otherwise reshuffle it)."""
        children = list(self.tree.get_children())
        positions = [children.index(i) for i in iids if i in children]
        if not positions:
            return children
        last = max(positions)
        return children[last + 1:] + children[:last + 1]

    def _select_next_uncategorised(self, candidates: list[str]) -> str | None:
        """Select and scroll to the first still-highlighted row; return its
        id, or None if no highlighted rows are left."""
        shown = set(self.tree.get_children())
        for iid in candidates:
            if iid in shown and "uncategorised" in self.tree.item(iid, "tags"):
                self.tree.selection_set(iid)
                self.tree.see(iid, animate=True)
                return iid
        return None

    def auto_classify(self) -> None:
        repo = self.app.repo
        classifier = RuleBasedClassifier(db_rules=repo.get_active_rules())
        applied = 0
        needs_confirmation = False
        for txn in self._transactions():
            if repo.get_categorisation(txn.id) is not None:
                continue
            result = classifier.classify(txn.description, txn.amount)
            if result.confidence == "default":
                continue  # don't blanket-apply the personal fallback
            repo.set_categorisation(
                raw_transaction_id=txn.id,
                category=result.category,
                confidence=result.confidence,
                matched_rule_id=result.rule_id,
                changed_by="auto",
            )
            if result.rule_id:
                repo.increment_rule_hit(result.rule_id)
            if result.category in [c.value for c in CATEGORIES_REQUIRING_CONFIRMATION]:
                needs_confirmation = True
            applied += 1
        repo.commit()
        self.refresh()
        message = (
            f"Categorised {applied} transaction(s) from your rules.\n"
            "Lines still highlighted need a manual category."
        )
        if needs_confirmation:
            message += (
                "\n\nSome lines were set to Employment Income. Quarterly "
                "updates never include it, so nothing is held up - just "
                "confirm the gross figure on the Summary & Bridging tab "
                "before the year-end final declaration."
            )
        messagebox.showinfo("Auto-classify", message, parent=self)

    # --- details / refunds ---

    @staticmethod
    def _allowed_categories(amounts: list[float]) -> list[TransactionCategory]:
        """Categories matching the direction of the given amounts."""
        if not amounts:
            return list(TransactionCategory)
        if all(a >= 0 for a in amounts):
            return categories_for_amount(1.0)
        if all(a < 0 for a in amounts):
            return categories_for_amount(-1.0)
        # Mixed money in and out: only the direction-free categories fit
        return [c for c in TransactionCategory if c in NEUTRAL_CATEGORIES]

    def _update_category_choices(self) -> None:
        """Offer only categories matching the selected lines' direction,
        grouped SA103 / SA105 / year-end."""
        ids = set(self._selected_ids())
        amounts = [t.amount for t in self._transactions() if t.id in ids]
        values = grouped_category_labels(self._allowed_categories(amounts))
        self.category_combo.configure(values=values)
        current = self.category_var.get()
        if current not in values or current not in LABEL_TO_CATEGORY:
            self.category_var.set("")

    def _on_category_picked(self, _event=None) -> None:
        """Bounce divider-header rows: they exist for grouping only."""
        if self.category_var.get() not in LABEL_TO_CATEGORY:
            self.category_var.set("")

    # --- context menu ---

    def _show_context_menu(self, event) -> None:
        """Right-click on a transaction: filter, categorise, refund, delete.

        A right-click outside the current selection moves the selection to
        the clicked row; inside it, the menu acts on the whole selection.
        """
        row = self.tree.identify_row(event.y)
        if not row:
            return
        if row not in self.tree.selection():
            self.tree.selection_set(row)
            # The table redraws when Tk is idle, but the popup menu blocks
            # until it closes - draw the new selection now so the user can
            # see which line the menu applies to.
            self.tree.update_idletasks()
        ids = self._selected_ids()
        by_id = {t.id: t for t in self._transactions()}
        clicked = by_id.get(int(row))
        if clicked is None:
            return

        menu = tk.Menu(self, tearoff=0)
        menu.add_command(
            label="Filter transactions like this",
            command=lambda: self.search_var.set(normalize_description(clicked.description)),
        )
        menu.add_separator()

        amounts = [by_id[i].amount for i in ids if i in by_id]
        cat_menu = tk.Menu(menu, tearoff=0)
        allowed = self._allowed_categories(amounts)
        guidance_by_index: dict[int, str] = {}
        entry_index = 0
        for key in CATEGORY_GROUP_ORDER:
            group = [c for c in allowed if category_group(c) == key]
            if not group:
                continue
            if entry_index:
                cat_menu.add_separator()
                entry_index += 1
            cat_menu.add_command(
                label=CATEGORY_GROUP_TITLES[key], state="disabled",
            )
            entry_index += 1
            for cat in group:
                label = CATEGORY_LABELS[cat]
                cat_menu.add_command(
                    label=label, command=lambda l=label: self._apply_category(ids, l),
                )
                guidance_by_index[entry_index] = CATEGORY_GUIDANCE.get(
                    cat, CATEGORY_BOX_MAPPING.get(cat, "")
                )
                entry_index += 1
        # HMRC guidance follows the highlighted entry; keep a reference so
        # the tooltip isn't garbage-collected while the menu is up
        cat_menu._guidance_tooltip = MenuTooltip(cat_menu, guidance_by_index)
        suffix = f" {len(ids)} lines as" if len(ids) > 1 else " as"
        menu.add_cascade(label=f"Categorise{suffix}", menu=cat_menu)
        menu.add_command(
            label="Categorise as one of a refund pair...",
            command=lambda: self._pick_refund_pair(clicked),
            state="normal" if len(ids) == 1 else "disabled",
        )

        # Disallowable toggle - only for categorised expense (money out)
        # lines; wholly-disallowable categories are always disallowable so
        # they get no toggle.
        repo = self.app.repo
        flaggable = []
        for txn_id in ids:
            txn = by_id.get(txn_id)
            cat = repo.get_categorisation(txn_id)
            if txn is None or cat is None or txn.amount >= 0:
                continue
            if (cat.category in [c.value for c in TransactionCategory]
                    and TransactionCategory(cat.category) in ALWAYS_DISALLOWABLE_CATEGORIES):
                continue
            flaggable.append((txn_id, cat))
        if flaggable:
            flag_ids = [i for i, _ in flaggable]
            if any(not getattr(c, "disallowable", False) for _, c in flaggable):
                menu.add_command(
                    label="Mark as disallowable for tax",
                    command=lambda: self._set_disallowable(flag_ids, True),
                )
            else:
                menu.add_command(
                    label="Mark as allowable for tax",
                    command=lambda: self._set_disallowable(flag_ids, False),
                )
        menu.add_separator()
        menu.add_command(
            label="Delete transaction..." if len(ids) == 1
            else f"Delete {len(ids)} transactions...",
            command=lambda: self._delete_transactions(ids),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _set_disallowable(self, ids: list[int], disallowable: bool) -> None:
        """Flag expense lines as (dis)allowable for tax. Disallowable amounts
        stay in the SA103 expense box total with a matching disallowable
        entry (HMRC nets them off); on SA105 they are left out of the box."""
        repo = self.app.repo
        updated = repo.set_disallowable_flag(ids, disallowable)
        repo.commit()
        self.refresh()
        if disallowable:
            self.app.set_status(
                f"Marked {updated} line(s) as disallowable - reported to HMRC "
                "but not deducted from profit"
            )
        else:
            self.app.set_status(f"Marked {updated} line(s) as allowable")

    def _pick_refund_pair(self, txn) -> None:
        """Pair the clicked transaction with an opposite-sign one; the later
        of the two is recorded as a refund inheriting the earlier's category."""
        repo = self.app.repo
        candidates = [
            t for t in self._transactions()
            if t.id != txn.id and t.amount != 0
            and (t.amount < 0) != (txn.amount < 0)
        ]
        if not candidates:
            messagebox.showinfo(
                "No candidates",
                "There are no transactions of the opposite sign to pair with.",
                parent=self,
            )
            return
        other = prompt_refund_pair(self, txn, candidates, repo)
        if other is None:
            return
        original, refund = (txn, other) if txn.date <= other.date else (other, txn)
        if (original.date == refund.date
                and repo.get_categorisation(original.id) is None
                and repo.get_categorisation(refund.id) is not None):
            original, refund = refund, original
        original_cat = repo.get_categorisation(original.id)
        if original_cat is None:
            messagebox.showinfo(
                "Categorise the original first",
                "The earlier transaction of the pair is uncategorised. "
                "Categorise it first - the later one is then recorded as its "
                "refund and inherits that category.",
                parent=self,
            )
            return
        repo.set_categorisation(
            raw_transaction_id=refund.id,
            category=original_cat.category,
            confidence="refund",
            changed_by="user",
        )
        repo.commit()
        self.refresh()
        self.app.set_status(
            "Marked as refund - the pair now nets against the same HMRC box."
        )

    def _delete_transactions(self, ids: list[int]) -> None:
        repo = self.app.repo
        by_id = {t.id: t for t in self._transactions()}
        txns = [by_id[i] for i in ids if i in by_id]
        if not txns:
            return
        if len(txns) == 1:
            t = txns[0]
            prompt = (
                "Delete this transaction?\n\n"
                f"{t.date}   {t.amount:,.2f}   {t.description}"
            )
        else:
            listed = "\n".join(
                f"  {t.date}   {t.amount:,.2f}   {t.description}" for t in txns[:8]
            )
            if len(txns) > 8:
                listed += f"\n  ... and {len(txns) - 8} more"
            prompt = f"Delete these {len(txns)} transactions?\n\n{listed}"
        if repo.has_categorisation_or_attachment([t.id for t in txns]):
            prompt += (
                "\n\nAt least one of them has a category or an attached "
                "receipt/invoice - those are deleted too."
            )
        prompt += (
            "\n\nThis cannot be undone. Re-importing the statement file "
            "they came from can bring them back."
        )
        if not messagebox.askyesno(
            "Delete transaction(s)", prompt, icon="warning", default="no", parent=self,
        ):
            return
        result = repo.delete_raw_transactions([t.id for t in txns])
        for path in result["attachment_paths"]:
            if path.exists():
                path.unlink()
        repo.commit()
        self.refresh()
        self.app.update_quarter_options()
        self.app.set_status(f"Deleted {result['transactions_removed']} transaction(s)")

    def on_select(self, _event=None) -> None:
        self._update_category_choices()
        ids = self._selected_ids()
        self._refund_candidates = []
        self.refund_combo.set("")
        self.refund_combo.configure(state="disabled", values=[])
        self.refund_btn.configure(state="disabled")
        self._attachments = []
        self.attachments_var.set("")
        self.attach_btn.configure(state="disabled")
        self.open_attachment_btn.configure(state="disabled")
        self.remove_attachment_btn.configure(state="disabled")
        if len(ids) != 1:
            self.extract_var.set(
                "Select a single transaction to see its original statement line."
            )
            return
        repo = self.app.repo
        txn = next((t for t in self._transactions() if t.id == ids[0]), None)
        if txn is None:
            return
        src = txn.import_log.filename if txn.import_log else "unknown file"
        self.extract_var.set(f"From {src}:  {txn.raw_data or '(no raw line stored)'}")

        self.attach_btn.configure(state="normal")
        self._attachments = list(repo.get_attachments_for_transaction(txn.id))
        if self._attachments:
            names = ", ".join(a.original_filename for a in self._attachments)
            self.attachments_var.set(names)
            self.open_attachment_btn.configure(state="normal")
            self.remove_attachment_btn.configure(state="normal")
        else:
            self.attachments_var.set("(none)")

        candidates = [
            c for c in repo.find_refund_candidates(txn.amount, txn.date)
            if c.id != txn.id
        ]
        if candidates:
            self._refund_candidates = candidates
            display = []
            for c in candidates:
                cat = repo.get_categorisation(c.id)
                cat_note = ""
                if cat and cat.category in [x.value for x in TransactionCategory]:
                    cat_note = f"  [{CATEGORY_LABELS[TransactionCategory(cat.category)]}]"
                display.append(f"{c.date}  {c.amount:,.2f}  {c.description}{cat_note}")
            self.refund_combo.configure(state="readonly", values=display)
            self.refund_combo.current(0)
            self.refund_btn.configure(state="normal")

    def mark_refund(self) -> None:
        ids = self._selected_ids()
        if len(ids) != 1 or not self._refund_candidates:
            return
        idx = self.refund_combo.current()
        if idx < 0:
            return
        repo = self.app.repo
        cand = self._refund_candidates[idx]
        cand_cat = repo.get_categorisation(cand.id)
        if cand_cat is None:
            messagebox.showinfo(
                "Categorise the original first",
                "The transaction being refunded is uncategorised. Categorise it "
                "first, then mark this one as its refund.",
                parent=self,
            )
            return
        repo.set_categorisation(
            raw_transaction_id=ids[0],
            category=cand_cat.category,
            confidence="refund",
            changed_by="user",
        )
        repo.commit()
        self.refresh()
        self.app.set_status(
            "Marked as refund - it now nets against the same HMRC box as the original."
        )

    # --- attachments (receipts/invoices) ---

    def attach_file(self) -> None:
        ids = self._selected_ids()
        if len(ids) != 1:
            return
        from mtd_classifier.services.attachments import (
            ALLOWED_EXTENSIONS, UnsupportedAttachmentType, attach_file,
        )

        exts = " ".join(f"*{e}" for e in ALLOWED_EXTENSIONS)
        path = filedialog.askopenfilename(
            parent=self, title="Attach a receipt or invoice",
            filetypes=[("Receipts/invoices", exts), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            attach_file(self.app.repo, ids[0], Path(path))
        except UnsupportedAttachmentType as e:
            messagebox.showinfo("Unsupported file type", str(e), parent=self)
            return
        self.app.repo.commit()
        self.on_select()
        self.app.set_status(f"Attached {Path(path).name}")

    def open_attachment(self) -> None:
        if not self._attachments:
            return
        from mtd_classifier.services.attachments import open_attachment

        attachment = self._attachments[0]
        if len(self._attachments) > 1:
            chosen = self._prompt_pick_attachment("Open which file?")
            if chosen is None:
                return
            attachment = chosen
        try:
            open_attachment(attachment)
        except FileNotFoundError:
            messagebox.showinfo(
                "File missing",
                "That attachment's file is no longer on disk.", parent=self,
            )

    def remove_attachment(self) -> None:
        if not self._attachments:
            return
        from mtd_classifier.services.attachments import delete_attachment

        attachment = self._attachments[0]
        if len(self._attachments) > 1:
            chosen = self._prompt_pick_attachment("Remove which file?")
            if chosen is None:
                return
            attachment = chosen
        if not messagebox.askyesno(
            "Remove attachment", f"Remove {attachment.original_filename}?", parent=self,
        ):
            return
        delete_attachment(self.app.repo, attachment)
        self.app.repo.commit()
        self.on_select()
        self.app.set_status(f"Removed {attachment.original_filename}")

    def _prompt_pick_attachment(self, title: str):
        """Simple picker for when a transaction has more than one attachment."""
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.grab_set()
        listbox = tk.Listbox(dialog, height=min(8, len(self._attachments)), width=50)
        theme.style_listbox(listbox)
        for a in self._attachments:
            listbox.insert("end", a.original_filename)
        listbox.pack(padx=10, pady=10, fill="both", expand=True)
        fade_in(dialog)
        result: list = []

        def confirm():
            sel = listbox.curselection()
            if sel:
                result.append(self._attachments[sel[0]])
            dialog.destroy()

        ttk.Button(dialog, text="Select", command=confirm).pack(pady=(0, 10))
        dialog.wait_window()
        return result[0] if result else None


class ImportHistoryDialog(tk.Toplevel):
    """List past imports for one account, newest first, with Undo Import.

    Undo removes exactly the transactions that import created - anything
    skipped as a duplicate at the time (because it already existed from an
    earlier import) stays untouched. Imports must be undone in reverse
    (most recent first) order; the repository enforces this and this
    dialog surfaces a clear message if it's violated.
    """

    def __init__(self, account_tab: AccountTab) -> None:
        super().__init__(account_tab.app)
        self.account_tab = account_tab
        self.app = account_tab.app

        self.title(f"Import History / Undo Imports - {account_tab.label}")
        self.configure(bg=theme.PAPER)
        self.transient(account_tab.app)
        self.grab_set()
        self.geometry("640x360")
        self.minsize(520, 260)

        # Grid (not pack) so the button row has a fixed, non-expanding row
        # and stays visible even when the window is shrunk - the tree area
        # is the only row that grows/shrinks, and scrolls internally.
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        band = gradient_band(self, height=32, left_text=f"Imports - {account_tab.label}")
        band.grid(row=0, column=0, sticky="ew")

        frame = ttk.Frame(self, padding=(14, 10, 14, 0))
        frame.grid(row=1, column=0, sticky="ew")
        ttk.Label(
            frame, text="Undo removes only the transactions that import "
            "added - duplicates already recorded from an earlier import are "
            "never removed. Undo the most recent import first. Select "
            "multiple rows (Shift/Ctrl-click) to undo several at once.",
            wraplength=600, foreground=theme.INK_MUTED,
        ).pack(anchor="w", pady=(2, 0))

        tree_frame = ttk.Frame(self, padding=(14, 10, 14, 0))
        tree_frame.grid(row=2, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        columns = ("filename", "bank", "imported_at", "count")
        self.tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", selectmode="extended", height=10
        )
        self.tree.heading("filename", text="File")
        self.tree.heading("bank", text="Bank")
        self.tree.heading("imported_at", text="Imported")
        self.tree.heading("count", text="Transactions")
        self.tree.column("filename", width=240)
        self.tree.column("bank", width=110)
        self.tree.column("imported_at", width=150)
        self.tree.column("count", width=90, anchor="e")
        self.tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

        # Delete key is the clear, discoverable shortcut for undoing
        # whatever is currently selected.
        self.tree.bind("<Delete>", lambda event: self._undo_selected())

        button_row = ttk.Frame(self, padding=14)
        button_row.grid(row=3, column=0, sticky="ew")
        self.undo_btn = ttk.Button(
            button_row, text="Undo Selected Import(s)  (Del)", command=self._undo_selected,
        )
        self.undo_btn.pack(side="left")
        ttk.Button(
            button_row, text="Close", command=lambda: close_window(self),
        ).pack(side="right")

        self._refresh()
        fade_in(self)

    def _refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        logs = self.app.repo.get_import_logs_for_account(
            bank=self.account_tab.bank, account=self.account_tab.account,
        )
        for log in logs:
            count = self.app.repo.count_current_transactions_for_import(log.id)
            self.tree.insert(
                "", "end", iid=str(log.id),
                values=(log.filename, log.bank, log.imported_at.strftime("%Y-%m-%d %H:%M"), count),
            )
        if logs:
            self.tree.selection_set(str(logs[0].id))

    def _undo_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Nothing selected", "Select an import first.", parent=self)
            return
        # The tree lists imports newest-first; undo must happen in that same
        # order regardless of click order, since undoing an older one first
        # would trip the repository's out-of-order check.
        ordered = [iid for iid in self.tree.get_children("") if iid in selection]
        filenames = [self.tree.set(iid, "filename") for iid in ordered]

        if len(ordered) == 1:
            prompt = (
                f"Undo the import of '{filenames[0]}'?\n\n"
                "This removes the transactions it added (duplicates already "
                "recorded from an earlier import are kept). This cannot be undone."
            )
        else:
            prompt = (
                f"Undo these {len(ordered)} imports?\n\n"
                + "\n".join(f"  - {name}" for name in filenames)
                + "\n\nThis removes the transactions each one added (duplicates "
                "already recorded from an earlier import are kept). "
                "This cannot be undone."
            )
        if not messagebox.askyesno("Undo import", prompt, parent=self):
            return

        repo = self.app.repo
        undone = 0
        removed = 0
        for iid in ordered:
            filename = self.tree.set(iid, "filename")
            try:
                result = repo.undo_import(int(iid))
            except ImportOutOfOrderError:
                repo.rollback()
                if undone:
                    self._refresh()
                    self.account_tab.refresh()
                messagebox.showerror(
                    "Undo out of order",
                    (f"Undid {undone} import(s). " if undone else "")
                    + f"Can't undo '{filename}' while a more recent import for "
                    "this account still stands - undo that one first.",
                    parent=self,
                )
                return

            for path in result["attachment_paths"]:
                if path.exists():
                    path.unlink()
            repo.commit()
            undone += 1
            removed += result["transactions_removed"]

        self._refresh()
        self.account_tab.refresh()
        self.app.update_quarter_options()
        if undone == 1:
            self.app.set_status(f"Undid import of '{filenames[0]}' ({removed} transaction(s) removed)")
        else:
            self.app.set_status(f"Undid {undone} imports ({removed} transaction(s) removed)")


UNCONFIRMED = "UNCONFIRMED"  # sentinel used throughout the quarter matrix


def fmt_money(value: float) -> str:
    return f"{value:,.2f}"


def compute_fy_estimate(repo: Repository, tax_year: int, tq: TaxQuarter):
    """Full-year tax estimate from cumulative data up to ``tq``'s end.

    THE single engine behind every estimated-tax figure in the GUI: the
    Tax Calculation tab and the Summary tab's estimated rows both call
    this, so their numbers always agree. Category totals are scaled
    x4/quarters; a confirmed annual employment figure is not scaled, while
    a to-date deposit sum is (see EmploymentIncomeRecord.is_year_to_date).

    Returns None when no tax rates exist for the year, else a namespace
    with .fy (TaxComputationResult), .rates, .scale, .employment_record.
    """
    from mtd_classifier.tax_year_rates import get_rates
    from mtd_classifier.services.tax_computation import compute_tax_from_category_totals

    rates = get_rates(tax_year)
    if rates is None:
        return None
    year_start = date(tax_year, 4, 6)
    cat_txns = repo.get_categorised_transactions_in_period(year_start, tq.end_date)
    all_txns = repo.get_transactions_in_period(year_start, tq.end_date)
    employment_record = repo.get_employment_income(tax_year)
    summary = summarise_quarter(
        tq, cat_txns, total_in_period=len(all_txns),
        employment_income_record=employment_record,
    )
    scale = 4.0 / tq.quarter
    employment_ytd = employment_fy = (
        employment_record.gross_amount if employment_record else 0.0
    )
    if employment_record is not None and employment_record.is_year_to_date:
        emp_ids = [
            raw.id for raw, cat in cat_txns
            if cat.category == TransactionCategory.EMPLOYMENT_INCOME.value
        ]
        entries = repo.get_employment_gross_entries(emp_ids)
        if entries:
            employment_ytd = round(
                sum(e.gross_amount for e in entries.values()), 2
            )
        employment_fy = round(employment_ytd * scale, 2)
    scaled_totals = {
        key: SimpleNamespace(
            total_income=ct.total_income * scale,
            total_expense=ct.total_expense * scale,
            total_disallowable=ct.total_disallowable * scale,
        )
        for key, ct in summary.category_totals.items()
    }
    fy = compute_tax_from_category_totals(
        scaled_totals, rates, employment_income=employment_fy,
    )
    return SimpleNamespace(
        fy=fy, rates=rates, scale=scale, employment_record=employment_record,
    )


class SourceRowsDialog(tk.Toplevel):
    """Read-only list of the transactions behind one summary total."""

    def __init__(self, app: "MtdApp", title: str,
                 pairs: list, start: date, end: date) -> None:
        super().__init__(app)
        self.title(f"Source transactions - {title}")
        self.configure(bg=theme.PAPER)
        self.transient(app)
        self.grab_set()
        self.geometry("820x440")
        self.minsize(620, 300)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(14, 12, 14, 0))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=title, font=theme.fnt_semibold(11)).pack(anchor="w")
        ttk.Label(
            header, text=f"{start} to {end}  -  {len(pairs)} transaction(s)",
            foreground=theme.INK_MUTED,
        ).pack(anchor="w")

        tree_frame = ttk.Frame(self, padding=(14, 8, 14, 0))
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        columns = ("date", "account", "description", "amount", "category", "how")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        # No stretch columns: with a single stretchy column, dragging it
        # narrower is instantly undone (the freed space flows back into the
        # same column), which reads as the width "jumping back".
        for col, text, width, anchor in [
            ("date", "Date", 90, "w"),
            ("account", "Account", 130, "w"),
            ("description", "Description", 300, "w"),
            ("amount", "Amount", 90, "e"),
            ("category", "Category", 150, "w"),
            ("how", "How", 70, "w"),
        ]:
            tree.heading(col, text=text)
            tree.column(col, width=width, anchor=anchor, stretch=False)
        tree.tag_configure("refund", background=theme.MINT)
        total = 0.0
        for raw, cat in sorted(pairs, key=lambda p: (p[0].date, p[0].id)):
            total += raw.amount
            cat_label = (
                CATEGORY_LABELS[TransactionCategory(cat.category)]
                if cat.category in [c.value for c in TransactionCategory]
                else cat.category
            )
            tree.insert(
                "", "end",
                values=(raw.date.isoformat(), raw.account or raw.bank,
                        raw.description, f"{raw.amount:,.2f}", cat_label,
                        cat.confidence),
                tags=("refund",) if cat.confidence == "refund" else (),
            )
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

        footer = ttk.Frame(self, padding=14)
        footer.grid(row=2, column=0, sticky="ew")
        ttk.Label(
            footer, text=f"Sum of these transactions: {fmt_money(round(total, 2))}",
            font=theme.fnt_semibold(10),
        ).pack(side="left")
        ttk.Button(footer, text="Close", command=lambda: close_window(self)).pack(side="right")
        fade_in(self)


class SummaryTab(ttk.Frame):
    """Per-quarter income/expenses/net matrix across all accounts, plus export.

    Each quarter is shown in its OWN column (not cumulative) so quarter-to-
    quarter comparison is direct. Cumulative figures - what actually gets
    exported/bridged - are governed solely by the "Export up to quarter"
    selector; the per-quarter checkboxes only affect the on-screen "Selected"
    column, never what gets exported.
    """

    def __init__(self, notebook: ttk.Notebook, app: "MtdApp") -> None:
        super().__init__(notebook)
        self.app = app
        self._quarterlies: dict[int, object] = {}
        self._row_quarter_values: dict[str, tuple[dict, bool]] = {}
        self._row_meta: dict[str, str] = {}

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Label(
            toolbar,
            text="Double-click a row to see its source transactions.",
            foreground=theme.INK_MUTED,
        ).pack(side="left")
        self.bridging_btn = ttk.Button(
            toolbar, text="Generate Bridging Files", command=self.generate_bridging,
            style="Accent.TButton",
        )
        self.bridging_btn.pack(side="right")
        self.export_btn = ttk.Button(toolbar, text="Export CSV", command=self.export_csv,
                                     style="Sky.TButton")
        self.export_btn.pack(side="right", padx=6)
        self.employment_btn = ttk.Button(
            toolbar, text="Confirm Employment Income...", command=self.open_employment_dialog,
            style="Peach.TButton",
        )
        self.employment_btn.pack(side="right", padx=6)
        self.submission_guide_btn = ttk.Button(
            toolbar, text="How to Submit...", command=self.show_submission_guide,
            style="Lilac.TButton",
        )
        self.submission_guide_btn.pack(side="right", padx=(6, 0))

        columns = ("q1", "q2", "q3", "q4", "total", "fy_est")
        self.tree = ttk.Treeview(self, columns=columns, show="tree headings",
                                 style="Summary.Treeview")
        self.tree.tag_configure("unconfirmed", foreground=theme.BRICK)
        self.tree.tag_configure("parent", font=theme.fnt_semibold(10))
        self.tree.tag_configure("flat_total", font=theme.fnt_semibold(10))
        self.tree.tag_configure("income_hue", foreground=theme.SAGE_INK)
        self.tree.tag_configure("expense_hue", foreground=theme.CORAL_DEEP)
        self.tree.tag_configure("net_band", background=theme.CORAL_MIST)
        self.tree.heading("#0", text="Category")
        self.tree.column("#0", width=230, anchor="w", stretch=True)
        for col, text in [
            ("q1", "Q1"), ("q2", "Q2"), ("q3", "Q3"), ("q4", "Q4"),
            ("total", "Total"), ("fy_est", "FY Est."),
        ]:
            self.tree.heading(col, text=text)
            self.tree.column(col, width=105, anchor="e", stretch=False)
        self.tree.pack(fill="both", expand=True, padx=6)
        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)
        self.tree.bind("<Double-Button-1>", self._on_double_click)

        self.detail_var = tk.StringVar(
            value="Select a row to see its HMRC box mapping and any notes."
        )
        ttk.Label(self, textvariable=self.detail_var, wraplength=900,
                  foreground=theme.INK_MUTED).pack(anchor="w", padx=8, pady=(4, 0))

        self.scope_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.scope_var, wraplength=900,
                  foreground=theme.INK_MUTED).pack(anchor="w", padx=8, pady=(2, 0))

        self.fy_est_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.fy_est_var, wraplength=900,
                  foreground=theme.INK_MUTED).pack(anchor="w", padx=8, pady=(2, 0))

        ttk.Separator(self).pack(fill="x", padx=6, pady=4)
        ttk.Label(self, text=DISCLAIMER, wraplength=900,
                  foreground=theme.INK_MUTED, font=theme.fnt(8)).pack(
            anchor="w", padx=8, pady=(0, 6)
        )

        self.update_periods()

    def update_periods(self) -> None:
        """Re-sync when the app's working tax year or data changes."""
        self.refresh()

    def _current_quarter(self) -> TaxQuarter | None:
        """The globally selected reporting quarter (None when the toolbar
        selector is greyed out - All transactions mode or no complete data)."""
        return self.app.export_quarter

    def _update_scope_label(self) -> None:
        tq = self._current_quarter()
        if tq is None:
            self.scope_var.set(
                "No quarter has complete cumulative data for this tax year "
                "yet (the 'Up to quarter' selector in the toolbar is greyed "
                "out), so export and bridging are unavailable. Import "
                "statements covering from 6 April onwards to enable them."
            )
            self.fy_est_var.set("")
            return
        year_start = date(tq.tax_year, 4, 6)
        self.scope_var.set(
            f"Export & bridging use ALL categorised transactions for "
            f"{self.app._tax_year_label(tq.tax_year)} from {year_start} to "
            f"{tq.end_date} (end of {tq.label}) - the quarter columns above "
            "are shown for comparison only and don't affect what's exported."
        )
        n = getattr(self, "_data_quarters", 0)
        if n:
            self.fy_est_var.set(
                f"FY Est. linearly scales up your first {n} fully-completed "
                f"quarter(s) of data to a full year (x{4.0 / n:.2f}) - a rough "
                "projection only, not an actual figure. The current in-progress "
                "quarter is excluded so its partial total doesn't skew the estimate."
            )
        else:
            self.fy_est_var.set("")

    def refresh(self) -> None:
        """Recompute all 4 quarters of the app's working tax year (each in
        isolation, not cumulative) and rebuild the matrix."""
        repo = self.app.repo
        ty = self.app.concrete_tax_year
        employment_record = repo.get_employment_income(ty)
        self._employment_record = employment_record
        quarterlies = {}
        for q in range(1, 5):
            tq = TaxQuarter(ty, q)
            cat_txns = repo.get_categorised_transactions_in_period(tq.start_date, tq.end_date)
            all_txns = repo.get_transactions_in_period(tq.start_date, tq.end_date)
            quarterlies[q] = summarise_quarter(
                tq, cat_txns, total_in_period=len(all_txns),
                employment_income_record=employment_record,
            )
        self._quarterlies = quarterlies
        # Largest contiguous run of quarters (from Q1) that have both FULLY
        # ELAPSED and have actual data - the FY Est. column scales that run's
        # total up to 4 quarters. A quarter still in progress is excluded even
        # if it already has some transactions, since its total is necessarily
        # incomplete and would distort the projection.
        today = date.today()
        self._data_quarters = 0
        for q in range(1, 5):
            tq = TaxQuarter(ty, q)
            if tq.end_date > today:
                break
            if quarterlies[q].categorised_count > 0:
                self._data_quarters = q
            else:
                break
        self._rebuild_tree()
        self._update_scope_label()

    # --- matrix construction ---

    @staticmethod
    def _fmt_cell(value) -> str:
        if value is None:
            return ""
        if value == UNCONFIRMED:
            return UNCONFIRMED
        return fmt_money(value)

    @staticmethod
    def _combine(values: list, is_annual: bool) -> str:
        if UNCONFIRMED in values:
            return UNCONFIRMED
        numbers = [v for v in values if isinstance(v, (int, float))]
        if not numbers:
            return fmt_money(0.0)
        return fmt_money(numbers[0] if is_annual else sum(numbers))

    def _fy_estimate(self, values_by_quarter: dict, is_annual: bool) -> str:
        """Scale the quarters with actual data up to a full year.

        E.g. only Q1 has data -> multiply by 4; cumulative data through Q3 ->
        divide by 3 and multiply by 4. An annual-locked row (confirmed
        Employment Income) is only scaled when the confirmed figure is itself
        a to-date deposit sum (paid gross / statement breakdown) - a P60 or
        typed annual figure is already a full-year amount.
        """
        if is_annual:
            combined = self._combine(list(values_by_quarter.values()), is_annual)
            record = getattr(self, "_employment_record", None)
            if (
                record is not None
                and record.is_year_to_date
                and combined != UNCONFIRMED
            ):
                n = self._data_quarters
                if n == 0:
                    return "N/A"
                return fmt_money(record.gross_amount * 4.0 / n)
            return combined
        n = self._data_quarters
        if n == 0:
            return "N/A"
        relevant = [values_by_quarter.get(q) for q in range(1, n + 1)]
        if UNCONFIRMED in relevant:
            return UNCONFIRMED
        total = sum(v for v in relevant if isinstance(v, (int, float)))
        return fmt_money(total * (4.0 / n))

    def _insert_row(self, item_id: str, parent: str, label: str,
                    values_by_quarter: dict, is_annual: bool = False,
                    tags: tuple = (), detail: str = "") -> None:
        self._row_quarter_values[item_id] = (values_by_quarter, is_annual)
        self._row_meta[item_id] = detail
        q_cells = [self._fmt_cell(values_by_quarter.get(q)) for q in range(1, 5)]
        total_cell = self._combine(list(values_by_quarter.values()), is_annual)
        fy_cell = self._fy_estimate(values_by_quarter, is_annual)
        row_tags = tuple(tags)
        if UNCONFIRMED in values_by_quarter.values():
            row_tags = row_tags + ("unconfirmed",)
        self.tree.insert(
            parent, "end", iid=item_id, text=label,
            values=(*q_cells, total_cell, fy_cell), tags=row_tags,
        )

    def _category_values(self, categories, is_income: bool) -> list[tuple]:
        """(category, {quarter: value}) for every category with any activity."""
        rows = []
        for cat in categories:
            values: dict[int, object] = {}
            has_any = False
            for q in range(1, 5):
                ct = self._quarterlies[q].category_totals[cat.value]
                if ct.transaction_count == 0:
                    values[q] = None
                    continue
                has_any = True
                if ct.unconfirmed:
                    values[q] = UNCONFIRMED
                else:
                    values[q] = ct.total_income if is_income else ct.total_expense
            if has_any:
                rows.append((cat, values))
        return rows

    def _rebuild_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._row_quarter_values = {}
        self._row_meta = {}

        income_order = [c for c in TransactionCategory if c in INCOME_CATEGORIES]
        expense_order = [c for c in TransactionCategory if c in EXPENSE_CATEGORIES]

        income_values = {q: self._quarterlies[q].total_income for q in range(1, 5)}
        self._insert_row("income", "", "Income", income_values,
                         tags=("parent", "income_hue"))
        for cat, values in self._category_values(income_order, is_income=True):
            is_annual = cat in CATEGORIES_REQUIRING_CONFIRMATION
            detail = self._category_detail(cat)
            self._insert_row(
                f"income:{cat.value}", "income", CATEGORY_LABELS[cat],
                values, is_annual=is_annual, detail=detail,
            )
        self.tree.item("income", open=True)

        expense_values = {q: self._quarterlies[q].total_expenses for q in range(1, 5)}
        self._insert_row("expenses", "", "Expenses", expense_values,
                         tags=("parent", "expense_hue"))
        for cat, values in self._category_values(expense_order, is_income=False):
            detail = self._category_detail(cat)
            self._insert_row(
                f"expenses:{cat.value}", "expenses", CATEGORY_LABELS[cat],
                values, detail=detail,
            )
        self.tree.item("expenses", open=True)

        net_values = {q: self._quarterlies[q].net_profit for q in range(1, 5)}
        self._insert_row("net_profit", "", "Net Profit from Property & SE", net_values,
                         tags=("flat_total", "net_band"))

        if any(self._quarterlies[q].finance_costs > 0 for q in range(1, 5)):
            fc_values = {q: self._quarterlies[q].finance_costs for q in range(1, 5)}
            self._insert_row(
                "finance_costs", "", "Mortgage int. / finance costs (not deducted)",
                fc_values,
                detail="Since 2020-21, residential mortgage/finance costs are "
                "no longer deducted from property profit - HMRC gives a 20% "
                "tax reduction instead. See the Tax Calculation tab for the "
                "relief and the full tax estimate.",
            )

        if any(self._quarterlies[q].rent_a_room_relief > 0 for q in range(1, 5)):
            rar_values = {
                q: (self._quarterlies[q].rent_a_room_relief or None)
                for q in range(1, 5)
            }
            self._insert_row(
                "rent_a_room_relief", "", "Rent-a-Room exemption (not taxed)",
                rar_values,
                detail="SA105 Box 37 - min(Rent-a-Room receipts, annual limit) "
                "is deducted from property profit and auto-filled on the "
                "bridging template from cumulative receipts. If someone else "
                "shares the letting income the limit halves to £3,750 - adjust "
                "the figure in My Tax Digital before submitting.",
            )

        # No tax-estimate rows here: the Summary tab reports the business
        # figures that feed HMRC boxes, and tax estimates live solely on
        # the Tax Calculation tab - one engine, one answer.

    def _category_detail(self, cat: TransactionCategory) -> str:
        box = CATEGORY_BOX_MAPPING.get(cat, "")
        for q in range(1, 5):
            ct = self._quarterlies[q].category_totals[cat.value]
            if ct.confirmation_note:
                return f"{box} ({ct.confirmation_note})"
            if ct.excluded_note:
                return f"{box} - {ct.excluded_note}"
        return box

    def _on_row_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        detail = self._row_meta.get(selection[0], "")
        self.detail_var.set(
            detail or "No HMRC box mapping for this row."
        )

    # --- drill-down to source transactions ---

    def _on_double_click(self, event) -> None:
        """Double-clicking a total reveals the transactions it sums up,
        scoped to the cell that was clicked: a quarter column drills into
        that quarter, the name/Total/FY Est. columns cover the whole tax
        year. The Employment Income row opens the confirmation dialog
        instead, which lists the salary deposits behind the figure.
        """
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        if iid == f"income:{TransactionCategory.EMPLOYMENT_INCOME.value}":
            self.open_employment_dialog()
            return
        quarter = {"#1": 1, "#2": 2, "#3": 3, "#4": 4}.get(
            self.tree.identify_column(event.x)
        )
        self._show_source_rows(iid, quarter)

    def _source_categories(self, iid: str) -> tuple[list[TransactionCategory], str] | None:
        """Which categories feed the total on a given summary row."""
        reportable_income = [
            c for c in TransactionCategory
            if c in INCOME_CATEGORIES and c not in NON_REPORTABLE_CATEGORIES
        ]
        reportable_expense = [
            c for c in TransactionCategory
            if c in EXPENSE_CATEGORIES
            and c not in NON_REPORTABLE_CATEGORIES
            and c not in FINANCE_COST_CATEGORIES
            and c not in TAX_CREDIT_CATEGORIES
        ]
        if iid.startswith("income:") or iid.startswith("expenses:"):
            cat = TransactionCategory(iid.split(":", 1)[1])
            return [cat], CATEGORY_LABELS[cat]
        if iid == "income":
            return reportable_income, "Income"
        if iid == "expenses":
            return reportable_expense, "Expenses"
        if iid == "net_profit":
            return reportable_income + reportable_expense, "Net Profit from Property & SE"
        if iid == "finance_costs":
            return list(FINANCE_COST_CATEGORIES), "Mortgage/finance costs"
        if iid == "rent_a_room_relief":
            return [TransactionCategory.RENT_A_ROOM_INCOME], "Rent-a-Room Income"
        return None  # derived rows (estimated tax) have no direct source rows

    def _drill_down_period(self, quarter: int | None) -> tuple[date, date, str]:
        """(start, end, label) for a drill-down: the clicked quarter's own
        range, or the whole tax year for the name/Total/FY Est. columns."""
        ty = self.app.concrete_tax_year
        if quarter is not None:
            tq = TaxQuarter(ty, quarter)
            return tq.start_date, tq.end_date, tq.label
        return (
            date(ty, 4, 6), date(ty + 1, 4, 5),
            f"tax year {self.app._tax_year_label(ty)}",
        )

    def _show_source_rows(self, iid: str, quarter: int | None) -> "SourceRowsDialog | None":
        mapping = self._source_categories(iid)
        if mapping is None:
            self.app.set_status(
                "That row is calculated from Net Profit and finance costs - "
                "double-click those rows to see their source transactions."
            )
            return None
        categories, label = mapping
        start, end, period_label = self._drill_down_period(quarter)
        wanted = {c.value for c in categories}
        pairs = [
            (raw, cat)
            for raw, cat in self.app.repo.get_categorised_transactions_in_period(start, end)
            if cat.category in wanted
        ]
        return SourceRowsDialog(self.app, f"{label} - {period_label}", pairs, start, end)

    # --- export / bridging ---

    def _confirm_cross_format_verification(self, year_start: date, end_date) -> bool:
        """HMRC output should only be generated once PDF statements and
        CSV/XLS transaction lists both cover the period and agree on its
        total. Returns True to proceed - either verification passed, or the
        user explicitly chose to continue despite the mismatch."""
        results = verify_cross_format(self.app.repo, year_start, end_date)
        report = verification_report(results)
        if not report:
            return True
        return messagebox.askyesno(
            "Statement verification failed",
            "The transactions for "
            f"{year_start} to {end_date} couldn't be verified as complete:\n\n{report}\n\n"
            "Either re-export the transaction list (CSV/XLS) covering at "
            "least one day beyond each end of the period - an unbroken "
            "balance chain is proof on its own - or import PDF statements "
            "so the two sources can be cross-checked. Or proceed anyway at "
            "your own risk.\n\n"
            "Generate HMRC output anyway?",
            icon="warning", default="no", parent=self,
        )

    def _no_quarter_message(self) -> None:
        messagebox.showinfo(
            "No complete quarter",
            "No quarter has complete cumulative data for this tax year, so "
            "there is nothing safe to export yet. Import statements covering "
            "from 6 April onwards - the 'Up to quarter' selector in the "
            "toolbar enables as soon as a quarter's year-to-date data is "
            "complete.",
            parent=self,
        )

    def export_csv(self) -> None:
        tq = self._current_quarter()
        if tq is None:
            self._no_quarter_message()
            return
        if not self._confirm_cross_format_verification(
            date(tq.tax_year, 4, 6), tq.end_date
        ):
            return
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".csv",
            initialfile=f"mtd_export_{tq.label}.csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        repo = self.app.repo
        year_start = date(tq.tax_year, 4, 6)
        cat_txns = repo.get_categorised_transactions_in_period(year_start, tq.end_date)
        all_txns = repo.get_transactions_in_period(year_start, tq.end_date)
        employment_record = repo.get_employment_income(tq.tax_year)
        summary = summarise_quarter(
            tq, cat_txns, total_in_period=len(all_txns),
            employment_income_record=employment_record,
        )
        export_to_csv(summary, cat_txns, Path(path))
        if any(ct.unconfirmed for ct in summary.reportable_totals()):
            messagebox.showinfo(
                "Exported - one reminder",
                f"Exported {path}\n\n"
                "Reminder: the Employment Income row is marked UNCONFIRMED - "
                "bank deposits are net pay, not the P60/P45 gross figure. "
                "Quarterly figures don't need it, but confirm it (Summary "
                "tab) before the year-end final declaration.",
                parent=self,
            )
        else:
            self.app.set_status(f"Exported {path}")

    def generate_bridging(self) -> None:
        tq = self._current_quarter()
        if tq is None:
            self._no_quarter_message()
            return
        if not self._confirm_cross_format_verification(
            date(tq.tax_year, 4, 6), tq.end_date
        ):
            return
        out_dir = filedialog.askdirectory(
            parent=self, title="Folder for the bridging files"
        )
        if not out_dir:
            return
        repo = self.app.repo
        year_start = date(tq.tax_year, 4, 6)
        cat_txns = repo.get_categorised_transactions_in_period(year_start, tq.end_date)
        all_txns = repo.get_transactions_in_period(year_start, tq.end_date)
        uncategorised = len(all_txns) - len(cat_txns)
        if uncategorised > 0 and not messagebox.askyesno(
            "Uncategorised transactions",
            f"{uncategorised} transactions between {year_start} and {tq.end_date} "
            "are uncategorised and will NOT be included.\n\nContinue anyway?",
            parent=self,
        ):
            return
        from mtd_classifier.export.template_fetch import TemplateError

        try:
            result = export_bridging_templates(
                cat_txns, Path(out_dir), tq.label, tax_year=tq.tax_year,
            )
        except TemplateError as e:
            # First use needs one download of My Tax Digital's own template
            # files (they are not bundled with this app)
            messagebox.showerror("Bridging template unavailable", str(e), parent=self)
            return
        if not result.files_written:
            messagebox.showinfo(
                "Nothing to export",
                "No reportable figures found for this period.", parent=self,
            )
            return
        # Unconfirmed employment income never blocks bridging - it isn't part
        # of quarterly updates (HMRC gets it via PAYE) - but a reminder here
        # keeps the year-end final declaration on the radar.
        employment_reminder = ""
        if repo.get_employment_income(tq.tax_year) is None and any(
            cat.category == TransactionCategory.EMPLOYMENT_INCOME.value
            for _, cat in cat_txns
        ):
            employment_reminder = (
                "\n\nReminder: Employment Income for this year is not yet "
                "confirmed. It is never part of quarterly updates, so these "
                "files are complete and fine to upload - but confirm the "
                "gross figure (double-click the Employment Income row on the "
                "Summary tab) before the year-end final declaration.\n"
            )
        rent_a_room_note = ""
        rar = result.box_totals.get("SA105:I33")
        if rar is not None:
            rent_a_room_note = (
                f"\nRent-a-Room exempt amount (box 37) auto-filled: "
                f"{rar.amount:,.2f} = min(receipts to date, annual limit). "
                "If someone else shares that income the limit halves to "
                "3,750.00 - adjust the figure in My Tax Digital before "
                "submitting.\n"
            )
        files = "\n".join(p.name for p in result.files_written)
        messagebox.showinfo(
            "Bridging files ready",
            f"Written to {out_dir}:\n{files}\n\n"
            "These contain CUMULATIVE figures from 6 April to "
            f"{tq.end_date}.\n"
            f"{rent_a_room_note}\nNext steps:\n"
            "1. Log in at mytaxdigital.co.uk\n"
            "2. Open the business (self-employment or property)\n"
            "3. Ensure Bridging mode, choose the open obligation\n"
            f"4. Click 'Import figures', upload, and set the 'to' date to "
            f"{tq.end_date} - it DEFAULTS TO TODAY, which is wrong\n"
            "5. Cross-check every figure against the Summary tab, then "
            "Submit - a submitted obligation CANNOT be amended\n\n"
            "Full step-by-step guide (with links): the 'How to Submit...' "
            "button on this tab."
            f"{employment_reminder}\n"
            f"{DISCLAIMER}",
            parent=self,
        )
        # Walk straight into the submission guide (skippable; the guide
        # itself offers a "don't open automatically" opt-out)
        if self.app.submission_guide_auto:
            SubmissionGuideDialog(self.app, auto_opened=True)

    def show_submission_guide(self) -> None:
        SubmissionGuideDialog(self.app)

    def open_employment_dialog(self) -> None:
        dialog = EmploymentIncomeDialog(self.app, self.app.concrete_tax_year)
        self.wait_window(dialog)
        self.refresh()


class TaxCalculationTab(ttk.Frame):
    """A Personal Tax Computation for one tax year, mirroring the layout of
    a professional accountant's tax computation printout: income by source,
    allowances, tax borne by band (non-savings, savings, dividends), the
    mortgage-interest rate-restricted relief, Class 2/4 NIC, and the total
    tax and NIC due. Always follows the app's concrete_tax_year - use the
    "Tax year" selector in the main toolbar, not a selector on this tab.
    """

    def __init__(self, notebook: ttk.Notebook, app: "MtdApp") -> None:
        super().__init__(notebook)
        self.app = app

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=6, pady=6)
        self.status_var = tk.StringVar(value="")
        ttk.Label(toolbar, textvariable=self.status_var, foreground=theme.BRICK).pack(
            side="left"
        )

        columns = ("fy_est", "ytd")
        self.tree = ttk.Treeview(self, columns=columns, show="tree headings",
                                 style="Summary.Treeview")
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=400, anchor="w", stretch=True)
        self.tree.heading("fy_est", text="FY Estimate (£)")
        self.tree.column("fy_est", width=140, anchor="e", stretch=False)
        self.tree.heading("ytd", text="Year to date (£)")
        self.tree.column("ytd", width=160, anchor="e", stretch=False)
        self.tree.tag_configure("section", font=theme.fnt_semibold(10))
        self.tree.tag_configure("total", font=theme.fnt_semibold(10))
        self.tree.pack(fill="both", expand=True, padx=6)

        self.basis_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.basis_var, wraplength=900,
                  foreground=theme.INK_MUTED).pack(anchor="w", padx=8, pady=(4, 0))

        ttk.Separator(self).pack(fill="x", padx=6, pady=4)
        ttk.Label(self, text=DISCLAIMER, wraplength=900,
                  foreground=theme.INK_MUTED, font=theme.fnt(8)).pack(
            anchor="w", padx=8, pady=(0, 6)
        )

    def refresh(self) -> None:
        from mtd_classifier.tax_year_rates import get_rates

        ty = self.app.concrete_tax_year
        self.tree.delete(*self.tree.get_children())
        self.basis_var.set("")
        rates = get_rates(ty)
        if rates is None:
            self.status_var.set(
                f"No tax rates available yet for {self.app._tax_year_label(ty)}."
            )
            return
        tq = self.app.export_quarter
        if tq is None or tq.tax_year != ty:
            self.tree.heading("ytd", text="Year to date (£)")
            self.status_var.set(
                "No quarter has complete cumulative data for "
                f"{self.app._tax_year_label(ty)} - the computation needs the "
                "'Up to quarter' selector in the toolbar."
            )
            return
        self.status_var.set(
            "" if "UNVERIFIED" not in rates.source
            else f"Rates for {self.app._tax_year_label(ty)} are unverified placeholders - see source note below."
        )

        year_start = date(ty, 4, 6)
        ytd_end = tq.end_date
        est = compute_fy_estimate(self.app.repo, ty, tq)  # rates checked above
        fy = est.fy
        employment_record = est.employment_record
        scale = est.scale
        # The YTD column is PRO-FORMA: every full-year estimate figure -
        # income, allowances, tax bands, NIC - multiplied by the elapsed
        # share of the year (q/4), so the quarter's tax is the FY estimate
        # divided evenly across the quarters rather than a standalone
        # computation that would front-load the whole personal allowance.
        factor = tq.quarter / 4.0

        self.tree.heading("ytd", text=f"YTD to {tq.label} (£)")
        self.basis_var.set(
            f"FY Estimate scales the transactions from {year_start} to "
            f"{ytd_end} up to a full year (x{scale:.2f}); a confirmed annual "
            "Employment Income figure is not scaled. The YTD column is "
            f"pro-forma: the FY Estimate x {tq.quarter}/4, so tax spreads "
            "evenly across the quarters. Rough projections, not actual figures."
        )

        def add(parent, label, fy_amount=None, tags=()):
            values = (
                fmt_money(fy_amount) if fy_amount is not None else "",
                fmt_money(round(fy_amount * factor, 2)) if fy_amount is not None else "",
            )
            return self.tree.insert(parent, "end", text=label, values=values, tags=tags)

        def add_bands(parent, label, bands):
            node = add(parent, label)
            for b in bands:
                add(node, f"  {b.label} @ {b.rate*100:.2f}%", b.tax)
            self.tree.item(node, open=True)

        add("", f"Personal Tax Computation - {self.app._tax_year_label(ty)}", tags=("section",))

        income = add("", "Income", tags=("section",))
        if fy.self_employment_profit:
            add(income, "Self-employment profit", fy.self_employment_profit)
        if fy.property_profit:
            add(income, "UK property profit", fy.property_profit)
        if employment_record is None:
            add(income, "Employment income - UNCONFIRMED (see Summary tab)", 0.0)
        elif fy.employment_income:
            add(income, "Employment income (confirmed annual figure)",
                fy.employment_income)
        if fy.interest_income:
            add(income, "UK interest", fy.interest_income)
        if fy.dividend_income:
            add(income, "UK dividends", fy.dividend_income)
        add("", "Total income", fy.total_income, tags=("total",))

        allowances = add("", "Allowances", tags=("section",))
        taper_note = ""
        if fy.personal_allowance < fy.personal_allowance_before_taper:
            taper_note = " (tapered)"
        add(allowances, f"Personal allowance{taper_note}", -fy.personal_allowance)
        add("", "Taxable income", fy.taxable_income, tags=("total",))

        tax_borne = add("", "Tax borne", tags=("section",))
        if fy.non_savings_bands:
            add_bands(tax_borne, "Non-savings income", fy.non_savings_bands)
        if fy.savings_bands:
            add_bands(tax_borne, "Savings income", fy.savings_bands)
        if fy.dividend_bands:
            add_bands(tax_borne, "Dividend income", fy.dividend_bands)
        add("", "Gross tax", fy.gross_tax, tags=("total",))

        if fy.finance_costs > 0:
            relief = add("", "Rate restricted relief", tags=("section",))
            add(relief, f"Relief for finance costs @ {rates.basic_rate*100:.0f}%",
                -fy.rate_restricted_relief)
        add("", "Income Tax liability", fy.income_tax_liability, tags=("total",))

        if fy.class2_nic or fy.class4_nic:
            nic = add("", "National Insurance", tags=("section",))
            if fy.class2_nic:
                add(nic, "Class 2 NIC", fy.class2_nic)
            if fy.class4_nic:
                add(nic, "Class 4 NIC", fy.class4_nic)
            self.tree.item(nic, open=True)

        add("", "Total Tax and NIC due", fy.total_tax_and_nic, tags=("total",))

        if fy.gift_aid_grossed_up > 0:
            add("", "Basic/additional rate bands widened for Gift Aid",
                fy.gift_aid_grossed_up, tags=("section",))

        for item in (income, allowances, tax_borne):
            self.tree.item(item, open=True)


class EmploymentIncomeDialog(tk.Toplevel):
    """Confirm the gross employment income figure for a tax year.

    Lists the salary deposits found on the statements (net pay) so each one
    can be amended with its pre-tax gross amount - typed manually or pulled
    from a payslip PDF - or all deposits can be accepted as-is when the pay
    is received gross (no tax taken off at source). Alternatively one annual
    figure (e.g. from a P60) can be confirmed directly. Nothing is saved
    until a Confirm button is clicked.
    """

    def __init__(self, app: "MtdApp", tax_year: int) -> None:
        super().__init__(app)
        self.app = app
        self.tax_year = tax_year
        self._filling = False  # guards the var traces during programmatic fills

        self.title("Confirm Employment Income")
        self.configure(bg=theme.PAPER)
        self.transient(app)
        self.grab_set()
        self.geometry("780x640")
        self.minsize(660, 500)

        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)

        label = app._tax_year_label(tax_year)
        ttk.Label(
            outer, text=f"Tax year {label}", font=theme.fnt_semibold(11)
        ).pack(anchor="w")
        self.status_var = tk.StringVar()
        ttk.Label(outer, textvariable=self.status_var, wraplength=720).pack(
            anchor="w", pady=(2, 8)
        )
        self._refresh_status()

        ttk.Label(
            outer,
            text="Bank deposits show NET (after-tax) pay. Amend each deposit "
            "with its gross (pre-tax) amount - type it, or load the matching "
            "payslip PDF - or, if this income is paid without tax taken off "
            "at source, accept the deposits as already gross.",
            wraplength=720, foreground=theme.INK_MUTED,
        ).pack(anchor="w")

        # --- per-deposit breakdown ---
        repo = app.repo
        year_start, year_end = date(tax_year, 4, 6), date(tax_year + 1, 4, 5)
        self._txns = [
            raw for raw, cat in repo.get_categorised_transactions_in_period(year_start, year_end)
            if cat.category == TransactionCategory.EMPLOYMENT_INCOME.value
        ]
        saved = repo.get_employment_gross_entries([t.id for t in self._txns])

        list_frame = ttk.Frame(outer)
        list_frame.pack(fill="both", expand=True, pady=(8, 4))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        canvas = tk.Canvas(list_frame, highlightthickness=0, bg=theme.PAPER)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self._rows: list[dict] = []
        if not self._txns:
            ttk.Label(
                inner,
                text="No transactions are categorised as Employment Income for "
                "this tax year yet - categorise the salary deposits first, or "
                "confirm an annual figure below.",
                wraplength=680, foreground=theme.INK_MUTED,
            ).pack(anchor="w", pady=6)
        else:
            header = ttk.Frame(inner)
            header.pack(fill="x", pady=(2, 2))
            for text, width, anchor in [
                ("Date", 11, "w"), ("Description", 36, "w"),
                ("Deposit (net)", 13, "e"), ("  Gross (pre-tax)", 16, "w"),
            ]:
                ttk.Label(header, text=text, width=width, anchor=anchor,
                          font=theme.fnt_semibold(9)).pack(side="left")
            for txn in self._txns:
                row_frame = ttk.Frame(inner)
                row_frame.pack(fill="x", pady=1)
                ttk.Label(row_frame, text=txn.date.isoformat(), width=11).pack(side="left")
                desc = txn.description if len(txn.description) <= 36 else txn.description[:33] + "..."
                ttk.Label(row_frame, text=desc, width=36).pack(side="left")
                ttk.Label(row_frame, text=f"{txn.amount:,.2f}", width=13, anchor="e").pack(side="left")
                var = tk.StringVar()
                entry = saved.get(txn.id)
                if entry is not None:
                    var.set(f"{entry.gross_amount:.2f}")
                row = {
                    "txn": txn, "var": var,
                    "source": entry.source if entry else "manual",
                    "note": entry.note if entry else None,
                }
                var.trace_add("write", lambda *_, r=row: self._on_gross_edited(r))
                self._rows.append(row)
                ttk.Entry(row_frame, textvariable=var, width=14).pack(side="left", padx=(12, 4))
                ttk.Button(
                    row_frame, text="Payslip...", width=10,
                    command=lambda r=row: self._fill_from_payslip(r),
                ).pack(side="left")

        self.total_var = tk.StringVar()
        ttk.Label(outer, textvariable=self.total_var, font=theme.fnt_semibold(10)).pack(anchor="e")
        self._update_total()

        button_row = ttk.Frame(outer)
        button_row.pack(fill="x", pady=(4, 0))
        ttk.Button(
            button_row, text="Paid gross - deposits are already gross",
            command=self._confirm_paid_gross,
        ).pack(side="left")
        ttk.Button(
            button_row, text="Confirm gross total", style="Accent.TButton",
            command=self._confirm_breakdown,
        ).pack(side="right")

        ttk.Separator(outer).pack(fill="x", pady=10)

        ttk.Label(
            outer, text="Or confirm one annual gross figure - e.g. the total "
            "for the year from your P60 or P45:",
            wraplength=720,
        ).pack(anchor="w")
        annual_row = ttk.Frame(outer)
        annual_row.pack(fill="x", pady=6)
        ttk.Label(annual_row, text="Gross pay for the year:").pack(side="left")
        self.annual_var = tk.StringVar()
        ttk.Entry(annual_row, textvariable=self.annual_var, width=16).pack(side="left", padx=6)
        ttk.Button(
            annual_row, text="Confirm annual figure", command=self._confirm_annual,
        ).pack(side="left")
        ttk.Button(
            annual_row, text="Load P60/payslip PDF...",
            command=self._fill_annual_from_pdf,
        ).pack(side="left", padx=(8, 0))
        self._annual_loaded: tuple[float, str] | None = None  # (amount, note)

        ttk.Button(outer, text="Close", command=lambda: close_window(self)).pack(
            anchor="e", pady=(8, 0)
        )
        fade_in(self)

    # --- shared helpers ---

    def _refresh_status(self) -> None:
        record = self.app.repo.get_employment_income(self.tax_year)
        if record is None:
            self.status_var.set("Not yet confirmed - the summary will show UNCONFIRMED.")
        else:
            note = f" - {record.note}" if record.note else ""
            self.status_var.set(
                f"Confirmed: {fmt_money(record.gross_amount)} (source: {record.source}, "
                f"entered {record.confirmed_at:%Y-%m-%d}){note}"
            )

    def _save_total(self, amount: float, source: str, note: str | None) -> None:
        repo = self.app.repo
        repo.set_employment_income(self.tax_year, amount, source=source, note=note)
        repo.commit()
        self._refresh_status()
        messagebox.showinfo(
            "Confirmed", f"Employment income for {self.app._tax_year_label(self.tax_year)} "
            f"confirmed as {fmt_money(amount)}.", parent=self,
        )

    @staticmethod
    def _parse_amount(text: str) -> float | None:
        text = text.strip().replace(",", "").replace("£", "")
        if not text:
            return None
        return float(text)  # caller handles ValueError

    def _pick_pdf_candidate(self) -> tuple | None:
        """Open a payslip/P60 PDF and let the user pick one recognised
        figure. Returns (candidate, filename) or None."""
        path = filedialog.askopenfilename(
            parent=self, title="Select a payslip or P60 PDF",
            filetypes=[("PDF", "*.pdf"), ("All files", "*.*")],
        )
        if not path:
            return None
        from mtd_classifier.services.payslip_extractor import extract_candidates

        candidates = extract_candidates(Path(path))
        if not candidates:
            messagebox.showinfo(
                "No figures found",
                "No figures were recognised in this PDF - enter the amount "
                "manually instead.", parent=self,
            )
            return None
        picker = tk.Toplevel(self)
        picker.title(f"Figures found in {Path(path).name}")
        picker.transient(self)
        picker.grab_set()
        listbox = tk.Listbox(
            picker, height=min(8, len(candidates)), width=80, exportselection=False
        )
        theme.style_listbox(listbox)
        for c in candidates:
            period = "full year" if c.is_annual else "this period"
            listbox.insert("end", f"{c.label} ({period}): {fmt_money(c.amount)}   ({c.line})")
        listbox.selection_set(0)
        listbox.pack(padx=10, pady=10, fill="both", expand=True)
        result: list = []

        def confirm(_event=None) -> None:
            selection = listbox.curselection()
            if selection:
                result.append(candidates[selection[0]])
            close_window(picker)

        listbox.bind("<Double-Button-1>", confirm)
        ttk.Button(picker, text="Use this figure", command=confirm).pack(pady=(0, 10))
        fade_in(picker)
        picker.wait_window()
        return (result[0], Path(path).name) if result else None

    # --- per-deposit breakdown ---

    def _on_gross_edited(self, row: dict) -> None:
        if not self._filling:
            row["source"], row["note"] = "manual", None
        self._update_total()

    def _update_total(self) -> None:
        deposits = sum(r["txn"].amount for r in self._rows)
        total, bad = 0.0, False
        for r in self._rows:
            try:
                value = self._parse_amount(r["var"].get())
            except ValueError:
                bad = True
                continue
            if value is not None:
                total += value
        text = (
            f"Deposits (net) total: {fmt_money(round(deposits, 2))}    "
            f"Gross total: {fmt_money(round(total, 2))}"
        )
        if bad:
            text += "    (some amounts aren't numbers)"
        self.total_var.set(text)

    def _fill_from_payslip(self, row: dict) -> None:
        picked = self._pick_pdf_candidate()
        if picked is None:
            return
        candidate, filename = picked
        self._filling = True
        row["var"].set(f"{candidate.amount:.2f}")
        self._filling = False
        row["source"] = "payslip_pdf"
        row["note"] = f"{candidate.label} from {filename}: {candidate.line}"[:255]
        self._update_total()

    def _confirm_breakdown(self) -> None:
        if not self._rows:
            messagebox.showinfo(
                "No deposits listed",
                "No transactions are categorised as Employment Income for this "
                "tax year - use the annual figure below instead.", parent=self,
            )
            return
        filled: list[tuple[dict, float]] = []
        empty: list[dict] = []
        for r in self._rows:
            try:
                value = self._parse_amount(r["var"].get())
            except ValueError:
                messagebox.showinfo(
                    "Enter a number",
                    f"'{r['var'].get()}' (deposit on {r['txn'].date}) isn't a "
                    "number.", parent=self,
                )
                return
            if value is None:
                empty.append(r)
            else:
                filled.append((r, value))
        if not filled:
            messagebox.showinfo(
                "Nothing to confirm",
                "Enter a gross amount for at least one deposit - or use "
                "'Paid gross' if the deposits already are the gross pay.",
                parent=self,
            )
            return
        total = round(sum(v for _, v in filled), 2)
        prompt = (
            f"Confirm {fmt_money(total)} as the gross employment income for "
            f"{self.app._tax_year_label(self.tax_year)}?\n\n"
            f"({len(filled)} deposit(s) with gross amounts entered.)"
        )
        if empty:
            prompt += (
                f"\n\nWARNING: {len(empty)} deposit(s) have no gross amount "
                "and will NOT be counted."
            )
        if not messagebox.askyesno("Confirm gross total", prompt, parent=self):
            return
        repo = self.app.repo
        for r, value in filled:
            repo.set_employment_gross_entry(
                r["txn"].id, value, source=r["source"], note=r["note"],
            )
        repo.delete_employment_gross_entries([r["txn"].id for r in empty])
        self._save_total(
            total, source="statement_breakdown",
            note=f"Sum of {len(filled)} per-deposit gross amounts",
        )

    def _confirm_paid_gross(self) -> None:
        if not self._rows:
            messagebox.showinfo(
                "No deposits listed",
                "No transactions are categorised as Employment Income for this "
                "tax year - use the annual figure below instead.", parent=self,
            )
            return
        total = round(sum(r["txn"].amount for r in self._rows), 2)
        if not messagebox.askyesno(
            "Paid gross",
            f"Treat all {len(self._rows)} deposit(s) as GROSS pay - no tax "
            f"taken off at source - and confirm {fmt_money(total)} as the "
            f"total gross employment income for "
            f"{self.app._tax_year_label(self.tax_year)}?",
            parent=self,
        ):
            return
        self._filling = True
        for r in self._rows:
            r["var"].set(f"{r['txn'].amount:.2f}")
            r["source"], r["note"] = "paid_gross", None
        self._filling = False
        self._update_total()
        repo = self.app.repo
        for r in self._rows:
            repo.set_employment_gross_entry(
                r["txn"].id, r["txn"].amount, source="paid_gross",
            )
        self._save_total(
            total, source="paid_gross",
            note=f"{len(self._rows)} deposit(s) confirmed as paid gross",
        )

    # --- single annual figure ---

    def _fill_annual_from_pdf(self) -> None:
        picked = self._pick_pdf_candidate()
        if picked is None:
            return
        candidate, filename = picked
        if not candidate.is_annual and not messagebox.askyesno(
            "Not a full-year figure",
            f"{fmt_money(candidate.amount)} ({candidate.label}) looks like ONE "
            "period's pay, not a full-year total. Use it as the ANNUAL figure "
            "anyway?\n\n(To build the year up from monthly payslips, use the "
            "per-deposit list above instead.)",
            parent=self,
        ):
            return
        self.annual_var.set(f"{candidate.amount:.2f}")
        self._annual_loaded = (
            candidate.amount,
            f"{candidate.label} from {filename}: {candidate.line}"[:255],
        )

    def _confirm_annual(self) -> None:
        try:
            amount = self._parse_amount(self.annual_var.get())
        except ValueError:
            amount = None
        if amount is None:
            messagebox.showinfo(
                "Enter a number", "Enter the gross pay figure, e.g. 45000.00",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "Confirm figure",
            f"Confirm {fmt_money(amount)} as the gross employment income for "
            f"{self.app._tax_year_label(self.tax_year)}?",
            parent=self,
        ):
            return
        loaded = self._annual_loaded
        if loaded is not None and abs(loaded[0] - amount) < 0.005:
            self._save_total(amount, source="payslip_pdf", note=loaded[1])
        else:
            self._save_total(amount, source="manual", note=None)


class MtdApp(tk.Tk):
    """Main window."""

    def __init__(self, repo: Repository, show_walkthrough: bool | None = None,
                 user: str | None = None) -> None:
        super().__init__()
        self.repo = repo
        self.user = user
        self.switch_user_requested = False
        title = "MTD Classifier - Making Tax Digital"
        if user:
            title = f"MTD Classifier [{user}] - Making Tax Digital"
        self.title(title)
        self.geometry("1080x680")
        apply_theme(self)

        # Status bar first: tabs report into it while they build
        self.status_var = tk.StringVar(value="Ready")
        self._status_label = ttk.Label(
            self, textvariable=self.status_var, style="Status.TLabel", anchor="w"
        )
        self._status_label.pack(fill="x", side="bottom")
        ttk.Separator(self, orient="horizontal").pack(fill="x", side="bottom")

        # Colour lives in the bones: a pastel gradient band heads the window
        band_right = f"User: {user}" if user else ""
        self._band = gradient_band(self, height=34, left_text="MTD Classifier",
                                   right_text=band_right)
        self._band.pack(fill="x")

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=6, pady=4)
        self.add_account_btn = ttk.Button(toolbar, text="Add Account", command=self.add_account)
        self.add_account_btn.pack(side="left")

        # Tax year being worked on - filters every account's transaction list.
        # None means "All transactions" (no filter); folder imports, the
        # Summary tab and Employment Income always use concrete_tax_year
        # instead, since those are inherently tied to one specific tax year.
        current_ty = tax_year_for_date(date.today())
        settings = load_settings()
        saved_ty = settings.get("tax_year")
        saved_concrete_ty = settings.get("concrete_tax_year")
        self.tax_year = saved_ty if isinstance(saved_ty, int) else current_ty
        self.concrete_tax_year = (
            saved_concrete_ty if isinstance(saved_concrete_ty, int) else current_ty
        )
        # Whether the How-to-Submit guide opens by itself after generating
        # bridging files (opt-out lives on the guide itself)
        self.submission_guide_auto = settings.get("submission_guide_auto", True) is not False
        ttk.Label(toolbar, text="Tax year:").pack(side="left", padx=(16, 2))
        self.tax_year_var = tk.StringVar(value=self._tax_year_label(self.tax_year))
        # Covers every year with known tax rates (see tax_year_rates.py) so
        # the Tax Calculation tab's full historical range stays selectable
        # from here - it has no year selector of its own.
        from mtd_classifier.tax_year_rates import available_tax_years
        selectable_years = sorted(set(available_tax_years()) | {current_ty - 2, current_ty - 1, current_ty})
        self.tax_year_combo = ttk.Combobox(
            toolbar, textvariable=self.tax_year_var, state="readonly", width=15,
            values=[self._tax_year_label(ty) for ty in selectable_years]
            + [ALL_TRANSACTIONS_LABEL],
        )
        self.tax_year_combo.pack(side="left")
        self.tax_year_combo.bind("<<ComboboxSelected>>", self._on_tax_year_change)

        # Global reporting quarter: every cumulative view (Summary export/
        # bridging, Tax Calculation YTD) reports up to this quarter's end.
        # Quarters without complete cumulative data are greyed out.
        ttk.Label(toolbar, text="Up to quarter:").pack(side="left", padx=(16, 2))
        self.quarter_btn = ttk.Menubutton(toolbar, text="", width=18)
        self.quarter_menu = tk.Menu(self.quarter_btn, tearoff=0)
        self.quarter_btn.configure(menu=self.quarter_menu)
        self.quarter_btn.pack(side="left")
        self.export_quarter: TaxQuarter | None = None
        self.update_quarter_options()

        ttk.Button(toolbar, text="Help / Tour", command=self.show_walkthrough).pack(side="right")
        if user:
            # Active-user indicator plus in-session switching: the switch
            # closes this window and main()'s loop re-shows the selector.
            ttk.Button(
                toolbar, text="Switch User...", command=self.request_user_switch,
            ).pack(side="right", padx=(0, 6))
            ttk.Label(
                toolbar, text=f"User: {user}", font=theme.fnt_semibold(10),
            ).pack(side="right", padx=(0, 10))

        # One-time startup motion: the accent hairline sweeps across once,
        # then the window stays static until the user interacts.
        self._accent_line = startup_sweep(self, lambda: self.winfo_width())
        self._accent_line.pack(fill="x", padx=6)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=4)
        # Each tab owns a hue: account tabs alternate coral/sage, Summary is
        # sky, Tax Calculation lilac. The gliding indicator adopts the hue of
        # the tab it lands on; the list is live so added accounts join in.
        self._tab_hues: list[str] = []
        attach_tab_indicator(self.notebook, self._tab_hues)

        self.account_tabs: dict[str, AccountTab] = {}
        for spec in self._account_specs():
            self._add_account_tab(spec["label"], spec.get("bank"), spec.get("account"))

        self.summary_tab = SummaryTab(self.notebook, self)
        self.notebook.add(self.summary_tab, text="Summary & Bridging")
        self.tax_calc_tab = TaxCalculationTab(self.notebook, self)
        self.notebook.add(self.tax_calc_tab, text="Tax Calculation")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._refresh_tab_hues()

        if show_walkthrough is None:
            show_walkthrough = walkthrough_pending()
        if show_walkthrough:
            self.after(300, self.show_walkthrough)

        fade_in(self, 220)

    def _refresh_tab_hues(self) -> None:
        """Assign each tab its hue dot and keep the indicator's live colour
        list in step: account tabs alternate coral/sage, the Summary tab is
        sky, Tax Calculation lilac."""
        account_hues = (theme.CORAL_DEEP, theme.SAGE_INK)
        hues = []
        for tab_id in self.notebook.tabs():
            text = self.notebook.tab(tab_id, "text")
            if text == "Summary & Bridging":
                hue = theme.SKY_INK
            elif text == "Tax Calculation":
                hue = theme.LILAC_INK
            else:
                hue = account_hues[len(hues) % 2]
            hues.append(hue)
            try:
                self.notebook.tab(tab_id, image=tab_dot(self, hue), compound="left")
            except tk.TclError:
                pass
        self._tab_hues[:] = hues

    def request_user_switch(self) -> None:
        """Close this window so main()'s loop re-shows the user selector."""
        self.switch_user_requested = True
        close_window(self)

    # --- accounts ---

    def _account_specs(self) -> list[dict]:
        """Merge accounts saved by the GUI with those found in the database."""
        specs = {s["label"]: s for s in load_saved_accounts()}
        for bank, account in self.repo.get_distinct_accounts():
            label = account or bank
            specs.setdefault(label, {"label": label, "bank": bank, "account": account})
        return list(specs.values())

    def _add_account_tab(self, label: str, bank: str | None, account: str | None,
                         demo: bool = False) -> AccountTab:
        tab = AccountTab(self.notebook, self, label=label, bank=bank, account=account)
        tab.is_demo = demo
        if hasattr(self, "summary_tab"):
            # keep the Summary tab last
            self.notebook.insert(self.notebook.index(self.summary_tab), tab, text=label)
        else:
            self.notebook.add(tab, text=label)
        self.account_tabs[label] = tab
        if hasattr(self, "_tab_hues"):
            self._refresh_tab_hues()
        return tab

    def add_account(self) -> None:
        dialog = AddAccountDialog(self)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        label, bank = dialog.result
        if label in self.account_tabs:
            messagebox.showinfo("Exists", f"There is already a tab for '{label}'.", parent=self)
            return
        tab = self._add_account_tab(label, bank, account=label)
        self.notebook.select(tab)
        self.persist_accounts()

    def set_submission_guide_auto(self, enabled: bool) -> None:
        self.submission_guide_auto = bool(enabled)
        self.persist_accounts()

    def persist_accounts(self) -> None:
        save_settings({
            "tax_year": self.tax_year,
            "concrete_tax_year": self.concrete_tax_year,
            "submission_guide_auto": self.submission_guide_auto,
            "accounts": [
                {"label": t.label, "bank": t.bank, "account": t.account}
                for t in self.account_tabs.values()
                if not getattr(t, "is_demo", False)
            ],
        })

    # --- tax year ---

    @staticmethod
    def _tax_year_label(tax_year: int | None) -> str:
        if tax_year is None:
            return ALL_TRANSACTIONS_LABEL
        return f"{tax_year}-{(tax_year + 1) % 100:02d}"

    def _on_tax_year_change(self, _event=None) -> None:
        label = self.tax_year_var.get()
        if label == ALL_TRANSACTIONS_LABEL:
            self.tax_year = None
        else:
            self.tax_year = int(label.split("-")[0])
            self.concrete_tax_year = self.tax_year
        self.update_quarter_options()
        for tab in self.account_tabs.values():
            tab.refresh()
        self.summary_tab.update_periods()
        self.tax_calc_tab.refresh()
        self.persist_accounts()
        if self.tax_year is None:
            self.set_status("Showing all transactions, every tax year")
        else:
            self.set_status(
                f"Working on tax year {label} "
                f"({date(self.tax_year, 4, 6)} to {date(self.tax_year + 1, 4, 5)})"
            )

    def tax_year_range(self) -> tuple[date, date]:
        """The concrete tax year being worked on, capped at today (no future
        transactions). Folder imports always target one specific tax year,
        so this ignores "All transactions" mode and uses concrete_tax_year."""
        start = date(self.concrete_tax_year, 4, 6)
        end = min(date(self.concrete_tax_year + 1, 4, 5), date.today())
        return start, end

    # --- reporting quarter (global cumulative "up to" selector) ---

    def _quarter_ytd_status(self, tq: TaxQuarter) -> tuple[str, str]:
        """How selectable a quarter is, based on its cumulative year-to-date
        data (6 April to the quarter's end). Returns (state, note):

        - ("blocked", reason): greyed out - the quarter hasn't ended, or
          there is no data at all, so a cumulative export is impossible.
        - ("warn", note): selectable, but completeness couldn't be VERIFIED
          (no unbroken balance chain and no agreeing second source) - the
          export flow shows its detailed verification warning before output.
        - ("ok", ""): selectable, completeness verified.
        """
        start = date(tq.tax_year, 4, 6)
        txns = self.repo.get_transactions_in_period(start, tq.end_date)
        if getattr(self, "_tour_active", False):
            # The tour's example data is synthetic - skip completeness rules
            return ("ok", "") if txns else ("blocked", "no data")
        if tq.end_date > date.today():
            return "blocked", "not ended yet"
        if not txns:
            return "blocked", "no data"
        results = verify_cross_format(self.repo, start, tq.end_date)
        if all(r.ok for r in results):
            return "ok", ""
        return "warn", "completeness not verified"

    def update_quarter_options(self) -> None:
        """Rebuild the 'Up to quarter' menu. Impossible quarters (not ended,
        or no data) are greyed out; quarters whose completeness couldn't be
        verified stay selectable but carry a warning - the export flow's
        verification prompt gives the details and the final say. The whole
        selector is greyed out in "All transactions" mode or when no
        quarter is selectable at all."""
        self.quarter_menu.delete(0, "end")
        if self.tax_year is None:
            self.export_quarter = None
            self.quarter_btn.configure(state="disabled", text="(pick a tax year)")
            return
        selectable: list[TaxQuarter] = []
        clean: list[TaxQuarter] = []
        for q in range(1, 5):
            tq = TaxQuarter(self.concrete_tax_year, q)
            state, note = self._quarter_ytd_status(tq)
            if state == "blocked":
                self.quarter_menu.add_command(
                    label=f"{tq.label}  ({note})", state="disabled"
                )
                continue
            selectable.append(tq)
            if state == "ok":
                clean.append(tq)
            suffix = f"  (! {note})" if note else ""
            self.quarter_menu.add_command(
                label=f"{tq.label}{suffix}",
                command=lambda t=tq: self.select_export_quarter(t),
            )
        if not selectable:
            self.export_quarter = None
            self.quarter_btn.configure(state="disabled", text="No complete quarter")
            return
        self.quarter_btn.configure(state="normal")
        current = self.export_quarter
        if (
            current is None
            or current.tax_year != self.concrete_tax_year
            or all(t.quarter != current.quarter for t in selectable)
        ):
            # Prefer the latest VERIFIED quarter; fall back to the latest
            # selectable one (its warning still shows in the menu).
            self.export_quarter = (clean or selectable)[-1]
        self.quarter_btn.configure(text=self.export_quarter.label)

    def select_export_quarter(self, tq: TaxQuarter) -> None:
        self.export_quarter = tq
        self.quarter_btn.configure(text=tq.label)
        if hasattr(self, "summary_tab"):
            self.summary_tab.refresh()
            self.tax_calc_tab.refresh()
        self.set_status(
            f"Reporting cumulative figures for {date(tq.tax_year, 4, 6)} to "
            f"{tq.end_date} (up to {tq.label})"
        )

    # --- tour demo mode ---

    def start_tour_demo(self) -> None:
        """Swap in an in-memory example dataset for the walkthrough.

        The real repository is untouched; it is restored by end_tour_demo().
        """
        if getattr(self, "_tour_active", False):
            return
        from mtd_classifier.gui.demo import DEMO_ACCOUNT, DEMO_BANK, DEMO_TAB_LABEL, create_demo_repo

        self._tour_active = True
        self._real_repo = self.repo
        # Seed the example data inside the selected tax year so the summary
        # step has figures to show
        ty_start, ty_end = date(self.concrete_tax_year, 4, 6), date(self.concrete_tax_year + 1, 4, 5)
        ref_date = date.today() if ty_start <= date.today() <= ty_end else ty_start
        demo_repo, refund_id = create_demo_repo(today=ref_date)
        self._demo_repo = demo_repo
        self.repo = demo_repo
        self._tour_demo_quarter = quarter_for_date(ref_date).label
        self.tour_refund_id = refund_id
        self.tour_tab = self._add_account_tab(
            DEMO_TAB_LABEL, DEMO_BANK, DEMO_ACCOUNT, demo=True
        )
        for tab in self.account_tabs.values():
            if tab is not self.tour_tab:
                tab.refresh()
        self.update_quarter_options()
        demo_quarter = quarter_for_date(ref_date)
        if self._quarter_ytd_status(demo_quarter)[0]:
            self.export_quarter = demo_quarter
            self.quarter_btn.configure(state="normal", text=demo_quarter.label)
        self.summary_tab.refresh()
        self.notebook.select(self.tour_tab)
        self.set_status("Tour running on example data - it disappears when the tour ends.")

    def end_tour_demo(self) -> None:
        """Remove the example data and restore the real database."""
        if not getattr(self, "_tour_active", False):
            return
        self._tour_active = False
        tab = self.tour_tab
        self.tour_tab = None
        self.account_tabs.pop(tab.label, None)
        self.notebook.forget(tab)
        tab.destroy()
        self.repo = self._real_repo
        self._demo_repo.session.close()
        self._demo_repo = None
        self.update_quarter_options()
        for t in self.account_tabs.values():
            t.refresh()
        self.summary_tab.update_periods()
        self.set_status("Ready")

    # --- misc ---

    def set_status(self, text: str) -> None:
        self.status_var.set(text)
        settle_status(self._status_label, self)

    def show_walkthrough(self) -> None:
        WalkthroughDialog(self)

    def _on_tab_changed(self, _event=None) -> None:
        """Refresh the Summary/Tax Calculation tabs automatically when opened."""
        try:
            selected = self.notebook.select()
            if selected == str(self.summary_tab):
                self.update_quarter_options()
                self.summary_tab.refresh()
            elif selected == str(self.tax_calc_tab):
                self.update_quarter_options()
                self.tax_calc_tab.refresh()
        except tk.TclError:
            pass


class AddAccountDialog(tk.Toplevel):
    """Ask for an account name and (optionally) which bank's format it uses."""

    def __init__(self, app: MtdApp) -> None:
        super().__init__(app)
        self.result: tuple[str, str | None] | None = None
        self.title("Add Account")
        self.configure(bg=theme.PAPER)
        self.transient(app)
        self.grab_set()

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Account name (e.g. 'First Direct Current'):").grid(
            row=0, column=0, sticky="w"
        )
        self.name_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.name_var, width=36).grid(
            row=1, column=0, sticky="we", pady=(2, 8)
        )
        ttk.Label(frame, text="Statement format:").grid(row=2, column=0, sticky="w")
        self.bank_var = tk.StringVar(value=BANK_CHOICES[0])
        ttk.Combobox(
            frame, textvariable=self.bank_var, state="readonly", values=BANK_CHOICES,
        ).grid(row=3, column=0, sticky="we", pady=(2, 8))

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, sticky="e")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(buttons, text="Add", command=self.submit, style="Accent.TButton").pack(
            side="right"
        )
        fade_in(self)

    def submit(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            messagebox.showinfo("Name needed", "Enter a name for the account.", parent=self)
            return
        bank = self.bank_var.get()
        self.result = (name, None if bank == "auto-detect" else bank)
        self.destroy()


def main() -> None:
    """Entry point for `mtd gui` / `mtd-gui`.

    Runs as a select-user -> main-window loop: choosing a user opens their
    window; Switch User closes it and returns to the selector; closing the
    selector exits.
    """
    ensure_app_dir()
    preselect = config.last_user()
    while True:
        user = select_user(preselect)
        if user is None:
            return
        _activate_user_paths(user)
        engine = get_engine()
        init_db(engine)
        session = get_session(engine)
        repo = Repository(session)
        app = MtdApp(repo, user=user)
        app.mainloop()
        session.close()
        if not app.switch_user_requested:
            return
        preselect = user


if __name__ == "__main__":
    main()
