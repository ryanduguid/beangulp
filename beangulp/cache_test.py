__copyright__ = "Copyright (C) 2016  Martin Blais"
__license__ = "GNU GPLv2"

import os
import shutil
import tempfile
import time
import unittest

from unittest import mock

from beangulp import cache
from beangulp import utils


CALLS = []


class TestFileMemo(unittest.TestCase):
    def test_cache(self):
        wrap = cache._FileMemo(__file__)

        # Check attributes.
        self.assertEqual(__file__, wrap.name)

        # Check that caching works.
        converter = mock.MagicMock(return_value="abc")
        self.assertEqual("abc", wrap.convert(converter))
        self.assertEqual("abc", wrap.convert(converter))
        self.assertEqual("abc", wrap.convert(converter))
        self.assertEqual(1, converter.call_count)

    def test_cache_head_and_contents(self):
        wrap = cache._FileMemo(__file__)

        contents = wrap.convert(cache.contents)
        self.assertIsInstance(contents, str)
        self.assertGreater(len(contents), 128)

        contents2 = wrap.contents()
        self.assertEqual(contents, contents2)

        head = wrap.convert(cache.head(128))
        self.assertIsInstance(head, str)
        self.assertEqual(128, len(head))

        mimetype = wrap.convert(cache.mimetype)
        self.assertIn(
            mimetype,
            {"text/plain", "text/x-python", "text/x-script.python", "text/c++"},
        )

    def test_cache_head_obeys_explict_utf8_encoding_avoids_chardet_exception(self):
        data = b"asciiHeader1,\xf0\x9f\x8d\x8fHeader1,asciiHeader2"
        with mock.patch("builtins.open", mock.mock_open(read_data=data)):
            string = cache._FileMemo("filepath").head(encoding="utf-8")
            self.assertEqual(string, data.decode("utf8"))

    def test_cache_head_encoding(self):
        data = b"asciiHeader1,\xf0\x9f\x8d\x8fHeader1,asciiHeader2"
        # The 15th bytes is in the middle of the unicode character.
        num_bytes = 15
        with mock.patch("builtins.open", mock.mock_open(read_data=data)):
            string = cache._FileMemo("filepath").head(num_bytes, encoding="utf-8")
            self.assertEqual(string, "asciiHeader1,")

    def test_cache_head_empty(self):
        for encoding in (None, "utf-8"):
            with self.subTest(encoding=encoding):
                with mock.patch("builtins.open", mock.mock_open(read_data=b"")):
                    self.assertEqual(cache._FileMemo("filepath").head(encoding=encoding), "")

    def test_cache_head_incomplete_character(self):
        with mock.patch("builtins.open", mock.mock_open(read_data=b"\xf0\x9f")):
            self.assertEqual(cache._FileMemo("filepath").head(encoding="utf-8"), "")

    def test_cache_head_reuses_matching_conversion(self):
        wrapper = cache._FileMemo("filepath")
        with mock.patch("builtins.open", mock.mock_open(read_data=b"abcdef")) as opened:
            self.assertEqual(wrapper.head(3, encoding="utf-8"), "abc")
            self.assertEqual(wrapper.head(4, encoding="utf-8"), "abcd")
            self.assertEqual(wrapper.head(3, encoding="utf-8"), "abc")
            self.assertEqual(opened.call_count, 2)

    def test_cache_head_zero_bytes(self):
        with mock.patch("builtins.open", mock.mock_open(read_data=b"content")):
            self.assertEqual(cache._FileMemo("filepath").head(0, encoding="utf-8"), "")

    def test_cache_head_invalid_interior_character(self):
        with mock.patch("builtins.open", mock.mock_open(read_data=b"a\xffb")):
            with self.assertRaises(UnicodeDecodeError):
                cache._FileMemo("filepath").head(encoding="utf-8")

    def test_cache_head_retains_byte_count_type_check(self):
        wrapper = cache._FileMemo("filepath")
        with mock.patch("builtins.open", mock.mock_open(read_data=b"content")):
            self.assertEqual(wrapper.head(1, encoding="utf-8"), "c")
            with self.assertRaises(TypeError):
                wrapper.head(1.0, encoding="utf-8")


class CacheTestCase(unittest.TestCase):
    def setUp(self):
        calls = mock.patch(__name__ + ".CALLS", [])
        calls.start()
        self.addCleanup(calls.stop)
        self.tempdir = tempfile.mkdtemp()
        patcher = mock.patch.object(cache, "CACHEDIR", os.path.join(self.tempdir, "cache"))
        patcher.start()
        self.addCleanup(patcher.stop)
        os.mkdir(cache.CACHEDIR)
        self.filename = os.path.join(self.tempdir, "test.txt")
        with open(self.filename, "w"):
            pass

    def tearDown(self):
        shutil.rmtree(self.tempdir)

    def test_no_cache(self):
        def func(filename):
            CALLS.append(filename)
            return len(CALLS)

        r = func(self.filename)
        self.assertEqual(r, 1)

        r = func(self.filename)
        self.assertEqual(r, 2)

    def test_cache(self):
        @cache.cache
        def func(filename):
            CALLS.append(filename)
            return len(CALLS)

        r = func(self.filename)
        self.assertEqual(r, 1)

        r = func(self.filename)
        self.assertEqual(r, 1)

    def test_different_converters_different_cache(self):
        @cache.cache
        def first(filename):
            return "first conversion"

        @cache.cache
        def second(filename):
            return "second conversion"

        self.assertEqual(first(self.filename), "first conversion")
        self.assertEqual(second(self.filename), "second conversion")

    def test_configured_converters_do_not_share_results(self):
        def make_converter(prefix):
            @cache.cache
            def converter(filename):
                return prefix
            return converter

        self.assertEqual(make_converter("first")(self.filename), "first")
        self.assertEqual(make_converter("second")(self.filename), "second")

    def test_keyword_order_is_preserved(self):
        @cache.cache
        def converter(filename, **kwargs):
            return tuple(kwargs)

        self.assertEqual(converter(self.filename, first=1, second=2), ("first", "second"))
        self.assertEqual(converter(self.filename, second=2, first=1), ("second", "first"))

    def test_forced_read_recovers_missing_and_corrupt_entries(self):
        @cache.cache
        def converter(filename):
            CALLS.append(filename)
            return "converted content"

        self.assertEqual(converter(self.filename, cache=True), "converted content")
        for name in os.listdir(cache.CACHEDIR):
            with open(os.path.join(cache.CACHEDIR, name), "wb") as entry:
                entry.write(b"\x80")
        self.assertEqual(converter(self.filename, cache=True), "converted content")
        self.assertEqual(len(CALLS), 2)

    def test_custom_key_reuses_identical_files(self):
        @cache.cache(key=utils.sha1sum)
        def converter(filename):
            CALLS.append(filename)
            return len(CALLS)

        other = os.path.join(self.tempdir, "other.txt")
        shutil.copyfile(self.filename, other)
        os.utime(self.filename, ns=(0, 0))
        self.assertEqual(converter(self.filename), 1)
        self.assertEqual(converter(other), 1)

    def test_false_valued_key_callable_runs_uncached(self):
        class Key:
            def __bool__(self):
                return False

            def __call__(self, filename):
                return filename

        @cache.cache(key=Key())
        def converter(filename):
            with open(filename, encoding="utf-8") as source:
                return source.read()

        self.assertEqual(converter(self.filename), "")
        stamp = os.stat(self.filename).st_mtime_ns + 2_000_000_000
        with open(self.filename, "w", encoding="utf-8") as source:
            source.write("updated")
        os.utime(self.filename, ns=(stamp, stamp))
        self.assertEqual(converter(self.filename), "updated")

    def test_missing_cache_directory_is_created(self):
        os.rmdir(cache.CACHEDIR)

        @cache.cache
        def converter(filename):
            CALLS.append(filename)
            return len(CALLS)

        self.assertEqual(converter(self.filename), 1)
        self.assertEqual(converter(self.filename), 1)
        self.assertTrue(os.path.isdir(cache.CACHEDIR))

    def test_failed_refresh_preserves_previous_entry(self):
        @cache.cache
        def converter(filename):
            CALLS.append(filename)
            with open(filename, encoding="utf-8") as source:
                return source.read()

        self.assertEqual(converter(self.filename), "")
        with mock.patch("builtins.open", side_effect=OSError("conversion failed")):
            with self.assertRaisesRegex(OSError, "conversion failed"):
                converter(self.filename, cache=False)
        self.assertEqual(converter(self.filename, cache=True), "")
        self.assertEqual(len(CALLS), 2)
        os.remove(self.filename)
        with self.assertRaises(FileNotFoundError):
            converter(self.filename, cache=True)

    def test_relative_paths_do_not_share_entries(self):
        @cache.cache
        def converter(filename):
            with open(filename, encoding="utf-8") as source:
                return source.read()

        other = os.path.join(self.tempdir, "other")
        os.mkdir(other)
        other_file = os.path.join(other, "test.txt")
        with open(other_file, "w", encoding="utf-8") as source:
            source.write("other content")
        stamp = os.stat(self.filename).st_mtime_ns
        os.utime(other_file, ns=(stamp, stamp))
        original = os.getcwd()
        try:
            os.chdir(self.tempdir)
            self.assertEqual(converter("test.txt"), "")
            os.chdir(other)
            self.assertEqual(converter("test.txt"), "other content")
        finally:
            os.chdir(original)

    def test_cache_expire_mtime(self):
        @cache.cache
        def func(filename):
            CALLS.append(filename)
            return len(CALLS)

        r = func(self.filename)
        self.assertEqual(r, 1)

        r = func(self.filename)
        self.assertEqual(r, 1)

        t = time.time() + 2.0
        os.utime(self.filename, (t, t))

        r = func(self.filename)
        self.assertEqual(r, 2)

        r = func(self.filename)
        self.assertEqual(r, 2)

    def test_cache_expire_args(self):
        @cache.cache
        def func(filename, arg):
            CALLS.append(filename)
            return len(CALLS)

        r = func(self.filename, 1)
        self.assertEqual(r, 1)

        r = func(self.filename, 1)
        self.assertEqual(r, 1)

        r = func(self.filename, 2)
        self.assertEqual(r, 2)

    def test_cache_expire_override(self):
        @cache.cache
        def func(filename):
            CALLS.append(filename)
            return len(CALLS)

        r = func(self.filename)
        self.assertEqual(r, 1)

        r = func(self.filename)
        self.assertEqual(r, 1)

        r = func(self.filename, cache=False)
        self.assertEqual(r, 2)

        r = func(self.filename, cache=True)
        self.assertEqual(r, 2)

        t = time.time() + 2.0
        os.utime(self.filename, (t, t))

        r = func(self.filename, cache=True)
        self.assertEqual(r, 2)

    def test_cache_reset_mtime(self):
        @cache.cache
        def func(filename):
            CALLS.append(filename)
            return len(CALLS)

        r = func(self.filename)
        self.assertEqual(r, 1)

        t = os.stat(self.filename).st_mtime_ns
        with open(self.filename, "w") as f:
            f.write("baz")
        os.utime(self.filename, ns=(t, t))

        r = func(self.filename)
        self.assertEqual(r, 1)

    def test_cache_key_sha1(self):
        @cache.cache(key=utils.sha1sum)
        def func(filename):
            CALLS.append(filename)
            return len(CALLS)

        with open(self.filename, "w") as f:
            f.write("test")

        r = func(self.filename)
        self.assertEqual(r, 1)

        r = func(self.filename)
        self.assertEqual(r, 1)

        t = time.time() + 2.0
        os.utime(self.filename, (t, t))

        r = func(self.filename)
        self.assertEqual(r, 1)

        t = os.stat(self.filename).st_mtime_ns
        with open(self.filename, "w") as f:
            f.write("baz")
        os.utime(self.filename, ns=(t, t))

        r = func(self.filename)
        self.assertEqual(r, 2)
