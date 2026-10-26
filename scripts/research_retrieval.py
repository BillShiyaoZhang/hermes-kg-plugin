#!/usr/bin/env python3
"""Source-checkout entry point for isolated, reproducible retrieval research."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins'))
from kg.research.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
