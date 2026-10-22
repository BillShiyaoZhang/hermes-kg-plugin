"""Compatibility entry for the unified read-only KG health/aggregate check."""
from check_kg import main


if __name__=="__main__":
    raise SystemExit(main())
