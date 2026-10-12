---
name: notetaker-meeting-sync
description: Meeting notebook — one page per day with the to-dos pulled from that day's meetings (Wispr Flow via a Claude sync task). Read a day, tick or add to-dos.
approve: [todos.py, chat.py, notion.py]
---
# Meeting Notebook

One notebook page per day in `.fused/data/days/<YYYY-MM-DD>.json`: the meetings of that day (summary,
discussed, decisions) and the to-dos the user owns. A Claude sync task reads Wispr Flow (falling back to
Circleback / Notion / Google Calendar) and writes `.fused/data/inbox/<date>.json`; `notebook.py` merges
that into the day page on its next call, keeping ticks, edits and hand-added to-dos. To-dos are taken from
each meeting once — on the first sync after it has ended with notes (stamped `todos_at` on the meeting); later
syncs refresh the meeting but ignore its to-dos, so deleted or reworded ones never come back.

To-dos the user picks by hand can become tickets in the Notion "Engineering Tasks" database: `notion.py` marks
them queued and returns a Claude task prompt; that task (Notion connector) writes `.fused/data/notion/<request>.json`
and `notebook.py` stamps each to-do's `notion` field (`{"status": "queued" | "created" | "failed", "url"?, "error"?}`).

## notebook.py
Returns the page index and one day page. Merges any finished sync and Notion results first (writes `.fused/data/days/`).
- Args: `date` (str, "YYYY-MM-DD", default "" = index only)
- Returns: `{"today": str, "index": [{"date", "summary", "synced_at", "n_meetings", "meetings": [...], "todos": [{"id", "text", "done", "priority", "due", "meeting_id", "meeting_title", "notion"}]}], "day": {"date", "summary", "meetings": [...], "todos": [...], "synced_at", "sources", "warnings"} | null, "merged": [str], "problems": [str], "inbox_pending": [str], "settings": {...}, "sync": {...}}`
- Example: `{"action":"py","app":"notetaker-meeting-sync","file":"notebook.py","args":{"date":"2026-10-07"}}`

## todos.py
Changes one to-do on one day page. Writes `.fused/data/days/<date>.json` (and the target day for `move`).
- Args: `date` (str, required), `op` (str, required: "add" | "done" | "edit" | "delete" | "move"), `todo_id` (str, required except for add), `text` (str, add/edit), `meeting_id` (str, add, default ""), `done` (bool, for op=done, default false), `priority` (str "high"|"medium"|"low", default ""), `due` (str "YYYY-MM-DD", default ""), `to_date` (str, for move)
- Returns: `{"day": {...}, "index": [...]}`
- Example: `{"action":"py","app":"notetaker-meeting-sync","file":"todos.py","args":{"date":"2026-10-07","op":"add","text":"Send Vasu the pricing deck"}}`

## sync.py
Plans a meeting sync and returns the Claude task prompt; also stores the auto-sync settings. Writes `.fused/data/sync.json` (request log) and `.fused/data/settings.json`. It does not start the task itself — the page does that with `fused.tasks.create`.
- Args: `op` (str, default "plan": "plan" | "request" | "mark" | "settings"), `dates` (str, comma-separated "YYYY-MM-DD", for request), `request_id` (str, for mark), `entry_id` (str, for mark), `auto_sync` (str "true"/"false", for settings), `interval_min` (int, for settings, default 0 = unchanged)
- Returns: plan/request → `{"dates": [str], "prompt": str, "request_id": str | null}`; settings → `{"settings": {"auto_sync": bool, "interval_min": int}}`; mark → `{"ok": true}`
- Example: `{"action":"py","app":"notetaker-meeting-sync","file":"sync.py","args":{"op":"request","dates":"2026-10-07"}}`

## notion.py
Queues hand-picked to-dos for Notion tickets and returns the Claude task prompt that creates them; `cancel` un-queues a request whose task never started. Writes the `notion` field on to-dos in `.fused/data/days/`. It does not start the task itself — the page does that with `fused.tasks.create`. To-dos that already have a ticket or are queued are skipped.
- Args: `op` (str, default "request": "request" | "cancel"), `items` (str, comma-separated "YYYY-MM-DD:todo_id", for request), `request_id` (str, for cancel)
- Returns: request → `{"count": int, "skipped": int, "request_id": str | null, "database": str, "prompt": str, "index": [...]}`; cancel → `{"ok": true, "index": [...]}`
- Example: `{"action":"py","app":"notetaker-meeting-sync","file":"notion.py","args":{"op":"request","items":"2026-10-07:s-1a2b3c4d5e6f"}}`

## chat.py
Reads or extends the saved conversation with one meeting; `get` also returns the meeting record, its to-dos and transcript. `append`/`clear` write `.fused/data/chats/<meeting>.json`.
- Args: `date` (str, required), `meeting_id` (str, required), `op` (str, default "get": "get" | "append" | "clear"), `role` (str "user"|"assistant", for append), `content` (str, for append)
- Returns: get → `{"meeting": {...}, "todos": [...], "transcript": str, "messages": [{"role", "content", "at"}]}`; append/clear → `{"messages": [...]}`
- Example: `{"action":"py","app":"notetaker-meeting-sync","file":"chat.py","args":{"date":"2026-10-07","meeting_id":"gcal-i29k9egs9q2bnmra7ju5bjua64_20261007T101500Z"}}`
