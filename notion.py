import json
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _store as S  # noqa: E402

OPS = ("request", "cancel")

PROMPT = """You are the Notion ticket agent for the "Meeting Notebook" app in {app_dir}.

Job: turn exactly the {n} to-do(s) below into tickets in the user's Notion ticket database, one ticket per to-do,
nothing else. The user picked these by hand: don't add, merge, split or drop any. A to-do is the user's own
shorthand ("surya to remove the following from ..."), so read everything that comes with it before writing the
ticket: the text, its context, every note in its notes thread, every image (open each one with the Read tool; they
often say what "the following" or "this" means), and the meeting summary and attendees.

Use the claude.ai Notion connector (its tools have "notion" in their names; load them with
ToolSearch("notion search fetch create pages users self file upload")). "The user" is the person the connector is
signed in as: get their user id and name with notion-get-self.

1. The ticket database.
{database}
   Fetch its data source once and read the schema. Map the properties by what they mean, whatever they are named:
   - title: the title property.
   - owner: the person property for whoever does the work ("Owner", "Assignee", "Assigned to", "DRI", ...), never a
     created-by, subscriber, reviewer or other read-only one. A creator property fills itself with the user.
   - status: if there is a status property, its default option, else the option meaning not started / to do.
   - priority: if there is a priority property, the option meaning "not prioritized yet" ("?", "None", "Unknown",
     "Triage", ...); if it has no such option, leave priority empty so the owner sets it.
   Leave every other property empty.

2. For each to-do decide:
   - Title: a clear, specific ticket title in the imperative ("Remove X from Y"), under 90 characters, that makes
     sense to someone who wasn't in the meeting. Leave out who does it (that goes in the owner property). Use only
     what the to-do, its context, notes and images say; don't invent scope.
   - Owner: the person who will actually do the work, not the person it is for. The user is the default; when the
     to-do, its context or its notes hand it to someone else ("surya to ...", "ask Surya to ...", "Surya will ..."),
     it is that person. Find them with notion-get-users {{"query": "<name>"}}; try the first name, the full name
     from the meeting attendees, and the email name (surya@... for Surya). Use their user id when exactly one
     workspace person clearly matches. If nobody matches, or several could, the user is the owner and the page
     gets a line "Meant for: <name> (not matched to a Notion user)". If the database has no owner property,
     put "Owner: <user mention>" at the top of the page instead.

3. Upload every image listed under each to-do's notes, one at a time, right before you create the pages:
   a. notion-create-file-upload {{"filename": "<its filename>"}} returns upload_url and upload_headers.
   b. Send the file with the shell (one header flag per upload_headers entry):
      curl -sS -X POST "<upload_url>" -H "<header>: <value>" -F "file=@<its path>;type=<its type>;filename=<its filename>"
   c. The response's suggested_markdown (<image source="file-upload://..." />) is the image block for the page.
   If an image won't upload after one retry, write "(image <filename> could not be attached)" in its place and go on.

4. Create all pages in ONE notion-create-pages call with parent {{"data_source_id": "<the data source id>"}}, the
   properties from steps 1 and 2, and this page content (Notion markdown):
   - One to three sentences saying what needs doing and why, from the to-do, its context, notes and images.
   - If the to-do has notes: a "## Notes" heading, then every note oldest first, its text verbatim (links kept),
     each followed by that note's images from step 3 in order.
   - "From meeting: <meeting title> (<date>)", linked to the meeting url when one is given, if the to-do has a
     meeting; otherwise "Added by hand on <date>".
   - "Due: <due>" if a due date is given.

To-dos (JSON; "meeting" is null for a to-do added by hand):
{items}

If the Notion connector is missing or a page can't be created, retry at most once, then record the error for that
to-do instead.

When done, write exactly one file with the Write tool (it creates folders):
  {out}
Shape (JSON, no comments), one result per to-do above with its date and todo_id unchanged:
{{"request_id": "{rid}", "database": {{"name": "<database title>", "url": "<database URL>", "data_source": "collection://..."}}, "results": [{{"date": "...", "todo_id": "...", "url": "<new page URL, or null>", "title": "<the ticket title>", "owner": "<the owner's name>", "error": "<null, or why it failed>"}}]}}
("database" is null if you found none.)

Use the shell only for the curl uploads in step 3. Touch nothing else: no other files, no edits to existing Notion
pages or databases, no comments, no messages to anyone.
Finish with one line: tickets created (title → owner), failures.
"""

DB_SET = """   Use {db_name}: {db_url} (data source {data_source}). The user chose it; don't look for another."""

DB_FIND = """   None is set yet, so find the one this user's team files tickets in. Search Notion (notion-search) for databases
   such as tasks, tickets, issues, bugs, engineering, backlog or sprint, fetch the likely ones, and keep those that
   are ticket trackers: a title, a person property for whoever does the work, and a status. Prefer the one the user
   files pages in. If exactly one fits, use it. If several fit and none is clearly the team's tracker, ask the user
   which one with AskUserQuestion (up to 4 options: name and where it lives) before creating anything. If none
   fits, create nothing and record "No ticket database found in Notion" as the error for every to-do."""


def _pairs(items):
    out = []
    for part in items.split(","):
        d, _, tid = part.strip().partition(":")
        if tid.strip():
            out.append((S.check_date(d), tid.strip()))
    if not out:
        raise ValueError("items is required, e.g. 2026-10-07:s-1a2b3c4d5e6f")
    return out


def _filename(img, i):
    ext = img["id"].rsplit(".", 1)[-1]
    stem = os.path.splitext(str(img.get("name") or ""))[0]
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-")[:60] or "image"
    return "%s-%d.%s" % (stem, i, ext)


def _notes(t):
    out, n = [], 0
    for note in t.get("notes") or []:
        imgs = []
        for img in note.get("images") or []:
            try:
                path = S.image_path(str(img.get("id") or ""))
            except ValueError:
                continue
            if os.path.isfile(path):
                n += 1
                imgs.append({"path": path, "filename": _filename(img, n), "type": img.get("type") or "image/png"})
        out.append({"at": note.get("at"), "text": note.get("text") or "", "images": imgs})
    return out


def _meeting(day, m):
    if not m:
        return None
    return {"title": m.get("title") or "", "date": day["date"],
            "url": m.get("url") if str(m.get("url") or "").startswith("http") else None,
            "summary": m.get("summary") or "", "attendees": S.str_list(m.get("attendees"))}


def _database(cfg):
    if cfg["data_source"] and cfg["database_url"]:
        return DB_SET.format(db_name=cfg["database_name"] or "the configured database",
                             db_url=cfg["database_url"], data_source=cfg["data_source"])
    return DB_FIND


def main(op: str = "request", items: str = "", request_id: str = ""):
    """Queue hand-picked to-dos for Notion tickets and return the Claude task prompt; `cancel` un-queues them."""
    if op not in OPS:
        raise ValueError("op must be one of %s" % ", ".join(OPS))
    with S.locked():
        cfg = S.load_settings()["notion"]
        pages, changed = {}, set()

        if op == "cancel":
            for d in S.all_dates():
                day = pages.setdefault(d, S.load_day(d))
                for t in day["todos"]:
                    n = t.get("notion") or {}
                    if n.get("status") == "queued" and n.get("request_id") == request_id:
                        t.pop("notion")
                        changed.add(d)
            for d in changed:
                S.save_day(pages[d])
            return {"ok": True, "index": S.days_index()}

        rid = uuid.uuid4().hex[:10]
        picked, skipped = [], 0
        for date, tid in _pairs(items):
            day, t = S.find_todo(pages, date, tid)
            if not t or (t.get("notion") or {}).get("status") in ("created", "queued"):
                skipped += 1
                continue
            m = next((m for m in day["meetings"] if t.get("meeting_id") and m.get("id") == t.get("meeting_id")), None)
            picked.append({
                "date": day["date"], "todo_id": tid, "text": t.get("text") or "",
                "due": t.get("due"), "context": t.get("context") or "",
                "meeting": _meeting(day, m), "notes": _notes(t),
            })
            t["notion"] = {"status": "queued", "request_id": rid, "at": S.now_iso()}
            changed.add(day["date"])
        for d in changed:
            S.save_day(pages[d])

    prompt = PROMPT.format(
        app_dir=S.APP_DIR, n=len(picked), database=_database(cfg),
        items=json.dumps(picked, ensure_ascii=False, indent=1),
        out=os.path.join(S.NOTION_OUT, rid + ".json"), rid=rid) if picked else ""
    return {"count": len(picked), "skipped": skipped, "request_id": rid if picked else None,
            "database": cfg["database_name"] or "Notion", "prompt": prompt, "index": S.days_index()}
