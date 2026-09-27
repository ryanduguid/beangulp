import fnmatch
import functools
import os
import tempfile
import textwrap
from datetime import date
from pathlib import Path

from beancount.parser import parser

from beangulp import importer
from beangulp import mimetypes


class Importer(importer.Importer):
    def __init__(self, name, account, mimetype):
        self._name = name
        self._account = account
        self._mimetype = mimetype

    @property
    def name(self):
        return self._name

    def identify(self, filepath):
        mimetype, encoding = mimetypes.guess_type(filepath, False)
        return mimetype == self._mimetype

    def date(self, filepath):
        return date(1970, 1, 1)

    def account(self, filepath):
        return self._account

    def filename(self, filepath):
        return None

    def extract(self, filepath, existing):
        return []

    def deduplicate(self, entries, existing):
        # Opt-out from the default deduplication mechanism.
        return entries


class IdentityImporter(importer.Importer):
    def __init__(self, name, account, match):
        self._name = name
        self._account = account
        self._match = match

    @property
    def name(self):
        return self._name

    def identify(self, filepath):
        return fnmatch.fnmatch(filepath, self._match)

    def date(self, filepath):
        return date(1970, 1, 1)

    def account(self, filepath):
        return self._account

    def filename(self, filepath):
        return None

    def extract(self, filepath, existing):
        entries, errors, options = parser.parse_file(filepath)
        return entries


def normalize_paths(output):
    """Use forward slashes when comparing paths in doctest output."""
    return output.replace(os.sep, "/")


def docfile(function):
    """Pass the test's docstring in a closed temporary file to the test."""
    @functools.wraps(function)
    def wrapped(self):
        with tempfile.TemporaryDirectory(prefix="beangulp-test-") as directory:
            filename = Path(directory) / "temp.beancount"
            filename.write_text(textwrap.dedent(function.__doc__), encoding="utf-8")
            return function(self, str(filename))

    wrapped.__doc__ = None
    return wrapped
