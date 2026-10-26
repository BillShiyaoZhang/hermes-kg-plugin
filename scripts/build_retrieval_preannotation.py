"""Assemble explicit machine annotations into a private offline human workbook."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins'))
from kg.research_preannotation import build


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture-dir', type=Path, required=True)
    parser.add_argument('--batch', action='append', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    private = (ROOT / 'reports/retrieval-research/private').resolve()
    if not args.output_dir.resolve().is_relative_to(private):
        parser.error('Output must remain in this workspace private research reports')
    summary = build(args.fixture_dir, args.batch, args.output_dir)
    print(json.dumps({key: summary[key] for key in ('machine_drafts', 'human_reviewed', 'confidence', 'content_review_ids', 'answerable', 'not_answerable')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
