import ast
import json
import string
import unittest
from datetime import datetime
from pathlib import Path

from gpp3323.i18n import set_language, tr
from gpp3323.instrument import LoadVoltagePresentError
from gui.load_data import suggested_name, validate_name
from gui.load_tab import format_estimated_time


class TranslationTests(unittest.TestCase):
    def tearDown(self):
        set_language('zh-TW')

    def test_english_status_and_validation(self):
        set_language('en')
        self.assertEqual(tr('測試時間'), 'Duration')
        self.assertEqual(format_estimated_time(90), '1.5 minutes')
        self.assertIn('Disconnect the external source', str(LoadVoltagePresentError(1, 3.0)))
        with self.assertRaisesRegex(ValueError, 'Name must not be empty'):
            validate_name('')

    def test_values_and_notes_are_not_translated(self):
        set_language('en')
        self.assertEqual(tr('已儲存：\n{0}', '測試時間/{battery}'), 'Saved:\n測試時間/{battery}')
        notes = 'Toshiba\nCR2032 #1\n第二次 @15mA load'
        stamp = datetime(2026, 10, 1)
        english = suggested_name(notes, stamp)
        set_language('zh-TW')
        self.assertEqual(suggested_name(notes, stamp), english)

    def test_unknown_language_defaults_to_chinese(self):
        set_language('unknown')
        self.assertEqual(tr('測試時間'), '測試時間')

    def test_catalog_coverage_and_placeholders(self):
        root = Path(__file__).resolve().parents[1]
        catalog = json.loads((root / 'gpp3323/en.json').read_text(encoding='utf-8'))
        formatter = string.Formatter()
        for source, translated in catalog.items():
            fields = lambda text: sorted(field for _, field, _, _ in formatter.parse(text) if field is not None)
            self.assertEqual(fields(source), fields(translated), source)
        for path in [*root.joinpath('gui').glob('*.py'), root / 'gpp3323/instrument.py', root / 'gpp3323/load_control.py']:
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == 'tr' and node.args and isinstance(node.args[0], ast.Constant)):
                    self.assertIn(node.args[0].value, catalog, str(path))
