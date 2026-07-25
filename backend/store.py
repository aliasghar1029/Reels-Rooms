# =====================================================================
# Reel Room — tiny persistent store
# Just a JSON file on disk. PythonAnywhere's filesystem is persistent
# (unlike some free hosts), so this survives restarts/reloads.
# Structure:
#   { "<username>": { "<internal_page_id>": {
#         "fb_page_id": "...", "fb_page_name": "...", "fb_page_token": "..."
#   } } }
# =====================================================================

import json
import os
import threading

STORE_PATH = os.path.join(os.path.dirname(__file__), "fb_links.json")
_lock = threading.Lock()


def _read():
    if not os.path.exists(STORE_PATH):
        return {}
    with open(STORE_PATH, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _write(data):
    with open(STORE_PATH, "w") as f:
        json.dump(data, f, indent=2)


def get_link(username, page_id):
    with _lock:
        return _read().get(username, {}).get(page_id)


def get_all_links(username):
    with _lock:
        return _read().get(username, {})


def set_link(username, page_id, fb_page_id, fb_page_name, fb_page_token):
    with _lock:
        data = _read()
        data.setdefault(username, {})[page_id] = {
            "fb_page_id": fb_page_id,
            "fb_page_name": fb_page_name,
            "fb_page_token": fb_page_token,
        }
        _write(data)


def remove_link(username, page_id):
    with _lock:
        data = _read()
        if username in data and page_id in data[username]:
            del data[username][page_id]
            _write(data)