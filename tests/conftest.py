import datetime as dt

import pytest

from sidegig import Board


class Clock:
    """A settable clock; calling it returns epoch seconds."""

    def __init__(self):
        self.t = dt.datetime(2026, 10, 29, 12, 0, 0).timestamp()

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    """No board or identity from the host environment (new SIDEGIG_* or deprecated GIGBOARD_* names)."""
    for name in ("ROOT", "AGENT_ID"):
        monkeypatch.delenv("SIDEGIG_" + name, raising=False)
        monkeypatch.delenv("GIGBOARD_" + name, raising=False)  # deprecated spelling, still honoured


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def board(tmp_path, clock):
    b = Board(tmp_path / "board", clock=clock)
    b.init("test board", rules="Be kind.")
    return b


@pytest.fixture
def agents(board):
    """Two registered agents: ids alice-helper and bob-worker."""
    board.register("Alice Helper", skills=["python"])
    board.register("Bob Worker", skills=["sql"])
    return ("alice-helper", "bob-worker")
