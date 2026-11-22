"""Hosted mode, phase 1: the persisted event log, the HTTP API next to /mcp, groups over the network,
the Host check, a damaged token store, RemoteBoard driving the worker, and the token CLI."""
import asyncio
import json
import os
import socket
import stat
import threading
import time

import httpx
import pytest

from sidegig import Board
from sidegig.cli import main as cli_main
from sidegig.client import RemoteBoard, RemoteUnavailable, check_url, event_line, wait_for_answer_remote, watch_remote
from sidegig.errors import Conflict, Invalid, Refused
from sidegig.remote import EventBus, build_remote_app
from sidegig.tokens import TokenStore
from sidegig.worker import Worker

AGENTS = ("kostis", "giannis", "dev", "kostis-marketing", "giannis-marketing")


@pytest.fixture
def hosted(tmp_path):
    root, state = tmp_path / "board", tmp_path / "state"
    Board(root).init("hosted test")
    store = TokenStore(state / "tokens.json")
    toks = {aid: store.issue(aid, label="test") for aid in AGENTS}
    (state / "members.json").write_text(json.dumps({
        "dept:marketing": ["kostis-marketing", "giannis-marketing"],
        "person:giannis": ["giannis", "giannis-marketing"]}), "utf-8")
    return {"root": root, "state": state, "store": store, "tokens": toks, "tmp": tmp_path}


def run(h, scenario, **kw):
    """Build the app, run its lifespan, give scenario call(who, tool, **args) and get(who, path, **params)."""
    kw.setdefault("sweep_s", 0)
    app = build_remote_app(h["root"], h["state"], poll_s=0.05, **kw)

    async def main():
        async with app.lifespan():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
                def auth(who):
                    return {"Authorization": f"Bearer {h['tokens'][who]}"}

                async def call(who, tool, **args):
                    r = await c.post(f"/v1/call/{tool}", json=args, headers=auth(who))
                    return r.json()

                async def get(who, path, **params):
                    return (await c.get(path, params=params, headers=auth(who))).json()
                return await scenario(call=call, get=get, client=c, app=app)
    return asyncio.run(main())


# ---------------------------------------------------------------------- event log
def test_events_survive_a_restart_and_numbering_continues(tmp_path):
    path = tmp_path / "events.jsonl"
    bus = EventBus(path, keep=10)
    for i in range(3):
        bus.publish({"type": "posted", "gig_id": f"g{i}", "actor": "kostis", "to": ["dev"]})
    boot = bus.boot
    again = EventBus(path, keep=10)
    assert again.boot == boot and again.seq == 3
    assert [e["gig_id"] for e in again.for_agent("dev", 1)] == ["g1", "g2"]
    assert again.publish({"type": "answered", "gig_id": "g0", "actor": "dev", "to": ["kostis"]})["seq"] == 4


def test_a_torn_last_line_is_skipped_and_trimming_reports_a_gap(tmp_path):
    path = tmp_path / "events.jsonl"
    bus = EventBus(path, keep=10)
    bus.publish({"type": "posted", "gig_id": "a", "actor": "x", "to": ["*"]})
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"seq": 2, "type": "pos')                       # the process died mid-write
    bus = EventBus(path, keep=10)
    assert bus.seq == 1
    bus.publish({"type": "posted", "gig_id": "b", "actor": "x", "to": ["*"]})
    assert [json.loads(ln).get("gig_id") for ln in path.read_text().splitlines()[1:] if ln.strip()
            and not ln.startswith('{"seq": 2, "type": "pos')] == ["a", "b"]
    for i in range(25):
        bus.publish({"type": "posted", "gig_id": f"n{i}", "actor": "x", "to": ["*"]})
    assert len(path.read_text().splitlines()) <= 1 + 2 * 10      # compacted
    assert bus.gap(0) and not bus.gap(bus.seq - 3)
    assert EventBus(path, keep=10).seq == bus.seq                  # still continues after compaction


def test_a_client_resumes_after_a_server_restart(hosted):
    async def first(call, get, client, app):
        gid = (await call("kostis", "gig_post", title="Spec review", to_agent="dev"))["gig_id"]
        ev = await get("dev", "/v1/events", after=0, wait=0)
        return gid, ev["boot"], ev["next"]
    gid, boot, cursor = run(hosted, first)

    async def second(call, get, client, app):
        assert (await get("dev", "/v1/events", after=cursor, wait=0, boot=boot))["events"] == []   # no replay
        await call("dev", "gig_answer", gig_id=gid, text="Looks good.")
        ev = await get("kostis", "/v1/events", after=cursor, wait=0, boot=boot)
        assert [e["type"] for e in ev["events"]] == ["claimed", "answered"] and ev["boot"] == boot
        assert ev["events"][0]["seq"] == cursor + 1
        stale_boot = await get("dev", "/v1/events", after=cursor, wait=0, boot="00000000")
        assert stale_boot["events"][0]["type"] == "posted"         # an unknown boot replays from the start
    run(hosted, second)
    for name in ("events.jsonl", "audit.jsonl", "tokens.json"):    # only the server's user can read them
        assert stat.S_IMODE(os.stat(hosted["state"] / name).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(hosted["state"]).st_mode) == 0o700


# ---------------------------------------------------------------------- HTTP API
def test_v1_me_call_and_inbox(hosted):
    async def sc(call, get, client, app):
        me = await get("giannis-marketing", "/v1/me")
        assert me["agent"] == "giannis-marketing" and me["groups"] == ["dept:marketing", "person:giannis"]
        assert me["give_up_minutes"] == 240 and me["claim_stale_minutes"] == 10
        gid = (await call("kostis", "gig_post", title="Launch copy", to_agent="dept:marketing"))["gig_id"]
        box = await call("kostis-marketing", "inbox")
        assert [g["id"] for g in box["to_answer"]] == [gid] and "notice" in box
        spoof = await call("kostis-marketing", "inbox", agent_id="kostis")
        assert not spoof["ok"] and "speaks for kostis-marketing" in spoof["error"]
        r = await client.post("/v1/call/no_such_tool", json={},
                              headers={"Authorization": f"Bearer {hosted['tokens']['dev']}"})
        assert r.status_code == 404 and "gig_post" in r.json()["hint"]
        bad = await client.post("/v1/call/gig_read", json={"nope": 1},
                                headers={"Authorization": f"Bearer {hosted['tokens']['dev']}"})
        assert bad.status_code == 400 and bad.json()["ok"] is False
        await call("kostis-marketing", "gig_answer", gig_id=gid, text="Line one.")
        assert [g["id"] for g in (await call("kostis", "inbox"))["answered"]] == [gid]
    run(hosted, sc)


def test_group_claims_over_the_network_and_group_events(hosted):
    async def sc(call, get, client, app):
        gid = (await call("kostis", "gig_post", title="Launch copy", to_agent="dept:marketing"))["gig_id"]
        for who in ("kostis-marketing", "giannis-marketing"):
            assert [e["gig_id"] for e in (await get(who, "/v1/events", after=0, wait=0))["events"]] == [gid]
        assert (await get("dev", "/v1/events", after=0, wait=0))["events"] == []
        no = await call("dev", "gig_claim", gig_id=gid)
        assert not no["ok"] and no["code"] == "Conflict" and "not a member" in no["error"]
        yes = await call("giannis-marketing", "gig_claim", gig_id=gid)
        assert yes["ok"] and yes["meta"]["claimed_by"] == "giannis-marketing"
        unknown = await call("kostis", "gig_post", title="x", to_agent="dept:sales")
        assert not unknown["ok"] and "unknown group 'dept:sales'" in unknown["error"]
        scan = await get("dev", "/v1/scan")
        assert gid not in [g["id"] for g in scan["gigs"]]                 # not dev's business
        scan = await get("kostis-marketing", "/v1/scan")
        assert [g["for_me"] for g in scan["gigs"] if g["id"] == gid] == [True]
    run(hosted, sc)


def test_a_wrong_host_header_is_refused_on_every_path(hosted):
    async def sc(call, get, client, app):
        h = {"Authorization": f"Bearer {hosted['tokens']['dev']}", "Host": "evil.example"}
        assert (await client.get("/v1/me", headers=h)).status_code == 421
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        assert (await client.post("/mcp", json=body, headers={**h, "Accept": "application/json, text/event-stream"})
                ).status_code == 421
        ok = {"Authorization": f"Bearer {hosted['tokens']['dev']}", "Host": "board.example:8443"}
        assert (await client.get("/v1/me", headers=ok)).status_code == 200
        assert (await client.get("/healthz", headers={"Host": "evil.example"})).status_code == 200
    run(hosted, sc, allowed_hosts=["board.example:*"])


@pytest.mark.parametrize("damage", ["corrupt", "wrong_shape", "directory"])
def test_an_unreadable_token_store_refuses_everyone(hosted, damage):
    path = hosted["state"] / "tokens.json"

    async def sc(call, get, client, app):
        assert (await client.get("/v1/me", headers={"Authorization": f"Bearer {hosted['tokens']['dev']}"})
                ).status_code == 200
        path.unlink()
        if damage == "corrupt":
            path.write_text("{not json", "utf-8")
        elif damage == "wrong_shape":
            path.write_text(json.dumps({"tokens": ["a", "b"]}), "utf-8")
        else:
            path.mkdir()
        for who in AGENTS:
            r = await client.get("/v1/me", headers={"Authorization": f"Bearer {hosted['tokens'][who]}"})
            assert r.status_code == 401
    run(hosted, sc)
    if damage != "directory":
        with pytest.raises(Invalid, match="cannot use tokens.json"):
            TokenStore(path).issue("dev")


def test_the_sweep_turns_a_give_up_into_an_event(hosted):
    from conftest import Clock
    clock = Clock()

    async def sc(call, get, client, app):
        gid = (await call("kostis", "gig_post", title="Long job", to_agent="dev"))["gig_id"]
        await call("dev", "gig_claim", gig_id=gid)
        app.board.sweep()                                          # takes stock
        mark = (await get("kostis", "/v1/events", after=0, wait=0))["next"]
        clock.advance(5 * 3600)
        assert app.board.sweep() == 1
        ev = (await get("kostis", "/v1/events", after=mark, wait=0))["events"]
        assert [(e["type"], e["gig_id"]) for e in ev] == [("stale", gid)]
        assert [e["type"] for e in (await get("dev", "/v1/events", after=0, wait=0))["events"]][-1] == "stale"
    run(hosted, sc, clock=clock)


def test_state_folder_inside_the_board_is_refused(hosted):
    with pytest.raises(ValueError, match="inside the board"):
        build_remote_app(hosted["root"], hosted["root"] / "_state")


# ---------------------------------------------------------------------- RemoteBoard + worker
@pytest.fixture
def live(hosted):
    """The app on a real localhost port (uvicorn in a thread), for the synchronous client."""
    import uvicorn
    app = build_remote_app(hosted["root"], hosted["state"], poll_s=0.05, sweep_s=0)
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
    yield {**hosted, "url": f"http://127.0.0.1:{port}", "app": app}
    server.should_exit = True
    t.join(10)


def test_remote_board_drives_the_worker_end_to_end(live):
    asker = RemoteBoard(live["url"], live["tokens"]["kostis"])
    dev = RemoteBoard(live["url"], live["tokens"]["dev"])
    out = []
    w = Worker(dev, "dev", out=out.append, err=lambda s: None, sleep=dev.idle, settle_s=0, scan_s=10)
    w.start()
    assert out == []
    gid = asker.call("gig_post", title="Spec review", text="Is the API right?", to_agent="dev")["gig_id"]
    t0 = time.monotonic()
    w.sleep(10)                                         # wakes on the posted event, not after 10 s
    assert time.monotonic() - t0 < 5
    w.step()
    assert out == [f"NEW {gid} remote"]
    thread = dev.call("gig_read", gig_id=gid)
    assert "Is the API right?" in thread["thread"] and "notice" in thread
    with pytest.raises(Conflict):
        RemoteBoard(live["url"], live["tokens"]["giannis"]).claim(gid, "giannis")
    dev.call("gig_answer", gig_id=gid, text="Yes, ship it.")
    st = wait_for_answer_remote(asker, gid, timeout_s=5)
    assert st["state"] == "answered" and st["answer"]["text"].strip() == "Yes, ship it."
    w.step()
    assert dev.agent_id == "dev" and [a["online"] for a in live["app"].board.agents() if a["id"] == "dev"] == [True]
    live["store"].revoke(agent_id="dev")
    with pytest.raises(Refused):
        w.run()                                          # a revoked token stops the loop instead of spinning
    asker.close()
    dev.close()


def test_remote_group_worker_and_watch_lines(live):
    asker = RemoteBoard(live["url"], live["tokens"]["kostis"])
    km = RemoteBoard(live["url"], live["tokens"]["kostis-marketing"])
    km.connect()
    gid = asker.call("gig_post", title="Launch\ncopy \x1b[31m", to_agent="dept:marketing")["gig_id"]
    out = []
    w = Worker(km, "kostis-marketing", out=out.append, err=lambda s: None, sleep=km.idle, settle_s=0)
    w.start()
    w.step()
    assert out == [f"NEW {gid} remote"]
    lines = []
    watcher = RemoteBoard(live["url"], live["tokens"]["kostis"])
    watch_remote(watcher, out=lines.append, once=True, cursor_file=str(live["tmp"] / "cur.json"))
    assert lines == []                                   # a fresh watcher starts at "now"
    km.call("gig_answer", gig_id=gid, text="Done.")
    watch_remote(watcher, out=lines.append, once=True, cursor_file=str(live["tmp"] / "cur.json"))
    assert lines == [f'ANSWERED {gid} by=kostis-marketing status=answered title="Launch copy [31m"']   # no ESC
    for rb in (asker, km, watcher):
        rb.close()


def test_client_refuses_plain_http_to_other_machines_and_bad_tokens(live, monkeypatch, tmp_path):
    with pytest.raises(Invalid, match="plain http"):
        check_url("http://board.example")
    assert check_url("https://board.example/mcp/") == "https://board.example"
    with pytest.raises(Refused):
        RemoteBoard(live["url"], "sgk_" + "x" * 40).connect()
    with pytest.raises(RemoteUnavailable):
        RemoteBoard("http://127.0.0.1:9", "sgk_" + "x" * 40, timeout=2).connect()
    assert event_line({"type": "posted", "gig_id": "g", "actor": "a", "status": "open", "title": 'say "hi"\nnow'}) \
        == 'POSTED g by=a status=open title="say \\"hi\\" now"'


# ---------------------------------------------------------------------- CLI
def test_token_issue_writes_a_private_file_and_never_prints_the_token(tmp_path, capsys):
    state = tmp_path / "state"
    out = tmp_path / "dev.token"
    assert cli_main(["token", "issue", "dev", "--state", str(state), "--out", str(out)]) == 0
    printed = capsys.readouterr()
    token = out.read_text().strip()
    assert token.startswith("sgk_") and token not in printed.out + printed.err
    assert str(out) in printed.out and "dev" in printed.out
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert TokenStore(state / "tokens.json").verify(token) == "dev"
    assert cli_main(["token", "issue", "dev", "--state", str(state), "--out", str(out)]) == 1   # no overwrite
    assert "already exists" in capsys.readouterr().err
    assert cli_main(["token", "list", "--state", str(state)]) == 0
    listed = capsys.readouterr().out
    assert "dev" in listed and "live" in listed and token[:10] not in listed
    assert cli_main(["token", "revoke", "dev", "--state", str(state)]) == 0
    assert TokenStore(state / "tokens.json").verify(token) is None
    capsys.readouterr()
    assert cli_main(["token", "list", "--state", str(state), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"].startswith("revoked")


def test_cli_wait_and_watch_against_a_live_server(live, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("SIDEGIG_TOKEN", live["tokens"]["kostis"])
    tok_file = tmp_path / "dev.token"
    tok_file.write_text(live["tokens"]["dev"] + "\n")
    os.chmod(tok_file, 0o600)
    gid = RemoteBoard(live["url"], live["tokens"]["kostis"]).call("gig_post", title="Quick one", to_agent="dev")["gig_id"]
    assert cli_main(["worker", "--remote", live["url"], "--token-file", str(tok_file), "--once"]) == 0
    assert f"NEW {gid} remote" in capsys.readouterr().out
    RemoteBoard(live["url"], live["tokens"]["dev"]).call("gig_answer", gig_id=gid, text="Answer text.")
    assert cli_main(["wait", gid, "--remote", live["url"], "--timeout", "5"]) == 0
    assert capsys.readouterr().out.strip() == "Answer text."
    cur = str(tmp_path / "watch.cursor")
    assert cli_main(["watch", "--remote", live["url"], "--once", "--cursor", cur]) == 0
    RemoteBoard(live["url"], live["tokens"]["dev"]).call("gig_say", gig_id=gid, text="One more thing.")
    assert cli_main(["watch", "--remote", live["url"], "--once", "--cursor", cur]) == 0
    assert capsys.readouterr().out.strip().splitlines() == [f'SAID {gid} by=dev status=answered title="Quick one"']
    monkeypatch.setenv("SIDEGIG_TOKEN", "sgk_" + "y" * 40)
    assert cli_main(["watch", "--remote", live["url"], "--once"]) == 1
    assert "refused the token" in capsys.readouterr().err
