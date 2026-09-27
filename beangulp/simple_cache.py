"""A very simple cache mechanism for expensive file conversions.

Usage:

- You need to explicitly call this from your importers in order to benefit from
  caching. Nothing in beangulp calls this automatically for you.

- You provide a filename and a function to call:

      def extract(file_path):
          ...
          text = simple_cache.convert(file_path, slow_pdf2txt_converter)
          ...

  Results are reused when the filename, file bytes, converter code and immutable
  defaults and closure values match. File contents are hashed on every lookup.
  Other callable forms, NaN-valued configuration and unsupported state run
  without caching.

  Converters must be deterministic and depend on configuration values, not
  object identity, shared references or frozenset iteration order.
  Globals, imported helpers, external tools, file metadata and other external
  dependencies are not tracked. Represent their
  versions in an immutable default or closure value when they affect results.
  Keep the input and converter configuration stable during each call.

  Cache storage is best-effort. Damaged entries are recomputed, and concurrent
  misses may run the converter more than once. The cache directory must be
  trusted because cached results use pickle.

- The cache will be automatically cleaned up periodically. We leave a sentinel
  file for cleaning and if enough time has gone by we scan the timestamps of the
  cache contents and delete old files.

Note: This supersedes the beangulp.cache module, which should get deleted at
some point.
"""

import os
import sys
from os import path
from datetime import datetime, timedelta

from typing import Any, Callable

from beangulp import _cache


# Default location of cache directories.
CACHEDIR = (
    path.expandvars("%LOCALAPPDATA%\\Beangulp\\simple_cache")
    if sys.platform == "win32"
    else path.expanduser("~/.cache/beangulp/simple_cache")
)

GC_SENTINEL_FILENAME = "last_cleanup"  # Sentinel file to track last cleanup
GC_SENTINEL_PATH = os.path.join(CACHEDIR, GC_SENTINEL_FILENAME)

# Garbage collection settings
GC_THRESHOLD_DAYS = 7  # Clean files older than 7 days


ConverterFunc = Callable[[str], Any]


def _cleanup_old_cache_files():
    """Clean up cache files older than the threshold.

    This function scans all files in the cache directory and removes
    any that are older than GC_THRESHOLD_DAYS.
    """
    now = datetime.now()
    threshold = now - timedelta(days=GC_THRESHOLD_DAYS)
    threshold_timestamp = threshold.timestamp()

    # Scan all files in the cache directory
    for filename in os.listdir(CACHEDIR):
        if filename == GC_SENTINEL_FILENAME:
            continue

        filepath = os.path.join(CACHEDIR, filename)
        if os.path.isfile(filepath):
            # Check file modification time.
            mtime = os.path.getmtime(filepath)
            if mtime < threshold_timestamp:
                try:
                    os.remove(filepath)
                except OSError:
                    # Ignore errors when removing files.
                    pass

    # Update the sentinel file timestamp.
    gc_sentinel_path = os.path.join(CACHEDIR, GC_SENTINEL_FILENAME)
    with open(gc_sentinel_path, "w") as f:
        f.write(str(now.timestamp()))


def convert(file_path: str, converter: ConverterFunc) -> Any:
    """Convert a file using the provided converter function.

    This will cache the result in a file in the cache directory.
    The cache is cleaned up periodically.
    """
    converter_key = _cache.function_key(converter)
    if converter_key is None:
        return converter(file_path)
    input_key = _cache.file_key(file_path)
    if input_key is None:
        return converter(file_path)
    entry_key = _cache.key("simple", input_key, converter_key)
    cache_filename = os.path.join(CACHEDIR, "v1-" + entry_key.hex() + ".pickle")

    try:
        os.makedirs(CACHEDIR, exist_ok=True)
        gc_sentinel_path = os.path.join(CACHEDIR, GC_SENTINEL_FILENAME)
        if not os.path.exists(gc_sentinel_path):
            with open(gc_sentinel_path, "w") as cache_file:
                cache_file.write(str(datetime.now().timestamp()))
        else:
            sentinel_mtime = os.path.getmtime(gc_sentinel_path)
            threshold_time = datetime.now() - timedelta(days=GC_THRESHOLD_DAYS)
            if sentinel_mtime < threshold_time.timestamp():
                _cleanup_old_cache_files()
    except OSError:
        # Cache maintenance must not prevent conversion.
        pass

    result = _cache.read(cache_filename, entry_key)
    if result is not _cache.MISS:
        return result

    result = converter(file_path)
    if input_key == _cache.file_key(file_path) and converter_key == _cache.function_key(converter):
        _cache.write(cache_filename, entry_key, result)
    return result
