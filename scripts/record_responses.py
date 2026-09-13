#!/usr/bin/env python
"""Record live model answers for the review-pipeline samples into cassettes.

Runs every ``tests/recorded/samples/*.txt`` through ``run_check`` for each of
the three checks (with explanations) against the backend configured in
``TextEnhanceAI-settings.json`` and writes one cassette per sample to
``tests/recorded/<sample>.<model-slug>.json``. ``tests/test_recorded.py`` then
replays those cassettes offline.

Examples::

    python scripts/record_responses.py                       # settings file, saved model
    python scripts/record_responses.py --backend remote --model Qwen/Qwen3-32B-AWQ
    python scripts/record_responses.py --samples de_brief en_harbor

Not run in CI: it needs a live relay or Ollama. Re-record whenever a prompt in
``core/backend.py`` or ``core/workflow.py`` changes or a new sample is added.
"""

import argparse
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.backend import BackendUnavailable  # noqa: E402
from core.services import build_service  # noqa: E402
from core.settings import BACKENDS, SETTINGS_FILENAME, AppSettings  # noqa: E402
from core.workflow import CHECKS, FALLBACK_EXPLANATIONS, SAME_LANGUAGE, run_check  # noqa: E402
from tests.recorded_service import (  # noqa: E402
    RECORDED_DIR,
    SAMPLES_DIR,
    RecordingService,
    cassette_path,
    load_sample,
    sample_names,
    write_cassette,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--settings", type=Path, default=ROOT / SETTINGS_FILENAME,
        help="settings file to load (default: %(default)s)",
    )
    parser.add_argument("--backend", choices=BACKENDS, help="override the configured backend")
    parser.add_argument("--model", help="override the saved model (default: settings, else first listed)")
    parser.add_argument(
        "--samples", nargs="*", metavar="NAME",
        help="sample names to record (default: every tests/recorded/samples/*.txt)",
    )
    parser.add_argument(
        "--language", default=SAME_LANGUAGE, help="explanation language (default: same as text)",
    )
    parser.add_argument(
        "--out", type=Path, default=RECORDED_DIR, help="cassette directory (default: %(default)s)",
    )
    return parser.parse_args(argv)


def choose_model(service, settings, backend, override):
    if override:
        return override
    preferred = settings.preferred_model(backend)
    models = service.list_models()
    if not models:
        raise BackendUnavailable(service.no_models_hint())
    if preferred and preferred in models:
        return preferred
    if preferred:
        print("Saved model {0!r} is not available; using {1!r}.".format(preferred, models[0]))
    return models[0]


def record_sample(service, model, sample, language):
    """Run every check on one sample; returns (recorder, [row, ...])."""
    text = load_sample(sample, SAMPLES_DIR)
    recorder = RecordingService(service)
    rows = []
    for check in CHECKS:
        started = time.time()
        result = run_check(recorder, model, text, check, threading.Event(), explain=True, language=language)
        seconds = time.time() - started
        explained = sum(
            1 for change in result.changes if change.explanation != FALLBACK_EXPLANATIONS[check]
        )
        rows.append({
            "sample": sample,
            "check": check,
            "status": result.status,
            "changes": len(result.changes),
            "explained": "{0}/{1}".format(explained, len(result.changes)) if result.changes else "-",
            "seconds": seconds,
            "error": result.error,
        })
        print("  {0:<10} {1:<5} {2:>3} change(s)  {3:5.1f}s  {4}".format(
            check, result.status, len(result.changes), seconds, result.error
        ).rstrip())
    return recorder, rows


def print_summary(rows):
    print()
    print("{0:<14} {1:<10} {2:<6} {3:>7}  {4:<9} {5}".format(
        "sample", "check", "status", "changes", "explained", "error"
    ))
    print("-" * 72)
    for row in rows:
        print("{0:<14} {1:<10} {2:<6} {3:>7}  {4:<9} {5}".format(
            row["sample"], row["check"], row["status"], row["changes"], row["explained"], row["error"]
        ).rstrip())


def _display_path(path):
    try:
        return path.relative_to(ROOT)
    except ValueError:  # cassette directory outside the repository
        return path


def main(argv=None):
    args = parse_args(argv)
    settings = AppSettings.load(args.settings)
    if settings.load_error:
        print(settings.load_error)
    backend = args.backend or settings.backend
    service = build_service(settings, backend)
    try:
        model = choose_model(service, settings, backend, args.model)
    except BackendUnavailable as exc:
        print("Cannot reach the {0} backend: {1}".format(backend, exc))
        return 2
    print("Recording with {0} model {1!r} ({2})".format(backend, model, service.connection_summary()))

    names = args.samples or sample_names(SAMPLES_DIR)
    missing = [name for name in names if not (SAMPLES_DIR / (name + ".txt")).exists()]
    if missing:
        print("Unknown sample(s): {0}".format(", ".join(missing)))
        return 2

    all_rows = []
    for sample in names:
        print("\n{0}".format(sample))
        recorder, rows = record_sample(service, model, sample, args.language)
        all_rows.extend(rows)
        path = write_cassette(
            cassette_path(sample, model, args.out), model, backend, recorder.entries.values(), sample
        )
        print("  -> {0} ({1} request(s))".format(_display_path(path), len(recorder.entries)))

    print_summary(all_rows)
    failed = [row for row in all_rows if row["status"] != "done"]
    if failed:
        print("\n{0} check(s) failed; their cassettes hold the error and will fail the tests.".format(len(failed)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
