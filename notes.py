import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _store as S  # noqa: E402

OPS = ("get", "add", "edit", "delete", "discard")
MAX_NOTE = 20000
MAX_IMAGES = 12


def _clean(text, allow_empty=False):
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text and not allow_empty:
        raise ValueError("text is required")
    if len(text) > MAX_NOTE:
        raise ValueError("a note can be at most %d characters" % MAX_NOTE)
    return text


def _find(day, todo_id):
    for t in day["todos"]:
        if t.get("id") == todo_id:
            return t
    raise ValueError("no to-do %r on %s — it may have been moved or deleted" % (todo_id, day["date"]))


def _parse_images(images):
    if not images.strip():
        return []
    try:
        items = json.loads(images)
    except ValueError:
        raise ValueError("images must be a JSON list")
    if not isinstance(items, list):
        raise ValueError("images must be a JSON list")
    out = []
    for it in items:
        it = {"id": it} if isinstance(it, str) else it
        if isinstance(it, dict) and it.get("id"):
            out.append(it)
    if len(out) > MAX_IMAGES:
        raise ValueError("a note can hold at most %d images" % MAX_IMAGES)
    return out


def _dim(v):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return v if 0 < v <= 100000 else None


def _new_image(it):
    """Validate one freshly uploaded file in attachments/ and describe it."""
    path = S.image_path(str(it["id"]))
    if not os.path.isfile(path):
        raise ValueError("image %s was not uploaded" % it["id"])
    size = os.path.getsize(path)
    kind = S.sniff_image(path)
    if size > S.MAX_IMAGE_BYTES or kind is None or not it["id"].endswith("." + kind):
        os.unlink(path)
        raise ValueError("%s is not a PNG, JPEG, GIF or WebP image under 20 MB" % (it.get("name") or it["id"]))
    name = " ".join(str(it.get("name") or "").split())[:120] or "Image." + kind
    rec = {"id": it["id"], "name": name, "type": S.IMAGE_TYPES[kind], "size": size}
    w, h = _dim(it.get("w")), _dim(it.get("h"))
    if w and h:
        rec.update(w=w, h=h)
    return rec


def _images(wanted, current):
    have = {i["id"]: i for i in current or []}
    return [have[it["id"]] if it["id"] in have else _new_image(it) for it in wanted]


def main(date: str, todo_id: str, op: str = "get", note_id: str = "", text: str = "", images: str = ""):
    """Read or change the notes thread kept inside one to-do."""
    date = S.check_date(date)
    if op not in OPS:
        raise ValueError("op must be one of %s" % ", ".join(OPS))
    with S.locked():
        if op == "discard":
            S.drop_images([it["id"] for it in _parse_images(images)], keep=S.referenced_images())
            return {"ok": True}
        day = S.load_day(date)
        t = _find(day, todo_id)
        notes = list(t.get("notes") or [])
        gone = set()
        if op == "add":
            imgs = _images(_parse_images(images), [])
            note = {"id": "n-" + uuid.uuid4().hex[:10], "text": _clean(text, allow_empty=bool(imgs)), "at": S.now_iso()}
            if imgs:
                note["images"] = imgs
            notes.append(note)
        elif op in ("edit", "delete"):
            n = next((n for n in notes if n.get("id") == note_id), None)
            if n is None:
                raise ValueError("no note %r on this to-do" % note_id)
            if op == "edit":
                before = n.get("images") or []
                imgs = _images(_parse_images(images), before) if images.strip() else before
                n["text"] = _clean(text, allow_empty=bool(imgs))
                if imgs:
                    n["images"] = imgs
                else:
                    n.pop("images", None)
                n["edited_at"] = S.now_iso()
                gone = S.note_images([{"images": before}]) - S.note_images([n])
            else:
                notes.remove(n)
                gone = S.note_images([n])
        if op != "get":
            if notes:
                t["notes"] = notes
            else:
                t.pop("notes", None)
            S.save_day(day)
            S.drop_images(gone)
            if op == "add":
                S.sweep_images()
    m = next((m for m in day["meetings"] if m.get("id") == t.get("meeting_id")), None)
    meeting = {k: m.get(k) for k in ("id", "title", "start", "end", "url", "source")} if m else None
    return {"date": date, "todo": t, "meeting": meeting, "index": S.days_index() if op != "get" else None}
