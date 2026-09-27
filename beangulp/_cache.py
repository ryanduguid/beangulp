"""Stable keys and atomic entries for trusted, disposable conversion caches."""

import hashlib
import math
import os
import pickle
import struct
import sys
import tempfile
import types
from contextlib import suppress


MISS = object()
_HEADER = b"beangulp-cache-v1\n"
_CODE_FIELDS = (
    "co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals",
    "co_stacksize", "co_flags", "co_code", "co_consts", "co_names",
    "co_varnames", "co_freevars", "co_cellvars", "co_name", "co_qualname",
    "co_filename", "co_firstlineno", "co_exceptiontable",
)


def _encode(value):
    """Encode exact immutable built-ins without calling user-defined hooks."""
    kind = type(value)
    if (kind is float or kind is complex) and (math.isnan(value.real) or math.isnan(value.imag)):
        raise TypeError("NaN values cannot form a cache key")
    if value is None:
        return b"N"
    if value is Ellipsis:
        return b"E"
    if kind is bool:
        return b"B1" if value else b"B0"
    if kind is int:
        return b"I" + value.to_bytes((value.bit_length() + 8) // 8, "big", signed=True)
    if kind is float:
        return b"D" + struct.pack("!d", value)
    if kind is complex:
        return b"Z" + struct.pack("!dd", value.real, value.imag)
    if kind is str:
        return b"S" + value.encode("utf-8", "surrogatepass")
    if kind is bytes:
        return b"Y" + value
    if kind is tuple:
        tag, parts = b"T", [_encode(item) for item in value]
    elif kind is frozenset:
        tag, parts = b"F", sorted(_encode(item) for item in value)
    elif kind is types.CodeType:
        tag, parts = b"C", [_encode(getattr(value, field, None)) for field in _CODE_FIELDS]
        lines = value.co_linetable if hasattr(value, "co_linetable") else value.co_lnotab
        parts.append(_encode(lines))
    else:
        raise TypeError("Unsupported cache key value")
    return tag + b"".join(len(part).to_bytes(8, "big") + part for part in parts)


def key(*parts):
    """Return a digest, or None when a value cannot form a safe stable key."""
    namespace = ("v1", sys.implementation.name, tuple(sys.version_info[:3]), sys.platform)
    try:
        data = _encode((namespace, parts))
    except (TypeError, ValueError, RecursionError):
        return None
    return hashlib.sha256(data).digest()


def function_key(func):
    """Fingerprint Python code and immutable defaults and closure values."""
    if type(func) is not types.FunctionType:
        return None
    attributes = func.__dict__
    defaults = func.__kwdefaults__
    if type(attributes) is not dict or attributes:
        return None
    if defaults is not None and type(defaults) is not dict:
        return None
    try:
        closure = tuple(cell.cell_contents for cell in (func.__closure__ or ()))
    except ValueError:
        return None
    return key(func.__module__, func.__qualname__, func.__code__, func.__defaults__,
               tuple(defaults.items()) if defaults is not None else (), closure)


def file_key(filename):
    """Hash current input bytes, rejecting changes observed during the read."""
    if type(filename) is not str:
        return None
    try:
        with open(filename, "rb") as source:
            before = os.fstat(source.fileno())
            digest = hashlib.sha256()
            for chunk in iter(lambda: source.read(128 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(source.fileno())
        current = os.stat(filename)
    except OSError:
        return None
    # Some Windows runtimes give stat() and fstat() different ctime values.
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if any(getattr(before, field) != getattr(stat, field)
           for stat in (after, current) for field in fields):
        return None
    return key(os.path.abspath(filename), filename, digest.digest())


def read(filename, expected_key):
    """Return a verified cache value, or MISS for an unusable entry."""
    try:
        with open(filename, "rb") as source:
            header = source.read(len(_HEADER) + 64)
            payload = source.read()
        expected = _HEADER + expected_key + hashlib.sha256(payload).digest()
        if header != expected:
            return MISS
        return pickle.loads(payload)
    except Exception:
        # Only cache loading is optional; converter errors are handled by callers.
        return MISS


def write(filename, entry_key, value, mtime_ns=None):
    """Publish a complete entry; storage failures leave conversion usable."""
    temporary = None
    try:
        payload = pickle.dumps(value, protocol=4)
        directory = os.path.dirname(filename) or "."
        os.makedirs(directory, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
            temporary = output.name
            output.write(_HEADER + entry_key + hashlib.sha256(payload).digest())
            output.write(payload)
        if mtime_ns is not None:
            os.utime(temporary, ns=(mtime_ns, mtime_ns))
        os.replace(temporary, filename)
    except Exception:
        # Serialization and storage failures must not discard a converted result.
        pass
    finally:
        if temporary is not None:
            with suppress(OSError):
                os.unlink(temporary)
