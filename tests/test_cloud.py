from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
import tarfile
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.cloud import eligible_games
from src.cloud_storage import encrypt, restore


class Cloud(unittest.TestCase):
    def test_window_starts_before_baseline_and_excludes_finished_games(self):
        now = datetime.now(timezone.utc)
        def game(hours, state='Scheduled'):
            return {'gameDate': (now + timedelta(hours=hours)).isoformat(),
                    'status': {'detailedState': state}}
        games = [game(5.5), game(7), game(-1), game(4, 'Postponed')]
        self.assertEqual(eligible_games(games, now), [games[0]])

    def test_encrypted_checkpoint_roundtrip_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'original'
            root.mkdir()
            (root/'quotes.csv').write_text('player,odds\nAlex,300\n')
            (root/'health.json').write_text('{}')
            (root/'transient.tmp').write_text('not a checkpoint')
            with patch.dict(os.environ, {'SGP_ARCHIVE_PASSWORD': 'a-test-password-with-at-least-32-characters'}):
                payload = encrypt(root)
                self.assertNotIn(b'Alex', payload)
                destination = Path(directory)/'restored'
                restore(payload, destination)
                self.assertEqual((destination/'season-2026/quotes.csv').read_text(), 'player,odds\nAlex,300\n')
                self.assertFalse((destination/'season-2026/transient.tmp').exists())
                corrupted = bytearray(payload)
                corrupted[-1] ^= 1
                with self.assertRaises(ValueError):
                    restore(bytes(corrupted), Path(directory)/'bad')

    def test_checkpoint_upload_does_not_block_next_collector_tick(self):
        from src import cloud
        entered, release = threading.Event(), threading.Event()
        now = datetime(2026, 10, 18, 16, tzinfo=timezone.utc)
        game = {'gamePk': 100, 'gameDate': (now+timedelta(hours=4)).isoformat(),
                'status': {'detailedState': 'Scheduled'}}
        ticks = []
        worker = ThreadPoolExecutor(max_workers=1)
        collector = SimpleNamespace(schedule=[game], jobs={}, refresh_jobs={}, pool=worker)
        collector.refresh_schedule = lambda: None
        collector.poll_interval = lambda: 5
        collector.error = lambda *args: self.fail('Unexpected upload failure')
        def tick():
            ticks.append(1)
            if len(ticks) == 2:
                self.assertTrue(entered.wait(timeout=2))
                release.set()
                game['gameDate'] = (now-timedelta(seconds=1)).isoformat()
        collector.tick = tick
        def publish(root):
            entered.set()
            if not release.wait(timeout=3):
                raise AssertionError('Upload blocked the polling loop')
        with tempfile.TemporaryDirectory() as d, \
             patch.object(cloud, 'Collector', return_value=collector), \
             patch.object(cloud, 'download', return_value=Path(d)), \
             patch.object(cloud, 'publish', side_effect=publish), \
             patch.object(cloud, 'export_all'), patch.object(cloud.time, 'sleep'), \
             patch.object(cloud, 'datetime') as clock, \
             patch.dict(os.environ, {'FD_API_KEY': 'test'}), patch('sys.argv', ['cloud']):
            clock.now.return_value = now
            self.assertEqual(cloud.main(), 0)
        self.assertEqual(len(ticks), 2)

    def test_wrong_password_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'original'
            root.mkdir()
            (root/'health.json').write_text('{}')
            with patch.dict(os.environ, {'SGP_ARCHIVE_PASSWORD': 'first-test-password-at-least-32-characters'}):
                payload = encrypt(root)
            with patch.dict(os.environ, {'SGP_ARCHIVE_PASSWORD': 'second-test-password-at-least-32-characters'}):
                with self.assertRaises(ValueError):
                    restore(payload, Path(directory)/'restored')
