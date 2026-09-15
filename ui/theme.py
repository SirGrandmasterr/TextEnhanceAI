"""Visual theme: one palette and a set of ttk styles shared by every screen."""

import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

PALETTE = {
    "bg": "#f3f4f7",
    "surface": "#ffffff",
    "surface_alt": "#f8f9fb",
    "border": "#dfe3ea",
    "text": "#1f2937",
    "muted": "#6b7280",
    "faint": "#9ca3af",
    "accent": "#3b5bdb",
    "accent_dark": "#2f4ac7",
    "accent_soft": "#e7ebfb",
    "header": "#1e2a4a",
    "header_text": "#f8fafc",
    "header_muted": "#aab4d0",
    "success": "#15803d",
    "success_soft": "#dcfce7",
    "danger": "#b42318",
    "danger_soft": "#fee4e2",
    "warning": "#b54708",
    "warning_soft": "#fef0c7",
    "info": "#175cd3",
    "info_soft": "#dbeafe",
    "selection": "#eef2ff",
}

CHECK_COLORS = {
    "spelling": ("#6d28d9", "#ede9fe"),
    "grammar": ("#1d4ed8", "#dbeafe"),
    "expression": ("#0f766e", "#ccfbf1"),
}

STATUS_COLORS = {
    "queued": PALETTE["faint"],
    "running": PALETTE["info"],
    "error": PALETTE["danger"],
    "clean": PALETTE["success"],
    "ready": PALETTE["accent"],
    "reviewed": PALETTE["success"],
}

_FAMILY = None


def font_family():
    """Return the UI font family available on this platform."""
    global _FAMILY
    if _FAMILY is None:
        preferred = ["Segoe UI", "SF Pro Text", "Helvetica Neue", "Noto Sans", "DejaVu Sans", "Arial"]
        try:
            available = set(tkfont.families())
        except tk.TclError:
            available = set()
        _FAMILY = next((name for name in preferred if name in available), "TkDefaultFont")
    return _FAMILY


def font(size=10, weight="normal", slant="roman"):
    """Return a font tuple in the theme family."""
    return (font_family(), size, weight if slant == "roman" else "{0} {1}".format(weight, slant))


def apply_theme(root):
    """Configure ttk styles and default widget colours on ``root``."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    family = font_family()
    base = (family, 10)
    root.configure(background=PALETTE["bg"])
    root.option_add("*TCombobox*Listbox.font", base)
    root.option_add("*TCombobox*Listbox.selectBackground", PALETTE["accent"])

    style.configure(".", background=PALETTE["bg"], foreground=PALETTE["text"], font=base,
                    bordercolor=PALETTE["border"], focuscolor=PALETTE["accent"])
    style.configure("TFrame", background=PALETTE["bg"])
    style.configure("Surface.TFrame", background=PALETTE["surface"])
    style.configure("Card.TFrame", background=PALETTE["surface"], relief="solid", borderwidth=1,
                    bordercolor=PALETTE["border"])
    style.configure("Selected.Card.TFrame", background=PALETTE["selection"], bordercolor=PALETTE["accent"])
    style.configure("Selected.TFrame", background=PALETTE["selection"])
    style.configure("Header.TFrame", background=PALETTE["header"])
    style.configure("Toolbar.TFrame", background=PALETTE["surface"])

    style.configure("TLabel", background=PALETTE["bg"], foreground=PALETTE["text"])
    style.configure("Surface.TLabel", background=PALETTE["surface"])
    style.configure("Selected.TLabel", background=PALETTE["selection"])
    style.configure("Title.TLabel", background=PALETTE["bg"], font=(family, 15, "bold"))
    style.configure("CardTitle.TLabel", background=PALETTE["surface"], font=(family, 11, "bold"))
    style.configure("Muted.TLabel", background=PALETTE["bg"], foreground=PALETTE["muted"])
    style.configure("SurfaceMuted.TLabel", background=PALETTE["surface"], foreground=PALETTE["muted"], font=(family, 9))
    style.configure("SelectedMuted.TLabel", background=PALETTE["selection"], foreground=PALETTE["muted"], font=(family, 9))
    style.configure("Toolbar.TLabel", background=PALETTE["surface"], foreground=PALETTE["muted"])
    style.configure("Header.TLabel", background=PALETTE["header"], foreground=PALETTE["header_text"],
                    font=(family, 13, "bold"))
    style.configure("HeaderMuted.TLabel", background=PALETTE["header"], foreground=PALETTE["header_muted"],
                    font=(family, 9))
    style.configure("Status.TLabel", background=PALETTE["bg"], foreground=PALETTE["muted"])
    for state, color in STATUS_COLORS.items():
        style.configure("{0}.Status.TLabel".format(state.title()), background=PALETTE["bg"],
                        foreground=color, font=(family, 9, "bold"))
    for check, (fg, bg) in CHECK_COLORS.items():
        style.configure("{0}.Badge.TLabel".format(check.title()), background=bg, foreground=fg,
                        font=(family, 9, "bold"), padding=(6, 1))
    for name, fg, bg in (
        ("Applied", PALETTE["success"], PALETTE["success_soft"]),
        ("Superseded", PALETTE["warning"], PALETTE["warning_soft"]),
        ("Rejected", PALETTE["danger"], PALETTE["danger_soft"]),
        ("Pending", PALETTE["info"], PALETTE["info_soft"]),
    ):
        style.configure("{0}.State.TLabel".format(name), background=bg, foreground=fg,
                        font=(family, 9, "bold"), padding=(6, 1))
    # Amber badge for changes the hallucination guard flagged.
    style.configure("Flag.Badge.TLabel", background=PALETTE["warning_soft"], foreground=PALETTE["warning"],
                    font=(family, 9, "bold"), padding=(6, 1))

    style.configure("TButton", background=PALETTE["surface"], foreground=PALETTE["text"], padding=(10, 5),
                    borderwidth=1, bordercolor=PALETTE["border"], relief="flat")
    style.map("TButton",
              background=[("disabled", PALETTE["surface_alt"]), ("pressed", PALETTE["border"]),
                          ("active", PALETTE["surface_alt"])],
              foreground=[("disabled", PALETTE["faint"])],
              bordercolor=[("active", PALETTE["accent"])])
    style.configure("Accent.TButton", background=PALETTE["accent"], foreground="#ffffff",
                    bordercolor=PALETTE["accent"], font=(family, 10, "bold"))
    style.map("Accent.TButton",
              background=[("disabled", PALETTE["accent_soft"]), ("pressed", PALETTE["accent_dark"]),
                          ("active", PALETTE["accent_dark"])],
              foreground=[("disabled", PALETTE["muted"])])
    style.configure("Primary.TButton", background=PALETTE["accent"], foreground="#ffffff",
                    bordercolor=PALETTE["accent"], font=(family, 10, "bold"))
    style.map("Primary.TButton",
              background=[("disabled", PALETTE["accent_soft"]), ("pressed", PALETTE["accent_dark"]),
                          ("active", PALETTE["accent_dark"])],
              foreground=[("disabled", PALETTE["muted"])])
    style.configure("Success.TButton", foreground=PALETTE["success"], bordercolor=PALETTE["success"])
    style.map("Success.TButton", background=[("active", PALETTE["success_soft"])])
    style.configure("Danger.TButton", foreground=PALETTE["danger"], bordercolor=PALETTE["danger"])
    style.map("Danger.TButton", background=[("active", PALETTE["danger_soft"])])
    style.configure("Ghost.TButton", background=PALETTE["bg"], bordercolor=PALETTE["bg"], foreground=PALETTE["muted"])
    style.map("Ghost.TButton", background=[("active", PALETTE["surface"])], foreground=[("active", PALETTE["text"])])
    style.configure("Nav.TButton", background=PALETTE["header"], foreground=PALETTE["header_muted"],
                    bordercolor=PALETTE["header"], padding=(12, 5))
    style.map("Nav.TButton", background=[("active", "#2a3860")], foreground=[("active", PALETTE["header_text"])])
    style.configure("NavActive.TButton", background="#3a4a78", foreground=PALETTE["header_text"],
                    bordercolor="#3a4a78", padding=(12, 5), font=(family, 10, "bold"))
    style.map("NavActive.TButton", background=[("active", "#3a4a78")])
    style.configure("Small.TButton", padding=(6, 2), font=(family, 9))
    style.configure("Small.Success.TButton", padding=(6, 2), font=(family, 9), foreground=PALETTE["success"],
                    bordercolor=PALETTE["success"])
    style.configure("Small.Danger.TButton", padding=(6, 2), font=(family, 9), foreground=PALETTE["danger"],
                    bordercolor=PALETTE["danger"])

    _borrow_native_indicators(style)
    style.configure("TCheckbutton", background=PALETTE["bg"])
    style.configure("Surface.TCheckbutton", background=PALETTE["surface"])
    style.configure("Toolbar.TCheckbutton", background=PALETTE["surface"])
    style.configure("TRadiobutton", background=PALETTE["bg"])
    style.configure("Surface.TRadiobutton", background=PALETTE["surface"])
    style.configure("TLabelframe", background=PALETTE["bg"], bordercolor=PALETTE["border"], relief="solid")
    style.configure("TLabelframe.Label", background=PALETTE["bg"], foreground=PALETTE["muted"], font=(family, 9, "bold"))
    style.configure("TEntry", fieldbackground=PALETTE["surface"], bordercolor=PALETTE["border"], padding=4)
    style.configure("TSpinbox", fieldbackground=PALETTE["surface"], bordercolor=PALETTE["border"], arrowsize=12, padding=3)
    style.configure("TCombobox", fieldbackground=PALETTE["surface"], bordercolor=PALETTE["border"], padding=3,
                    arrowsize=14)
    style.map("TCombobox", fieldbackground=[("readonly", PALETTE["surface"])],
              selectbackground=[("readonly", PALETTE["surface"])],
              selectforeground=[("readonly", PALETTE["text"])])
    style.configure("Horizontal.TProgressbar", troughcolor=PALETTE["border"], background=PALETTE["accent"],
                    bordercolor=PALETTE["border"], lightcolor=PALETTE["accent"], darkcolor=PALETTE["accent"])
    style.configure("Treeview", background=PALETTE["surface"], fieldbackground=PALETTE["surface"],
                    foreground=PALETTE["text"], rowheight=26, bordercolor=PALETTE["border"])
    style.map("Treeview", background=[("selected", PALETTE["accent_soft"])],
              foreground=[("selected", PALETTE["text"])])
    style.configure("Treeview.Heading", background=PALETTE["surface_alt"], foreground=PALETTE["muted"],
                    font=(family, 9, "bold"), relief="flat")
    style.configure("TPanedwindow", background=PALETTE["bg"])
    style.configure("Sash", sashthickness=6, gripcount=0, background=PALETTE["bg"])
    style.configure("TScrollbar", background=PALETTE["surface_alt"], troughcolor=PALETTE["bg"],
                    bordercolor=PALETTE["bg"], arrowsize=12)
    style.configure("TNotebook", background=PALETTE["bg"], bordercolor=PALETTE["border"])
    style.configure("TNotebook.Tab", padding=(12, 5), background=PALETTE["surface_alt"])
    style.map("TNotebook.Tab", background=[("selected", PALETTE["surface"])])
    return style


def _borrow_native_indicators(style):
    """Use the platform's check/radio indicators instead of clam's boxes."""
    if sys.platform != "win32":
        return
    try:
        style.element_create("Native.Checkbutton.indicator", "from", "vista", "Checkbutton.indicator")
        style.element_create("Native.Radiobutton.indicator", "from", "vista", "Radiobutton.indicator")
    except tk.TclError:
        return
    style.layout("TCheckbutton", [
        ("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Native.Checkbutton.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})]}),
        ]}),
    ])
    style.layout("TRadiobutton", [
        ("Radiobutton.padding", {"sticky": "nswe", "children": [
            ("Native.Radiobutton.indicator", {"side": "left", "sticky": ""}),
            ("Radiobutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Radiobutton.label", {"sticky": "nswe"})]}),
        ]}),
    ])


class Tooltip:
    """Show ``text`` in a small window while the pointer rests on ``widget``."""

    def __init__(self, widget, text, delay=450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after = None
        self._window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, event=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _show(self):
        self._after = None
        if self._window is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            self._window = tk.Toplevel(self.widget)
            self._window.wm_overrideredirect(True)
            self._window.wm_geometry("+{0}+{1}".format(x, y))
            tk.Label(
                self._window, text=self.text, justify=tk.LEFT, wraplength=320,
                background=PALETTE["text"], foreground=PALETTE["surface"],
                font=(font_family(), 9), padx=8, pady=5,
            ).pack()
        except tk.TclError:
            self._window = None

    def _hide(self, event=None):
        self._cancel()
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


def style_text(widget, size=11, background=None, readonly=False):
    """Apply theme colours to a tk.Text widget."""
    widget.configure(
        font=(font_family(), size),
        background=background or PALETTE["surface"],
        foreground=PALETTE["text"],
        insertbackground=PALETTE["accent"],
        selectbackground=PALETTE["accent_soft"],
        selectforeground=PALETTE["text"],
        relief=tk.FLAT,
        highlightthickness=1,
        highlightbackground=PALETTE["border"],
        highlightcolor=PALETTE["accent"],
        padx=10,
        pady=8,
        spacing1=1,
        spacing3=3,
        wrap=tk.WORD,
    )
    if readonly:
        widget.configure(state=tk.DISABLED, cursor="arrow")
        if sys.platform == "win32":
            widget.configure(takefocus=0)
    return widget


class ScrollableFrame(ttk.Frame):
    """A vertically scrollable container (Canvas + inner Frame)."""

    def __init__(self, parent, background=None, **kwargs):
        super().__init__(parent, **kwargs)
        color = background or PALETTE["bg"]
        self.canvas = tk.Canvas(self, background=color, highlightthickness=0, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, style="TFrame")
        self.inner.configure(style="Surface.TFrame" if color == PALETTE["surface"] else "TFrame")
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", lambda event: self._bind_wheel())
        self.canvas.bind("<Leave>", lambda event: self._unbind_wheel())

    def _on_inner_configure(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", self._on_wheel)
        self.canvas.bind_all("<Button-5>", self._on_wheel)

    def _unbind_wheel(self):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_wheel(self, event):
        if getattr(event, "num", None) == 4:
            self.canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            self.canvas.yview_scroll(1, "units")
        else:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def scroll_to_widget(self, widget):
        """Scroll so that ``widget`` (a child of ``inner``) is visible."""
        try:
            if not widget.winfo_exists():
                return
            self.update_idletasks()
        except tk.TclError:
            return
        total = max(1, self.inner.winfo_height())
        top = widget.winfo_y()
        bottom = top + widget.winfo_height()
        view_top = self.canvas.canvasy(0)
        view_bottom = view_top + self.canvas.winfo_height()
        if top < view_top:
            self.canvas.yview_moveto(top / total)
        elif bottom > view_bottom:
            self.canvas.yview_moveto(max(0, bottom - self.canvas.winfo_height()) / total)

    def clear(self):
        for child in self.inner.winfo_children():
            child.destroy()
        self.canvas.yview_moveto(0)
