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
#
# DUPLICATE-POST PROTECTION
# --------------------------
# Publishing a video can take a minute or two. If a second check (e.g.
# the next cron ping) starts before the first one has finished and
# saved "uploaded: true" back to Drive, both would see the idea as
# still pending and publish it twice. To prevent that, as soon as an
# idea is found due, we immediately mark it with a "publishInProgress"
# timestamp and save that to Drive BEFORE calling Facebook — any
# overlapping check will then see it's already claimed and skip it.
# A stale claim (e.g. a crash mid-publish) expires after 10 minutes so
# it isn't stuck forever. A threading.Lock also prevents two checks
# from running at the same time within the same backend process.
# =====================================================================

import os
import json
import datetime
import threading

import drive_helpers as dh
import facebook as fb
import store
import media_token

GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
USERS = json.loads(os.environ.get("APP_USERS_JSON", "{}"))
BACKEND_PUBLIC_URL = os.environ["BACKEND_PUBLIC_URL"]  # e.g. https://yourname.pythonanywhere.com

PKT_OFFSET = datetime.timedelta(hours=5)
CLAIM_TIMEOUT = datetime.timedelta(minutes=10)

_run_lock = threading.Lock()


def now_pkt():
    return datetime.datetime.utcnow() + PKT_OFFSET


def is_due(idea, today_str, now_hm, force_all, now):
    if idea.get("uploaded"):
        return False
    if idea.get("date") != today_str:
        return False

    claimed_at = idea.get("publishInProgress")
    if claimed_at:
        try:
            started = datetime.datetime.fromisoformat(claimed_at)
            if (now - started) < CLAIM_TIMEOUT:
                return False  # another check is already handling this one
        except Exception:
            pass  # malformed timestamp — treat as not claimed

    if force_all:
        return True
    idea_time = idea.get("time") or "00:00"
    return idea_time <= now_hm


def media_url(username, file_id):
    token = media_token.make_media_token(username, file_id)
    return f"{BACKEND_PUBLIC_URL}/api/public/media/{file_id}?token={token}"


def publish_idea(username, ids, link, idea):
    """Does the actual Facebook publish + Drive file move. Assumes the
    caller has already claimed (and saved) this idea. Raises on failure —
    caller is responsible for clearing the claim if that happens."""
    caption = " ".join(filter(None, [idea.get("title"), idea.get("description"), idea.get("hashtags")]))

    if idea.get("videoFileId"):
        url = media_url(username, idea["videoFileId"])
        fb.publish_reel(link["fb_page_id"], link["fb_page_token"], url, caption)
    elif idea.get("thumbFileId"):
        url = media_url(username, idea["thumbFileId"])
        fb.publish_photo(link["fb_page_id"], link["fb_page_token"], url, caption)
    else:
        raise Exception("This idea has no video or thumbnail to publish.")

    idea["uploaded"] = True
    idea["uploadedAt"] = now_pkt().date().isoformat()
    idea["publishInProgress"] = None
    args = (username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
    if idea.get("thumbFileId"):
        dh.move_file(*args, idea["thumbFileId"], ids["pendingFolderId"], ids["uploadedFolderId"])
    if idea.get("videoFileId"):
        dh.move_file(*args, idea["videoFileId"], ids["pendingFolderId"], ids["uploadedFolderId"])


def run_check(force_all=False):
    """Returns a list of human-readable log lines describing what happened."""
    if not _run_lock.acquire(blocking=False):
        return ["Another check is already running in this process — skipped."]

    try:
        n = now_pkt()
        today_str = n.date().isoformat()
        now_hm = n.strftime("%H:%M")
        log = [f"Check at {today_str} {now_hm} PKT (force_all={force_all})"]

        for username in USERS:
            try:
                ids = dh.get_folder_ids(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
            except Exception as e:
                log.append(f"{username}: could not reach Drive ({e})")
                continue

            links = store.get_all_links(username)
            if not links:
                continue

            try:
                data = dh.load_data(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, ids["dataFileId"])
            except Exception as e:
                log.append(f"{username}: could not load data.json ({e})")
                continue

            for page in data.get("pages", []):
                link = links.get(page["id"])
                if not link:
                    continue
                for idea in page.get("ideas", []):
                    if not is_due(idea, today_str, now_hm, force_all, n):
                        continue

                    # --- CLAIM: mark + save immediately, before publishing ---
                    idea["publishInProgress"] = n.isoformat()
                    try:
                        dh.save_data(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, ids["dataFileId"], data)
                    except Exception as e:
                        log.append(f"{username}/{page['name']}: could not claim '{idea.get('title')}' ({e}), skipping this round")
                        continue

                    # --- PUBLISH ---
                    try:
                        publish_idea(username, ids, link, idea)
                        log.append(f"{username}/{page['name']}: published '{idea.get('title')}'")
                    except Exception as e:
                        idea["publishInProgress"] = None
                        log.append(f"{username}/{page['name']}: FAILED '{idea.get('title')}' — {e}")

                    # --- SAVE final state (success or failure) ---
                    try:
                        dh.save_data(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, ids["dataFileId"], data)
                    except Exception as e:
                        log.append(f"{username}/{page['name']}: could not save final state ({e})")

        return log
    finally:
        _run_lock.release()
