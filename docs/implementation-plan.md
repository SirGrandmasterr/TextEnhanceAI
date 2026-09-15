# TextEnhanceAI — implementation plan for the feature roadmap

This document turns the 30-item roadmap into an ordered set of work packages,
each with a ready-to-paste prompt for a Claude Code session. Numbers in
brackets (`[#12]`) refer to the roadmap numbering. File and symbol names are
the ones in the repository as of commit `39cc35e`.

---

## 0. How to use this plan

### Ground rules for every session

Prepend this **standard preamble** to every prompt below (or keep it in
`CLAUDE.md`; it repeats what `AGENTS.md` already says plus a few things the
roadmap depends on):

```text
Read AGENTS.md first and follow it. Additional rules for this task:
- Work on a branch named as given in the task; keep the commit(s) focused and in imperative mood.
- Keep Tk out of core/. Every new core behaviour needs a pytest in tests/ (or remote/*/tests) that
  runs without a live model; use the FakeService pattern in tests/test_workflow.py.
- Project persistence: project.json must stay loadable by the current code. If you change the
  saved shape, bump PROJECT_FORMAT in core/workflow.py and make from_dict tolerate old files
  (missing keys → defaults). Never break resumability of a half-evaluated project.
- Settings: new fields go on AppSettings in core/settings.py, into _PERSISTED, with a safe default
  and an env-var seed (TEAI_*) where it makes sense.
- Prompts to the model live in core/ (core/prompts.py, core/workflow.py CHECK_INSTRUCTIONS etc.),
  never in ui/. Model-facing text stays English; user-facing UI text goes through ui/i18n.tr()
  once that helper exists.
- The relay and agent must never log prompt or output text.
- Finish with: `pytest -q` output, a short list of manual test steps (what to click/type,
  expected result), and a summary of any limitation you left in.
```

### Conventions used below

- **Size**: S = under half a day, M = a day, L = several days.
- **Depends on**: packages that must be merged first.
- **Branch**: suggested branch name.
- Every prompt assumes the standard preamble above.

### Phase order and why

| Phase | Packages | Why this order |
|-------|----------|----------------|
| 1 Foundations | 27, 30a, 3a, 15 | A recorded-response test suite must exist before prompts are touched; the i18n helper must exist before more UI strings are added; change classification is reused by 5 later packages; the prompt-order change for prefix caching is a prompt change that needs the recorded suite to measure. |
| 2 Check quality | 1, 2, 5, 13, 14 | These change what the model is asked and how answers are parsed. They share the "project instructions" plumbing from 1/2. |
| 3 Review experience | 6, 3b, 9, 7, 8, 10, 12, 4 | UI work on top of a stable core. 6 and 7 change the decision model and should land before 8/10 render it. 12 and 4 are the largest and go last. |
| 4 Documents and quick editor | 11, 18, 17, 19, 20 | Import/export first (it defines the file model 19 reuses), then the editor features. |
| 5 Operations | 21+16, 22, 25, 23, 24, 26 | Usage accounting is a prerequisite for the throughput display; profiles before keyring (keyring stores per profile); packaging last because it freezes the settings location. |
| 6 Hardening | 28, 29, 30b | Long-run test after the runner stopped changing; accessibility and translations after the screens stopped changing. |

Packages inside a phase are ordered by value-for-effort; they are independent
unless "Depends on" says otherwise, so they can run in parallel branches.

---

## Phase 1 — Foundations

### P1.1 Recorded live-model test suite `[#27]`

- **Size**: M · **Branch**: `test/recorded-responses` · **Depends on**: —
- **Why first**: every later change to `CHECK_INSTRUCTIONS`, `build_explanation_messages`, `parse_explanations`, `sanity_check_proposal` or message ordering needs a regression net that uses real `qwen-27b` output shapes, not the hand-written `FakeService` strings.
- **Design**
  - `tests/recorded/` holds cassettes: one JSON per sample, `{"model": ..., "requests": [{"key": <sha256 of canonical json(messages, max_tokens)>, "messages": [...], "response": "..."}]}`.
  - `tests/recorded_service.py`: `RecordedService(cassette)` with the backend contract (`stream_edit`, `generate`, `list_models`); raises a clear `KeyError` naming the unmatched request when a prompt changed, so the failure says "re-record".
  - `scripts/record_responses.py`: runs the sample texts (`tests/recorded/samples/*.txt`, German + English, 6–10 segments with known typos/comma errors/awkward phrasing) through `run_check` against the configured live backend (`AppSettings.load`) and writes cassettes. Not run in CI.
  - Tests assert on **parsed** outcomes (number and kind of changes, explanation presence, no sanity-check rejections), not exact strings.
- **Acceptance**: `pytest -q` passes offline; `python scripts/record_responses.py` re-records against the relay; a deliberately changed prompt fails with a message that says which request is unrecorded.

```text
Branch: test/recorded-responses

Build a recorded-response test harness for the automatic review pipeline in core/workflow.py.

1. Add tests/recorded_service.py with class RecordedService implementing the backend contract from
   core/backend.py (display_name, list_models, connection_summary, no_models_hint, stream_edit,
   generate). It loads a cassette JSON and answers each request by looking up a key = sha256 of the
   canonical JSON of (model, messages, max_tokens). stream_edit must build its messages via
   core.backend.build_messages so the key matches what the real backends send. On a miss raise
   KeyError with the check name, the first 80 chars of the user message and the hint
   "run scripts/record_responses.py to re-record".
2. Add scripts/record_responses.py: loads AppSettings from the usual settings file, builds the
   real service (ui/app.py shows how RemoteService/OllamaService are constructed from settings —
   factor that into core if needed so the script does not import Tk), and for every
   tests/recorded/samples/*.txt runs run_check for each of CHECKS with explain=True, capturing every
   (messages, response) pair through a thin recording wrapper around the service. Write
   tests/recorded/<sample>.<model-slug>.json. Print a summary table (sample, check, changes, explained).
3. Add 6 sample texts (3 German, 3 English, 300–900 chars each) with planted errors: typos, umlaut
   mistakes, a missing comma before a subordinate clause, a tense slip, one awkward phrase, plus a
   proper noun that must not be changed.
4. Add tests/test_recorded.py that skips when no cassette exists for a sample and otherwise asserts:
   run_check returns status "done", the planted typo is among the spelling changes, the proper noun is
   not touched by any check, explanations are non-fallback for at least half of the changes, and
   sanity_check_proposal never rejected the answer.
5. Document the workflow in AGENTS.md (Testing Guidelines): when to re-record, that cassettes are
   committed, and that they contain sample text only (no manuscripts).
Do not run the recording script yourself; leave the cassettes empty and make sure the suite passes
with skips.
```

### P1.2 i18n plumbing (no translations yet) `[#30a]`

- **Size**: S · **Branch**: `feat/i18n-plumbing` · **Depends on**: —
- **Design**: `ui/i18n.py` with `tr(text, **kwargs)`, a `locales/de.json` (empty for now), `set_language(code)`, and `AppSettings.ui_language` (`auto|en|de`, seeded by `TEAI_LANG`). `auto` uses `locale.getdefaultlocale()`. Only the helper and the settings field land now; the actual wrapping of strings is P6.3.

```text
Branch: feat/i18n-plumbing

Add a minimal translation helper so later UI work can be localised without a rewrite.

1. Create ui/i18n.py: tr(text, **kwargs) returns the translation for the current language from a
   flat JSON dictionary (English source string → translated string), falling back to the source
   string, then applies str.format(**kwargs) when kwargs are given. set_language(code) loads
   locales/<code>.json next to the ui package; unknown codes and missing files silently mean English.
   current_language() returns the active code.
2. Add ui_language: str = "auto" to AppSettings (persisted, seeded by TEAI_LANG). Resolve "auto" via
   locale.getdefaultlocale() → "de" if it starts with "de", else "en". Call set_language at app start
   (ui/app.py) before any widget is built.
3. Create an empty locales/de.json ({}), and a scripts/extract_strings.py that greps ui/ for tr("...")
   calls and prints the source strings that are missing from a given locale file.
4. Add a "Language" combobox (Auto / English / Deutsch) to ConnectionDialog (ui/connection_dialog.py)
   or a new small Preferences section; changing it saves settings and shows a status line
   "Restart TextEnhanceAI to apply the language."
5. Wrap only the strings in ui/connection_dialog.py with tr() as a worked example; leave the other
   screens for a later task. Tests: tests/test_i18n.py covering fallback, format kwargs, and unknown
   language.
```

### P1.3 Change classification helper `[#3a]`

- **Size**: S · **Branch**: `feat/change-kinds` · **Depends on**: —
- **Design**: pure function `classify_change(original, proposed) -> kind` in `core/workflow.py` (or a new `core/change_kinds.py`), kinds in a fixed order: `whitespace`, `punctuation`, `capitalization`, `spelling`, `word_choice`, `insertion`, `deletion`, `rewrite`. Stored on `Change.kind`, computed on load when missing (no format bump). The filter UI is P3.2 (`[#3b]`).

```text
Branch: feat/change-kinds

Add a classification of each Change in core/workflow.py so the UI can later filter or bulk-decide
by kind.

1. Add CHANGE_KINDS = ("whitespace", "punctuation", "capitalization", "spelling", "word_choice",
   "insertion", "deletion", "rewrite") with CHANGE_KIND_LABELS, and classify_change(original_text,
   proposed_text) -> kind using these rules in order:
   - both sides equal after collapsing whitespace → whitespace
   - equal after removing punctuation (use unicodedata category P*) and collapsing whitespace → punctuation
   - equal after casefold → capitalization
   - empty original → insertion; empty proposed → deletion
   - single word on both sides (split on whitespace) and Levenshtein distance ≤ 2 (write a small
     helper, no dependency) → spelling
   - both sides ≤ 3 words → word_choice
   - otherwise rewrite
2. Add kind: str = "" to Change, fill it in extract_changes, persist it in to_dict, and in from_dict
   compute it when the saved value is missing or unknown.
3. Extend Project.progress() with per_kind counters and add a "by kind" line per check to
   build_report().
4. Tests in tests/test_workflow.py: one example per kind, a mixed diff of a real German sentence
   (comma insertion + typo + rephrase), and a round trip through to_dict/from_dict with and without
   the field.
```

### P1.4 Prompt order for prefix caching `[#15]`

- **Size**: S · **Branch**: `perf/prefix-cache-order` · **Depends on**: P1.1
- **Why**: vLLM's prefix cache only helps when requests share a *prefix*. Today `build_messages` puts the instruction before the text, so the three checks of one segment share only the system prompt. Putting the text first means the second and third check of a segment reuse the whole segment's KV cache. The runner already orders tasks `(segment, check)`, so consecutive workers hit the same prefix.

```text
Branch: perf/prefix-cache-order

Make the automatic review's requests prefix-cache friendly and document the ordering guarantee.

1. In core/backend.py add build_messages(instruction, text, text_first=False). When text_first is
   True the user message is "Text:\n{text}\n\nInstruction:\n{instruction}" and the system prompt gets
   one extra sentence: "The instruction follows the text." Keep the default (instruction first) for
   the quick editor so existing behaviour and the recorded cassettes for it do not change.
2. Add stream_edit(..., text_first=False) to OllamaService and RemoteService (pass-through to
   build_messages). run_check in core/workflow.py uses text_first=True.
3. In ProjectRunner.start, keep the task order (chapter, segment, check) and add a docstring note
   that this order is what lets vLLM reuse the segment prefix; add a test that the queue order groups
   checks of one segment together.
4. You cannot reach the model, so do not re-record cassettes. Update tests/test_recorded.py to
   compute keys with text_first=True for the review checks and state in the PR description that the
   review cassettes need re-recording with scripts/record_responses.py.
5. In remote/gpu-agent/README.md, add a note that --enable-prefix-caching should be on in the vLLM
   command (check docker-compose.yml and add the flag if it is missing), and in remote/PROTOCOL.md a
   short "Prefix caching" paragraph explaining the client-side ordering.
```

---

## Phase 2 — Automatic review: quality of the checks

### P2.1 Per-project style guide / author instructions `[#1]`

- **Size**: S · **Branch**: `feat/style-guide` · **Depends on**: —
- **Design**: `ProjectOptions.style_guide: str` (free text), shown as a multi-line box on the `StartView` card and editable from the project view (Options…). `run_check` appends it to every check instruction as an "Author's instructions" block; the explanation prompt gets it too so explanations respect it. Changing it on a running project offers "Re-evaluate all" (uses P3.3 when available; until then, it clears results).

```text
Branch: feat/style-guide

Let the author give standing instructions that every check must respect.

1. Add style_guide: str = "" to ProjectOptions (to_dict/from_dict; old projects → ""). Also add
   AppSettings.default_style_guide (persisted) that pre-fills the field for new projects.
2. In core/workflow.py add build_check_instruction(check, options) returning
   CHECK_INSTRUCTIONS[check] plus, when options.style_guide.strip() is non-empty:
   "\n\nAuthor's instructions (they take precedence over the rules above where they conflict):\n"
   + the text, one rule per line, trimmed to 1,500 characters. Use it in run_check (pass options or
   the style guide string through; keep the run_check signature backward compatible with a keyword
   argument). Append the same block to build_explanation_messages so explanations do not argue
   against the author's rules.
3. UI (ui/workflow_screen.py StartView): a 4-line Text widget "Author's instructions" with the
   placeholder examples "British spelling · keep dialect inside dialogue · never touch quotations".
   ProjectView: an "Options…" button opening a small dialog with the same box; saving it stores the
   option and, if any results exist, asks "Re-evaluate all segments with the new instructions?
   (existing decisions on unchanged text are lost)". Yes → clear all results and resume the runner.
4. Tests: instruction assembly with and without a style guide, trimming, and that
   Project.to_dict round-trips the field. Extend FakeService-based run_check tests to assert the
   author block is in the sent instruction.
```

### P2.2 Glossary / do-not-touch list `[#2]`

- **Size**: M · **Branch**: `feat/glossary` · **Depends on**: P1.3 (uses `kind` for the post-filter message), P2.1 (shares the options dialog)
- **Design**: `ProjectOptions.glossary: List[str]` (one term per line; supports a trailing `*` wildcard for inflections, e.g. `Nyxara*`). Two enforcement layers: the prompt lists the terms ("Never change these spellings"), and a post-filter in `run_check` drops any change whose original text contains a glossary term (case-sensitive by default) and records it in `CheckResult.suppressed` so the report can show how often the model tried. Also an "Add to glossary" action on a change card that rejects the change and appends its original text.

```text
Branch: feat/glossary

Protect names, invented words and technical terms from the checks.

1. Add glossary: list[str] = [] to ProjectOptions (persist; old projects → []), plus
   AppSettings.default_glossary. Terms are one per line; a trailing "*" matches any suffix
   (word-boundary at the start). Provide parse_glossary(text) -> list and glossary_matcher(terms)
   -> callable(str) -> bool in core/workflow.py (compiled regex, Unicode word boundaries).
2. Prompt: build_check_instruction (from feat/style-guide) appends "Protected terms — never change
   their spelling, capitalization or form: term1, term2, …" (cap at 200 terms / 2,000 chars; beyond
   that rely on the post-filter only).
3. Post-filter in run_check after extract_changes: drop a change when the matcher hits
   change.original_text or, for insertions, the 20 characters around it; keep the dropped ones in
   CheckResult.suppressed (list of Change dicts, persisted) and count them in progress() as
   "suppressed". Report: a line "Glossary suppressed N proposed changes" per check.
4. UI: glossary box in the StartView card and the Options dialog; on every ChangeCard a small
   "Add to glossary" link (rejects the change, appends original_text, saves). Tree tooltip or status
   line shows suppressed counts.
5. Tests: matcher semantics (exact, wildcard, umlauts, case), post-filter drops/keeps, persistence
   round trip, prompt contains the terms, cap behaviour.
```

### P2.3 Model-side hallucination guard `[#5]`

- **Size**: S · **Branch**: `feat/hallucination-guard` · **Depends on**: P1.3
- **Design**: pure function `flag_suspicious(segment_text, change) -> reason|None` in core; a change is *suspicious* when (a) proposed text is > 1.6× the original span length + 12 chars, or (b) it introduces ≥ 2 content words (len ≥ 4, not stopwords) that occur nowhere in the segment, or (c) it is a `rewrite` kind under the spelling/grammar check. Stored as `Change.flags: list[str]`. UI: amber badge "check this" on the card, tree glyph, and a "Reject all flagged in this segment" action. The expression instruction also gets the sentence "Never add facts, names, clauses or sentences that are not in the text."

```text
Branch: feat/hallucination-guard

Flag proposed changes that add content instead of correcting it.

1. In core/workflow.py add flags: list[str] = [] to Change (persist; default []). Add
   flag_suspicious(segment_text, change) -> str|None implementing: length growth
   (len(proposed) > 1.6*len(original) + 12), novelty (≥ 2 words of length ≥ 4 in proposed that do
   not appear case-insensitively anywhere in segment_text — use a small German+English stopword
   list to ignore function words), and kind == "rewrite" for CHECK_SPELLING/CHECK_GRAMMAR. Return a
   short reason id: "growth", "novel_words", "rewrite_in_strict_check". Call it in run_check after
   the glossary filter and append the reason to change.flags.
2. Add to CHECK_INSTRUCTIONS[CHECK_EXPRESSION]: "Never add facts, names, clauses or sentences that
   are not already in the text." and to the grammar instruction "Do not rewrite sentences."
3. progress(): count "flagged"; build_report(): mark flagged changes with "⚠ possibly invented".
4. UI: ChangeCard shows an amber "⚠ check this" badge with the reason as tooltip; the Treeview row
   glyph column (see feat/change-kinds) shows ⚠ when a segment has pending flagged changes;
   ProjectView gets "Reject flagged" next to Accept all / Reject all.
5. Tests: each rule fires on a constructed example and does not fire on a plain typo fix, a comma
   insertion or a two-word rephrase; persistence round trip.
```

### P2.4 Combined check pass with structured output `[#13]`

- **Size**: L · **Branch**: `feat/combined-pass` · **Depends on**: P1.1 (must re-record), P2.1, P2.2, P2.3
- **Design decision**: one request per segment that returns an **edit list** with a category and a reason per edit, not three corrected full texts (which would triple output tokens). Anchoring: each edit carries `original` and `replacement`; the client locates `original` in the segment with `text.find(original, cursor)` in order; any miss → the whole segment falls back to the three-pass mode (`run_check`). Output is requested as JSON Schema via the OpenAI-compatible `response_format` field (vLLM structured outputs); a 400 from the server disables it for the session and plain JSON parsing is used. Results are still stored as three `CheckResult`s (one per category, `method="combined"`), so the review UI, merge rules and report do not change. Explanations come for free from the `reason` field (the explanation pass is skipped).
- **Risk**: anchoring on repeated words (`original` occurs twice). Mitigation: the schema asks for `original` to include enough surrounding words to be unique; ambiguous edits → fallback.
- **Options**: `ProjectOptions.evaluation_mode: "combined" | "separate"` (default combined; separate stays as fallback and for Ollama models that ignore `response_format`).

```text
Branch: feat/combined-pass

Add a single-request evaluation mode that returns all check categories at once, with the current
three-pass mode kept as the fallback.

1. core/workflow.py:
   - COMBINED_SCHEMA: JSON schema for {"edits": [{"category": "spelling|grammar|expression",
     "original": str, "replacement": str, "reason": str}]}. build_combined_messages(text, options)
     builds one system+user message pair: system = EXPLANATION_SYSTEM_PROMPT style ("answer with a
     single JSON object"), user = the three CHECK_INSTRUCTIONS as numbered category rules, the
     author's instructions and protected terms (reuse build_check_instruction pieces), then the
     text, then the required output shape with one example, and the rule that "original" must be
     the exact substring of the text, extended with neighbouring words until it is unique.
   - parse_combined(text, answer) -> dict[check, CheckResult] | None: parse JSON (reuse
     _parse_json_object), locate each edit (exact find from a moving cursor; if the substring occurs
     more than once at/after the cursor, give up on that edit), build Change objects with offsets,
     kind, flags (flag_suspicious), glossary filtering, and reason[:200] as the explanation. Return
     None when any edit cannot be anchored or the JSON is malformed.
   - run_segment_combined(service, model, text, options, cancel_event) → dict[check, CheckResult].
     On None from parse_combined or a StructuredOutputUnsupported error, fall back to the three
     run_check calls and mark results method="separate". Add CheckResult.method
     ("combined"/"separate", persisted, default "separate").
   - ProjectRunner: when options.evaluation_mode == "combined", tasks are (chapter, segment, None)
     and one worker call yields three results; emit one workflow_result event per check so the UI
     stays unchanged. pending_tasks() must still produce per-check tasks in separate mode and
     per-segment tasks in combined mode (a segment is pending when any enabled check is missing).
2. Backends: generate(..., response_format=None) on OllamaService (map to Ollama's "format" =
   schema object) and RemoteService (add "response_format": {"type": "json_schema", "json_schema":
   {"name": "teai_edits", "schema": ...}} to the body). RemoteService: if the server answers 400 and
   the body mentions response_format/guided/structured, raise StructuredOutputUnsupported
   (subclass of RemoteUnavailable) so the runner can disable it for the rest of the run (store a
   flag on the runner, not the service).
3. Options: evaluation_mode on ProjectOptions (default "combined"), radio buttons on StartView
   ("One combined request per segment (faster)" / "Three separate requests (more thorough)"),
   and an AppSettings default.
4. Tests: parser with a realistic answer (German sentence, 4 edits across categories), a repeated
   substring that anchors correctly because the model included context, an unanchorable edit that
   triggers None, fallback path with a FakeService that returns garbage for the combined request
   and normal answers for the separate ones, and the runner producing three workflow_result events
   per segment. Add a combined-mode cassette target to scripts/record_responses.py and a skip-if-
   missing test in tests/test_recorded.py.
5. README: explain the two modes and the request-count difference (≈1 vs ≈6 per segment).
```

### P2.5 Explanation batching and canned explanations `[#14]`

- **Size**: S · **Branch**: `perf/explanations` · **Depends on**: P1.3, P2.4 (only matters in separate mode)
- **Design**: in separate mode, changes of kind `punctuation`, `capitalization`, `whitespace`, and `spelling` with distance 1 get a canned, localised explanation (`CANNED_EXPLANATIONS[kind]`, language-aware via `options.language`) and are excluded from the explanation request; if no changes remain, the request is skipped. Cross-segment batching: a second queue stage — evaluation workers push `(segment, check, changes)` into an `explain` queue drained by one explainer thread that groups up to 6 entries of the same check into one request; results for a segment are emitted only once explanations arrived (or failed).

```text
Branch: perf/explanations

Cut explanation requests in the three-pass mode.

1. core/workflow.py: CANNED_EXPLANATIONS = {kind: {"en": ..., "de": ...}} for whitespace,
   punctuation, capitalization and single-letter spelling fixes (compute distance in
   classify_change and expose it as Change.distance or reuse the helper). canned_explanation(change,
   language) picks the language from options.language ("same as text" → detect crudely: if the
   segment contains typical German function words use "de", else "en"; keep it a tiny heuristic
   with a test).
2. run_check: apply canned explanations first; only the remaining changes go into
   build_explanation_messages; skip the request when nothing remains. Mark CheckResult.explained
   True when every change has a non-fallback explanation.
3. Batching: refactor run_check into evaluate (edit request → changes) and explain (explanation
   request) steps. ProjectRunner gets an explainer stage: evaluation workers enqueue the changes
   needing explanations; a single explainer thread drains the queue, groups up to 6 entries with
   the same check into one request (numbering continues across segments, with a "Segment N" header
   per group in the prompt and each segment text included once), distributes answers back, then
   emits workflow_result. Cancellation must still stop within one request. Pending explanations at
   cancel time are emitted with fallback text so the project stays consistent.
4. Tests: canned selection, request skipped when all canned, grouped prompt numbering and answer
   distribution, cancel during the explain stage still finishes cleanly, and event ordering
   (workflow_result once per (segment, check)).
```

---

## Phase 3 — Automatic review: review experience

### P3.1 Undo per decision and decision log `[#6]`

- **Size**: S · **Branch**: `feat/decision-undo` · **Depends on**: —
- **Design**: `Project.decision_log: list[{ts, chapter, segment, change_id, before, after, group}]` (persisted, appended by a single `Project.decide(...)` entry point that the UI must use). Undo (`Alt+Z`, and a toolbar button) pops the last group, restores `before`, selects that change. Bulk actions log one entry per change with a shared `group` id so one undo reverts the whole "Accept all".

```text
Branch: feat/decision-undo

Add undo for review decisions and a persisted decision log.

1. core/workflow.py: Project.decision_log: list of dicts {"ts", "chapter", "segment", "change_id",
   "before", "after", "group"} (persist; default []). Add Project.decide(chapter_index,
   segment_index, change, decision, group=None) that records and applies, and Project.undo() ->
   entries|None that reverts the last group (all entries with the same group id, or the single last
   entry) and returns what was undone. Keep the log at most 5,000 entries (drop oldest).
2. ui/workflow_screen.py: route ProjectView.decide, decide_all and (from feat/hallucination-guard)
   "Reject flagged" through Project.decide with a fresh group id for bulk actions. Add
   WorkflowScreen.undo (Alt+Z, bound in ui/app.py _bind_shortcuts like the other Alt shortcuts) and
   an "Undo" button next to Accept all; after undo, select the segment and change that was
   reverted and refresh. Update the hint line to include "Alt+Z undo".
3. A "Decisions…" button shows the log in a Toplevel Treeview (time, chapter, segment, original →
   proposed, before → after) with double-click to jump to the change.
4. Tests: decide/undo single, undo group, log cap, persistence round trip, undo on an empty log
   returns None.
```

### P3.2 Change-type filters and bulk actions `[#3b]`

- **Size**: S · **Branch**: `feat/kind-filters` · **Depends on**: P1.3, P3.1
- **Design**: check boxes per kind next to the existing per-check toggles, stored on `ProjectOptions.hidden_kinds` (hidden kinds are still applied if accepted — hiding is a view filter, not a decision). Bulk: "Reject all <kind> in project" / "Accept all <kind> in project" in a menu, executed via `Project.decide` with one group id (undoable).

```text
Branch: feat/kind-filters

Let the author hide or bulk-decide changes by kind.

1. ProjectOptions.hidden_kinds: list[str] = [] (persist). Segment.changes(enabled, hidden_kinds=())
   gets an optional filter used only by the UI; status()/progress()/render must keep counting hidden
   changes (hiding is a view filter). Add Project.changes_by_kind(kind) -> list of (chapter, segment,
   change).
2. ui/workflow_screen.py: a "Kinds" menubutton in the ProjectView header with a checkbutton per
   CHANGE_KINDS (label + count from progress()["per_kind"]); toggling updates hidden_kinds, saves
   and re-renders. Under a separator: "Accept all <kind>…" / "Reject all <kind>…" submenus that
   confirm with the count and run through Project.decide with one group id so Alt+Z reverts them.
   The ChangeCard shows the kind as a small grey tag.
3. Tests: filter does not affect render_segment/progress; bulk decide affects only pending changes
   of that kind; undo reverts the bulk action.
```

### P3.3 Re-run a single segment or check `[#9]`

- **Size**: S · **Branch**: `feat/rerun-segment` · **Depends on**: —
- **Design**: `Project.invalidate(chapter_index, segment_index, checks=None)` removes the results (and their decisions) and the runner's `pending_tasks()` picks them up on resume. UI: right-click on the tree row → "Evaluate again ▸ All checks / Spelling / Grammar / Expression / Whole chapter", plus a "Re-evaluate" button in the segment header. If the runner is idle, resume it automatically.

```text
Branch: feat/rerun-segment

Allow re-evaluating one segment, one check, or one chapter without touching the rest.

1. core/workflow.py: Project.invalidate(chapter_index, segment_index=None, checks=None) → number of
   results removed; None segment = whole chapter. Keep decision-log entries of removed results but
   mark them "stale": True so the Decisions view greys them out and undo skips them.
2. ui/workflow_screen.py: context menu on the Treeview (right-click, and the Menu key) with
   "Evaluate again" → All checks / per enabled check / Whole chapter; a "Re-evaluate" button in the
   segment header. After invalidation: refresh the row(s), schedule_save, and if no runner is active
   call resume_runner(); if one is active, add ProjectRunner.enqueue(tasks) that pushes new tasks
   while workers run (and spawns workers up to parallelism if all exited) and use it.
3. Tests: invalidate counts, pending_tasks after invalidation, runner picks up enqueued tasks
   mid-run with FakeService(delay=0.2), stale flag on log entries.
```

### P3.4 Inline editing of the proposed text and author fixes `[#7]`

- **Size**: M · **Branch**: `feat/inline-edit` · **Depends on**: P3.1
- **Design**: two things. (a) Edit a suggestion: `Change.proposed_text` becomes editable; `Change.model_proposed_text` keeps the original suggestion (persisted); the decision becomes `accepted` and `Change.edited = True`, rendered with a pencil glyph; undo restores both text and decision. (b) Author's own fix on untouched text: select a span in the segment text widget → "Add my correction…" → creates a `Change` under a pseudo-check `CHECK_AUTHOR = "author"` that is always enabled and has the highest priority. Keep `CHECKS` as the *model* checks and introduce `ALL_CHECKS = (CHECK_AUTHOR,) + CHECKS` for priority/merging so the options UI and runner ignore the author check.

```text
Branch: feat/inline-edit

Let the author tweak a suggestion before accepting it, or add a correction of their own.

1. core/workflow.py: add model_proposed_text: str (persist; default = proposed_text on load) and
   edited: bool = False to Change. Add Project.edit_change(chapter, segment, change, new_text): sets
   proposed_text, edited=True, decision ACCEPTED, logs before/after (extend the decision log entry
   with "before_text"/"after_text") so undo can restore text and decision.
2. Author changes: CHECK_AUTHOR = "author", CHECK_LABELS/DESCRIPTIONS entries, ALL_CHECKS =
   (CHECK_AUTHOR,) + CHECKS. Change.priority uses ALL_CHECKS. Segment.changes(enabled) always includes
   results[CHECK_AUTHOR]. pending_tasks/progress/report treat the author check as always enabled
   and never queued. Project.add_author_change(chapter, segment, start, end, new_text) creates the
   result lazily and a Change with kind via classify_change and explanation "Author's correction".
3. UI: on a ChangeCard a small "Edit…" button (and F2 when the card is selected) opens an entry
   pre-filled with the proposed text; Enter saves via Project.edit_change. Edited cards show ✎ and
   "edited". In the segment text widget, selecting text and pressing Ctrl+E (or "Add my
   correction…" in the right-click menu) asks for the replacement and calls add_author_change; the
   new change appears as a card under an "Author" section and is applied with top priority.
4. Tests: edit → render uses the new text; undo restores; author change beats an overlapping
   spelling change; report lists author changes; old project files load with model_proposed_text
   defaulted.
```

### P3.5 Whole-chapter view and project-wide pending list `[#8]`

- **Size**: M · **Branch**: `feat/chapter-view` · **Depends on**: P3.1, P3.2
- **Design**: a `ttk.Notebook` in the right pane of `ProjectView` with tabs "Segment" (today's cards), "Chapter" (read-only `tk.Text` rendering the chapter with tags: applied = green underline, pending = amber background, rejected = strike, flagged = amber wave; click a span → selects the segment and change in the Segment tab), and "All changes" (Treeview of every pending change: chapter, segment, check, kind, original → proposed; filters by check/kind; double-click jumps; Accept/Reject on the selected rows through `Project.decide`).

```text
Branch: feat/chapter-view

Add a chapter reading view and a project-wide list of pending changes.

1. core/workflow.py: render_chapter_annotated(project, chapter) -> (text, spans) where spans is a
   list of {"start", "end", "state", "change_id", "segment", "flags"} in coordinates of the
   rendered chapter text. Pending changes render the ORIGINAL text with a span; applied ones render
   the proposed text with a span; rejected and superseded render the original with a span. Tests
   must prove that the text equals render_chapter(chapter) when no change is pending, and that
   offsets line up on a chapter with insertions and deletions in several segments.
2. ui/workflow_screen.py: turn the right pane into a ttk.Notebook with tabs "Segment", "Chapter",
   "All changes". Chapter tab: read-only Text with tags per state (use ui/theme.py colours).
   Clicking a span selects the segment and the change in the Segment tab; Alt+A/Alt+R keep working
   on the selected change. Re-render on segment_updated for the current chapter only (debounce with
   after(150)).
3. All-changes tab: Treeview of pending changes across the project with combobox filters for
   check and kind and a text filter; buttons Accept/Reject selected (multi-select) via
   Project.decide with a group id; double-click jumps to the segment.
4. Manual test steps in the PR: open a project with results, switch tabs, click a highlighted span,
   accept it, verify both tabs update.
```

### P3.6 Statistics view `[#10]`

- **Size**: S · **Branch**: `feat/statistics` · **Depends on**: P1.3
- **Design**: pure `project_statistics(project)` returning per-chapter rows (words, changes, changes per 1,000 words, accepted %, per check) plus top-20 most frequent `(original → proposed)` pairs and per-check acceptance rate; UI as a Toplevel with Treeviews (no plotting dependency), "Copy as Markdown", and the same tables appended to `report.md`.

```text
Branch: feat/statistics

Show where the manuscript is weak and which checks earn their cost.

1. core/statistics.py (pure): project_statistics(project) -> {"chapters": [{"index", "title",
   "words", "changes", "per_1000", "accepted", "rejected", "pending", "per_check": {...}}],
   "checks": {check: {"changes", "accepted", "rejected", "acceptance_rate", "per_1000"}},
   "kinds": {...}, "frequent": [{"original", "proposed", "count", "check"}] (top 20,
   case-insensitive grouping, ignoring whitespace-only changes)}. Add statistics_markdown(stats).
2. build_report() appends the Markdown tables under "## Statistics".
3. UI: "Statistics…" button in ProjectView opening a Toplevel with a Notebook: Chapters table,
   Checks table, Frequent corrections table (double-click → jump to the first occurrence), and a
   "Copy as Markdown" button.
4. Tests: per-1000 math, acceptance rate with zero changes, frequent grouping, markdown output.
```

### P3.7 Source edits during review (re-split with kept decisions) `[#12]`

- **Size**: M · **Branch**: `feat/resync-source` · **Depends on**: P3.3
- **Design**: store `source_sha256` and `source_mtime` on `Project`. On open (and on a "Check source" button), compare; if changed, offer "Re-sync". Re-sync = re-split the new text; for each new segment find an old segment with identical text (hash map) and carry over `results` and decisions; otherwise the segment is queued. Report a summary ("212 segments kept, 9 new, 3 removed") and write removed segments' pending changes to `resync-<timestamp>.md` in the project folder so nothing silently disappears.

```text
Branch: feat/resync-source

Keep decisions when the manuscript file changes underneath a project.

1. core/workflow.py: Project.source_sha256 and source_mtime (persist; set in create_project; old
   projects → ""). Project.source_changed() -> bool (missing file → False, with a reason attribute).
   resync_project(project, new_text) -> summary dict {"kept", "new", "removed", "chapters"}: split
   new_text with the project's options and chapter_mode (if chapter_mode == "model", keep the old
   chapter boundaries by title match and fall back to auto), then match segments by exact text (a
   dict from text → list of old Segment, consumed in order so duplicates pair up), carry results,
   decisions, flags and author changes; unmatched old segments with pending/accepted changes are
   written to <root>/resync-<timestamp>.md (original → proposed, state) before being dropped.
   Update source hash/mtime and add a "resync" marker entry to the decision log.
2. ui/workflow_screen.py: on open_project and via a "Check source" button, when source_changed():
   dialog "The manuscript changed since the project was created. Re-sync? Segments with identical
   text keep their results and decisions; new or edited segments are evaluated again." Yes →
   resync, refresh, resume runner if there is anything pending, status line with the summary.
3. Tests: unchanged file → no-op; edit in one paragraph → only that segment new, others kept
   including decisions; paragraph deleted with pending changes → resync-*.md written; duplicate
   segments pair in order.
```

### P3.8 Consistency check across chapters `[#4]`

- **Size**: L · **Branch**: `feat/consistency-check` · **Depends on**: P2.2, P3.4, P3.5
- **Design**: a **project-level** pass, separate from the segment checks, in two layers. (a) **Deterministic candidates** (no model): capitalised tokens grouped by edit distance (`Nyxara`/`Nixara`), hyphenation variants (`E-Mail`/`Email`), number style (`3`/`drei`), quote-mark styles, and a coarse POV/tense profile per chapter. (b) **Model triage**: candidates go to the model in batches with representative sentences; it returns JSON verdicts `{"issue": bool, "preferred": ..., "reason": ...}`. Findings are stored as `Project.consistency` with occurrences; the UI lists them in a fourth Notebook tab; "Apply preferred spelling everywhere" creates author changes (P3.4) at every occurrence, undoable as one group. Runs on demand ("Consistency…") after the segment checks, and again after a re-sync.

```text
Branch: feat/consistency-check

Add a chapter-spanning consistency pass for names, hyphenation, numbers, quotes and POV/tense drift.

1. core/consistency.py (pure): collect_candidates(project) scanning the RENDERED text of every
   segment (render_segment) and returning candidate groups: proper-noun variants (capitalised
   tokens not at sentence start, grouped by Levenshtein ≤ 2 and shared prefix ≥ 3, ignoring
   glossary-protected exact forms), hyphenation variants (same letters ignoring "-" and case),
   number-style pairs (digits vs spelled-out 0–20 in German and English), quote styles („“ vs "" vs
   »«), and a per-chapter POV/tense profile (share of ich/wir vs er/sie/es + I/we vs he/she; share
   of typical past-tense endings vs present) flagged when a chapter deviates > 25 points from the
   project mean. Every candidate carries occurrences (chapter_index, segment_index, start, end,
   text) — max 10 stored, plus a total count.
2. build_consistency_messages(batch) asks for JSON {"verdicts": [{"id", "issue": bool,
   "preferred": str, "reason": str}]} with one representative sentence per variant; batch ≤ 15
   candidates. parse_verdicts(answer) tolerant like parse_explanations. Finding dataclass with
   to_dict/from_dict; Project.consistency: list[Finding], persisted under a new top-level key
   (no PROJECT_FORMAT bump needed: older code ignores unknown keys).
3. ConsistencyRunner: background thread like ProjectRunner emitting ("consistency_progress", done,
   total) and ("consistency_finished", findings, error); cancellable; deterministic candidates are
   shown immediately with "unverified" status, model verdicts update them.
4. UI: "Consistency…" button (enabled once every segment has results), a "Consistency" Notebook
   tab listing findings (type, variants with counts, preferred, reason, status), double-click an
   occurrence to jump, and "Apply preferred everywhere" creating author changes for each
   occurrence via Project.add_author_change under one undo group; "Dismiss" hides a finding.
5. Tests: candidate collection on a synthetic 3-chapter project (name variant, hyphenation, number
   style, POV drift), glossary exemption, verdict parsing, apply-everywhere creates the right
   number of author changes and one undo group.
```

---

## Phase 4 — Documents and the quick editor

### P4.1 Import and export formats (`.docx`, `.md`, `.odt`) `[#11]`

- **Size**: L · **Branch**: `feat/document-formats` · **Depends on**: —
- **Design**: new `core/documents.py` with `load_document(path) -> LoadedDocument(text, kind, paragraphs)` and `save_document(loaded, new_text, path)`. The manuscript text handed to chunking is plain text exactly as today, so `chunking`/`workflow` do not change. The round trip works at **paragraph granularity**: `paragraphs` maps each text paragraph to its source element (docx paragraph index / odt `text:p` index / markdown line span). On export, unchanged paragraphs keep all formatting; changed paragraphs get their text replaced run-wise (first run keeps its style, other runs cleared) — inline bold/italic inside a changed paragraph is lost and the UI says so. If the paragraph count changed (the model merged or split paragraphs), the exporter writes a fresh document with the paragraph styles of the nearest original paragraph and warns. `python-docx` is an optional dependency; `.odt` is read/written with `zipfile` + `xml.etree` (stdlib); `.md` is treated as text.

```text
Branch: feat/document-formats

Import .docx/.md/.odt manuscripts into the automatic review and export the reviewed text back to
the same format.

1. core/documents.py: LoadedDocument(text, kind, paragraphs, source_path, meta) where paragraphs is
   a list of {"index": n, "start": char offset in text, "end": ..., "ref": format-specific
   locator}. load_document(path): ".txt" → read_text_file (core/chunking.py); ".md" → text as-is,
   each line block a paragraph; ".docx" → python-docx (import lazily; raise a clear error naming
   the pip package when missing), body paragraphs in order, tables ignored but kept in the file;
   headings become lines so _find_headings in chunking still detects chapters; ".odt" → unzip
   content.xml, walk text:h / text:p in document order, text with text:s/text:tab/text:line-break
   expanded. Normalise to "\n\n" between paragraphs.
2. save_document(loaded, new_text, out_path, on_warning): split new_text into paragraphs by the same
   rule; if the count matches, replace only paragraphs whose text differs (docx: keep the first run's
   formatting, set its text, remove other runs; odt: replace child text nodes of the element,
   keeping its attributes; md/txt: write text). If the count differs, write a fresh document (docx:
   copy paragraph style names from the nearest original paragraph by relative position; odt: plain
   text:p elements with the style-name of the nearest paragraph) and call
   on_warning("Paragraph structure changed; inline formatting could not be preserved").
3. Project: store source_kind and the paragraphs locator list (persist under "document"); export()
   writes <name>-reviewed.<ext> next to the existing .txt outputs and returns it in the dict.
   create_project takes the LoadedDocument.
4. UI: StartView file chooser filters for .txt/.md/.docx/.odt; the export dialog states the
   inline-formatting limitation once.
5. requirements.txt: add "python-docx>=1.1,<2" as optional under a comment; requirements-dev.txt
   installs it. Tests: build small .docx (with python-docx) and .odt (hand-written content.xml in a
   zip) fixtures in the test, round-trip with one changed paragraph → other paragraphs' runs
   untouched; count mismatch → fresh document + warning; heading detection still works from a docx
   with Heading 1 paragraphs.
```

### P4.2 Selection-only editing in the quick editor `[#18]`

- **Size**: S · **Branch**: `feat/selection-edit` · **Depends on**: —
- **Design**: if the editor has a selection when Enhance is pressed, only the selected text is sent; `EditSession` gets `selection` (char offsets); the review panel shows the selection as the document; `apply_review` replaces only that range. No word/paragraph snapping — the author chose the span.

```text
Branch: feat/selection-edit

Apply the editing mode to the selected text only, when there is a selection.

1. core/models.py: EditSession gets selection: tuple[int, int] | None = None (char offsets into the
   full editor text at the time of the request) and full_text: str = "". Keep using revision_id in
   ui/app.py to detect that the editor changed under the review.
2. ui/app.py start_review: read text_area.tag_ranges("sel"); if present, send only that text, set
   session.selection and use the status "Editing selection (N words)…". In apply_review, when
   session.selection is set, replace exactly that range (convert offsets to Tk indices via
   "1.0 + {n} chars") and re-select the replaced span; undo_applied_review must restore the previous
   full text as it does today.
3. ReviewPanel shows a one-line note "Reviewing the selected passage only" when selection is set;
   the context toggle shows the surrounding paragraph from full_text.
4. Scratchpad logging (core/scratchpad.py) records "Selection: chars a–b" in the proposal block.
5. Tests: offset ↔ Tk-index helpers in a Tk-free module (core/text_positions.py) and EditSession
   round trip.
```

### P4.3 Custom mode presets and chained modes `[#17]`

- **Size**: M · **Branch**: `feat/mode-presets` · **Depends on**: P4.2 (shares the request path)
- **Design**: `AppSettings.custom_modes: list[{name, instruction}]` and `AppSettings.chains: list[{name, steps}]`; both appear in the mode combobox under separators. A chain runs its steps sequentially, each step's *output* feeding the next, with one review at the end diffing original against final (assumption: one review, not N; the scratchpad logs every intermediate). A "Manage modes…" dialog edits presets and chains; "Save as preset…" appears after a Custom instruction has been entered.

```text
Branch: feat/mode-presets

Let users save custom instructions as named modes and run several modes in sequence.

1. core/prompts.py: build_instruction gains lookup in a caller-supplied dict of custom modes
   (function parameter, not a global), and build_chain(steps, custom_modes) -> list[str] of
   instructions with validation (unknown step name → ValueError; Translate/Custom not allowed
   inside a chain). Keep PROMPTS untouched.
2. core/settings.py: custom_modes: list[dict] and chains: list[dict] (persisted; validated on load:
   names unique, non-empty instruction, steps referencing PROMPTS or custom names only; invalid
   entries dropped with a load_error mention).
3. core/editing.py: run_chain(service, model, instructions, text, cancel_event, on_step) running
   the steps sequentially and returning the final text (testable with FakeService).
4. ui/app.py: mode combobox values = EDITING_MODES + ["— Custom modes —"] + names + ["— Chains —"]
   + chain names (separators unselectable: re-select the previous value). _get_instruction returns
   a list of instructions; start_review's worker uses run_chain, updating the status "Step 2/3:
   Polish…" and honouring cancel_event; the final EditSession diffs the original against the last
   output; the scratchpad logs each step. After a "Custom" prompt: a "Save as preset…" button next
   to the mode combobox (asks for a name). "Manage modes…" Toplevel: list/add/edit/delete presets
   and chains (chain editor = ordered listbox with Add/Remove/Up/Down).
5. Tests: chain validation, settings validation/round trip, run_chain with FakeService including
   cancellation between steps.
```

### P4.4 Editor file handling and "send to automatic review" `[#19]`

- **Size**: S · **Branch**: `feat/editor-files` · **Depends on**: P4.1
- **Design**: a File menu (Open, Save, Save As, Recent ▸, Send to automatic review) in the quick editor; `AppSettings.recent_files` (max 10); title bar shows the file name and a `•` when modified; closing with unsaved changes asks. "Send to automatic review" saves (or asks to save) and switches to the automatic mode with the file preselected in `StartView`. Uses `load_document`/`save_document` for `.docx/.md/.odt`.

```text
Branch: feat/editor-files

Give the quick editor a file concept.

1. AppSettings.recent_files: list[str] (persist, max 10, most recent first, missing files pruned on
   load). ui/app.py: a menubar with File (Open… Ctrl+O, Save Ctrl+S, Save As… Ctrl+Shift+S, Recent ▸,
   separator, Send to automatic review, separator, Quit) — keep the existing toolbar. Track
   current_path, current_document (LoadedDocument from core/documents.py) and a modified flag from
   the existing <<Modified>> handling; window title "name • — TextEnhanceAI" when modified.
2. Open uses core.documents.load_document; Save uses save_document for the loaded kind (plain write
   for .txt/.md). Closing the window or opening another file with unsaved changes asks Save/Discard/
   Cancel. Opening a file must clear any active review and reset revision tracking like
   _set_editor_text does.
3. "Send to automatic review": if unsaved or untitled, ask to save first (the automatic mode works
   on files), then switch_mode("auto") and pre-fill StartView's file path (add a
   StartView.set_file(path) that also updates the preview).
4. Tests: recent-files pruning/ordering in tests/test_settings.py; the rest is manual — list the
   steps (open a .docx, edit, save, reopen; send to review; unsaved-changes prompt).
```

### P4.5 Diff explanations in the quick review `[#20]`

- **Size**: S · **Branch**: `feat/quick-explanations` · **Depends on**: P2.5 (generic explanation helper)
- **Design**: after `build_edit_session`, an optional second request explains the hunks (reusing `build_explanation_messages` generalised to take `(start, end, original, proposed)` objects and a free-text description = the mode's instruction). Off by default (`AppSettings.quick_explanations`) because it costs a request; explanations are shown under each hunk in `ReviewPanel._render_hunks` and logged to the scratchpad.

```text
Branch: feat/quick-explanations

Show a one-line explanation per change in the quick editor's review panel.

1. core/workflow.py: generalise build_explanation_messages so the "check" argument may be a plain
   description string (used verbatim when it is not a CHECKS member) and the changes argument may
   be any objects with start/end/original_text/proposed_text. Add core/editing.py
   explain_session(service, model, session, instruction, cancel_event, language) that numbers the
   changed hunks across all ReviewItems, asks once (max_tokens 2048), and stores the text on
   ChangeHunk.explanation (new field in core/models.py, default ""), using canned explanations
   from perf/explanations where they apply.
2. AppSettings.quick_explanations: bool = False (persist, env TEAI_QUICK_EXPLAIN). ui/app.py: a
   checkbutton "Explain changes" next to the mode combobox; when on, start_review's worker calls
   explain_session after the edit completes (status "Explaining changes…", cancellable; failures are
   ignored and the review still opens).
3. ui/review_panel.py: _render_hunks shows the explanation in a smaller grey line under each hunk;
   the details toggle hides/shows them. core/scratchpad.py logs explanations in the proposal block.
4. Tests: explain_session with FakeService assigns explanations by number, ignores a malformed
   answer, honours cancel.
```

---

## Phase 5 — Backends, relay, operations

### P5.1 Usage accounting and throughput display `[#21] + [#16]`

- **Size**: M · **Branch**: `feat/usage-metrics` · **Depends on**: —
- **Design**: client side, `RemoteService.generate` requests `stream_options: {"include_usage": true}` and records the final `usage` object (Ollama: `prompt_eval_count`/`eval_count` from the final chunk) via an optional `on_usage` callback. The runner sums them into `Project.usage` (persisted) and the app keeps a session total in the status bar. Throughput: extend the relay's `/status` with a rolling chunks/s and queue depth per agent; the client polls `/status` every 10 s while a runner is active and shows "GPU ≈ 38 chunks/s · 3 queued" in the progress line. Agent-side GPU metrics come in P5.5.

```text
Branch: feat/usage-metrics

Track tokens per request, per project and per session, and surface relay throughput in the
progress line.

1. core/backend.py: UsageRecord dataclass (prompt_tokens, completion_tokens, seconds, model).
   RemoteService.generate: add "stream_options": {"include_usage": true} to the body, capture the
   usage object from the final chunk (choices may be empty in that chunk — handle it), set
   self.last_usage and call on_usage(record) when given. OllamaService.generate: map
   prompt_eval_count/eval_count from the final message. Both keep working when the fields are absent.
2. core/workflow.py: Project.usage {"prompt_tokens", "completion_tokens", "requests", "seconds"}
   (persist; default zeros); run_check/run_segment_combined accept an on_usage callback and the
   runner forwards records through the event queue (("workflow_usage", record)) so the UI thread
   does the mutation. build_report adds a Usage line.
3. ui/app.py: session totals (every request in quick and automatic mode) in the status bar's right
   corner; tooltip with the breakdown; reset on app start only.
4. Relay: remote/relay/teai_relay/registry.py tracks per-agent in-flight, queued (waiting for a
   slot), and a 60-second rolling chunks/s; /status exposes {"agents": [{..., "in_flight",
   "queued", "chunks_per_s"}]}. Tests in remote/relay/tests.
5. Client: RemoteService.fetch_status already exists; WorkflowScreen polls it every 10 s while
   self.runner is active (background thread → event ("workflow_relay_status", data)) and
   ProjectView.update_progress shows "· GPU ≈ N chunks/s · Q queued" when available. Ollama backend:
   nothing shown.
6. Tests: usage parsing for both backends with fake streams (tests/test_remote_service.py,
   tests/test_ollama_service.py), project accumulation, report line.
```

### P5.2 Multiple relays / connection profiles `[#22]`

- **Size**: S · **Branch**: `feat/connection-profiles` · **Depends on**: —
- **Design**: `AppSettings.remote_profiles: list[{name, url, api_key, max_tokens, enable_thinking}]` + `active_profile`; the flat `remote_*` fields become properties of the active profile (kept for compatibility: a file with only flat fields creates a profile "Default"). The Connection dialog gets a profile combobox with Add/Rename/Delete; the main window's backend combobox lists "Remote: <profile>" entries. Model preference is stored per profile (`models["remote:<name>"]`).

```text
Branch: feat/connection-profiles

Support several relay profiles and one-click switching.

1. core/settings.py: remote_profiles: list[dict] and active_profile: str. On load: if the file has
   flat remote_url/remote_api_key/... and no profiles, create {"name": "Default", ...} from them.
   Expose remote_url/remote_api_key/remote_max_tokens/remote_enable_thinking as properties of the
   active profile (read/write) so existing callers keep working; to_dict writes profiles + active
   and the flat fields of the active profile (older app versions then still work). Env
   TEAI_REMOTE_URL/KEY seed the Default profile only when no profiles exist. Model memory:
   remember_model/preferred_model key "remote:<profile>" for the remote backend.
2. ui/connection_dialog.py: a profile combobox at the top with Add… / Rename… / Delete; the
   remaining fields edit the selected profile; Test connection uses the selected one; Save writes
   all profiles and the selected one becomes active.
3. ui/app.py: backend combobox lists "Local Ollama" and "Remote: <name>" per profile; selecting a
   remote entry sets active_profile and rebuilds the RemoteService (_remote_from_settings).
4. Tests in tests/test_settings.py: migration from flat fields, property view, round trip,
   deleting the active profile falls back to the first, model memory per profile.
```

### P5.3 Secure key storage via the OS keyring `[#25]`

- **Size**: S · **Branch**: `feat/keyring` · **Depends on**: P5.2
- **Design**: optional `keyring` dependency. When available, each profile's API key is stored under service `TextEnhanceAI`, username `relay:<profile name>`, and the settings file holds `"api_key": "@keyring"`. Fallback: plain text as today (the dialog's warning stays for that case). A "Store keys in the system keyring" checkbox in the Connection dialog (disabled with a hint when the package is missing); switching it migrates in both directions.

```text
Branch: feat/keyring

Store relay API keys in the OS keyring when the optional keyring package is present.

1. core/secrets.py: keyring_available() (import guarded), store_key(profile, key), load_key(profile),
   delete_key(profile); all failures degrade to None/False without raising. Service name
   "TextEnhanceAI", username "relay:<profile>".
2. core/settings.py: AppSettings.use_keyring: bool (persist). When True and available: save() writes
   "@keyring" as api_key in every profile and stores the real keys; load() resolves "@keyring" via
   load_key. Migration when the checkbox changes: keyring → file or file → keyring, deleting the other
   copy. When "@keyring" is found but keyring is unavailable, load_error explains it and the key is
   empty.
3. ui/connection_dialog.py: checkbox "Store keys in the system keyring (recommended)"; disabled with
   the hint "pip install keyring" when unavailable; the plain-text warning is shown only when the
   key is stored in the file. requirements.txt: keyring as optional (commented), requirements-dev
   installs it; tests inject a fake keyring backend by monkeypatching core.secrets functions.
4. Tests: round trip with fake keyring, unavailable path, migration both ways, TEAI_REMOTE_API_KEY
   env still wins for scripted setups.
```

### P5.4 Relay admin endpoint and status page `[#23]`

- **Size**: M · **Branch**: `feat/relay-admin` · **Depends on**: P5.1 (status fields)
- **Design**: `RELAY_ADMIN_KEY` (separate from client/agent keys). Keys live in a JSON file (`RELAY_KEYS_FILE`, default `/data/keys.json`, a Docker volume) merged over the env keys at start; `GET /admin/keys` lists names (never the keys), `POST /admin/keys {"name", "kind": "client"|"agent"}` generates and returns the key once, `DELETE /admin/keys/<name>` revokes immediately (in-memory update, no restart; active requests of that client finish). `GET /admin` serves a small HTML page (inline CSS, no JS dependencies) with agent state, models, in-flight/queued, chunk rate and the last 50 request outcomes (id, client name, status, duration — no content). Admin routes use their own constant-time check; all admin actions are logged with the key *name* only.

```text
Branch: feat/relay-admin

Add key management and a status page to the relay.

1. remote/relay/teai_relay/config.py: RELAY_ADMIN_KEY (optional; admin routes return 404 when
   unset) and RELAY_KEYS_FILE (default /data/keys.json). auth.py/registry.py: a KeyStore that
   merges env keys with the file, supports add(name, kind) → key, revoke(name), list() → names and
   kinds, and persists the file atomically; the auth middleware reads client keys from the store
   so revocation is immediate; agent connections use the store too (revoking an agent key closes
   its socket).
2. server.py: routes GET /admin (HTML), GET /admin/keys, POST /admin/keys, DELETE /admin/keys/{name},
   all requiring Authorization: Bearer <RELAY_ADMIN_KEY> via hmac.compare_digest; wrong key → 401,
   unset admin key → 404. The HTML page: agents (name, state, models, in_flight, queued,
   chunks/s, connected since), relay counters, and a ring buffer of the last 50 request outcomes
   (request id, client name, status, duration, chunks) — never bodies. Add "Cache-Control: no-store".
3. python -m teai_relay gains "keys list|add <name> [--agent]|revoke <name>" that edits the local
   file (for use when the relay is down) and prints the same warnings as keygen.
4. Docs: remote/relay/README.md (admin key, volume for keys.json, curl examples), .env.example,
   PROTOCOL.md (admin endpoints table). docker-compose*.yml: mount a volume for /data.
5. Tests (remote/relay/tests): admin auth, add/list/revoke round trip and immediate effect on
   /v1/models, file persistence, 404 when unset, page renders without leaking keys.
```

### P5.5 Agent metrics and drain mode `[#24]`

- **Size**: M · **Branch**: `feat/agent-drain` · **Depends on**: P5.1
- **Design**: the agent scrapes vLLM's Prometheus `/metrics` every 10 s (`num_requests_running`, `num_requests_waiting`, `gpu_cache_usage_perc`, `generation_tokens_total` → tokens/s by delta) and sends them in `hello.meta` and periodic `status` frames (protocol stays version 1; new fields are optional). Drain: `POST /drain` on the agent's local health server (or `SIGTERM` when `AGENT_DRAIN_ON_TERM=1`) switches the agent to state `draining`: the relay stops routing to it, in-flight requests finish, and once `in_flight == 0` the agent sends a final `status` and exits 0 — so `docker compose stop agent` becomes graceful.

```text
Branch: feat/agent-drain

Report GPU metrics from the agent and add a graceful drain mode.

1. remote/gpu-agent/agent/teai_agent/vllm_client.py: metrics() fetching GET /metrics (text
   exposition) and extracting num_requests_running, num_requests_waiting, gpu_cache_usage_perc and
   generation_tokens_total (tolerate missing metrics and renamed prefixes by matching the suffix).
   agent.py: a _monitor_metrics task computing tokens/s from the counter delta, included in
   _meta() and sent as {"type": "status", "state", "in_flight", "metrics": {...}} every
   AGENT_STATUS_INTERVAL seconds (default 10, config.py).
2. Drain: config AGENT_DRAIN_ON_TERM (default true). Agent.request_drain(): state → "draining",
   send a status frame, stop accepting request frames (answer new ones with error 503 "draining"),
   and when in_flight reaches 0 close the socket cleanly and exit. POST /drain on the health server
   triggers it; SIGTERM triggers it when the flag is on (otherwise the current abrupt stop). Add a
   drain grace timeout (default 600 s) after which in-flight requests are aborted with 502.
3. Relay: registry treats state "draining" like not-ready for routing and model listing but keeps
   the socket; /status and the admin page show it plus the metrics; PROTOCOL.md documents the new
   optional fields and the draining state.
4. docker-compose.yml (gpu-agent): stop_grace_period: 660s for the agent service. README: how to
   restart vLLM without failing user requests (drain agent → wait → restart vllm → agent reconnects).
5. Tests: remote/gpu-agent/tests (metrics parsing, drain transitions with the fake vLLM, SIGTERM
   handling via calling the handler) and remote/tests end-to-end: a streaming request in flight
   while the agent drains completes; a new request during drain gets 503.
```

### P5.6 Windows/macOS packaging `[#26]`

- **Size**: M · **Branch**: `feat/packaging` · **Depends on**: P5.2, P5.3 (settings shape must be final)
- **Design**: settings and scratchpads move to a per-user directory (`%APPDATA%\TextEnhanceAI`, `~/Library/Application Support/TextEnhanceAI`, `$XDG_CONFIG_HOME/TextEnhanceAI`) via `core/paths.py`; a script-adjacent settings file is migrated once. PyInstaller spec, a GitHub Actions workflow building on `windows-latest` and `macos-latest` on tags, and a "Releases" link in an About dialog (no auto-update).

```text
Branch: feat/packaging

Package the app for authors who do not run pip.

1. core/paths.py: user_data_dir() following the platform conventions (APPDATA on Windows,
   ~/Library/Application Support on macOS, XDG on Linux), overridable by TEAI_DATA_DIR; ensure_dir.
   ScratchpadLogger and AppSettings default their paths there. TextEnhanceAI.py: if a
   TextEnhanceAI-settings.json exists next to the script and none in the data dir, copy it and
   show "Settings migrated to …" in the status bar once. Running from a git checkout with
   TEAI_DATA_DIR=. keeps the old behaviour for developers (document in AGENTS.md).
2. packaging/TextEnhanceAI.spec (PyInstaller, onedir, windowed, name TextEnhanceAI, include
   locales/ and an app icon — add a simple icon.ico/.icns generated from a plain SVG in the
   workflow). packaging/README.md with the exact commands.
3. .github/workflows/build.yml: on tag v*, build on windows-latest and macos-latest with Python
   3.11, pip install -r requirements.txt pyinstaller python-docx keyring, run the spec, zip the
   result (Windows) / create a .dmg with hdiutil (macOS, unsigned — say so in the README), upload
   as release assets. Also run pytest on ubuntu-latest for every push.
4. ui/app.py: Help → About with version (single source: core/__init__.py __version__, also used
   for USER_AGENT in core/remote_service.py) and a "Releases" link opened with webbrowser.
5. Tests: paths resolution per platform via monkeypatched sys.platform/env, migration copy only
   once.
```

---

## Phase 6 — Robustness, accessibility, localisation

### P6.1 Long-run stability test of the runner `[#28]`

- **Size**: M · **Branch**: `test/long-run` · **Depends on**: P2.4, P2.5, P3.3
- **Design**: two opt-in tests (`-m slow`, excluded from the default `pytest -q`). (a) Desktop: a 3,000-segment synthetic project against a `FakeService` that injects `BackendUnavailable` on 3 % of calls, truncation on 0.5 %, 20–80 ms delays, parallelism 4; cancel and resume five times at random points; assert every segment ends `done` or `error`, no duplicate results, the saved file loads, and no leaked threads. (b) End-to-end: the `remote/tests` fake vLLM with relay and agent, 200 streaming requests through `RemoteService`, agent socket killed twice mid-run, assert the runner recovers via the retry path and completes.

```text
Branch: test/long-run

Add opt-in long-run tests for the evaluation runner and the relay path.

1. pytest marker "slow" registered in pytest.ini (default run deselects it:
   addopts = -m "not slow"; document `pytest -m slow -q` in AGENTS.md).
2. tests/test_long_run.py: build a project with 3,000 segments (generate paragraphs with planted
   typos from a small vocabulary), FakeService subclass with configurable failure/truncation
   probabilities and latency jitter, parallelism 4. Loop: start runner, wait a random 0.2–1.5 s,
   cancel, save, reload from disk, resume — five times, then run to completion. Assertions: every
   non-blank segment has a result for every enabled check, no (segment, check) received two "done"
   results (count workflow_result events per key), pending_tasks() is empty, threading.active_count()
   is back to the pre-test value within 2 s, tracemalloc peak < 200 MB, and Project.load of the
   final file equals the in-memory progress(). Run it for both evaluation modes.
3. remote/tests/test_long_run.py: start the fake vLLM, relay and agent as in test_end_to_end.py;
   drive 200 streaming requests through core.remote_service.RemoteService from 4 threads; from a
   controller thread, force-close the agent's websocket twice; assert every request either
   succeeded or raised RemoteUnavailable (never hung past the timeout), and that the relay's
   /status shows zero in-flight at the end.
4. Fix anything these tests reveal (report each fix separately in the PR description).
```

### P6.2 Accessibility pass `[#29]`

- **Size**: M · **Branch**: `feat/accessibility` · **Depends on**: P3.5
- **Design**: every colour-coded state gets a glyph and a text label (tree rows: `⏳ queued`, `✔ clean`, `● ready`, `✓ reviewed`, `✖ error`, `⚠ flagged`; cards: state word next to the badge; chapter view spans: optional `[+]`, `[−]`, `[~]` markers). Font scaling via `Ctrl+=`/`Ctrl+-`/`Ctrl+0` on a persisted `ui_scale` applied by `theme.py` to every named font. High-contrast palette in `theme.py` selected by `AppSettings.high_contrast`. Every action reachable by keyboard; visible focus rings.

```text
Branch: feat/accessibility

Make state understandable without colour and support font scaling and high contrast.

1. ui/theme.py: named fonts created once (body, small, mono, heading) with a scale factor applied
   in apply_scale(factor); palette selection normal/high-contrast with the same token names;
   STATUS_GLYPHS = {queued: "⏳", running: "▶", clean: "✔", ready: "●", reviewed: "✓", error: "✖",
   flagged: "⚠"} and DECISION_GLYPHS for applied/superseded/rejected/pending/edited.
2. AppSettings.ui_scale: float = 1.0 (persist, clamp 0.8–2.0) and high_contrast: bool. ui/app.py:
   Ctrl+= / Ctrl+- / Ctrl+0 adjust and save; a View menu with the same plus "High contrast".
3. ui/workflow_screen.py: tree rows get a glyph + word status column; ChangeCard badges show the
   check label, kind and decision as text next to the colour; the chapter view (feat/chapter-view)
   gains a "Show markers" toggle inserting [+]/[−]/[~] before spans. ui/review_panel.py: hunks show
   "removed"/"added" labels in the details view in addition to strike/underline.
4. Keyboard: verify every button has a mnemonic or shortcut; Tab order sensible; the focused card
   has a visible outline (relief/border via theme), not just a background tint.
5. Manual checklist in the PR: run with ui_scale 1.6, with high contrast, and with Windows
   high-contrast mode; screenshots before/after.
```

### P6.3 Localised UI — German `[#30b]`

- **Size**: M · **Branch**: `feat/german-ui` · **Depends on**: P1.2 and every UI package you want translated (do it last)
- **Design**: wrap every user-facing string in `ui/` with `tr()`, including format strings. Constants in `core/` that reach the UI (`CHECK_LABELS`, status labels, fallback/canned explanations, kind labels, backend labels) stay English in core and are translated at the UI boundary. Model-facing prompts remain English; explanation language stays a per-project option. Fill `locales/de.json` completely and add a test that no `tr()` source string lacks a German entry.

```text
Branch: feat/german-ui

Translate the UI into German.

1. Wrap every user-visible literal in ui/*.py with ui.i18n.tr(); use keyword formatting for
   strings with values. Where core/ produces text that the UI shows verbatim (CHECK_LABELS,
   CHECK_DESCRIPTIONS, STATUS labels, FALLBACK_EXPLANATIONS, CANNED_EXPLANATIONS,
   CHANGE_KIND_LABELS, BACKEND_LABELS, the hint line), keep the English constants in core and add
   a UI-side lookup that passes the constant through tr() at render time — core must stay
   language-neutral. Backend error messages stay English inside exceptions; the UI prefixes them
   with a translated sentence.
2. Run scripts/extract_strings.py and fill locales/de.json completely, using consistent
   terminology: Rechtschreibung / Grammatik / Ausdruck; Änderung; übernehmen / verwerfen;
   Abschnitt (segment); Kapitel; Projekt; Prüfung (check); Relais (relay) — keep "TextEnhanceAI"
   and "Ollama" untranslated. Keep shortcut hints as "Alt+A übernehmen · Alt+R verwerfen …".
3. Number formatting: a small ui/i18n.format_number(n) using thousands separators per language
   (no locale.setlocale side effects).
4. Add a test that every tr() source string in ui/ has a German entry (reuse
   scripts/extract_strings.py as a function) so new strings cannot be added without a translation.
5. Manual: start with TEAI_LANG=de, walk both modes, the Connection dialog, the statistics and
   chapter views, and take screenshots for README (add a German screenshot next to the existing
   ones).
```

---

## Appendix A — Cross-cutting notes

- **Project format**: P1.3, P2.2, P2.3, P2.4, P3.1, P3.4, P3.7, P3.8, P4.1 and P5.1 all add keys to `project.json`. None removes or changes the meaning of an existing key, so `PROJECT_FORMAT` can stay at 1 as long as every `from_dict` defaults missing keys. Bump it only if a package changes the meaning of `results` or `changes` (P3.4's `author` pseudo-check is the closest call: an older app version would carry it as an unknown check that `Segment.changes` ignores — acceptable).
- **Settings file**: P1.2, P2.1, P2.2, P2.4, P4.3, P4.4, P4.5, P5.2, P5.3, P5.6, P6.2 add fields. The loader already tolerates unknown keys; P5.6 moves the file, so it goes after the others.
- **Request volume** after Phase 2 (per non-blank segment): combined mode ≈ 1 request; separate mode ≈ 3 edit + ≤ 3 explanation requests (fewer with canned explanations). Check the numbers with the recorded suite before changing defaults.
- **Parallel branches**: within a phase, packages without a "Depends on" link can be developed concurrently; expect merge friction mainly in `ui/workflow_screen.py` (`ProjectView.__init__/_build`) — rebase small packages onto the larger UI ones (P3.5) rather than the reverse.
- **Re-recording**: P1.4, P2.1–P2.4 and P3.8 change prompts. After each merge, run `scripts/record_responses.py` once against the relay and commit the cassettes in a follow-up commit.

## Appendix B — Quick reference: where things live today

| Concern | Location |
|---------|----------|
| Check instructions, explanation prompt, JSON parsing | `core/workflow.py` (`CHECK_INSTRUCTIONS`, `build_explanation_messages`, `parse_explanations`, `_parse_json_object`) |
| One check on one segment | `core/workflow.py` `run_check` |
| Background evaluation, events | `core/workflow.py` `ProjectRunner`, consumed by `ui/workflow_screen.py` `WorkflowScreen.handle_event` |
| Merge rules (priority, overlap, supersede) | `applied_changes`, `change_states`, `render_segment` |
| Persistence | `Project.to_dict/from_dict/save/load`, `PROJECT_FORMAT` |
| Backend contract, system prompt, message shape | `core/backend.py` |
| Remote SSE client, cancel-by-socket-close | `core/remote_service.py` `RemoteService.generate` |
| Settings | `core/settings.py` `AppSettings` |
| Quick editor request flow | `ui/app.py` `start_review`, `_get_instruction`, `apply_review` |
| Quick review rendering | `ui/review_panel.py`, `core/diff_engine.py`, `core/models.py` |
| Automatic review screens | `ui/workflow_screen.py` (`StartView`, `ProjectView`, `ChangeCard`) |
| Relay routes / auth / registry | `remote/relay/teai_relay/{server,auth,registry,config}.py` |
| GPU agent | `remote/gpu-agent/agent/teai_agent/{agent,vllm_client,config}.py` |
| Wire protocol | `remote/PROTOCOL.md` |
| Tests with a fake backend | `tests/test_workflow.py` `FakeService`; `remote/tests` fake vLLM |
