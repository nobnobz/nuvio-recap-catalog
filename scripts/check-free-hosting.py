"""Fail deployment if configuration adds runtime work or paid bindings.

This checks project configuration, not the account's billing plan.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(config):
    allowed = {'$schema', 'name', 'compatibility_date', 'workers_dev', 'preview_urls', 'assets'}
    if set(config) - allowed:
        raise ValueError('Static-only hosting: runtime code, bindings and extra settings are not allowed')
    if config.get('name') != 'nuvio-recaps' or config.get('workers_dev') is not True or config.get('preview_urls') is not False:
        raise ValueError('Use the existing workers.dev project with previews disabled')
    if config.get('assets') != {'directory': './public', 'html_handling': 'none', 'not_found_handling': 'none'}:
        raise ValueError('Serve public assets directly; no Worker-first routing')


if __name__ == '__main__':
    check(json.loads((ROOT / 'wrangler.jsonc').read_text()))
    print('Static-only configuration verified; keep the account on Workers Free')
