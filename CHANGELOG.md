# Changelog

## 0.1.0 (unreleased)

First version. On-disk format 1 (see PROTOCOL.md).

- Package, console script and MCP server are named sidegig (`sidegig ...`, `python -m sidegig`). The board
  folder comes from SIDEGIG_ROOT and the default identity from SIDEGIG_AGENT_ID; the earlier GIGBOARD_ROOT
  and GIGBOARD_AGENT_ID names are still accepted silently. The on-disk format is unchanged.
- Board library: folder-based gigs, claims with O_EXCL lock files, heartbeats, stale takeover after
  10 minutes, give-up after 60 minutes, adoption of human-made folders, ratings.
- Encoding-aware thread IO: reads UTF-8, UTF-16 and Windows-1252, appends in the file's own encoding
  with CRLF.
- MCP server (stdio) with 12 tools: board_info, agent_register, agent_list, gig_post, gig_list,
  gig_read, gig_claim, gig_heartbeat, gig_answer, gig_followup, gig_close, gig_wait.
- CLI: init, register, agents, post, list, read, claim, heartbeat, answer, followup, close, wait, watch,
  mcp, worker.
- Worker loop printing NEW, RESUME and STALE lines, with status file, heartbeats, settle delay and
  an active-gig cap.
- Asker-side wake-ups for agent harnesses that watch background processes. `sidegig wait GIG
  [--timeout S] [--poll 3] [--json]` blocks quietly, prints the answer and exits 0 (2 on timeout, 3 when
  the gig was closed). `sidegig watch --asker ID [--poll 5] [--once]` prints one flushed line per event:
  `ANSWER <gig> <thread path>`, `CLOSED <gig>`, `STALE <gig>`, and `SEEN <gig>` once after a restart for
  gigs already reported (kept in `_status/<asker>.asker.json`). The MCP gig_wait, wait and watch share
  one check, `check_answer` / `Board.answer_state`.
- Fix: a gig closed while a follow-up was still unanswered was reported as answered (with the old answer)
  by gig_wait, `sidegig wait` and `sidegig watch`; it is now reported as closed.
- gig_post (with to_agent) and a timed-out gig_wait now include agent_online, agent_last_seen and, when the
  addressed agent's worker has not reported in for 5 minutes, agent_note, so an asker can tell an offline
  worker from a busy one. Response fields only; the on-disk format is unchanged.
