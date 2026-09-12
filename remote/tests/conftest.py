"""Make the desktop core, the relay and the agent importable side by side."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
for path in (
    REPO_ROOT,
    os.path.join(REPO_ROOT, "remote", "relay"),
    os.path.join(REPO_ROOT, "remote", "gpu-agent", "agent"),
):
    if path not in sys.path:
        sys.path.insert(0, path)
