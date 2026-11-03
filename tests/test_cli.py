"""The command line, driven through main(argv)."""
import json

import pytest

from sidegig.cli import main


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


@pytest.fixture
def root(tmp_path, capsys):
    path = tmp_path / "cli-board"
    code, out, _ = run(capsys, "init", path, "--name", "cli board", "--rules", "Be kind.")
    assert code == 0 and "initialised" in out
    return path


def test_init_twice_reports_existing(root, capsys):
    code, out, _ = run(capsys, "init", root, "--name", "cli board")
    assert code == 0 and "already exists" in out
    assert (root / "board.json").is_file() and (root / "BOARD_GUIDE.txt").is_file()


def test_register_post_list_read_answer_flow(root, capsys, tmp_path):
    code, out, _ = run(capsys, "register", "--root", root, "--name", "Bob Worker", "--skills", "sql, python")
    assert code == 0 and out.strip() == "bob-worker"
    code, out, _ = run(capsys, "agents", "--root", root)
    assert "bob-worker" in out and "sql,python" in out

    code, out, _ = run(capsys, "post", "--root", root, "--title", "Pivot?", "--text", "How?", "--tags", "sql", "--asker", "me")
    gid = out.strip()
    assert code == 0 and gid.startswith("20")

    code, out, _ = run(capsys, "list", "--root", root, "--status", "open")
    assert gid in out and "Pivot?" in out
    code, out, _ = run(capsys, "list", "--root", root, "--json")
    assert json.loads(out)[0]["id"] == gid

    code, out, _ = run(capsys, "read", "--root", root, gid)
    assert "How?" in out

    answer = tmp_path / "answer.txt"
    answer.write_text("Use CASE.\n", encoding="utf-8")
    code, out, _ = run(capsys, "answer", "--root", root, gid, "--agent", "bob-worker", "--file", answer)
    assert code == 0
    code, out, _ = run(capsys, "read", "--root", root, gid)
    assert "ANSWER from Bob Worker" in out and "Use CASE." in out
    code, out, _ = run(capsys, "list", "--root", root, "--status", "answered")
    assert gid in out

    assert run(capsys, "followup", "--root", root, gid, "--author", "me", "--text", "More?")[0] == 0
    assert run(capsys, "close", "--root", root, gid, "--rating", "4", "--note", "ok")[0] == 0
    code, out, _ = run(capsys, "list", "--root", root, "--status", "closed")
    assert gid in out


def test_root_from_environment(root, capsys, monkeypatch):
    monkeypatch.setenv("SIDEGIG_ROOT", str(root))
    code, out, _ = run(capsys, "post", "--title", "From env")
    assert code == 0 and out.strip()


def test_root_from_deprecated_environment_name(root, capsys, monkeypatch):
    monkeypatch.setenv("GIGBOARD_ROOT", str(root))  # pre-rename name, accepted silently
    code, out, err = run(capsys, "post", "--title", "From old env")
    assert code == 0 and out.strip() and err == ""


def test_new_environment_name_wins_over_deprecated(root, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GIGBOARD_ROOT", str(tmp_path / "nowhere"))
    monkeypatch.setenv("SIDEGIG_ROOT", str(root))
    code, out, _ = run(capsys, "post", "--title", "New env wins")
    assert code == 0 and out.strip()


def test_errors_exit_one_with_hint(root, capsys):
    code, out, err = run(capsys, "read", "--root", root, "2026-01-01-nope-0000")
    assert code == 1 and out == "" and err.startswith("error:") and "hint:" in err
    code, _, err = run(capsys, "list")
    assert code == 1 and "SIDEGIG_ROOT" in err
    code, _, err = run(capsys, "answer", "--root", root, "x", "--agent", "a")
    assert code == 1 and "no answer given" in err


def test_post_text_from_utf16_file(root, capsys, tmp_path):
    f = tmp_path / "q.txt"
    f.write_text("café question", encoding="utf-16")
    code, out, _ = run(capsys, "post", "--root", root, "--title", "Accents", "--file", f)
    assert code == 0
    code, out, _ = run(capsys, "read", "--root", root, out.strip())
    assert "café question" in out


def test_worker_once_registers_and_prints_new(root, capsys):
    code, out, _ = run(capsys, "post", "--root", root, "--title", "For the worker", "--to", "helper-bot")
    assert code == 1                                         # the agent does not exist yet
    run(capsys, "register", "--root", root, "--name", "helper-bot", "--skills", "python")
    code, out, _ = run(capsys, "post", "--root", root, "--title", "For the worker", "--to", "helper-bot")
    gid = out.strip()
    code, out, err = run(capsys, "worker", "--root", root, "--agent", "helper-bot", "--once")
    assert code == 0
    lines = out.splitlines()
    assert len(lines) == 1 and lines[0].startswith(f"NEW {gid} ")
    assert "online" in err
    code, out, _ = run(capsys, "worker", "--root", root, "--agent", "helper-bot", "--once")
    assert out.splitlines()[0].startswith(f"RESUME {gid} ")


def test_worker_registers_unknown_agent_and_rejects_bad_id(root, capsys):
    code, out, err = run(capsys, "worker", "--root", root, "--agent", "new-bot", "--skills", "go", "--once")
    assert code == 0
    code, out, _ = run(capsys, "agents", "--root", root, "--json")
    assert [a["id"] for a in json.loads(out)] == ["new-bot"]
    code, _, err = run(capsys, "worker", "--root", root, "--agent", "Bad Id!", "--once")
    assert code == 1 and "bad agent id" in err
