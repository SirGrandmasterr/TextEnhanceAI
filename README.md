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

Generated `TextEnhanceAI-scratchpad_*.md` files and `TextEnhanceAI-settings.json` are stored next to the application and ignored by Git.

## Development and tests

The launcher remains `TextEnhanceAI.py`. Pure editing logic and model access live in `core/` (`ollama_service.py`, `remote_service.py`, shared contract in `backend.py`, persisted `settings.py`), Tkinter screens live in `ui/`, and the relay and GPU agent live in `remote/`.

Install test dependencies and run the suite:

```powershell
uv pip install -r requirements-dev.txt
uv run pytest -q
```

The automated suite uses fake Ollama responses and does not require a running model. Version 0.13 is verified with Ollama Python client 0.6.1; a live Ollama smoke test is still recommended for release verification. The same command also runs the relay, agent, and end-to-end suites under `remote/` (they need `aiohttp`, included in `requirements-dev.txt`).

## Contact

**Email:** [wenrolland@designecologique.ca](mailto:wenrolland@designecologique.ca)

## Updates

- **Unreleased:** Adds the remote backend (relay + GPU agent + vLLM stack), a Connection dialog with live testing, persisted settings, progress display, and truncation detection.
- **Version 0.13:** Adds structured sentence and word-group review, safe mixed decisions, cancellation, connection status, enhanced scratchpads, undo, and automated tests.
- **Version 0.12:** Adds local model selection and background generation.
- **Version 0.11:** Preserves line breaks after editing.
