import io
import textwrap
import unittest

from datetime import timedelta
from os import path
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from click.testing import CliRunner

from beancount.parser import parser
from beangulp import Ingest
from beangulp import extract
from beangulp import similar
from beangulp import tests


class TestExtractCommand(unittest.TestCase):
    def test_unicode_output(self):
        # Simulate a non-UTF-8 default for files opened by Click on any platform.
        def open_cp1252(filename, mode, encoding=None, errors=None):
            return open(filename, mode, encoding=encoding or "cp1252", errors=errors)

        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.beans"
            output = Path(directory) / "output.bean"
            narration = "Payment from \u7ba1\u8ca1\u4eba"
            source.write_text(
                f'2026-01-01 * "{narration}"\n'
                "  Assets:Tests  1 AUD\n"
                "  Equity:Tests  -1 AUD\n",
                encoding="utf-8",
            )
            importer = tests.utils.IdentityImporter(
                "test.Identity", "Assets:Tests", "*source.beans"
            )
            ingest = Ingest([importer])
            runner = CliRunner()

            for options in (["-o", str(output)], [], ["-o", "-"]):
                with self.subTest(options=options):
                    with mock.patch("click._compat.open", open_cp1252, create=True):
                        result = runner.invoke(
                            ingest.cli,
                            ["extract", str(source), *options],
                            catch_exceptions=False,
                        )
                    self.assertEqual(result.exit_code, 0)
                    content = (
                        output.read_text(encoding="utf-8")
                        if options == ["-o", str(output)]
                        else result.stdout
                    )
                    entries, errors, _ = parser.parse_string(content)
                    self.assertFalse(errors)
                    self.assertEqual(len(entries), 1)
                    self.assertEqual(entries[0].narration, narration)


class TestExtract(unittest.TestCase):
    def setUp(self):
        self.importer = tests.utils.Importer(None, "Assets:Tests", None)

    def test_extract_from_file_no_entries(self):
        entries = extract.extract_from_file(self.importer, path.abspath("test.csv"), [])
        self.assertEqual(entries, [])

    def test_extract_from_file(self):
        entries, errors, options = parser.parse_string(
            textwrap.dedent("""
            1970-01-03 * "Test"
              Assets:Tests  1.00 USD

            1970-01-01 * "Test"
              Assets:Tests  1.00 USD

            1970-01-02 * "Test"
              Assets:Tests  1.00 USD
            """)
        )

        importer = mock.MagicMock(wraps=self.importer)
        importer.extract.return_value = entries
        entries = extract.extract_from_file(importer, path.abspath("test.csv"), [])
        dates = [entry.date for entry in entries]
        self.assertSequenceEqual(dates, sorted(dates))

    def test_extract_from_file_ensure_sanity(self):
        entries, errors, options = parser.parse_string("""
            1970-01-01 * "Test"
              Assets:Tests  1.00 USD
            """)

        # Break something.
        entries[-1] = entries[-1]._replace(narration=42)
        importer = mock.MagicMock(wraps=self.importer)
        importer.extract.return_value = entries
        with self.assertRaises(AssertionError):
            extract.extract_from_file(importer, path.abspath("test.csv"), [])


class TestDuplicates(unittest.TestCase):
    def test_mark_duplicate_entries(self):
        entries, error, options = parser.parse_string(
            textwrap.dedent("""
          1970-01-01 * "Test"
            Assets:Tests  10.00 USD

          1970-01-02 * "Test"
            Assets:Tests  20.00 USD
        """)
        )
        compare = similar.heuristic_comparator()
        extract.mark_duplicate_entries(entries, entries[:1], timedelta(days=2), compare)
        self.assertTrue(entries[0].meta[extract.DUPLICATE])
        self.assertNotIn(extract.DUPLICATE, entries[1].meta)


class TestPrint(unittest.TestCase):
    def test_print_extracted_entries(self):
        entries, error, options = parser.parse_string(
            textwrap.dedent("""
            1970-01-01 * "Test"
              Assets:Tests  10.00 USD""")
        )

        extracted = [
            ("/path/to/test.csv", entries, None, None),
            ("/path/to/empty.pdf", [], None, None),
        ]

        output = io.StringIO()
        extract.print_extracted_entries(extracted, output)

        self.assertEqual(
            output.getvalue(),
            textwrap.dedent("""\
            ;; -*- mode: beancount -*-

            **** /path/to/test.csv

            1970-01-01 * "Test"
              Assets:Tests  10.00 USD


            **** /path/to/empty.pdf


            """),
        )

    def test_print_extracted_entries_duplictes(self):
        entries, error, options = parser.parse_string(
            textwrap.dedent("""
            1970-01-01 * "Test"
              Assets:Tests  10.00 USD

            1970-01-01 * "Test"
              Assets:Tests  10.00 USD """)
        )

        # Mark the second entry as duplicate
        entries[1].meta[extract.DUPLICATE] = True

        extracted = [
            ("/path/to/test.csv", entries, None, None),
        ]

        output = io.StringIO()
        extract.print_extracted_entries(extracted, output)

        self.assertEqual(
            output.getvalue(),
            textwrap.dedent("""\
            ;; -*- mode: beancount -*-

            **** /path/to/test.csv

            1970-01-01 * "Test"
              Assets:Tests  10.00 USD

            ; 1970-01-01 * "Test"
            ;   Assets:Tests  10.00 USD


            """),
        )
