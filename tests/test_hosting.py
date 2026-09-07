import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f'scripts/{name}.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

build, discover = module('build'), module('discover')

class HostingTests(unittest.TestCase):
    def test_revision_guards(self):
        build.check_revision({'revision': 2}, {'revision': 1})
        build.check_revision({'revision': 2}, {'revision': 2})
        for current in [{'revision': 1}, {'revision': 2, 'changed': True}]:
            with self.assertRaises(ValueError):
                build.check_revision(current, {'revision': 2})

    def test_feed_candidates_require_channel_and_recap_title(self):
        raw = b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><yt:channelId>UC123</yt:channelId>
        <entry><yt:videoId>abcdefghijk</yt:videoId><title>Show Season 1 Recap</title></entry>
        <entry><yt:videoId>lmnopqrstuv</yt:videoId><title>New season trailer</title></entry></feed>'''
        channel = {'id':'UC123', 'name':'Approved'}
        result = discover.parse_feed(raw, channel, set())
        self.assertEqual([v['videoID'] for v in result], ['abcdefghijk'])
        self.assertEqual(result[0]['status'], 'needs_review')
        self.assertEqual(discover.parse_feed(raw.replace(b'>UC123<', b'>123<'), channel, set()), result)
        self.assertNotIn('imdbID', result[0])
        self.assertEqual(discover.parse_feed(raw, channel, {'abcdefghijk'}), [])
        with self.assertRaises(ValueError):
            discover.parse_feed(raw, {'id':'other'}, set())

    def test_feed_size_is_bounded(self):
        with self.assertRaises(ValueError):
            discover.parse_feed(b' ' * 1_048_577, {}, set())

    def test_catalog_validation(self):
        result = build.validator.validate(ROOT / 'catalog.json')
        self.assertTrue(all(v['language'] == 'en' for v in result['videos']))


class FreeHostingTests(unittest.TestCase):
    def test_runtime_and_paid_bindings_are_rejected(self):
        import json
        check = module('check-free-hosting').check
        config = json.loads((ROOT / 'wrangler.jsonc').read_text())
        check(config)
        for key, value in [('main','worker.js'), ('kv_namespaces',[]), ('r2_buckets',[]), ('routes',[]), ('triggers',{})]:
            with self.assertRaises(ValueError):
                check({**config, key: value})
        with self.assertRaises(ValueError):
            check({**config, 'assets': {**config['assets'], 'run_worker_first': True}})

    def test_health_budget_rotates_without_growing_with_catalog(self):
        videos = [{'videoID': str(i).zfill(11), 'enabled': True} for i in range(140)]
        days = [discover.health_batch(videos, day) for day in range(3)]
        self.assertTrue(all(len(day) == 60 for day in days))
        self.assertEqual(len({v['videoID'] for day in days for v in day}), 140)
        self.assertEqual(discover.health_batch([], 1), [])

    def test_review_page_does_not_execute_feed_markup(self):
        html = module('review').render({}, {'candidates':[{'title':'</script><script>alert(1)</script>'}]})
        self.assertNotIn('</script><script>alert(1)</script>', html)
        self.assertIn('\\u003c/script>', html)

class ReviewImportTests(unittest.TestCase):
    def test_stale_or_destructive_drafts_do_not_replace_catalog(self):
        import json, tempfile
        review = module('review')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            review.ROOT = root
            (root / 'scripts').symlink_to(ROOT / 'scripts', target_is_directory=True)
            current = json.loads((ROOT / 'catalog.json').read_text())
            (root / 'catalog.json').write_text(json.dumps(current))
            original = (root / 'catalog.json').read_bytes()
            for mutate in [lambda d: d.update(revision=d['revision']),
                           lambda d: d.update(revision=d['revision']+1, videos=[]),
                           lambda d: d.update(revision=d['revision']+1, updateURL='https://other.example/catalog.json')]:
                draft = json.loads(json.dumps(current)); mutate(draft)
                path = root / 'draft.json'; path.write_text(json.dumps(draft))
                with self.assertRaises(ValueError): review.import_draft(path)
                self.assertEqual((root / 'catalog.json').read_bytes(), original)

class CatalogCompatibilityTests(unittest.TestCase):
    def test_nonstring_metadata_and_compact_dates_cannot_publish(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            for collection, key, value in [
                ('channels', 'name', ['A channel']),
                ('videos', 'title', ['A recap']),
                ('videos', 'title', 42),
                ('videos', 'reviewedAt', '20260906'),
            ]:
                data = json.loads((ROOT / 'catalog.json').read_text())
                data[collection][0][key] = value
                path.write_text(json.dumps(data))
                with self.subTest(key=key, value=value), self.assertRaises(AssertionError):
                    build.validator.validate(path)

    def test_fractional_numbers_cannot_publish_an_app_incompatible_catalog(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            for key in ['firstSeason', 'lastSeason', 'durationSeconds', 'tmdbID']:
                data = json.loads((ROOT / 'catalog.json').read_text())
                data['videos'][0][key] = 1.5
                path.write_text(json.dumps(data))
                with self.assertRaises(AssertionError): build.validator.validate(path)

if __name__ == '__main__':
    unittest.main()
