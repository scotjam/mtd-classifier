"""Canvas-drawn transaction table with true hairlines between rows.

ttk.Treeview on the Tk 8.6 that ships with Python cannot draw per-row
separator lines, and its zebra striping clashed with the semantic row
colours (money in / refund / uncategorised). The account ledger is drawn on
a Canvas instead: every row sits on paper with a faint rule underneath it.

LedgerTable implements the subset of the Treeview API the app (and its
tests) actually use - insert/delete/get_children/item/set, heading/column,
tag_configure, selection/selection_set/see/identify_row, yview and the
<<TreeviewSelect>> virtual event - so AccountTab drives it exactly like the
old widget. Columns are drag-resizable on the header; once the user resizes
one, automatic stretching is switched off so the chosen width sticks.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, font as tkfont

from mtd_classifier.gui import theme

HEADER_H = 26
ROW_H = 26
_GRAB = 5  # px either side of a column boundary that starts a resize drag
SCROLL_STEPS = 14  # frames in an animated see()
SCROLL_FRAME_MS = 16


class LedgerTable(ttk.Frame):
    """A flat (no hierarchy) Treeview-compatible table drawn on a Canvas."""

    def __init__(self, master, columns=(), show="headings",
                 selectmode="extended", height=None) -> None:
        super().__init__(master)
        self._columns = list(columns)
        self._heading_cfg = {c: {"text": "", "command": None} for c in self._columns}
        self._col_cfg = {
            c: {"width": 100, "anchor": "w", "stretch": True, "minwidth": 28}
            for c in self._columns
        }
        self._tags: dict[str, dict] = {}
        self._items: list[dict] = []
        self._by_iid: dict[str, dict] = {}
        self._sel: list[str] = []
        self._anchor_iid: str | None = None
        self._auto_iid = 0
        self._user_resized = False
        self._rowheight = ROW_H
        self._font = tkfont.Font(root=self, font=theme.fnt(10))
        self._heading_font = tkfont.Font(root=self, font=theme.fnt_semibold(9))
        self._hairline = theme._blend(theme.RULE, theme.PAPER, 0.35)
        self._redraw_scheduled = False
        self._scroll_job: str | None = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.header = tk.Canvas(self, height=HEADER_H, bg=theme.PAPER_RAISED,
                                highlightthickness=0)
        self.header.grid(row=0, column=0, sticky="ew")
        self.body = tk.Canvas(self, bg=theme.PAPER, highlightthickness=0,
                              takefocus=1, yscrollincrement=self._rowheight)
        self.body.grid(row=1, column=0, sticky="nsew")

        self.body.bind("<Button-1>", self._on_click)
        self.body.bind("<Control-Button-1>", self._on_ctrl_click)
        self.body.bind("<Shift-Button-1>", self._on_shift_click)
        self.body.bind("<MouseWheel>", self._on_wheel)
        self.body.bind("<Configure>", lambda e: self._schedule_redraw())
        self.body.bind("<Up>", lambda e: self._move_selection(-1))
        self.body.bind("<Down>", lambda e: self._move_selection(1))

        self._drag_col: str | None = None
        self._drag_start_x = 0
        self._drag_start_w = 0
        self._press_col: str | None = None
        self.header.bind("<Motion>", self._on_header_motion)
        self.header.bind("<Button-1>", self._on_header_press)
        self.header.bind("<B1-Motion>", self._on_header_drag)
        self.header.bind("<ButtonRelease-1>", self._on_header_release)

    # --- event plumbing -------------------------------------------------

    def bind(self, sequence=None, func=None, add=None):
        """Virtual events stay on this frame (where they are generated);
        real pointer/key events go to the body canvas the user interacts
        with, so callers' event.y matches identify_row()."""
        if sequence and sequence.startswith("<<"):
            return super().bind(sequence, func, add)
        return self.body.bind(sequence, func, add)

    def configure(self, cnf=None, **kw):
        if "yscrollcommand" in kw:
            self.body.configure(yscrollcommand=kw.pop("yscrollcommand"))
        if cnf or kw:
            return super().configure(cnf, **kw)

    config = configure

    def yview(self, *args):
        result = self.body.yview(*args)
        if args:
            self._schedule_redraw()
        return result

    def _fire_select(self) -> None:
        try:
            self.event_generate("<<TreeviewSelect>>")
        except tk.TclError:
            pass

    # --- data API (Treeview-compatible subset) ---------------------------

    def insert(self, parent, index, iid=None, values=(), tags=(), **kw) -> str:
        if iid is None:
            self._auto_iid += 1
            iid = f"I{self._auto_iid:05d}"
        iid = str(iid)
        if iid in self._by_iid:
            raise tk.TclError(f"Item {iid} already exists")
        item = {"iid": iid, "values": tuple(str(v) for v in values),
                "tags": tuple(tags)}
        self._items.append(item)
        self._by_iid[iid] = item
        self._update_scrollregion()
        self._schedule_redraw()
        return iid

    def delete(self, *iids) -> None:
        doomed = {str(i) for i in iids}
        self._items = [it for it in self._items if it["iid"] not in doomed]
        for i in doomed:
            self._by_iid.pop(i, None)
        self._sel = [i for i in self._sel if i not in doomed]
        if self._anchor_iid in doomed:
            self._anchor_iid = None
        self._update_scrollregion()
        self._schedule_redraw()

    def get_children(self, item="") -> tuple:
        return tuple(it["iid"] for it in self._items)

    def item(self, iid, option=None, **kw):
        it = self._by_iid[str(iid)]
        if kw:
            if "values" in kw:
                it["values"] = tuple(str(v) for v in kw["values"])
            if "tags" in kw:
                it["tags"] = tuple(kw["tags"])
            self._schedule_redraw()
            return None
        if option is not None:
            return it[option]
        return {"values": it["values"], "tags": it["tags"]}

    def set(self, iid, column=None, value=None):
        it = self._by_iid[str(iid)]
        if column is None:
            return dict(zip(self._columns, it["values"]))
        idx = self._columns.index(column)
        if value is None:
            return it["values"][idx]
        vals = list(it["values"])
        vals[idx] = str(value)
        it["values"] = tuple(vals)
        self._schedule_redraw()

    def heading(self, column, text=None, command=None):
        cfg = self._heading_cfg[column]
        if text is None and command is None:
            return dict(cfg)
        if text is not None:
            cfg["text"] = text
        if command is not None:
            cfg["command"] = command
        self._schedule_redraw()

    def column(self, column, width=None, anchor=None, stretch=None, minwidth=None):
        cfg = self._col_cfg[column]
        if width is None and anchor is None and stretch is None and minwidth is None:
            return dict(cfg)
        if width is not None:
            cfg["width"] = int(width)
        if anchor is not None:
            cfg["anchor"] = anchor
        if stretch is not None:
            cfg["stretch"] = bool(stretch)
        if minwidth is not None:
            cfg["minwidth"] = int(minwidth)
        self._schedule_redraw()

    def tag_configure(self, tag, background=None, foreground=None):
        cfg = self._tags.setdefault(tag, {})
        if background is not None:
            cfg["background"] = background
        if foreground is not None:
            cfg["foreground"] = foreground
        self._schedule_redraw()

    # --- selection --------------------------------------------------------

    def selection(self) -> tuple:
        return tuple(self._sel)

    def selection_set(self, *items) -> None:
        flat: list = []
        for entry in items:
            if isinstance(entry, (list, tuple)):
                flat.extend(entry)
            else:
                flat.append(entry)
        wanted = {str(i) for i in flat if str(i) in self._by_iid}
        self._sel = [it["iid"] for it in self._items if it["iid"] in wanted]
        self._anchor_iid = self._sel[0] if self._sel else None
        self._fire_select()
        self._schedule_redraw()

    def see(self, iid, animate: bool = False) -> None:
        """Scroll so the row is visible. With animate=True the list glides
        there (ease-out over ~0.2s) so the user can follow where it went."""
        self._cancel_scroll()
        iid = str(iid)
        index = next((n for n, it in enumerate(self._items) if it["iid"] == iid), None)
        if index is None:
            return
        total_h = max(1, len(self._items) * self._rowheight)
        top = self.body.canvasy(0)
        view_h = max(self._rowheight, self.body.winfo_height())
        y0, y1 = index * self._rowheight, (index + 1) * self._rowheight
        if y0 < top:
            target = y0 / total_h
        elif y1 > top + view_h:
            # Scrolling snaps to whole rows (yscrollincrement), rounding
            # down - round the new top row up so the target isn't clipped.
            rows_hidden = -(-(y1 - view_h) // self._rowheight)
            target = max(0.0, rows_hidden * self._rowheight / total_h)
        else:
            self._schedule_redraw()
            return
        if animate:
            self._animate_scroll(top / total_h, target, 1)
        else:
            self.body.yview_moveto(target)
            self._schedule_redraw()

    def _animate_scroll(self, start: float, target: float, step: int) -> None:
        t = step / SCROLL_STEPS
        eased = 1 - (1 - t) ** 3
        try:
            self.body.yview_moveto(start + (target - start) * eased)
            self._redraw()  # draw this frame now, not at idle
            if step < SCROLL_STEPS:
                self._scroll_job = self.after(
                    SCROLL_FRAME_MS, self._animate_scroll, start, target, step + 1
                )
            else:
                self._scroll_job = None
        except tk.TclError:
            self._scroll_job = None  # widget destroyed mid-animation

    def _cancel_scroll(self) -> None:
        if self._scroll_job is not None:
            try:
                self.after_cancel(self._scroll_job)
            except tk.TclError:
                pass
            self._scroll_job = None

    def identify_row(self, y) -> str:
        row = int(self.body.canvasy(y)) // self._rowheight
        if 0 <= row < len(self._items):
            return self._items[row]["iid"]
        return ""

    def _row_index_at(self, y) -> int | None:
        row = int(self.body.canvasy(y)) // self._rowheight
        return row if 0 <= row < len(self._items) else None

    def _on_click(self, event) -> None:
        self.body.focus_set()
        index = self._row_index_at(event.y)
        if index is None:
            return
        iid = self._items[index]["iid"]
        self._anchor_iid = iid
        if self._sel != [iid]:
            self._sel = [iid]
            self._fire_select()
            self._schedule_redraw()

    def _on_ctrl_click(self, event) -> None:
        self.body.focus_set()
        index = self._row_index_at(event.y)
        if index is None:
            return
        iid = self._items[index]["iid"]
        current = set(self._sel)
        current.symmetric_difference_update({iid})
        self._sel = [it["iid"] for it in self._items if it["iid"] in current]
        self._anchor_iid = iid
        self._fire_select()
        self._schedule_redraw()

    def _on_shift_click(self, event) -> None:
        self.body.focus_set()
        index = self._row_index_at(event.y)
        if index is None:
            return
        order = [it["iid"] for it in self._items]
        try:
            anchor_index = order.index(self._anchor_iid)
        except ValueError:
            anchor_index = index
        lo, hi = sorted((anchor_index, index))
        self._sel = order[lo:hi + 1]
        self._fire_select()
        self._schedule_redraw()

    def _move_selection(self, delta) -> str:
        if self._items:
            order = [it["iid"] for it in self._items]
            if self._sel:
                index = order.index(self._sel[-1]) + delta
            else:
                index = 0 if delta > 0 else len(order) - 1
            index = min(max(index, 0), len(order) - 1)
            iid = order[index]
            self._anchor_iid = iid
            self._sel = [iid]
            self.see(iid)
            self._fire_select()
            self._schedule_redraw()
        return "break"

    def _on_wheel(self, event) -> str:
        self._cancel_scroll()
        self.body.yview_scroll(-int(event.delta / 120), "units")
        self._schedule_redraw()
        return "break"

    # --- column resize on the header ---------------------------------------

    def _boundaries(self) -> list:
        """(x, column) for each draggable right edge, in canvas coords."""
        widths = self._effective_widths()
        bounds, x = [], 0
        for c in self._columns:
            x += widths[c]
            bounds.append((x, c))
        return bounds

    def _boundary_at(self, x) -> str | None:
        for bx, col in self._boundaries():
            if abs(x - bx) <= _GRAB:
                return col
        return None

    def _column_at(self, x) -> str | None:
        widths = self._effective_widths()
        pos = 0
        for c in self._columns:
            pos += widths[c]
            if x < pos:
                return c
        return None

    def _on_header_motion(self, event) -> None:
        cursor = "sb_h_double_arrow" if self._boundary_at(event.x) else ""
        if str(self.header.cget("cursor")) != cursor:
            self.header.configure(cursor=cursor)

    def _on_header_press(self, event) -> None:
        col = self._boundary_at(event.x)
        if col is not None:
            self._drag_col = col
            self._drag_start_x = event.x
            self._drag_start_w = self._effective_widths()[col]
            self._press_col = None
        else:
            self._press_col = self._column_at(event.x)

    def _on_header_drag(self, event) -> None:
        if self._drag_col is None:
            return
        cfg = self._col_cfg[self._drag_col]
        new_w = max(cfg["minwidth"], self._drag_start_w + event.x - self._drag_start_x)
        if new_w != cfg["width"]:
            # Manual resize wins from here on: freeze every column at its
            # current effective width so nothing snaps back via stretching.
            if not self._user_resized:
                current = self._effective_widths()
                for c in self._columns:
                    self._col_cfg[c]["width"] = current[c]
                self._user_resized = True
            cfg["width"] = new_w
            self._schedule_redraw()

    def _on_header_release(self, event) -> None:
        if self._drag_col is not None:
            self._drag_col = None
            return
        if self._press_col is not None and self._column_at(event.x) == self._press_col:
            command = self._heading_cfg[self._press_col]["command"]
            self._press_col = None
            if command is not None:
                command()

    # --- drawing ------------------------------------------------------------

    def _effective_widths(self) -> dict:
        widths = {c: max(cfg["width"], cfg["minwidth"])
                  for c, cfg in self._col_cfg.items()}
        if self._user_resized:
            return widths
        avail = self.body.winfo_width()
        extra = avail - sum(widths.values())
        stretchers = [c for c in self._columns if self._col_cfg[c]["stretch"]]
        if avail > 1 and extra > 0 and stretchers:
            share, leftover = divmod(extra, len(stretchers))
            for c in stretchers:
                widths[c] += share
            widths[stretchers[-1]] += leftover
        return widths

    def _update_scrollregion(self, total_w: int | None = None) -> None:
        if total_w is None:
            total_w = sum(self._effective_widths().values())
        height = max(1, len(self._items)) * self._rowheight
        try:
            self.body.configure(scrollregion=(0, 0, total_w, height))
        except tk.TclError:
            pass

    def _fit(self, text: str, width: int) -> str:
        """Truncate text to the column width (Treeview clips; we truncate)."""
        if width <= 0:
            return ""
        if self._font.measure(text) <= width:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._font.measure(text[:mid]) <= width:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo]

    def _schedule_redraw(self) -> None:
        if self._redraw_scheduled:
            return
        self._redraw_scheduled = True
        try:
            self.after_idle(self._redraw)
        except tk.TclError:
            self._redraw_scheduled = False

    def _redraw(self) -> None:
        self._redraw_scheduled = False
        try:
            widths = self._effective_widths()
            total_w = sum(widths.values())
            self._update_scrollregion(total_w)
            self._draw_header(widths, total_w)
            self._draw_body(widths, total_w)
        except tk.TclError:
            pass  # widget destroyed mid-schedule

    def _draw_header(self, widths, total_w) -> None:
        hdr = self.header
        hdr.delete("all")
        right = max(total_w, hdr.winfo_width())
        x = 0
        for c in self._columns:
            w = widths[c]
            anchor = self._col_cfg[c]["anchor"]
            text = self._fit(self._heading_cfg[c]["text"], w - 12)
            if anchor == "e":
                hdr.create_text(x + w - 6, HEADER_H / 2, anchor="e", text=text,
                                font=self._heading_font, fill=theme.INK_MUTED)
            else:
                hdr.create_text(x + 6, HEADER_H / 2, anchor="w", text=text,
                                font=self._heading_font, fill=theme.INK_MUTED)
            x += w
            hdr.create_line(x, 5, x, HEADER_H - 5, fill=theme.RULE)
        hdr.create_line(0, HEADER_H - 1, right, HEADER_H - 1, fill=theme.RULE)

    def _draw_body(self, widths, total_w) -> None:
        body = self.body
        body.delete("all")
        rowh = self._rowheight
        right = max(total_w, body.winfo_width())
        selected = set(self._sel)
        # Only the visible band of rows is drawn; every scroll path
        # (wheel, scrollbar->yview, see) schedules a redraw.
        first = max(0, int(body.canvasy(0)) // rowh)
        last = min(len(self._items),
                   first + max(1, body.winfo_height() // rowh) + 2)
        for index in range(first, last):
            item = self._items[index]
            y0 = index * rowh
            background = None
            foreground = theme.INK
            for tag in item["tags"]:
                cfg = self._tags.get(tag, {})
                if background is None and "background" in cfg:
                    background = cfg["background"]
                if "foreground" in cfg:
                    foreground = cfg["foreground"]
            if item["iid"] in selected:
                background, foreground = theme.SELECTION, theme.SELECTION_TEXT
            if background:
                body.create_rectangle(0, y0, right, y0 + rowh,
                                      fill=background, width=0)
            x = 0
            for n, c in enumerate(self._columns):
                w = widths[c]
                value = item["values"][n] if n < len(item["values"]) else ""
                if value:
                    text = self._fit(value, w - 12)
                    if self._col_cfg[c]["anchor"] == "e":
                        body.create_text(x + w - 6, y0 + rowh / 2, anchor="e",
                                         text=text, font=self._font, fill=foreground)
                    else:
                        body.create_text(x + 6, y0 + rowh / 2, anchor="w",
                                         text=text, font=self._font, fill=foreground)
                x += w
            # the faint rule that separates this row from the next
            body.create_line(0, y0 + rowh, right, y0 + rowh, fill=self._hairline)
