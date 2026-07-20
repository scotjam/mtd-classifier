"""First-startup walkthrough: spotlights each element against example data.

While the tour runs, the app is switched to an in-memory demo dataset
(see demo.py) so every highlighted element has something real to show.
The demo data is removed and the real database restored when the tour
finishes, is skipped, or is closed.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from dataclasses import dataclass
from typing import Callable

import webbrowser

from mtd_classifier.config import APP_DIR
from mtd_classifier.gui.theme import (
    BRICK,
    INK,
    INK_MUTED,
    PAPER,
    SKY_INK,
    SPOTLIGHT_COLORS,
    close_window,
    fade_in,
    fnt,
    fnt_semibold,
    gradient_band,
)

WALKTHROUGH_MARKER = APP_DIR / "gui_walkthrough_done"

# A highlight target: a widget, or (widget, clip_height) to spotlight only
# the top strip of a tall widget (used for the notebook's tab row)
Target = "tk.Widget | tuple[tk.Widget, int]"


class Highlighter:
    """Draws a pulsing border around one or more widgets in the main window."""

    COLORS = SPOTLIGHT_COLORS
    THICKNESS = 3

    def __init__(self, app) -> None:
        self.app = app
        self.frames: list[tk.Frame] = []
        self._pulse_job: str | None = None
        self._color_index = 0

    def show(self, targets: list) -> None:
        self.clear()
        for target in targets:
            clip = None
            widget = target
            if isinstance(target, tuple):
                widget, clip = target
            if widget is None or not widget.winfo_ismapped():
                continue
            x = widget.winfo_rootx() - self.app.winfo_rootx()
            y = widget.winfo_rooty() - self.app.winfo_rooty()
            w = widget.winfo_width()
            h = widget.winfo_height() if clip is None else min(clip, widget.winfo_height())
            t, pad = self.THICKNESS, 2
            sides = [
                (x - pad, y - pad, w + 2 * pad, t),              # top
                (x - pad, y + h + pad - t, w + 2 * pad, t),      # bottom
                (x - pad, y - pad, t, h + 2 * pad),              # left
                (x + w + pad - t, y - pad, t, h + 2 * pad),      # right
            ]
            for sx, sy, sw, sh in sides:
                frame = tk.Frame(self.app, bg=self.COLORS[0])
                frame.place(x=sx, y=sy, width=max(sw, t), height=max(sh, t))
                self.frames.append(frame)
        if self.frames:
            self._pulse()

    def _pulse(self) -> None:
        self._color_index = 1 - self._color_index
        color = self.COLORS[self._color_index]
        for frame in self.frames:
            frame.configure(bg=color)
        self._pulse_job = self.app.after(450, self._pulse)

    def clear(self) -> None:
        if self._pulse_job is not None:
            self.app.after_cancel(self._pulse_job)
            self._pulse_job = None
        for frame in self.frames:
            frame.destroy()
        self.frames = []


@dataclass
class Step:
    title: str
    body: str
    # Runs before highlighting, e.g. select a tab or a demo row
    prepare: Callable[[], None] | None = None
    # Returns the widgets to spotlight
    targets: Callable[[], list] | None = None


def build_steps(app) -> list[Step]:
    """The tour, in the order a new user needs it.

    Requires demo mode to be active (app.tour_tab set) so every step has
    populated widgets to point at.
    """

    def tab():
        return app.tour_tab

    def select_demo_tab() -> None:
        if tab() is not None:
            app.notebook.select(tab())

    def select_refund_row() -> None:
        select_demo_tab()
        t = tab()
        if t is None:
            return
        iid = str(app.tour_refund_id)
        if iid in t.tree.get_children():
            t.tree.selection_set(iid)
            t.tree.see(iid)
            t.on_select()

    def select_summary() -> None:
        app.notebook.select(app.summary_tab)

    return [
        Step(
            "Welcome to MTD Classifier",
            "This app turns your bank statements into the figures HMRC needs "
            "for Making Tax Digital quarterly updates.\n\n"
            "The workflow is:\n"
            "  1. Import your monthly bank statement files\n"
            "  2. Categorise each business transaction\n"
            "  3. Generate bridging files for upload to My Tax Digital\n\n"
            "For this tour the app is showing EXAMPLE data in a temporary "
            "'Demo Account (tour)' tab - it disappears when the tour ends. "
            "Each step highlights the part of the window it describes.",
            prepare=select_demo_tab,
        ),
        Step(
            "Account tabs and the tax year",
            "Each bank account gets its own tab along the top, so statements "
            "and transactions from different accounts stay separate. Use the "
            "highlighted 'Add Account' button to create a tab for each "
            "account you use for business income or expenses (e.g. 'First "
            "Direct Current', 'Santander Rental').\n\n"
            "The 'Tax year' selector sets which year you're working on - "
            "folder imports and the quarterly summary follow it.",
            prepare=select_demo_tab,
            targets=lambda: [(app.notebook, 34), app.add_account_btn, app.tax_year_combo],
        ),
        Step(
            "Importing statements",
            "'Import Statements...' lets you select monthly statement files "
            "(CSV or OFX) - as many at once as you like. 'Import Folder...' "
            "goes further: point it at a folder of statements and it pulls "
            "in ONLY the transactions belonging to the selected tax year.\n\n"
            "Safe to re-import: files and overlapping date ranges you've "
            "already imported are detected and skipped. A statement that "
            "doesn't match this account's bank triggers a warning, and if "
            "the same day/description/amount appears twice you'll see the "
            "original statement lines and decide whether it's a genuine "
            "second transaction.",
            prepare=select_demo_tab,
            targets=lambda: [tab() and tab().import_btn,
                             tab() and tab().import_folder_btn],
        ),
        Step(
            "The transaction list",
            "All imported transactions for the account appear here as one "
            "consolidated list - like the example lines shown now.\n\n"
            "Lines highlighted in yellow (e.g. TESCO STORES) are still "
            "uncategorised and won't be included in your tax figures until "
            "you categorise them. Tick 'Uncategorised only' to focus on "
            "what's left to do.",
            prepare=select_demo_tab,
            targets=lambda: [tab() and tab().tree, tab() and tab().uncat_check],
        ),
        Step(
            "Categorising",
            "Select one or more lines, pick a category from the highlighted "
            "dropdown, and click 'Apply to Selected'. The picker is grouped: "
            "SA103 (self-employment) boxes first, then SA105 (UK property), "
            "then year-end items like Personal, Transfers and Tax Payments "
            "that are left out of your quarterly figures.\n\n"
            "'Auto-classify' applies your saved rules to everything "
            "uncategorised. The app learns: every category you set by hand "
            "becomes a rule for next time.",
            prepare=select_demo_tab,
            targets=lambda: [
                tab() and tab().category_combo,
                tab() and tab().apply_btn,
                tab() and tab().autoclassify_btn,
            ],
        ),
        Step(
            "Right-click: the fast path",
            "Right-click any transaction for the quick menu: filter to "
            "similar lines, categorise, pair a refund, mark an expense as "
            "disallowable for tax, or delete.\n\n"
            "While the Categorise submenu is open, HOVER over any category "
            "and a tooltip shows HMRC's guidance on what belongs in it - "
            "for example, that box 24 covers council tax, water rates and "
            "ground rent, or that home-to-work commuting doesn't count as "
            "business travel.\n\n"
            "You can also click any column heading to sort, and type in the "
            "Search box to filter the list as you type.",
            prepare=select_demo_tab,
            targets=lambda: [tab() and tab().tree],
        ),
        Step(
            "Details, refunds and reversals",
            "The example line selected now is a +180.00 credit from PLUMBER J "
            "SMITH - nine days after a -180.00 payment to the same plumber.\n\n"
            "The highlighted panel shows the original statement extract for "
            "any selected line, and offers nearby opposite-amount "
            "transactions as possible refunds or reversals - a double "
            "payment sent back, or an expense refunded. Click 'Mark as "
            "Refund' and the line inherits the original's category, so it "
            "nets against the same HMRC box instead of counting as income.",
            prepare=select_refund_row,
            targets=lambda: [tab() and tab().details_frame],
        ),
        Step(
            "Summary & Bridging",
            "This tab shows your quarterly totals per HMRC box, across all "
            "accounts - here calculated from the example data.\n\n"
            "Pick the quarter, check the figures, then click the highlighted "
            "'Generate Bridging Files' button. It fills My Tax Digital's "
            "official SA103/SA105 templates with cumulative year-to-date "
            "figures, ready to upload at mytaxdigital.co.uk.\n\n"
            "The highlighted 'How to Submit...' button walks you through the "
            "whole submission - registering businesses with HMRC, Bridging "
            "mode, picking the right obligation, and the two traps: the "
            "upload's 'to' date defaults to TODAY (set it to the quarter "
            "end), and a submitted obligation cannot be amended, so "
            "cross-check every figure first.\n\n"
            "That's the tour - the example data is removed when you click "
            "Finish. Re-run it any time from the Help button.",
            prepare=select_summary,
            targets=lambda: [
                app.quarter_btn,
                app.summary_tab.bridging_btn,
                app.summary_tab.submission_guide_btn,
            ],
        ),
    ]


class WalkthroughDialog(tk.Toplevel):
    """Modal step-by-step tour with element spotlighting on demo data."""

    def __init__(self, app) -> None:
        super().__init__(app)
        self.app = app
        app.start_tour_demo()
        self.highlighter = Highlighter(app)
        self.steps = build_steps(app)
        self.index = 0

        self.title("Welcome tour")
        self.configure(bg=PAPER)
        self.transient(app)
        self.grab_set()
        self._place_beside_app()

        self.step_label = ttk.Label(self, text="", font=("Segoe UI", 13, "bold"))
        self.step_label.pack(anchor="w", padx=16, pady=(16, 4))

        self.progress_label = ttk.Label(self, text="", foreground="grey")
        self.progress_label.pack(anchor="w", padx=16)

        self.body = tk.Text(
            self, wrap="word", relief="flat", height=12,
            font=fnt(10), padx=8, pady=8,
            background=PAPER, foreground=INK, highlightthickness=0,
        )
        self.body.pack(fill="both", expand=True, padx=12, pady=8)
        self.body.configure(state="disabled")

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        self.back_btn = ttk.Button(buttons, text="< Back", command=self.back)
        self.back_btn.pack(side="left")
        self.next_btn = ttk.Button(buttons, text="Next >", command=self.next)
        self.next_btn.pack(side="right")
        ttk.Button(buttons, text="Skip tour", command=self.finish).pack(side="right", padx=8)

        self.protocol("WM_DELETE_WINDOW", self.finish)
        fade_in(self)
        self.show_step()

    def _place_beside_app(self) -> None:
        """Prefer sitting beside the main window so spotlights stay visible."""
        self.update_idletasks()
        width, height = 560, 430
        app_x = self.app.winfo_rootx()
        app_y = self.app.winfo_rooty()
        app_w = self.app.winfo_width()
        screen_w = self.winfo_screenwidth()
        x = app_x + app_w + 10
        if x + width > screen_w:  # no room beside: overlap the right edge
            x = max(0, min(app_x + app_w - width - 20, screen_w - width))
        y = max(0, app_y + 40)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def show_step(self) -> None:
        step = self.steps[self.index]
        self.step_label.config(text=step.title)
        self.progress_label.config(
            text=f"Step {self.index + 1} of {len(self.steps)}"
        )
        self.body.configure(state="normal")
        self.body.delete("1.0", "end")
        self.body.insert("1.0", step.body)
        self.body.configure(state="disabled")
        self.back_btn.state(["!disabled"] if self.index > 0 else ["disabled"])
        self.next_btn.config(
            text="Finish" if self.index == len(self.steps) - 1 else "Next >"
        )
        # The tour must never crash the app over a highlight nicety
        try:
            if step.prepare is not None:
                step.prepare()
            self.app.update_idletasks()
            targets = [t for t in (step.targets() if step.targets else []) if t]
            self.highlighter.show(targets)
        except Exception:
            self.highlighter.clear()

    def next(self) -> None:
        if self.index == len(self.steps) - 1:
            self.finish()
            return
        self.index += 1
        self.show_step()

    def back(self) -> None:
        if self.index > 0:
            self.index -= 1
            self.show_step()

    def finish(self) -> None:
        self.highlighter.clear()
        self.app.end_tour_demo()
        mark_walkthrough_done()
        self.grab_release()
        self.destroy()


def walkthrough_pending() -> bool:
    return not WALKTHROUGH_MARKER.exists()


def mark_walkthrough_done() -> None:
    WALKTHROUGH_MARKER.parent.mkdir(parents=True, exist_ok=True)
    WALKTHROUGH_MARKER.touch()


# --- My Tax Digital submission guide ---------------------------------------

# (title, body, links) - the steps happen on external websites, so this is
# a plain pager rather than a widget-spotlighting tour.
SUBMISSION_STEPS: "list[tuple[str, str, list[tuple[str, str]]]]" = [
    (
        "1. Register your businesses with HMRC",
        "Before My Tax Digital can submit anything, HMRC must know about "
        "each business (income source): your self-employment and/or your UK "
        "property business.\n\n"
        "Sign in to your HMRC online account (the Government Gateway "
        "account signed up to Making Tax Digital for Income Tax) and add "
        "each business there. My Tax Digital can only see businesses that "
        "HMRC already knows about - if a business is missing later, this is "
        "the step to come back to.",
        [
            ("GOV.UK - Use Making Tax Digital for Income Tax",
             "https://www.gov.uk/guidance/use-making-tax-digital-for-income-tax"),
            ("GOV.UK - Sign in to HMRC online services",
             "https://www.gov.uk/log-in-register-hmrc-online-services"),
        ],
    ),
    (
        "2. Find the businesses in My Tax Digital",
        "Log in to My Tax Digital and authorise it with HMRC when prompted "
        "(a one-time grant through your Government Gateway sign-in).\n\n"
        "Choose 'Business income' - the businesses you registered with HMRC "
        "in step 1 appear there automatically.",
        [
            ("My Tax Digital - log in", "https://mytaxdigital.co.uk/app"),
            ("My Tax Digital - about", "https://mytaxdigital.co.uk"),
        ],
    ),
    (
        "3. Bridging mode and the accounting period",
        "Make sure Bridging mode is selected - that's the mode that accepts "
        "this app's spreadsheet files.\n\n"
        "Select the accounting period. Leave the default 6 April to 5 April "
        "unless your business genuinely uses a different accounting period - "
        "this app's figures are built on the 6 April tax year, so the "
        "default is the right choice.",
        [],
    ),
    (
        "4. Open the business and pick the obligation",
        "Click one of your businesses and choose 'Obligations'. HMRC's "
        "expected quarterly periods are listed there.\n\n"
        "Select the period you are submitting - for example 6 April to "
        "5 July for Q1. Each business is submitted separately: the SA103 "
        "file to the self-employment business, the SA105 file to the "
        "property business.",
        [],
    ),
    (
        "5. Import figures - CHECK THE 'TO' DATE",
        "Click 'Import figures' and upload the bridging file this app "
        "generated for that business.\n\n"
        "IMPORTANT: set the 'to' date to the QUARTER END (for example "
        "5 July for Q1) - the form defaults to TODAY'S DATE, which is wrong "
        "unless you happen to be submitting on the last day of the quarter. "
        "A submission with the wrong 'to' date misstates which period your "
        "cumulative figures cover.",
        [],
    ),
    (
        "6. Cross-check everything, then submit",
        "ONCE SUBMITTED, AN OBLIGATION CANNOT BE AMENDED - so before "
        "clicking Submit, cross-check every figure shown on screen against "
        "this app's Summary tab (double-click any row there to see the "
        "exact transactions behind it).\n\n"
        "When the figures match, click Submit and save the HMRC receipt. "
        "Then repeat from step 4 for the other business with its own file.",
        [],
    ),
]


class SubmissionGuideDialog(tk.Toplevel):
    """Step-by-step guide to submitting the bridging files on the My Tax
    Digital website. Deliberately non-modal so it can sit beside the
    browser while the user follows along.

    ``auto_opened=True`` is the after-generating-bridging-files mode: a
    Skip Guide button appears, plus a checkbox to stop it opening
    automatically in future (persisted via app.set_submission_guide_auto).
    """

    def __init__(self, app, auto_opened: bool = False) -> None:
        super().__init__(app)
        self.title("How to submit to My Tax Digital")
        self.configure(bg=PAPER)
        self.transient(app)
        self.geometry("640x470" if not auto_opened else "640x500")
        self.minsize(560, 400)
        self.index = 0

        gradient_band(self, height=32, left_text="Submitting to My Tax Digital").pack(fill="x")
        self.step_title = ttk.Label(self, text="", font=fnt_semibold(12))
        self.step_title.pack(anchor="w", padx=18, pady=(14, 4))
        self.step_body = ttk.Label(
            self, text="", wraplength=590, justify="left", font=fnt(10),
        )
        self.step_body.pack(anchor="w", padx=18)
        self.links_frame = ttk.Frame(self)
        self.links_frame.pack(anchor="w", padx=18, pady=(10, 0), fill="x")
        self.warning_label = ttk.Label(
            self, text="", wraplength=590, justify="left",
            foreground=BRICK, font=fnt_semibold(9),
        )
        self.warning_label.pack(anchor="w", padx=18, pady=(8, 0))

        footer = ttk.Frame(self, padding=(18, 10, 18, 14))
        footer.pack(side="bottom", fill="x")
        self.counter = ttk.Label(footer, text="", foreground=INK_MUTED, font=fnt(9))
        self.counter.pack(side="left")
        self.next_btn = ttk.Button(footer, text="Next", style="Accent.TButton",
                                   command=self._next)
        self.next_btn.pack(side="right")
        self.back_btn = ttk.Button(footer, text="Back", command=self._back)
        self.back_btn.pack(side="right", padx=(0, 8))

        if auto_opened:
            self.skip_btn = ttk.Button(
                footer, text="Skip Guide", command=lambda: close_window(self),
            )
            self.skip_btn.pack(side="left", padx=(12, 0))
            # Packed after the footer with side=bottom, so it sits just
            # above it. Unticking is remembered (gui settings).
            self.auto_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(
                self,
                text="Open this guide automatically after generating bridging files",
                variable=self.auto_var,
                command=lambda: app.set_submission_guide_auto(self.auto_var.get()),
            ).pack(side="bottom", anchor="w", padx=18, pady=(0, 2))

        self._render()
        fade_in(self)

    def _render(self) -> None:
        title, body, links = SUBMISSION_STEPS[self.index]
        self.step_title.configure(text=title)
        self.step_body.configure(text=body)
        for child in self.links_frame.winfo_children():
            child.destroy()
        for label, url in links:
            link = ttk.Label(
                self.links_frame, text=f"\N{RIGHTWARDS ARROW} {label}",
                foreground=SKY_INK, cursor="hand2",
                font=(fnt(10)[0], 10, "underline"),
            )
            link.pack(anchor="w", pady=1)
            link.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
        # The two traps worth repeating on their own steps
        if self.index == 4:
            self.warning_label.configure(
                text="The 'to' date defaults to the CURRENT DATE - change it "
                "to the quarter end before importing."
            )
        elif self.index == 5:
            self.warning_label.configure(
                text="No amendments after Submit - cross-check first."
            )
        else:
            self.warning_label.configure(text="")
        self.counter.configure(text=f"Step {self.index + 1} of {len(SUBMISSION_STEPS)}")
        self.back_btn.configure(state="normal" if self.index else "disabled")
        self.next_btn.configure(
            text="Done" if self.index == len(SUBMISSION_STEPS) - 1 else "Next"
        )

    def _next(self) -> None:
        if self.index >= len(SUBMISSION_STEPS) - 1:
            close_window(self)
            return
        self.index += 1
        self._render()

    def _back(self) -> None:
        if self.index:
            self.index -= 1
            self._render()
