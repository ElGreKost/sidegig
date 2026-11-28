"""People who sign in with Google (Firebase ID tokens): who gets in, what they may do, what they see."""
import asyncio
import datetime
import json

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from sidegig import Board
from sidegig import protocol as P
from sidegig.people import PeopleAuth, Person
from sidegig.remote import build_remote_app
from sidegig.teams import TeamStore
from sidegig.tokens import TokenStore

PROJECT = "demo-project"
KID = "key-1"


def _key_and_cert():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
    return key, cert.public_bytes(serialization.Encoding.PEM).decode()


KEY, CERT = _key_and_cert()
OTHER_KEY, _ = _key_and_cert()


def id_token(email="kim@example.com", key=None, kid=KID, project=PROJECT, provider="google.com", verified=True,
             age=0, **extra):
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - age
    claims = {"iss": f"https://securetoken.google.com/{project}", "aud": project, "sub": "uid-" + email,
              "iat": now, "exp": now + 3600, "auth_time": now, "email": email, "email_verified": verified,
              "firebase": {"sign_in_provider": provider}, **extra}
    return jwt.encode(claims, key or KEY, algorithm="RS256", headers={"kid": kid})


def people(fetches=None):
    def fetch():
        if fetches is not None:
            fetches.append(1)
        return {KID: CERT}, 3600.0
    return PeopleAuth(PROJECT, [Person("Kim@Example.com", "kim", "Kim", "acme", ["kim-*"]),
                                Person("lee@example.com", "lee", "Lee", "acme")], fetch=fetch)


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "board"
    Board(root).init("people test")
    state = tmp_path / "state"
    TeamStore(state).add("acme", "Acme", root, init=False)
    store = TokenStore(state / "tokens.json")
    toks = {}
    for aid in ("kim-dev", "lee-dev", "bot"):
        Board(root).ensure_agent(aid, name=aid)
        toks[aid] = store.issue("acme", aid, label="test")[0]
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>app</title>")
    (web / "main.dart.js").write_text("// app")
    return {"root": root, "state": state, "tokens": toks, "web": web}


def run(setup, scenario, **kw):
    app = build_remote_app(setup["state"], poll_s=0.05, people=people(), web=setup["web"], **kw)

    async def main():
        async with app.lifespan():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                async def as_person(method, path, token=None, speak_as=None, **kw):
                    h = {"Authorization": f"Bearer {token or id_token()}"}
                    if speak_as:
                        h["X-Sidegig-As"] = speak_as
                    return await client.request(method, path, headers=h, **kw)

                async def tool(name, token=None, speak_as=None, **args):
                    r = await as_person("POST", f"/v1/call/{name}", token, speak_as, json=args)
                    return r.status_code, r.json()

                async def agent(who, name, **args):
                    r = await client.post(f"/v1/call/{name}", json=args,
                                          headers={"Authorization": f"Bearer {setup['tokens'][who]}"})
                    return r.json()
                return await scenario(client=client, as_person=as_person, tool=tool, agent=agent)
    return asyncio.run(main())


def test_only_listed_verified_google_accounts_get_in():
    auth = people()
    assert auth.verify(id_token())[0].agent == "kim"                       # email match ignores case
    assert auth.verify(id_token("KIM@example.com"))[0].agent == "kim"
    refused = {
        "stranger": id_token("someone@example.com"),
        "unverified": id_token(verified=False),
        "password sign-in": id_token(provider="password"),
        "other project": id_token(project="other-project"),
        "expired": id_token(age=7200),
        "wrong signature": id_token(key=OTHER_KEY),
        "unknown key": id_token(kid="key-2"),
        "not a jwt": "hello",
    }
    for why, tok in refused.items():
        person, reason = auth.verify(tok)
        assert person is None and reason, why
    assert auth.verify(id_token(age=7200))[1].startswith("invalid token")
    assert "no access" in auth.verify(id_token("someone@example.com"))[1]


def test_unsigned_and_hs256_tokens_are_refused():
    claims = jwt.decode(id_token(), options={"verify_signature": False})
    assert people().verify(jwt.encode(claims, "s" * 40, algorithm="HS256", headers={"kid": KID}))[0] is None
    head = "eyJhbGciOiJub25lIiwia2lkIjoia2V5LTEifQ"                          # {"alg":"none","kid":"key-1"}
    body = id_token().split(".")[1]
    assert people().verify(f"{head}.{body}.")[0] is None


def test_certificates_are_cached():
    fetches = []
    auth = people(fetches)
    for _ in range(3):
        assert auth.verify(id_token())[0] is not None
    assert len(fetches) == 1


def test_people_file_is_checked(tmp_path):
    f = tmp_path / "people.json"
    f.write_text(json.dumps({"firebase_project": PROJECT, "team": "acme",
                             "people": [{"email": "a@example.com", "agent": "ann", "act_as": ["ann-*"]}]}))
    auth = PeopleAuth.load(f)
    assert auth.by_agent("ann").team == "acme" and auth.by_agent("ann").may_speak_as("ann-dev")
    assert auth.by_agent("ann").may_speak_as("ann")
    assert not auth.by_agent("ann").may_speak_as("bob-dev")
    for bad in ({"firebase_project": "", "people": []},
                {"firebase_project": PROJECT, "team": "acme", "people": [{"email": "x", "agent": "ann"}]},
                {"firebase_project": PROJECT, "team": "acme",
                 "people": [{"email": "a@example.com", "agent": "ann"}, {"email": "b@example.com", "agent": "ann"}]}):
        f.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            PeopleAuth.load(f)


def test_a_person_reads_and_writes_as_themselves_and_their_own_agents(setup):
    async def scenario(client, as_person, tool, agent, **_):
        r = await as_person("GET", "/v1/me")
        me = r.json()
        assert r.status_code == 200 and me["agent"] == "kim" and me["person"]["email"] == "kim@example.com"
        assert me["can_act_as"] == ["kim", "kim-dev"]                      # never lee-dev or bot
        code, posted = await tool("gig_post", title="Hello Lee", text="Can you look at this?", to_agent="lee")
        assert code == 200 and posted["ok"], posted
        gid = posted.get("gig_id") or posted.get("id")
        meta = (await tool("gig_read", gig_id=gid))[1]["meta"]
        assert meta["asker"] == "kim" and meta["to_agent"] == "lee"
        # Lee answers in the app, Kim follows up, then a gig asked by kim-dev is followed up as kim-dev
        lee = id_token("lee@example.com")
        code, said = await tool("gig_answer", token=lee, gig_id=gid, text="Done, see the branch.")
        assert said["ok"], said
        assert (await tool("gig_followup", gig_id=gid, text="Thanks, one more thing."))[1]["ok"]
        asked = await agent("kim-dev", "gig_post", title="Build check", text="Is CI green?", to_agent="bot")
        kid = asked.get("gig_id") or asked.get("id")
        code, refused = await tool("gig_followup", gig_id=kid, text="as kim")       # kim is not the asker
        assert not refused["ok"]
        assert (await tool("gig_followup", speak_as="kim-dev", gig_id=kid, text="as kim-dev"))[1]["ok"]
        # a person may never speak as someone else's agent
        r = await as_person("POST", "/v1/call/gig_followup", speak_as="lee-dev", json={"gig_id": kid, "text": "x"})
        assert r.status_code == 403
        # the thread as chat messages
        _, read = await tool("gig_read", gig_id=gid, as_messages=True)
        kinds = [(m["kind"], m["agent_id"]) for m in read["messages"] if m["kind"] != "status"]
        assert kinds == [("message", "kim"), ("answer", "lee"), ("message", "kim")], read["messages"]
        assert "thread" not in read and read["answers"] == 1
    run(setup, scenario)


def test_people_see_every_event_and_never_reach_mcp_or_admin(setup):
    async def scenario(client, as_person, tool, agent, **_):
        me = (await as_person("GET", "/v1/me")).json()
        await agent("bot", "gig_post", title="Between agents", text="x", to_agent="lee-dev")
        r = await as_person("GET", "/v1/events", params={"after": me["seq"], "wait": 0, "boot": me["boot"]})
        evs = r.json()["events"]
        assert any(e["title"] == "Between agents" for e in evs), evs           # not addressed to kim: still seen
        for path in ("/mcp", "/v1/team/tokens", "/v1/scan"):
            r = await as_person("POST" if path != "/v1/scan" else "GET", path, json={})
            assert r.status_code in (403, 404), (path, r.status_code)
        r = await as_person("GET", "/v1/me", token=id_token("someone@example.com"))
        assert r.status_code == 401 and "no access" in r.json()["error"]
        # agent tokens still work as before
        r = await client.get("/v1/me", headers={"Authorization": f"Bearer {setup['tokens']['bot']}"})
        assert r.status_code == 200 and r.json()["agent"] == "bot" and "can_act_as" not in r.json()
    run(setup, scenario)


def test_the_web_app_files_are_served_without_a_token(setup):
    async def scenario(client, **_):
        r = await client.get("/")
        assert r.status_code == 200 and "<title>app</title>" in r.text
        assert r.headers["cache-control"] == "no-cache" and r.headers["x-frame-options"] == "DENY"
        assert (await client.get("/main.dart.js")).status_code == 200
        assert (await client.get("/../state/tokens.json")).status_code in (400, 404)
        assert (await client.post("/", content=b"x")).status_code == 404
        assert (await client.get("/v1/me")).status_code == 401              # the API still needs a token
    run(setup, scenario)


def test_parse_thread_handles_every_block_kind():
    thread = (
        "----- kim · 2026-01-02 10:00 -----\nFirst line\n\nsecond paragraph\n"
        "[lee-dev] 10:01 Picked up.\n"
        "==================== ANSWER from Lee (lee-dev) · 2026-01-02 10:05 ====================\n"
        "----- not a header inside an answer · 2026-01-02 10:06 -----\nok\n"
        "==================== END OF ANSWER · 2026-01-02 10:05 ====================\n"
        "----- Kim Smith · 2026-01-02 10:07 -----\nthanks\n"
    )
    msgs = P.parse_thread(thread)
    assert [(m["kind"], m["author"], m["agent_id"], m["when"]) for m in msgs] == [
        ("message", "kim", "kim", "2026-01-02 10:00"), ("status", "lee-dev", "lee-dev", "10:01"),
        ("answer", "Lee", "lee-dev", "2026-01-02 10:05"), ("message", "Kim Smith", None, "2026-01-02 10:07")]
    assert msgs[0]["text"] == "First line\n\nsecond paragraph"
    assert msgs[2]["text"].startswith("----- not a header") and msgs[2]["text"].endswith("ok")
    assert P.parse_thread("plain text with no header")[0]["text"] == "plain text with no header"
