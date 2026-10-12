import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _store as S  # noqa: E402

OPS = ("get", "append", "clear")
MAX_TRANSCRIPT = 120000


def main(date: str, meeting_id: str, op: str = "get", role: str = "", content: str = ""):
    """Read or extend the conversation kept for one meeting; `get` also returns the meeting record."""
    if op not in OPS:
        raise ValueError("op must be one of %s" % ", ".join(OPS))
    date = S.check_date(date)
    mid = S.safe_id(meeting_id)
    path = S.chat_path(mid)
    with S.locked():
        chat = S.read_json(path, None) or {"meeting_id": mid, "date": date, "messages": []}
        if op == "append":
            if role not in ("user", "assistant") or not content.strip():
                raise ValueError("append needs role user|assistant and content")
            chat["messages"].append({"role": role, "content": content.strip(), "at": S.now_iso()})
            S.write_json(path, chat)
            return {"messages": chat["messages"]}
        if op == "clear":
            chat["messages"] = []
            S.write_json(path, chat)
            return {"messages": []}
    day = S.load_day(date)
    meeting = next((m for m in day["meetings"] if m.get("id") == mid), None)
    if meeting is None:
        raise ValueError("no meeting %r on %s" % (meeting_id, date))
    transcript = ""
    if os.path.exists(S.transcript_path(mid)):
        with open(S.transcript_path(mid), encoding="utf-8") as f:
            transcript = f.read(MAX_TRANSCRIPT)
    todos = [t for t in day["todos"] if t.get("meeting_id") == mid]
    return {"meeting": meeting, "todos": todos, "transcript": transcript, "messages": chat["messages"]}
