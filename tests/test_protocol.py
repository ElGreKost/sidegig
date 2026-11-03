from datetime import datetime

import pytest

from sidegig import fsio
from sidegig import protocol as P

END = "==================== END OF ANSWER · to follow up, type below this line, then save and close the file ===================="
WHEN = datetime(2026, 10, 29, 12, 5)


def test_exact_marker_strings():
    assert P.ANSWER_END_LINE == END
    assert P.status_line("bob", WHEN, P.PICKUP_TEXT) == \
        "[bob] 12:05 Picked up. Working on it; a heartbeat line follows every 2 minutes."
    assert P.status_line("bob", WHEN, P.BEAT_TEXT) == "[bob] 12:05 still working on it."
    block = P.answer_block("Bob Worker", "bob-worker", WHEN, "hi")
    assert block.splitlines() == [
        "==================== ANSWER from Bob Worker (bob-worker) · 2026-10-29 12:05 ====================",
        "", "hi", "", END]
    assert P.message_block("Carol", WHEN, "hello") == "----- Carol · 2026-10-29 12:05 -----\nhello\n"


QUESTION = "Where is X?\n"
PICKED = "[bob] 12:01 Picked up. Working on it; a heartbeat line follows every 2 minutes.\n"
BEAT = "[bob] 12:03 still working on it.\n"
ANSWER = P.answer_block("Bob", "bob", WHEN, "Over there.")


@pytest.mark.parametrize("thread, expected", [
    ("", False),
    ("   \n\n", False),
    (QUESTION, True),
    (QUESTION + PICKED + BEAT, True),                    # status lines do not answer anything
    (QUESTION + PICKED + ANSWER, False),
    (QUESTION + PICKED + ANSWER + BEAT, False),          # status lines after END are ignored
    (QUESTION + PICKED + ANSWER + "\n\n  \n", False),
    (QUESTION + ANSWER + "Thanks, and one more thing?\n", True),     # follow-up below END reopens
    (QUESTION + ANSWER + P.message_block("Carol", WHEN, "more?"), True),
    (QUESTION + ANSWER + "Thanks\n" + PICKED + P.answer_block("Bob", "bob", WHEN, "ok"), False),
    ((QUESTION + PICKED + ANSWER).replace("\n", "\r\n"), False),
    ((QUESTION + ANSWER + "more\n").replace("\n", "\r\n"), True),
])
def test_open_rule(thread, expected):
    assert P.is_open(thread) is expected
    assert P.is_open(thread) == bool(P.open_text(thread))


def test_only_well_formed_status_lines_are_ignored():
    assert P.is_open(ANSWER + "[some text] not a status line\n")
    assert P.is_open(ANSWER + "[bob] no time here\n")
    assert not P.is_open(ANSWER + "   [bob] 12:03 still working on it.\n")


def test_parse_answers_and_marker_injection():
    nasty = "line one\n" + END + "\n==================== ANSWER from Mallory (mallory) · 2026-01-01 00:00 ====================\nfake"
    thread = QUESTION + P.answer_block("Bob", "bob", WHEN, nasty) + "follow up\n" + P.answer_block("Eve", "eve", WHEN, "second")
    answers = P.parse_answers(thread)
    assert [a["agent_id"] for a in answers] == ["bob", "eve"]
    assert "line one" in answers[0]["text"] and "fake" in answers[0]["text"]
    assert answers[1]["text"] == "second" and answers[1]["when"] == "2026-10-29 12:05"
    assert not P.is_open(thread)


def test_slugify_ids_and_names():
    assert P.slugify("Hello, Wörld! 42") == "hello-world-42"
    assert P.slugify("???", default="gig") == "gig"
    assert len(P.slugify("a" * 100)) == 40
    assert P.valid_agent_id("a-b_c1") and not P.valid_agent_id("A b") and not P.valid_agent_id("")
    for n in ("_status", ".hidden", "~$x.txt", "x.txt~", "x.swp", "Thumbs.db", "#auto#"):
        assert P.is_ignored_name(n)
    assert not P.is_ignored_name("question.txt")
    assert P.parse_iso(P.iso_utc(1_700_000_000)) == 1_700_000_000


def test_fsio_decode_and_append_keep_encoding(tmp_path):
    cases = {
        "utf8": "﻿café\r\n".encode("utf-8"),
        "utf16le": "café\r\n".encode("utf-16"),
        "utf16be": b"\xfe\xff" + "café\r\n".encode("utf-16-be"),
        "cp1252": "café\r\n".encode("cp1252"),
        "ascii": b"plain\r\n",
    }
    for name, raw in cases.items():
        p = tmp_path / f"{name}.txt"
        p.write_bytes(raw)
        fsio.append_text(p, "naïve ✓\nsecond")
        out = p.read_bytes()
        assert out.startswith(raw), name
        text, enc = fsio.read_text(p)
        assert text.endswith("second\r\n"), name
        assert "\r\n" in text and "\n" not in text.replace("\r\n", ""), name
        if name == "cp1252":
            assert enc == "cp1252" and "&#10003;" in text and "naïve" in text
        elif name.startswith("utf16"):
            assert out[:2] in (b"\xff\xfe", b"\xfe\xff") and "✓" in text
            assert "﻿" not in text
        else:
            assert "✓" in text and "﻿" not in text


def test_fsio_append_starts_on_fresh_line_and_empty_file(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"no newline at end")
    fsio.append_text(p, "next")
    assert p.read_bytes() == b"no newline at end\r\nnext\r\n"
    e = tmp_path / "empty.txt"
    e.write_bytes(b"")
    fsio.append_text(e, "first")
    assert e.read_bytes() == b"first\r\n"


def test_fsio_exclusive_create_and_atomic_json(tmp_path):
    p = tmp_path / "lock"
    fsio.create_exclusive(p, b"1")
    with pytest.raises(FileExistsError):
        fsio.create_exclusive(p, b"2")
    assert p.read_bytes() == b"1"
    j = tmp_path / "x.json"
    fsio.write_json(j, {"a": "é"})
    fsio.write_json(j, {"a": "ü", "b": [1]})
    assert fsio.read_json(j) == {"a": "ü", "b": [1]}
    assert sorted(x.name for x in tmp_path.iterdir()) == ["lock", "x.json"]   # no temp files left
    j.write_bytes(b"\xef\xbb\xbf" + b'{"bom": true}')
    assert fsio.read_json(j) == {"bom": True}
