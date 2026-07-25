# =====================================================================
# Reel Room — Scheduler core
#
# Shared by:
#  - app.py's /api/scheduler/run endpoint (called every 15-30 min by a
#    free external pinger like cron-job.org — handles TIME-precise posts)
#  - daily_publish.py (runs once a day as an end-of-day safety net —
#    catches anything with today's date that somehow wasn't published
#    yet, regardless of time)
#
# All "now" calculations use Pakistan Standard Time (UTC+5, no DST),
# since that's where this deployment's ideas are scheduled from.
# =====================================================================

import os
import json
import datetime

import drive_helpers as dh
import facebook as fb
import store

GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
USERS = json.loads(os.environ.get("APP_USERS_JSON", "{}"))

PKT_OFFSET = datetime.timedelta(hours=5)


def now_pkt():
    return datetime.datetime.utcnow() + PKT_OFFSET


def is_due(idea, today_str, now_hm, force_all):
    if idea.get("uploaded"):
        return False
    if idea.get("date") != today_str:
        return False
    if force_all:
        return True
    idea_time = idea.get("time") or "00:00"
    return idea_time <= now_hm


def publish_idea(username, ids, link, idea):
    args = (username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
    caption = " ".join(filter(None, [idea.get("title"), idea.get("description"), idea.get("hashtags")]))

    if idea.get("videoFileId"):
        perm_id = dh.make_file_public(*args, idea["videoFileId"])
        try:
            url = dh.public_download_link(idea["videoFileId"])
            fb.publish_reel(link["fb_page_id"], link["fb_page_token"], url, caption)
        finally:
            dh.revoke_public_permission(*args, idea["videoFileId"], perm_id)
    elif idea.get("thumbFileId"):
        perm_id = dh.make_file_public(*args, idea["thumbFileId"])
        try:
            url = dh.public_download_link(idea["thumbFileId"])
            fb.publish_photo(link["fb_page_id"], link["fb_page_token"], url, caption)
        finally:
            dh.revoke_public_permission(*args, idea["thumbFileId"], perm_id)
    else:
        raise Exception("This idea has no video or thumbnail to publish.")

    idea["uploaded"] = True
    idea["uploadedAt"] = now_pkt().date().isoformat()
    if idea.get("thumbFileId"):
        dh.move_file(*args, idea["thumbFileId"], ids["pendingFolderId"], ids["uploadedFolderId"])
    if idea.get("videoFileId"):
        dh.move_file(*args, idea["videoFileId"], ids["pendingFolderId"], ids["uploadedFolderId"])


def run_check(force_all=False):
    """Returns a list of human-readable log lines describing what happened."""
    n = now_pkt()
    today_str = n.date().isoformat()
    now_hm = n.strftime("%H:%M")
    log = [f"Check at {today_str} {now_hm} PKT (force_all={force_all})"]

    for username in USERS:
        try:
            ids = dh.get_folder_ids(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
            data = dh.load_data(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, ids["dataFileId"])
        except Exception as e:
            log.append(f"{username}: could not load Drive data ({e})")
            continue

        links = store.get_all_links(username)
        if not links:
            continue

        changed = False
        for page in data.get("pages", []):
            link = links.get(page["id"])
            if not link:
                continue
            for idea in page.get("ideas", []):
                if not is_due(idea, today_str, now_hm, force_all):
                    continue
                try:
                    publish_idea(username, ids, link, idea)
                    changed = True
                    log.append(f"{username}/{page['name']}: published '{idea.get('title')}'")
                except Exception as e:
                    log.append(f"{username}/{page['name']}: FAILED '{idea.get('title')}' — {e}")

        if changed:
            dh.save_data(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, ids["dataFileId"], data)

    return log