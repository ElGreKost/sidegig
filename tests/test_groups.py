"""Group addressing (dept:..., person:...) and the configurable claim lease, on the board library."""
import json

import pytest

from sidegig import Board
from sidegig.errors import Conflict, Invalid
from sidegig.members import MemberStore
from sidegig.worker import Worker


@pytest.fixture
def gboard(tmp_path, clock):
    members = tmp_path / "state" / "members.json"
    members.parent.mkdir()
    members.write_text(json.dumps({"dept:marketing": ["kostis-marketing", "giannis-marketing"],
                                   "person:giannis": ["giannis", "giannis-marketing"]}), "utf-8")
    store = MemberStore(members)
    b = Board(tmp_path / "board", clock=clock, members=store.groups)
    b.members_problem = lambda: store.problem
    b.init("groups")
    for aid in ("kostis", "kostis-marketing", "giannis-marketing", "giannis", "dev"):
        b.ensure_agent(aid)
    b.store = store
    return b


def test_member_claims_and_non_member_is_refused(gboard):
    gid = gboard.post("Launch copy", "three lines", asker="kostis", to_agent="dept:marketing")
    assert gboard.read(gid)["meta"]["to_agent"] == "dept:marketing"
    with pytest.raises(Conflict, match="not a member"):
        gboard.claim(gid, "dev")
    got = gboard.claim(gid, "giannis-marketing")
    assert got["meta"]["claimed_by"] == "giannis-marketing"
    gboard.answer(gid, "giannis-marketing", "Line one.")
    gboard.say(gid, "giannis-marketing", "A variant.")          # the member that answered may go on
    with pytest.raises(Conflict):
        gboard.say(gid, "kostis-marketing", "me too")           # another member did not answer


def test_unknown_group_and_group_without_members_file(gboard, tmp_path):
    with pytest.raises(Invalid, match="unknown group 'dept:sales'") as exc:
        gboard.post("x", asker="kostis", to_agent="dept:sales")
    assert "dept:marketing" in exc.value.hint and "members.json" in exc.value.hint
    plain = Board(gboard.root)                                    # file mode: no groups at all
    with pytest.raises(Invalid, match="has no groups"):
        plain.post("x", to_agent="dept:marketing")


def test_group_names_are_case_insensitive_and_direct_addressing_still_works(gboard):
    gid = gboard.post("x", asker="kostis", to_agent="Dept:Marketing")
    assert gboard.read(gid)["meta"]["to_agent"] == "dept:marketing"
    direct = gboard.post("y", asker="kostis", to_agent="dev")
    with pytest.raises(Conflict, match="addressed to dev"):
        gboard.claim(direct, "giannis")
    gboard.claim(direct, "dev")


def test_for_agent_listing_includes_my_groups(gboard):
    g1 = gboard.post("to marketing", asker="kostis", to_agent="dept:marketing")
    g2 = gboard.post("to giannis the person", asker="kostis", to_agent="person:giannis")
    g3 = gboard.post("to dev", asker="kostis", to_agent="dev")
    g4 = gboard.post("to anyone", asker="kostis")
    ids = {g["id"] for g in gboard.gigs(for_agent="giannis-marketing")}
    assert ids == {g1, g2, g4} and g3 not in ids
    assert gboard.groups_of("giannis-marketing") == ["dept:marketing", "person:giannis"]
    assert sorted(gboard.recipients("dept:marketing")) == ["giannis-marketing", "kostis-marketing"]


def test_members_file_reloads_and_a_broken_file_means_no_groups(gboard):
    path = gboard.store.path
    path.write_text(json.dumps({"groups": {"dept:sales": ["dev"]}}), "utf-8")   # wrapped form, new content
    gid = gboard.post("lead list", asker="kostis", to_agent="dept:sales")
    gboard.claim(gid, "dev")
    path.write_text("{not json", "utf-8")
    with pytest.raises(Invalid, match="unknown group") as exc:
        gboard.post("x", asker="kostis", to_agent="dept:sales")
    assert "unreadable" in exc.value.hint
    (gboard.gigs_dir / gid / "claim.lock").unlink()                # the gig is unclaimed again
    with pytest.raises(Conflict, match="not a member"):           # fail closed for existing gigs too
        gboard.claim(gid, "dev")


def test_a_worker_takes_group_gigs_for_its_groups_only(gboard):
    mine = gboard.post("for marketing", asker="kostis", to_agent="dept:marketing")
    gboard.post("for dev", asker="kostis", to_agent="dev")
    out = []
    w = Worker(gboard, "kostis-marketing", out=out.append, err=lambda s: None, sleep=lambda s: None, settle_s=0)
    w.start()
    w.step()
    assert len(out) == 1 and out[0].startswith(f"NEW {mine} ")


def test_the_lease_is_configurable(tmp_path, clock):
    b = Board(tmp_path / "b", clock=clock, give_up_s=4 * 3600)
    b.init("lease")
    b.ensure_agent("worker")
    gid = b.post("long job", "a day of work")
    b.claim(gid, "worker")
    for _ in range(20):                                           # 3 h 20 of heartbeats: still held
        clock.advance(600)
        b.heartbeat(gid, "worker")
    assert b.read(gid)["meta"]["status"] == "claimed"
    clock.advance(41 * 60)                                        # past 4 h since the claim
    assert b.read(gid)["meta"]["status"] == "stale"
    assert "No answer after 240 minutes" in b.read(gid)["thread"]
    assert b.limits() == {"claim_stale_minutes": 10, "give_up_minutes": 240}
