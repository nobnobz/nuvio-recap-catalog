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

if __name__ == '__main__':
    unittest.main()
