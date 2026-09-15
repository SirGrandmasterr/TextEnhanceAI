"""Automatic manuscript review: start screen and project review screen."""

import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core.backend import EditCancelled
from core.change_kinds import CHANGE_KIND_LABELS, CHANGE_KINDS
from core.chunking import read_text_file, split_document, word_count
from core.models import ACCEPTED, PENDING, REJECTED
from core.workflow import (
    CHECK_DESCRIPTIONS,
    CHECK_LABELS,
    CHECKS,
    FLAG_LABELS,
    PROJECT_DIR_SUFFIX,
    PROJECT_FILE,
    SAME_LANGUAGE,
    STATE_APPLIED,
    STATE_PENDING,
    STATE_REJECTED,
    STATE_SUPERSEDED,
    STATUS_CLEAN,
    STATUS_ERROR,
    STATUS_QUEUED,
    STATUS_READY,
    STATUS_REVIEWED,
    Project,
    ProjectOptions,
    ProjectRunner,
    apply_model_outline,
    apply_result,
    build_outline_messages,
    change_states,
    create_project,
    new_decision_group,
    normalise_style_guide,
    parse_glossary,
    parse_outline,
    render_segment,
)
from .theme import CHECK_COLORS, PALETTE, STATUS_COLORS, ScrollableFrame, Tooltip, font, style_text

STATUS_LABELS = {
    STATUS_QUEUED: "Queued",
    "running": "Evaluating",
    STATUS_ERROR: "Error",
    STATUS_CLEAN: "No changes",
    STATUS_READY: "To review",
    STATUS_REVIEWED: "Reviewed",
}
STATUS_SYMBOLS = {
    STATUS_QUEUED: "○",
    "running": "◐",
    STATUS_ERROR: "!",
    STATUS_CLEAN: "✓",
    STATUS_READY: "●",
    STATUS_REVIEWED: "✓",
}
STATE_LABELS = {
    STATE_APPLIED: "Applied",
    STATE_SUPERSEDED: "Superseded",
    STATE_REJECTED: "Rejected",
    STATE_PENDING: "Pending",
}
LANGUAGES = [SAME_LANGUAGE, "English", "German", "French", "Spanish", "Italian", "Dutch"]
STYLE_GUIDE_PLACEHOLDER = "British spelling · keep dialect inside dialogue · never touch quotations"
STYLE_GUIDE_HINT = ("Standing rules every check must respect, one per line. They are sent with every request "
                    "and take precedence over the built-in rules where they conflict.")
GLOSSARY_PLACEHOLDER = "Thalbrück\nMeret Aubinger\nhyper*"
GLOSSARY_HINT = ("Protected terms: names, invented words and technical terms the checks must never change, one per "
                 "line. A trailing * protects every word starting with it (hyper* covers hyperdrive). Changes that "
                 "touch a protected term are dropped before you see them.")


def flag_tooltip(change):
    """Human-readable reasons behind a change's hallucination-guard flags."""
    return "\n".join(FLAG_LABELS.get(flag, flag) for flag in change.flags) or "Possibly invented content."


def _flagged_note(stats):
    return " \u00b7 {0} flagged \u26a0".format(stats["flagged"]) if stats.get("flagged") else ""


def _suppressed_note(stats):
    return " · {0} suppressed by glossary".format(stats["suppressed"]) if stats.get("suppressed") else ""


def _format_eta(seconds):
    if seconds is None:
        return ""
    if seconds < 90:
        return "about a minute left"
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return "~{0} min left".format(minutes)
    return "~{0} h {1:02d} min left".format(minutes // 60, minutes % 60)


class StyleGuideBox(tk.Text):
    """Multi-line entry for the author's instructions with a grey placeholder."""

    def __init__(self, parent, height=4, placeholder=STYLE_GUIDE_PLACEHOLDER, **kwargs):
        super().__init__(parent, height=height, **kwargs)
        style_text(self, size=10)
        self.placeholder = placeholder
        self._showing_placeholder = False
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", self._focus_out)
        self.set("")

    def get_text(self):
        """Return the author's text ("" while the placeholder is shown)."""
        if self._showing_placeholder:
            return ""
        return self.get("1.0", tk.END).rstrip()

    def set(self, text):
        self.delete("1.0", tk.END)
        if text:
            self._showing_placeholder = False
            self.configure(foreground=PALETTE["text"])
            self.insert("1.0", text)
        else:
            self._showing_placeholder = True
            self.configure(foreground=PALETTE["muted"])
            self.insert("1.0", self.placeholder)

    def _focus_in(self, event=None):
        if self._showing_placeholder:
            self._showing_placeholder = False
            self.delete("1.0", tk.END)
            self.configure(foreground=PALETTE["text"])

    def _focus_out(self, event=None):
        if not self.get("1.0", tk.END).strip():
            self.set("")


class StyleGuideDialog(tk.Toplevel):
    """Edit the author's instructions and protected terms of an open project."""

    def __init__(self, parent, style_guide, on_save, glossary=()):
        super().__init__(parent)
        self.on_save = on_save
        self.title("Review options")
        self.transient(parent.winfo_toplevel())
        self.resizable(True, False)
        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="Author's instructions", font=font(11, "bold")).pack(anchor="w")
        ttk.Label(body, text=STYLE_GUIDE_HINT, style="Muted.TLabel", wraplength=460).pack(anchor="w", pady=(2, 8))
        self.box = StyleGuideBox(body, height=5, width=60)
        self.box.pack(fill=tk.BOTH, expand=True)
        self.box.set(style_guide)
        ttk.Label(body, text="Protected terms", font=font(11, "bold")).pack(anchor="w", pady=(12, 0))
        ttk.Label(body, text=GLOSSARY_HINT, style="Muted.TLabel", wraplength=460).pack(anchor="w", pady=(2, 8))
        self.glossary_box = StyleGuideBox(body, height=5, width=60, placeholder=GLOSSARY_PLACEHOLDER)
        self.glossary_box.pack(fill=tk.BOTH, expand=True)
        self.glossary_box.set("\n".join(glossary))
        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Save", style="Primary.TButton", command=self.save).pack(side=tk.RIGHT, padx=(0, 6))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda event: self.destroy())
        self.grab_set()
        self.box.focus_set()
        self.update_idletasks()
        try:
            top = parent.winfo_toplevel()
            x = top.winfo_rootx() + (top.winfo_width() - self.winfo_width()) // 2
            y = top.winfo_rooty() + (top.winfo_height() - self.winfo_height()) // 2
            self.geometry("+{0}+{1}".format(max(x, 0), max(y, 0)))
        except tk.TclError:
            pass

    def save(self):
        text = self.box.get_text()
        glossary = parse_glossary(self.glossary_box.get_text())
        self.destroy()
        self.on_save(text, glossary)


class DecisionLogDialog(tk.Toplevel):
    """Read-only list of every accept/reject the author made; double-click jumps to the change."""

    COLUMNS = (
        ("time", "Time", 130),
        ("chapter", "Chapter", 150),
        ("segment", "Segment", 70),
        ("change", "Original → proposed", 320),
        ("decision", "Before → after", 150),
    )

    def __init__(self, parent, project, on_jump):
        super().__init__(parent)
        self.title("Decisions · {0}".format(project.name))
        self.transient(parent.winfo_toplevel())
        self.geometry("880x420")
        self.project = project
        self.on_jump = on_jump
        self.entries = {}
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        ttk.Label(frame, text="Newest decision first. Double-click a row to jump to the change.",
                  style="Muted.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.tree = ttk.Treeview(frame, columns=[key for key, _, _ in self.COLUMNS], show="headings",
                                 selectmode="browse")
        for key, heading, width in self.COLUMNS:
            self.tree.heading(key, text=heading, anchor="w")
            self.tree.column(key, width=width, stretch=(key == "change"), anchor="w")
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=1, column=1, sticky="ns")
        self.tree.bind("<Double-1>", self._jump)
        self.tree.bind("<Return>", self._jump)
        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="Go to change", command=self._jump).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Close", style="Ghost.TButton", command=self.destroy).pack(side=tk.LEFT, padx=(6, 0))
        self.bind("<Escape>", lambda event: self.destroy())
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        self.entries = {}
        log = self.project.decision_log
        if not log:
            self.tree.insert("", tk.END, values=("", "", "", "No decisions yet.", ""))
            return
        for position, entry in enumerate(reversed(log)):
            chapter, segment = self.project.find(entry["chapter"], entry["segment"])
            change = segment.find_change(entry["change_id"]) if segment is not None else None
            if change is not None:
                text = "{0} → {1}".format(_one_line(change.original_text) or "∅",
                                              _one_line(change.proposed_text) or "∅")
            else:
                text = "(change no longer exists)"
            iid = "d{0}".format(position)
            self.tree.insert("", tk.END, iid=iid, values=(
                str(entry.get("ts", "")).replace("T", " "),
                chapter.title if chapter is not None else str(entry["chapter"]),
                entry["segment"],
                text,
                "{0} → {1}".format(entry["before"], entry["after"]),
            ))
            self.entries[iid] = entry

    def _jump(self, event=None):
        selection = self.tree.selection()
        entry = self.entries.get(selection[0]) if selection else None
        if entry is not None:
            self.on_jump(entry["chapter"], entry["segment"], entry["change_id"])
        return "break"


def _one_line(text, limit=60):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


class WorkflowScreen(ttk.Frame):
    """Container that switches between the start view and the project view."""

    def __init__(self, parent, host):
        super().__init__(parent)
        self.host = host
        self.project = None
        self.runner = None
        self.running_tasks = set()
        self._save_after = None
        self.start_view = StartView(self, on_start=self.start_project, on_open=self.open_project)
        self.project_view = ProjectView(self, on_close=self.close_project, on_pause=self.toggle_pause,
                                        on_export=self.export_project, on_retry=self.resume_runner,
                                        on_checks_changed=self.checks_changed, on_options=self.edit_options,
                                        on_add_to_glossary=self.add_to_glossary)
        self.decision_dialog = None
        self.start_view.pack(fill=tk.BOTH, expand=True)

    # ----------------------------------------------------------- lifecycle
    @property
    def active(self):
        return self.project is not None

    @property
    def evaluating(self):
        return self.runner is not None and self.runner.active

    def show_start(self):
        self.project_view.pack_forget()
        self.start_view.pack(fill=tk.BOTH, expand=True)
        self.start_view.refresh_defaults(self.host)

    def show_project(self):
        self.start_view.pack_forget()
        self.project_view.pack(fill=tk.BOTH, expand=True)

    def start_project(self, path, options):
        try:
            text = read_text_file(path)
        except OSError as exc:
            messagebox.showerror("Cannot read file", str(exc))
            return
        if not text.strip():
            messagebox.showinfo("Empty file", "The selected file contains no text.")
            return
        model = self.host.get_model()
        if not model:
            messagebox.showerror("No model", "Select a model in the toolbar before starting a review.")
            return
        project = create_project(path, text, options, model=model, backend=self.host.backend_id())
        self.host.remember_style_guide(options.style_guide)
        self.host.remember_glossary(options.glossary)
        if project.root.exists() and (project.root / PROJECT_FILE).exists():
            if not messagebox.askyesno(
                "Replace previous review?",
                "A previous review of this file exists in\n{0}\n\nStart over and discard it?".format(project.root),
            ):
                return
        if options.chapter_mode == "model":
            self.host.set_status("Asking the model where the chapters start...")
            self._detect_chapters_with_model(project, text)
            return
        self._launch(project)

    def _detect_chapters_with_model(self, project, text):
        service = self.host.get_service()
        model = project.model
        cancel = threading.Event()

        def worker():
            try:
                answer = service.generate(model, build_outline_messages(text), cancel, max_tokens=2048)
                starts, titles = parse_outline(answer)
                self.host.events.put(("workflow_outline", project, text, starts, titles, None))
            except Exception as exc:
                self.host.events.put(("workflow_outline", project, text, [], [], str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _launch(self, project):
        self.project = project
        project.write_chapter_files()
        project.save()
        self.project_view.load_project(project)
        self.show_project()
        self.host.lock_controls(True)
        self.resume_runner()

    def open_project(self, path):
        try:
            project = Project.load(path)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("Cannot open project", str(exc))
            return
        self.project = project
        self.project_view.load_project(project)
        self.show_project()
        pending = project.pending_tasks()
        if pending:
            if messagebox.askyesno(
                "Resume evaluation?",
                "{0} check(s) are still missing. Continue the automatic evaluation now?".format(len(pending)),
            ):
                self.host.lock_controls(True)
                self.resume_runner()
                return
        self.host.set_status("Project opened. {0}".format(self.project_view.summary_text()))

    def resume_runner(self):
        if self.project is None or self.evaluating:
            return
        service = self.host.get_service()
        model = self.host.get_model() or self.project.model
        self.project.model = model
        self.project.backend = self.host.backend_id()
        self.runner = ProjectRunner(self.project, service, model, self.host.events)
        self.running_tasks = set()
        queued = self.runner.start()
        self.host.lock_controls(queued > 0)
        self.project_view.set_running(queued > 0)
        if queued:
            self.host.set_status(
                "Evaluating {0} check(s) with {1} on {2} parallel request(s)...".format(
                    queued, model, self.runner.parallelism
                )
            )
            self.project_view.update_progress(0, queued, 0, None)

    def toggle_pause(self):
        if self.evaluating:
            self.runner.cancel()
            self.project_view.set_pausing()
            self.host.set_status("Pausing after the running requests finish...")
        else:
            self.resume_runner()

    def checks_changed(self):
        """A check was enabled/disabled on the project screen."""
        if self.project is None:
            return
        self.project.save()
        self.project_view.refresh_all()
        if self.project.pending_tasks() and not self.evaluating:
            if messagebox.askyesno(
                "Evaluate now?",
                "The newly enabled check has not been evaluated for every segment yet. Start now?",
            ):
                self.host.lock_controls(True)
                self.resume_runner()

    def edit_options(self):
        """Open the review options (author's instructions) of the current project."""
        if self.project is None:
            return
        if self.evaluating:
            messagebox.showinfo("Evaluation running", "Pause the evaluation before changing the instructions.")
            return
        StyleGuideDialog(self, self.project.options.style_guide, on_save=self._apply_review_options,
                         glossary=self.project.options.glossary)

    def add_to_glossary(self, term):
        """Protect ``term`` from now on (called from a change card); returns whether it was new."""
        if self.project is None:
            return False
        term = " ".join((term or "").split())
        if not term or term in self.project.options.glossary:
            return False
        self.project.options.glossary.append(term)
        self.host.remember_glossary(self.project.options.glossary)
        self.schedule_save()
        self.host.set_status("Added \u201c{0}\u201d to the protected terms; it applies to segments evaluated from now on.".format(term))
        return True

    def _apply_review_options(self, style_guide, glossary):
        if self.project is None:
            return
        options = self.project.options
        unchanged = (
            normalise_style_guide(style_guide) == normalise_style_guide(options.style_guide)
            and list(glossary) == list(options.glossary)
        )
        options.style_guide = style_guide
        options.glossary = list(glossary)
        self.project.save()
        if unchanged:
            return
        self.host.remember_style_guide(style_guide)
        self.host.remember_glossary(options.glossary)
        has_results = any(segment.results for _, segment in self.project.all_segments())
        if has_results and messagebox.askyesno(
            "Re-evaluate?",
            "Re-evaluate all segments with the new instructions? "
            "(existing decisions on unchanged text are lost)",
        ):
            for _, segment in self.project.all_segments():
                segment.results.clear()
            self.running_tasks = set()
            self.project.save()
            self.project_view.refresh_all()
            self.project_view.render_segment()
            self.host.lock_controls(True)
            self.resume_runner()
            return
        self.project_view.refresh_all()
        self.host.set_status("Instructions saved; they apply to segments evaluated from now on.")

    def export_project(self):
        if self.project is None:
            return
        stats = self.project.progress()
        if stats["pending"]:
            if not messagebox.askyesno(
                "Pending changes",
                "{0} change(s) have no decision yet and will be left out (the original text is kept). "
                "Export anyway?".format(stats["pending"]),
            ):
                return
        try:
            paths = self.project.export()
            self.project.save()
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        self.host.set_status("Exported to {0}".format(paths["document"]))
        messagebox.showinfo(
            "Export complete",
            "Reviewed manuscript:\n{0}\n\nChapter files:\n{1}\n\nReport:\n{2}".format(
                paths["document"], paths["document"].parent / "reviewed", paths["report"]
            ),
        )

    def close_project(self):
        if self.project is None:
            return
        if self.evaluating:
            if not messagebox.askyesno("Stop evaluation?", "The evaluation is still running. Stop it and close the project?"):
                return
            self.runner.cancel()
        self._save_now()
        self.project = None
        self.runner = None
        if self.decision_dialog is not None and self.decision_dialog.winfo_exists():
            self.decision_dialog.destroy()
        self.decision_dialog = None
        self.host.lock_controls(False)
        self.show_start()
        self.host.set_status("Project closed. Progress was saved; open it again any time.")

    def shutdown(self):
        """Called when the application closes."""
        if self.runner is not None:
            self.runner.cancel()
        self._save_now()

    # -------------------------------------------------------------- saving
    def schedule_save(self):
        if self.project is None:
            return
        if self._save_after is not None:
            try:
                self.after_cancel(self._save_after)
            except tk.TclError:
                pass
        self._save_after = self.after(1500, self._save_now)

    def _save_now(self):
        self._save_after = None
        if self.project is not None:
            try:
                self.project.save()
            except OSError as exc:
                self.host.set_status("Could not save project: {0}".format(exc))

    # -------------------------------------------------------------- events
    def handle_event(self, event):
        kind = event[0]
        if kind == "workflow_outline":
            project, text, starts, titles, error = event[1:]
            if error or len(starts) < 2:
                self.host.set_status(
                    "The model did not propose usable chapter boundaries{0}; using the automatic split."
                    .format(" (" + error + ")" if error else "")
                )
            else:
                apply_model_outline(project, text, starts, titles)
            self._launch(project)
            return True
        if self.project is None:
            return kind.startswith("workflow_")
        if kind == "workflow_started":
            _, chapter_index, segment_index, check = event
            self.running_tasks.add((chapter_index, segment_index, check))
            self.project_view.running = {(c, s) for c, s, _ in self.running_tasks}
            self.project_view.segment_updated(chapter_index, segment_index)
            return True
        if kind == "workflow_result":
            _, chapter_index, segment_index, check, result = event
            segment = apply_result(self.project, chapter_index, segment_index, check, result)
            self.running_tasks.discard((chapter_index, segment_index, check))
            self.project_view.running = {(c, s) for c, s, _ in self.running_tasks}
            if segment is not None:
                self.project_view.segment_updated(chapter_index, segment_index)
            self.schedule_save()
            return True
        if kind == "workflow_progress":
            _, done, total, running = event
            eta = self.runner.eta_seconds() if self.runner else None
            self.project_view.update_progress(done, total, running, eta)
            return True
        if kind == "workflow_finished":
            cancelled = event[1]
            self.project_view.set_running(False)
            self.host.lock_controls(False)
            self._save_now()
            stats = self.project.progress()
            if cancelled:
                self.host.set_status("Evaluation paused. {0}".format(self.project_view.summary_text()))
            elif stats["error"]:
                self.host.set_status(
                    "Evaluation finished with {0} segment(s) in error. Use Retry to evaluate them again."
                    .format(stats["error"])
                )
            else:
                self.host.set_status("Evaluation complete. {0}".format(self.project_view.summary_text()))
            self.project_view.refresh_all()
            return True
        return False

    # ---------------------------------------------------------- shortcuts
    def accept_current(self, event=None):
        if self.active:
            self.project_view.decide_selected(ACCEPTED)
        return "break" if event else None

    def reject_current(self, event=None):
        if self.active:
            self.project_view.decide_selected(REJECTED)
        return "break" if event else None

    def undo(self, event=None):
        """Revert the last decision (Alt+Z) and show the change it belonged to."""
        if self.active:
            undone = self.project_view.undo()
            if undone is None:
                self.host.set_status("Nothing to undo.")
            else:
                self.host.set_status("Undid {0} decision(s).".format(len(undone)))
                self._refresh_decision_dialog()
        return "break" if event else None

    def show_decisions(self):
        """Open (or raise) the decision log window."""
        if not self.active:
            return
        if self.decision_dialog is not None and self.decision_dialog.winfo_exists():
            self.decision_dialog.project = self.project
            self.decision_dialog.refresh()
            self.decision_dialog.lift()
            return
        self.decision_dialog = DecisionLogDialog(self, self.project, on_jump=self.project_view.show_change)

    def _refresh_decision_dialog(self):
        if self.decision_dialog is not None and self.decision_dialog.winfo_exists():
            self.decision_dialog.refresh()

    def previous(self, event=None):
        if self.active:
            self.project_view.step_segment(-1)
        return "break" if event else None

    def next(self, event=None):
        if self.active:
            self.project_view.step_segment(1)
        return "break" if event else None

    def select_previous_change(self, event=None):
        if self.active:
            self.project_view.step_change(-1)
        return "break" if event else None

    def select_next_change(self, event=None):
        if self.active:
            self.project_view.step_change(1)
        return "break" if event else None


# ============================================================ start view
class StartView(ttk.Frame):
    """Choose the manuscript, the checks and the splitting options."""

    def __init__(self, parent, on_start, on_open):
        super().__init__(parent, padding=(24, 18))
        self.on_start = on_start
        self.on_open = on_open
        self.path = None
        self.text = ""
        self._build()

    def _card(self, parent, title, subtitle=None):
        card = ttk.Frame(parent, style="Card.TFrame", padding=14)
        ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w")
        if subtitle:
            ttk.Label(card, text=subtitle, style="SurfaceMuted.TLabel", wraplength=640).pack(anchor="w", pady=(2, 8))
        return card

    def _build(self):
        ttk.Label(self, text="Automatic review", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            self,
            text=("Upload a manuscript as .txt. It is split into chapters and short segments, each segment is "
                  "checked for spelling, grammar and expression, and every proposed change comes with a short "
                  "explanation for you to accept or reject."),
            style="Muted.TLabel", wraplength=760,
        ).pack(anchor="w", pady=(2, 14))

        scroller = ScrollableFrame(self)
        scroller.pack(fill=tk.BOTH, expand=True)
        body = scroller.inner
        body.columnconfigure(0, weight=1)

        # --- manuscript
        manuscript = self._card(body, "1. Manuscript")
        manuscript.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        row = ttk.Frame(manuscript, style="Surface.TFrame")
        row.pack(fill=tk.X)
        ttk.Button(row, text="Choose .txt file...", style="Accent.TButton", command=self.choose_file).pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value="No file selected")
        ttk.Label(row, textvariable=self.path_var, style="Surface.TLabel").pack(side=tk.LEFT, padx=12)
        self.info_var = tk.StringVar(value="")
        ttk.Label(manuscript, textvariable=self.info_var, style="SurfaceMuted.TLabel", wraplength=700,
                  justify=tk.LEFT).pack(anchor="w", pady=(8, 0))
        self.resume_frame = ttk.Frame(manuscript, style="Surface.TFrame")
        ttk.Label(self.resume_frame, text="A previous review of this file exists.", style="Surface.TLabel").pack(side=tk.LEFT)
        ttk.Button(self.resume_frame, text="Resume previous review", command=self.resume_existing).pack(side=tk.LEFT, padx=10)

        # --- checks
        checks = self._card(body, "2. Checks", "Each check runs independently on the original text; disable what you do not need. "
                            "Auto-accept applies a check's changes without asking (you can still undo them per change).")
        checks.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.check_vars = {}
        self.auto_vars = {}
        for check in CHECKS:
            row = ttk.Frame(checks, style="Surface.TFrame")
            row.pack(fill=tk.X, pady=2)
            self.check_vars[check] = tk.BooleanVar(value=True)
            ttk.Checkbutton(row, text=CHECK_LABELS[check], variable=self.check_vars[check],
                            style="Surface.TCheckbutton", width=12).pack(side=tk.LEFT)
            ttk.Label(row, text=CHECK_DESCRIPTIONS[check], style="SurfaceMuted.TLabel", wraplength=420).pack(side=tk.LEFT)
            self.auto_vars[check] = tk.BooleanVar(value=False)
            ttk.Checkbutton(row, text="auto-accept", variable=self.auto_vars[check],
                            style="Surface.TCheckbutton").pack(side=tk.RIGHT)

        # --- splitting & model
        options = self._card(body, "3. Splitting and evaluation")
        options.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        grid = ttk.Frame(options, style="Surface.TFrame")
        grid.pack(fill=tk.X)
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text="Chapters:", style="Surface.TLabel").grid(row=0, column=0, sticky="nw", pady=2)
        modes = ttk.Frame(grid, style="Surface.TFrame")
        modes.grid(row=0, column=1, sticky="w")
        self.chapter_mode = tk.StringVar(value="auto")
        for value, label in (
            ("auto", "Detect headings and scene breaks (fall back to size)"),
            ("size", "Split by size only"),
            ("model", "Ask the model to find chapter boundaries"),
            ("single", "Keep as one chapter"),
        ):
            ttk.Radiobutton(modes, text=label, value=value, variable=self.chapter_mode,
                            style="Surface.TRadiobutton", command=self._update_preview).pack(anchor="w")

        ttk.Label(grid, text="Segment size:", style="Surface.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 2))
        size_row = ttk.Frame(grid, style="Surface.TFrame")
        size_row.grid(row=1, column=1, sticky="w", pady=(8, 2))
        self.target_var = tk.StringVar(value="1800")
        spin = ttk.Spinbox(size_row, from_=400, to=6000, increment=200, textvariable=self.target_var, width=7,
                           command=self._update_preview)
        spin.pack(side=tk.LEFT)
        spin.bind("<FocusOut>", lambda event: self._update_preview())
        ttk.Label(size_row, text="characters per segment (≈ 300 words at 1800). Smaller segments give more precise "
                  "explanations, larger ones need fewer requests.", style="SurfaceMuted.TLabel",
                  wraplength=460).pack(side=tk.LEFT, padx=8)

        ttk.Label(grid, text="Parallel requests:", style="Surface.TLabel").grid(row=2, column=0, sticky="w", pady=2)
        par_row = ttk.Frame(grid, style="Surface.TFrame")
        par_row.grid(row=2, column=1, sticky="w", pady=2)
        self.parallel_var = tk.StringVar(value="2")
        ttk.Spinbox(par_row, from_=1, to=8, textvariable=self.parallel_var, width=5).pack(side=tk.LEFT)
        ttk.Label(par_row, text="(1 for local Ollama, 2–4 for a GPU server behind the relay)",
                  style="SurfaceMuted.TLabel").pack(side=tk.LEFT, padx=8)

        ttk.Label(grid, text="Explanations:", style="Surface.TLabel").grid(row=3, column=0, sticky="w", pady=2)
        expl_row = ttk.Frame(grid, style="Surface.TFrame")
        expl_row.grid(row=3, column=1, sticky="w", pady=2)
        self.explain_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(expl_row, text="Ask the model to explain each change", variable=self.explain_var,
                        style="Surface.TCheckbutton").pack(side=tk.LEFT)
        ttk.Label(expl_row, text="in", style="Surface.TLabel").pack(side=tk.LEFT, padx=(12, 4))
        self.language_var = tk.StringVar(value=SAME_LANGUAGE)
        ttk.Combobox(expl_row, textvariable=self.language_var, values=LANGUAGES, width=14).pack(side=tk.LEFT)

        # --- author's instructions and protected terms
        guide = self._card(body, "4. Author's instructions", STYLE_GUIDE_HINT)
        guide.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        self.style_guide_box = StyleGuideBox(guide, height=4)
        self.style_guide_box.pack(fill=tk.X)
        ttk.Label(guide, text="Protected terms", style="CardTitle.TLabel").pack(anchor="w", pady=(12, 0))
        ttk.Label(guide, text=GLOSSARY_HINT, style="SurfaceMuted.TLabel", wraplength=640).pack(anchor="w", pady=(2, 8))
        self.glossary_box = StyleGuideBox(guide, height=4, placeholder=GLOSSARY_PLACEHOLDER)
        self.glossary_box.pack(fill=tk.X)

        # --- actions
        actions = ttk.Frame(body)
        actions.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        self.start_button = ttk.Button(actions, text="Start automatic review", style="Accent.TButton",
                                       command=self.start, state=tk.DISABLED)
        self.start_button.pack(side=tk.LEFT)
        ttk.Button(actions, text="Open existing project...", command=self.open_existing).pack(side=tk.LEFT, padx=8)
        self.estimate_var = tk.StringVar(value="")
        ttk.Label(actions, textvariable=self.estimate_var, style="Muted.TLabel").pack(side=tk.LEFT, padx=12)

    def refresh_defaults(self, host):
        """Suggest a parallelism that suits the backend and pre-fill the author's instructions."""
        try:
            self.parallel_var.set("1" if host.backend_id() == "ollama" else "2")
        except Exception:
            pass
        try:
            if not self.style_guide_box.get_text():
                self.style_guide_box.set(host.default_style_guide())
            if not self.glossary_box.get_text():
                self.glossary_box.set("\n".join(host.default_glossary()))
        except Exception:
            pass

    # ------------------------------------------------------------ actions
    def choose_file(self):
        path = filedialog.askopenfilename(
            title="Choose a manuscript", filetypes=[("Text files", "*.txt *.md *.text"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            self.text = read_text_file(path)
        except OSError as exc:
            messagebox.showerror("Cannot read file", str(exc))
            return
        self.path = Path(path)
        self.path_var.set(self.path.name)
        self.start_button.configure(state=tk.NORMAL if self.text.strip() else tk.DISABLED)
        project_dir = self.path.with_name(self.path.stem + PROJECT_DIR_SUFFIX)
        if (project_dir / PROJECT_FILE).exists():
            self.resume_frame.pack(anchor="w", pady=(8, 0))
        else:
            self.resume_frame.pack_forget()
        self._update_preview()

    def options(self):
        try:
            target = max(400, min(6000, int(self.target_var.get())))
        except ValueError:
            target = 1800
        try:
            parallel = max(1, min(8, int(self.parallel_var.get())))
        except ValueError:
            parallel = 2
        return ProjectOptions(
            checks={check: bool(var.get()) for check, var in self.check_vars.items()},
            auto_accept={check: bool(var.get()) for check, var in self.auto_vars.items()},
            target_chars=target,
            max_chars=int(target * 1.7),
            chapter_mode=self.chapter_mode.get(),
            explain=bool(self.explain_var.get()),
            language=self.language_var.get().strip() or SAME_LANGUAGE,
            parallelism=parallel,
            style_guide=self.style_guide_box.get_text(),
            glossary=parse_glossary(self.glossary_box.get_text()),
        )

    def _update_preview(self):
        if not self.text:
            self.info_var.set("")
            self.estimate_var.set("")
            return
        options = self.options()
        mode = "auto" if options.chapter_mode == "model" else options.chapter_mode
        result = split_document(self.text, mode=mode, target_chars=options.target_chars, max_chars=options.max_chars)
        segments = sum(len(chapter.segments) for chapter in result.chapters)
        words = word_count(self.text)
        method = {
            "single": "one chapter", "size": "split by size", "separators": "scene breaks", "model": "model",
        }.get(result.method, "headings")
        titles = ", ".join(chapter.title for chapter in result.chapters[:6])
        if len(result.chapters) > 6:
            titles += ", …"
        self.info_var.set(
            "{0:,} words · {1:,} characters · {2} chapter(s) ({3}) · {4} segment(s)\n{5}".format(
                words, len(self.text), len(result.chapters), method, segments, titles
            )
        )
        checks = len(options.enabled_checks())
        requests = segments * checks * (2 if options.explain else 1)
        self.estimate_var.set("≈ {0} model requests".format(requests) if checks else "No check enabled")

    def start(self):
        if not self.path:
            return
        options = self.options()
        if not options.enabled_checks():
            messagebox.showinfo("No checks", "Enable at least one check.")
            return
        self.on_start(self.path, options)

    def resume_existing(self):
        if self.path:
            self.on_open(self.path.with_name(self.path.stem + PROJECT_DIR_SUFFIX))

    def open_existing(self):
        path = filedialog.askopenfilename(
            title="Open a review project", filetypes=[("TextEnhanceAI project", PROJECT_FILE), ("All files", "*.*")]
        )
        if path:
            self.on_open(path)


# ========================================================== project view
class ChangeCard(ttk.Frame):
    """One proposed change with explanation and accept/reject buttons."""

    def __init__(self, parent, change, segment_text, state, on_select, on_decide, on_add_to_glossary=None):
        super().__init__(parent, style="Card.TFrame", padding=(10, 8))
        self.change = change
        self.on_select = on_select
        self.on_decide = on_decide
        self.on_add_to_glossary = on_add_to_glossary
        self.selected = False
        fg, bg = CHECK_COLORS[change.check]

        top = ttk.Frame(self, style="Surface.TFrame")
        top.pack(fill=tk.X)
        self.badge = ttk.Label(top, text=CHECK_LABELS[change.check], style="{0}.Badge.TLabel".format(change.check.title()))
        self.badge.pack(side=tk.LEFT)
        self.state_label = ttk.Label(top, text=STATE_LABELS[state], style="{0}.State.TLabel".format(state.title()))
        self.state_label.pack(side=tk.LEFT, padx=8)
        self.kind_tag = ttk.Label(top, text=CHANGE_KIND_LABELS.get(change.kind, change.kind), style="Kind.Badge.TLabel")
        self.kind_tag.pack(side=tk.LEFT, padx=(0, 8))
        self.flag_badge = None
        if change.flagged:
            # Hallucination guard: the reason ids explain themselves in the tooltip.
            self.flag_badge = ttk.Label(top, text="\u26a0 check this", style="Flag.Badge.TLabel")
            self.flag_badge.pack(side=tk.LEFT, padx=(0, 8))
            Tooltip(self.flag_badge, flag_tooltip(change))
        self.glossary_link = None
        if on_add_to_glossary is not None and change.original_text.strip():
            # A small link: reject this change and protect the original wording from now on.
            self.glossary_link = tk.Label(top, text="Add to glossary", cursor="hand2", font=font(9),
                                          foreground=PALETTE["accent"], background=PALETTE["surface"])
            self.glossary_link.pack(side=tk.LEFT, padx=(4, 0))
            self.glossary_link.bind("<Button-1>", self._add_to_glossary)
        self.reject_button = ttk.Button(top, text="Reject", style="Small.Danger.TButton",
                                        command=lambda: self.on_decide(self.change, REJECTED))
        self.reject_button.pack(side=tk.RIGHT)
        self.accept_button = ttk.Button(top, text="Accept", style="Small.Success.TButton",
                                        command=lambda: self.on_decide(self.change, ACCEPTED))
        self.accept_button.pack(side=tk.RIGHT, padx=(0, 6))

        self.diff = tk.Text(self, height=2, cursor="arrow")
        style_text(self.diff, size=10)
        self.diff.configure(padx=6, pady=4, highlightthickness=0, spacing1=0, spacing3=0)
        self.diff.tag_configure("context", foreground=PALETTE["muted"])
        self.diff.tag_configure("removed", foreground=PALETTE["danger"], background=PALETTE["danger_soft"], overstrike=True)
        self.diff.tag_configure("added", foreground=PALETTE["success"], background=PALETTE["success_soft"], underline=True)
        self.diff.tag_configure("arrow", foreground=PALETTE["faint"])
        self._render_diff(segment_text)
        self.diff.pack(fill=tk.X, pady=(6, 4))

        self.explanation = ttk.Label(self, text=change.explanation or "", style="SurfaceMuted.TLabel",
                                     wraplength=520, justify=tk.LEFT, font=font(9, slant="italic"))
        self.explanation.pack(anchor="w")

        for widget in (self, top, self.badge, self.state_label, self.diff, self.explanation):
            widget.bind("<Button-1>", self._clicked, add="+")
        self.refresh(state)

    def _render_diff(self, segment_text):
        change = self.change
        radius = 36
        before = segment_text[max(0, change.start - radius):change.start].replace("\n", " ")
        after = segment_text[change.end:change.end + radius].replace("\n", " ")
        if change.start - radius > 0:
            before = "…" + before
        if change.end + radius < len(segment_text):
            after = after + "…"
        self.diff.configure(state=tk.NORMAL)
        self.diff.delete("1.0", tk.END)
        self.diff.insert(tk.END, before, "context")
        if change.original_text:
            self.diff.insert(tk.END, change.original_text.replace("\n", "↵"), "removed")
        if change.original_text and change.proposed_text:
            self.diff.insert(tk.END, " → ", "arrow")
        if change.proposed_text:
            self.diff.insert(tk.END, change.proposed_text.replace("\n", "↵"), "added")
        elif not change.original_text:
            self.diff.insert(tk.END, "∅", "arrow")
        self.diff.insert(tk.END, after, "context")
        length = len(before) + len(change.original_text) + len(change.proposed_text) + len(after) + 3
        self.diff.configure(height=min(4, max(1, length // 70 + 1)), state=tk.DISABLED)

    def _clicked(self, event=None):
        self.on_select(self.change)
        return "break"

    def _add_to_glossary(self, event=None):
        if self.on_add_to_glossary is not None:
            self.on_add_to_glossary(self.change)
        return "break"

    def refresh(self, state, selected=None):
        if selected is not None:
            self.selected = selected
        self.state_label.configure(text=STATE_LABELS[state], style="{0}.State.TLabel".format(state.title()))
        self.configure(style="Selected.Card.TFrame" if self.selected else "Card.TFrame")
        surface = PALETTE["selection"] if self.selected else PALETTE["surface"]
        self.diff.configure(background=surface)
        if self.glossary_link is not None:
            self.glossary_link.configure(background=surface)
        self.explanation.configure(style="SelectedMuted.TLabel" if self.selected else "SurfaceMuted.TLabel")
        for child in self.winfo_children():
            if isinstance(child, ttk.Frame):
                child.configure(style="Selected.TFrame" if self.selected else "Surface.TFrame")
        self.accept_button.configure(state=tk.DISABLED if self.change.decision == ACCEPTED else tk.NORMAL)
        self.reject_button.configure(state=tk.DISABLED if self.change.decision == REJECTED else tk.NORMAL)


class ProjectView(ttk.Frame):
    """Chapter/segment navigation, highlighted text and change cards."""

    def __init__(self, parent, on_close, on_pause, on_export, on_retry, on_checks_changed, on_options=None,
                 on_add_to_glossary=None):
        super().__init__(parent, padding=(12, 8))
        self.on_close = on_close
        self.on_pause = on_pause
        self.on_export = on_export
        self.on_retry = on_retry
        self.on_checks_changed = on_checks_changed
        self.on_options = on_options or (lambda: None)
        self.on_add_to_glossary = on_add_to_glossary
        self.project = None
        self.current = None  # (chapter_index, segment_index)
        self.running = set()  # (chapter_index, segment_index) currently being evaluated
        self.cards = {}
        self.selected_change_id = None
        self.filter_vars = {check: tk.BooleanVar(value=True) for check in CHECKS}
        self.kind_vars = {kind: tk.BooleanVar(value=True) for kind in CHANGE_KINDS}  # True = shown
        self.preview_var = tk.BooleanVar(value=False)
        self._build()

    # ---------------------------------------------------------------- build
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        self.title_var = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.title_var, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.summary_var = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.summary_var, style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w")
        buttons = ttk.Frame(header)
        buttons.grid(row=0, column=2, rowspan=2, sticky="e")
        self.pause_button = ttk.Button(buttons, text="Pause", command=self.on_pause)
        self.pause_button.pack(side=tk.LEFT)
        ttk.Button(buttons, text="Options...", command=self.on_options).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(buttons, text="Decisions...", command=lambda: self.master.show_decisions()).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(buttons, text="Export...", style="Accent.TButton", command=self.on_export).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Close project", style="Ghost.TButton", command=self.on_close).pack(side=tk.LEFT)

        progress_row = ttk.Frame(self)
        progress_row.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        progress_row.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_row, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress_var = tk.StringVar(value="")
        ttk.Label(progress_row, textvariable=self.progress_var, style="Muted.TLabel").grid(row=0, column=1, padx=(10, 0))

        checks_row = ttk.Frame(progress_row)
        checks_row.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(checks_row, text="Checks:", style="Muted.TLabel").pack(side=tk.LEFT)
        self.check_vars = {}
        self.check_buttons = {}
        for check in CHECKS:
            self.check_vars[check] = tk.BooleanVar(value=True)
            button = ttk.Checkbutton(checks_row, text=CHECK_LABELS[check], variable=self.check_vars[check],
                                     command=lambda c=check: self._toggle_check(c))
            button.pack(side=tk.LEFT, padx=(8, 0))
            self.check_buttons[check] = button
        ttk.Label(checks_row, text="   Show:", style="Muted.TLabel").pack(side=tk.LEFT)
        for check in CHECKS:
            ttk.Checkbutton(checks_row, text=CHECK_LABELS[check], variable=self.filter_vars[check],
                            command=self.render_segment).pack(side=tk.LEFT, padx=(8, 0))
        self.kinds_button = ttk.Menubutton(checks_row, text="Kinds ▾", style="Small.TButton")
        self.kinds_menu = tk.Menu(self.kinds_button, tearoff=False, postcommand=self._fill_kinds_menu)
        self.kinds_button.configure(menu=self.kinds_menu)
        self.kinds_button.pack(side=tk.LEFT, padx=(12, 0))
        Tooltip(self.kinds_button, "Hide kinds of edits from the review, or accept/reject every pending "
                                   "change of one kind across the project (Alt+Z reverts).")
        self.hint_var = tk.StringVar(value="Alt+A accept · Alt+R reject · Alt+Z undo · Alt+↑/↓ change · Alt+←/→ segment")
        ttk.Label(checks_row, textvariable=self.hint_var, style="Muted.TLabel", font=font(9)).pack(side=tk.RIGHT)

        paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        paned.grid(row=2, column=0, sticky="nsew")

        left = ttk.Frame(paned)
        paned.add(left, weight=1)
        self.tree = ttk.Treeview(left, columns=("status",), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Chapters and segments", anchor="w")
        self.tree.heading("status", text="Status", anchor="w")
        self.tree.column("#0", width=200, stretch=True)
        self.tree.column("status", width=125, stretch=False)
        tree_scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        for status, color in STATUS_COLORS.items():
            self.tree.tag_configure(status, foreground=color)
        self.tree.tag_configure("chapter", font=font(10, "bold"))
        self.tree.bind("<<TreeviewSelect>>", self._tree_selected)

        right = ttk.Frame(paned)
        paned.add(right, weight=3)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=2)
        right.rowconfigure(3, weight=3)

        seg_header = ttk.Frame(right)
        seg_header.grid(row=0, column=0, sticky="ew", padx=(10, 0))
        self.segment_var = tk.StringVar(value="Select a segment")
        ttk.Label(seg_header, textvariable=self.segment_var, style="TLabel", font=font(11, "bold")).pack(side=tk.LEFT)
        self.segment_status = ttk.Label(seg_header, text="", style="Status.TLabel")
        self.segment_status.pack(side=tk.LEFT, padx=10)
        ttk.Checkbutton(seg_header, text="Preview result", variable=self.preview_var, command=self.render_segment).pack(side=tk.RIGHT)

        self.text = tk.Text(right, height=9)
        style_text(self.text, size=11, readonly=True)
        self.text.grid(row=1, column=0, sticky="nsew", padx=(10, 0), pady=(4, 6))
        for check, (fg, bg) in CHECK_COLORS.items():
            self.text.tag_configure(check, background=bg, underline=True)
        self.text.tag_configure("selected", background=PALETTE["accent_soft"], foreground=PALETTE["accent_dark"], underline=True)
        self.text.tag_configure("rejected", background=PALETTE["surface"], underline=False, overstrike=False,
                                foreground=PALETTE["muted"])
        self.text.tag_raise("selected")

        actions = ttk.Frame(right)
        actions.grid(row=2, column=0, sticky="ew", padx=(10, 0), pady=(0, 4))
        ttk.Button(actions, text="◀ Previous", style="Small.TButton", command=lambda: self.step_segment(-1)).pack(side=tk.LEFT)
        ttk.Button(actions, text="Next ▶", style="Small.TButton", command=lambda: self.step_segment(1)).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Next to review", style="Small.TButton", command=self.jump_to_next_pending).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="Reject all shown", style="Small.Danger.TButton", command=lambda: self.decide_all(REJECTED)).pack(side=tk.RIGHT)
        ttk.Button(actions, text="Accept all shown", style="Small.Success.TButton", command=lambda: self.decide_all(ACCEPTED)).pack(side=tk.RIGHT, padx=(0, 6))
        self.undo_button = ttk.Button(actions, text="Undo", style="Small.TButton", command=lambda: self.master.undo())
        self.undo_button.pack(side=tk.RIGHT, padx=(0, 6))
        Tooltip(self.undo_button, "Revert the last accept/reject (a bulk action is reverted as a whole). Alt+Z")
        self.reject_flagged_button = ttk.Button(actions, text="Reject flagged \u26a0", style="Small.TButton",
                                                command=self.reject_flagged)
        self.reject_flagged_button.pack(side=tk.RIGHT, padx=(0, 6))
        self.retry_button = ttk.Button(actions, text="Retry failed", style="Small.TButton", command=self.on_retry)

        self.cards_frame = ScrollableFrame(right)
        self.cards_frame.grid(row=3, column=0, sticky="nsew", padx=(10, 0))

    # ------------------------------------------------------------- loading
    def load_project(self, project):
        self.project = project
        self.title_var.set(project.name)
        for check in CHECKS:
            self.check_vars[check].set(bool(project.options.checks.get(check)))
        for kind in CHANGE_KINDS:
            self.kind_vars[kind].set(kind not in project.options.hidden_kinds)
        self.tree.delete(*self.tree.get_children())
        for chapter in project.chapters:
            chapter_id = "c{0}".format(chapter.index)
            self.tree.insert("", tk.END, iid=chapter_id, text="{0}. {1}".format(chapter.index, chapter.title),
                             values=("",), open=True, tags=("chapter",))
            for segment in chapter.segments:
                self.tree.insert(chapter_id, tk.END, iid=self._segment_iid(chapter.index, segment.index),
                                 text="Segment {0} · {1} words".format(segment.index, word_count(segment.text)),
                                 values=("",))
        self.current = None
        self.refresh_all()
        first = self._first_segment_with(lambda status: status == STATUS_READY) or self._first_segment_with(lambda status: True)
        if first:
            self.select_segment(*first)

    @staticmethod
    def _segment_iid(chapter_index, segment_index):
        return "s{0}-{1}".format(chapter_index, segment_index)

    def _first_segment_with(self, predicate, after=None):
        found_start = after is None
        for chapter, segment in self.project.all_segments():
            key = (chapter.index, segment.index)
            if not found_start:
                if key == after:
                    found_start = True
                continue
            if predicate(segment.status(self.project.enabled)):
                return key
        return None

    # -------------------------------------------------------------- state
    def set_running(self, running):
        if not running:
            self.running = set()
        if running:
            self.pause_button.configure(text="Pause", state=tk.NORMAL)
        else:
            pending = bool(self.project and self.project.pending_tasks())
            self.pause_button.configure(text="Resume" if pending else "Evaluation complete",
                                        state=tk.NORMAL if pending else tk.DISABLED)
        self.refresh_all()

    def set_pausing(self):
        self.pause_button.configure(text="Pausing...", state=tk.DISABLED)

    def update_progress(self, done, total, running, eta):
        if total:
            self.progress.configure(value=100.0 * done / total)
        else:
            self.progress.configure(value=100)
        parts = ["{0}/{1} checks evaluated".format(done, total)]
        if running:
            parts.append("{0} running".format(running))
        eta_text = _format_eta(eta)
        if eta_text and done < total:
            parts.append(eta_text)
        self.progress_var.set(" · ".join(parts))

    def summary_text(self):
        stats = self.project.progress()
        return "{0} segments · {1} changes: {2} accepted, {3} rejected, {4} pending{5}".format(
            stats["segments"], stats["changes"], stats["accepted"], stats["rejected"], stats["pending"],
            _suppressed_note(stats),
        )

    def _summary_line(self, stats):
        return (
            "{0} chapters · {1} segments · {2:,} words   |   {3} changes proposed · {4} accepted · {5} rejected · "
            "{6} pending{7}"
        ).format(len(self.project.chapters), stats["segments"], stats["words"], stats["changes"],
                 stats["accepted"], stats["rejected"], stats["pending"],
                 _flagged_note(stats) + _suppressed_note(stats))

    def refresh_all(self):
        if self.project is None:
            return
        stats = self.project.progress()
        self.summary_var.set(self._summary_line(stats))
        for check in CHECKS:
            per = stats["per_check"][check]
            self.check_buttons[check].configure(text="{0} ({1})".format(CHECK_LABELS[check], per["changes"]))
        for chapter, segment in self.project.all_segments():
            self._refresh_tree_row(chapter, segment)
        if stats["error"]:
            self.retry_button.pack(side=tk.LEFT, padx=(8, 0))
        else:
            self.retry_button.pack_forget()
        if self.current:
            self.render_segment()

    def _segment_status(self, chapter, segment):
        status = segment.status(self.project.enabled)
        if status == STATUS_QUEUED and (chapter.index, segment.index) in self.running:
            return "running"
        return status

    def _refresh_tree_row(self, chapter, segment):
        status = self._segment_status(chapter, segment)
        iid = self._segment_iid(chapter.index, segment.index)
        changes = segment.changes(self.project.enabled)
        pending = sum(1 for change in changes if change.decision == PENDING)
        label = STATUS_LABELS[status]
        if status == STATUS_READY:
            label = "{0} pending".format(pending)
        elif status == STATUS_REVIEWED:
            label = "Reviewed ({0})".format(len(changes))
        glyph = STATUS_SYMBOLS[status]
        if any(change.flagged and change.decision == PENDING for change in changes):
            glyph = "\u26a0"  # pending changes the hallucination guard flagged
        self.tree.item(iid, values=("{0} {1}".format(glyph, label),), tags=(status,))

    def segment_updated(self, chapter_index, segment_index):
        chapter, segment = self.project.find(chapter_index, segment_index)
        if segment is None:
            return
        self._refresh_tree_row(chapter, segment)
        stats = self.project.progress()
        for check in CHECKS:
            per = stats["per_check"][check]
            self.check_buttons[check].configure(text="{0} ({1})".format(CHECK_LABELS[check], per["changes"]))
        if self.current == (chapter_index, segment_index):
            self.render_segment()

    def _toggle_check(self, check):
        if self.project is None:
            return
        self.project.options.checks[check] = bool(self.check_vars[check].get())
        self.on_checks_changed()

    # -------------------------------------------------------------- kinds
    def _fill_kinds_menu(self):
        """Rebuild the Kinds menu with current counts every time it opens."""
        menu = self.kinds_menu
        menu.delete(0, tk.END)
        if self.project is None:
            return
        stats = self.project.progress()
        menu.add_command(label="Show kinds", state=tk.DISABLED)
        for kind in CHANGE_KINDS:
            menu.add_checkbutton(
                label="{0} ({1})".format(CHANGE_KIND_LABELS[kind], stats["per_kind"][kind]["changes"]),
                variable=self.kind_vars[kind], onvalue=True, offvalue=False,
                command=lambda k=kind: self._toggle_kind(k),
            )
        menu.add_separator()
        for decision, label in ((ACCEPTED, "Accept all…"), (REJECTED, "Reject all…")):
            submenu = tk.Menu(menu, tearoff=False)
            for kind in CHANGE_KINDS:
                pending = len(self.project.changes_by_kind(kind, decision=PENDING))
                submenu.add_command(
                    label="{0} ({1} pending)".format(CHANGE_KIND_LABELS[kind], pending),
                    state=tk.NORMAL if pending else tk.DISABLED,
                    command=lambda k=kind, d=decision: self.decide_kind_everywhere(k, d),
                )
            menu.add_cascade(label=label, menu=submenu)

    def _toggle_kind(self, kind):
        if self.project is None:
            return
        hidden = [k for k in CHANGE_KINDS if not self.kind_vars[k].get()]
        self.project.options.hidden_kinds = hidden
        self.kinds_button.configure(text="Kinds ▾" if not hidden else "Kinds ({0} hidden) ▾".format(len(hidden)))
        self.master.schedule_save()
        self.render_segment()

    def decide_kind_everywhere(self, kind, decision):
        """Accept or reject every pending change of ``kind`` in the project (one undo group)."""
        if self.project is None:
            return
        pending = self.project.changes_by_kind(kind, decision=PENDING)
        label = CHANGE_KIND_LABELS[kind].lower()
        verb = "Accept" if decision == ACCEPTED else "Reject"
        if not pending:
            self.master.host.set_status("No pending {0} changes.".format(label))
            return
        if not messagebox.askyesno(
            "{0} all {1} changes?".format(verb, label),
            "{0} {1} pending {2} change(s) across the whole project?\n\nAlt+Z (Undo) reverts them all at once."
            .format(verb, len(pending), label),
        ):
            return
        entries = self.project.decide_kind(kind, decision)
        self.refresh_all()
        self._after_decision()
        self.master.host.set_status("{0}ed {1} {2} change(s). Alt+Z reverts them.".format(
            verb, len(entries), label))

    # ------------------------------------------------------------ segment
    def _tree_selected(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        iid = selection[0]
        if iid.startswith("s"):
            chapter_index, segment_index = iid[1:].split("-")
            self.select_segment(int(chapter_index), int(segment_index), from_tree=True)

    def select_segment(self, chapter_index, segment_index, from_tree=False, change_id=None):
        self.current = (chapter_index, segment_index)
        self.selected_change_id = change_id
        if not from_tree:
            iid = self._segment_iid(chapter_index, segment_index)
            try:
                self.tree.selection_set(iid)
                self.tree.see(iid)
            except tk.TclError:
                pass
        self.render_segment()

    def _current_segment(self):
        if self.project is None or self.current is None:
            return None, None
        return self.project.find(*self.current)

    def _visible_changes(self, segment):
        enabled = dict(self.project.enabled)
        for check in CHECKS:
            if not self.filter_vars[check].get():
                enabled[check] = False
        return segment.changes(enabled, hidden_kinds=self.project.options.hidden_kinds)

    def render_segment(self):
        chapter, segment = self._current_segment()
        if segment is None:
            return
        enabled = self.project.enabled
        status = self._segment_status(chapter, segment)
        changes = self._visible_changes(segment)
        states = change_states(segment, enabled)
        pending = sum(1 for change in changes if change.decision == PENDING)
        suppressed = sum(
            len(result.suppressed) for check, result in segment.results.items()
            if enabled.get(check) and result.status == "done"
        )
        self.segment_var.set("{0} · Segment {1} of {2} · {3} words".format(
            chapter.title, segment.index, len(chapter.segments), word_count(segment.text)))
        self.segment_status.configure(
            text="{0}{1}{2}".format(
                STATUS_LABELS[status],
                " · {0} pending".format(pending) if pending else "",
                " · {0} suppressed by glossary".format(suppressed) if suppressed else "",
            ),
            style="{0}.Status.TLabel".format(status.title()),
        )
        if self.selected_change_id and not any(c.change_id == self.selected_change_id for c in changes):
            self.selected_change_id = None
        if self.selected_change_id is None:
            first_pending = next((c for c in changes if c.decision == PENDING), None)
            self.selected_change_id = first_pending.change_id if first_pending else (changes[0].change_id if changes else None)

        # --- text with highlights
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        if self.preview_var.get():
            self.text.insert("1.0", render_segment(segment, enabled))
        else:
            self.text.insert("1.0", segment.text)
            for change in changes:
                start = "1.0+{0}c".format(change.start)
                end = "1.0+{0}c".format(change.end if change.end > change.start else change.start + 1)
                tag = "rejected" if states[change.change_id] == STATE_REJECTED else change.check
                self.text.tag_add(tag, start, end)
                if change.change_id == self.selected_change_id:
                    self.text.tag_add("selected", start, end)
                    self.text.see(start)
        self.text.configure(state=tk.DISABLED)

        self.reject_flagged_button.configure(
            state=tk.NORMAL if any(c.flagged and c.decision == PENDING for c in changes) else tk.DISABLED
        )

        # --- cards
        self.cards_frame.clear()
        self.cards = {}
        if not changes:
            message = {
                STATUS_QUEUED: "Waiting for the evaluation of this segment...",
                "running": "Evaluating...",
                STATUS_ERROR: "The evaluation failed for this segment:\n" + "\n".join(
                    "• {0}: {1}".format(CHECK_LABELS[c], r.error)
                    for c, r in segment.results.items() if r.status == "error" and enabled.get(c)),
                STATUS_CLEAN: "No changes proposed for this segment." if not segment.is_blank else "Blank segment.",
            }.get(status, "No changes to show with the current filters.")
            ttk.Label(self.cards_frame.inner, text=message, style="Muted.TLabel", wraplength=560,
                      justify=tk.LEFT, padding=(12, 16)).pack(anchor="w")
            return
        for change in changes:
            card = ChangeCard(self.cards_frame.inner, change, segment.text, states[change.change_id],
                              on_select=self._card_selected, on_decide=self.decide,
                              on_add_to_glossary=self.add_to_glossary if self.on_add_to_glossary else None)
            card.pack(fill=tk.X, padx=(0, 4), pady=(0, 6))
            card.refresh(states[change.change_id], selected=change.change_id == self.selected_change_id)
            self.cards[change.change_id] = card
        selected = self.cards.get(self.selected_change_id)
        if selected is not None:
            self.after_idle(lambda: self.cards_frame.scroll_to_widget(selected))

    def _card_selected(self, change):
        self.selected_change_id = change.change_id
        self._refresh_cards_only()

    def _refresh_cards_only(self):
        chapter, segment = self._current_segment()
        if segment is None:
            return
        states = change_states(segment, self.project.enabled)
        for change_id, card in self.cards.items():
            card.refresh(states.get(change_id, STATE_PENDING), selected=change_id == self.selected_change_id)
        self.text.configure(state=tk.NORMAL)
        self.text.tag_remove("selected", "1.0", tk.END)
        for change_id, card in self.cards.items():
            change = card.change
            start = "1.0+{0}c".format(change.start)
            end = "1.0+{0}c".format(change.end if change.end > change.start else change.start + 1)
            if states.get(change_id) == STATE_REJECTED:
                self.text.tag_remove(change.check, start, end)
                self.text.tag_add("rejected", start, end)
            else:
                self.text.tag_remove("rejected", start, end)
                self.text.tag_add(change.check, start, end)
            if change_id == self.selected_change_id:
                self.text.tag_add("selected", start, end)
                self.text.see(start)
        self.text.configure(state=tk.DISABLED)
        self._refresh_tree_row(chapter, segment)
        pending = sum(1 for change in self._visible_changes(segment) if change.decision == PENDING)
        status = self._segment_status(chapter, segment)
        self.segment_status.configure(
            text="{0}{1}".format(STATUS_LABELS[status], " · {0} pending".format(pending) if pending else ""),
            style="{0}.Status.TLabel".format(status.title()),
        )

    # ---------------------------------------------------------- decisions
    def decide(self, change, decision, group=None):
        chapter, segment = self._current_segment()
        if segment is None:
            return
        self.project.decide(chapter.index, segment.index, change, decision, group=group)
        self.selected_change_id = change.change_id
        self._refresh_cards_only()
        self._after_decision()
        # move on to the next pending change for a fast keyboard flow
        self.step_change(1, pending_only=True)

    def add_to_glossary(self, change):
        """Reject ``change`` and protect its original wording (the "Add to glossary" link)."""
        self.on_add_to_glossary(change.original_text)
        self.decide(change, REJECTED)

    def decide_selected(self, decision):
        card = self.cards.get(self.selected_change_id)
        if card is not None:
            self.decide(card.change, decision)

    def decide_all(self, decision):
        chapter, segment = self._current_segment()
        if segment is None:
            return
        group = new_decision_group()
        for change in self._visible_changes(segment):
            self.project.decide(chapter.index, segment.index, change, decision, group=group)
        self.render_segment()
        self._after_decision()

    def reject_flagged(self):
        """Reject every shown change the hallucination guard flagged that is still pending."""
        chapter, segment = self._current_segment()
        if segment is None:
            return
        flagged = [c for c in self._visible_changes(segment) if c.flagged and c.decision == PENDING]
        group = new_decision_group()
        for change in flagged:
            self.project.decide(chapter.index, segment.index, change, REJECTED, group=group)
        if flagged:
            self.render_segment()
            self._after_decision()

    def undo(self):
        """Revert the last decision (group) and show the first change it touched.

        Returns the reverted log entries, or None when the log was empty.
        """
        if self.project is None:
            return None
        undone = self.project.undo()
        if not undone:
            return None
        first = undone[0]
        self.show_change(first["chapter"], first["segment"], first["change_id"])
        self._after_decision()
        return undone

    def show_change(self, chapter_index, segment_index, change_id):
        """Select ``change_id`` in its segment and scroll it into view."""
        if self.project is None or self.project.find(chapter_index, segment_index)[1] is None:
            return
        self.select_segment(chapter_index, segment_index, change_id=change_id)
        card = self.cards.get(change_id)
        if card is not None:
            self.cards_frame.scroll_to_widget(card)

    def _after_decision(self):
        self.summary_var.set(self._summary_line(self.project.progress()))
        chapter, segment = self._current_segment()
        if segment is not None:
            self._refresh_tree_row(chapter, segment)
        self.master.schedule_save()

    def step_change(self, delta, pending_only=False):
        ids = list(self.cards.keys())
        if not ids:
            return
        if self.selected_change_id not in ids:
            self.selected_change_id = ids[0]
        else:
            index = ids.index(self.selected_change_id)
            candidates = ids[index + 1:] if delta > 0 else list(reversed(ids[:index]))
            if pending_only:
                candidates = [cid for cid in candidates if self.cards[cid].change.decision == PENDING]
            if not candidates:
                if pending_only:
                    return
                candidates = [ids[index]]
            self.selected_change_id = candidates[0]
        self._refresh_cards_only()
        card = self.cards.get(self.selected_change_id)
        if card is not None:
            self.cards_frame.scroll_to_widget(card)

    def step_segment(self, delta):
        if self.project is None:
            return
        keys = [(chapter.index, segment.index) for chapter, segment in self.project.all_segments()]
        if not keys:
            return
        if self.current not in keys:
            target = keys[0]
        else:
            index = keys.index(self.current) + delta
            if not 0 <= index < len(keys):
                return
            target = keys[index]
        self.select_segment(*target)

    def jump_to_next_pending(self):
        if self.project is None:
            return
        target = self._first_segment_with(lambda status: status == STATUS_READY, after=self.current)
        if target is None:
            target = self._first_segment_with(lambda status: status == STATUS_READY)
        if target is None:
            self.master.host.set_status("No segment is waiting for a decision.")
            return
        self.select_segment(*target)
