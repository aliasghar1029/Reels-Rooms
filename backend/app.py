# =====================================================================
# Reel Room — Backend Proxy
#
# Two jobs:
#  1) Let a restricted PC use the site via username+password instead of
#     Google sign-in (proxies all Drive calls — unchanged from before).
#  2) Let each internal "page" be linked to a real Facebook Page, so the
#     daily_publish.py script can auto-post Reels/photos on their
#     scheduled date. See FACEBOOK_SETUP_GUIDE.md.
#
# Environment variables (set on PythonAnywhere / Render dashboard):
#   APP_USERS_JSON        {"username": {"password_hash": "...", "refresh_token": "..."}}
#   GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET   same OAuth client as the site
#   JWT_SECRET             any long random string
#   ALLOWED_ORIGIN          your GitHub Pages origin
#   FACEBOOK_APP_ID / FACEBOOK_APP_SECRET     from developers.facebook.com
#   FACEBOOK_REDIRECT_URI    e.g. https://yourname.pythonanywhere.com/api/facebook/callback
# =====================================================================

import os
import time
import datetime
import threading

import bcrypt
import jwt
import requests
from flask import Flask, request, jsonify, Response, stream_with_context, redirect
from flask_cors import CORS

import drive_helpers as dh
import facebook as fb
import store
import scheduler_core
import media_token

app = Flask(__name__)

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGIN}}, supports_credentials=False)

JWT_SECRET = os.environ["JWT_SECRET"]
GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
import json as _json
USERS = _json.loads(os.environ.get("APP_USERS_JSON", "{}"))

FACEBOOK_APP_ID = os.environ.get("FACEBOOK_APP_ID", "")
FACEBOOK_APP_SECRET = os.environ.get("FACEBOOK_APP_SECRET", "")
FACEBOOK_REDIRECT_URI = os.environ.get("FACEBOOK_REDIRECT_URI", "")
SCHEDULER_SECRET = os.environ.get("SCHEDULER_SECRET", "")

# Short-lived cache: state token -> list of pages the user just granted
# access to, while they pick which one to link (a few minutes is plenty).
_pending_fb_pages = {}


# ---------------------------------------------------------------
# AUTH HELPERS
# ---------------------------------------------------------------
def make_jwt(username):
    payload = {"u": username, "exp": datetime.datetime.utcnow() + datetime.timedelta(days=14)}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def current_username():
    auth = request.headers.get("Authorization", "")
    token = auth[len("Bearer "):] if auth.startswith("Bearer ") else request.args.get("token", "")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload.get("u")
    except jwt.PyJWTError:
        return None


def require_user():
    username = current_username()
    if not username or username not in USERS:
        return None
    return username


def drive_headers(username):
    return {"Authorization": f"Bearer {dh.get_access_token(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)}"}


# ---------------------------------------------------------------
# EXISTING ROUTES — Drive proxy (unchanged behavior)
# ---------------------------------------------------------------
@app.route("/api/login", methods=["POST"])
def login():
    body = request.get_json(force=True, silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    user = USERS.get(username)
    if not user:
        return jsonify({"error": "Invalid username or password"}), 401
    if not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        return jsonify({"error": "Invalid username or password"}), 401
    return jsonify({"token": make_jwt(username)})


@app.route("/api/bootstrap", methods=["GET"])
def bootstrap():
    username = require_user()
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    ids = dh.get_folder_ids(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
    data = dh.load_data(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, ids["dataFileId"])
    return jsonify({**ids, "data": data})


@app.route("/api/data", methods=["PUT"])
def save_data_route():
    username = require_user()
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    ids = dh.get_folder_ids(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
    body = request.get_json(force=True)
    dh.save_data(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, ids["dataFileId"], body)
    return jsonify({"ok": True})


@app.route("/api/upload", methods=["POST"])
def upload():
    username = require_user()
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    ids = dh.get_folder_ids(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
    target = request.form.get("target", "pending")
    folder_id = ids["uploadedFolderId"] if target == "uploaded" else ids["pendingFolderId"]

    f = request.files["file"]
    metadata = {"name": f"{int(time.time()*1000)}_{f.filename}", "parents": [folder_id]}
    files_payload = {
        "metadata": (None, _json.dumps(metadata), "application/json"),
        "file": (f.filename, f.stream, f.mimetype),
    }
    r = requests.post(
        f"{dh.DRIVE_UPLOAD}?uploadType=multipart&fields=id,mimeType",
        headers=drive_headers(username), files=files_payload,
    )
    r.raise_for_status()
    return jsonify(r.json())


@app.route("/api/move", methods=["POST"])
def move_file_route():
    username = require_user()
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    ids = dh.get_folder_ids(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
    body = request.get_json(force=True)
    to_uploaded = bool(body.get("toUploaded"))
    to_id = ids["uploadedFolderId"] if to_uploaded else ids["pendingFolderId"]
    from_id = ids["pendingFolderId"] if to_uploaded else ids["uploadedFolderId"]
    dh.move_file(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, body["fileId"], from_id, to_id)
    return jsonify({"ok": True})


@app.route("/api/file/<file_id>", methods=["DELETE"])
def delete_file(file_id):
    username = require_user()
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    requests.delete(f"{dh.DRIVE_FILES}/{file_id}", headers=drive_headers(username))
    return jsonify({"ok": True})


@app.route("/api/file/<file_id>", methods=["GET"])
def get_file(file_id):
    username = current_username()
    if not username or username not in USERS:
        return jsonify({"error": "Unauthorized"}), 401
    upstream = requests.get(f"{dh.DRIVE_FILES}/{file_id}", headers=drive_headers(username), params={"alt": "media"}, stream=True)
    return Response(
        stream_with_context(upstream.iter_content(chunk_size=65536)),
        content_type=upstream.headers.get("Content-Type", "application/octet-stream"),
    )


@app.route("/api/public/media/<file_id>", methods=["GET"])
def public_media(file_id):
    """Facebook's servers fetch the actual video/photo bytes from here.
    Protected by a signed, short-lived token (not a login) — Facebook
    can't send an Authorization header, so this can't require a JWT."""
    result = media_token.verify_media_token(request.args.get("token", ""), expected_file_id=file_id)
    if not result:
        return jsonify({"error": "Invalid or expired link"}), 403
    username, _ = result
    upstream = requests.get(
        f"{dh.DRIVE_FILES}/{file_id}", headers=drive_headers(username),
        params={"alt": "media"}, stream=True,
    )
    return Response(
        stream_with_context(upstream.iter_content(chunk_size=65536)),
        content_type=upstream.headers.get("Content-Type", "application/octet-stream"),
    )


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


# ---------------------------------------------------------------
# NEW ROUTES — Facebook Page connection
# ---------------------------------------------------------------
@app.route("/api/facebook/connect-url", methods=["GET"])
def facebook_connect_url():
    """Frontend opens this in a new tab. state = '<username>:<internalPageId>'."""
    username = require_user()
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    internal_page_id = request.args.get("pageId", "")
    state = f"{username}:{internal_page_id}"
    url = fb.build_login_url(FACEBOOK_APP_ID, FACEBOOK_REDIRECT_URI, state)
    return jsonify({"url": url})


@app.route("/api/facebook/callback", methods=["GET"])
def facebook_callback():
    """Facebook redirects here after the user approves access. We fetch
    their Pages and show a plain HTML picker (this runs in the popped-up
    tab, outside the main single-page app)."""
    code = request.args.get("code")
    state = request.args.get("state", "")
    username, _, internal_page_id = state.partition(":")

    short_token = fb.exchange_code_for_user_token(FACEBOOK_APP_ID, FACEBOOK_APP_SECRET, FACEBOOK_REDIRECT_URI, code)
    long_token = fb.get_long_lived_user_token(FACEBOOK_APP_ID, FACEBOOK_APP_SECRET, short_token)
    pages = fb.list_managed_pages(long_token)

    _pending_fb_pages[state] = pages

    if not pages:
        return "<h2>No Facebook Pages found for this account. Make sure you're an admin of the Page and try again.</h2>"

    rows = "".join(
        f"""<form method="POST" action="/api/facebook/select-page" style="margin:8px 0;">
              <input type="hidden" name="state" value="{state}">
              <input type="hidden" name="fbPageId" value="{p['id']}">
              <button type="submit" style="padding:10px 16px;font-size:14px;">
                Use "{p['name']}"
              </button>
            </form>"""
        for p in pages
    )
    return f"""
    <html><body style="font-family:sans-serif;max-width:480px;margin:60px auto;">
      <h2>Choose a Facebook Page</h2>
      <p>This will be linked to your Reel Room page.</p>
      {rows}
    </body></html>
    """


@app.route("/api/facebook/select-page", methods=["POST"])
def facebook_select_page():
    state = request.form.get("state", "")
    fb_page_id = request.form.get("fbPageId", "")
    username, _, internal_page_id = state.partition(":")

    pages = _pending_fb_pages.get(state, [])
    chosen = next((p for p in pages if p["id"] == fb_page_id), None)
    if not chosen:
        return "<h2>Something went wrong — that page selection expired. Please try connecting again.</h2>"

    store.set_link(username, internal_page_id, chosen["id"], chosen["name"], chosen["access_token"])
    _pending_fb_pages.pop(state, None)
    return "<h2>Linked! You can close this tab and go back to Reel Room.</h2>"


@app.route("/api/facebook/status", methods=["GET"])
def facebook_status():
    username = require_user()
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(store.get_all_links(username))


@app.route("/api/facebook/unlink", methods=["POST"])
def facebook_unlink():
    username = require_user()
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(force=True)
    store.remove_link(username, body["pageId"])
    return jsonify({"ok": True})


@app.route("/api/facebook/publish-now", methods=["POST"])
def facebook_publish_now():
    """Manual 'Publish Now' button — publishes one idea immediately,
    reusing the same logic as the daily automation."""
    username = require_user()
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(force=True)
    internal_page_id = body["pageId"]
    idea_id = body["ideaId"]

    link = store.get_link(username, internal_page_id)
    if not link:
        return jsonify({"error": "This page isn't linked to a Facebook Page yet."}), 400

    ids = dh.get_folder_ids(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
    data = dh.load_data(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, ids["dataFileId"])
    page = next((p for p in data["pages"] if p["id"] == internal_page_id), None)
    idea = next((i for i in page["ideas"] if i["id"] == idea_id), None) if page else None
    if not idea:
        return jsonify({"error": "Idea not found."}), 404

    try:
        scheduler_core.publish_idea(username, ids, link, idea)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    dh.save_data(username, USERS, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, ids["dataFileId"], data)
    return jsonify({"ok": True})


@app.route("/api/scheduler/run", methods=["GET", "POST"])
def scheduler_run():
    """Pinged by free external cron services (cron-job.org). Protected by
    a shared secret so nobody else can trigger it.
    - Default (no mode param): time-precise check, runs every 15-30 min.
    - ?mode=safety: end-of-day catch-all, ignores each idea's time field.

    Publishing (downloading the video and uploading it to Facebook) can
    take longer than cron-job.org's timeout allows, so this responds
    IMMEDIATELY and does the actual work in a background thread — the
    cron ping never has to wait for it to finish."""
    if not SCHEDULER_SECRET or request.args.get("secret") != SCHEDULER_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    force_all = request.args.get("mode") == "safety"

    def worker():
        try:
            scheduler_core.run_check(force_all=force_all)
        except Exception as e:
            print(f"Background scheduler run failed: {e}")

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "started": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
