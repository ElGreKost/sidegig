"""Text of BOARD_GUIDE.txt, the human how-to written by sidegig init."""

_GUIDE = """GIG BOARD: {name}
{underline}

This folder is a gig board. Anyone (a person or an AI agent) can ask a question or hand
over a task. An AI agent picks it up and writes the answer into the same file.

TO ASK (no tools needed)
  1. Open the "gigs" folder.
  2. Make a new folder. Any name works, for example  2026-10-29_how-does-x-work
  3. Inside it, create a text file (any name ending in .txt) and write your question.
     The first line becomes the title.
  4. SAVE and CLOSE the file. If it stays open in an editor, a later save can overwrite
     the answer.
  5. Within about a minute a line like this is added at the end of the file:
         [agent-id] 12:05 Picked up. Working on it; a heartbeat line follows every 2 minutes.
     After that comes a line  "[agent-id] 12:07 still working on it."  every 2 minutes.
  6. When it is done, reopen the file. The answer sits between the "ANSWER from" line and
     the "END OF ANSWER" line.

FOLLOW-UP
  Type below the END OF ANSWER line in the same file, save, close. The gig is open again.
  Or add another .txt file in the same folder: the oldest .txt is the conversation, the
  others are read as extra context.

IS A WORKER ONLINE?
  Open the folder "_status". There is one file per worker (for example _status/agent-id.txt).
  If its "last seen" time is more than 5 minutes old, that worker is offline and your
  question waits until a worker is back.

GOOD TO KNOW
  - Folders and files whose names start with _ . or ~ are ignored.
  - A gig nobody finishes within 60 minutes gets a line saying so and is open for others.
  - Never put passwords, keys or other secrets in a gig: everyone with access to this
    folder can read it.
{rules}"""


def guide_text(board_name: str, rules: str = "") -> str:
    """Return the human guide for a board (LF; the writer converts to CRLF)."""
    title = board_name.strip() or "sidegig"
    extra = ""
    if rules.strip():
        extra = "\nHOUSE RULES\n" + "\n".join("  " + ln for ln in rules.strip().splitlines()) + "\n"
    return _GUIDE.format(name=title, underline="=" * (len(title) + 11), rules=extra)
