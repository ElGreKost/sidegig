"""sidegig wait and sidegig watch: the asking side's wake-ups."""
import json
import threading
import time

import pytest

from sidegig.asker import AskerWatch, wait_for_answer
from sidegig.board import check_answer
from sidegig.cli import main
from sidegig.errors import NotFound


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def watcher(board, asker="me", **kw):
    out, err = [], []
    w = AskerWatch(board, asker, out=out.append, err=err.append, sleep=lambda s: None, **kw)
    return w, out, err


def state_of(board, asker="me"):
    return json.loads((board.status_dir / f"{asker}.asker.json").read_text(encoding="utf-8"))


class FakeTime:
    """monotonic + sleep: time only moves when sleep is called."""

    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


# ------------------------------------------------------------------ shared rule
def test_check_answer_states(board, agents):
    gid = board.post("Q", "text", asker="me")
    r = board.read(gid)
    assert check_answer(r["meta"], r["open"], r["thread"])["state"] == "waiting"
    board.answer(gid, "alice-helper", "A1")
    st = board.answer_state(gid)
    assert st["state"] == "answered" and st["count"] == 1 and st["answer"]["text"] == "A1"
    assert st["gig_id"] == gid and st["thread_file"].endswith("thread.txt")
    board.followup(gid, "me", "and?")
    assert board.answer_state(gid)["state"] == "waiting"
    other = board.post("Q2", asker="me")
    board.close(other, "me")
    assert board.answer_state(other)["state"] == "closed"


def test_closed_gig_with_unanswered_follow_up_is_not_answered(board, agents):
    # Closing right after an answer still delivers that answer ...
    done = board.post("Q", "text", asker="me")
    board.answer(done, "alice-helper", "A1")
    board.close(done, "me")
    assert board.answer_state(done)["state"] == "answered"
    # ... but a follow-up nobody answered before the close must not report the old answer as its reply.
    gid = board.post("Q2", "text", asker="me")
    board.answer(gid, "alice-helper", "A1")
    board.followup(gid, "me", "and what about Postgres?")
    board.close(gid, "me")
    st = board.answer_state(gid)
    assert st["state"] == "closed" and st["count"] == 1


# ------------------------------------------------------------------ wait (library)
def test_wait_for_answer_notes_at_most_once_a_minute(board):
    gid = board.post("Slow", asker="me")
    ft, notes = FakeTime(), []
    res = wait_for_answer(board, gid, timeout_s=150, poll_s=3, sleep=ft.sleep, monotonic=ft.monotonic, err=notes.append)
    assert res["state"] == "timeout"
    assert len(notes) == 2 and all(gid in n for n in notes)
    assert sum(ft.sleeps) == pytest.approx(150)             # the last sleep is shortened to the deadline


def test_wait_for_answer_unknown_gig_raises(board):
    with pytest.raises(NotFound):
        wait_for_answer(board, "2026-01-01-nope-0000", timeout_s=1, sleep=lambda s: None)


def test_wait_for_answer_survives_a_board_read_error(board, agents, monkeypatch):
    gid = board.post("Flaky", asker="me")
    real, calls = board.answer_state, []

    def flaky(g):
        calls.append(g)
        if len(calls) == 1:
            raise OSError("share unreachable")
        return real(g)

    monkeypatch.setattr(board, "answer_state", flaky)
    board.answer(gid, "alice-helper", "ok")
    res = wait_for_answer(board, gid, timeout_s=0, poll_s=0.01, sleep=lambda s: None)
    assert res["state"] == "answered" and len(calls) == 2


# ------------------------------------------------------------------ wait (CLI)
def test_wait_returns_answer_posted_mid_wait(board, agents, capsys):
    gid = board.post("Need a regex", "Match ISO dates.", asker="me")
    answered = threading.Event()

    def answer_later():
        time.sleep(0.2)
        board.answer(gid, "alice-helper", "Use the ISO pattern")
        answered.set()

    t = threading.Thread(target=answer_later)
    t.start()
    code, out, err = run(capsys, "wait", gid, "--root", board.root, "--poll", "0.02", "--timeout", "20")
    t.join()
    assert answered.is_set()
    assert code == 0 and out == "Use the ISO pattern\n" and err == ""


def test_wait_json_has_answer_and_thread_path(board, agents, capsys):
    gid = board.post("Q", asker="me")
    board.answer(gid, "alice-helper", "42")
    code, out, _ = run(capsys, "wait", gid, "--root", board.root, "--json")
    data = json.loads(out)
    assert code == 0 and data["gig_id"] == gid and data["answer"]["text"] == "42"
    assert data["answer"]["agent_id"] == "alice-helper" and data["thread_file"].endswith("thread.txt")


def test_wait_timeout_exits_2_with_empty_stdout(board, capsys):
    gid = board.post("Nobody home", asker="me")
    code, out, err = run(capsys, "wait", gid, "--root", board.root, "--timeout", "0.2", "--poll", "0.05")
    assert code == 2 and out == "" and "timed out" in err and gid in err
    code, out, _ = run(capsys, "wait", gid, "--root", board.root, "--timeout", "0.1", "--poll", "0.05", "--json")
    assert code == 2 and json.loads(out)["timed_out"] is True


def test_wait_closed_exits_3(board, capsys):
    gid = board.post("Never mind", asker="me")
    board.close(gid, "me")
    code, out, err = run(capsys, "wait", gid, "--root", board.root, "--poll", "0.05")
    assert code == 3 and out == "" and "closed" in err


def test_wait_unknown_gig_exits_1_and_follow_up_waits_for_next_answer(board, agents, capsys):
    code, _, err = run(capsys, "wait", "2026-01-01-nope-0000", "--root", board.root)
    assert code == 1 and "hint:" in err
    gid = board.post("Q", asker="me")
    board.answer(gid, "alice-helper", "first")
    assert run(capsys, "wait", gid, "--root", board.root)[1] == "first\n"
    board.followup(gid, "me", "more?")
    code, out, _ = run(capsys, "wait", gid, "--root", board.root, "--timeout", "0.1", "--poll", "0.05")
    assert code == 2 and out == ""


# ------------------------------------------------------------------ watch
def test_watch_prints_answer_once_and_seen_after_restart(board, agents):
    gid = board.post("Mine", asker="me")
    w, out, err = watcher(board)
    w.start()
    w.step()
    assert out == []                                          # nothing answered yet
    board.answer(gid, "alice-helper", "done")
    w.step()
    w.step()
    assert out == [f"ANSWER {gid} {board.read(gid)['thread_file']}"]
    assert state_of(board)["gigs"][gid]["answers"] == 1

    w2, out2, _ = watcher(board)                              # restart
    w2.start()
    w2.step()
    w2.step()
    assert out2 == [f"SEEN {gid}"]

    board.followup(gid, "me", "and another?")
    w2.step()
    assert len(out2) == 1                                     # open again: no event
    board.answer(gid, "alice-helper", "second")
    w2.step()
    assert len(out2) == 2 and out2[-1].startswith(f"ANSWER {gid} ")
    board.say(gid, "alice-helper", "and a third")             # a chat message is a new answer
    w2.step()
    assert len(out2) == 3 and out2[-1].startswith(f"ANSWER {gid} ")
    assert err == []


def test_watch_answer_that_arrived_while_down_is_emitted_not_seen(board, agents):
    gid = board.post("Mine", asker="me")
    w, out, _ = watcher(board)
    w.start()
    w.step()
    board.answer(gid, "alice-helper", "while you were away")
    w2, out2, _ = watcher(board)
    w2.start()
    w2.step()
    assert len(out2) == 1 and out2[0].startswith(f"ANSWER {gid} ")


def test_watch_ignores_other_askers(board, agents):
    mine = board.post("Mine", asker="me")
    theirs = board.post("Theirs", asker="someone-else")
    board.answer(mine, "alice-helper", "a")
    board.answer(theirs, "alice-helper", "b")
    board.close(theirs, "someone-else")
    w, out, _ = watcher(board)
    w.start()
    w.step()
    assert len(out) == 1 and mine in out[0] and theirs not in " ".join(out)
    assert theirs not in state_of(board)["gigs"]
    w3, out3, _ = watcher(board, "someone-else")
    w3.start()
    w3.step()
    assert [ln.split()[0] for ln in out3] == ["ANSWER", "CLOSED"] and theirs in out3[0]


def test_watch_closed_once_then_seen(board, agents):
    gid = board.post("Mine", asker="Me")                       # asker match ignores case
    w, out, _ = watcher(board)
    w.start()
    w.step()
    board.close(gid, "me", 5)
    w.step()
    w.step()
    assert out == [f"CLOSED {gid}"]
    w2, out2, _ = watcher(board)
    w2.start()
    w2.step()
    assert out2 == [f"SEEN {gid}"]


def test_watch_stale_when_a_worker_gives_up(board, agents, clock):
    gid = board.post("Slow", asker="me")
    board.claim(gid, "alice-helper")
    w, out, _ = watcher(board)
    w.start()
    w.step()
    assert out == []
    clock.advance(3700)                                       # past the 60 minute limit
    w.step()
    w.step()
    assert out == [f"STALE {gid}"]
    w2, out2, _ = watcher(board)
    w2.start()
    w2.step()
    assert out2 == [f"SEEN {gid}"]
    board.claim(gid, "bob-worker")                            # another worker takes it, later gives up too
    w2.step()
    clock.advance(3700)
    w2.step()
    assert out2 == [f"SEEN {gid}", f"STALE {gid}"]


def test_watch_corrupt_state_file_warns_and_starts_fresh(board, agents):
    gid = board.post("Mine", asker="me")
    board.answer(gid, "alice-helper", "a")
    (board.status_dir / "me.asker.json").write_text("{not json", encoding="utf-8")
    w, out, err = watcher(board)
    w.start()
    w.step()
    assert len(out) == 1 and out[0].startswith("ANSWER ") and any("starting without memory" in e for e in err)


def test_watch_run_once_and_poll_loop(board, agents):
    gid = board.post("Mine", asker="me")
    board.answer(gid, "alice-helper", "a")
    out, sleeps = [], []
    w = AskerWatch(board, "me", out=out.append, err=lambda s: None, poll_s=7, sleep=sleeps.append)
    w.run(once=True)
    assert len(out) == 1 and sleeps == []

    def stop(s):
        sleeps.append(s)
        raise KeyboardInterrupt

    w = AskerWatch(board, "me", out=out.append, err=lambda s: None, poll_s=7, sleep=stop)
    w.run()
    assert sleeps == [7] and out[-1] == f"SEEN {gid}"


def test_watch_cli_once_prints_answer_then_seen(board, agents, capsys):
    gid = board.post("Mine", asker="me")
    board.answer(gid, "alice-helper", "a")
    code, out, err = run(capsys, "watch", "--root", board.root, "--asker", "me", "--once")
    assert code == 0 and len(out.splitlines()) == 1 and out.startswith(f"ANSWER {gid} ")
    assert "watching" in err
    code, out, _ = run(capsys, "watch", "--root", board.root, "--asker", "me", "--once")
    assert out.splitlines() == [f"SEEN {gid}"]
    code, out, err = run(capsys, "watch", "--root", board.root, "--asker", "  ", "--once")
    assert code == 1 and "hint:" in err
