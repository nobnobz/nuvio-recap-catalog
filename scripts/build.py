#!/usr/bin/env python3
"""Publish only validated public catalog data. No Worker runtime or paid bindings."""
import importlib.util
import json
import os
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('catalog_validator', ROOT / 'scripts/check-recap-catalog.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)

def check_revision(current, previous):
    if current['revision'] < previous['revision']:
        raise ValueError('Revision rollback rejected; publish corrections with a higher revision')
    if current != previous and current['revision'] == previous['revision']:
        raise ValueError('Catalog changed without increasing revision')

def build():
    catalog = validator.validate(ROOT / 'catalog.json')
    previous_url = os.environ.get('CATALOG_URL')
    if previous_url:
        if not previous_url.startswith('https://'):
            raise ValueError('CATALOG_URL must use HTTPS')
        with urllib.request.urlopen(urllib.request.Request(previous_url, headers={'Cache-Control':'no-cache'}), timeout=15) as response:
            raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ValueError('Published catalog too large')
        check_revision(catalog, json.loads(raw))
    target = ROOT / 'public/v1'
    target.mkdir(parents=True, exist_ok=True)
    (target / 'catalog.json').write_bytes((ROOT / 'catalog.json').read_bytes())
    (ROOT / 'public/_headers').write_text('/v1/catalog.json\n  Cache-Control: public, max-age=300, must-revalidate\n  Content-Type: application/json; charset=utf-8\n  X-Content-Type-Options: nosniff\n  X-Robots-Tag: noindex\n')
    print('Built public/v1/catalog.json (static assets only)')

if __name__ == '__main__':
    build()
