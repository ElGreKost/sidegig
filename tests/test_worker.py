"""The worker loop with an injected clock and captured output."""
from pathlib import Path

from sidegig.worker import Worker


def make(board, agent, **kw):
    out, err = [], []
    kw.setdefault("settle_s", 0)
    w = Worker(board, agent, out=out.append, err=err.append, sleep=lambda s: None, **kw)
    return w, out, err


def beats(board, gid):
    return board.read(gid)["thread"].count("still working on it")


def test_new_is_printed_once_and_thread_path_is_real(board, agents):
    gid = board.post("Question", "Please help", asker="someone", to_agent="alice-helper")
    w, out, err = make(board, "alice-helper")
    w.start()
    w.step()
    w.step()
    w.step()
    assert len(out) == 1 and out[0].startswith(f"NEW {gid} ")
    assert Path(out[0].split(" ", 2)[2]).is_file()
    assert board.gigs(status="claimed")[0]["claimed_by"] == "alice-helper"
    assert err == []


def test_start_writes_status_and_stop_marks_offline(board, agents):
    w, out, err = make(board, "alice-helper")
    w.run(once=True)
    text = (board.status_dir / "alice-helper.txt").read_text(encoding="utf-8")
    assert "OFFLINE" in text
    w2, _, _ = make(board, "alice-helper")
    w2.start()
    assert "ONLINE" in (board.status_dir / "alice-helper.txt").read_text(encoding="utf-8")


def test_addressed_to_somebody_else_is_ignored(board, agents):
    board.post("Not yours", "x", to_agent="bob-worker")
    w, out, _ = make(board, "alice-helper", auto_claim=True)
    w.start()
    w.step()
    assert out == []


def test_unaddressed_needs_auto_claim_and_matching_skill(board, agents):
    board.post("Fix my python script", "x", tags=["python"])
    board.post("Tune a query", "x", tags=["sql"])
    w, out, _ = make(board, "alice-helper")                 # no --auto-claim
    w.start()
    w.step()
    assert out == []
    w, out, _ = make(board, "alice-helper", skills=["python"], auto_claim=True)
    w.start()
    w.step()
    assert len(out) == 1 and out[0].startswith("NEW ") and "python" in out[0]


def test_skill_in_title_matches_and_no_skills_takes_anything(board, agents):
    board.post("Need Python help", "x")
    w, out, _ = make(board, "bob-worker", skills=["sql"], auto_claim=True)
    w.start()
    w.step()
    assert out == []
    w, out, _ = make(board, "bob-worker", skills=[], auto_claim=True)
    w.start()
    w.step()
    assert len(out) == 1


def test_resume_after_restart_without_duplicate_new(board, agents):
    gid = board.post("Q", "x", to_agent="alice-helper")
    first, out1, _ = make(board, "alice-helper")
    first.start()
    first.step()
    assert out1[0].startswith("NEW ")
    second, out2, _ = make(board, "alice-helper")          # the process was restarted
    second.start()
    second.step()
    second.step()
    assert len(out2) == 1 and out2[0].startswith(f"RESUME {gid} ")


def test_heartbeat_every_two_minutes(board, clock, agents):
    gid = board.post("Q", "x", to_agent="alice-helper")
    w, out, _ = make(board, "alice-helper")
    w.start()
    w.step()
    clock.advance(60)
    w.step()
    assert beats(board, gid) == 0                          # not yet
    clock.advance(61)
    w.step()
    assert beats(board, gid) == 1
    w.step()
    assert beats(board, gid) == 1                          # no second line in the same round
    clock.advance(121)
    w.step()
    assert beats(board, gid) == 2


def test_stale_after_sixty_minutes_is_reported_once_and_not_reclaimed(board, clock, agents):
    gid = board.post("Q", "x", to_agent="alice-helper")
    w, out, _ = make(board, "alice-helper")
    w.start()
    w.step()
    clock.advance(3601)
    w.step()
    w.step()
    assert out[0].startswith("NEW ") and out[1:] == [f"STALE {gid}"]
    assert board.gigs(status="stale")[0]["id"] == gid
    # somebody edits the thread again: the worker may take it once more
    board.followup(gid, "someone", "still need this")
    clock.advance(1)
    w.step()
    assert out[-1].startswith(f"NEW {gid} ")


def test_answered_gig_leaves_the_held_set(board, agents):
    gid = board.post("Q", "x", to_agent="alice-helper")
    w, out, _ = make(board, "alice-helper")
    w.start()
    w.step()
    board.answer(gid, "alice-helper", "done")
    w.step()
    assert gid not in w.held and len(out) == 1


def test_settle_delay_waits_for_stable_text(board, clock, agents):
    gid = board.post("Q", "draft", to_agent="alice-helper")
    w, out, _ = make(board, "alice-helper", settle_s=10)
    w.start()
    w.step()
    assert out == []
    clock.advance(6)
    w.step()
    assert out == []
    # the person edits the file: the timer restarts
    thread = Path(board.read(gid)["thread_file"])
    with open(thread, "a", encoding="utf-8", newline="") as fh:
        fh.write("\r\nmore detail\r\n")
    clock.advance(6)
    w.step()
    assert out == []
    clock.advance(11)
    w.step()
    assert len(out) == 1


def test_max_active_caps_claims(board, agents):
    for i in range(4):
        board.post(f"Q{i}", "x", to_agent="alice-helper")
    w, out, _ = make(board, "alice-helper", max_active=2)
    w.start()
    w.step()
    assert len(out) == 2 and len(w.held) == 2


def test_takes_over_a_stale_claim_of_somebody_else(board, clock, agents):
    gid = board.post("Q", "x")
    board.claim(gid, "bob-worker")
    w, out, _ = make(board, "alice-helper", auto_claim=True)
    w.start()
    w.step()
    assert out == []                                        # a live claim is respected
    clock.advance(601)
    w.step()
    assert len(out) == 1 and out[0].startswith(f"NEW {gid} ")


def test_never_claims_its_own_gigs(board, agents):
    board.post("Self", "x", asker="alice-helper", to_agent="alice-helper")
    w, out, _ = make(board, "alice-helper")
    w.start()
    w.step()
    assert out == []
