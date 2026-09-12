# Repository Guidelines

## Project Structure & Module Organization
- Root files: `TextEnhanceAI.py` (main app), `README.md`, `LICENSE`, screenshots `TextEnhanceAI-*.png`.
- Runtime artifacts: scratchpads `TextEnhanceAI-scratchpad_*.md` are generated next to the script.
- `core/` holds editing logic and model backends (`backend.py` contract, `ollama_service.py`, `remote_service.py`, `settings.py`); `ui/` holds Tkinter screens (`app.py`, `review_panel.py`, `connection_dialog.py`).
- `remote/` holds the self-hosted relay (`remote/relay`, runs on a public server) and the GPU stack (`remote/gpu-agent`, vLLM + agent). Each is a self-contained Docker Compose deployment; the wire protocol is in `remote/PROTOCOL.md`.

## Build, Test, and Development Commands
- Run locally: `python TextEnhanceAI.py`
- Create venv (optional):
  - Windows: `python -m venv .venv && .venv\\Scripts\\activate`
  - Unix: `python -m venv .venv && source .venv/bin/activate`
- Install deps: `pip install -r requirements.txt` (Tkinter and difflib are stdlib).
- Ollama model: `ollama pull llama3.1:8b` (ensure Ollama is installed and running).

## Coding Style & Naming Conventions
- Python 3.8+; follow PEP 8 with 4‑space indents.
- Functions/variables: `snake_case`; classes: `PascalCase`; constants: `UPPER_CASE`.
- Docstrings: short summary + key args/returns where useful.
- UI labeling: keep button text concise; tooltips explain behavior.
- Prompts: extend the `PROMPTS` dict; avoid duplicating strings across the UI.

## Testing Guidelines
- Run `pytest -q` from the repository root; it collects `tests/` (desktop), `remote/relay/tests`, `remote/gpu-agent/tests`, and `remote/tests` (end-to-end with a fake vLLM).
- Name tests `test_*.py` (pytest style). Backend tests use fake servers, never a live model. For UI changes, still provide manual steps (what you typed, which button you clicked, expected behavior).

## Commit & Pull Request Guidelines
- Commits: imperative mood, present tense (e.g., "Fix grammar prompt", "Update README"). Keep focused and small.
- PRs: include a clear description, linked issues (if any), and before/after screenshots for UI changes.
- Checklists: note local run results and any edge cases tested.

## Security & Configuration Tips
- The app uses a local LLM via the `ollama` Python client, or a self-hosted relay (`core/remote_service.py`, standard library only). No third-party cloud calls are made; the relay and agent must never log prompt or output text.
- Do not commit runtime artifacts (e.g., `TextEnhanceAI-scratchpad_*.md`, `TextEnhanceAI-settings.json`) or deployment secrets (`remote/**/.env`).
- If you introduce config, prefer environment variables with safe defaults.
