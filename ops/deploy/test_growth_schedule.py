import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('growth_schedule', Path(__file__).with_name('growth_schedule.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class GrowthScheduleTests(unittest.TestCase):
    def test_idempotent_active_release_dispatch_and_preserve_unrelated_files(self):
        with tempfile.TemporaryDirectory() as root:
            other = Path(root)/'other'; other.write_text('untouched')
            module.install(root); module.install(root)
            text = (Path(root)/'remoteimpact-growth').read_text()
            self.assertIn('exec-active', text)
            self.assertIn('in get_commands()', text)
            self.assertEqual(other.read_text(), 'untouched')

    def test_unknown_existing_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root)/'remoteimpact-growth'; file.write_text('user schedule')
            with self.assertRaises(RuntimeError): module.install(root)
            self.assertEqual(file.read_text(), 'user schedule')
