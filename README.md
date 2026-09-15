# TextEnhanceAI - Editor with Local LLM Integration

TextEnhanceAI is a local desktop editor that uses [Ollama](https://ollama.com/) to proofread, rewrite, translate, and improve text without sending it to a cloud service. Version 0.13 adds a structured review workflow so every suggestion can be accepted or rejected before it changes the document. The app can also use a large model on **your own GPU server** through a self-hosted relay, so a 27B model behind a firewall is one click away while your text still never touches a third-party service (see [Remote GPU models](#remote-gpu-models-through-your-own-relay)).

![TextEnhanceAI v0.13 review screen](https://github.com/wenrolland/TextEnhanceAI/blob/main/TextEnhanceAI-v0.13.png)

## Key features

- **Sentence-by-sentence review:** changed sentences are shown separately instead of combining red and green words in the editor.
- **Individual change groups:** expand a sentence to accept or reject specific word or punctuation changes.
- **Clear visual differences:** removed text is labeled and struck through; added text is labeled and underlined, so color is not the only indicator.
- **Visible decisions:** accepted and rejected states are bold green and red; pending and partially accepted states are bold blue and orange.
- **Safe application:** the source remains unchanged while suggestions are reviewed. Apply is enabled only after every change has a decision.
- **Undo:** restore the exact text from before the most recently applied review.
- **Cancellable local generation:** Ollama runs in the background, the editor stays responsive, and partial cancelled responses are discarded.
- **Editing modes:** Grammar, Proofread, Natural, Streamline, Awkward, Rewrite, Concise, Polish, Improve, Translate, and Custom.
- **Model status:** see whether Ollama is connected, unavailable, or missing a local model.
- **Local or remote backend:** switch between local Ollama models and a model hosted on your own GPU server via the bundled relay; the choice, relay address, and last model per backend are remembered.
- **Live progress:** the status bar shows elapsed time and characters received while a model works, and truncated responses are reported instead of silently applied.
- **Automatic review of whole manuscripts:** upload a `.txt`, let the app split it into chapters and segments, run spelling, grammar and expression checks on every segment in the background, and review each proposed change with a one-line explanation (see below).
- **Consistent design:** a single visual theme (header with mode switch, cards, colour-coded checks and states) across the quick editor, the review screens and the dialogs.
- **Markdown history:** each successful proposal, individual decision, and final result is logged to a local scratchpad.

## Requirements

- Python 3.8 or newer
- Ollama installed and running
- At least one local Ollama model

Install the Python dependency:

```powershell
pip install -r requirements.txt
```

Pull a model suited to your hardware:

- GPU with approximately 6–8 GB of VRAM: `ollama pull llama3.1:8b`
- CPU-only or lower-resource computer: `ollama pull qwen3:1.7b`

The app defaults to `llama3.1:8b`. Select another installed model from the Model dropdown or change the default with the `TEAI_MODEL` environment variable.

## Run the app

```powershell
python TextEnhanceAI.py
```

Using `uv` is also supported:

```powershell
uv venv
uv pip install -r requirements.txt
uv run python TextEnhanceAI.py
```

## Automatic review of a manuscript

Switch to **Automatic review** in the header to process a whole text instead of a pasted passage.

1. **Choose a `.txt` file.** The start screen previews how the text will be split: chapters are detected from Markdown headings, `Chapter 3` / `Kapitel 3` lines, numbered titles, ALL-CAPS titles or scene breaks (`* * *`); long texts without structure are split by size, or you can ask the model to find chapter boundaries. Each chapter is stored as its own file.
2. **Pick the checks.** *Spelling*, *Grammar* and *Expression* can be enabled individually (and optionally auto-accepted). Each check runs independently on the original text of every segment (paragraph-aligned chunks of about 1,800 characters), so you can compare what each one would change.
3. **Author's instructions (optional).** Standing rules every check must respect, one per line, e.g. `British spelling`, `keep dialect inside dialogue`, `never touch quotations`. They are appended to every request and to the explanation prompt and take precedence over the built-in rules where they conflict; the last used set pre-fills the next project. On the project screen, *Options...* edits them later and offers to re-evaluate all segments (existing decisions are lost). **Protected terms** (same card) shield names, invented words and technical terms: one per line, a trailing `*` covers every word starting with the stem (`hyper*`). They are named in every request and any proposed change touching one is dropped before you see it (counted as *suppressed by glossary* in the status line and the report). *Add to glossary* on a change card rejects that change and protects its wording from then on. A **hallucination guard** marks changes that look like added content rather than corrections — a replacement far longer than the original, two or more content words that appear nowhere in the segment, or a whole-phrase rewrite from the spelling/grammar check — with an amber *⚠ check this* badge (hover for the reason); the segment shows ⚠ in the tree while such changes are pending, *Reject flagged* clears them at once, and the report marks them *⚠ possibly invented*.
4. **Start.** The evaluation runs in the background with the parallelism you chose; segments become reviewable as soon as their checks are done, and you can pause, resume, close the app and come back later — everything is saved in `<name>.teai/project.json` next to the manuscript.
5. **Review.** The left tree lists chapters and segments with their state. The segment text highlights every proposed change in the colour of its check; below it, one card per change shows the before/after diff, the model's short explanation and Accept/Reject buttons. Filters show or hide checks, *Accept all shown* / *Reject all shown* handle a segment at once, and *Preview result* renders the segment with the current decisions. Every card carries a grey tag with the *kind* of edit (whitespace, punctuation, capitalization, spelling, word choice, insertion, deletion, rewrite); the *Kinds ▾* menu hides kinds you do not want to see (a view filter only — hidden changes still count and are still applied if accepted, and the choice is saved with the project) and offers *Accept all…* / *Reject all…* per kind across the whole project, confirmed with the count and undoable in one step. *Undo* (`Alt+Z`) reverts the last accept or reject — a bulk action (*Accept all shown*, *Reject all shown*, *Reject flagged*) is reverted as a whole — and jumps to the change it restored; *Decisions...* lists every decision made so far (newest first) with double-click to jump to the change. The log is saved with the project, so undo still works after reopening it. *Re-evaluate* in the segment header (or right-click a row in the tree → *Evaluate again* → all checks, one check, or the whole chapter) discards those results and their decisions and runs the checks again immediately — while an evaluation is running the tasks join the queue, otherwise it starts one; the old decisions stay in the log greyed out and are skipped by undo.
6. **Export.** Writes `reviewed/NN - Title.txt` per chapter, `<name>-reviewed.txt` for the whole manuscript, and `report.md` listing every change, its decision and explanation.

When two accepted changes touch the same words, the earlier check wins (spelling > grammar > expression) and the other one is marked *Superseded*; rejecting the winner applies the next one. Model answers that are not a plausible edit of the segment (summaries, refusals, truncated output) are rejected and retried once, and failed segments can be retried later with one click.

Keyboard shortcuts in the review: `Alt+A` / `Alt+R` accept or reject the selected change (and move to the next pending one), `Alt+Z` undoes the last decision, `Alt+↑` / `Alt+↓` select the previous or next change, `Alt+←` / `Alt+→` switch segments.

## Remote GPU models through your own relay

A GPU server that can only make outbound connections (no inbound ports) can still serve TextEnhanceAI: the [`remote/`](remote/README.md) directory contains

- a **relay** for a public server (Docker Compose with automatic HTTPS) that GPU agents dial into and clients talk to with the OpenAI-compatible API,
- a **GPU server stack** (Docker Compose: vLLM + a small agent) that hosts e.g. a Qwen 27B model and registers itself with the relay using an API key,
- the **Remote GPU (relay)** backend in the app: choose **Connection...**, enter the relay URL and your client key, press **Test connection**, save.

Pressing **Cancel** stops the GPU immediately, the connection status shows whether the model is still loading or busy, and nothing about your text is logged on the relay. Set-up, operations, and troubleshooting are documented in [remote/README.md](remote/README.md); the wire protocol in [remote/PROTOCOL.md](remote/PROTOCOL.md).

Settings are stored in `TextEnhanceAI-settings.json` next to the application (ignored by Git; the API key is stored in plain text). `TEAI_BACKEND`, `TEAI_REMOTE_URL`, `TEAI_REMOTE_API_KEY`, `TEAI_REMOTE_MAX_TOKENS`, and `TEAI_REMOTE_THINKING` seed these settings when the file has no value.

## Review workflow

1. Paste or type text in the editor.
2. Choose an editing mode and model.
3. Select **Review changes** or press `Ctrl+Enter`.
4. Review each changed sentence. Accept or reject the whole sentence, or expand **Review individual changes** for finer control.
5. Use **Previous** and **Next** to navigate.
6. Apply the reviewed text after no pending decisions remain.

Keyboard shortcuts:

- `Ctrl+Enter`: generate suggestions or apply a completed review
- `Alt+A`: accept the current sentence
- `Alt+R`: reject the current sentence
- `Alt+Left` / `Alt+Right`: previous or next suggestion

Generated `TextEnhanceAI-scratchpad_*.md` files and `TextEnhanceAI-settings.json` are stored next to the application and ignored by Git. Automatic-review projects live in a `<manuscript>.teai/` folder next to the manuscript.

## Development and tests

The launcher remains `TextEnhanceAI.py`. Pure editing logic and model access live in `core/` (`ollama_service.py`, `remote_service.py`, shared contract in `backend.py`, persisted `settings.py`, manuscript splitting in `chunking.py`, the automatic review model/runner in `workflow.py`), Tkinter screens live in `ui/` (`app.py`, `review_panel.py`, `connection_dialog.py`, `workflow_screen.py`, shared `theme.py`), and the relay and GPU agent live in `remote/`.

Install test dependencies and run the suite:

```powershell
uv pip install -r requirements-dev.txt
uv run pytest -q
```

The automated suite uses fake Ollama responses and does not require a running model. Version 0.13 is verified with Ollama Python client 0.6.1; a live Ollama smoke test is still recommended for release verification. The same command also runs the relay, agent, and end-to-end suites under `remote/` (they need `aiohttp`, included in `requirements-dev.txt`).

## Contact

**Email:** [wenrolland@designecologique.ca](mailto:wenrolland@designecologique.ca)

## Updates

- **Unreleased:** Adds the automatic manuscript review (chapter/segment splitting, spelling/grammar/expression checks with explanations, resumable background evaluation, export and report), a shared visual theme, the remote backend (relay + GPU agent + vLLM stack), a Connection dialog with live testing, persisted settings, progress display, and truncation detection.
- **Version 0.13:** Adds structured sentence and word-group review, safe mixed decisions, cancellation, connection status, enhanced scratchpads, undo, and automated tests.
- **Version 0.12:** Adds local model selection and background generation.
- **Version 0.11:** Preserves line breaks after editing.
