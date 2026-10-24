#!/usr/bin/env python3
"""Start the deployed console using any Python 3.11+ stdlib bootstrap."""
from pathlib import Path
import sys

home = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(home / 'plugins'))
from kg.deployment_startup import main

if __name__ == '__main__':
    raise SystemExit(main(default_home=home))
