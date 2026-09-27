"""A file wrapper which acts as a cache for on-demand evaluation of conversions.

This object is used in lieu of a file in order to allow the various importers to
reuse each others' conversion results. Converting file contents, e.g. PDF to
text, can be expensive.

NOTE: This module is deprecated. Use beangulp.simple_cache instead.
"""

__copyright__ = "Copyright (C) 2016  Martin Blais"
__license__ = "GNU GPLv2"

import codecs
import functools
import os
import sys

from contextlib import suppress
from os import path

import chardet

from beangulp import mimetypes
from beangulp import utils
from beangulp import _cache as disk_cache

# NOTE: See get_file() at the end of this file to create instances of FileMemo.


# Default location of cache directories.
CACHEDIR = (
    path.expandvars("%LOCALAPPDATA%\\Beangulp")
    if sys.platform == "win32"
    else path.expanduser("~/.cache/beangulp")
)


# Maximum number of bytes to read in order to detect the encoding of a file.
HEAD_DETECT_MAX_BYTES = 128 * 1024


class _FileMemo:
    """A file memoizer which acts as a cache for on-demand evaluation of conversions.

    Attributes:
      name: A string, the name of the underlying file.
    """

    def __init__(self, filename):
        self.name = filename

        # A cache of converter function to saved conversion value.
        self._cache = {}

    def __str__(self):
        return f'<FileWrapper filename="{self.name}">'

    def convert(self, converter_func):
        """Return the cached result of a file conversion.
        Args:
          converter_func: A callable which accepts a filename and produces some
          derived version of the file contents.
        Returns:
          The value returned by the converter.
        """
        try:
            result = self._cache[converter_func]
        except KeyError:
            # FIXME: Implement timing of conversions here. Store it for
            # reporting later.
            result = self._cache[converter_func] = converter_func(self.name)
        return result

    def mimetype(self):
        """Computes the MIME type of the file."""
        return self.convert(mimetype)

    def head(self, num_bytes=8192, encoding=None):
        """An alias for reading just the first bytes of a file."""
        return self.convert(head(num_bytes, encoding=encoding))

    def contents(self):
        """An alias for reading the entire contents of the file."""
        return self.convert(contents)


def mimetype(filename):
    """A converter that computes the MIME type of the file.

    Returns:
      A converter function.
    """
    mtype, _ = mimetypes.guess_type(filename, strict=False)
    return mtype


def head(num_bytes=8192, encoding=None):
    """A converter that just reads the first bytes of a file.

    An incomplete trailing character is omitted from the returned string.
    This can occur with variable-width encodings such as UTF-8.

    Args:
      num_bytes: The number of bytes to read.
      encoding: The text encoding, or None to detect it from the bytes read.
    Returns:
      A converter function.
    """
    kind = type(num_bytes)
    if ((kind is int or kind is bool or num_bytes is None)
            and (encoding is None or type(encoding) is str)):
        return _cached_head(num_bytes, encoding)
    return _make_head(num_bytes, encoding)


def _make_head(num_bytes, encoding):
    def head_reader(filename):
        with open(filename, "rb") as fd:
            data = fd.read(num_bytes)
            if not data:
                return ""
            enc = encoding or chardet.detect(data)["encoding"]
            decoder = codecs.getincrementaldecoder(enc)()
            return decoder.decode(data, final=False)

    return head_reader


_cached_head = functools.lru_cache(maxsize=128, typed=True)(_make_head)


def contents(filename):
    """A converter that just reads the entire contents of a file.

    Args:
      num_bytes: The number of bytes to read.
    Returns:
      A converter function.
    """
    # Attempt to detect the input encoding automatically, using chardet and a
    # decent amount of input.
    with open(filename, "rb") as infile:
        rawdata = infile.read(HEAD_DETECT_MAX_BYTES)
    detected = chardet.detect(rawdata)
    encoding = detected["encoding"]

    # Ignore encoding errors for reading the contents because input files
    # routinely break this assumption.
    errors = "ignore"

    with open(filename, encoding=encoding, errors=errors) as file:
        return file.read()


def get_file(filename):
    """Create or reuse a globally registered instance of a FileMemo.

    Note: the FileMemo objects' lifetimes are reused for the duration of the
    process. This is usually the intended behavior. Always create them by
    calling this constructor.

    Args:
      filename: A path string, the absolute name of the file whose memo to create.
    Returns:
      A FileMemo instance.

    """
    assert path.isabs(filename), (
        "Path should be absolute in order to guarantee a single call."
    )
    return _CACHE[filename]


_CACHE = utils.DefaultDictWithKey(_FileMemo)


def cache(func=None, *, key=None):
    """Memoise a file conversion with timestamp or caller-supplied invalidation.

    Python function code, immutable defaults, closure values and arguments
    identify each conversion by value. Results must not depend on object
    identity, shared references or frozenset iteration order.
    Unsupported state runs uncached. External
    dependencies are not tracked and must remain stable, or be represented by
    an immutable argument. Missing or damaged entries are recomputed even when
    cache=True. Cache storage failures do not discard the converted result.
    The cache directory must be trusted because entries contain pickle data.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(filename, *args, cache=None, **kwargs):
            # Preserve the legacy input stat requirement, including forced reads.
            input_mtime = os.stat(filename).st_mtime_ns
            converter_key = disk_cache.function_key(func)
            policy_key = disk_cache.function_key(key) if key is not None else b"filename"
            if converter_key is None or policy_key is None:
                return func(filename, *args, **kwargs)
            input_key = key(filename) if key is not None else (path.abspath(filename), filename)
            entry_key = disk_cache.key("legacy", converter_key, policy_key,
                                       input_key, args, tuple(kwargs.items()))
            if entry_key is None:
                return func(filename, *args, **kwargs)
            name = "v1-" + entry_key.hex() + ".pickle"
            cache_fname = path.join(CACHEDIR, name)

            # We inspect the modified time of the input file and the cache.
            cache_mtime = None
            with suppress(OSError):
                cache_mtime = os.stat(cache_fname).st_mtime_ns

            if cache is None:
                # Read from cache when a key function has been supplied and the
                # cache file exists or when the filename has been used to
                # compute the cache key and the cache entry modification time is
                # equal or later the input file modification time.
                cache = cache_mtime is not None and (key is not None or cache_mtime >= input_mtime)

            if cache:
                result = disk_cache.read(cache_fname, entry_key)
                if result is not disk_cache.MISS:
                    return result

            # Invoke the potentially expensive function.
            ret = func(filename, *args, **kwargs)

            if converter_key == disk_cache.function_key(func):
                disk_cache.write(cache_fname, entry_key, ret, input_mtime)
            return ret

        return wrapper

    if func is None:
        return decorator
    return decorator(func)
