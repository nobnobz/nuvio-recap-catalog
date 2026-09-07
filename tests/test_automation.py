import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('automate', ROOT / 'scripts/automate.py')
a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)
CHANNEL = 'UCNCTxLZ3EKKry-oWgLlsYsw'
SHOW = {'imdbID': 'tt10986410', 'tmdbID': 97546, 'aliases': ['Ted Lasso']}


def item(key='abcdefghijk', title='Ted Lasso Season 3 Recap'):
    return {'id': key, 'snippet': {'title': title, 'description': '', 'channelId': CHANNEL,
                                  'defaultAudioLanguage': 'en-US', 'liveBroadcastContent': 'none'},
            'contentDetails': {'duration': 'PT8M1S'},
            'status': {'privacyStatus': 'public', 'uploadStatus': 'processed', 'embeddable': True}}


class FakeAPI:
    def __init__(self, items=None, pages=None):
        self.items = items if items is not None else [item()]
        self.pages = pages or {'': {'items': [{'contentDetails': {'videoId': v['id']}, 'snippet': v['snippet']} for v in self.items]}}
        self.calls = 0
        self.tokens = []

    def get(self, resource, **params):
        self.calls += 1
        if resource == 'channels':
            return {'items': [{'id': params['id'], 'contentDetails': {'relatedPlaylists': {'uploads': 'uploads'}}}]}
        if resource == 'playlistItems':
            token = params.get('pageToken', '')
            self.tokens.append(token)
            return self.pages[token]
        if resource == 'videos':
            return {'items': [v for v in self.items if v['id'] in params['id'].split(',')]}
        raise AssertionError(resource)


def catalog():
    return {'schemaVersion': 1, 'revision': 4, 'channels': [{'id': CHANNEL, 'name': 'Recaps', 'rank': 10, 'official': False}], 'videos': []}


class RuleTests(unittest.TestCase):
    def test_explicit_coverage_only(self):
        for title, expected in [('Ted Lasso RECAP: Season 3', (3, 3)),
                                ('TED LASSO Season 1-3 Recap | Must Watch Before Season 4 | Series Explained', (1, 3)),
                                ('Ted Lasso Season 1 & 2 Recap', (1, 2)),
                                ('Ted Lasso — Season 1 Recap | Apple TV', (1, 1))]:
            with self.subTest(title=title):
                self.assertEqual(a.coverage(title, [SHOW])[1:], expected)
        for title in ['Ted Lasso RECAP before Season 4', 'Ted Lasso Season 1 & 3 Recap',
                      'Ted Lasso Season 2-3 Recap', 'Ted Lasso Season 3 Episode 1 Recap',
                      'Ted Lasso Season 1 Recap | Must Watch Before Season 4',
                      'Ted Lasso Season 3 Recap and predictions', 'Ted Lasso Season 1',
                      'Ted Lasso and Silo Season 1 Recap', 'Ted Lasso Season 0 Recap',
                      'Ted Lasso Season 1 Recap | Season 2 Trailer']:
            with self.subTest(title=title):
                self.assertIsNone(a.coverage(title, [SHOW]))

    def test_ambiguous_identity_and_description_fail_closed(self):
        show = {**SHOW, 'requiredContext': ['Apple TV']}
        self.assertIsNone(a.coverage('Ted Lasso Season 1 Recap', [show]))
        self.assertIsNotNone(a.coverage('Ted Lasso Season 1 Recap', [show], 'An Apple TV series'))
        self.assertIsNone(a.coverage('Ted Lasso Season 1 Recap', [SHOW, SHOW]))
        self.assertIsNone(a.coverage('Ted Lasso Season 1 Recap', [SHOW], 'This covers seasons 1-3'))
        self.assertIsNone(a.coverage('Ted Lasso Season 1 Recap', [SHOW], 'Includes the season 2 trailer'))

    def test_language_identity_and_playability_are_required(self):
        self.assertIsNotNone(a.admissible(item(), CHANNEL, [SHOW], '2026-09-07'))
        for group, key, value in [('snippet', 'defaultAudioLanguage', ''), ('snippet', 'defaultAudioLanguage', 'de'),
                                  ('snippet', 'channelId', 'other'), ('snippet', 'liveBroadcastContent', 'upcoming'),
                                  ('status', 'embeddable', False), ('status', 'privacyStatus', 'private'),
                                  ('status', 'uploadStatus', 'uploaded'), ('contentDetails', 'duration', 'PT30S'),
                                  ('contentDetails', 'regionRestriction', {'blocked': ['AT']}),
                                  ('contentDetails', 'contentRating', {'ytRating': 'ytAgeRestricted'})]:
            video = item(); video[group][key] = value
            with self.subTest(key=key):
                self.assertIsNone(a.admissible(video, CHANNEL, [SHOW], '2026-09-07'))

    def test_registry_has_unique_exact_identities(self):
        data = json.loads((ROOT / 'catalog.json').read_text())
        series = json.loads((ROOT / 'series.json').read_text())
        a.validate_series(series, data)
        for invalid in [series + [series[0]], [{**series[0], 'tmdbID': 1}]]:
            with self.assertRaises(AssertionError): a.validate_series(invalid, data)


class AutomationTests(unittest.TestCase):
    def test_addition_is_idempotent_and_does_not_mutate_inputs(self):
        original, state = catalog(), {}
        updated, state2, report = a.run(original, state, [SHOW], {}, FakeAPI(), '2026-09-07')
        self.assertEqual(original, catalog()); self.assertEqual(state, {})
        self.assertEqual(updated['revision'], 5)
        self.assertEqual(report['added'], ['abcdefghijk'])
        again, _, report2 = a.run(updated, state2, [SHOW], {}, FakeAPI(), '2026-09-08')
        self.assertEqual(again, updated); self.assertEqual(report2['added'], [])

    def test_decisions_and_manually_disabled_videos_are_not_readded(self):
        result, _, _ = a.run(catalog(), {}, [SHOW], {'abcdefghijk': {}}, FakeAPI(), '2026-09-07')
        self.assertEqual(result, catalog())
        seed = catalog(); seed['videos'] = [a.admissible(item(), CHANNEL, [SHOW], '2026-09-07')]
        seed['videos'][0]['enabled'] = False
        result, _, _ = a.run(seed, {}, [SHOW], {}, FakeAPI(), '2026-09-08')
        self.assertEqual(result, seed)

    def test_archive_checkpoint_does_not_skip_old_pages_or_new_uploads(self):
        pages = {'': {'items': [], 'nextPageToken': '1'}}
        for n in range(1, 7): pages[str(n)] = {'items': [], **({'nextPageToken': str(n + 1)} if n < 6 else {})}
        first = FakeAPI(pages=pages)
        _, state, _ = a.run(catalog(), {}, [SHOW], {}, first, '2026-09-07')
        self.assertEqual(first.tokens, ['', '1', '2', '3', '4'])
        second = FakeAPI(pages=pages)
        _, state, report = a.run(catalog(), state, [SHOW], {}, second, '2026-09-08')
        self.assertEqual(second.tokens, ['', '5', '6']); self.assertTrue(report['archivesComplete'])
        third = FakeAPI(pages=pages)
        a.run(catalog(), state, [SHOW], {}, third, '2026-09-09')
        self.assertEqual(third.tokens, [''])
        fourth = FakeAPI(pages=pages)
        a.run(catalog(), state, [SHOW], {}, fourth, '2026-10-09')
        self.assertEqual(fourth.tokens, ['', '1', '2', '3', '4'])

    def test_withdrawal_requires_three_distinct_days_and_can_recover(self):
        data, state, _ = a.run(catalog(), {}, [SHOW], {}, FakeAPI(), '2026-09-07')
        for day in ['2026-09-08', '2026-09-08', '2026-09-09']:
            data, state, _ = a.run(data, state, [SHOW], {}, FakeAPI(items=[]), day)
            self.assertTrue(data['videos'][0]['enabled'])
        data, state, report = a.run(data, state, [SHOW], {}, FakeAPI(items=[]), '2026-09-10')
        self.assertFalse(data['videos'][0]['enabled']); self.assertEqual(report['withdrawn'], ['abcdefghijk'])
        data, state, report = a.run(data, state, [SHOW], {}, FakeAPI(), '2026-09-11')
        self.assertTrue(data['videos'][0]['enabled']); self.assertEqual(report['restored'], ['abcdefghijk'])

    def test_partial_failure_preserves_catalog_and_checkpoint(self):
        class BrokenAPI(FakeAPI):
            def get(self, resource, **params):
                if resource == 'videos': raise RuntimeError('Quota unavailable')
                return super().get(resource, **params)
        data, state = catalog(), {}
        with self.assertRaises(RuntimeError): a.run(data, state, [SHOW], {}, BrokenAPI(), '2026-09-07')
        self.assertEqual(data, catalog()); self.assertEqual(state, {})

    def test_unclassifiable_candidates_do_not_increase_revision(self):
        data, _, report = a.run(catalog(), {}, [SHOW], {}, FakeAPI([item(title='Ted Lasso RECAP before Season 4')]), '2026-09-07')
        self.assertEqual(data, catalog()); self.assertEqual(report['skipped'], ['abcdefghijk'])


class IdentityResolutionTests(unittest.TestCase):
    def test_new_series_is_resolved_once_and_persisted(self):
        class Resolver:
            calls = 0
            def resolve(self, name):
                self.calls += 1
                return {'imdbID': 'tt999999', 'tmdbID': 1234, 'aliases': [name]}
        resolver = Resolver()
        api = FakeAPI([item(title='New Show Season 1 Recap')])
        updated, state, report = a.run(catalog(), {}, [SHOW], {}, api, '2026-09-07', resolver)
        self.assertEqual(updated['videos'][0]['imdbID'], 'tt999999')
        self.assertEqual(resolver.calls, 1); self.assertEqual(len(state['series']), 1)
        a.run(updated, state, [SHOW], {}, FakeAPI([item(title='New Show Season 1 Recap')]), '2026-09-08', resolver)
        self.assertEqual(resolver.calls, 1)

    def test_ambiguous_series_are_skipped_and_network_failures_queued(self):
        class Ambiguous:
            def resolve(self, name): return None
        class Offline:
            def resolve(self, name): raise RuntimeError('Unavailable')
        for resolver, count in [(Ambiguous(), 0), (Offline(), 1)]:
            data, state, report = a.run(catalog(), {}, [SHOW], {}, FakeAPI([item(title='New Show Season 1 Recap')]), '2026-09-07', resolver)
            self.assertEqual(data, catalog())
            self.assertEqual(len(state['pending']), count)

    def test_cinemeta_requires_unique_exact_match_and_confirmed_detail_ids(self):
        class Resolver(a.Cinemeta):
            results = [{'id': 'tt999999', 'name': 'New Show', 'type': 'series'}]
            meta = {'imdb_id': 'tt999999', 'name': 'New Show', 'type': 'series', 'moviedb_id': 123}
            def get(self, path): return {'metas': self.results} if path.startswith('catalog/') else {'meta': self.meta}
        resolver = Resolver()
        self.assertEqual(resolver.resolve('New Show')['tmdbID'], 123)
        resolver.results = Resolver.results + [{'id': 'tt888888', 'name': 'New Show', 'type': 'series'}]
        self.assertIsNone(resolver.resolve('New Show'))
        resolver.results = Resolver.results
        resolver.meta = {**Resolver.meta, 'imdb_id': 'tt111111'}
        self.assertIsNone(resolver.resolve('New Show'))

    def test_expired_archive_cursor_recovers_without_losing_head(self):
        class Expired(FakeAPI):
            def get(self, resource, **params):
                if params.get('pageToken') == 'expired': raise a.InvalidPageToken()
                return super().get(resource, **params)
        api = Expired(pages={'': {'items': [], 'nextPageToken': 'fresh'}})
        _, state, _ = a.run(catalog(), {'channels': {CHANNEL: {'nextPageToken': 'expired'}}}, [SHOW], {}, api, '2026-09-07')
        self.assertEqual(state['channels'][CHANNEL]['nextPageToken'], 'fresh')

    def test_remapped_identity_cannot_publish(self):
        class Wrong:
            def resolve(self, name): return {**SHOW, 'aliases': [name]}
        data, _, _ = a.run(catalog(), {}, [SHOW], {}, FakeAPI([item(title='New Show Season 1 Recap')]), '2026-09-07', Wrong())
        self.assertEqual(data, catalog())

if __name__ == '__main__': unittest.main()
