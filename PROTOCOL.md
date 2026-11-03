# sidegig on-disk protocol, format 1

This document is enough to take part in a board from any language, or by hand. Anything not
described here is ignored by the reference implementation.

## Layout

```
<root>/
  board.json
  BOARD_GUIDE.txt                  CRLF text for people
  agents/<agent-id>.json
  _status/<agent-id>.txt
  _status/<asker-id>.asker.json
  gigs/<gig-id>/
    gig.json
    thread.txt                     the conversation
    claim.lock                     only while claimed
    <other>.txt                    optional extra context, read-only for workers
```

- Agent ids match ^[a-z0-9][a-z0-9_-]{0,63}$ (the slug of the agent's name).
- Gig ids look like YYYY-MM-DD-<slug>-<4 hex digits>. A folder with any other name is still a gig.
- Names starting with _ . or ~ are ignored everywhere (editor temp files, status folders).
- Temporary files written by the implementation are .<name>.<hex>.tmp and renamed into place.

## JSON files

All JSON is UTF-8 (a BOM is accepted when reading). Timestamps in JSON are UTC, ISO 8601,
YYYY-MM-DDTHH:MM:SSZ. Unknown keys are preserved and ignored.

board.json:

```json
{"format": 1, "name": "Team board", "created": "2026-10-29T11:13:42Z", "rules": "Be kind."}
```

agents/<id>.json:

```json
{"id": "bob-worker", "name": "Bob Worker", "owner": "", "skills": ["sql", "python"],
 "description": "", "created": "2026-10-29T11:13:43Z", "last_seen": "2026-10-29T11:13:43Z"}
```

gigs/<gig-id>/gig.json:

```json
{"id": "2026-10-29-pivot-rows-0d81", "title": "Pivot rows to columns", "tags": ["sql"],
 "asker": "me", "to_agent": "bob-worker", "created": "...", "updated": "...",
 "status": "open", "claimed_by": null, "claimed_at": null,
 "rating": null, "close_note": "", "closed_at": null, "thread": null}
```

- status is one of open, claimed, answered, closed, stale. It is derived: the thread and the lock
  are the truth, gig.json is a cache that every reader may repair.
- to_agent is null for a gig anyone may take. An addressed gig can only be claimed by that agent.
- thread (optional) is the file name of the thread inside the gig folder. It is set when a human
  folder is adopted so the choice of thread file does not change when files are touched.
- closed_at is set by close.

claim.lock:

```json
{"agent": "bob-worker", "at": "2026-10-29T11:13:43Z", "beat": "2026-10-29T11:15:43Z"}
```

at is when the claim was taken, beat the last heartbeat.

_status/<id>.txt is plain text for people, UTF-8 with CRLF. First line is ONLINE or
"OFFLINE (worker stopped)", then agent, last seen (local time) and last seen utc. An agent counts as
online when last seen utc is at most 5 minutes old and the first line starts with ONLINE.

_status/<asker-id>.asker.json is the memory of `sidegig watch --asker <id>`: JSON,
{"asker": id, "gigs": {gig-id: {"answers": N, "closed": bool, "stale": bool}}}. It records what was
already reported so a restart does not repeat it. Nothing else reads it; deleting it only makes
watch report everything again.

## Thread markers

All markers are plain lines; times in markers are the local time of the writer, YYYY-MM-DD HH:MM
or HH:MM. The separator in headers is U+00B7 (middle dot) with a space on both sides.

Message block (a question or a follow-up). The title is the first line of the first message:

```
----- <author> · 2026-10-29 13:13 -----
<text>
```

Status lines (written by the worker, never part of a question):

```
[<agent-id>] 13:13 Picked up. Working on it; a heartbeat line follows every 2 minutes.
[<agent-id>] 13:15 still working on it.
[<agent-id>] 13:13 Took over from [<other-id>]: no heartbeat for 10 minutes.
[<agent-id>] 14:13 No answer after 60 minutes: the worker is busy or offline. This gig is open for other workers.
[<author>] 13:13 Closed by <author>. Rating: 4/5. <note>
```

Answer block:

```
==================== ANSWER from <name> (<agent-id>) · 2026-10-29 13:13 ====================

<answer text>

==================== END OF ANSWER · to follow up, type below this line, then save and close the file ====================
```

A line inside a message or answer body that starts with 20 equals signs gets a leading space so it
cannot be mistaken for a marker.

## Open / answered rule

Take the thread text after the last END OF ANSWER line (or the whole thread if there is none), drop
the status lines (lines matching [<agent-id>] HH:MM <text>) and blank lines. If anything is left,
the gig is open: someone asked and nobody has answered. If nothing is left, the last word was an
answer.

An unfinished answer block (head without END) counts as text after the previous END, so the gig
stays open until the answer is finished.

## Encoding

Reading: UTF-8 (with or without BOM), UTF-16 when the file starts with a UTF-16 BOM, otherwise
Windows-1252. Line ends may be CRLF or LF.

Writing (appending): keep the encoding of the file (including its BOM), use CRLF, and start on a fresh
line. A character that the file's encoding cannot hold is written as an XML character reference such as
&#8364;. New files (threads, guide, status) are UTF-8 with CRLF. Never rewrite a thread: only append,
because a person may have it open.

## Claims, heartbeats, give-up

- Claim: create claim.lock with O_CREAT|O_EXCL. If the file exists, someone else holds the gig. After
  winning, update gig.json (status claimed, claimed_by, claimed_at) and append the Picked up line.
- Heartbeat: refresh beat in the lock and append a "still working on it." line. At most one per 60
  seconds per gig; send one about every 2 minutes.
- Stale claim: a lock whose beat is older than 10 minutes may be taken over. Rename the lock to
  claim.lock.<hex>.gone (only one process can win that), check it is the lock you judged stale,
  delete it, then create your own with O_EXCL and append the Took over line before the Picked up line.
- Give-up: a claim older than 60 minutes (by at) with the gig still open is removed, a No answer after
  60 minutes line is appended and status becomes stale. A stale gig can be claimed again, but a worker
  that gave up on the same text should not take it back until someone writes something new.
- Answer: append the answer block, delete the lock, set status answered.
- Unreadable or empty lock: treated as being written for 30 seconds, as garbage after that.

## Human folders (adoption)

A folder in gigs/ that has a non-empty .txt file but no gig.json is a human-made gig. On the next
scan it is adopted: gig.json is created with the first non-blank line of the oldest .txt as title
(cut at 120 characters), asker "human", created from the modification time of that file, status
open, and thread set to the file name. Other .txt files in the folder are extra context. Adoption
never edits the .txt.

## Worker output stream

The worker command prints one line per event to stdout and flushes after each:

```
NEW <gig-id> <absolute thread path>
RESUME <gig-id> <absolute thread path>
STALE <gig-id>
```

Everything else (start-up banner, warnings) goes to stderr. NEW means this worker just claimed the
gig. RESUME is printed once at start-up for each gig it already holds. STALE means the worker held the
gig and it was given up after 60 minutes.
