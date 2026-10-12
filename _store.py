"""Shared storage for the notebook's .py files (no main — not bot-callable).

Layout under .fused/data/:
  days/<YYYY-MM-DD>.json      one notebook page: meetings + to-dos (the source of truth)
  inbox/<YYYY-MM-DD>.json     written by the sync task; merged into days/ on next read
  inbox/processed/            last merged inbox file per date (audit trail)
  transcripts/<meeting>.txt   transcript text pulled out of inbox files
  chats/<meeting>.json        conversation with one meeting
  notion/<request>.json       written by the Notion ticket task; stamped onto to-dos on next read
  notion/processed/           merged Notion results (audit trail)
  settings.json, sync.json    auto-sync switch + Notion target, sync request log
"""
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(APP_DIR, ".fused", "data")
DAYS = os.path.join(DATA, "days")
INBOX = os.path.join(DATA, "inbox")
PROCESSED = os.path.join(INBOX, "processed")
TRANSCRIPTS = os.path.join(DATA, "transcripts")
CHATS = os.path.join(DATA, "chats")
SETTINGS = os.path.join(DATA, "settings.json")
SYNC_LOG = os.path.join(DATA, "sync.json")
NOTION_OUT = os.path.join(DATA, "notion")
NOTION_DONE = os.path.join(NOTION_OUT, "processed")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PRIORITIES = ("high", "medium", "low")
# Where picked to-dos become tickets: the "Engineering Tasks" database, owned by the user (the "Aman" board).
NOTION_DEFAULTS = {
    "database_name": "Engineering Tasks",
    "database_url": "https://app.notion.com/p/9d18fa8396e24ea98f7bb84f44a2b152",
    "data_source": "collection://f23dba4b-107c-4b12-ae4e-4274fd87a243",
    "owner_id": "32fd872b-594c-819a-99c6-0002a6d6c599",
    "status": "Not started",
    "priority": {"high": "P1", "medium": "P2", "low": "P3"},
}
NOTION_STALE_S = 30 * 60
DEFAULT_SETTINGS = {"auto_sync": True, "interval_min": 90, "notion": NOTION_DEFAULTS}


def ensure_dirs():
    for d in (DAYS, INBOX, PROCESSED, TRANSCRIPTS, CHATS, NOTION_DONE):
        os.makedirs(d, exist_ok=True)


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def check_date(s):
    s = (s or "").strip()
    if not DATE_RE.match(s):
        raise ValueError("expected a date like 2026-10-07, got %r" % s)
    return s


def safe_id(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(s or ""))[:96].strip("._") or "meeting"


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


@contextmanager
def locked():
    """One writer at a time across overlapping runPython calls."""
    ensure_dirs()
    with open(os.path.join(DATA, ".lock"), "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# ---------- days ----------

def day_path(d):
    return os.path.join(DAYS, check_date(d) + ".json")


def empty_day(d):
    return {"date": d, "summary": "", "meetings": [], "todos": [], "dismissed": [],
            "synced_at": None, "sources": [], "warnings": []}


def load_day(d):
    day = read_json(day_path(d), None) or empty_day(d)
    for k, v in empty_day(d).items():
        day.setdefault(k, v)
    return day


def save_day(day):
    write_json(day_path(day["date"]), day)


def norm_text(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def todo_key(text):
    # Text only: the same meeting can come back under another id (calendar first, Wispr Flow later).
    return hashlib.sha1(norm_text(text).encode()).hexdigest()[:12]


def clean_priority(p):
    p = (p or "").strip().lower()
    if p in ("med", "normal"):
        p = "medium"
    return p if p in PRIORITIES else "medium"


def clean_due(s):
    s = (s or "").strip() if isinstance(s, str) else ""
    return s if DATE_RE.match(s) else None


def str_list(v):
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()]


def transcript_path(meeting_id):
    return os.path.join(TRANSCRIPTS, safe_id(meeting_id) + ".txt")


def all_dates():
    if not os.path.isdir(DAYS):
        return []
    return sorted(n[:-5] for n in os.listdir(DAYS) if n.endswith(".json") and DATE_RE.match(n[:-5]))


def days_index():
    """One compact row per notebook page, newest first — feeds sidebar, calendar, open list."""
    rows = []
    for d in all_dates():
        try:
            day = load_day(d)
        except ValueError:
            continue
        titles = {m.get("id"): m.get("title") or "" for m in day["meetings"]}
        rows.append({
            "date": day["date"],
            "summary": day.get("summary") or "",
            "synced_at": day.get("synced_at"),
            "n_meetings": len(day["meetings"]),
            "meetings": [{"id": m.get("id"), "title": m.get("title") or "", "start": m.get("start") or ""}
                         for m in day["meetings"]],
            "todos": [{"id": t["id"], "text": t.get("text") or "", "done": bool(t.get("done")),
                       "priority": t.get("priority") or "medium", "due": t.get("due"),
                       "meeting_id": t.get("meeting_id") or "",
                       "meeting_title": titles.get(t.get("meeting_id")) or t.get("meeting_title") or "",
                       "notion": t.get("notion")}
                      for t in day["todos"]],
        })
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


# ---------- inbox merge ----------

MEETING_FIELDS = ("title", "start", "end", "source", "url", "summary", "notes", "details")
MEETING_LISTS = ("attendees", "discussed", "decisions")


def chat_path(meeting_id):
    return os.path.join(CHATS, safe_id(meeting_id) + ".json")


def _meeting_sig(m):
    return (norm_text(m.get("title")), (m.get("start") or "").strip())


def has_notes(m):
    """The meeting happened and came back with notes — the only kind to-dos are taken from."""
    return m.get("source") != "google-calendar" and bool(m.get("summary") or m.get("details") or m.get("discussed"))


def todos_taken(m):
    # todos_at: when its to-dos were taken; None = notes came in before it ended, still to take.
    # Pages synced before this rule have no todos_at at all: their to-dos were taken back then.
    return has_notes(m) and m.get("todos_at", True) is not None


def _ended(d, m, at):
    try:
        return datetime.fromisoformat("%sT%s" % (d, (m.get("end") or "").strip())).astimezone() <= at
    except ValueError:
        return True


def merge_payload(payload):
    """Fold one sync result into its day page. A sync is the full picture of its day: meetings it no
    longer lists are dropped unless a to-do or chat hangs off them. To-dos are taken from each meeting
    once — on the first sync after it ended with notes; later syncs refresh the meeting but never re-add
    its to-dos, so rewordings don't pile up and ticked or deleted ones stay put."""
    d = check_date(payload.get("date"))
    day = load_day(d)
    old = {m["id"]: m for m in day["meetings"] if m.get("id")}
    new, id_map = {}, {}
    for m in payload.get("meetings") or []:
        if not isinstance(m, dict):
            continue
        raw_id = m.get("id") or "%s-%s" % (d, m.get("title") or "meeting")
        mid = safe_id(raw_id)
        id_map[str(raw_id)] = mid
        rec = dict(old.get(mid, {}))
        rec["id"] = mid
        for k in MEETING_FIELDS:
            if k in m:
                rec[k] = (str(m[k]).strip() if m[k] is not None else None)
        for k in MEETING_LISTS:
            if k in m:
                rec[k] = str_list(m[k])
        transcript = m.get("transcript")
        if isinstance(transcript, str) and transcript.strip():
            write_text(transcript_path(mid), transcript.strip())
        new[mid] = rec

    # The same meeting may come back under a new id (e.g. calendar first, Wispr Flow later).
    by_sig = {_meeting_sig(m): mid for mid, m in new.items()}
    remap = {}
    for oid, om in old.items():
        if oid in new:
            continue
        nid = by_sig.get(_meeting_sig(om))
        if nid:
            remap[oid] = nid
            for src, dst in ((chat_path(oid), chat_path(nid)), (transcript_path(oid), transcript_path(nid))):
                if os.path.exists(src) and not os.path.exists(dst):
                    os.replace(src, dst)
        elif any(t.get("meeting_id") == oid for t in day["todos"]) or os.path.exists(chat_path(oid)):
            new[oid] = om
    for t in day["todos"]:
        if t.get("meeting_id") in remap:
            t["meeting_id"] = remap[t["meeting_id"]]
    for m in new.values():
        m["has_transcript"] = os.path.exists(transcript_path(m["id"]))
    day["meetings"] = sorted(new.values(), key=lambda m: (m.get("start") or "99:99", m.get("title") or ""))

    taken = {remap.get(oid, oid) for oid, om in old.items() if todos_taken(om)}
    try:
        at = datetime.fromisoformat(str(payload.get("synced_at"))).astimezone()
    except ValueError:
        at = datetime.now().astimezone()
    harvest = set()
    for mid in set(id_map.values()):
        m = new[mid]
        if mid in taken or not has_notes(m):
            continue
        if _ended(d, m, at):
            harvest.add(mid)
            m["todos_at"] = at.isoformat(timespec="seconds")
        else:
            m["todos_at"] = None

    existing = {t["key"]: t for t in day["todos"] if t.get("key")}
    dismissed = set(day.get("dismissed") or [])
    for t in payload.get("todos") or []:
        if not isinstance(t, dict):
            continue
        text = " ".join(str(t.get("text") or "").split())
        if not text:
            continue
        raw_mid = str(t.get("meeting_id") or "")
        mid = id_map.get(raw_mid) or (safe_id(raw_mid) if raw_mid else "")
        key = todo_key(text)
        if mid not in harvest or key in dismissed:
            continue
        fields = {"meeting_id": mid, "priority": clean_priority(t.get("priority")),
                  "due": clean_due(t.get("due")), "context": str(t.get("context") or "").strip()}
        cur = existing.get(key)
        if cur:
            cur.update(fields)
            if not cur.get("edited"):
                cur["text"] = text
        else:
            new = {"id": "s-" + key, "key": key, "text": text, "done": False, "done_at": None,
                   "origin": "sync", "created_at": now_iso()}
            new.update(fields)
            day["todos"].append(new)
            existing[key] = new

    if payload.get("summary"):
        day["summary"] = str(payload["summary"]).strip()
    day["synced_at"] = payload.get("synced_at") or now_iso()
    sources = set(day.get("sources") or [])
    sources.update(str_list(payload.get("sources")))
    if payload.get("source"):
        sources.add(str(payload["source"]))
    day["sources"] = sorted(sources)
    day["warnings"] = str_list(payload.get("warnings"))
    save_day(day)
    return d


def ingest_inbox():
    """Merge every finished inbox file. Caller holds the lock."""
    merged, problems = [], []
    if not os.path.isdir(INBOX):
        return merged, problems
    for name in sorted(os.listdir(INBOX)):
        if not (name.endswith(".json") and DATE_RE.match(name[:-5])):
            continue
        path = os.path.join(INBOX, name)
        try:
            payload = read_json(path, None)
            if not isinstance(payload, dict):
                raise ValueError("not a JSON object")
            payload.setdefault("date", name[:-5])
            merged.append(merge_payload(payload))
            os.replace(path, os.path.join(PROCESSED, name))
        except (ValueError, KeyError, TypeError) as e:
            # The sync task may still be writing; give it a minute before calling it broken.
            if time.time() - os.path.getmtime(path) < 60:
                continue
            os.replace(path, os.path.join(PROCESSED, name[:-5] + ".bad.json"))
            problems.append("%s: %s" % (name, e))
    return merged, problems


# ---------- Notion tickets ----------

def find_todo(pages, date, todo_id):
    """(day, to-do) for todo_id: on `date` first, then on any page (it may have moved since).
    `pages` caches loaded days by date so the caller can save the ones it changed."""
    dates = ([date] if isinstance(date, str) and DATE_RE.match(date) else []) + all_dates()
    for d in dates:
        if d not in pages:
            pages[d] = load_day(d)
        for t in pages[d]["todos"]:
            if t.get("id") == todo_id:
                return pages[d], t
    return None, None


def ingest_notion():
    """Stamp ticket links from finished Notion tasks onto their to-dos, and give up on requests that
    never reported back so they can be sent again. Caller holds the lock."""
    problems, pages, changed = [], {}, set()
    for name in sorted(os.listdir(NOTION_OUT)) if os.path.isdir(NOTION_OUT) else []:
        if not name.endswith(".json"):
            continue
        path = os.path.join(NOTION_OUT, name)
        try:
            payload = read_json(path, None)
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ValueError("no results list")
        except ValueError as e:
            if time.time() - os.path.getmtime(path) < 60:
                continue
            os.replace(path, os.path.join(NOTION_DONE, name[:-5] + ".bad.json"))
            problems.append("Notion result %s: %s" % (name, e))
            continue
        for r in payload["results"]:
            if not isinstance(r, dict):
                continue
            day, t = find_todo(pages, r.get("date"), str(r.get("todo_id") or ""))
            if not t:
                continue
            url = str(r.get("url") or "").strip()
            if url.startswith(("https://", "http://")):
                t["notion"] = {"status": "created", "url": url, "at": now_iso()}
            else:
                t["notion"] = {"status": "failed", "at": now_iso(),
                               "error": str(r.get("error") or "No ticket link came back")}
            changed.add(day["date"])
        os.replace(path, os.path.join(NOTION_DONE, name))

    cutoff = time.time() - NOTION_STALE_S
    for d in all_dates():
        day = pages.get(d) or load_day(d)
        for t in day["todos"]:
            n = t.get("notion") or {}
            at = _parse_ts(n.get("at"))
            if n.get("status") == "queued" and (at is None or at < cutoff):
                t["notion"] = {"status": "failed", "at": now_iso(),
                               "error": "The Notion task never reported back — send it again"}
                pages[d] = day
                changed.add(d)
    for d in changed:
        save_day(pages[d])
    return problems


def _parse_ts(s):
    try:
        return datetime.fromisoformat(str(s)).timestamp()
    except ValueError:
        return None


# ---------- settings / sync log ----------

def load_settings():
    saved = read_json(SETTINGS, {}) or {}
    s = dict(DEFAULT_SETTINGS)
    s.update(saved)
    s["notion"] = dict(NOTION_DEFAULTS, **(saved.get("notion") or {}))
    return s


def load_sync_log():
    log = read_json(SYNC_LOG, None) or {}
    log.setdefault("requests", [])
    log.setdefault("last_request", {})
    return log
