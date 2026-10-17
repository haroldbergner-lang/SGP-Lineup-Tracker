from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
import tarfile
import tempfile
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
