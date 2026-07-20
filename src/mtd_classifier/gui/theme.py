"""Visual theme v2: one typeface, five pastels, motion only on interaction.

Design tokens (colourful pastel, warm ground)
---------------------------------------------
Paper    #FBFAF6  window and field background (warm ivory)
Raised   #F2EEE5  toolbar / header strips / zebra stripes
Ink      #37342B  primary text (warm near-black)
Coral    #E9A98C  the primary accent, pastel terracotta
Crail    #C96442  accent deep - pressed, focus, selected-tab text, money out
Peach    #F5D3BE  selection fill and a tinted-action hue
Butter   #F5E7C8  needs-attention (uncategorised)
Sage     #D9EBDD  money in / resolved / verified
Sky      #D8E7F2  informational
Lilac    #E6DFF2  the fourth category hue
Brick    #C05B4A  warnings only
Rule     #E6E0D2  hairline borders

Colour is structural: a gradient band heads the window and dialogs, each
notebook tab owns a hue (dot + gliding indicator that adopts it), toolbar
actions are tinted by role, and money is coloured by direction - sage in,
terracotta out.

Type: one family everywhere - Bahnschrift (DIN-derived, ships with
Windows), Light for display, Regular for reading, SemiBold for labels;
Segoe UI stands in where Bahnschrift is missing. Resolve fonts through
``fnt``/``fnt_semibold``/``fnt_light`` so every widget speaks with the
same voice.

Motion: event-driven only, never idle. Launch = window fade + one accent
sweep, then still. Each interaction gets one light transition: dialog
fades, the tab indicator glide, status settle. Durations stay under ~250ms.
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk, font as tkfont
from typing import Callable, Sequence

PAPER = "#FBFAF6"
PAPER_RAISED = "#F2EEE5"
INK = "#37342B"
INK_MUTED = "#8B8477"
CORAL = "#E9A98C"
CORAL_DEEP = "#C96442"
CORAL_MIST = "#F9E4D6"
PEACH = "#F5D3BE"
BUTTER = "#F5E7C8"
BUTTER_INK = "#9A7B2F"
SAGE = "#D9EBDD"
SAGE_INK = "#4E8266"
SKY = "#D8E7F2"
SKY_INK = "#4A7396"
LILAC = "#E6DFF2"
LILAC_INK = "#75679E"
BRICK = "#C05B4A"
RULE = "#E6E0D2"
SELECTION = PEACH
SELECTION_TEXT = INK

# The four-hue system used by tabs and the startup sweep
TAB_HUES = (CORAL_DEEP, SAGE_INK, SKY_INK, LILAC_INK)
BAND_COLORS = (CORAL_MIST, BUTTER, SAGE, SKY)

# Compatibility aliases (older widgets reference the ledger-era names)
GREEN = CORAL_DEEP
GREEN_DARK = "#B36248"
GREEN_SOFT = CORAL
STRAW = BUTTER
MINT = SAGE

# Tour spotlight pulse
SPOTLIGHT_COLORS = (CORAL_DEEP, CORAL)

# Font families, resolved by apply_theme (Bahnschrift with Segoe fallback)
_UI = "Segoe UI"
_UI_SEMIBOLD = "Segoe UI Semibold"
_UI_LIGHT = "Segoe UI Light"


def fnt(size: int = 10) -> tuple:
    """Body text in the app's single family."""
    return (_UI, size)


def fnt_semibold(size: int = 10) -> tuple:
    """Label/emphasis weight of the same family."""
    return (_UI_SEMIBOLD, size)


def fnt_light(size: int = 16) -> tuple:
    """Display weight of the same family, for wordmarks and big titles."""
    return (_UI_LIGHT, size)


def _resolve_fonts(root: tk.Misc) -> None:
    global _UI, _UI_SEMIBOLD, _UI_LIGHT
    families = set(tkfont.families(root))
    if "Bahnschrift" in families:
        _UI = "Bahnschrift"
        _UI_SEMIBOLD = ("Bahnschrift SemiBold" if "Bahnschrift SemiBold" in families
                        else "Bahnschrift")
        _UI_LIGHT = ("Bahnschrift Light" if "Bahnschrift Light" in families
                     else "Bahnschrift")
    else:  # keep the Segoe trio
        _UI, _UI_SEMIBOLD, _UI_LIGHT = (
            "Segoe UI", "Segoe UI Semibold", "Segoe UI Light")


def _rounded_photo(root: tk.Misc, fill: str, outline: str | None = None,
                   radius: int = 10, size: int = 32,
                   corners: tuple = (True, True, True, True)):
    """A smooth rounded-rectangle image for ttk elements, anti-aliased by
    supersampling. Transparent corners let the parent surface show through."""
    from PIL import Image, ImageDraw, ImageTk

    scale = 4
    img = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [0, 0, size * scale - 1, size * scale - 1],
        radius=radius * scale, fill=fill,
        outline=outline or fill, width=scale, corners=corners)
    img = img.resize((size, size), Image.LANCZOS)
    return ImageTk.PhotoImage(img, master=root)


def _install_rounded_elements(root: tk.Tk, style: ttk.Style) -> None:
    """Replace the square clam surfaces with rounded ones: every button
    style, the notebook tabs, and combobox/entry fields.

    Sizes are DPI-aware: the 9-patch source images are raw pixels, so on a
    scaled display (125%/150%) an unscaled radius shrinks visually and the
    curve reads as square. Everything here multiplies by the real scaling.
    """
    keep = root._theme_images = []
    dpi = max(1.0, root.winfo_fpixels("1i") / 96.0)
    # Buttons and tabs are compact: an image element's natural size is a
    # MINIMUM for the widget, so the source image height caps how short a
    # button can get. ~20px (pre-DPI) leaves just enough for the text plus
    # a whisker of padding; the radius shrinks with it to keep the same
    # curvature. Fields keep the original taller surface.
    r_btn = round(6 * dpi)
    size = round(38 * dpi)
    size_btn = round(20 * dpi)

    def register(name: str, layout_builder) -> None:
        try:
            layout_builder()
        except tk.TclError:
            pass  # element already exists on this interpreter

    # Buttons - one rounded surface per role, radius as in the design
    button_fills = {
        "TButton": (PAPER_RAISED, CORAL_MIST, PEACH, RULE),
        "Accent.TButton": (CORAL, "#DE9573", CORAL_DEEP, CORAL),
        "Peach.TButton": (PEACH, PEACH, CORAL_DEEP, PEACH),
        "Sage.TButton": (SAGE, SAGE, SAGE_INK, SAGE),
        "Sky.TButton": (SKY, SKY, SKY_INK, SKY),
        "Lilac.TButton": (LILAC, LILAC, LILAC_INK, LILAC),
    }
    for style_name, (normal, active, pressed, border) in button_fills.items():
        imgs = [_rounded_photo(root, normal, border, radius=r_btn, size=size_btn),
                _rounded_photo(root, active, border, radius=r_btn, size=size_btn),
                _rounded_photo(root, pressed, pressed, radius=r_btn, size=size_btn)]
        keep.extend(imgs)
        element = style_name.replace(".", "_") + "_surface"

        def build(element=element, imgs=imgs, style_name=style_name):
            style.element_create(
                element, "image", imgs[0],
                ("pressed", imgs[2]), ("active", imgs[1]),
                border=r_btn + 2, sticky="nsew")
            style.layout(style_name, [
                (element, {"sticky": "nswe", "children": [
                    ("Button.padding", {"sticky": "nswe", "children": [
                        ("Button.label", {"sticky": "nswe"})]})]})])
            # ttk erases the widget rectangle with the style background
            # BEFORE drawing elements - the image's transparent corners
            # reveal it. It must be the surrounding paper, never the button
            # colour, or the corners read as a solid square.
            style.configure(style_name, background=PAPER)
            style.map(style_name, background=[])

        register(element, build)

    # Notebook tabs - rounded top corners only, square where they meet the pane
    r_tab = round(5 * dpi)
    tab_imgs = [
        _rounded_photo(root, PAPER_RAISED, RULE, radius=r_tab, size=size_btn,
                       corners=(True, True, False, False)),
        _rounded_photo(root, PAPER, RULE, radius=r_tab, size=size_btn,
                       corners=(True, True, False, False)),
        _rounded_photo(root, CORAL_MIST, RULE, radius=r_tab, size=size_btn,
                       corners=(True, True, False, False)),
    ]
    keep.extend(tab_imgs)

    def build_tab():
        style.element_create(
            "Rounded_tab", "image", tab_imgs[0],
            ("selected", tab_imgs[1]), ("active", tab_imgs[2]),
            border=(r_tab + 1, r_tab + 1, r_tab + 1, 2), sticky="nsew")
        style.layout("TNotebook.Tab", [
            ("Rounded_tab", {"sticky": "nswe", "children": [
                ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
                    ("Notebook.label", {"side": "top", "sticky": ""})]})]})])
        # Same erase-colour rule as buttons: corners must reveal paper
        style.configure("TNotebook.Tab", background=PAPER)
        style.map("TNotebook.Tab",
                  background=[],
                  foreground=[("selected", CORAL_DEEP)])

    register("Rounded_tab", build_tab)

    # Combobox and entry fields - soft corners on the white wells
    r_field = round(9 * dpi)
    field_imgs = [_rounded_photo(root, "#FFFFFF", RULE, radius=r_field, size=size),
                  _rounded_photo(root, "#FFFFFF", CORAL_DEEP, radius=r_field, size=size)]
    keep.extend(field_imgs)

    def build_fields():
        style.element_create(
            "Rounded_field", "image", field_imgs[0],
            ("focus", field_imgs[1]), border=r_field + 1, sticky="nsew")
        style.layout("TCombobox", [
            ("Rounded_field", {"sticky": "nswe", "children": [
                ("Combobox.padding", {"expand": "1", "sticky": "nswe", "children": [
                    ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
                    ("Combobox.textarea", {"sticky": "nswe"})]})]})])
        style.layout("TEntry", [
            ("Rounded_field", {"sticky": "nswe", "children": [
                ("Entry.padding", {"sticky": "nswe", "children": [
                    ("Entry.textarea", {"sticky": "nswe"})]})]})])

    register("Rounded_field", build_fields)


def apply_theme(root: tk.Tk) -> ttk.Style:
    """Apply the pastel theme to every ttk widget class."""
    root.configure(bg=PAPER)
    _resolve_fonts(root)
    # The ledger uses the same family - tabular DIN figures align cleanly
    root.ledger_font = fnt(10)
    root.ledger_font_small = fnt(9)

    style = ttk.Style(root)
    style.theme_use("clam")

    ui = fnt(10)
    ui_semibold = fnt_semibold(10)

    style.configure(".", background=PAPER, foreground=INK, font=ui,
                    bordercolor=RULE, lightcolor=PAPER, darkcolor=PAPER,
                    focuscolor=CORAL_DEEP, selectbackground=SELECTION,
                    selectforeground=SELECTION_TEXT)

    style.configure("TFrame", background=PAPER)
    style.configure("Toolbar.TFrame", background=PAPER_RAISED)
    style.configure("TLabel", background=PAPER, foreground=INK)
    style.configure("Muted.TLabel", foreground=INK_MUTED)
    style.configure("Status.TLabel", background=PAPER_RAISED, foreground=INK_MUTED,
                    padding=(10, 4), font=fnt(9))

    # Quiet buttons by default; tinted role styles below carry the colour.
    # Vertical padding is minimal - the text still clears the rounded
    # surface, but the buttons hug their labels instead of towering.
    style.configure("TButton", padding=(12, 1), relief="flat",
                    background=PAPER_RAISED, borderwidth=1)
    style.map("TButton",
              background=[("pressed", PEACH), ("active", CORAL_MIST)],
              bordercolor=[("focus", CORAL_DEEP)])
    style.configure("Accent.TButton", background=CORAL, foreground="#3A2A20",
                    bordercolor=CORAL, font=ui_semibold)
    style.map("Accent.TButton",
              background=[("pressed", CORAL_DEEP), ("active", "#DE9573")],
              foreground=[("pressed", "#FFF8F3"), ("disabled", INK_MUTED)])
    for name, fill, ink in (("Peach", PEACH, CORAL_DEEP),
                            ("Sage", SAGE, SAGE_INK),
                            ("Sky", SKY, SKY_INK),
                            ("Lilac", LILAC, LILAC_INK)):
        style.configure(f"{name}.TButton", background=fill, foreground=ink,
                        bordercolor=fill, font=ui_semibold)
        style.map(f"{name}.TButton",
                  background=[("pressed", ink), ("active", fill)],
                  foreground=[("pressed", PAPER)],
                  bordercolor=[("focus", ink)])

    style.configure("TNotebook", background=PAPER, borderwidth=0, tabmargins=(8, 4, 8, 0))
    style.configure("TNotebook.Tab", padding=(14, 1), background=PAPER_RAISED,
                    borderwidth=0, font=ui)
    style.map("TNotebook.Tab",
              background=[("selected", PAPER), ("active", CORAL_MIST)],
              foreground=[("selected", CORAL_DEEP)],
              expand=[("selected", (0, 2, 0, 0))])

    # The ledger itself
    style.configure("Treeview", background=PAPER, fieldbackground=PAPER,
                    foreground=INK, rowheight=26, borderwidth=0,
                    font=root.ledger_font)
    style.configure("Treeview.Heading", background=PAPER_RAISED, foreground=INK_MUTED,
                    font=fnt_semibold(9), relief="flat", padding=(6, 5))
    style.map("Treeview.Heading", background=[("active", CORAL_MIST)])
    style.map("Treeview",
              background=[("selected", SELECTION)],
              foreground=[("selected", SELECTION_TEXT)])

    style.configure("Summary.Treeview", font=ui)

    style.configure("TCombobox", fieldbackground="#FFFFFF", padding=4)
    style.map("TCombobox", bordercolor=[("focus", CORAL_DEEP)])
    style.configure("TCheckbutton", background=PAPER)
    style.configure("TLabelframe", background=PAPER, bordercolor=RULE,
                    borderwidth=1, relief="solid")
    style.configure("TLabelframe.Label", background=PAPER, foreground=INK_MUTED,
                    font=fnt_semibold(9))
    style.configure("TSeparator", background=RULE)
    style.configure("Vertical.TScrollbar", background=PAPER_RAISED,
                    troughcolor=PAPER, borderwidth=0, arrowsize=12)
    style.configure("Horizontal.TScrollbar", background=PAPER_RAISED,
                    troughcolor=PAPER, borderwidth=0, arrowsize=12)

    try:
        _install_rounded_elements(root, style)
    except Exception:
        pass  # rounded surfaces are an enhancement; square clam still works

    return style


def _hex_to_rgb(color: str) -> tuple:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def _blend(a: str, b: str, f: float) -> str:
    ra, ga, ba = _hex_to_rgb(a)
    rb, gb, bb = _hex_to_rgb(b)
    return "#%02x%02x%02x" % (
        int(ra + (rb - ra) * f), int(ga + (gb - ga) * f), int(ba + (bb - ba) * f))


def gradient_band(parent: tk.Misc, height: int = 36,
                  colors: Sequence[str] = BAND_COLORS,
                  left_text: str | None = None,
                  right_text: str | None = None) -> tk.Canvas:
    """A horizontal pastel gradient band (the window/dialog header).

    Redraws itself on resize; optional wordmark on the left and detail text
    on the right, set in the app family.
    """
    band = tk.Canvas(parent, height=height, highlightthickness=0, bg=colors[0])

    def draw(event=None) -> None:
        try:
            band.delete("all")
            w = max(band.winfo_width(), 2)
            segments = len(colors) - 1
            for x in range(0, w, 2):
                pos = x / w * segments
                i = min(int(pos), segments - 1)
                band.create_rectangle(
                    x, 0, x + 2, height, width=0,
                    fill=_blend(colors[i], colors[i + 1], pos - i))
            if left_text:
                band.create_text(18, height // 2, anchor="w", text=left_text,
                                 font=fnt_light(14), fill=INK)
            if right_text:
                band.create_text(w - 16, height // 2, anchor="e", text=right_text,
                                 font=fnt_semibold(9), fill=INK)
            band.create_rectangle(0, height - 1, w, height, width=0, fill=RULE)
        except tk.TclError:
            pass

    band.bind("<Configure>", draw)
    return band


_dot_cache: dict = {}


def tab_dot(root: tk.Misc, color: str) -> tk.PhotoImage:
    """A small round hue dot used as a notebook tab's identity marker."""
    key = (str(root), color)
    if key in _dot_cache:
        return _dot_cache[key]
    size = 9
    img = tk.PhotoImage(master=root, width=size, height=size)
    radius = size / 2 - 0.5
    for y in range(size):
        for x in range(size):
            if (x - size / 2 + 0.5) ** 2 + (y - size / 2 + 0.5) ** 2 <= radius ** 2:
                img.put(color, (x, y))
            else:
                img.transparency_set(x, y, True)
    _dot_cache[key] = img
    return img


def style_listbox(listbox: tk.Listbox) -> None:
    """Bring a plain tk.Listbox into the design: paper ground, peach
    selection with ink text (never the OS blue), no harsh borders."""
    listbox.configure(
        bg=PAPER, fg=INK, relief="flat", borderwidth=0,
        highlightthickness=1, highlightbackground=RULE, highlightcolor=CORAL_DEEP,
        selectbackground=PEACH, selectforeground=INK,
        activestyle="none", font=fnt(11),
    )


def fade_in(window: tk.Toplevel, duration_ms: int = 150) -> None:
    """Fade a window in from transparent. Fails silently where unsupported."""
    steps = 8
    try:
        window.attributes("-alpha", 0.0)
    except tk.TclError:
        return

    def step(i: int = 1) -> None:
        try:
            window.attributes("-alpha", min(1.0, i / steps))
        except tk.TclError:
            return
        if i < steps:
            window.after(duration_ms // steps, step, i + 1)

    step()


def close_window(window: tk.Misc, duration_ms: int = 120) -> None:
    """Fade a window out, then destroy it.

    Runs synchronously (bounded, ~duration_ms) so it works both inside a
    running mainloop and in tests that drive widgets without one.
    """
    steps = 6
    try:
        for i in range(steps):
            window.attributes("-alpha", 1.0 - (i + 1) / steps)
            window.update()
            time.sleep(duration_ms / steps / 1000)
    except tk.TclError:
        pass  # already gone, or platform without alpha
    try:
        window.destroy()
    except tk.TclError:
        pass


def startup_sweep(parent: tk.Misc, width_provider: Callable[[], int],
                  y: int = 0, duration_ms: int = 450) -> tk.Canvas:
    """One-time accent sweep: a four-hue hairline draws itself across the
    window once on startup, then remains as a static rule. Returns the
    canvas so the caller can pack/place it."""
    line = tk.Canvas(parent, height=3, bg=PAPER, highlightthickness=0)
    frames = max(1, duration_ms // 16)
    hues = (CORAL_DEEP, CORAL, SAGE_INK, SKY_INK)

    def step(i: int = 1) -> None:
        try:
            w = max(1, width_provider())
            f = 1 - (1 - i / frames) ** 3  # ease-out
            line.delete("sweep")
            reach = int(w * f)
            seg = max(1, reach // len(hues))
            for k, hue in enumerate(hues):
                x0 = k * seg
                x1 = reach if k == len(hues) - 1 else (k + 1) * seg
                if x0 < reach:
                    line.create_rectangle(x0, 0, x1, 4, fill=hue, outline=hue,
                                          tags="sweep")
            if i < frames:
                line.after(16, step, i + 1)
        except tk.TclError:
            pass

    line.after(60, step)
    return line


def attach_tab_indicator(notebook: ttk.Notebook,
                         colors: Sequence[str] = ()) -> None:
    """A hue underline that glides beneath the selected notebook tab on
    every tab change, adopting that tab's colour. Static between
    interactions. ``colors`` maps tab index -> hue (cycled if short)."""
    indicator = tk.Frame(notebook, height=3, bg=CORAL_DEEP)
    state = {"x": 0, "w": 0, "job": None}

    def hue_for(index: int) -> str:
        if not colors:
            return CORAL_DEEP
        return colors[index % len(colors)]

    def tab_geometry() -> "tuple[int, int, int, int] | None":
        try:
            index = notebook.index(notebook.select())
            x, y, w, h = notebook.bbox(index)
            return index, x, y + h - 2, w
        except (tk.TclError, TypeError, ValueError):
            return None

    def place_at(x: int, y: int, w: int) -> None:
        indicator.place(in_=notebook, x=x, y=y, width=max(1, w), height=3)
        state["x"], state["w"] = x, w

    def glide(event=None, duration_ms: int = 160) -> None:
        geo = tab_geometry()
        if geo is None:
            return
        index, tx, ty, tw = geo
        indicator.configure(bg=hue_for(index))
        if state["w"] == 0:  # first placement: no animation
            place_at(tx, ty, tw)
            return
        if state["job"] is not None:
            try:
                notebook.after_cancel(state["job"])
            except (tk.TclError, ValueError):
                pass
            state["job"] = None
        x0, w0 = state["x"], state["w"]
        frames = max(1, duration_ms // 16)

        def step(i: int = 1) -> None:
            state["job"] = None
            f = 1 - (1 - i / frames) ** 3  # ease-out
            place_at(int(x0 + (tx - x0) * f), ty, int(w0 + (tw - w0) * f))
            if i < frames:
                try:
                    state["job"] = notebook.after(16, step, i + 1)
                except tk.TclError:
                    pass

        step()

    notebook.bind("<<NotebookTabChanged>>", glide, add="+")
    notebook.after_idle(glide)


def settle_status(label_widget: ttk.Label, root: tk.Misc) -> None:
    """Flash a status message in the accent, then settle to muted ink."""
    try:
        label_widget.configure(foreground=CORAL_DEEP)
        root.after(1800, lambda: _safe_fg(label_widget, INK_MUTED))
    except tk.TclError:
        pass


def _safe_fg(widget: ttk.Label, color: str) -> None:
    try:
        widget.configure(foreground=color)
    except tk.TclError:
        pass


def animate_totals(
    root: tk.Misc,
    render: Callable[[float, float, float], None],
    income: float,
    expenses: float,
    net: float,
    duration_ms: int = 220,
) -> None:
    """Count the summary totals up to their values, like a calculator settling.

    ``render`` receives interpolated (income, expenses, net) each frame.
    """
    frames = max(1, duration_ms // 16)

    def ease(t: float) -> float:
        return 1 - (1 - t) ** 3  # ease-out cubic

    def step(i: int = 1) -> None:
        f = ease(i / frames)
        render(income * f, expenses * f, net * f)
        if i < frames:
            try:
                root.after(16, step, i + 1)
            except tk.TclError:
                pass

    step()
