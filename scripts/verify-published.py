#!/usr/bin/env python3
"""Confirm the newly committed catalog reached the static public endpoint."""
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = 'https://nuvio-recaps.marvins-dashboard.workers.dev/v1/catalog.json'


def matches(expected, actual):
    return actual == expected


def main():
    expected = json.loads((ROOT / 'catalog.json').read_text())
    for attempt in range(18):
        try:
            request = urllib.request.Request(ENDPOINT, headers={'Cache-Control': 'no-cache', 'User-Agent': 'NuvioCatalogPublisher/1.0'})
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read(1_048_577)
            if len(raw) <= 1_048_576 and matches(expected, json.loads(raw)):
                print(f"Public catalog revision {expected['revision']} verified")
                return
        except Exception:
            pass
        if attempt < 17:
            time.sleep(10)
    raise SystemExit('Catalog committed, but publication not confirmed. Check Cloudflare Git build; the app retains its previous catalog.')


if __name__ == '__main__':
    main()
