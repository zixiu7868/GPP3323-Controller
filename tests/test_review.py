import csv
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from gui.curve_panel import quantity_scale, format_power
from gui.load_data import suggested_name, export_dataset, read_samples, rename_dataset, validate_name
from gui.monitor_tab import Sample


class ReviewTests(unittest.TestCase):
    def test_names_from_notes(self):
        stamp = datetime(2026, 10, 1, 14, 30, 25)
        for text, count in [('1st run', 1), ('2nd run', 2), ('3rd run', 3),
                            ('10th run', 10), ('第一次', 1), ('第二次', 2),
                            ('第十次', 10), ('第二十一回', 1), ('第二十一次', 21), ('第100次', 100)]:
            notes = f'Toshiba\nCR2032 ,#1\n{text} @15mA load'
            name = suggested_name(notes, stamp)
            self.assertIn(f'15mA-{count}-20261001_143025', name)
        self.assertEqual(suggested_name('Toshiba\nCR2032 ,#1\n1st run @15mA load', stamp),
                         'Toshiba_CR2032_no1_15mA-1-20261001_143025')
        self.assertTrue(suggested_name('', stamp).startswith('LoadTest-1-'))

    def test_power_units(self):
        self.assertEqual(format_power(.045), '45 mW')
        self.assertEqual(quantity_scale([.5, 1], 'W'), (1., 'W'))
        self.assertEqual(quantity_scale([-.2], 'W'), (1000., 'mW'))

    def test_export_preserves_readback_and_calculates_from_measurements(self):
        sample = Sample(datetime(2026, 10, 1), .5, 1, 2.5811, .0147, .04)
        with TemporaryDirectory() as temp:
            folder = export_dataset(Path(temp), 'power-test', [sample], '')
            with (folder / 'measurements.csv').open(encoding='utf-8-sig') as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(float(row['power_W']), .04)
            self.assertAlmostEqual(float(row['power_calculated_W']), .03794217)
            loaded = read_samples(folder / 'measurements.csv')[0]
            self.assertEqual(loaded.power, .04)
            self.assertAlmostEqual(loaded.calculated_power, .03794217)

    def test_legacy_csv_recalculates_power_without_changing_file(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'old.csv'
            content = ('timestamp,elapsed_s,channel,voltage_V,current_A,power_W\n'
                       '2026-10-01,0.5,1,2.5811,0.0147,0.04\n')
            path.write_text(content, encoding='utf-8')
            sample = read_samples(path)[0]
            self.assertEqual(sample.power, .04)
            self.assertAlmostEqual(sample.calculated_power, .03794217)
            self.assertEqual(path.read_text(encoding='utf-8'), content)

    def test_export_read_collision_and_rename(self):
        sample = Sample(datetime(2026, 10, 1), .5, 1, 3., .015, .045)
        with TemporaryDirectory() as temp:
            root = Path(temp)
            first = export_dataset(root, 'test-1', [sample], '第一次')
            second = export_dataset(root, 'test-1', [sample], '第二次')
            self.assertNotEqual(first, second)
            self.assertEqual(read_samples(first / 'measurements.csv'), [sample])
            self.assertEqual((first / 'notes.txt').read_text(encoding='utf-8-sig'), '第一次')
            with self.assertRaises(ValueError):
                rename_dataset(first, second.name)
            renamed = rename_dataset(first, 'new-1')
            self.assertFalse(first.exists())
            self.assertTrue((renamed / 'measurements.csv').exists())

    def test_unsafe_names(self):
        for name in ['', '..', '../escape', 'CON', 'nul.txt', 'bad:name', 'trail.', ' space']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_name(name)

    def test_malformed_csv(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'bad.csv'
            path.write_text('timestamp,elapsed_s,channel,voltage_V,current_A,power_W\n2026-10-01,nan,1,3,.015,.045\n')
            with self.assertRaisesRegex(ValueError, '第 2 行'):
                read_samples(path)
            path.write_text('x,y\n1,2\n')
            with self.assertRaisesRegex(ValueError, '欄位'):
                read_samples(path)
