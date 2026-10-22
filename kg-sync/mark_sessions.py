"""Retired unsafe helper. Completion requires an immutable snapshot receipt."""
import sys


def main():
    print("Manual processed-session marking is disabled. Use sync.py --status, then --replay <snapshot_id> for a durable extraction. No state was changed.",file=sys.stderr)
    return 2


if __name__=="__main__":
    raise SystemExit(main())
