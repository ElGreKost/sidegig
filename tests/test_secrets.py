"""The secret filter: posts, answers, says, follow-ups and profiles that contain a key are refused.

Fake keys are assembled at run time so this public file holds no key-shaped literal."""
import json

import pytest

from sidegig.errors import Invalid
from sidegig.mcp_server import build_app
from sidegig.secretscan import find_secret

R = "Ab3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0eF3hJ6kM9"   # 39 random-looking characters

FAKES = {
    "anthropic": "sk-" + "ant-api03-" + R,
    "openai": "sk-" + "proj-" + R,
    "openai-legacy": "sk-" + R,
    "aws": "AK" + "IA" + "Z3QX7LMN2PWR5TYU",
    "github": "gh" + "p_" + R[:36],
    "github-pat": "github" + "_pat_" + R + "_" + R[:20],
    "private-key": "-----BEGIN " + "OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----",
    "rsa-key": "-----BEGIN " + "RSA PRIVATE KEY-----",
    "slack-bot": "xo" + "xb-" + "1234567890-" + R[:24],
    "slack-user": "xo" + "xp-" + "1234567890-" + R[:24],
    "stripe": "sk" + "_live_" + R[:24],
    "stripe-restricted": "rk" + "_live_" + R[:24],
    "sidegig": "sg" + "k_" + R,
}


@pytest.mark.parametrize("kind", sorted(FAKES))
def test_every_key_kind_is_found_in_running_text(kind):
    assert find_secret(f"here is the config: {FAKES[kind]} thanks") is not None


@pytest.mark.parametrize("text", [
    "use sk-learn style pipelines", "the task-skip-flag is fine", "AKIA is a prefix", "ghp_ alone",
    "risk_live_check is a function", "-----BEGIN PUBLIC KEY-----", "sk-this-is-just-a-long-slug-name",
    "xoxb- without a body", "sgk_short",
])
def test_ordinary_text_is_not_flagged(text):
    assert find_secret(text) is None


def test_board_refuses_secrets_everywhere_text_is_written(board, agents):
    key = FAKES["anthropic"]
    with pytest.raises(Invalid, match="Anthropic API key") as exc:
        board.post("Deploy", f"use {key}")
    assert key not in str(exc.value) and key not in exc.value.hint      # never echoed back
    with pytest.raises(Invalid):
        board.post(f"key {FAKES['aws']}", "x")
    assert board.gigs() == []                                          # nothing was written
    gid = board.post("Deploy", "how?", asker="alice-helper")
    with pytest.raises(Invalid, match="GitHub token"):
        board.answer(gid, "bob-worker", f"token: {FAKES['github']}")
    board.answer(gid, "bob-worker", "use the deploy script")
    with pytest.raises(Invalid):
        board.say(gid, "bob-worker", FAKES["stripe"])
    with pytest.raises(Invalid, match="private key"):
        board.followup(gid, "alice-helper", FAKES["private-key"])
    with pytest.raises(Invalid):
        board.close(gid, "alice-helper", 5, note=FAKES["slack-bot"])
    with pytest.raises(Invalid):
        board.register("Leaky", description=FAKES["openai"])
    with pytest.raises(Invalid):
        board.ensure_agent("leaky", description=FAKES["sidegig"])
    thread = board.read(gid)["thread"]
    assert all(v not in thread for v in FAKES.values())


def test_mcp_tool_returns_a_clear_error(board, agents):
    app = build_app(board=board, agent_id="alice-helper")
    import asyncio

    async def call(name, **args):
        res = await app.call_tool(name, args)
        return json.loads(res[0].text)

    r = asyncio.run(call("gig_post", title="Need help", text=f"key={FAKES['openai']}"))
    assert r["ok"] is False and "secret key" in r["error"] and "remove the secret" in r["hint"]
    assert FAKES["openai"] not in json.dumps(r)
