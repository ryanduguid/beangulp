import copyreg
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from datetime import datetime, timedelta

from beangulp import _cache
from beangulp import simple_cache


CALLS = []


class SimpleCacheTest(unittest.TestCase):
    """Test the simple_cache module."""

    def setUp(self):
        """Set up a temporary directory for cache files."""
        calls = mock.patch(__name__ + ".CALLS", [])
        calls.start()
        self.addCleanup(calls.stop)
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.tempdir = tempdir.name
        self.original_cachedir = simple_cache.CACHEDIR
        simple_cache.CACHEDIR = os.path.join(self.tempdir, "cache")

        # Create a test file
        self.test_file = os.path.join(self.tempdir, "test.txt")
        with open(self.test_file, "w") as f:
            f.write("test content")

    def tearDown(self):
        """Clean up the temporary directory."""
        simple_cache.CACHEDIR = self.original_cachedir

    def test_convert_creates_cache_dir(self):
        """Test that the cache directory is created if it doesn't exist."""

        def converter(filename):
            return "converted content"

        simple_cache.convert(self.test_file, converter)
        self.assertTrue(os.path.exists(simple_cache.CACHEDIR))

    def test_convert_caches_result(self):
        """Test that the result is cached."""
        def converter(filename):
            CALLS.append(filename)
            return f"converted content {len(CALLS)}"

        # First call should execute the converter
        result1 = simple_cache.convert(self.test_file, converter)
        self.assertEqual(result1, "converted content 1")
        self.assertEqual(len(CALLS), 1)

        # Second call should use the cached result
        result2 = simple_cache.convert(self.test_file, converter)
        self.assertEqual(result2, "converted content 1")
        self.assertEqual(len(CALLS), 1)  # Converter not called again

    def test_different_converters_different_cache(self):
        """Test that different converter functions use different cache entries."""

        def converter1(filename):
            return "result from converter1"

        def converter2(filename):
            return "result from converter2"

        result1 = simple_cache.convert(self.test_file, converter1)
        result2 = simple_cache.convert(self.test_file, converter2)

        self.assertEqual(result1, "result from converter1")
        self.assertEqual(result2, "result from converter2")

    def test_different_files_different_cache(self):
        """Test that different files use different cache entries."""
        test_file2 = os.path.join(self.tempdir, "test2.txt")
        with open(test_file2, "w") as f:
            f.write("different content")

        def converter(filename):
            with open(filename, "r") as f:
                return f"converted: {f.read()}"

        result1 = simple_cache.convert(self.test_file, converter)
        result2 = simple_cache.convert(test_file2, converter)

        self.assertEqual(result1, "converted: test content")
        self.assertEqual(result2, "converted: different content")

    def test_changed_contents_with_preserved_timestamp(self):
        def converter(filename):
            with open(filename, encoding="utf-8") as source:
                return source.read()

        self.assertEqual(simple_cache.convert(self.test_file, converter), "test content")
        original = os.stat(self.test_file)
        with open(self.test_file, "w", encoding="utf-8") as source:
            source.write("next content")
        os.utime(self.test_file, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertEqual(simple_cache.convert(self.test_file, converter), "next content")

    def test_removed_input_is_not_served_from_cache(self):
        def converter(filename):
            with open(filename, encoding="utf-8") as source:
                return source.read()

        simple_cache.convert(self.test_file, converter)
        os.remove(self.test_file)
        with self.assertRaises(FileNotFoundError):
            simple_cache.convert(self.test_file, converter)

    def test_different_closure_configuration(self):
        def make_converter(prefix):
            def converter(filename):
                return prefix + os.path.basename(filename)
            return converter

        first = make_converter("first:")
        second = make_converter("second:")
        self.assertEqual(simple_cache.convert(self.test_file, first), "first:test.txt")
        self.assertEqual(simple_cache.convert(self.test_file, second), "second:test.txt")

    def test_different_default_configuration(self):
        def make_converter(prefix):
            def converter(filename, prefix=prefix):
                return prefix + os.path.basename(filename)
            return converter

        first = make_converter("first:")
        second = make_converter("second:")
        self.assertEqual(simple_cache.convert(self.test_file, first), "first:test.txt")
        self.assertEqual(simple_cache.convert(self.test_file, second), "second:test.txt")

    def test_changed_default_configuration(self):
        def converter(filename, prefix="first:"):
            return prefix + os.path.basename(filename)

        self.assertEqual(simple_cache.convert(self.test_file, converter), "first:test.txt")
        converter.__defaults__ = ("second:",)
        self.assertEqual(simple_cache.convert(self.test_file, converter), "second:test.txt")

    def test_changed_closure_configuration(self):
        prefix = "first:"

        def converter(filename):
            return prefix + os.path.basename(filename)

        self.assertEqual(simple_cache.convert(self.test_file, converter), "first:test.txt")
        prefix = "second:"
        self.assertEqual(simple_cache.convert(self.test_file, converter), "second:test.txt")

    def test_equal_default_values_reuse_the_cache(self):
        def converter(filename, prefix="str"):
            CALLS.append(filename)
            return prefix

        self.assertEqual(simple_cache.convert(self.test_file, converter), "str")
        converter.__defaults__ = ("".join(("s", "tr")),)
        self.assertEqual(simple_cache.convert(self.test_file, converter), "str")
        self.assertEqual(len(CALLS), 1)

    def test_unsupported_state_runs_without_serialisation_hooks(self):
        class State:
            def __reduce__(self):
                raise AssertionError("state must not be serialised")

            def __repr__(self):
                raise AssertionError("state must not be represented")

            def __hash__(self):
                raise AssertionError("state must not be hashed")

        state = State()

        def converter(filename):
            CALLS.append(filename)
            return "converted content" if state is not None else "missing"

        self.assertEqual(simple_cache.convert(self.test_file, converter), "converted content")
        self.assertEqual(simple_cache.convert(self.test_file, converter), "converted content")
        self.assertEqual(len(CALLS), 2)

    def test_unserialisable_result_is_returned(self):
        def converter(filename):
            return lambda: "converted content"

        for _ in range(2):
            self.assertEqual(simple_cache.convert(self.test_file, converter)(), "converted content")
        self.assertEqual(os.listdir(simple_cache.CACHEDIR), [simple_cache.GC_SENTINEL_FILENAME])

    def test_unsupported_state_does_not_compare_custom_types(self):
        class StateType(type):
            def __eq__(cls, other):
                raise AssertionError("state type must not be compared")

            def __hash__(cls):
                raise AssertionError("state type must not be hashed")

        class State(metaclass=StateType):
            pass

        state = State()

        def converter(filename):
            return "converted content" if state is not None else "missing"

        self.assertEqual(simple_cache.convert(self.test_file, converter), "converted content")

    def test_custom_function_mappings_run_uncached(self):
        class State(dict):
            def __bool__(self):
                raise AssertionError("custom mapping must not be inspected")

            def items(self):
                raise AssertionError("custom mapping must not be inspected")

        for attribute in ("__dict__", "__kwdefaults__"):
            with self.subTest(attribute=attribute):
                def converter(filename, *, prefix="converted content"):
                    CALLS.append(filename)
                    return prefix

                setattr(converter, attribute, State(prefix="converted content"))
                count = len(CALLS)
                self.assertEqual(simple_cache.convert(self.test_file, converter), "converted content")
                self.assertEqual(simple_cache.convert(self.test_file, converter), "converted content")
                self.assertEqual(len(CALLS), count + 2)

    def test_key_does_not_use_registered_pickle_hooks(self):
        value = 1 + 2j

        def converter(filename):
            return "converted content" if value == 1 + 2j else "different"

        def reduce_value(value):
            raise AssertionError("key must not invoke a registered pickle hook")

        with mock.patch.dict(copyreg.dispatch_table, {complex: reduce_value}):
            self.assertEqual(simple_cache.convert(self.test_file, converter), "converted content")

    def test_cache_storage_failure_does_not_discard_result(self):
        def converter(filename):
            CALLS.append(filename)
            return "converted content"

        for operation in ("os.makedirs", "os.replace"):
            with self.subTest(operation=operation):
                count = len(CALLS)
                with mock.patch(operation, side_effect=PermissionError("cache unavailable")):
                    self.assertEqual(simple_cache.convert(self.test_file, converter), "converted content")
                self.assertEqual(len(CALLS), count + 1)
        self.assertEqual(os.listdir(simple_cache.CACHEDIR), [simple_cache.GC_SENTINEL_FILENAME])
        self.assertEqual(simple_cache.convert(self.test_file, converter), "converted content")
        self.assertEqual(simple_cache.convert(self.test_file, converter), "converted content")
        self.assertEqual(len(CALLS), 3)

    def test_input_changed_during_conversion_is_not_cached(self):
        def converter(filename):
            with open(filename, encoding="utf-8") as source:
                result = source.read()
            with open(filename, "w", encoding="utf-8") as source:
                source.write("next content")
            return result

        self.assertEqual(simple_cache.convert(self.test_file, converter), "test content")
        self.assertEqual(os.listdir(simple_cache.CACHEDIR), [simple_cache.GC_SENTINEL_FILENAME])
        self.assertEqual(simple_cache.convert(self.test_file, converter), "next content")

    def test_relative_paths_in_different_directories(self):
        other = os.path.join(self.tempdir, "other")
        os.mkdir(other)
        with open(os.path.join(other, "test.txt"), "w", encoding="utf-8") as source:
            source.write("other content")

        def converter(filename):
            with open(filename, encoding="utf-8") as source:
                return source.read()

        original = os.getcwd()
        try:
            os.chdir(self.tempdir)
            self.assertEqual(simple_cache.convert("test.txt", converter), "test content")
            os.chdir(other)
            self.assertEqual(simple_cache.convert("test.txt", converter), "other content")
        finally:
            os.chdir(original)

    def test_callable_instance(self):
        class Converter:
            def __call__(self, filename):
                return "converted content"

        with mock.patch.object(_cache, "file_key", side_effect=AssertionError("unneeded file hash")):
            self.assertEqual(simple_cache.convert(self.test_file, Converter()), "converted content")

    def test_nan_configuration_runs_uncached(self):
        def make_converter(values):
            def converter(filename):
                return len(set(values))
            return converter

        for make_nan in (lambda: float("nan"), lambda: complex(float("nan"), 0)):
            first, second = make_nan(), make_nan()
            with self.subTest(kind=type(first).__name__):
                self.assertEqual(simple_cache.convert(self.test_file, make_converter((first, first))), 1)
                self.assertEqual(simple_cache.convert(self.test_file, make_converter((first, second))), 2)

    def test_concurrent_read_does_not_see_partial_write(self):
        filename = os.path.join(self.tempdir, "publication.pickle")
        entry_key = _cache.key("publication test")
        replace = os.replace
        for workers in (1, 2):
            with self.subTest(workers=workers):
                _cache.write(filename, entry_key, "old")
                ready = threading.Barrier(workers + 1)
                release = threading.Event()
                staging = []

                def delayed_replace(source, destination, staging=staging, ready=ready, release=release):
                    staging.append((source, destination))
                    ready.wait(timeout=10)
                    if not release.wait(10):
                        raise TimeoutError("cache writer was not released")
                    replace(source, destination)

                with mock.patch("os.replace", side_effect=delayed_replace):
                    with ThreadPoolExecutor(max_workers=workers) as executor:
                        futures = [executor.submit(_cache.write, filename, entry_key, str(number))
                                   for number in range(workers)]
                        try:
                            ready.wait(timeout=10)
                            self.assertEqual(_cache.read(filename, entry_key), "old")
                            self.assertEqual(len({source for source, _ in staging}), workers)
                            for source, destination in staging:
                                self.assertNotEqual(source, destination)
                                self.assertEqual(destination, filename)
                            self.assertEqual({_cache.read(source, entry_key) for source, _ in staging},
                                             {str(number) for number in range(workers)})
                        finally:
                            release.set()
                        for future in futures:
                            future.result(timeout=10)
                self.assertIn(_cache.read(filename, entry_key), {str(number) for number in range(workers)})
                self.assertTrue(all(not os.path.exists(source) for source, _ in staging))

    def test_corrupt_entry_is_recomputed(self):
        def converter(filename):
            CALLS.append(filename)
            return "converted content"

        simple_cache.convert(self.test_file, converter)
        entries = [name for name in os.listdir(simple_cache.CACHEDIR) if name.endswith(".pickle")]
        self.assertEqual(len(entries), 1)
        filename = os.path.join(simple_cache.CACHEDIR, entries[0])
        with open(filename, "rb") as entry:
            valid = entry.read()
        changed = valid.replace(b"converted content", b"converted contenx")
        self.assertNotEqual(changed, valid)
        for data in (b"", b"\x80", b"not a cache entry", changed):
            with self.subTest(data=data):
                with open(filename, "wb") as entry:
                    entry.write(data)
                count = len(CALLS)
                self.assertEqual(simple_cache.convert(self.test_file, converter), "converted content")
                self.assertEqual(len(CALLS), count + 1)

    def test_converter_error_is_not_cached(self):
        fail = True

        def converter(filename):
            if fail:
                raise ValueError("conversion failed")
            return "converted content"

        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "conversion failed"):
                simple_cache.convert(self.test_file, converter)
            self.assertEqual(os.listdir(simple_cache.CACHEDIR), [simple_cache.GC_SENTINEL_FILENAME])
        fail = False
        self.assertEqual(simple_cache.convert(self.test_file, converter), "converted content")

    def test_cache_reused_across_processes(self):
        script = os.path.join(self.tempdir, "convert.py")
        calls = os.path.join(self.tempdir, "calls.txt")
        with open(script, "w", encoding="utf-8") as source:
            source.write(textwrap.dedent("""\
                import sys
                from beangulp import simple_cache
                simple_cache.CACHEDIR = sys.argv[1]
                def converter(filename):
                    def suffix():
                        return "txt" in {"txt", "pdf", "csv", "ofx"}
                    assert suffix()
                    with open(sys.argv[2], "a", encoding="utf-8") as calls:
                        calls.write("called\\n")
                    with open(filename, encoding="utf-8") as source:
                        return source.read()
                print(simple_cache.convert(sys.argv[3], converter))
                """))
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
        env["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for seed in ("1", "2"):
            env["PYTHONHASHSEED"] = seed
            result = subprocess.run(
                [sys.executable, script, simple_cache.CACHEDIR, calls, self.test_file],
                env=env, capture_output=True, text=True, check=True,
            )
            self.assertEqual(result.stdout.strip(), "test content")
        with open(calls, encoding="utf-8") as source:
            self.assertEqual(source.readlines(), ["called\n"])

    def test_falsey_results_are_cached(self):
        for value in (None, False, 0, ""):
            with self.subTest(value=value):
                def converter(filename, value=value):
                    CALLS.append(filename)
                    return value

                count = len(CALLS)
                self.assertEqual(simple_cache.convert(self.test_file, converter), value)
                self.assertEqual(simple_cache.convert(self.test_file, converter), value)
                self.assertEqual(len(CALLS), count + 1)

    def test_sentinel_file_creation(self):
        """Test that the sentinel file is created."""

        def converter(filename):
            return "result"

        # Make sure the sentinel file doesn't exist
        sentinel_path = os.path.join(
            simple_cache.CACHEDIR, simple_cache.GC_SENTINEL_FILENAME
        )
        if os.path.exists(sentinel_path):
            os.remove(sentinel_path)

        # Call convert, which should create the sentinel file
        simple_cache.convert(self.test_file, converter)

        # Check that the sentinel file was created
        self.assertTrue(os.path.exists(sentinel_path))

    def test_cleanup_old_files(self):
        """Test that old cache files are cleaned up."""

        # Create some fake old cache files
        os.makedirs(simple_cache.CACHEDIR, exist_ok=True)
        old_file = os.path.join(simple_cache.CACHEDIR, "old_file.pickle")
        with open(old_file, "w") as f:
            f.write("old content")

        # Set the modification time to be older than the threshold
        old_time = datetime.now() - timedelta(days=simple_cache.GC_THRESHOLD_DAYS + 1)
        os.utime(old_file, (old_time.timestamp(), old_time.timestamp()))

        # Create a sentinel file with an old timestamp
        sentinel_path = os.path.join(
            simple_cache.CACHEDIR, simple_cache.GC_SENTINEL_FILENAME
        )
        with open(sentinel_path, "w") as f:
            f.write(str(old_time.timestamp()))
        os.utime(sentinel_path, (old_time.timestamp(), old_time.timestamp()))

        # Call convert, which should trigger cleanup
        def converter(filename):
            return "result"

        with mock.patch("os.remove") as mock_remove:
            simple_cache.convert(self.test_file, converter)

            # Check that os.remove was called for the old file
            mock_remove.assert_called()

        # Check that the sentinel file was updated (newer timestamp)
        self.assertGreater(os.path.getmtime(sentinel_path), old_time.timestamp())
