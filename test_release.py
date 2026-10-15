"""Public-distribution checks: fictional data and no dependency on a developer DB."""
import json
from pathlib import Path
import tempfile
import unittest
from htmai.app import open_app,performance_report


class ReleaseTests(unittest.TestCase):
    def test_fixture_is_explicitly_fictional(self):
        fixture=json.loads(Path(__file__).with_name('events.json').read_text(encoding='utf-8'))
        self.assertTrue(fixture['synthetic'])
        self.assertTrue(fixture['source'].startswith('synthetic:'))
        self.assertTrue(all(row['author']=='Demo Maintainer' for row in fixture['events']))

    def test_clean_initialization_needs_no_bundled_database(self):
        with tempfile.TemporaryDirectory() as directory:
            store=open_app(Path(directory))
            try:self.assertEqual(store.overview('haxy')['issues'],8)
            finally:store.close()

    def test_performance_view_prefers_local_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'benchmarks';target.mkdir()
            (target/'git-benchmarks.json').write_text('{"local":true}',encoding='utf-8')
            self.assertTrue(performance_report(directory)['local'])


if __name__=='__main__':unittest.main()
