"""Entry point for `python -m pianolotayu`."""

from .cli.main import main
import sys

if __name__ == "__main__":
    sys.exit(main())
