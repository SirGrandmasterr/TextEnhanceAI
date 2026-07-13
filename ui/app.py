"""Main Tkinter application for TextEnhanceAI v0.13."""

import os
import queue
import re
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, simpledialog, ttk

from core.diff_engine import build_edit_session, render_reviewed_text
from core.ollama_service import EditCancelled, OllamaService, OllamaUnavailable
from core.prompts import EDITING_MODES, PROMPTS, build_instruction
from core.scratchpad import ScratchpadLogger
from .review_panel import ReviewPanel


class EditorApp:
    """Coordinate editing, local generation, and structured review."""

    def __init__(self, root, app_directory=None, ollama_service=None):
        self.root = root
        self.app_directory = Path(app_directory or Path.cwd())
        self.service = ollama_service or OllamaService()
        self.events = queue.Queue()
        self.cancel_event = None
        self.active_request_id = 0
        self.active_revision_id = None
        self.revision_id = 0
        self.generating = False
        self.generation_started_at = None
        self.current_session = None
        self.current_logger = None
        self.last_applied_source = None
        self._suppress_modified = False

        self.root.title("TextEnhanceAI Editor with Local LLM - V 0.13")
        self.root.geometry("900x700")
        self.root.minsize(800, 600)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._configure_style()
        self._build_interface()
        self._bind_shortcuts()
        self.root.after(100, self._poll_events)
        self.root.after(150, self.refresh_models)

    def _configure_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"))

    def _build_interface(self):
        top_bar = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        top_bar.grid(row=0, column=0, sticky="ew")
        ttk.Label(top_bar, text="Model:").pack(side=tk.LEFT)
        self.model_var = tk.StringVar(value=os.getenv("TEAI_MODEL", "llama3.1:8b"))
        self.model_combo = ttk.Combobox(
            top_bar,
            textvariable=self.model_var,
            state="readonly",
            width=28,
            values=(self.model_var.get(),),
        )
        self.model_combo.pack(side=tk.LEFT, padx=(5, 6))
        self.refresh_button = ttk.Button(
            top_bar, text="Refresh models", command=self.refresh_models
        )
        self.refresh_button.pack(side=tk.LEFT)
        self.connection_var = tk.StringVar(value="Checking Ollama...")
        self.connection_label = tk.Label(
            top_bar,
            textvariable=self.connection_var,
            anchor="e",
            font=("Segoe UI", 9, "bold"),
        )
        self.connection_label.pack(side=tk.RIGHT)

        self.content = ttk.Frame(self.root)
        self.content.grid(row=1, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self.editor_frame = ttk.Frame(self.content, padding=(8, 4, 8, 4))
        self.editor_frame.pack(fill=tk.BOTH, expand=True)
        editor_heading = ttk.Label(
            self.editor_frame,
            text="Text to improve",
            font=("Segoe UI", 11, "bold"),
        )
        editor_heading.grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.text_area = scrolledtext.ScrolledText(
            self.editor_frame,
            wrap=tk.WORD,
            undo=True,
            font=("Segoe UI", 11),
            padx=8,
            pady=8,
        )
        self.text_area.grid(row=1, column=0, sticky="nsew")
        self.text_area.bind("<<Modified>>", self._on_text_modified)
        self.editor_frame.columnconfigure(0, weight=1)
        self.editor_frame.rowconfigure(1, weight=1)

        editor_meta = ttk.Frame(self.editor_frame)
        editor_meta.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self.count_var = tk.StringVar(value="0 words · 0 characters")
        ttk.Label(editor_meta, textvariable=self.count_var).pack(side=tk.RIGHT)

        controls = ttk.LabelFrame(self.editor_frame, text="Editing request", padding=7)
        controls.grid(row=3, column=0, sticky="ew", pady=(7, 0))
        ttk.Label(controls, text="Editing mode:").grid(row=0, column=0, sticky="w")
        self.mode_var = tk.StringVar(value="Grammar")
        self.mode_combo = ttk.Combobox(
            controls,
            textvariable=self.mode_var,
            state="readonly",
            values=EDITING_MODES,
            width=20,
        )
        self.mode_combo.grid(row=0, column=1, padx=6, sticky="w")
        self.mode_combo.bind("<<ComboboxSelected>>", self._update_mode_description)
        self.review_button = ttk.Button(
            controls,
            text="Review changes",
            style="Primary.TButton",
            command=self.start_review,
        )
        self.review_button.grid(row=0, column=2, padx=(8, 4))
        self.undo_button = ttk.Button(
            controls,
            text="Undo applied review",
            command=self.undo_applied_review,
            state=tk.DISABLED,
        )
        self.undo_button.grid(row=0, column=3, padx=4)
        controls.columnconfigure(4, weight=1)
        self.mode_description_var = tk.StringVar(value=PROMPTS["Grammar"])
        ttk.Label(
            controls,
            textvariable=self.mode_description_var,
            wraplength=720,
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(5, 0))

        self.review_panel = ReviewPanel(
            self.content,
            on_apply=self.apply_review,
            on_back=self.discard_review,
            on_status=self.set_status,
        )

        bottom = ttk.Frame(self.root, padding=(8, 4, 8, 8))
        bottom.grid(row=2, column=0, sticky="ew")
        self.progress = ttk.Progressbar(bottom, mode="indeterminate", length=160)
        self.progress.pack(side=tk.LEFT)
        self.cancel_button = ttk.Button(
            bottom, text="Cancel", command=self.cancel_generation, state=tk.DISABLED
        )
        self.cancel_button.pack(side=tk.LEFT, padx=6)
        self.status_var = tk.StringVar(
            value="Paste text, choose an editing mode, then review suggestions."
        )
        self.status_label = ttk.Label(bottom, textvariable=self.status_var, anchor="w")
        self.status_label.pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=6
        )
        ttk.Button(bottom, text="Quit", command=self.close).pack(side=tk.RIGHT)
        self.progress.pack_forget()
        self.cancel_button.pack_forget()

    def _bind_shortcuts(self):
        self.root.bind_all("<Control-Return>", self._primary_shortcut)
        self.root.bind_all("<Alt-KeyPress-a>", self._accept_shortcut)
        self.root.bind_all("<Alt-KeyPress-A>", self._accept_shortcut)
        self.root.bind_all("<Alt-KeyPress-r>", self._reject_shortcut)
        self.root.bind_all("<Alt-KeyPress-R>", self._reject_shortcut)
        self.root.bind_all("<Alt-Left>", self._previous_shortcut)
        self.root.bind_all("<Alt-Right>", self._next_shortcut)

    def _in_review(self):
        return bool(self.current_session)

    def _primary_shortcut(self, event=None):
        if self._in_review():
            if self.current_session.pending_count == 0:
                self.apply_review(self.current_session)
        elif not self.generating:
            self.start_review()
        return "break"

    def _accept_shortcut(self, event=None):
        if self._in_review():
            return self.review_panel.accept_current(event)
        return None

    def _reject_shortcut(self, event=None):
        if self._in_review():
            return self.review_panel.reject_current(event)
        return None

    def _previous_shortcut(self, event=None):
        if self._in_review():
            return self.review_panel.previous(event)
        return None

    def _next_shortcut(self, event=None):
        if self._in_review():
            return self.review_panel.next(event)
        return None

    def _on_text_modified(self, event=None):
        if self._suppress_modified:
            self.text_area.edit_modified(False)
            return
        if self.text_area.edit_modified():
            self.revision_id += 1
            self.text_area.edit_modified(False)
            self._update_counts()

    def _update_counts(self):
        text = self.text_area.get("1.0", "end-1c")
        words = len(re.findall(r"\S+", text))
        self.count_var.set(
            "{0} words · {1} characters".format(words, len(text))
        )

    def _update_mode_description(self, event=None):
        mode = self.mode_var.get()
        descriptions = {
            "Translate": "Translate the complete text into a language you choose.",
            "Custom": "Enter a custom editing instruction before generation.",
        }
        self.mode_description_var.set(descriptions.get(mode, PROMPTS.get(mode, "")))

    def set_status(self, message):
        self.status_var.set(message)

    def _set_connection(self, message, color):
        self.connection_var.set(message)
        self.connection_label.configure(foreground=color)

    def refresh_models(self):
        if self.generating:
            return
        self.refresh_button.configure(state=tk.DISABLED)
        self._set_connection("Checking Ollama...", "#555555")

        def worker():
            try:
                models = self.service.list_models()
                self.events.put(("models", models))
            except Exception as exc:
                self.events.put(("model_error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_models(self, models):
        self.refresh_button.configure(state=tk.NORMAL)
        self.model_combo.configure(values=models)
        if not models:
            self.model_var.set("")
            self._set_connection("Model missing", "#9a6700")
            self.set_status("Install a local Ollama model, then refresh the list.")
            return
        preferred = self.model_var.get()
        self.model_var.set(preferred if preferred in models else models[0])
        self._set_connection("Connected", "#176b32")

    def _handle_model_error(self, error):
        self.refresh_button.configure(state=tk.NORMAL)
        self.model_combo.configure(values=())
        self._set_connection("Unavailable", "#9b1c1c")
        self.set_status(str(error))

    def _get_instruction(self):
        mode = self.mode_var.get()
        if mode == "Translate":
            language = simpledialog.askstring(
                "Translate", "Target language:", parent=self.root
            )
            if not language or not language.strip():
                return None
            return build_instruction(mode, language.strip())
        if mode == "Custom":
            custom = simpledialog.askstring(
                "Custom instruction", "Editing instruction:", parent=self.root
            )
            if not custom or not custom.strip():
                return None
            return build_instruction(mode, custom.strip())
        return build_instruction(mode)

    def start_review(self):
        if self.generating:
            return
        source = self.text_area.get("1.0", "end-1c")
        if not source.strip():
            messagebox.showinfo("TextEnhanceAI", "Enter or paste text to edit.")
            return
        model = self.model_var.get().strip()
        if not model:
            messagebox.showerror(
                "Ollama model missing",
                "No local model is available. Install a model and refresh the list.",
            )
            return
        instruction = self._get_instruction()
        if instruction is None:
            return

        self.active_request_id += 1
        request_id = self.active_request_id
        revision_id = self.revision_id
        self.active_revision_id = revision_id
        self.cancel_event = threading.Event()
        self.generating = True
        self.generation_started_at = time.time()
        self._set_generating_state(True)

        def worker():
            try:
                result = self.service.stream_edit(
                    model, instruction, source, self.cancel_event
                )
                self.events.put(
                    (
                        "generation_result",
                        request_id,
                        revision_id,
                        source,
                        result,
                        instruction,
                        model,
                    )
                )
            except EditCancelled as exc:
                self.events.put(("generation_cancelled", request_id, exc))
            except Exception as exc:
                self.events.put(("generation_error", request_id, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _set_generating_state(self, generating):
        self.generating = generating
        editor_state = tk.DISABLED if generating else tk.NORMAL
        self.text_area.configure(state=editor_state)
        self.model_combo.configure(state="disabled" if generating else "readonly")
        self.mode_combo.configure(state="disabled" if generating else "readonly")
        self.refresh_button.configure(state=tk.DISABLED if generating else tk.NORMAL)
        self.review_button.configure(state=tk.DISABLED if generating else tk.NORMAL)
        self.cancel_button.configure(state=tk.NORMAL if generating else tk.DISABLED)
        if generating:
            self.progress.pack(side=tk.LEFT, before=self.status_label)
            self.cancel_button.pack(side=tk.LEFT, padx=6, before=self.status_label)
            self.progress.start(12)
            self.set_status("Generating review with Ollama...")
        else:
            self.progress.stop()
            self.progress.pack_forget()
            self.cancel_button.pack_forget()

    def cancel_generation(self):
        if self.generating and self.cancel_event:
            self.cancel_event.set()
            self.cancel_button.configure(state=tk.DISABLED)
            self.set_status("Cancelling generation...")

    def _finish_generation(self):
        self._set_generating_state(False)
        self.cancel_event = None
        self.generation_started_at = None

    def _handle_generation_result(self, event):
        (
            _,
            request_id,
            revision_id,
            source,
            proposed,
            instruction,
            model,
        ) = event
        if request_id != self.active_request_id:
            return
        self._finish_generation()
        if revision_id != self.revision_id:
            self.set_status("The text changed; the stale Ollama result was discarded.")
            return

        session = build_edit_session(
            source,
            proposed,
            instruction=instruction,
            model=model,
            revision_id=revision_id,
        )
        self.current_logger = ScratchpadLogger(self.app_directory)
        self.current_logger.log_proposal(session)
        if not session.review_items:
            self.current_logger.log_outcome(session, "no changes", source)
            self.current_logger = None
            self.set_status("Ollama did not suggest any changes.")
            messagebox.showinfo("Review complete", "No changes were suggested.")
            return

        self.current_session = session
        self.editor_frame.pack_forget()
        self.review_panel.pack(fill=tk.BOTH, expand=True)
        self.review_panel.set_session(session)

    def _handle_generation_error(self, request_id, error):
        if request_id != self.active_request_id:
            return
        self._finish_generation()
        self._set_connection("Unavailable", "#9b1c1c")
        self.set_status(str(error))
        messagebox.showerror("Ollama error", str(error))

    def _handle_generation_cancelled(self, request_id):
        if request_id != self.active_request_id:
            return
        self._finish_generation()
        self.set_status("Generation cancelled. Your text was not changed.")

    def _poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "models":
                    self._handle_models(event[1])
                elif kind == "model_error":
                    self._handle_model_error(event[1])
                elif kind == "generation_result":
                    self._handle_generation_result(event)
                elif kind == "generation_error":
                    self._handle_generation_error(event[1], event[2])
                elif kind == "generation_cancelled":
                    self._handle_generation_cancelled(event[1])
        except queue.Empty:
            pass

        if self.generating and self.generation_started_at:
            elapsed = int(time.time() - self.generation_started_at)
            self.set_status(
                "Generating review with Ollama... {0}s elapsed".format(elapsed)
            )
        try:
            self.root.after(100, self._poll_events)
        except tk.TclError:
            pass

    def apply_review(self, session):
        if session.pending_count:
            self.set_status("Review every pending change before applying.")
            return
        final_text = render_reviewed_text(session)
        self.last_applied_source = session.original_text
        session.state = "applied"
        if self.current_logger:
            self.current_logger.log_outcome(session, "applied", final_text)
        self._set_editor_text(final_text)
        self.undo_button.configure(state=tk.NORMAL)
        self._leave_review("Reviewed changes applied.")

    def discard_review(self, session):
        session.state = "discarded"
        if self.current_logger:
            self.current_logger.log_outcome(session, "discarded", session.original_text)
        self._leave_review("Review discarded. The original text was kept.")

    def _leave_review(self, status):
        self.review_panel.pack_forget()
        self.editor_frame.pack(fill=tk.BOTH, expand=True)
        self.current_session = None
        self.current_logger = None
        self.set_status(status)
        self.text_area.focus_set()

    def _set_editor_text(self, text):
        self._suppress_modified = True
        self.text_area.configure(state=tk.NORMAL)
        self.text_area.delete("1.0", tk.END)
        self.text_area.insert("1.0", text)
        self.text_area.edit_modified(False)
        self._suppress_modified = False
        self.revision_id += 1
        self._update_counts()

    def undo_applied_review(self):
        if self.last_applied_source is None:
            return
        source = self.last_applied_source
        self.last_applied_source = None
        self._set_editor_text(source)
        self.undo_button.configure(state=tk.DISABLED)
        self.set_status("The last applied review was undone.")

    def close(self):
        if self.cancel_event:
            self.cancel_event.set()
        self.root.destroy()
