"""TextEnhanceAI v0.13 launcher.

Run this file directly to start the local Ollama-powered editor.
"""

import tkinter as tk
from pathlib import Path

from core.prompts import PROMPTS
from ui.app import EditorApp

__all__ = ["EditorApp", "PROMPTS", "main"]


def main():
    """Start the TextEnhanceAI desktop application."""
    root = tk.Tk()
    EditorApp(root, app_directory=Path(__file__).resolve().parent)
    root.mainloop()


if __name__ == "__main__":
    main()
