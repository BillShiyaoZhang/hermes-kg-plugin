#!/usr/bin/env python3
"""Launch the workbench from an installed Hermes profile."""
from pathlib import Path
import sys
home=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(home/'plugins'),str(home)]
from kg.console_cli import main
if __name__=='__main__': raise SystemExit(main())
