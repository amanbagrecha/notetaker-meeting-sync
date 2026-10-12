import os
import sys
from datetime import date as _date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _store as S  # noqa: E402


def main(date: str = ""):
    """Merge any finished sync and Notion results, then return the page index plus one day page."""
    with S.locked():
        merged, problems = S.ingest_inbox()
        problems += S.ingest_notion()
    day = S.load_day(S.check_date(date)) if date else None
    if day:
        for m in day["meetings"]:
            m["chat_count"] = len((S.read_json(S.chat_path(m["id"]), {}) or {}).get("messages", []))
    pending = sorted(n[:-5] for n in os.listdir(S.INBOX) if n.endswith(".json") and S.DATE_RE.match(n[:-5]))
    return {
        "today": _date.today().isoformat(),
        "index": S.days_index(),
        "day": day,
        "merged": merged,
        "problems": problems,
        "inbox_pending": pending,
        "settings": S.load_settings(),
        "sync": S.load_sync_log(),
    }
