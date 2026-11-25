"""Stateless random streams and canonical, lossless simulation fingerprints."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import sys
from pathlib import Path
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from fractions import Fraction
from typing import Any


_ATOMS = frozenset((str, int, bool, type(None)))
_DATACLASS_FIELDS: dict[type, tuple[str, ...]] = {}


def primitive(value: Any) -> Any:
    """Convert engine values to unambiguous JSON. No repr(), pickle or eval()."""
    # Exact-type fast paths; every branch returns what the general chain below
    # would return for the same value (exact str/int/bool/None, Fraction,
    # tuple/list, dict and already-seen dataclass types).
    t = type(value)
    if t in _ATOMS:
        return value
    if t is Fraction:
        return {"$rational": [value.numerator, value.denominator]}
    if t is tuple or t is list:
        return [primitive(v) for v in value]
    names = _DATACLASS_FIELDS.get(t)
    if names is not None:
        return {name: primitive(getattr(value, name)) for name in names}
    if t is dict:
        for k in value:
            if type(k) is not str and not isinstance(k, str):
                raise TypeError("canonical objects require string keys")
        return {k: primitive(v) for k, v in value.items()}
    return _primitive_general(value)


def _primitive_general(value: Any) -> Any:
    if isinstance(value, Fraction):
        return {"$rational": [value.numerator, value.denominator]}
    if isinstance(value, Enum):
        return primitive(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        names = tuple(f.name for f in fields(value))
        _DATACLASS_FIELDS[type(value)] = names
        return {name: primitive(getattr(value, name)) for name in names}
    if isinstance(value, Mapping):
        if not all(isinstance(k, str) for k in value):
            raise TypeError("canonical objects require string keys")
        return {k: primitive(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [primitive(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((primitive(v) for v in value), key=canonical)
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise TypeError(f"not a canonical value: {type(value).__name__}")


# One shared encoder with exactly the json.dumps() options used before;
# json.dumps() would construct an identical JSONEncoder on every call.
_ENCODER = json.JSONEncoder(sort_keys=True, ensure_ascii=False,
                            separators=(",", ":"), allow_nan=False)
_encode = _ENCODER.encode


def canonical(value: Any) -> bytes:
    return _encode(primitive(value)).encode("utf-8")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


_sha256 = hashlib.sha256


def digest(*key: str | int) -> bytes:
    # JSON preserves int vs string, and prevents ambiguous concatenated keys.
    for k in key:
        if type(k) is not str and type(k) is not int:
            raise TypeError("random keys must contain only typed strings and integers")
    # primitive() of a tuple of exact str/int is the same list of values.
    return _sha256(_encode(key).encode("utf-8")).digest()


def uniform(seed: int, stream: str, tick: int, location: str, sub: int = 0) -> float:
    bits = int.from_bytes(digest(seed, stream, tick, location, sub)[:8], "big")
    return (bits >> 11) * 2.0 ** -53


def normal(seed: int, stream: str, tick: int, location: str) -> float:
    u = max(uniform(seed, stream, tick, location, 0), 2.0 ** -53)
    v = uniform(seed, stream, tick, location, 1)
    return math.sqrt(-2 * math.log(u)) * math.cos(2 * math.pi * v)


def runtime_fingerprint() -> dict[str, str]:
    return {"python": sys.version, "implementation": platform.python_implementation(),
            "architecture": platform.machine(), "platform": platform.platform(),
            "libc": ":".join(platform.libc_ver())}


def engine_source_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    files = (sorted((root / 'engine').glob('*.py'), key=lambda p: p.name)
             + sorted((root / 'data').glob('*.json'), key=lambda p: p.name)
             + [root / '__init__.py', root / '__main__.py', root / 'sdk.py'])
    return fingerprint({path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files})
