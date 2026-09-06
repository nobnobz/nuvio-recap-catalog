#!/usr/bin/env python3
"""Validate the editorial catalog; --online checks live YouTube identity/duration.
Never adds search results, changes coverage, or modifies the catalog automatically.
"""
import argparse
import concurrent.futures
import datetime
import json
from pathlib import Path
import re
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'tvosApp/NuvioTV/Resources/SeasonRecapCatalog.json'


def validate(path):
    raw = path.read_bytes()
    assert len(raw) <= 1048576, 'Catalog exceeds 1 MiB'
    data = json.loads(raw)
    assert data['schemaVersion'] == 1 and data['revision'] > 0, 'Invalid schema/revision'
    assert len(data['channels']) <= 30 and len(data['videos']) <= 3000, 'Catalog exceeds limits'
    channels = {}
    for channel in data['channels']:
        key = channel['id']
        assert re.fullmatch(r'UC[A-Za-z0-9_-]{22}', key), 'Invalid channel ID'
        assert key not in channels and 0 <= channel['rank'] <= 100, 'Duplicate/invalid channel'
        assert 0 < len(channel['name']) <= 64 and isinstance(channel['official'], bool)
        channels[key] = channel
    ids, imdb, tmdb = set(), {}, {}
    for video in data['videos']:
        key = video['videoID']
        assert re.fullmatch(r'[A-Za-z0-9_-]{11}', key) and key not in ids, 'Duplicate/invalid video ID'
        ids.add(key)
        assert video['channelID'] in channels, f'{key}: unapproved channel'
        assert video['language'] == 'en', f'{key}: English only'
        start, end = video['firstSeason'], video['lastSeason']
        assert 0 < start <= end <= 100 and start in (1, end), f'{key}: invalid coverage'
        assert 30 <= video['durationSeconds'] <= 7200 and video['title'], f'{key}: invalid metadata'
        assert isinstance(video['enabled'], bool)
        datetime.date.fromisoformat(video['reviewedAt'])
        i, t = video['imdbID'], video['tmdbID']
        assert re.fullmatch(r'tt[0-9]+', i) and t > 0, f'{key}: invalid series identity'
        assert imdb.get(i, t) == t and tmdb.get(t, i) == i, f'{key}: conflicting identities'
        imdb[i], tmdb[t] = t, i
    print(f"Valid catalog r{data['revision']}: {len(ids)} videos, {len(imdb)} series, {len(channels)} approved channels, {len(raw)} bytes")
    return data


def verify_video(video):
    key = video['videoID']
    try:
        request = urllib.request.Request(f'https://www.youtube.com/watch?v={key}&hl=en', headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=15) as response:
            html = response.read(8 * 1024 * 1024).decode()
        match = re.search(r'var ytInitialPlayerResponse\s*=\s*', html)
        assert match, 'No player metadata'
        player = json.JSONDecoder().raw_decode(html[match.end():])[0]
        details = player.get('videoDetails', {})
        assert details.get('videoId') == key, 'Video identity mismatch'
        assert details.get('channelId') == video['channelID'], 'Channel identity mismatch'
        assert abs(int(details.get('lengthSeconds', 0)) - video['durationSeconds']) <= 1, 'Duration changed; review required'
        assert player.get('playabilityStatus', {}).get('status') == 'OK', 'Watch-page playback unavailable; review required'
        return True, f"OK {key}: {details.get('author')} / {details.get('lengthSeconds')}s"
    except Exception as error:
        # Do not echo response bodies or temporary signed stream URLs.
        return False, f'REVIEW {key}: {type(error).__name__}: {str(error)[:120]}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=DEFAULT)
    parser.add_argument('--online', action='store_true')
    args = parser.parse_args()
    data = validate(args.catalog)
    if args.online:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(verify_video, (v for v in data['videos'] if v['enabled'])))
        for _, message in results:
            print(message)
        return 0 if all(ok for ok, _ in results) else 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
