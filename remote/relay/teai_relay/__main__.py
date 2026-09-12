"""Command line: ``python -m teai_relay`` (serve) or ``python -m teai_relay keygen``."""

import sys

from .auth import generate_key
from .config import ConfigError, RelayConfig


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("keygen", "key"):
        count = int(argv[1]) if len(argv) > 1 else 1
        for _ in range(max(1, count)):
            print(generate_key())
        return 0
    if argv and argv[0] in ("-h", "--help", "help"):
        print(
            "Usage:\n"
            "  python -m teai_relay            start the relay (configured via environment)\n"
            "  python -m teai_relay keygen [n] print n new random API keys\n"
        )
        return 0
    try:
        config = RelayConfig.from_environ()
    except ConfigError as exc:
        print("Configuration error: {0}".format(exc), file=sys.stderr)
        return 2
    from .server import run

    run(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
