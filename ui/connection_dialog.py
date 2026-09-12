"""Dialog for choosing the backend and configuring the remote relay."""

import threading
import tkinter as tk
from tkinter import ttk

from core.remote_service import RemoteService
from core.settings import BACKEND_LABELS, BACKEND_OLLAMA, BACKEND_REMOTE


class ConnectionDialog(tk.Toplevel):
    """Edit backend/relay settings with a live connection test."""

    def __init__(self, parent, settings, on_save, remote_factory=None):
        super().__init__(parent)
        self.settings = settings
        self.on_save = on_save
        self.remote_factory = remote_factory or self._default_remote_factory
        self._test_token = 0
        self.title("Connection settings")
        self.transient(parent)
        self.resizable(False, False)
        self._build_widgets()
        self._update_remote_state()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda event: self.destroy())
        self.bind("<Return>", self._save_shortcut)
        self.grab_set()
        self.url_entry.focus_set()
        self._center_on(parent)

    @staticmethod
    def _default_remote_factory(url, api_key, max_tokens, enable_thinking):
        return RemoteService(
            url, api_key, max_tokens=max_tokens, enable_thinking=enable_thinking
        )

    def _center_on(self, parent):
        self.update_idletasks()
        try:
            x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
            self.geometry("+{0}+{1}".format(max(x, 0), max(y, 0)))
        except tk.TclError:
            pass

    # ---------------------------------------------------------------- layout
    def _build_widgets(self):
        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        backend_box = ttk.LabelFrame(body, text="Where should the model run?", padding=8)
        backend_box.pack(fill=tk.X)
        self.backend_var = tk.StringVar(value=self.settings.backend)
        ttk.Radiobutton(
            backend_box,
            text="{0} — models installed on this computer".format(
                BACKEND_LABELS[BACKEND_OLLAMA]
            ),
            value=BACKEND_OLLAMA,
            variable=self.backend_var,
            command=self._update_remote_state,
        ).pack(anchor="w")
        ttk.Radiobutton(
            backend_box,
            text="{0} — a GPU server reached through your relay".format(
                BACKEND_LABELS[BACKEND_REMOTE]
            ),
            value=BACKEND_REMOTE,
            variable=self.backend_var,
            command=self._update_remote_state,
        ).pack(anchor="w")

        self.remote_box = ttk.LabelFrame(body, text="Remote relay", padding=8)
        self.remote_box.pack(fill=tk.X, pady=(10, 0))
        self.remote_box.columnconfigure(1, weight=1)

        ttk.Label(self.remote_box, text="Relay URL:").grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar(value=self.settings.remote_url)
        self.url_entry = ttk.Entry(self.remote_box, textvariable=self.url_var, width=46)
        self.url_entry.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(6, 0))

        ttk.Label(self.remote_box, text="API key:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.key_var = tk.StringVar(value=self.settings.remote_api_key)
        self.key_entry = ttk.Entry(self.remote_box, textvariable=self.key_var, show="•", width=36)
        self.key_entry.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(6, 0))
        self.show_key_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.remote_box,
            text="Show",
            variable=self.show_key_var,
            command=self._toggle_key_visibility,
        ).grid(row=1, column=2, sticky="w", padx=(6, 0), pady=(6, 0))

        ttk.Label(self.remote_box, text="Max output tokens:").grid(
            row=2, column=0, sticky="w", pady=(6, 0)
        )
        self.max_tokens_var = tk.StringVar(value=str(self.settings.remote_max_tokens))
        self.max_tokens_spin = ttk.Spinbox(
            self.remote_box,
            from_=256,
            to=65536,
            increment=512,
            textvariable=self.max_tokens_var,
            width=10,
        )
        self.max_tokens_spin.grid(row=2, column=1, sticky="w", padx=(6, 0), pady=(6, 0))

        self.thinking_var = tk.BooleanVar(value=self.settings.remote_enable_thinking)
        self.thinking_check = ttk.Checkbutton(
            self.remote_box,
            text="Allow the model to think before answering (slower, may improve quality)",
            variable=self.thinking_var,
        )
        self.thinking_check.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

        test_row = ttk.Frame(self.remote_box)
        test_row.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.test_button = ttk.Button(test_row, text="Test connection", command=self.test_connection)
        self.test_button.pack(side=tk.LEFT)
        self.test_status_var = tk.StringVar(value="")
        self.test_status_label = ttk.Label(test_row, textvariable=self.test_status_var, wraplength=360)
        self.test_status_label.pack(side=tk.LEFT, padx=(10, 0), fill=tk.X, expand=True)

        self.details = tk.Text(
            self.remote_box,
            height=5,
            width=60,
            wrap=tk.WORD,
            relief=tk.FLAT,
            background="#f3f4f6",
            font=("Segoe UI", 9),
            state=tk.DISABLED,
        )
        self.details.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(6, 0))

        ttk.Label(
            body,
            text=(
                "Settings are saved next to the application in "
                "TextEnhanceAI-settings.json (the API key is stored in plain text)."
            ),
            wraplength=480,
            foreground="#555555",
        ).pack(anchor="w", pady=(10, 0))

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        self.save_button = ttk.Button(buttons, text="Save", command=self.save, style="Primary.TButton")
        self.save_button.pack(side=tk.RIGHT, padx=(0, 6))

    # --------------------------------------------------------------- actions
    def _toggle_key_visibility(self):
        self.key_entry.configure(show="" if self.show_key_var.get() else "•")

    def _update_remote_state(self):
        remote = self.backend_var.get() == BACKEND_REMOTE
        state = tk.NORMAL if remote else tk.DISABLED
        for widget in (
            self.url_entry,
            self.key_entry,
            self.max_tokens_spin,
            self.thinking_check,
            self.test_button,
        ):
            widget.configure(state=state)

    def _set_details(self, text):
        self.details.configure(state=tk.NORMAL)
        self.details.delete("1.0", tk.END)
        self.details.insert("1.0", text)
        self.details.configure(state=tk.DISABLED)

    def _current_max_tokens(self):
        try:
            return max(256, int(self.max_tokens_var.get().strip()))
        except ValueError:
            return self.settings.remote_max_tokens

    def _build_remote(self):
        return self.remote_factory(
            self.url_var.get().strip(),
            self.key_var.get().strip(),
            self._current_max_tokens(),
            self.thinking_var.get(),
        )

    def test_connection(self):
        url = self.url_var.get().strip()
        if not url:
            self.test_status_var.set("Enter the relay URL first.")
            return
        self._test_token += 1
        token = self._test_token
        self.test_button.configure(state=tk.DISABLED)
        self.test_status_var.set("Connecting to {0} ...".format(url))
        self._set_details("")

        def worker():
            try:
                service = self._build_remote()
                models = service.list_models()
                summary = service.connection_summary()
                details = describe_status(service.last_status, models)
                outcome = ("ok", summary, details)
            except Exception as exc:  # surfaced to the user, never raised in Tk
                outcome = ("error", str(exc), "")
            try:
                self.after(0, lambda: self._show_test_result(token, outcome))
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _show_test_result(self, token, outcome):
        if token != self._test_token:
            return
        kind, message, details = outcome
        try:
            self.test_button.configure(state=tk.NORMAL)
        except tk.TclError:
            return
        if kind == "ok":
            self.test_status_var.set(message)
            self.test_status_label.configure(foreground="#176b32")
        else:
            self.test_status_var.set(message)
            self.test_status_label.configure(foreground="#9b1c1c")
        self._set_details(details)

    def _save_shortcut(self, event=None):
        if self.focus_get() is not self.details:
            self.save()
        return "break"

    def save(self):
        backend = self.backend_var.get()
        url = self.url_var.get().strip()
        if backend == BACKEND_REMOTE and not url:
            self.test_status_var.set("Enter the relay URL before saving.")
            self.test_status_label.configure(foreground="#9b1c1c")
            return
        self.settings.backend = backend
        self.settings.remote_url = url
        self.settings.remote_api_key = self.key_var.get().strip()
        self.settings.remote_max_tokens = self._current_max_tokens()
        self.settings.remote_enable_thinking = bool(self.thinking_var.get())
        self.destroy()
        self.on_save(self.settings)


def describe_status(status, models):
    """Render a compact multi-line description of a relay status document."""
    lines = []
    if models:
        lines.append("Models: " + ", ".join(models))
    else:
        lines.append("Models: none available yet")
    agents = (status or {}).get("agents") or []
    if not agents:
        if status is None:
            lines.append("The server did not expose /status (plain OpenAI-compatible endpoint).")
        else:
            lines.append("GPU agents: none connected")
    for agent in agents:
        parts = [agent.get("name", "agent"), agent.get("state", "unknown")]
        in_flight = agent.get("in_flight")
        if in_flight is not None:
            parts.append("{0}/{1} busy".format(in_flight, agent.get("max_concurrency", "?")))
        agent_models = agent.get("models") or []
        if agent_models:
            parts.append(", ".join(str(m.get("id", m)) if isinstance(m, dict) else str(m) for m in agent_models))
        lines.append("Agent: " + " · ".join(str(part) for part in parts))
    relay = (status or {}).get("relay") or {}
    if relay.get("version"):
        lines.append("Relay version {0}, {1} requests served".format(
            relay.get("version"), relay.get("requests_total", 0)
        ))
    return "\n".join(lines)
