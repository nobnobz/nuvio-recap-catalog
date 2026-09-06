#!/usr/bin/env python3
"""Collect review candidates from approved channel feeds. Never edits catalog.json."""
import concurrent.futures
import datetime
import importlib.util
import json
from pathlib import Path
import re
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
NS = {'a': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015'}
spec = importlib.util.spec_from_file_location('validator', ROOT / 'scripts/check-recap-catalog.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def parse_feed(raw, channel, known):
    if len(raw) > 1_048_576:
        raise ValueError('Feed exceeds size limit')
    root = ET.fromstring(raw)
    feed_id = root.findtext('yt:channelId', default='', namespaces=NS)
    # YouTube Atom feeds use the channel identity without the UC prefix.
    if feed_id not in (channel['id'], channel['id'][2:]):
        raise ValueError('Channel identity mismatch')
    result = []
    for entry in root.findall('a:entry', NS)[:30]:
        key = entry.findtext('yt:videoId', default='', namespaces=NS)
        title = entry.findtext('a:title', default='', namespaces=NS)
        if key in known or not re.fullmatch(r'[A-Za-z0-9_-]{11}', key):
            continue
        if not re.search(r'\b(recap|recapped|catch up|previously on)\b', title, re.I):
            continue
        result.append({'videoID': key, 'channelID': channel['id'], 'channelName': channel['name'],
                       'title': title[:500], 'published': entry.findtext('a:published', default='', namespaces=NS),
                       'url': f'https://www.youtube.com/watch?v={key}',
                       'status': 'needs_review'})
    return result


def fetch_channel(channel, known):
    try:
        request = urllib.request.Request(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel['id']}", headers={'User-Agent':'NuvioRecapCatalog/1.0'})
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read(1_048_577)
        return parse_feed(raw, channel, known), None
    except Exception as error:
        return [], {'channelID': channel['id'], 'error': type(error).__name__, 'httpStatus': getattr(error, 'code', None)}


def main():
    catalog = validator.validate(ROOT / 'catalog.json')
    known = {v['videoID'] for v in catalog['videos']}
    target = ROOT / 'review/inbox.json'
    previous = json.loads(target.read_text()) if target.exists() else {}
    allowed = {c['id'] for c in catalog['channels']}
    pending = {v['videoID']: v for v in previous.get('candidates', []) if v['videoID'] not in known and v['channelID'] in allowed}
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for candidates, error in pool.map(lambda c: fetch_channel(c, known), catalog['channels']):
            pending.update({v['videoID']: v for v in candidates})
            if error:
                errors.append(error)
        health = list(pool.map(validator.verify_video, (v for v in catalog['videos'] if v['enabled'])))
    report = {'checkedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'catalogRevision': catalog['revision'],
              'candidates': sorted(pending.values(), key=lambda v: v['published'], reverse=True)[:500],
              'feedErrors': errors, 'availabilityReview': [message for ok, message in health if not ok]}
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f"Review inbox: {len(report['candidates'])} candidates; {len(errors)} feed errors; {len(report['availabilityReview'])} availability checks need review")

if __name__ == '__main__':
    main()
