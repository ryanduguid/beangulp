import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


class TestMimeTypes(unittest.TestCase):
    def test_financial_types_with_host_mappings(self):
        script = textwrap.dedent('''\
            import mimetypes as stdlib_mimetypes
            import sys
            import tempfile
            import unittest
            from pathlib import Path

            check = unittest.TestCase()
            check.assertNotIn("beangulp", sys.modules)
            stdlib_mimetypes.add_type("application/vnd.ms-excel", ".csv", strict=True)
            stdlib_mimetypes.add_type("application/vnd.intu.qfx", ".qfx", strict=True)
            stdlib_mimetypes.types_map.pop(".ods", None)
            stdlib_mimetypes.common_types.pop(".ods", None)
            check.assertEqual(stdlib_mimetypes.guess_type("statement.ods"), (None, None))
            check.assertEqual(stdlib_mimetypes.guess_type("statement.csv")[0],
                              "application/vnd.ms-excel")
            excel_type = stdlib_mimetypes.guess_type("statement.xls")

            from beangulp import mimetypes

            for strict in (True, False):
                for filename, encoding in (
                    ("statement.csv", None),
                    ("statement.CSV", None),
                    ("statement.csv.gz", "gzip"),
                    ("statement.csv.bz2", "bzip2"),
                ):
                    check.assertEqual(mimetypes.guess_type(filename, strict=strict),
                                      ("text/csv", encoding))
            check.assertEqual(mimetypes.guess_type("statement.xls"), excel_type)
            check.assertEqual(mimetypes.guess_type("statement.ods"),
                              ("application/vnd.oasis.opendocument.spreadsheet", None))
            check.assertEqual(mimetypes.guess_type("statement.qfx", strict=False)[0],
                              "application/vnd.intu.qfx")
            check.assertEqual(stdlib_mimetypes.common_types[".qfx"], "application/x-ofx")
            check.assertIs(mimetypes.guess_type, stdlib_mimetypes.guess_type)
            for name in stdlib_mimetypes.__all__:
                check.assertTrue(hasattr(mimetypes, name), name)

            from examples.importers import csvbank

            importer = csvbank.Importer("Assets:Bank", "USD")
            header = 'Details,Posting Date,"Description",Amount,Type,Balance,Check or Slip #,'
            with tempfile.TemporaryDirectory() as directory:
                for name, content, expected in (
                    ("statement.csv", header, True),
                    ("wrong-header.csv", "wrong header", False),
                    ("statement.xls", header, False),
                ):
                    filename = Path(directory) / name
                    filename.write_text(content, encoding="utf-8")
                    check.assertIs(importer.identify(str(filename)), expected)
        ''')
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
