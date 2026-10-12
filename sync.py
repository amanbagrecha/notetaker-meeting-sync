import os
import sys
import uuid
from datetime import datetime, time, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _store as S  # noqa: E402

OPS = ("plan", "request", "mark", "settings")

PROMPT = """You are the background sync agent for the "Meeting Notebook" app in {app_dir}.

Job: for each date below, collect the meetings the user had that day and pull out the to-dos THEY personally own.
Dates (local time, {tz}): {dates}
Now: {now}

Where to look — always read BOTH of these and combine them:
1. Google Calendar (the claude.ai Google Calendar connector, list_events on the primary calendar for the whole
   local day): the schedule — every meeting the user is invited to, including ones later today that haven't
   happened yet. Skip events the user declined and non-meeting blocks (out of office, focus time, working
   location). Wispr Flow's own calendar mirror can be empty; don't rely on it for the schedule.
2. Wispr Flow (Notetaker MCP): meeting summaries, notes, transcripts and extracted tasks. Its tools have "wispr" in
   their names — load them with ToolSearch("wispr"). Use its account/identity tool to learn who the user is, so
   you can tell their action items from everyone else's.
Match each Wispr Flow recording to the calendar event it overlaps in time (or whose title it clearly matches).
A matched pair is ONE meeting: id = the calendar event id, title = the calendar title, attendees from the
calendar, url = the Wispr Flow share link, source "wispr-flow", and the summary/discussed/decisions/details/to-dos
from Wispr Flow. Recordings with no calendar event go in with their Wispr Flow id. Calendar events with no
recording go in with source "google-calendar", url = the event's htmlLink, empty lists and a "notes" line: "Scheduled —
hasn't happened yet." if it starts after now, else "No Wispr Flow recording for this meeting."
If Wispr Flow is not connected, try Circleback, then Notion meeting notes, for the notes and say so in "warnings";
if Google Calendar is not connected, say that in "warnings" too. List every source you used in "sources".
Never invent discussion points or tasks.

For EACH date write exactly one file with the Write tool (it creates folders; you need no shell):
  {inbox}/<YYYY-MM-DD>.json

File shape (JSON, no comments):
{{
  "date": "YYYY-MM-DD",
  "synced_at": "<now, ISO 8601 with UTC offset>",
  "sources": ["google-calendar", "wispr-flow"],
  "summary": "<one sentence about the day>",
  "warnings": [],
  "meetings": [{{
    "id": "<stable id from the source; else date + slug of the title>",
    "title": "...",
    "start": "HH:MM", "end": "HH:MM",
    "attendees": ["First Last"],
    "source": "wispr-flow | google-calendar",
    "url": "<link to the meeting in its source, or null>",
    "summary": "<2-3 sentences>",
    "discussed": ["<topic — what was said>"],
    "decisions": ["<decision>"],
    "notes": "<caveats, or empty string>",
    "details": "<the source's full meeting notes / summary as markdown (Wispr Flow's summary incl. next steps and decisions), at most ~6000 characters>"
  }}],
  "todos": [{{
    "text": "<imperative, at most 120 characters, e.g. 'Send Vasu the revised pricing deck'>",
    "meeting_id": "<id of one meeting above>",
    "priority": "high | medium | low",
    "due": "YYYY-MM-DD or null",
    "context": "<one sentence: who asked for it and why>"
  }}]
}}

To-do rules: take to-dos only from meetings that have ended and have notes — none for scheduled, in-progress or
unrecorded meetings. Each meeting's to-dos are taken once. These meetings already had theirs taken: still list them
under "meetings", but write no to-dos for them and don't re-read their transcripts (the app ignores any you send):
{taken}
Only items the user committed to, was assigned, or clearly has to follow up on — not other people's
tasks. Wispr Flow often labels people "Speaker 1/2/…" and the numbering can change within one transcript; when a
next step is assigned to a speaker number, read the transcript (get_meeting with view_transcript) to work out
which speaker is the user before keeping or dropping it. Do not copy transcripts into the file. Merge duplicates.
Re-syncing a date is normal; always list every meeting of that date — meetings you leave out are removed from the page.

Touch nothing else: no other files, no git, no messages or emails to anyone. Finish with one line per date:
meetings found, to-dos written, source used.
"""


def _parse(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.astimezone()


def _due_dates(settings, log, now):
    """Yesterday until it has been synced after midnight; today every interval from 08:00."""
    today = now.date()
    interval = timedelta(minutes=max(15, int(settings.get("interval_min") or 90)))
    retry = max(interval, timedelta(hours=3))
    midnight = datetime.combine(today, time.min).astimezone()
    out = []

    y = (today - timedelta(days=1)).isoformat()
    y_sync = _parse(S.load_day(y).get("synced_at"))
    y_req = _parse(log["last_request"].get(y))
    if (not y_sync or y_sync < midnight) and (not y_req or now - y_req >= retry):
        out.append(y)

    t = today.isoformat()
    if now.hour >= 8:
        seen = [x for x in (_parse(S.load_day(t).get("synced_at")), _parse(log["last_request"].get(t))) if x]
        if not seen or now - max(seen) >= interval:
            out.append(t)
    return out


def _record(log, dates, trigger, now):
    rid = uuid.uuid4().hex[:10]
    for d in dates:
        log["last_request"][d] = now.isoformat(timespec="seconds")
    log["requests"] = (log["requests"] + [{"id": rid, "at": now.isoformat(timespec="seconds"),
                                           "dates": dates, "trigger": trigger, "entry_id": None}])[-50:]
    S.write_json(S.SYNC_LOG, log)
    return rid


def _taken(dates):
    rows = ['  %s %s "%s" (id %s)' % (d, m.get("start") or "", m.get("title") or "", m["id"])
            for d in dates for m in S.load_day(d)["meetings"] if S.todos_taken(m)]
    return "\n".join(rows) or "  (none yet)"


def _prompt(dates, now):
    return PROMPT.format(app_dir=S.APP_DIR, inbox=S.INBOX, dates=", ".join(dates), taken=_taken(dates),
                         tz=now.tzname() or "local", now=now.isoformat(timespec="seconds"))


def main(op: str = "plan", dates: str = "", request_id: str = "", entry_id: str = "",
         auto_sync: str = "", interval_min: int = 0):
    """Decide what to sync and hand back the Claude task prompt; also stores sync settings."""
    if op not in OPS:
        raise ValueError("op must be one of %s" % ", ".join(OPS))
    now = datetime.now().astimezone()
    with S.locked():
        settings = S.load_settings()
        log = S.load_sync_log()

        if op == "settings":
            if auto_sync:
                settings["auto_sync"] = auto_sync.strip().lower() in ("1", "true", "yes", "on")
            if interval_min:
                settings["interval_min"] = max(15, int(interval_min))
            S.write_json(S.SETTINGS, settings)
            return {"settings": settings}

        if op == "mark":
            for r in log["requests"]:
                if r["id"] == request_id:
                    r["entry_id"] = entry_id or None
            S.write_json(S.SYNC_LOG, log)
            return {"ok": True}

        if op == "plan":
            if not settings.get("auto_sync"):
                return {"dates": [], "prompt": "", "request_id": None}
            todo = _due_dates(settings, log, now)
            trigger = "auto"
        else:
            todo = sorted({S.check_date(d) for d in dates.split(",") if d.strip()})
            if not todo:
                raise ValueError("dates is required, e.g. 2026-10-07")
            trigger = "manual"
        if not todo:
            return {"dates": [], "prompt": "", "request_id": None}
        rid = _record(log, todo, trigger, now)
    return {"dates": todo, "prompt": _prompt(todo, now), "request_id": rid}
