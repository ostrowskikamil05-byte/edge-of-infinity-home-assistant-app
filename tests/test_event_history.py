from datetime import datetime
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

from test_detection import module
from test_edge_panel_config import load_panel_module


class EventHistoryTests(unittest.TestCase):
    def test_filters_find_events_outside_recent_window_and_paging_survives_inserts(self):
        m = module()
        with tempfile.TemporaryDirectory() as directory:
            store = m.EventStore(Path(directory) / 'events.sqlite')
            first = datetime(2026, 7, 27, 0, 0, 8).timestamp()
            store.add('front', 'person', 'camera', first, {'score': .91})
            for index in range(210):
                store.add('front', 'motion', 'camera', first + 86400 + index, {})
            store.add('back', 'person', 'local', first, {})
            self.assertNotIn(1, [event['id'] for event in store.recent()])
            selected = store.page(camera_id='front', day='2026-07-27', kind='person')
            self.assertEqual([event['id'] for event in selected['events']], [1])
            first_page = store.page(camera_id='front', limit=100)
            self.assertTrue(first_page['has_more'])
            self.assertEqual(len(first_page['events']), 100)
            store.add('front', 'motion', 'camera', first + 100000, {})
            second_page = store.page(camera_id='front', limit=100, before=first_page['next_before'])
            third_page = store.page(camera_id='front', limit=100, before=second_page['next_before'])
            ids = [row['id'] for page in (first_page, second_page, third_page) for row in page['events']]
            self.assertEqual(ids, list(range(211, 0, -1)))
            self.assertFalse(third_page['has_more'])
            self.assertIsNone(third_page['next_before'])
            self.assertEqual(store.page(camera_id="front' OR 1=1 --")['events'], [])

    @unittest.skipUnless(hasattr(time, 'tzset'), 'Requires system timezone support')
    def test_day_filters_and_markers_include_dst_boundaries_and_repeated_hour(self):
        m = module()
        old = os.environ.get('TZ')
        try:
            os.environ['TZ'] = 'Europe/Warsaw'
            time.tzset()
            with tempfile.TemporaryDirectory() as directory:
                store = m.EventStore(Path(directory) / 'events.sqlite')
                for day, hours in [('2026-03-29', 23), ('2026-10-25', 25)]:
                    start, end = m.day_bounds(day)
                    self.assertEqual(end - start, hours * 3600)
                    for stamp in (start - .1, start, end - .1, end):
                        store.add('front', 'person', 'camera', stamp, {})
                    rows = store.page(camera_id='front', day=day)['events']
                    self.assertEqual(len(rows), 2)
                    self.assertAlmostEqual(rows[0]['day_seconds'], hours * 3600 - .1, places=3)
                    timeline = store.timeline('front', day)
                    self.assertEqual(timeline['count'], 2)
                    self.assertEqual(timeline['day_total_seconds'], hours * 3600)
                    self.assertEqual(timeline['midpoint_label'], '12:30' if hours == 23 else '11:30')
                    self.assertEqual(timeline['markers'][-1]['slot'], hours * 12 - 1)
                for fold in (0, 1):
                    stamp = datetime(2026, 10, 25, 2, 30, fold=fold).timestamp()
                    store.add('front', 'vehicle', 'camera', stamp, {})
                markers = [row for row in store.timeline('front', '2026-10-25')['markers'] if 'vehicle' in row['kinds']]
                self.assertEqual(len(markers), 2)
                self.assertEqual(markers[1]['day_seconds'] - markers[0]['day_seconds'], 3600)
                self.assertEqual([marker['time_label'] for marker in markers], ['02:30:00', '02:30:00'])
        finally:
            if old is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = old
            time.tzset()

    def test_summary_counts_every_event_but_keeps_response_bounded(self):
        m = module()
        with tempfile.TemporaryDirectory() as directory:
            store = m.EventStore(Path(directory) / 'events.sqlite')
            start, end = m.day_bounds('2026-07-27')
            for index in range(350):
                store.add('front', 'person' if index % 2 else 'vehicle', 'camera', start + 8 + index / 10, {})
            store.add('front', 'motion', 'camera', start + 601, {})
            store.add('back', 'person', 'camera', start + 8, {})
            store.add('front', 'person', 'camera', end, {})
            summary = store.timeline('front', '2026-07-27')
            self.assertEqual(summary['count'], 351)
            self.assertEqual(len(summary['markers']), 2)
            self.assertEqual(summary['markers'][0]['count'], 350)
            self.assertEqual(set(summary['markers'][0]['kinds']), {'person', 'vehicle'})
            self.assertEqual(summary['markers'][0]['day_seconds'], 8)
            self.assertEqual(store.page(day='2026-07-26')['events'], [])

    def test_api_filters_and_invalid_queries_are_controlled(self):
        panel, m = load_panel_module(), module()
        with tempfile.TemporaryDirectory() as directory:
            store = m.EventStore(Path(directory) / 'events.sqlite')
            stamp = datetime(2026, 7, 27, 0, 0, 8).timestamp()
            store.add('front', 'person', 'camera', stamp, {})
            panel.DETECTION_SERVICE = SimpleNamespace(store=store)
            handler = object.__new__(panel.EdgeHandler)
            responses = []
            handler.send_json = lambda payload, status=200: responses.append((payload, int(status)))
            for path in ['/api/events?camera=front&day=2026-07-27&kind=person&limit=1', '/api/events/timeline?camera=front&day=2026-07-27']:
                handler.path = path
                handler.do_GET()
                self.assertEqual(responses[-1][1], 200)
                self.assertIn('day', str(responses[-1][0]))
            for query in ['day=2026-02-30', 'day=2026-7-27', 'day=9999-12-31', 'kind=unknown', 'before=-1', 'before=9223372036854775808', 'before=1.2', 'limit=oops']:
                handler.path = '/api/events?' + query
                handler.do_GET()
                self.assertEqual(responses[-1], ({'error': 'invalid_event_query'}, 400), query)
            handler.path = '/api/events/timeline?day=2026-07-27'
            handler.do_GET()
            self.assertEqual(responses[-1][1], 400)
