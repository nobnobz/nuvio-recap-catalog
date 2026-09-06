"""Generate an offline review desk; import an explicitly reviewed catalog draft.

No credentials or automatic publishing. Run `npm run review` then open reports/review.html.
Run `npm run review -- --import-draft /path/catalog-draft.json` to validate a draft.
"""
import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f'scripts/{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def import_draft(path):
    validator = load_module('check-recap-catalog')
    draft = validator.validate(path)
    current = validator.validate(ROOT / 'catalog.json')
    if draft['revision'] != current['revision'] + 1:
        raise ValueError('Draft is stale. Open a new review desk from the current catalog.')
    for key in ('channels', 'schemaVersion', 'updateURL'):
        if draft.get(key) != current.get(key):
            raise ValueError(f'Review desk cannot change {key}')
    existing = {v['videoID']: v for v in draft['videos']}
    if any(existing.get(v['videoID']) != v for v in current['videos']):
        raise ValueError('Review desk is additive; existing videos must be preserved')
    pending = ROOT / 'catalog.json.tmp'
    pending.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + '\n')
    pending.replace(ROOT / 'catalog.json')
    print('Draft imported. Run npm test and review the diff, then commit catalog.json to publish.')


def render(catalog, inbox):
    # Do not let feed titles terminate the inert JSON script element.
    payload = json.dumps({'catalog': catalog, 'inbox': inbox}, ensure_ascii=False).replace('<', '\\u003c')
    template = (ROOT / 'scripts/review-template.html').read_text()
    return template.replace('__REVIEW_DATA__', payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--import-draft', type=Path)
    args = parser.parse_args()
    if args.import_draft:
        import_draft(args.import_draft)
        return
    catalog = load_module('check-recap-catalog').validate(ROOT / 'catalog.json')
    inbox = json.loads((ROOT / 'review/inbox.json').read_text())
    decisions_path = ROOT / 'review/decisions.json'
    decisions = json.loads(decisions_path.read_text()) if decisions_path.exists() else {}
    inbox['candidates'] = [v for v in inbox['candidates'] if v['videoID'] not in decisions]
    target = ROOT / 'reports/review.html'
    target.parent.mkdir(exist_ok=True)
    target.write_text(render(catalog, inbox))
    print(target)


if __name__ == '__main__':
    main()
