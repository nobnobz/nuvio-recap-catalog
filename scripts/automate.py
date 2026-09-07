#!/usr/bin/env python3
"""Deterministic YouTube discovery. No AI, scraping, or title-only identity guesses.

Only approved channels and exact registered series aliases can auto-publish.
State and evidence are private build inputs, never public Worker assets.
"""
import calendar
import collections
import hashlib
import copy
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.parse
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
MAX_CALLS = 180
ARCHIVE_PAGES = min(20, max(1, int(os.environ.get('RECAP_ARCHIVE_PAGES', '4'))))
POLICY_VERSION = 2
DISCOVERY_VERSION = 1
REJECTION_RECHECK_DAYS = 30
MAX_RECHECKS = 500
TITLE_PATTERN = r'(.+?)\s*(?:[—–:-]\s*)?(?:RECAP\s*:\s*)?Seasons?\s+(\d{1,2})(?:\s*([-–&])\s*(\d{1,2}))?\s*(?:RECAP)?(?:\s*\|\s*(.*))?'


def normalize(text):
    return re.sub(r'[^a-z0-9]+', ' ', text.casefold()).strip()


def six_months_after(value):
    day = dt.date.fromisoformat(value)
    month = day.month + 5
    year, month = day.year + month // 12, month % 12 + 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1])).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rejection_due(record, today, signature):
    return record.get('rulesSignature') != signature or record.get('checkedAt', '') <= (dt.date.fromisoformat(today) - dt.timedelta(days=REJECTION_RECHECK_DAYS)).isoformat()


def rejection_reason(item, channel, series):
    if not item:
        return 'metadata_unavailable'
    snippet, content, status = (item.get(k, {}) for k in ('snippet', 'contentDetails', 'status'))
    if snippet.get('channelId') != channel:
        return 'channel_mismatch'
    language = snippet.get('defaultAudioLanguage', '')
    if not language:
        return 'audio_language_missing'
    if language.lower().split('-')[0] != 'en':
        return 'non_english_audio'
    if status.get('privacyStatus') != 'public' or status.get('uploadStatus') != 'processed' or status.get('embeddable') is not True:
        return 'unavailable_or_not_embeddable'
    if snippet.get('liveBroadcastContent', 'none') != 'none' or content.get('regionRestriction') or content.get('contentRating', {}).get('ytRating') == 'ytAgeRestricted':
        return 'live_region_or_age_restriction'
    if not 60 <= duration(content.get('duration', '')) <= 7200:
        return 'duration_outside_limits'
    title, description = snippet.get('title', ''), snippet.get('description', '')
    match = re.fullmatch(TITLE_PATTERN, title.strip(), re.I)
    if not match or not re.search(r'\brecap\b', title, re.I):
        return 'coverage_title_not_explicit'
    name, start, separator, end, suffix = match.groups()
    start, end = int(start), int(end or start)
    if not (0 < start <= end <= 100 and start in (1, end)) or (separator == '&' and end != start + 1):
        return 'unsupported_or_discontinuous_coverage'
    if not any(normalize(name) in [normalize(alias) for alias in show['aliases']] for show in series):
        return 'series_identity_unresolved'
    if re.search(r'\b(trailer|teaser|prediction|theor(?:y|ies)|episode\s+\d|book spoilers|movie recap)\b', title + ' ' + description, re.I):
        return 'mixed_format_or_description_flag'
    return 'coverage_context_or_suffix_conflict'


def validate_series(series, catalog):
    aliases, imdbs, tmdbs = set(), set(), set()
    established = {v['imdbID']: v['tmdbID'] for v in catalog['videos']}
    for show in series:
        assert re.fullmatch(r'tt[0-9]+', show['imdbID'])
        assert type(show['tmdbID']) is int and show['tmdbID'] > 0
        assert show['imdbID'] not in imdbs and show['tmdbID'] not in tmdbs
        assert established.get(show['imdbID'], show['tmdbID']) == show['tmdbID']
        imdbs.add(show['imdbID']); tmdbs.add(show['tmdbID'])
        assert show['aliases']
        for alias in show['aliases']:
            key = normalize(alias)
            assert key and key not in aliases, 'Ambiguous series alias'
            aliases.add(key)


def coverage(title, series, description=''):
    # Full title grammar, not fuzzy search. "Before season 3" alone does not
    # say whether this covers season 2 or seasons 1-2 and is never admitted.
    match = re.fullmatch(TITLE_PATTERN, title.strip(), re.I)
    if not match or not re.search(r'\brecap\b', title, re.I):
        return None
    name, start, separator, end, suffix = match.groups()
    start, end = int(start), int(end or start)
    if not (0 < start <= end <= 100 and start in (1, end)):
        return None
    # 1 & 3 does not mean 1 through 3.
    if separator == '&' and end != start + 1:
        return None
    if suffix and not re.fullmatch(
        r'(?:(?:Must Watch|Everything You Need To Know) Before Season (\d{1,2})(?: Explained)?(?:\s*\|\s*(?:Apple |Netflix )?Series Explained)?|(?:Apple TV|Max|HBO Max|Series Explained))', suffix, re.I):
        return None
    advertised = re.search(r'Before Season (\d+)', suffix or '', re.I)
    if advertised and int(advertised[1]) != end + 1:
        return None
    matches = [show for show in series if normalize(name) in [normalize(a) for a in show['aliases']]]
    if len(matches) != 1:
        return None
    show = matches[0]
    context = normalize(title + ' ' + description)
    if show.get('requiredContext') and not any(normalize(c) in context for c in show['requiredContext']):
        return None
    # Conservative rejection of mixed formats and explicit contrary coverage.
    if re.search(r'\b(trailer|teaser|prediction|theor(?:y|ies)|episode\s+\d|book spoilers|movie recap)\b', title + ' ' + description, re.I):
        return None
    for mention in re.finditer(r'(?:recap(?: of)?|cover(?:s|ing)?)\s+(?:all\s+)?seasons?\s+(\d+)(?:\s*[-–&]\s*(\d+))?', description, re.I):
        if (int(mention[1]), int(mention[2] or mention[1])) != (start, end):
            return None
    return show, start, end


def duration(value):
    match = re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', value)
    return sum(int(v or 0) * multiplier for v, multiplier in zip(match.groups(), (3600, 60, 1))) if match else 0


def admissible(item, channel, series, today):
    snippet, content, status = (item.get(k, {}) for k in ('snippet', 'contentDetails', 'status'))
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', item.get('id', '')) or snippet.get('channelId') != channel:
        return None
    if snippet.get('defaultAudioLanguage', '').lower().split('-')[0] != 'en':
        return None
    if status.get('privacyStatus') != 'public' or status.get('uploadStatus') != 'processed' or status.get('embeddable') is not True:
        return None
    if snippet.get('liveBroadcastContent', 'none') != 'none' or content.get('regionRestriction') or content.get('contentRating', {}).get('ytRating') == 'ytAgeRestricted':
        return None
    seconds = duration(content.get('duration', ''))
    if not 60 <= seconds <= 7200:
        return None
    match = coverage(snippet.get('title', ''), series, snippet.get('description', ''))
    if not match:
        return None
    show, start, end = match
    return {'videoID': item['id'], 'imdbID': show['imdbID'], 'tmdbID': show['tmdbID'],
            'firstSeason': start, 'lastSeason': end, 'channelID': channel, 'language': 'en',
            'durationSeconds': seconds, 'title': snippet['title'], 'reviewedAt': today, 'enabled': True}


class InvalidPageToken(RuntimeError):
    pass


class Cinemeta:
    """Resolve only a unique exact series title, confirmed by detail metadata."""
    def __init__(self):
        self.calls = 0

    def get(self, path):
        self.calls += 1
        request = urllib.request.Request('https://v3-cinemeta.strem.io/' + path,
                                         headers={'User-Agent': 'NuvioRecaps/2.0'})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read(2_097_153)
            if len(raw) > 2_097_152:
                raise ValueError('Oversized metadata response')
            return json.loads(raw)
        except Exception:
            raise RuntimeError('Series metadata temporarily unavailable') from None

    def resolve(self, name):
        results = self.get('catalog/series/top/search=' + urllib.parse.quote(name, safe='') + '.json')['metas']
        exact = {v['id']: v for v in results if v.get('type') == 'series' and normalize(v.get('name', '')) == normalize(name)}
        if len(exact) != 1:
            return None
        identity = next(iter(exact))
        if not re.fullmatch(r'tt[0-9]+', identity):
            return None
        meta = self.get('meta/series/' + identity + '.json')['meta']
        tmdb = meta.get('moviedb_id')
        if meta.get('type') != 'series' or meta.get('imdb_id') != identity or normalize(meta.get('name', '')) != normalize(name):
            return None
        if type(tmdb) is not int or tmdb <= 0:
            return None
        return {'imdbID': identity, 'tmdbID': tmdb, 'aliases': [name]}


class YouTube:
    def __init__(self, key):
        self.key, self.calls = key, 0

    def get(self, resource, **params):
        if self.calls >= MAX_CALLS:
            raise RuntimeError('API request budget exhausted; no changes saved')
        self.calls += 1
        # Header keeps the credential out of URL errors and request logs.
        url = 'https://www.googleapis.com/youtube/v3/' + resource + '?' + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={'X-Goog-Api-Key': self.key, 'User-Agent': 'NuvioRecaps/2.0'})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read(4_194_305)
            if len(raw) > 4_194_304:
                raise ValueError('Oversized API response')
            data = json.loads(raw)
            if not isinstance(data.get('items'), list):
                raise ValueError('Missing API items')
            return data
        except urllib.error.HTTPError as error:
            try:
                reasons = [e.get('reason') for e in json.loads(error.read(65536)).get('error', {}).get('errors', [])]
            except Exception:
                reasons = []
            if resource == 'playlistItems' and error.code == 400 and 'invalidPageToken' in reasons:
                raise InvalidPageToken('Archive cursor expired') from None
            raise RuntimeError(f'YouTube {resource} failed (HTTP {error.code}); no changes saved') from None
        except Exception as error:
            # Never include URLs, response bodies, headers, or credentials.
            raise RuntimeError(f'YouTube {resource} failed ({type(error).__name__}); no changes saved') from None


def video_items(api, ids):
    result = {}
    ids = sorted(set(ids))
    for offset in range(0, len(ids), 50):
        response = api.get('videos', part='snippet,contentDetails,status', id=','.join(ids[offset:offset + 50]))
        result.update({item['id']: item for item in response['items']})
    return result


def run(catalog, state, series, decisions, api, today, resolver=None):
    validate_series(series, catalog)
    signature = digest({'policyVersion': POLICY_VERSION, 'series': series, 'channels': catalog['channels']})
    catalog, state = copy.deepcopy(catalog), copy.deepcopy(state)
    state.setdefault('channels', {})
    state.setdefault('health', {})
    state.setdefault('approved', {})
    series = copy.deepcopy(series)
    for show in state.get('series', []):
        if not any(v['imdbID'] == show['imdbID'] for v in series):
            series.append(show)
    validate_series(series, catalog)
    state.setdefault('identityChecks', {})
    state.setdefault('rejected', {})
    known = {v['videoID'] for v in catalog['videos']} | set(decisions)
    allowed = {c['id'] for c in catalog['channels']}
    state['rejected'] = {key: row for key, row in state['rejected'].items()
                         if key not in known and (row.get('channelID') is None or row.get('channelID') in allowed)}
    candidates = {key: channel for key, channel in state.get('pending', {}).items() if key not in known and channel in allowed}
    due = sorted(((key, row) for key, row in state['rejected'].items() if rejection_due(row, today, signature)),
                 key=lambda pair: (pair[1].get('checkedAt', ''), pair[0]))[:MAX_RECHECKS]
    candidates.update({key: row.get('channelID') for key, row in due})
    archive_pages = min(ARCHIVE_PAGES, max(0, (MAX_CALLS - 2 - len(catalog['channels']) * 3) // max(1, 2 * len(catalog['channels']))))
    for channel in catalog['channels']:
        channel_id = channel['id']
        checkpoint = state['channels'].setdefault(channel_id, {})
        info = api.get('channels', part='contentDetails', id=channel_id)['items']
        if len(info) != 1 or info[0]['id'] != channel_id:
            raise RuntimeError('Approved channel unavailable; no changes saved')
        playlist = info[0]['contentDetails']['relatedPlaylists']['uploads']
        # Always inspect the newest page. Historical pages advance separately
        # so a large archive cannot starve recent releases or other creators.
        head = api.get('playlistItems', part='snippet,contentDetails', playlistId=playlist, maxResults=50)
        pages = [head]
        token = checkpoint.get('nextPageToken')
        discovery_changed = checkpoint.get('discoveryVersion', DISCOVERY_VERSION) != DISCOVERY_VERSION
        if discovery_changed:
            token = None
            checkpoint.pop('completedAt', None)
        checkpoint['discoveryVersion'] = DISCOVERY_VERSION
        restart = not checkpoint.get('completedAt') or today >= six_months_after(checkpoint['completedAt'])
        if not token and (not checkpoint.get('completedAt') or restart):
            token = head.get('nextPageToken')
        for _ in range(archive_pages):
            if not token:
                break
            try:
                page = api.get('playlistItems', part='snippet,contentDetails', playlistId=playlist, maxResults=50, pageToken=token)
            except InvalidPageToken:
                # Persist a fresh cursor instead of failing on the same stale
                # token forever. The head was already processed this run.
                token = head.get('nextPageToken')
                checkpoint.pop('completedAt', None)
                break
            pages.append(page)
            token = page.get('nextPageToken')
        checkpoint['nextPageToken'] = token
        if not token:
            checkpoint['completedAt'] = today if restart or not checkpoint.get('completedAt') else checkpoint['completedAt']
        for page in pages:
            for item in page['items']:
                key = item.get('contentDetails', {}).get('videoId', '')
                title = item.get('snippet', {}).get('title', '')
                if key not in known and rejection_due(state['rejected'].get(key, {}), today, signature) and re.fullmatch(r'[A-Za-z0-9_-]{11}', key) and re.search(r'\brecap\b', title, re.I):
                    candidates[key] = channel_id
    details = video_items(api, candidates)
    added, pending = [], {}
    resolutions, identity_errors, unchanged_rechecks = 0, 0, 0
    attempted_names = set()
    for key, channel in sorted(candidates.items()):
        item = details.get(key)
        if channel is None:
            channel = item.get('snippet', {}).get('channelId') if item else None
        if channel not in allowed:
            if item is None:
                state['rejected'][key] = {**state['rejected'].get(key, {}), 'channelID': channel,
                    'reason': 'metadata_unavailable', 'checkedAt': today, 'rulesSignature': signature}
            else:
                state['rejected'].pop(key, None)
            continue
        fingerprint = digest({k: item.get(k) for k in ('snippet', 'contentDetails', 'status')} if item else None)
        prior = state['rejected'].get(key, {})
        if prior.get('fingerprint') == fingerprint and prior.get('rulesSignature') == signature and key not in state.get('pending', {}):
            prior['checkedAt'] = today
            unchanged_rechecks += 1
            continue
        if item and resolver:
            snippet = item.get('snippet', {})
            title = re.fullmatch(TITLE_PATTERN, snippet.get('title', '').strip(), re.I)
            name = title[1].strip(' —–:-') if title else ''
            registered = any(normalize(name) in [normalize(a) for a in show['aliases']] for show in series)
            if name and not registered and snippet.get('defaultAudioLanguage', '').lower().split('-')[0] == 'en':
                checked = state['identityChecks'].get(normalize(name), '')
                due = (key in state.get('pending', {}) or prior.get('rulesSignature') != signature or checked <= (dt.date.fromisoformat(today) - dt.timedelta(days=REJECTION_RECHECK_DAYS)).isoformat()) and normalize(name) not in attempted_names
                if due and resolutions < 8:
                    resolutions += 1
                    attempted_names.add(normalize(name))
                    try:
                        show = resolver.resolve(name)
                        if show:
                            # Reject remapped IDs, alias collisions and sequels
                            # that resolve to an already known different title.
                            validate_series(series + [show], catalog)
                            series.append(show)
                            state.setdefault('series', []).append(show)
                        state['identityChecks'][normalize(name)] = today
                    except (AssertionError, KeyError, ValueError):
                        state['identityChecks'][normalize(name)] = today
                    except RuntimeError:
                        identity_errors += 1
                        pending[key] = channel
                elif due:
                    pending[key] = channel
        video = admissible(item, channel, series, today) if item else None
        if video:
            catalog['videos'].append(video)
            state['approved'][key] = {'policyVersion': POLICY_VERSION, 'checkedAt': today,
                                      'audioLanguage': item['snippet']['defaultAudioLanguage']}
            added.append(key)
            state['rejected'].pop(key, None)
        else:
            snippet = item.get('snippet', {}) if item else {}
            description = snippet.get('description', '')
            flag = re.search(r'.{0,70}\b(?:trailer|teaser|prediction|theor(?:y|ies)|episode\s+\d|book spoilers|movie recap)\b.{0,70}', description, re.I)
            state['rejected'][key] = {'channelID': channel, 'title': snippet.get('title', ''),
                'reason': rejection_reason(item, channel, series), 'checkedAt': today,
                'fingerprint': fingerprint, 'rulesSignature': signature,
                'audioLanguage': snippet.get('defaultAudioLanguage', ''),
                'descriptionLead': re.sub(r'https?://\S+', '[link]', description)[:500],
                'flagExcerpt': flag[0] if flag else ''}
    # Rotating health coverage; two transient misses never withdraw a video.
    # A repeated run on the same day cannot count as another confirmation.
    existing = sorted((v for v in catalog['videos'] if v['videoID'] not in added and v['videoID'] not in decisions
                       and (v['enabled'] or state['health'].get(v['videoID'], {}).get('autoDisabled'))), key=lambda v: v['videoID'])
    start = (dt.date.fromisoformat(today).toordinal() * 60) % len(existing) if existing else 0
    batch = (existing[start:] + existing[:start])[:60]
    health = video_items(api, [v['videoID'] for v in batch])
    withdrawn, restored = [], []
    for video in batch:
        key = video['videoID']
        item = health.get(key)
        record = state['health'].setdefault(key, {})
        status = item.get('status', {}) if item else {}
        unavailable = item is None or status.get('privacyStatus') in ('private', 'unlisted') or status.get('uploadStatus') in ('deleted', 'failed', 'rejected')
        if unavailable:
            if record.get('checkedAt') != today:
                record['misses'] = record.get('misses', 0) + 1
            record['checkedAt'] = today
            if record['misses'] >= 3 and video['enabled']:
                video['enabled'] = False
                record['autoDisabled'] = True
                withdrawn.append(key)
        else:
            record['misses'] = 0
            record['checkedAt'] = today
            snippet = item.get('snippet', {})
            same = snippet.get('channelId') == video['channelID'] and snippet.get('title') == video['title'] and duration(item.get('contentDetails', {}).get('duration', '')) == video['durationSeconds']
            if record.get('autoDisabled') and same and status.get('privacyStatus') == 'public' and status.get('uploadStatus') == 'processed' and status.get('embeddable') is True:
                video['enabled'] = True
                record['autoDisabled'] = False
                restored.append(key)
    if added or withdrawn or restored:
        catalog['revision'] += 1
    state['pending'] = dict(list(pending.items())[:500])
    state['checkedAt'] = today
    report = {'checkedAt': today, 'policyVersion': POLICY_VERSION, 'catalogRevision': catalog['revision'],
              'candidatesChecked': len(candidates), 'added': added, 'withdrawn': withdrawn, 'restored': restored,
              'skipped': sorted(set(candidates) - set(added)), 'healthChecked': len(batch),
              'apiCalls': api.calls, 'seriesResolutions': resolutions, 'identityErrors': identity_errors,
              'pendingIdentities': len(state['pending']), 'unchangedRechecks': unchanged_rechecks,
              'rejectionsByReason': dict(collections.Counter(v['reason'] for v in state['rejected'].values())),
              'rejectionsTracked': len(state['rejected']), 'archivesComplete': all(c.get('completedAt') and not c.get('nextPageToken') for c in state['channels'].values())}
    return catalog, state, report


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write('\n')
        temporary = Path(file.name)
    temporary.replace(path)


def main():
    key = os.environ.get('YOUTUBE_API_KEY')
    if not key:
        raise SystemExit('YOUTUBE_API_KEY is missing. Add the repository secret to activate automatic discovery.')
    spec = importlib.util.spec_from_file_location('validator', ROOT / 'scripts/check-recap-catalog.py')
    validator = importlib.util.module_from_spec(spec); spec.loader.exec_module(validator)
    catalog = validator.validate(ROOT / 'catalog.json')
    state_path = ROOT / 'review/automation-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    series = json.loads((ROOT / 'series.json').read_text())
    decisions = json.loads((ROOT / 'review/decisions.json').read_text())
    result, state, report = run(catalog, state, series, decisions, YouTube(key), dt.datetime.now(dt.timezone.utc).date().isoformat(), Cinemeta())
    # Validate the entire transaction before replacing any on-disk input.
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / 'catalog.json'
        write_json(candidate, result)
        validator.validate(candidate)
    if result != catalog:
        write_json(ROOT / 'catalog.json', result)
    write_json(state_path, state)
    write_json(ROOT / 'review/automation-report.json', report)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
