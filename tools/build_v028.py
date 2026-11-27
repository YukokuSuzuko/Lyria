"""Freeze and build the v0.28 main edition using the audited candidate gates."""
import argparse
from pathlib import Path

from build_v024_dual import execute, make_plan, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'delivery', 'workspace', 'python', 'reading-bundle', 'plan'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.plan.exists():
        parser.error('Choose a new plan file.')
    plan = make_plan([args.source], args.delivery, args.workspace,
                     args.delivery / 'shared-runtime', args.python, args.reading_bundle)
    if not all(item['clean'] for item in plan['editions']):
        parser.error('Commit the reviewed source before freezing.')
    write(args.plan, plan)
    if args.execute:
        execute(plan)


if __name__ == '__main__':
    main()
