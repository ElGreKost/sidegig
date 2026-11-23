"""Teams: one server, many teams, nothing crosses between them. Isolation of reads, writes, lists and
streams; team admin and server admin boundaries; revocation and expiry; the token store at rest; the
migration from a single-board server; and the team commands of the CLI."""
import asyncio
import hashlib
import json
import os
import socket
import stat
import threading
import time

import httpx
import pytest

from conftest import Clock
from sidegig import Board
from sidegig.cli import main as cli_main
from sidegig.client import RemoteBoard
from sidegig.errors import Invalid, Refused
from sidegig.remote import EventBus, build_remote_app
from sidegig.teams import TeamStore, create_team, migrate
from sidegig.tokens import ADMIN_PREFIX, PREFIX, TokenStore, hash_token, load_admin_hash, new_token

HDRS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
ADMIN_CRED = new_token(ADMIN_PREFIX)


@pytest.fixture
def teams(tmp_path):
    """Two teams on one server. Both have an agent called carol; each has a team admin."""
    state = tmp_path / "state"
    toks = {}
    toks["acme-boss"], _ = create_team(state, "acme", "Acme Inc", "boss")
    toks["globex-chief"], _ = create_team(state, "globex", "Globex", "chief")
    store = TokenStore(state / "tokens.json")
    for team, name in (("acme", "carol"), ("acme", "dev"), ("globex", "carol"), ("globex", "eve")):
        toks[f"{team}-{name}"], _ = store.issue(team, name)
    return {"state": state, "store": store, "tokens": toks, "tmp": tmp_path}


def run(t, scenario, **kw):
    """Build the app and hand scenario helpers: call (via /v1/call), mcp (via /mcp), req (any request)."""
    kw.setdefault("sweep_s", 0)
    app = build_remote_app(t["state"], poll_s=0.05, **kw)

    async def main():
        async with app.lifespan():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
                def auth(who):
                    tok = t["tokens"].get(who, who)
                    return {"Authorization": f"Bearer {tok}"} if tok else {}

                async def req(method, path, who=None, **kw2):
                    return await c.request(method, path, headers={**kw2.pop("headers", {}), **auth(who)}, **kw2)

                async def call(who, tool, **args):
                    return (await req("POST", f"/v1/call/{tool}", who, json=args)).json()

                async def mcp(who, tool, **args):
                    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                       "params": {"name": tool, "arguments": args}})
                    r = await req("POST", "/mcp", who, content=body, headers=HDRS)
                    assert r.status_code == 200, r.text
                    data = [ln[6:] for ln in r.text.splitlines() if ln.startswith("data: ")]
                    msg = json.loads(data[-1] if data else r.text)
                    return json.loads(msg["result"]["content"][0]["text"])

                async def events(who, after=0):
                    return (await req("GET", "/v1/events", who, params={"after": after, "wait": 0})).json()
                return await scenario(call=call, mcp=mcp, req=req, events=events, app=app)
    return asyncio.run(main())


def _snapshot(*folders):
    """Every file under folders: content hash and mode, to prove that nothing changed."""
    out = {}
    for folder in folders:
        for dirpath, _dirs, files in os.walk(folder):
            for f in files:
                p = os.path.join(dirpath, f)
                with open(p, "rb") as fh:
                    out[p] = (hashlib.sha256(fh.read()).hexdigest(), stat.S_IMODE(os.stat(p).st_mode))
    return out


# ---------------------------------------------------------------------- isolation
def test_team_b_never_sees_team_a_gigs_agents_counts_or_events(teams):
    async def sc(call, mcp, req, events, app):
        gid = (await call("acme-carol", "gig_post", title="Acme launch plan", text="Q3 numbers",
                          tags=["python"]))["gig_id"]
        await call("acme-dev", "agent_register", name="Dev", skills=["python"])
        assert (await events("acme-dev"))["events"][0]["gig_id"] == gid       # the own team sees it
        for via in (call, mcp):                                                # both doors: /v1/call and /mcp
            listed = await via("globex-eve", "gig_list")
            assert gid not in json.dumps(listed) and "Acme" not in json.dumps(listed)
            agents = await via("globex-eve", "agent_list")
            assert {a["id"] for a in agents["agents"]} <= {"carol", "eve", "chief"}
            info = await via("globex-eve", "board_info")
            assert info["name"] == "Globex" and "Acme" not in json.dumps(info)
            assert sum(info["gigs"].values()) == 0 and info["agents"] <= 3 and "root" not in info
            mine = await via("globex-carol", "inbox")                          # same name, other team
            assert mine["agent"] == "carol" and gid not in json.dumps(mine)
        scan = (await req("GET", "/v1/scan", "globex-eve")).json()
        assert scan["ok"] and scan["gigs"] == []
        assert (await events("globex-eve"))["events"] == []
        assert (await events("globex-carol"))["events"] == []
        me = (await req("GET", "/v1/me", "globex-carol")).json()
        assert me["team"] == "globex" and me["team_name"] == "Globex" and "acme" not in json.dumps(me).lower()
        assert app.team("acme").board.read(gid)["meta"]["status"] == "open"
        assert not (app.team("globex").board.root / "gigs" / gid).exists()
    run(teams, sc)


def test_a_gig_id_from_another_team_answers_exactly_like_an_unknown_id(teams):
    async def sc(call, mcp, req, events, app):
        gid = (await call("acme-carol", "gig_post", title="Private task"))["gig_id"]
        thread = app.team("acme").board.read(gid)["thread"]
        unknown = gid[:-4] + ("0000" if not gid.endswith("0000") else "1111")
        for tool, args in (("gig_read", {}), ("gig_claim", {}), ("gig_heartbeat", {}),
                           ("gig_answer", {"text": "x"}), ("gig_say", {"text": "x"}),
                           ("gig_followup", {"text": "x"}), ("gig_close", {"rating": 5}),
                           ("gig_wait", {"timeout_s": 1})):
            for via in (call, mcp):
                cross = await via("globex-carol", tool, gig_id=gid, **args)
                none = await via("globex-carol", tool, gig_id=unknown, **args)
                assert cross["ok"] is False and none["ok"] is False
                assert json.dumps(cross).replace(gid, "ID") == json.dumps(none).replace(unknown, "ID"), tool
        meta = app.team("acme").board.read(gid)["meta"]                       # untouched
        assert meta["status"] == "open" and meta["claimed_by"] is None
        assert app.team("acme").board.read(gid)["thread"] == thread
    run(teams, sc)


def test_a_request_cannot_name_another_team_or_speak_for_another_agent(teams):
    async def sc(call, mcp, req, events, app):
        extra = await call("globex-eve", "gig_post", title="Hello", team="acme")   # unknown arguments are dropped
        assert not extra.get("ok", True) or app.team("globex").board.read(extra["gig_id"])["meta"]["asker"] == "eve"
        steal = await call("globex-eve", "gig_post", title="Hello", asker="carol")
        assert steal["ok"] is False and "speaks for eve" in steal["error"]
        posted = await call("globex-eve", "gig_post", title="Hello again", to_agent="dev")
        assert posted["ok"] is False and "acme" not in json.dumps(posted).lower()  # dev exists only in acme
        hdr = await req("POST", "/v1/call/board_info", "globex-eve", json={}, headers={"X-Sidegig-Team": "acme"})
        assert hdr.json()["name"] == "Globex"
        assert app.team("acme").board.scan() == []
    run(teams, sc)


def test_odd_path_rule():
    from sidegig.remote import _odd_path
    for path in ("/v1//scan", "/v1/./me", "/v1/team/../admin/teams", "/v1/me/..", "/v1/me/.", "/v1\\me"):
        assert _odd_path(path), path
    for path in ("/v1/me", "/mcp", "/v1/call/gig_list", "/v1/admin/teams/acme/tokens", "/v1/a.b"):
        assert not _odd_path(path), path


def test_rate_limits_are_per_token_and_never_shared_across_teams(teams):
    async def sc(call, mcp, req, events, app):
        codes = [(await req("GET", "/v1/me", "acme-carol")).status_code for _ in range(8)]
        assert codes[0] == 200 and 429 in codes
        assert (await req("GET", "/v1/me", "globex-carol")).status_code == 200    # same name, own bucket
    run(teams, sc, rate_per_min=3, burst=3)


def test_the_audit_log_names_team_and_token_id_but_never_a_token(teams):
    async def sc(call, mcp, req, events, app):
        await call("acme-carol", "gig_post", title="Audited", text="BODY TEXT")
        await req("GET", "/v1/me", "sgk_" + "x" * 43)
    run(teams, sc)
    text = (teams["state"] / "audit.jsonl").read_text()
    rows = [json.loads(ln) for ln in text.splitlines()]
    assert any(r.get("team") == "acme" and r.get("agent") == "carol" and r.get("token") for r in rows)
    assert "BODY TEXT" not in text and PREFIX not in text and ADMIN_PREFIX not in text
    assert all(tok not in text for tok in teams["tokens"].values())


# ---------------------------------------------------------------------- transport rules
def test_a_token_in_the_url_is_refused_before_anything_else(teams):
    async def sc(call, mcp, req, events, app):
        tok = teams["tokens"]["acme-carol"]
        for path in (f"/v1/scan?token={tok}", f"/v1/events?access_token={tok}&after=0", f"/mcp?t={tok}",
                     f"/v1/admin/teams?c={ADMIN_CRED}"):
            r = await req("GET", path)
            assert r.status_code == 400 and "Authorization header" in r.json()["error"]
            r = await req("GET", path, "acme-carol")
            assert r.status_code == 400
    run(teams, sc, admin_hash=hash_token(ADMIN_CRED))
    assert teams["tokens"]["acme-carol"] not in (teams["state"] / "audit.jsonl").read_text()


def test_odd_paths_are_refused(teams):
    async def sc(call, mcp, req, events, app):
        for path in ("/v1//scan", "/v1/team/%2e%2e/admin/teams", "/v1/%2e/me", "/v1/%5cme"):
            assert (await req("GET", path, "acme-carol")).status_code == 400, path
        assert (await req("GET", "/v1/me", "acme-carol")).status_code == 200
    run(teams, sc)


def test_missing_bad_and_admin_credentials_are_401_on_board_routes(teams):
    async def sc(call, mcp, req, events, app):
        for who in (None, "sgk_nope", ADMIN_CRED, "Bearer"):
            for method, path in (("GET", "/v1/me"), ("GET", "/v1/scan"), ("POST", "/v1/call/gig_list"),
                                 ("GET", "/v1/events")):
                assert (await req(method, path, who, json={} if method == "POST" else None)).status_code == 401
    run(teams, sc, admin_hash=hash_token(ADMIN_CRED))


# ---------------------------------------------------------------------- team admin
def test_a_team_admin_manages_only_its_own_team(teams):
    async def sc(call, mcp, req, events, app):
        listed = (await req("GET", "/v1/team/tokens", "acme-boss")).json()
        assert listed["team"] == "acme" and {r["team"] for r in listed["tokens"]} == {"acme"}
        assert {r["name"] for r in listed["tokens"]} == {"boss", "carol", "dev"}
        assert all("token" not in r and "hash" not in r for r in listed["tokens"])
        # a body naming another team is ignored: the token decides the team
        r = await req("POST", "/v1/team/tokens", "acme-boss", json={"name": "newbie", "team": "globex"})
        assert r.status_code == 200 and r.json()["team"] == "acme" and r.json()["token"].startswith(PREFIX)
        assert r.headers["cache-control"] == "no-store"
        teams["tokens"]["acme-newbie"] = r.json()["token"]
        assert (await call("acme-newbie", "board_info"))["name"] == "Acme Inc"
        # another team's token id answers like an unknown id
        globex_eve = next(x for x in teams["store"].records("globex") if x["name"] == "eve")
        cross = await req("POST", "/v1/team/tokens/revoke", "acme-boss", json={"id": globex_eve["id"]})
        unknown = await req("POST", "/v1/team/tokens/revoke", "acme-boss", json={"id": "0123456789ab"})
        assert cross.status_code == unknown.status_code == 404 and cross.json() == unknown.json()
        by_name = await req("POST", "/v1/team/tokens/revoke", "acme-boss", json={"name": "eve"})
        assert by_name.status_code == 404
        assert (await call("globex-eve", "board_info"))["name"] == "Globex"       # still works
        assert (await req("POST", "/v1/team/tokens/revoke", "acme-boss", json={"name": "carol"})).json()["revoked"] == 1
        assert (await req("GET", "/v1/me", "acme-carol")).status_code == 401
        assert (await req("GET", "/v1/me", "globex-carol")).status_code == 200    # the other carol is untouched
    run(teams, sc)


def test_roles_members_cannot_manage_tokens_and_admins_cannot_use_the_board(teams):
    async def sc(call, mcp, req, events, app):
        for path, method in (("/v1/team/tokens", "GET"), ("/v1/team/tokens", "POST"),
                             ("/v1/team/tokens/revoke", "POST")):
            r = await req(method, path, "acme-carol", json={"name": "x"} if method == "POST" else None)
            assert r.status_code == 403 and "team admin" in r.json()["error"]
        for method, path in (("POST", "/v1/call/gig_list"), ("GET", "/v1/scan"), ("GET", "/v1/events"),
                             ("POST", "/v1/status"), ("POST", "/mcp")):
            r = await req(method, path, "acme-boss", json={} if method == "POST" else None)
            assert r.status_code == 403 and "cannot use the board" in r.json()["error"]
        me = (await req("GET", "/v1/me", "acme-boss")).json()
        assert me["role"] == "admin" and me["team"] == "acme" and "groups" not in me
        assert (await req("GET", "/v1/admin/teams", "acme-boss")).status_code == 404    # API off
    run(teams, sc)


def test_the_last_admin_token_of_a_team_cannot_be_revoked_by_the_team(teams):
    async def sc(call, mcp, req, events, app):
        r = await req("POST", "/v1/team/tokens/revoke", "acme-boss", json={"name": "boss"})
        assert r.status_code == 409 and "without a live admin" in r.json()["error"]
        made = (await req("POST", "/v1/team/tokens", "acme-boss", json={"name": "deputy", "role": "admin"})).json()
        teams["tokens"]["acme-deputy"] = made["token"]
        assert (await req("POST", "/v1/team/tokens/revoke", "acme-deputy", json={"name": "boss"})).json()["revoked"] == 1
        assert (await req("GET", "/v1/team/tokens", "acme-boss")).status_code == 401
    run(teams, sc)


# ---------------------------------------------------------------------- server admin
def test_the_server_admin_api_is_off_without_a_credential(teams):
    async def sc(call, mcp, req, events, app):
        for who in (None, ADMIN_CRED, "acme-boss"):
            r = await req("GET", "/v1/admin/teams", who)
            assert r.status_code == 404
    run(teams, sc)


def test_the_server_admin_creates_teams_and_tokens_but_never_reads_a_board(teams):
    async def sc(call, mcp, req, events, app):
        for who in (None, "acme-boss", "acme-carol", ADMIN_PREFIX + "x" * 43):
            assert (await req("GET", "/v1/admin/teams", who)).status_code == 401
        listed = (await req("GET", "/v1/admin/teams", ADMIN_CRED)).json()["teams"]
        assert {(r["team"], r["members_live"], r["admins_live"]) for r in listed} == {("acme", 2, 1), ("globex", 2, 1)}
        assert "gigs" not in json.dumps(listed)
        made = await req("POST", "/v1/admin/teams", ADMIN_CRED, json={"team": "initech", "name": "Initech",
                                                                      "admin": "lumbergh"})
        body = made.json()
        assert made.status_code == 200 and made.headers["cache-control"] == "no-store"
        assert body["team"] == "initech" and body["admin"]["role"] == "admin" and body["token"].startswith(PREFIX)
        teams["tokens"]["initech-lumbergh"] = body["token"]
        assert (await req("GET", "/v1/me", "initech-lumbergh")).json()["team"] == "initech"
        dup = await req("POST", "/v1/admin/teams", ADMIN_CRED, json={"team": "acme", "admin": "x"})
        assert dup.status_code == 409
        bad = await req("POST", "/v1/admin/teams", ADMIN_CRED, json={"team": "../etc", "admin": "x"})
        assert bad.status_code == 400
        board = await req("POST", "/v1/admin/teams", ADMIN_CRED, json={"team": "evil", "admin": "x",
                                                                       "board": str(teams["tmp"])})
        assert board.status_code == 200 and (teams["state"] / "teams" / "evil" / "board" / "board.json").is_file()
        # tokens for a team, and revocation, by id or all
        issued = (await req("POST", "/v1/admin/teams/globex/tokens", ADMIN_CRED, json={"name": "chief2"})).json()
        assert issued["team"] == "globex" and issued["role"] == "admin"
        assert (await req("POST", "/v1/admin/teams/nope/tokens", ADMIN_CRED, json={"name": "x"})).status_code == 404
        r = await req("POST", "/v1/admin/teams/globex/revoke", ADMIN_CRED, json={"all": True})
        assert r.json()["revoked"] == 4
        assert (await req("GET", "/v1/me", "globex-chief")).status_code == 401
        assert (await req("GET", "/v1/me", "acme-boss")).status_code == 200
        # the credential is not a board token
        assert (await req("POST", "/v1/call/gig_list", ADMIN_CRED, json={})).status_code == 401
        assert (await req("POST", "/mcp", ADMIN_CRED, content="{}", headers=HDRS)).status_code == 401
    run(teams, sc, admin_hash=hash_token(ADMIN_CRED))
    audit = (teams["state"] / "admin-audit.jsonl").read_text()
    rows = [json.loads(ln) for ln in audit.splitlines()]
    assert any(r["action"] == "team.create" and r["team"] == "initech" and r["actor"] == "server-admin" for r in rows)
    assert any(r["action"] == "token.revoke" and r["team"] == "globex" and r.get("revoked") == 4 for r in rows)
    assert PREFIX not in audit and ADMIN_PREFIX not in audit
    assert stat.S_IMODE(os.stat(teams["state"] / "admin-audit.jsonl").st_mode) == 0o600


def test_a_removed_team_takes_its_tokens_with_it(teams):
    async def sc(call, mcp, req, events, app):
        assert (await req("GET", "/v1/me", "globex-eve")).status_code == 200
        path = teams["state"] / "teams.json"
        data = json.loads(path.read_text())
        del data["teams"]["globex"]
        path.write_text(json.dumps(data))
        assert (await req("GET", "/v1/me", "globex-eve")).status_code == 401
        assert (await req("GET", "/v1/me", "acme-carol")).status_code == 200
    run(teams, sc)


def test_two_teams_can_never_share_a_board_folder(teams):
    state = teams["state"]
    acme_board = TeamStore(state).board_path("acme")
    for board in (acme_board, acme_board / "gigs", state / "teams" / "globex"):
        with pytest.raises(Exception, match="overlaps the folders of team"):
            create_team(state, "copycat", "Copycat", "cat", board=board)
    assert TeamStore(state).get("copycat") is None
    # a hand-edited teams.json that points two teams at one board: both are refused, not merged
    path = state / "teams.json"
    data = json.loads(path.read_text())
    data["teams"]["globex"]["board"] = os.path.relpath(acme_board, state)
    path.write_text(json.dumps(data))

    async def sc(call, mcp, req, events, app):
        for who in ("acme-carol", "globex-carol"):
            assert (await req("POST", "/v1/call/gig_list", who, json={})).status_code == 503
    run(teams, sc)


def test_admin_credential_loading(tmp_path):
    cred = tmp_path / "admin.cred"
    cred.write_text(ADMIN_CRED + "\n")
    os.chmod(cred, 0o640)
    with pytest.raises(Invalid, match="can be read by other users"):
        load_admin_hash(str(cred), environ={})
    os.chmod(cred, 0o600)
    env = {"SIDEGIG_ADMIN_TOKEN": "ignored-when-a-file-is-given-" + "x" * 10}
    assert load_admin_hash(str(cred), environ=env) == hash_token(ADMIN_CRED) and env == {}
    env = {"SIDEGIG_ADMIN_TOKEN": ADMIN_CRED}
    assert load_admin_hash(None, environ=env) == hash_token(ADMIN_CRED) and env == {}   # removed from the env
    assert load_admin_hash(None, environ={}) is None
    with pytest.raises(Invalid, match="at least 32"):
        load_admin_hash(None, environ={"SIDEGIG_ADMIN_TOKEN": "short"})


# ---------------------------------------------------------------------- revocation, expiry, storage
def test_revoked_tokens_stop_at_the_next_request(teams):
    async def sc(call, mcp, req, events, app):
        assert (await call("acme-dev", "board_info"))["name"] == "Acme Inc"
        teams["store"].revoke("acme", name="dev")
        assert (await req("POST", "/v1/call/board_info", "acme-dev", json={})).status_code == 401
        assert (await req("GET", "/v1/events", "acme-dev")).status_code == 401
    run(teams, sc)
    with pytest.raises(Exception, match="no live token matched"):
        teams["store"].revoke("acme", name="dev")                              # already revoked


def test_expired_tokens_are_refused(teams):
    clock = Clock()
    store = TokenStore(teams["state"] / "tokens.json", clock)
    teams["tokens"]["acme-temp"], rec = store.issue("acme", "temp", expires_days=1)
    assert rec["expires"] and rec["status"] == "live"

    async def sc(call, mcp, req, events, app):
        assert (await req("GET", "/v1/me", "acme-temp")).status_code == 200
        clock.advance(86400 + 1)
        assert (await req("GET", "/v1/me", "acme-temp")).status_code == 401
        assert (await req("GET", "/v1/me", "acme-carol")).status_code == 200    # no expiry
    run(teams, sc, clock=clock)
    temp = next(r for r in store.records("acme") if r["name"] == "temp")
    assert temp["status"].startswith("expired")
    with pytest.raises(Invalid):
        store.issue("acme", "x", expires_days=0)


def test_tokens_are_random_and_stored_only_as_hashes(teams):
    toks = list(teams["tokens"].values())
    assert len(set(toks)) == len(toks)
    assert all(t.startswith(PREFIX) and len(t) - len(PREFIX) >= 43 for t in toks)   # 32 random bytes
    raw = (teams["state"] / "tokens.json").read_text()
    assert all(t not in raw for t in toks) and PREFIX not in raw
    data = json.loads(raw)
    assert data["format"] == 2 and set(data["tokens"]) == {hash_token(t) for t in toks}
    for rec in data["tokens"].values():
        assert set(rec) >= {"id", "team", "name", "role", "created", "expires", "revoked"} and "hint" not in rec
    for name in ("tokens.json", "teams.json"):
        assert stat.S_IMODE(os.stat(teams["state"] / name).st_mode) == 0o600
    for d in ("", "teams/acme", "teams/globex"):
        assert stat.S_IMODE(os.stat(teams["state"] / d).st_mode) == 0o700


# ---------------------------------------------------------------------- migration
@pytest.fixture
def legacy(tmp_path):
    """A state folder as a single-board server left it: format-1 tokens, members.json, events.jsonl."""
    root, state = tmp_path / "board", tmp_path / "state"
    Board(root).init("Old board")
    Board(root).ensure_agent("carol", name="carol")
    gid = Board(root).post("Older gig", "from before teams", asker="carol")
    state.mkdir(mode=0o700)
    live, gone = new_token(), new_token()
    recs = {hash_token(live): {"agent": "dev", "label": "laptop", "hint": live[:10],
                               "created": "2026-11-01T10:00:00Z", "revoked": None},
            hash_token(gone): {"agent": "old", "label": "", "hint": gone[:10],
                               "created": "2026-11-01T10:00:00Z", "revoked": "2026-11-02T10:00:00Z"}}
    (state / "tokens.json").write_text(json.dumps({"format": 1, "tokens": recs}))
    os.chmod(state / "tokens.json", 0o600)
    (state / "members.json").write_text(json.dumps({"dept:eng": ["dev"]}))
    bus = EventBus(state / "events.jsonl", keep=100)
    bus.publish({"type": "posted", "gig_id": gid, "actor": "carol", "to": ["*"], "title": "Older gig"})
    bus.publish({"type": "posted", "gig_id": gid, "actor": "carol", "to": ["dev"], "title": "Older gig"})
    return {"root": root, "state": state, "live": live, "gone": gone, "boot": bus.boot, "seq": bus.seq,
            "gid": gid, "tmp": tmp_path}


def test_a_server_with_single_board_data_refuses_to_start(legacy):
    with pytest.raises(ValueError, match="migrate-teams"):
        build_remote_app(legacy["state"], board=legacy["root"])
    with pytest.raises(ValueError, match="migrate-teams"):
        build_remote_app(legacy["state"])


def test_migration_dry_run_changes_nothing(legacy):
    before = _snapshot(legacy["state"], legacy["root"])
    steps = migrate(legacy["state"], legacy["root"], dry_run=True)
    assert [(s["step"], s["result"]) for s in steps] == [("team", "would do"), ("tokens", "would do"),
                                                         ("members.json", "would do"), ("events.jsonl", "would do")]
    assert _snapshot(legacy["state"], legacy["root"]) == before


def test_migration_moves_everything_into_one_team_and_is_idempotent(legacy):
    steps = migrate(legacy["state"], legacy["root"])
    assert {s["result"] for s in steps} == {"done"}
    state = legacy["state"]
    team = TeamStore(state).get("default")
    assert team["name"] == "Old board" and (state / team["board"]).resolve() == legacy["root"].resolve()
    assert not os.path.isabs(team["board"])                                   # the pair can move together
    data = json.loads((state / "tokens.json").read_text())
    assert data["format"] == 2
    recs = {r["agent"]: r for r in data["tokens"].values()}
    assert recs["dev"]["team"] == "default" and recs["dev"]["role"] == "member" and recs["dev"]["name"] == "dev"
    assert recs["old"]["revoked"] and all("hint" not in r for r in recs.values())
    assert stat.S_IMODE(os.stat(state / "tokens.json.v1.bak").st_mode) == 0o600
    assert (state / "teams" / "default" / "events.jsonl").is_file() and not (state / "events.jsonl").exists()
    assert (state / "teams" / "default" / "members.json").is_file() and not (state / "members.json").exists()
    rows = [json.loads(ln) for ln in (state / "admin-audit.jsonl").read_text().splitlines()]
    assert rows[-1]["action"] == "migrate" and rows[-1]["tokens_moved"] == 2
    # a second run finds everything done and changes nothing
    after = _snapshot(state, legacy["root"])
    again = migrate(legacy["state"], legacy["root"])
    assert {s["result"] for s in again} == {"already done"}
    assert _snapshot(state, legacy["root"]) == after
    Board(legacy["tmp"] / "other").init("other")
    with pytest.raises(Exception, match="exists already with another board"):
        migrate(legacy["state"], legacy["tmp"] / "other", team="default")


def test_after_migration_old_tokens_groups_gigs_and_event_cursors_keep_working(legacy):
    migrate(legacy["state"], legacy["root"])
    app = build_remote_app(legacy["state"], board=legacy["root"], poll_s=0.05, sweep_s=0)

    async def main():
        async with app.lifespan():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                h = {"Authorization": f"Bearer {legacy['live']}"}
                me = (await c.get("/v1/me", headers=h)).json()
                assert me["team"] == "default" and me["agent"] == "dev" and me["groups"] == ["dept:eng"]
                assert me["boot"] == legacy["boot"] and me["seq"] == legacy["seq"]
                ev = (await c.get("/v1/events", params={"after": 0, "wait": 0, "boot": legacy["boot"]},
                                  headers=h)).json()
                assert ev["boot"] == legacy["boot"] and [e["seq"] for e in ev["events"]] == [1, 2]
                listed = (await c.post("/v1/call/gig_list", json={}, headers=h)).json()
                assert legacy["gid"] in json.dumps(listed)
                gone = await c.get("/v1/me", headers={"Authorization": f"Bearer {legacy['gone']}"})
                assert gone.status_code == 401
    asyncio.run(main())


def test_migrate_teams_cli_dry_run_then_real(legacy, capsys):
    args = ["migrate-teams", "--state", str(legacy["state"]), "--board", str(legacy["root"])]
    assert cli_main(args + ["--dry-run", "--json"]) == 0
    out = capsys.readouterr().out
    assert [s["result"] for s in json.loads(out)] == ["would do"] * 4 and legacy["live"] not in out
    assert (legacy["state"] / "events.jsonl").exists()
    assert cli_main(args) == 0
    out = capsys.readouterr().out
    assert "done" in out and legacy["live"] not in out
    assert cli_main(args) == 0
    assert "already done" in capsys.readouterr().out


# ---------------------------------------------------------------------- CLI on the server's machine
def test_cli_teams_and_tokens_never_print_a_token(tmp_path, capsys):
    state = tmp_path / "state"
    s = ["--state", str(state)]
    assert cli_main(["team", "create", "acme", "--name", "Acme Inc", "--admin", "boss",
                     "--out", str(tmp_path / "boss.token")] + s) == 0
    assert cli_main(["team", "create", "globex", "--admin", "chief", "--out", str(tmp_path / "chief.token"),
                     "--expires-days", "30"] + s) == 0
    out = capsys.readouterr()
    for f in ("boss.token", "chief.token"):
        tok = (tmp_path / f).read_text().strip()
        assert tok.startswith(PREFIX) and tok not in out.out + out.err
        assert stat.S_IMODE(os.stat(tmp_path / f).st_mode) == 0o600
    assert cli_main(["token", "issue", "dev", "--out", str(tmp_path / "dev.token")] + s) == 1   # which team?
    assert "--team" in capsys.readouterr().err and not (tmp_path / "dev.token").exists()
    assert cli_main(["token", "issue", "dev", "--team", "nope", "--out", str(tmp_path / "dev.token")] + s) == 1
    capsys.readouterr()
    assert cli_main(["token", "issue", "dev", "--team", "acme", "--expires-days", "7",
                     "--out", str(tmp_path / "dev.token")] + s) == 0
    assert cli_main(["token", "issue", "dev", "--team", "acme", "--out", str(tmp_path / "dev.token")] + s) == 1
    capsys.readouterr()                                                       # --out exists: refused, not overwritten
    dev = (tmp_path / "dev.token").read_text().strip()
    assert TokenStore(state / "tokens.json").verify(dev).team == "acme"
    assert cli_main(["token", "list", "--all-teams", "--json"] + s) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {(r["team"], r["name"], r["role"]) for r in rows} == {("acme", "boss", "admin"), ("acme", "dev", "member"),
                                                                  ("globex", "chief", "admin")}
    assert all("token" not in r for r in rows)
    assert cli_main(["team", "list", "--json"] + s) == 0
    listed = {r["team"]: r for r in json.loads(capsys.readouterr().out)}
    assert listed["acme"]["members_live"] == 1 and listed["globex"]["admins_live"] == 1
    assert cli_main(["token", "revoke", "dev", "--team", "globex"] + s) == 1   # dev is in acme only
    capsys.readouterr()
    assert cli_main(["token", "revoke", "dev", "--team", "acme"] + s) == 0
    assert TokenStore(state / "tokens.json").verify(dev) is None
    audit = (state / "admin-audit.jsonl").read_text()
    assert PREFIX not in audit and {json.loads(ln)["action"] for ln in audit.splitlines()} >= {
        "team.create", "token.issue", "token.revoke"}


# ---------------------------------------------------------------------- CLI over the network
@pytest.fixture
def live_teams(teams):
    """The teams app with the server admin API on, on a real localhost port (uvicorn in a thread)."""
    import uvicorn
    cred = teams["tmp"] / "admin.cred"
    assert cli_main(["server-admin", "new-credential", "--out", str(cred)]) == 0
    app = build_remote_app(teams["state"], poll_s=0.05, sweep_s=0, admin_hash=load_admin_hash(str(cred), {}))
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    yield {**teams, "url": f"http://127.0.0.1:{port}", "cred": cred}
    server.should_exit = True
    t.join(10)


def test_cli_server_admin_and_team_admin_over_the_network(live_teams, capsys):
    lt, tmp = live_teams, live_teams["tmp"]
    capsys.readouterr()
    cred = lt["cred"].read_text().strip()
    assert cred.startswith(ADMIN_PREFIX) and stat.S_IMODE(os.stat(lt["cred"]).st_mode) == 0o600
    sa = ["--remote", lt["url"], "--token-file", str(lt["cred"])]
    assert cli_main(["server-admin", "create-team", "initech", "--name", "Initech", "--admin", "bill",
                     "--out", str(tmp / "bill.token")] + sa) == 0
    out = capsys.readouterr()
    bill = (tmp / "bill.token").read_text().strip()
    assert bill.startswith(PREFIX) and bill not in out.out + out.err and cred not in out.out + out.err
    ta = ["--remote", lt["url"], "--token-file", str(tmp / "bill.token")]
    assert cli_main(["team-admin", "issue", "peter", "--out", str(tmp / "peter.token")] + ta) == 0
    out = capsys.readouterr()
    peter = (tmp / "peter.token").read_text().strip()
    assert peter not in out.out + out.err and stat.S_IMODE(os.stat(tmp / "peter.token").st_mode) == 0o600
    rb = RemoteBoard(lt["url"], peter)
    rb.connect()
    assert rb.team == "initech" and rb.role == "member" and rb.agent_id == "peter"
    assert cli_main(["team-admin", "list", "--json"] + ta) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["name"] for r in rows} == {"bill", "peter"} and {r["team"] for r in rows} == {"initech"}
    assert cli_main(["team-admin", "revoke", "peter"] + ta) == 0
    capsys.readouterr()
    with pytest.raises(Refused):
        rb.call("board_info")
    rb.close()
    assert cli_main(["server-admin", "teams", "--json"] + sa) == 0
    assert {r["team"] for r in json.loads(capsys.readouterr().out)} == {"acme", "globex", "initech"}
    # a member token cannot manage tokens; a team admin token cannot reach the server admin API
    (tmp / "carol.token").write_text(lt["tokens"]["acme-carol"])
    os.chmod(tmp / "carol.token", 0o600)
    assert cli_main(["team-admin", "list", "--remote", lt["url"], "--token-file", str(tmp / "carol.token")]) == 1
    assert "team admin" in capsys.readouterr().err
    assert cli_main(["server-admin", "teams", "--remote", lt["url"], "--token-file", str(tmp / "bill.token")]) == 1
    assert "refused" in capsys.readouterr().err
