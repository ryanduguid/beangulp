import builtins
import datetime
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from beancount.parser import parser
from beangulp import testing


def _gbk_open(filepath, mode="r", *args, **kwargs):
    kwargs.setdefault("encoding", "gbk")
    return builtins.open(filepath, mode, *args, **kwargs)


class TestExpectedFileEncoding(unittest.TestCase):
    def setUp(self):
        entries, errors, _ = parser.parse_string(
            """
            2026-01-01 * "合成商户"
              Assets:Bank -1 USD
              Expenses:Groceries 1 USD
        """,
            dedent=True,
        )
        self.assertFalse(errors)
        self.values = ("Assets:Bank", datetime.date(2026, 1, 1), "synthetic.csv", entries)

    def test_write_expected_file_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            filepath = Path(directory) / "expected.beancount"
            with mock.patch.object(testing, "open", side_effect=_gbk_open, create=True):
                testing.write_expected_file(filepath, *self.values)
                self.assertIn("合成商户", filepath.read_text(encoding="utf-8"))
                with self.assertRaises(FileExistsError):
                    testing.write_expected_file(filepath, *self.values)
                updated = [self.values[-1][0]._replace(narration="更新商户")]
                testing.write_expected_file(
                    filepath, *self.values[:-1], updated, force=True
                )
                self.assertIn("更新商户", filepath.read_text(encoding="utf-8"))
            entries, errors, _ = parser.parse_file(str(filepath))
            self.assertFalse(errors)
            self.assertEqual(entries[0].narration, "更新商户")

    def test_compare_expected_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            filepath = Path(directory) / "expected.beancount"
            with filepath.open("w", encoding="utf-8") as stream:
                testing.write_expected(stream, *self.values)
            with mock.patch.object(testing, "open", side_effect=_gbk_open, create=True):
                self.assertEqual(testing.compare_expected(filepath, *self.values), [])
