"""Filesystem helpers: encoding-aware text IO, exclusive create, atomic JSON writes.

Everything here is stdlib only and written to behave on Windows (replaces are retried when
the target is briefly open elsewhere) and on network shares (no reliance on locks other than
O_CREAT|O_EXCL).
"""
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Callable, Tuple

_BIN = getattr(os, "O_BINARY", 0)


def _retry(fn: Callable[[], Any], tries: int = 25, delay: float = 0.02) -> Any:
    """Call fn, retrying on PermissionError (Windows reports a briefly busy file that way)."""
    for i in range(tries):
        try:
            return fn()
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(delay)


def to_crlf(text: str) -> str:
    """Normalise any newline style to CRLF."""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")


def decode(raw: bytes) -> Tuple[str, str]:
    """Decode file bytes -> (text, encoding). UTF-8 (BOM ok), UTF-16 by BOM, else cp1252."""
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", "replace"), "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "replace"), "utf-16-be"
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace"), "cp1252"


def read_text(path) -> Tuple[str, str]:
    """Read a text file -> (text, encoding)."""
    return decode(_retry(lambda: Path(path).read_bytes()))


def _encode(text: str, enc: str) -> bytes:
    # cp1252 cannot hold every character: write an XML character reference rather than a question mark.
    errors = "xmlcharrefreplace" if enc == "cp1252" else "replace"
    return to_crlf(text).encode(enc, errors)


def append_text(path, text: str) -> None:
    """Append in the file's own encoding (BOM untouched), CRLF, starting on a fresh line."""
    old, enc = read_text(path)
    if old and not old.endswith(("\n", "\r")):
        text = "\n" + text
    if not text.endswith("\n"):
        text += "\n"
    data = _encode(text, enc)

    def _write() -> None:
        with open(path, "ab") as f:
            f.write(data)

    _retry(_write)


def create_exclusive(path, data: bytes) -> None:
    """Create path with data using O_CREAT|O_EXCL; FileExistsError if it exists."""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | _BIN
    fd = -1
    for i in range(10):
        try:
            fd = os.open(str(path), flags, 0o666)
            break
        except PermissionError:
            # Windows: a file that is delete-pending (or just deleted while open) reports this.
            if os.path.exists(path):
                raise FileExistsError(str(path))
            if i == 9:
                raise
            time.sleep(0.02)
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
    finally:
        os.close(fd)


def create_text(path, text: str) -> None:
    """Create a new UTF-8 CRLF text file; FileExistsError if it exists (O_EXCL)."""
    create_exclusive(path, to_crlf(text).encode("utf-8"))


def replace_retry(src, dst, tries: int = 25, delay: float = 0.02) -> None:
    """os.replace, retrying on PermissionError (Windows: target briefly open elsewhere)."""
    _retry(lambda: os.replace(src, dst), tries, delay)


def _write_atomic(path, data: bytes) -> None:
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        replace_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_text_atomic(path, text: str) -> None:
    """Write a UTF-8 CRLF text file via temp file + os.replace."""
    _write_atomic(path, to_crlf(text).encode("utf-8"))


def json_bytes(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def write_json(path, obj: Any) -> None:
    """Write JSON via temp file + os.replace (readers never see a partial file)."""
    _write_atomic(path, json_bytes(obj))


def read_json(path) -> Any:
    """Read JSON (UTF-8, BOM ok). Raises OSError / ValueError."""
    raw = _retry(lambda: Path(path).read_bytes())
    return json.loads(raw.decode("utf-8-sig"))
