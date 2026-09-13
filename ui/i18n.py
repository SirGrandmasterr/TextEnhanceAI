"""Minimal translation helper for the Tkinter screens.

Translations are flat JSON dictionaries in ``locales/<code>.json`` (next to
the ``ui`` package) mapping the English source string to its translation::

    {"Save": "Speichern", "Connecting to {url} ...": "Verbinde mit {url} ..."}

Usage in a screen::

    from .i18n import tr
    ttk.Button(frame, text=tr("Save"))
    status.set(tr("Connecting to {url} ...", url=url))

``tr`` falls back to the English source string whenever no translation is
loaded, the string is missing from the table, or the translation's
placeholders do not match. ``set_language`` is called once at start-up from
the ``ui_language`` setting; ``scripts/extract_strings.py`` lists source
strings that still lack a translation.
"""

import json
import locale
import warnings
from pathlib import Path

from core.settings import UI_LANGUAGE_AUTO, UI_LANGUAGES

DEFAULT_LANGUAGE = "en"
LANGUAGE_LABELS = {
    UI_LANGUAGE_AUTO: "Auto",
    "en": "English",
    "de": "Deutsch",
}
LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"

_active = {"code": DEFAULT_LANGUAGE, "table": {}}


def tr(text, **kwargs):
    """Return ``text`` translated into the active language, formatted with ``kwargs``."""
    translated = _active["table"].get(text) or text
    if not kwargs:
        return translated
    try:
        return translated.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        # A translation with broken placeholders must never crash the UI.
        return text.format(**kwargs)


def current_language():
    """Return the active language code (``"en"`` or ``"de"``)."""
    return _active["code"]


def system_language():
    """Return ``"de"`` when the OS locale is German, else ``"en"``."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            code = locale.getdefaultlocale()[0] or ""
    except (ValueError, TypeError):
        code = ""
    return "de" if code.lower().startswith("de") else DEFAULT_LANGUAGE


def resolve_language(setting):
    """Map a ``ui_language`` setting (``auto|en|de``) to a concrete language code."""
    code = (setting or UI_LANGUAGE_AUTO).strip().lower()
    if code == UI_LANGUAGE_AUTO:
        return system_language()
    return code


def load_table(code, locales_dir=None):
    """Return the translation dictionary for ``code``, or ``None`` when its file is missing or unreadable."""
    path = Path(locales_dir or LOCALES_DIR) / "{0}.json".format(code)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {
        str(key): str(value)
        for key, value in data.items()
        if isinstance(value, str)
    }


def set_language(code, locales_dir=None):
    """Activate a language; unknown codes and missing files silently mean English.

    ``code`` may be ``"auto"`` (resolved from the OS locale) or a concrete code
    such as ``"de"``. Returns the code that is now active.
    """
    resolved = resolve_language(code)
    table = None
    if resolved != DEFAULT_LANGUAGE and resolved in UI_LANGUAGES:
        table = load_table(resolved, locales_dir)
    if table is None:
        resolved, table = DEFAULT_LANGUAGE, {}
    _active["code"] = resolved
    _active["table"] = table
    return resolved
