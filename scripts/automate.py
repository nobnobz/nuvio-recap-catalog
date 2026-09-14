#!/usr/bin/env python3
"""Deterministic YouTube discovery. No AI, scraping, or title-only identity guesses.

Only approved channels and exact registered series/movie aliases can auto-publish.
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
POLICY_VERSION = 8
DISCOVERY_VERSION = 2
REJECTION_RECHECK_DAYS = 30
MAX_RECHECKS = 500
MAX_RESOLUTIONS = min(32, max(1, int(os.environ.get('RECAP_IDENTITY_CHECKS', '8'))))
TITLE_PATTERN = r'(.+?)\s*(?:[—–:-]\s*)?(?:RECAP\s*:\s*)?Seasons?\s+(\d{1,2})(?:\s*([-–&])\s*(\d{1,2}))?\s*(?:RECAP)?(?:\s*\|\s*(.*))?'
SEASON_NUMBER = r'(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)'
SEASON_SCOPE_PATTERN = re.compile(
    r'\bSeasons?\s+(' + SEASON_NUMBER + r')(?:\s*([-–&]|and|to)\s*(' + SEASON_NUMBER + r'))?', re.I)
SEASON_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
                'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}
MOVIE_RECAP_MARKER = re.compile(
    r'\b(?:(?:ultimate|full)\s+)?(?:(?:story|movie|film)\s+)?recap\b', re.I)
MOVIE_YEAR_PATTERN = re.compile(r'(?:\(((?:19|20)\d{2})\)|\s+((?:19|20)\d{2}))$')
MOVIE_NUMBER_WORDS = re.compile(r'\b(one|two|three|four|five|six|seven|eight|nine|ten)\b', re.I)


def normalize(text):
    return re.sub(r'[^a-z0-9]+', ' ', text.casefold()).strip()


def numeric_word(value):
    value = value.casefold()
    return int(value) if value.isdigit() else SEASON_WORDS.get(value)


def trim_delimiters(value):
    return re.sub(r'\s*[|—–:-]\s*$', '', value.strip()).strip()


def movie_year(value):
    match = MOVIE_YEAR_PATTERN.search(value.strip())
    return int(match[1] or match[2]) if match else None


def movie_base_title(value):
    value = value.strip()
    value = re.sub(r'\s*\((?:19|20)\d{2}\)\s*$', '', value)
    value = re.sub(r'\s+(?:19|20)\d{2}\s*$', '', value)
    return value.strip()


def movie_title_key(value):
    value = re.sub(r'\bchapter\s+(?=\d{1,2}\b)', '', movie_base_title(value), flags=re.I)
    value = MOVIE_NUMBER_WORDS.sub(lambda m: str(SEASON_WORDS[m[1].casefold()]), value)
    return normalize(value)


def movie_identity_matches(name, movie):
    source_year = movie_year(name)
    identity_year = movie.get('releaseYear')
    if source_year is not None and identity_year is not None and source_year != identity_year:
        return False
    if source_year is not None and identity_year is None:
        return False
    return any(movie_title_key(name) == movie_title_key(alias) for alias in movie.get('aliases', []))


def six_months_after(value):
    day = dt.date.fromisoformat(value)
    month = day.month + 5
    year, month = day.year + month // 12, month % 12 + 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1])).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rejection_due(record, today, signature):
    return record.get('rulesSignature') != signature or record.get('checkedAt', '') <= (dt.date.fromisoformat(today) - dt.timedelta(days=REJECTION_RECHECK_DAYS)).isoformat()


FULL_TITLE_PATTERN = r'(.+?) Full Series Recap\s*\|\s*Seasons? (\d{1,2}(?:\s*[-–&]\s*\d{1,2})?) (?:Ending )?Explained'


NUMBER_SCOPE = r'\d{1,2}(?:\s*(?:[-–&+]|,\s*(?:and\s+)?|\band\b)\s*\d{1,2})*'
DESCRIPTION_TITLE_PATTERN = r'(.+?)\s*(?:[—–:-]\s*)?RECAP\s*:\s*Full Series(?: before the Final Season)?'


def numeric_scope(value):
    numbers = [int(n) for n in re.findall(r'\d+', value)]
    if len(numbers) == 2 and re.search(r'[-–]', value):
        numbers = list(range(numbers[0], numbers[1] + 1))
    if not numbers or not 0 < numbers[0] <= numbers[-1] <= 100:
        return None
    if numbers != list(range(numbers[0], numbers[-1] + 1)):
        return None
    return numbers[0], numbers[-1]


def clean_title(title):
    # Only trailing hashtag tokens, not arbitrary trailing prose.
    return re.sub(r'(?:\s+#[\w]+)+\s*$', '', title).strip()


def full_series_title(title):
    return bool(re.fullmatch(FULL_TITLE_PATTERN, clean_title(title), re.I) or
                re.fullmatch(DESCRIPTION_TITLE_PATTERN, clean_title(title), re.I))


def description_coverage(description):
    claims = list(re.finditer(r'\bfull series recap of seasons?\s+(' + NUMBER_SCOPE + r')', description, re.I))
    spans = [numeric_scope(m[1]) for m in claims]
    if not spans or spans[0] is None or spans[0][0] != 1 or any(v != spans[0] for v in spans):
        return None
    # Independent chapter evidence must enumerate exactly the declared seasons.
    chapters = re.findall(r'(?im)^\s*Season (\d{1,2})\s+\d{1,2}:\d{2}(?::\d{2})?\s*$', description)
    if [int(n) for n in chapters] != list(range(spans[0][0], spans[0][1] + 1)):
        return None
    return spans[0]


def parse_explicit_season_title(title):
    """Return the existing match-shaped series result for common title layouts.

    The canonical form remains ``Name Season 1-2 Recap | suffix``.  Keeping
    that shape lets the strict coverage code below retain its fail-closed
    behavior while accepting pipes, word-number ranges and publisher formats
    used by the approved channels.
    """
    title = clean_title(title)
    scopes = list(SEASON_SCOPE_PATTERN.finditer(title))
    if not scopes:
        return None
    scope = scopes[0]
    prefix = title[:scope.start()]
    had_prefix_recap = bool(re.search(r'\brecap\b', prefix, re.I))
    name = re.sub(r'\b(?:ultimate\s+)?recap\s*[:\-–—|]*\s*$', '', prefix, flags=re.I).strip()
    name = trim_delimiters(name)
    if not name or '|' in name:
        return None
    start = numeric_word(scope[1])
    end = numeric_word(scope[3]) if scope[3] else start
    if start is None or end is None:
        return None
    connector = scope[2].casefold() if scope[2] else None
    separator = '&' if connector in ('&', 'and') else '-' if connector in ('-', '–', 'to') else None
    rest = title[scope.end():].strip()
    marker = MOVIE_RECAP_MARKER.search(rest)
    if marker:
        before = rest[:marker.start()].strip(' \t|—–:-!.,;')
        # "in Minutes" is a format label, not coverage context. Other text
        # remains in the suffix and is checked by valid_suffix().
        suffix_parts = [] if not before or normalize(before) == 'in minutes' else [before]
        suffix_parts.append(rest[marker.end():])
        suffix = ' | '.join(piece for piece in suffix_parts if piece.strip()).strip().strip('—–:-!.,;|').strip()
    elif had_prefix_recap:
        suffix = rest.strip('—–:-!.,;|').strip()
    else:
        return None
    canonical = f'{name} Season {start}'
    if end != start:
        canonical += f' {separator or "-"} {end}'
    canonical += ' Recap'
    if suffix:
        canonical += f' | {suffix}'
    return re.fullmatch(TITLE_PATTERN, canonical, re.I)


def parse_title(title, description=''):
    title = clean_title(title)
    full = re.fullmatch(FULL_TITLE_PATTERN, title, re.I)
    described = re.fullmatch(DESCRIPTION_TITLE_PATTERN, title, re.I)
    if full:
        title = f'{full[1]} Season {full[2]} Recap'
    elif described:
        span = description_coverage(description)
        if span is None:
            return None
        title = f'{described[1].strip(" —–:-")} Season {span[0]}-{span[1]} Recap'
    match = parse_explicit_season_title(title)
    return match if match and '|' not in match[1] else None


def parse_movie_title(title):
    """Parse a single-film recap heading without accepting series coverage."""
    title = clean_title(title)
    if re.search(r'\b(?:full\s+series|seasons?|episodes?)\b', title, re.I):
        return None
    in_minutes = re.search(r'\bin\s+minutes\b', title, re.I)
    if in_minutes:
        if not re.search(r'\brecap\b', title[in_minutes.end():], re.I):
            return None
        name = title[:in_minutes.start()]
        suffix = title[in_minutes.end():]
    else:
        marker = MOVIE_RECAP_MARKER.search(title)
        if not marker:
            return None
        name = title[:marker.start()]
        suffix = title[marker.end():]
    name = re.sub(r'\bfull\s*$', '', name, flags=re.I).strip()
    name = trim_delimiters(name)
    if not name or '|' in name:
        return None
    return {'name': name, 'suffix': suffix.strip(), 'inMinutes': bool(in_minutes)}


def mixed_format(title, description):
    # An actor credit is not a prediction or fan theory; other occurrences stay.
    description = re.sub(r"\b[A-Z][a-z]+ Theory as [A-Z][A-Za-z'’–-]+", '[cast credit]', description)
    return bool(re.search(r'\b(trailer|teaser|prediction|theor(?:y|ies)|episode\s+\d|book spoilers|movie recap)\b|#(?:shorts|highlights)\b', title + ' ' + description, re.I))


def mixed_full_series(description):
    description = re.sub(r'\bthe two-part final season\b', 'the final season', description, flags=re.I)
    return bool(re.search(r'\b(?:film|movie|parts?|volumes?|miniseries|spin.?off)\b', description, re.I))


def mixed_movie_format(title, description):
    # Channel descriptions often contain links to related recap playlists.
    # Those links are promotion, not evidence that the current upload covers
    # every film named by the linked playlist. Remove only clearly delimited
    # linked headings; unlinked scope claims remain fail-closed.
    lines = description.splitlines()
    kept = []
    link_only = re.compile(r'\s*(?:https?://\S+|\[link\])\s*$', re.I)
    heading = re.compile(
        r'(?:\bplaylist\b|\bcheck\s+out\s+more\s+movies\s+in\s+minutes\b|'
        r'\b(?:every|all)\b.+\b(?:films?|movies?)\s+recap\s*:?)', re.I)
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        without_link = re.sub(r'\s*(?:https?://\S+|\[link\])\s*$', '', stripped).strip()
        if heading.search(without_link):
            if without_link != stripped or (index + 1 < len(lines) and link_only.fullmatch(lines[index + 1])):
                index += 2 if index + 1 < len(lines) and link_only.fullmatch(lines[index + 1]) else 1
                continue
        kept.append(line)
        index += 1
    text = title + ' ' + '\n'.join(kept)
    if re.search(r'\b(?:trailer|teaser|prediction|theor(?:y|ies)|episode\s+\d|book spoilers|series recap)\b|#(?:shorts|highlights)\b', text, re.I):
        return True
    # A recap of a named film is admissible; a franchise/collection recap is
    # not representable as one movie identity.
    return bool(re.search(r'\b(?:every|all)\b.{0,100}\b(?:films?|movies?)\b|\b(?:film|movie)\s+franchise\b|\b(?:complete|full)\s+(?:film|movie)\s+(?:saga|collection)\b|\bcompilation\b', text, re.I))


def content_description(description):
    # Publisher service advertisements are not a description of this video.
    description = re.split(r'(?im)^About (?:HBO Max|Max|Netflix|Prime Video|Disney(?: Plus|\+)?):\s*$', description, maxsplit=1)[0]
    # Ignore a standalone link to another recap, never an unlinked scope claim.
    return '\n'.join(line for line in description.splitlines() if not re.fullmatch(
        r'\s*Watch\b[^\n]*\brecap\b[^\n]*https?://\S+\s*', line, re.I))


def valid_suffix(suffix, name, end):
    for piece in (suffix or '').split('|'):
        piece = piece.strip().strip('—–:-!.,;').strip()
        if not piece or normalize(piece) == normalize(name):
            continue
        if re.fullmatch(r'(?:Apple TV(?: Plus)?|Max|HBO Max|Netflix|Prime Video|Disney(?: Plus|\+)?|HBO|Hulu|Starz|Showtime)', piece, re.I):
            continue
        if re.fullmatch(r'(?:(?:TV|Apple|Apple TV(?: Plus)?|Netflix|Prime Video|Disney(?: Plus|\+)?|HBO(?: Max)?|Hulu|Starz|Showtime|Amazon(?: Prime Video)?) )?Series Explained', piece, re.I):
            continue
        if re.fullmatch(r'in Minutes', piece, re.I):
            continue
        before = re.fullmatch(r'(?:Must Watch|Everything You Need To Know) Before (.*?)Season (\d{1,2})(?: Explained)?', piece, re.I)
        if before and (not before[1].strip() or normalize(before[1]) == normalize(name)) and int(before[2]) == end + 1:
            continue
        return False
    return True


def valid_movie_suffix(suffix, name):
    for piece in (suffix or '').split('|'):
        piece = piece.strip().strip('—–:-!.,;').strip()
        if not piece or movie_title_key(piece) == movie_title_key(name):
            continue
        if re.fullmatch(r'(?:Apple TV(?: Plus)?|Max|HBO Max|Netflix|Prime Video|Disney(?: Plus|\+)?|HBO|Hulu|Starz|Showtime)', piece, re.I):
            continue
        if re.fullmatch(r'(?:in Minutes|recap|(?:ultimate|full|story|movie|film)\s+recap)', piece, re.I):
            continue
        if re.fullmatch(r'\([^)]*\b(?:ultimate\s+)?(?:story\s+)?recap\b[^)]*\)', piece, re.I):
            continue
        if re.fullmatch(r'(?:watch|must watch|everything you need to know)\s+before\b.*', piece, re.I):
            continue
        return False
    return True


def movie_coverage(title, movies, description=''):
    parsed = parse_movie_title(title)
    if not parsed or not re.search(r'\brecap\b', title, re.I):
        return None
    name, suffix = parsed['name'], parsed['suffix']
    if not valid_movie_suffix(suffix, name):
        return None
    description = content_description(description)
    if mixed_movie_format(title, description):
        return None
    matches = [movie for movie in movies if movie_identity_matches(name, movie)]
    if len(matches) != 1:
        return None
    movie = matches[0]
    context = normalize(title + ' ' + description)
    if movie.get('requiredContext') and not any(normalize(c) in context for c in movie['requiredContext']):
        return None
    return movie


def rejection_reason(item, channel, series, movies=None):
    movies = movies or []
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
    title, description = snippet.get('title', ''), content_description(snippet.get('description', ''))
    movie = parse_movie_title(title)
    if movie:
        if not valid_movie_suffix(movie['suffix'], movie['name']) or mixed_movie_format(title, description):
            return 'mixed_format_or_description_flag'
        if not any(movie_identity_matches(movie['name'], entry) for entry in movies):
            return 'movie_identity_unresolved'
        if not movie_coverage(title, movies, description):
            return 'movie_context_or_suffix_conflict'
        return 'movie_context_or_suffix_conflict'
    match = parse_title(title, description)
    if not match or not re.search(r'\brecap\b', title, re.I):
        return 'coverage_title_not_explicit'
    name, start, separator, end, suffix = match.groups()
    start, end = int(start), int(end or start)
    if not (0 < start <= end <= 100 and start in (1, end)) or (separator == '&' and end != start + 1):
        return 'unsupported_or_discontinuous_coverage'
    if not valid_suffix(suffix, name, end):
        return 'coverage_context_or_suffix_conflict'
    if mixed_format(title, description):
        return 'mixed_format_or_description_flag'
    if full_series_title(title) and mixed_full_series(description):
        return 'mixed_format_or_description_flag'
    if not any(normalize(name) in [normalize(alias) for alias in show['aliases']] for show in series):
        return 'series_identity_unresolved'
    if full_series_title(title):
        show = next(show for show in series if normalize(name) in [normalize(alias) for alias in show['aliases']])
        if not all(n in show.get('seasonNumbers', []) for n in range(start, end + 1)):
            return 'season_numbering_unverified'
    return 'coverage_context_or_suffix_conflict'


def validate_series(series, catalog):
    validate_media_registry(series, catalog, 'series')


def validate_movies(movies, catalog):
    validate_media_registry(movies, catalog, 'movie')


def validate_media_registry(entries, catalog, media_type):
    aliases, imdbs, tmdbs = {}, set(), set()
    established = {v['imdbID']: v['tmdbID'] for v in catalog['videos']
                   if v.get('mediaType', 'series') == media_type}
    for entry in entries:
        assert re.fullmatch(r'tt[0-9]+', entry['imdbID'])
        assert type(entry['tmdbID']) is int and entry['tmdbID'] > 0
        assert entry['imdbID'] not in imdbs and entry['tmdbID'] not in tmdbs
        assert established.get(entry['imdbID'], entry['tmdbID']) == entry['tmdbID']
        if media_type == 'movie' and 'releaseYear' in entry:
            assert type(entry['releaseYear']) is int and 1888 <= entry['releaseYear'] <= 2100
        imdbs.add(entry['imdbID']); tmdbs.add(entry['tmdbID'])
        assert entry['aliases']
        for alias in entry['aliases']:
            assert isinstance(alias, str)
            if media_type == 'movie':
                year = movie_year(alias) or entry.get('releaseYear')
                key = f'{movie_title_key(alias)}|{year or ""}'
            else:
                key = normalize(alias)
            assert key and (key not in aliases or aliases[key] == entry['imdbID']), f'Ambiguous {media_type} alias'
            aliases[key] = entry['imdbID']


def coverage(title, series, description=''):
    # Full title grammar, not fuzzy search. "Before season 3" alone does not
    # say whether this covers season 2 or seasons 1-2 and is never admitted.
    match = parse_title(title, description)
    if not match or not re.search(r'\brecap\b', title, re.I):
        return None
    name, start, separator, end, suffix = match.groups()
    start, end = int(start), int(end or start)
    if not (0 < start <= end <= 100 and start in (1, end)):
        return None
    # 1 & 3 does not mean 1 through 3.
    if separator == '&' and end != start + 1:
        return None
    if not valid_suffix(suffix, name, end):
        return None
    description = content_description(description)
    matches = [show for show in series if normalize(name) in [normalize(a) for a in show['aliases']]]
    if len(matches) != 1:
        return None
    show = matches[0]
    if full_series_title(title):
        # Cross-check uploader numbering against the app's episode metadata.
        if start != 1 or not all(n in show.get('seasonNumbers', []) for n in range(start, end + 1)):
            return None
        if mixed_full_series(description):
            return None
        for span in re.finditer(r'\bseasons?\s+(\d+)\s*[-–&]\s*(\d+)', description, re.I):
            if (int(span[1]), int(span[2])) != (start, end):
                return None
        for before in re.finditer(r'\b(?:before|(?:prepare|get)[^.\n]{0,60}for)\s+(?:\w+\s+){0,6}?season\s+(\d+)', description, re.I):
            if int(before[1]) <= end:
                return None
    context = normalize(title + ' ' + description)
    if show.get('requiredContext') and not any(normalize(c) in context for c in show['requiredContext']):
        return None
    # Conservative rejection of mixed formats and explicit contrary coverage.
    if mixed_format(title, description):
        return None
    for mention in re.finditer(r'(?:recap(?: of)?|cover(?:s|ing)?)\s+(?:all\s+)?seasons?\s+(' + NUMBER_SCOPE + r')', description, re.I):
        if numeric_scope(mention[1]) != (start, end):
            return None
    return show, start, end


def duration(value):
    match = re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', value)
    return sum(int(v or 0) * multiplier for v, multiplier in zip(match.groups(), (3600, 60, 1))) if match else 0


def admissible(item, channel, series, today, movies=None):
    movies = movies or []
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
    movie = movie_coverage(snippet.get('title', ''), movies, snippet.get('description', ''))
    if movie:
        return {'videoID': item['id'], 'imdbID': movie['imdbID'], 'tmdbID': movie['tmdbID'],
                'mediaType': 'movie', 'channelID': channel, 'language': 'en',
                'durationSeconds': seconds, 'title': snippet['title'], 'reviewedAt': today, 'enabled': True}
    match = coverage(snippet.get('title', ''), series, snippet.get('description', ''))
    if not match:
        return None
    show, start, end = match
    return {'videoID': item['id'], 'imdbID': show['imdbID'], 'tmdbID': show['tmdbID'], 'mediaType': 'series',
            'firstSeason': start, 'lastSeason': end, 'channelID': channel, 'language': 'en',
            'durationSeconds': seconds, 'title': snippet['title'], 'reviewedAt': today, 'enabled': True}


class InvalidPageToken(RuntimeError):
    pass


class PlaylistNotFound(RuntimeError):
    pass


class Cinemeta:
    """Resolve only unique exact series/movie titles, confirmed by details."""
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
            raise RuntimeError('Media metadata temporarily unavailable') from None

    def season_numbers(self, show):
        meta = self.get('meta/series/' + show['imdbID'] + '.json')['meta']
        if meta.get('imdb_id') != show['imdbID'] or meta.get('moviedb_id') != show['tmdbID'] or meta.get('type') != 'series':
            return []
        return sorted({v['season'] for v in meta.get('videos', []) if type(v.get('season')) is int and v['season'] > 0})

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

    def resolve_movie(self, name):
        query = movie_base_title(name) or name
        results = self.get('catalog/movie/top/search=' + urllib.parse.quote(query, safe='') + '.json')['metas']
        year = movie_year(name)
        exact = {}
        for value in results:
            result_name = value.get('name', '')
            if (value.get('type') != 'movie' or not isinstance(result_name, str) or
                    movie_title_key(result_name) != movie_title_key(name)):
                continue
            release = str(value.get('releaseInfo', '')).strip()
            if year is not None and release != str(year):
                continue
            identity = value.get('id', '')
            if isinstance(identity, str) and re.fullmatch(r'tt[0-9]+', identity):
                exact[identity] = value
        if len(exact) != 1:
            return None
        identity = next(iter(exact))
        if not re.fullmatch(r'tt[0-9]+', identity):
            return None
        meta = self.get('meta/movie/' + identity + '.json')['meta']
        tmdb = meta.get('moviedb_id')
        meta_year = str(meta.get('releaseInfo', '')).strip()
        meta_name = meta.get('name', '')
        if (meta.get('type') != 'movie' or meta.get('imdb_id') != identity or
                not isinstance(meta_name, str) or movie_title_key(meta_name) != movie_title_key(name)):
            return None
        if year is not None and meta_year != str(year):
            return None
        if type(tmdb) is not int or tmdb <= 0:
            return None
        release_year = year
        if release_year is None and re.fullmatch(r'(?:19|20)\d{2}', meta_year):
            release_year = int(meta_year)
        result = {'imdbID': identity, 'tmdbID': tmdb, 'aliases': [name, meta_name]}
        if release_year is not None:
            result['releaseYear'] = release_year
        return result


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
            if resource == 'playlistItems' and error.code == 404:
                raise PlaylistNotFound('Uploads playlist unavailable') from None
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


def run(catalog, state, series, decisions, api, today, resolver=None, movies=None):
    movies = [] if movies is None else movies
    validate_series(series, catalog)
    validate_movies(movies, catalog)
    signature = digest({'policyVersion': POLICY_VERSION, 'series': series, 'movies': movies,
                        'channels': catalog['channels']})
    catalog, state = copy.deepcopy(catalog), copy.deepcopy(state)
    state.setdefault('channels', {})
    state.setdefault('health', {})
    state.setdefault('approved', {})
    series = copy.deepcopy(series)
    movies = copy.deepcopy(movies)
    for show in state.get('series', []):
        if 'seasonNumbers' in show:
            show.setdefault('seasonNumbersCheckedAt', today)
        established = next((v for v in series if v['imdbID'] == show['imdbID']), None)
        if established is None:
            series.append(show)
        elif 'seasonNumbers' in show:
            established['seasonNumbers'] = show['seasonNumbers']
            established['seasonNumbersCheckedAt'] = show.get('seasonNumbersCheckedAt', today)
    for movie in state.get('movies', []):
        established = next((v for v in movies if v['imdbID'] == movie['imdbID']), None)
        if established is None:
            movies.append(movie)
    validate_series(series, catalog)
    validate_movies(movies, catalog)
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
    channel_errors = []
    for channel in catalog['channels']:
        channel_id = channel['id']
        checkpoint = state['channels'].setdefault(channel_id, {})
        try:
            info = api.get('channels', part='contentDetails', id=channel_id)['items']
        except RuntimeError as error:
            raise RuntimeError(f'Approved channel {channel["name"]} ({channel_id}) lookup failed: {error}') from None
        if len(info) != 1 or info[0]['id'] != channel_id:
            raise RuntimeError('Approved channel unavailable; no changes saved')
        playlist = info[0]['contentDetails']['relatedPlaylists']['uploads']
        # Always inspect the newest page. Historical pages advance separately
        # so a large archive cannot starve recent releases or other creators.
        try:
            head = api.get('playlistItems', part='snippet,contentDetails', playlistId=playlist, maxResults=50)
        except PlaylistNotFound:
            checkpoint.pop('nextPageToken', None)
            checkpoint['completedAt'] = today
            checkpoint['uploadsUnavailableAt'] = today
            channel_errors.append({'channelID': channel_id, 'channelName': channel['name'],
                                   'reason': 'uploads_playlist_not_found'})
            continue
        except RuntimeError as error:
            raise RuntimeError(f'Approved channel {channel["name"]} ({channel_id}) uploads failed: {error}') from None
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
    resolutions, series_resolutions, movie_resolutions = 0, 0, 0
    identity_errors, unchanged_rechecks = 0, 0
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
        retryable = ('series_identity_unresolved', 'movie_identity_unresolved', 'season_numbering_unverified')
        if (prior.get('fingerprint') == fingerprint and prior.get('rulesSignature') == signature and
                key not in state.get('pending', {}) and prior.get('reason') not in retryable):
            prior['checkedAt'] = today
            unchanged_rechecks += 1
            continue
        format_supported = False
        movie_format_supported = False
        series_name = movie_name = ''
        if item and resolver:
            snippet = item.get('snippet', {})
            parsed_series = parse_title(snippet.get('title', ''), snippet.get('description', ''))
            series_name = parsed_series[1].strip(' —–:-') if parsed_series else ''
            format_supported = bool(series_name and coverage(
                snippet.get('title', ''), [{'aliases': [series_name], 'seasonNumbers': list(range(1, 101))}],
                snippet.get('description', '')) is not None)
            parsed_movie = parse_movie_title(snippet.get('title', ''))
            movie_name = parsed_movie['name'] if parsed_movie else ''
            movie_format_supported = bool(parsed_movie and valid_movie_suffix(parsed_movie['suffix'], movie_name) and
                                          not mixed_movie_format(snippet.get('title', ''), content_description(snippet.get('description', ''))))
            registered = any(normalize(series_name) in [normalize(a) for a in show['aliases']] for show in series)
            if format_supported and not registered and snippet.get('defaultAudioLanguage', '').lower().split('-')[0] == 'en':
                identity_key = 'series:' + normalize(series_name)
                checked = state['identityChecks'].get(identity_key, state['identityChecks'].get(normalize(series_name), ''))
                due_identity = (key in state.get('pending', {}) or prior.get('rulesSignature') != signature or
                                checked <= (dt.date.fromisoformat(today) - dt.timedelta(days=REJECTION_RECHECK_DAYS)).isoformat()) and identity_key not in attempted_names
                if due_identity and resolutions < MAX_RESOLUTIONS:
                    resolutions += 1; series_resolutions += 1
                    attempted_names.add(identity_key)
                    try:
                        show = copy.deepcopy(resolver.resolve(series_name))
                        if show:
                            # Reject remapped IDs, alias collisions and sequels
                            # that resolve to an already known different title.
                            validate_series(series + [show], catalog)
                            series.append(show)
                            state.setdefault('series', []).append(show)
                        state['identityChecks'][identity_key] = today
                    except (AssertionError, KeyError, ValueError):
                        state['identityChecks'][identity_key] = today
                    except RuntimeError:
                        identity_errors += 1
                        pending[key] = channel
                elif due_identity:
                    pending[key] = channel
            registered_movie = any(movie_identity_matches(movie_name, movie) for movie in movies) if movie_name else False
            if (movie_format_supported and not registered_movie and hasattr(resolver, 'resolve_movie') and
                    snippet.get('defaultAudioLanguage', '').lower().split('-')[0] == 'en'):
                identity_key = 'movie:' + movie_title_key(movie_name)
                checked = state['identityChecks'].get(identity_key, '')
                due_identity = (key in state.get('pending', {}) or prior.get('rulesSignature') != signature or
                                checked <= (dt.date.fromisoformat(today) - dt.timedelta(days=REJECTION_RECHECK_DAYS)).isoformat()) and identity_key not in attempted_names
                if due_identity and resolutions < MAX_RESOLUTIONS:
                    resolutions += 1; movie_resolutions += 1
                    attempted_names.add(identity_key)
                    try:
                        movie = copy.deepcopy(resolver.resolve_movie(movie_name))
                        if movie:
                            validate_movies(movies + [movie], catalog)
                            movies.append(movie)
                            state.setdefault('movies', []).append(movie)
                        state['identityChecks'][identity_key] = today
                    except (AssertionError, KeyError, ValueError):
                        state['identityChecks'][identity_key] = today
                    except RuntimeError:
                        identity_errors += 1
                        pending[key] = channel
                elif due_identity:
                    pending[key] = channel
        if item and resolver and format_supported and full_series_title(item['snippet'].get('title', '')):
            parsed = parse_title(item['snippet']['title'], item['snippet'].get('description', ''))
            matches = [show for show in series if normalize(parsed[1]) in [normalize(v) for v in show['aliases']]]
            stale_seasons = len(matches) == 1 and matches[0].get('seasonNumbersCheckedAt', '') <= (dt.date.fromisoformat(today) - dt.timedelta(days=REJECTION_RECHECK_DAYS)).isoformat()
            if len(matches) == 1 and ('seasonNumbers' not in matches[0] or stale_seasons):
                if resolutions < MAX_RESOLUTIONS:
                    resolutions += 1; series_resolutions += 1
                    try:
                        matches[0]['seasonNumbers'] = resolver.season_numbers(matches[0])
                        matches[0]['seasonNumbersCheckedAt'] = today
                        stored = next((v for v in state.get('series', []) if v['imdbID'] == matches[0]['imdbID']), None)
                        if stored is None:
                            state.setdefault('series', []).append(copy.deepcopy(matches[0]))
                        else:
                            stored['seasonNumbers'] = matches[0]['seasonNumbers']
                            stored['seasonNumbersCheckedAt'] = today
                    except (RuntimeError, KeyError, ValueError):
                        identity_errors += 1
                        pending[key] = channel
                else:
                    pending[key] = channel
        video = admissible(item, channel, series, today, movies) if item else None
        if video:
            catalog['videos'].append(video)
            state['approved'][key] = {'policyVersion': POLICY_VERSION, 'checkedAt': today,
                                      'audioLanguage': item['snippet']['defaultAudioLanguage']}
            added.append(key)
            state['rejected'].pop(key, None)
        else:
            snippet = item.get('snippet', {}) if item else {}
            description = snippet.get('description', '')
            flag = re.search(r'.{0,70}\b(?:trailer|teaser|prediction|theor(?:y|ies)|episode\s+\d|book spoilers|movie recap|series recap)\b.{0,70}', description, re.I)
            state['rejected'][key] = {'channelID': channel, 'title': snippet.get('title', ''),
                'reason': rejection_reason(item, channel, series, movies), 'checkedAt': today,
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
              'apiCalls': api.calls, 'seriesResolutions': series_resolutions, 'movieResolutions': movie_resolutions,
              'identityResolutions': resolutions, 'identityErrors': identity_errors,
              'channelErrors': channel_errors,
              'pendingIdentities': len(state['pending']), 'unchangedRechecks': unchanged_rechecks,
              'rejectionsByReason': dict(collections.Counter(v['reason'] for v in state['rejected'].values())),
              'rejectionsTracked': len(state['rejected']),
              'archivesComplete': not channel_errors and all(c.get('completedAt') and not c.get('nextPageToken') for c in state['channels'].values())}
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
    movies_path = ROOT / 'movies.json'
    movies = json.loads(movies_path.read_text()) if movies_path.exists() else []
    decisions = json.loads((ROOT / 'review/decisions.json').read_text())
    result, state, report = run(catalog, state, series, decisions, YouTube(key),
                                dt.datetime.now(dt.timezone.utc).date().isoformat(), Cinemeta(), movies)
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
