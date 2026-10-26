#!/usr/bin/env python3
"""Build/check deterministic personal ontology artifacts without network access."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins'))
from kg.ontology_profile import build_personal_artifacts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Compare without writing files')
    args = parser.parse_args(argv)
    root = ROOT / 'skills' / 'kg-memory' / 'ontology' / 'personal'
    artifacts = build_personal_artifacts(root)
    outputs = {'manifest.json': json.dumps(artifacts['manifest'], ensure_ascii=False, sort_keys=True, indent=2) + '\n',
               'shapes.ttl': artifacts['shapes'], 'selected.ttl': artifacts['selected']}
    stale = []
    for name, text in outputs.items():
        path = root / name
        if args.check:
            if not path.is_file() or path.read_text(encoding='utf-8') != text:
                stale.append(name)
        else:
            path.write_text(text, encoding='utf-8', newline='\n')
    if stale:
        print('Stale personal ontology artifacts: ' + ', '.join(stale), file=sys.stderr)
        return 1
    print(json.dumps({'ok': True, 'check': args.check, 'digest': artifacts['manifest']['digest'],
                      'definitions': len(artifacts['manifest']['operations'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
