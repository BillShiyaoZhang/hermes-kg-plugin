#!/usr/bin/env python3
"""Install or upgrade KG in an existing Hermes profile with one command."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins'))
from kg.deployment import main

if __name__ == '__main__':
    raise SystemExit(main(source=Path(__file__).resolve().parents[1]))
