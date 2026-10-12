import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _store as S  # noqa: E402

OPS = ("request", "cancel")

PROMPT = """You are the Notion ticket agent for the "Meeting Notebook" app in {app_dir}.

Job: turn exactly the {n} to-do(s) below into tickets in the user's Notion database "{db_name}" — nothing else.
The user picked these by hand: don't add, merge, split or reword them.

Use the claude.ai Notion connector (its tools have "notion" in their names — load them with
ToolSearch("notion fetch create pages")).
Database: {db_url}
Data source: {data_source}
Fetch the data source once to confirm the property names, then create all pages in ONE notion-create-pages call
with parent {{"data_source_id": "<the data source id>"}}. Properties on every page:
- "Name": the to-do text
- "Owner": ["{owner_id}"]  (the user)
- "Status": "{status}"
- "Priority": the to-do's "priority" below
Leave every other property empty. Page content, short Notion markdown:
- the to-do's context, if any
- "From meeting: <meeting> (<date>)", with the meeting link if one is given
- "Due: <due>" if a due date is given

To-dos (JSON):
{items}

If the Notion connector is missing or a page can't be created, retry at most once, then record the error for that
to-do instead.

When done, write exactly one file with the Write tool (it creates folders; you need no shell):
  {out}
Shape (JSON, no comments), one result per to-do above with its date and todo_id unchanged:
{{"request_id": "{rid}", "results": [{{"date": "...", "todo_id": "...", "url": "<new page URL, or null>", "error": "<null, or why it failed>"}}]}}

Touch nothing else: no other files, no edits to existing Notion pages, no comments, no messages to anyone.
Finish with one line: tickets created, failures.
"""


def _pairs(items):
    out = []
    for part in items.split(","):
        d, _, tid = part.strip().partition(":")
        if tid.strip():
            out.append((S.check_date(d), tid.strip()))
    if not out:
        raise ValueError("items is required, e.g. 2026-10-07:s-1a2b3c4d5e6f")
    return out


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
            m = next((m for m in day["meetings"] if m.get("id") == t.get("meeting_id")), {})
            picked.append({
                "date": day["date"], "todo_id": tid, "text": t.get("text") or "",
                "priority": cfg["priority"].get(t.get("priority") or "medium", "P2"),
                "due": t.get("due"), "context": t.get("context") or "",
                "meeting": m.get("title") or t.get("meeting_title") or "",
                "meeting_url": m.get("url") if str(m.get("url") or "").startswith("http") else None,
            })
            t["notion"] = {"status": "queued", "request_id": rid, "at": S.now_iso()}
            changed.add(day["date"])
        for d in changed:
            S.save_day(pages[d])

    prompt = PROMPT.format(
        app_dir=S.APP_DIR, n=len(picked), db_name=cfg["database_name"], db_url=cfg["database_url"],
        data_source=cfg["data_source"], owner_id=cfg["owner_id"], status=cfg["status"],
        items=json.dumps(picked, ensure_ascii=False, indent=1),
        out=os.path.join(S.NOTION_OUT, rid + ".json"), rid=rid) if picked else ""
    return {"count": len(picked), "skipped": skipped, "request_id": rid if picked else None,
            "database": cfg["database_name"], "prompt": prompt, "index": S.days_index()}
