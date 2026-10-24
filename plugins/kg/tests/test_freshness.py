"""Opaque, atomic profile recall notifications; no external services."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kg.freshness import bump_recall_generation, read_recall_generation


class RecallGenerationTests(unittest.TestCase):
    def test_read_is_side_effect_free_and_changes_are_profile_scoped(self):
        with TemporaryDirectory() as root:
            home, other = Path(root)/'profile', Path(root)/'other'
            self.assertEqual(read_recall_generation(home), '')
            self.assertFalse(home.exists())
            first = bump_recall_generation(home)
            self.assertEqual(read_recall_generation(home), first)
            self.assertNotEqual(bump_recall_generation(home), first)
            self.assertEqual(read_recall_generation(other), '')
            data = json.loads((home/'kg-sync'/'recall-generation.json').read_text())
            self.assertEqual(set(data), {'version', 'generation'})
            self.assertEqual(len(data['generation']), 32)
            self.assertEqual(list((home/'kg-sync').glob('*.tmp')), [])

    def test_failed_atomic_replace_keeps_previous_complete_notification(self):
        with TemporaryDirectory() as root:
            first = bump_recall_generation(root)
            with patch('kg.freshness.os.replace', side_effect=OSError('fixture read-only destination')):
                with self.assertRaises(OSError):
                    bump_recall_generation(root)
            self.assertEqual(read_recall_generation(root), first)
            self.assertEqual(list((Path(root)/'kg-sync').glob('*.tmp')), [])

    def test_corrupt_notification_never_authorizes_cache_reuse(self):
        with TemporaryDirectory() as root:
            bump_recall_generation(root)
            path = Path(root)/'kg-sync'/'recall-generation.json'
            for text in ('{}', '[]', 'null', 'invalid', '{"version":1,"generation":"z"}'):
                path.write_text(text, encoding='utf-8')
                first, second = read_recall_generation(root), read_recall_generation(root)
                self.assertTrue(first.startswith('unreadable-'))
                self.assertNotEqual(first, second)


if __name__ == '__main__':
    unittest.main()
