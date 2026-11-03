import json
import os
import re
import threading

import pytest

from sidegig import Board, Conflict, Invalid, NotFound, fsio
from sidegig import protocol as P


def thread_of(board, gid):
    return board.read(gid)["thread"]


def lock_path(board, gid):
    return board.gigs_dir / gid / "claim.lock"


def gig_json(board, gid):
    return json.loads((board.gigs_dir / gid / "gig.json").read_text("utf-8"))


# ---------------------------------------------------------------- init, agents

def test_init_layout_and_guide(board):
    assert (board.root / "board.json").is_file()
    for d in ("gigs", "agents", "_status"):
        assert (board.root / d).is_dir()
    info = json.loads((board.root / "board.json").read_text("utf-8"))
    assert info["format"] == 1 and info["name"] == "test board" and info["rules"] == "Be kind."
    guide = (board.root / "BOARD_GUIDE.txt").read_bytes()
    assert b"\r\n" in guide and b"Be kind." in guide
    again = board.init("other name")
    assert again["already_existed"] and again["name"] == "test board"


def test_not_a_board(tmp_path):
    with pytest.raises(NotFound) as e:
        Board(tmp_path / "nothing").post("x")
    assert "init" in e.value.hint


def test_register_and_list(board):
    a = board.register("Alice Helper", skills=["Python", "SQL"], description="d", owner="team a")
    assert a["id"] == "alice-helper" and a["skills"] == ["python", "sql"] and a["owner"] == "team a"
    board.register("Bob", skills="sql,rust")
    assert [x["id"] for x in board.agents()] == ["alice-helper", "bob"]
    assert [x["id"] for x in board.agents(skill="rust")] == ["bob"]
    assert [x["id"] for x in board.agents(skill="SQL")] == ["alice-helper", "bob"]
    again = board.register("Alice Helper", description="new")
    assert again["id"] == "alice-helper" and again["skills"] == ["python", "sql"]
    assert again["description"] == "new" and again["created"] == a["created"]
    other = board.register("alice  helper!")        # same slug, different name
    assert other["id"] == "alice-helper-2"
    assert set(a) >= {"id", "name", "owner", "skills", "description", "created", "last_seen"}
    with pytest.raises(Invalid):
        board.register("   ")


def test_status_file_and_online_flag(board, clock, agents):
    assert not any(a["online"] for a in board.agents())
    board.write_status("bob-worker")
    assert [a["id"] for a in board.agents() if a["online"]] == ["bob-worker"]
    raw = (board.status_dir / "bob-worker.txt").read_bytes()
    assert raw.startswith(b"ONLINE\r\n") and b"last seen:" in raw
    clock.advance(301)
    assert not any(a["online"] for a in board.agents())
    board.write_status("bob-worker", "OFFLINE (worker stopped)")
    assert not any(a["online"] for a in board.agents())
    with pytest.raises(NotFound):
        board.write_status("ghost")


# ---------------------------------------------------------------- post, list, read

def test_post_list_read(board, agents):
    gid = board.post("How does X work?", "details here", tags=["Docs", "x"], asker="Carol", to_agent="bob-worker")
    assert re.match(r"\d{4}-\d\d-\d\d-how-does-x-work-[0-9a-f]{4}$", gid)
    meta = gig_json(board, gid)
    assert meta["title"] == "How does X work?" and meta["tags"] == ["docs", "x"] and meta["status"] == "open"
    assert meta["asker"] == "Carol" and meta["to_agent"] == "bob-worker" and meta["claimed_by"] is None
    r = board.read(gid)
    assert r["open"] and r["meta"]["id"] == gid and r["extra"] == []
    assert r["thread"].startswith("----- Carol · ") and "How does X work?" in r["thread"] and "details here" in r["thread"]
    assert b"\r\n" in (board.gigs_dir / gid / "thread.txt").read_bytes()

    other = board.post("Second", asker="Dave")
    assert [g["id"] for g in board.gigs()] == sorted([gid, other], reverse=True) or len(board.gigs()) == 2
    assert [g["id"] for g in board.gigs(tag="DOCS")] == [gid]
    assert [g["id"] for g in board.gigs(asker="dave")] == [other]
    assert [g["id"] for g in board.gigs(for_agent="alice-helper")] == [other]       # not addressed to her
    assert {g["id"] for g in board.gigs(for_agent="bob-worker")} == {gid, other}
    assert len(board.gigs(status="open")) == 2 and board.gigs(status="claimed") == []

    with pytest.raises(NotFound):
        board.post("x", to_agent="nobody")
    with pytest.raises(Invalid):
        board.post("   ")


@pytest.mark.parametrize("bad", ["../x", "..", "a/b", "a\\b", "", "_hidden", ".git"])
def test_bad_gig_ids_are_refused(board, bad):
    with pytest.raises(Invalid):
        board.read(bad)


def test_unknown_gig(board):
    with pytest.raises(NotFound):
        board.read("2026-01-01-nothing-0000")


# ---------------------------------------------------------------- claims

def test_claim_writes_pickup_and_holds(board, agents):
    gid = board.post("q", "text")
    got = board.claim(gid, "alice-helper")
    assert got["meta"]["status"] == "claimed" and got["meta"]["claimed_by"] == "alice-helper" and not got["resumed"]
    assert json.loads(lock_path(board, gid).read_text("utf-8"))["agent"] == "alice-helper"
    th = thread_of(board, gid)
    assert re.search(r"^\[alice-helper\] \d\d:\d\d Picked up\. Working on it; a heartbeat line follows every 2 minutes\.$", th, re.M)
    with pytest.raises(Conflict, match="already claimed by alice-helper"):
        board.claim(gid, "bob-worker")
    again = board.claim(gid, "alice-helper")                      # idempotent for the holder
    assert again["resumed"] and thread_of(board, gid).count("Picked up.") == 1
    with pytest.raises(NotFound):
        board.claim(gid, "ghost")


def test_claim_race_exactly_one_wins(board, clock):
    gid = board.post("race", "who wins")
    ids = [board.register(f"Racer {i}")["id"] for i in range(8)]
    boards = [Board(board.root, clock=clock) for _ in ids]
    barrier = threading.Barrier(len(ids))
    results = []

    def go(b, aid):
        barrier.wait()
        try:
            b.claim(gid, aid)
            results.append((aid, "won"))
        except Conflict:
            results.append((aid, "lost"))

    ts = [threading.Thread(target=go, args=(b, a)) for b, a in zip(boards, ids)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    won = [a for a, r in results if r == "won"]
    assert len(results) == 8 and len(won) == 1
    assert json.loads(lock_path(board, gid).read_text("utf-8"))["agent"] == won[0]
    assert board.read(gid)["meta"]["claimed_by"] == won[0]
    assert thread_of(board, gid).count("Picked up.") == 1


def test_stale_claim_takeover(board, clock, agents):
    gid = board.post("t", "q")
    board.claim(gid, "alice-helper")
    clock.advance(300)
    with pytest.raises(Conflict, match="already claimed"):
        board.claim(gid, "bob-worker")
    clock.advance(301)                                           # 601 s without a heartbeat
    got = board.claim(gid, "bob-worker")
    assert got["took_over_from"] == "alice-helper" and got["meta"]["claimed_by"] == "bob-worker"
    th = thread_of(board, gid)
    assert "[bob-worker]" in th and "Took over from [alice-helper]: no heartbeat for 10 minutes." in th
    assert json.loads(lock_path(board, gid).read_text("utf-8"))["agent"] == "bob-worker"
    with pytest.raises(Conflict):
        board.answer(gid, "alice-helper", "too late")


def test_heartbeat_keeps_a_claim_alive(board, clock, agents):
    gid = board.post("t", "q")
    board.claim(gid, "alice-helper")
    clock.advance(400)
    assert board.heartbeat(gid, "alice-helper")["written"]
    clock.advance(400)                                           # 400 s since the last beat: not stale
    with pytest.raises(Conflict):
        board.claim(gid, "bob-worker")


def test_stale_takeover_race_has_one_winner(board, clock):
    gid = board.post("t", "q")
    ids = [board.register(f"Taker {i}")["id"] for i in range(6)]
    board.claim(gid, ids[0])
    clock.advance(700)
    boards = [Board(board.root, clock=clock) for _ in ids[1:]]
    barrier = threading.Barrier(len(boards))
    won = []

    def go(b, aid):
        barrier.wait()
        try:
            b.claim(gid, aid)
            won.append(aid)
        except Conflict:
            pass

    ts = [threading.Thread(target=go, args=(b, a)) for b, a in zip(boards, ids[1:])]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(won) == 1
    assert json.loads(lock_path(board, gid).read_text("utf-8"))["agent"] == won[0]
    assert thread_of(board, gid).count("Took over from") == 1


def test_addressed_gig_only_for_that_agent(board, agents):
    gid = board.post("for bob", "q", to_agent="bob-worker")
    with pytest.raises(Conflict, match="addressed to bob-worker"):
        board.claim(gid, "alice-helper")
    board.claim(gid, "bob-worker")


# ---------------------------------------------------------------- heartbeat and give-up

def test_heartbeat_rate_limit(board, clock, agents):
    gid = board.post("hb", "q")
    board.claim(gid, "alice-helper")
    assert board.heartbeat(gid, "alice-helper")["written"] is False
    clock.advance(59)
    assert board.heartbeat(gid, "alice-helper")["written"] is False
    clock.advance(2)
    assert board.heartbeat(gid, "alice-helper")["written"] is True
    assert board.heartbeat(gid, "alice-helper")["written"] is False
    assert thread_of(board, gid).count("still working on it.") == 1
    assert re.search(r"^\[alice-helper\] \d\d:\d\d still working on it\.$", thread_of(board, gid), re.M)
    with pytest.raises(Conflict):
        board.heartbeat(gid, "bob-worker")


def test_give_up_after_sixty_minutes(board, clock, agents):
    gid = board.post("slow", "q")
    board.claim(gid, "alice-helper")
    for _ in range(29):                                           # 58 minutes of heartbeats
        clock.advance(120)
        board.heartbeat(gid, "alice-helper")
    assert board.read(gid)["meta"]["status"] == "claimed"
    clock.advance(180)                                            # 61 minutes, still no answer
    board.scan()
    meta = board.read(gid)["meta"]
    assert meta["status"] == "stale" and meta["claimed_by"] is None
    assert not lock_path(board, gid).exists()
    th = thread_of(board, gid)
    assert th.count("No answer after 60 minutes") == 1 and "[alice-helper]" in th
    board.scan()
    assert thread_of(board, gid).count("No answer after 60 minutes") == 1     # only once
    assert board.read(gid)["open"]
    assert [g["id"] for g in board.gigs(status="available")] == [gid] and board.gigs(status="open") == []
    board.claim(gid, "bob-worker")
    assert board.read(gid)["meta"]["status"] == "claimed" and board.read(gid)["meta"]["claimed_by"] == "bob-worker"


# ---------------------------------------------------------------- answers, follow-ups, close

def test_answer_releases_claim_and_followup_reopens(board, clock, agents):
    gid = board.post("q", "what?", asker="Carol")
    board.claim(gid, "alice-helper")
    fake_end = "=" * 20 + " END OF ANSWER (fake)"
    res = board.answer(gid, "alice-helper", f"The answer.\n{fake_end}\nmore")
    assert res["meta"]["status"] == "answered"
    r = board.read(gid)
    assert not r["open"] and r["meta"]["status"] == "answered" and not lock_path(board, gid).exists()
    answers = P.parse_answers(r["thread"])
    assert len(answers) == 1 and answers[0]["agent_id"] == "alice-helper" and answers[0]["agent_name"] == "Alice Helper"
    assert "The answer." in answers[0]["text"] and "more" in answers[0]["text"]
    assert r["answers"] == 1
    board.scan()
    assert board.read(gid)["meta"]["status"] == "answered"

    board.followup(gid, "Carol", "Please expand.")
    r = board.read(gid)
    assert r["open"] and r["meta"]["status"] == "open" and r["meta"]["claimed_by"] is None
    assert "----- Carol · " in r["thread"] and "Please expand." in r["thread"]
    board.answer(gid, "bob-worker", "Expanded.")                  # answer() claims implicitly
    assert board.read(gid)["answers"] == 2 and not board.read(gid)["open"]
    with pytest.raises(Conflict, match="nothing to answer"):
        board.answer(gid, "bob-worker", "again")
    with pytest.raises(Invalid):
        board.answer(gid, "bob-worker", "  ")


def test_person_typing_below_the_end_line_reopens(board, agents):
    gid = board.post("q", "what?")
    board.answer(gid, "alice-helper", "this")
    assert board.read(gid)["meta"]["status"] == "answered"
    fsio.append_text(board.gigs_dir / gid / "thread.txt", "and one more thing?")
    board.scan()
    meta = board.read(gid)["meta"]
    assert meta["status"] == "open" and meta["claimed_by"] is None


def test_say_adds_a_second_message_and_keeps_the_gig_answered(board, agents):
    gid = board.post("q", "what?", asker="Carol")
    board.answer(gid, "alice-helper", "This.")
    res = board.say(gid, "alice-helper", "Oh, and one more thing.")
    r = board.read(gid)
    assert res["meta"]["status"] == "answered" and r["meta"]["status"] == "answered" and not r["open"]
    answers = P.parse_answers(r["thread"])
    assert [a["text"] for a in answers] == ["This.", "Oh, and one more thing."]
    assert not lock_path(board, gid).exists()
    with pytest.raises(Conflict, match="not in this conversation"):
        board.say(gid, "bob-worker", "me too")              # bob never answered
    with pytest.raises(Invalid):
        board.say(gid, "alice-helper", " ")


def test_say_on_an_open_thread_is_an_answer(board, agents):
    gid = board.post("q", "what?")
    board.say(gid, "bob-worker", "Here.")
    r = board.read(gid)
    assert r["answers"] == 1 and r["meta"]["status"] == "answered"


def test_say_follows_the_addressed_agent_and_refuses_a_closed_gig(board, agents):
    gid = board.post("q", "what?", to_agent="alice-helper")
    board.answer(gid, "alice-helper", "This.")
    board.say(gid, "alice-helper", "Still here.")
    with pytest.raises(Conflict, match="not in this conversation"):
        board.say(gid, "bob-worker", "hi")
    board.close(gid, "Carol")
    with pytest.raises(Conflict, match="closed"):
        board.say(gid, "alice-helper", "bye")


def test_followup_while_claimed_keeps_the_claim(board, agents):
    gid = board.post("q", "what?")
    board.claim(gid, "alice-helper")
    board.followup(gid, "Carol", "also this")
    assert board.read(gid)["meta"]["status"] == "claimed"


def test_close_with_rating_and_note(board, agents):
    gid = board.post("q", "what?", asker="Carol")
    board.answer(gid, "alice-helper", "this")
    res = board.close(gid, "Carol", rating=5, note="great\nthanks")
    meta = res["meta"]
    assert meta["status"] == "closed" and meta["rating"] == 5 and meta["close_note"] == "great thanks"
    th = thread_of(board, gid)
    assert re.search(r"^\[carol\] \d\d:\d\d Closed by Carol\. Rating: 5/5\. great thanks$", th, re.M)
    assert not board.read(gid)["open"]
    board.scan()
    assert gig_json(board, gid)["status"] == "closed"
    with pytest.raises(Conflict):
        board.followup(gid, "Carol", "more")
    with pytest.raises(Conflict):
        board.claim(gid, "bob-worker")
    with pytest.raises(Conflict):
        board.close(gid, "Carol")


@pytest.mark.parametrize("rating", [0, 6, 2.5, True, "x"])
def test_close_rejects_bad_rating(board, agents, rating):
    gid = board.post("q")
    with pytest.raises(Invalid):
        board.close(gid, "Carol", rating=rating)


def test_close_unanswered_gig_releases_claim(board, agents):
    gid = board.post("q")
    board.claim(gid, "alice-helper")
    board.close(gid, "Carol", note="never mind")
    assert not lock_path(board, gid).exists() and board.read(gid)["meta"]["status"] == "closed"


def test_info_counts(board, agents):
    a, b = board.post("one"), board.post("two")
    board.answer(a, "alice-helper", "x")
    board.close(b, "Carol")
    info = board.info()
    assert info["gigs"]["answered"] == 1 and info["gigs"]["closed"] == 1 and info["agents"] == 2 and info["name"] == "test board"


# ---------------------------------------------------------------- human folders

def make_human(board, folder, name, raw):
    d = board.gigs_dir / folder
    d.mkdir(exist_ok=True)
    (d / name).write_bytes(raw)
    return d


def test_adopt_utf8_bom_folder_and_keep_encoding(board, agents):
    d = make_human(board, "my question", "q.txt", b"\xef\xbb\xbfWhat is Y?\r\nmore detail\r\n")
    snaps = board.scan()
    assert [s["id"] for s in snaps] == ["my question"]
    meta = json.loads((d / "gig.json").read_text("utf-8"))
    assert meta["title"] == "What is Y?" and meta["asker"] == "human" and meta["status"] == "open"
    assert meta["to_agent"] is None and meta["tags"] == []
    board.answer("my question", "bob-worker", "Y is a thing")
    raw = (d / "q.txt").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and raw.count(b"\xef\xbb\xbf") == 1
    text = raw.decode("utf-8-sig")
    assert "ANSWER from Bob Worker (bob-worker)" in text and "Y is a thing" in text
    assert "\n" not in text.replace("\r\n", "")
    assert board.read("my question")["meta"]["status"] == "answered"


@pytest.mark.parametrize("codec", ["utf-16", "utf-16-be-bom"])
def test_adopt_utf16_folder_and_keep_encoding(board, agents, codec):
    original = "Ερώτηση: πώς δουλεύει;\r\nλεπτομέρειες\r\n"
    raw = original.encode("utf-16") if codec == "utf-16" else b"\xfe\xff" + original.encode("utf-16-be")
    bom = raw[:2]
    d = make_human(board, "ελληνικά", "ερώτηση.txt", raw)
    board.scan()
    assert gig_json(board, "ελληνικά")["title"] == "Ερώτηση: πώς δουλεύει;"
    board.answer("ελληνικά", "alice-helper", "Απάντηση ✓")
    out = (d / "ερώτηση.txt").read_bytes()
    assert out.startswith(raw) and out[:2] == bom
    decoded = out.decode("utf-16")
    assert "Απάντηση ✓" in decoded and "\ufeff" not in decoded
    assert "\n" not in decoded.replace("\r\n", "")
    assert not board.read("ελληνικά")["open"]


def test_adopt_cp1252_folder_and_keep_encoding(board, agents):
    raw = "Caf\u00e9 menu?\r\n".encode("cp1252")
    d = make_human(board, "cafe", "q.txt", raw)
    board.scan()
    assert gig_json(board, "cafe")["title"] == "Caf\u00e9 menu?"
    board.answer("cafe", "bob-worker", "na\u00efve \u2192 yes")
    out = (d / "q.txt").read_bytes()
    assert out.startswith(raw)
    text = out.decode("cp1252")
    assert "na\u00efve &#8594; yes" in text


def test_folder_with_several_txt_files(board, agents):
    d = board.gigs_dir / "multi"
    d.mkdir()
    (d / "b_followup.txt").write_bytes(b"Second file: some context\r\n")
    (d / "a_question.txt").write_bytes(b"First file: the real question\r\n")
    os.utime(d / "b_followup.txt", (1_700_000_100, 1_700_000_100))   # older than the other one
    board.scan()
    r = board.read("multi")
    assert r["meta"]["title"] == "Second file: some context" and r["meta"]["thread"] == "b_followup.txt"
    assert [e["file"] for e in r["extra"]] == ["a_question.txt"] and "real question" in r["extra"][0]["text"]
    board.answer("multi", "alice-helper", "done")
    assert board.read("multi")["meta"]["thread"] == "b_followup.txt"      # still the same thread file
    assert "done" in board.read("multi")["thread"] and "done" not in board.read("multi")["extra"][0]["text"]


def test_thread_txt_wins_over_other_files(board):
    d = board.gigs_dir / "mixed"
    d.mkdir()
    (d / "notes.txt").write_bytes(b"notes\r\n")
    (d / "thread.txt").write_bytes(b"Real question\r\n")
    os.utime(d / "thread.txt", (1_800_000_000, 1_800_000_000))
    board.scan()
    assert board.read("mixed")["meta"]["title"] == "Real question"


def test_ignored_names_and_empty_files(board):
    (board.gigs_dir / "_private").mkdir()
    (board.gigs_dir / "_private" / "q.txt").write_bytes(b"hidden?\r\n")
    (board.gigs_dir / ".hidden").mkdir()
    (board.gigs_dir / ".hidden" / "q.txt").write_bytes(b"hidden?\r\n")
    make_human(board, "temps", "~$q.txt", b"lock file\r\n")
    make_human(board, "temps2", "q.txt~", b"backup\r\n")
    make_human(board, "empty", "q.txt", b"  \r\n")
    make_human(board, "notxt", "image.png", b"\x89PNG")
    assert board.scan() == []
    assert not (board.gigs_dir / "_private" / "gig.json").exists() and not (board.gigs_dir / "empty" / "gig.json").exists()
    (board.gigs_dir / "empty" / "q.txt").write_bytes(b"Now it has text\r\n")
    assert [s["id"] for s in board.scan()] == ["empty"]


def test_corrupt_gig_json_does_not_break_the_scan(board):
    good = board.post("fine")
    bad = board.gigs_dir / "broken"
    bad.mkdir()
    (bad / "gig.json").write_bytes(b"{not json")
    (bad / "q.txt").write_bytes(b"text\r\n")
    assert [s["id"] for s in board.scan()] == [good]
    assert board.warnings and "broken" in board.warnings[-1]
