import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _store as S  # noqa: E402

OPS = ("add", "done", "edit", "delete", "move")


def _find(day, todo_id):
    for t in day["todos"]:
        if t["id"] == todo_id:
            return t
    raise ValueError("no to-do %r on %s" % (todo_id, day["date"]))


def main(date: str, op: str, todo_id: str = "", text: str = "", meeting_id: str = "",
         done: bool = False, priority: str = "", due: str = "", to_date: str = ""):
    """Change one to-do on one notebook page; returns the fresh page and index."""
    date = S.check_date(date)
    if op not in OPS:
        raise ValueError("op must be one of %s" % ", ".join(OPS))
    with S.locked():
        day = S.load_day(date)
        if op == "add":
            text = " ".join(text.split())
            if not text:
                raise ValueError("text is required")
            day["todos"].append({
                "id": "m-" + uuid.uuid4().hex[:10], "key": None, "text": text,
                "done": False, "done_at": None, "origin": "manual", "created_at": S.now_iso(),
                "meeting_id": S.safe_id(meeting_id) if meeting_id else "",
                "priority": S.clean_priority(priority), "due": S.clean_due(due), "context": "",
            })
        elif op == "done":
            t = _find(day, todo_id)
            t["done"] = bool(done)
            t["done_at"] = S.now_iso() if done else None
        elif op == "edit":
            t = _find(day, todo_id)
            if text.strip():
                t["text"] = " ".join(text.split())
                t["edited"] = True
            if priority:
                t["priority"] = S.clean_priority(priority)
            if due:
                t["due"] = S.clean_due(due) if due != "none" else None
        elif op == "delete":
            t = _find(day, todo_id)
            day["todos"].remove(t)
            if t.get("key"):
                # Remember deletions so the next sync of this day doesn't bring it back.
                day.setdefault("dismissed", []).append(t["key"])
        elif op == "move":
            target = S.check_date(to_date)
            if target == date:
                raise ValueError("to_date is the same page")
            t = _find(day, todo_id)
            day["todos"].remove(t)
            if t.get("key"):
                day.setdefault("dismissed", []).append(t["key"])
            dest = S.load_day(target)
            title = next((m.get("title") for m in day["meetings"] if m.get("id") == t.get("meeting_id")), "")
            moved = dict(t, moved_from=date, key=None, meeting_title=t.get("meeting_title") or title)
            dest["todos"].append(moved)
            S.save_day(dest)
        S.save_day(day)
    return {"day": day, "index": S.days_index()}
