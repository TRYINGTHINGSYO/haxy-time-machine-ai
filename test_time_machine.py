import unittest
from time_machine import replay, clean, load_git, summary
from pathlib import Path
import json


class ReplayTests(unittest.TestCase):
    def test_rewind_update_and_removal(self):
        rows = [dict(commit=str(i), timestamp=i, event=dict(id='a', kind='issue', data=d))
                for i, d in enumerate([{'title': 'one', 'status': 'open'},
                                       {'title': 'two', 'status': 'closed'}, None])]
        self.assertEqual(replay(rows, 0), [])
        self.assertEqual(replay(rows, 1)[0]['data']['status'], 'open')
        self.assertEqual(replay(rows, 2)[0]['data']['title'], 'two')
        self.assertEqual(replay(rows, 3), [])

    def test_terminal_controls(self):
        self.assertNotIn('\x1b', clean('\x1b[31mhello\x07'))
        self.assertNotIn('\x07', clean('hello\x07'))

    def test_demo_snapshot_matches_expected_board(self):
        events = json.loads(Path(__file__).with_name('events.json').read_text(encoding='utf-8'))['events']
        state = summary(events, len(events))
        self.assertEqual((state['open_issues'], state['closed_issues']), (4, 4))
        self.assertEqual(sum(r['kind'] == 'discuss' for r in state['records']), 1)


if __name__ == '__main__':
    unittest.main()
