import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _store as S  # noqa: E402

OPS = ("get", "add", "edit", "delete")
MAX_NOTE = 20000


def _clean(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("text is required")
    if len(text) > MAX_NOTE:
        raise ValueError("a note can be at most %d characters" % MAX_NOTE)
    return text


def _find(day, todo_id):
    for t in day["todos"]:
        if t.get("id") == todo_id:
            return t
    raise ValueError("no to-do %r on %s — it may have been moved or deleted" % (todo_id, day["date"]))


def main(date: str, todo_id: str, op: str = "get", note_id: str = "", text: str = ""):
    """Read or change the notes thread kept inside one to-do."""
    date = S.check_date(date)
    if op not in OPS:
        raise ValueError("op must be one of %s" % ", ".join(OPS))
    with S.locked():
        day = S.load_day(date)
        t = _find(day, todo_id)
        notes = list(t.get("notes") or [])
        if op == "add":
            notes.append({"id": "n-" + uuid.uuid4().hex[:10], "text": _clean(text), "at": S.now_iso()})
        elif op in ("edit", "delete"):
            n = next((n for n in notes if n.get("id") == note_id), None)
            if n is None:
                raise ValueError("no note %r on this to-do" % note_id)
            if op == "edit":
                n["text"] = _clean(text)
                n["edited_at"] = S.now_iso()
            else:
                notes.remove(n)
        if op != "get":
            if notes:
                t["notes"] = notes
            else:
                t.pop("notes", None)
            S.save_day(day)
    m = next((m for m in day["meetings"] if m.get("id") == t.get("meeting_id")), None)
    meeting = {k: m.get(k) for k in ("id", "title", "start", "end", "url", "source")} if m else None
    return {"date": date, "todo": t, "meeting": meeting, "index": S.days_index() if op != "get" else None}
