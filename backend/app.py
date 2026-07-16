# =====================================================================
# Reel Room — Backend Proxy
#
# Purpose: let a restricted PC (no Google sign-in, no downloads allowed)
# use the site with a plain username + password. This server holds a
# Google refresh token per user (set up once, from an unrestricted
# device — see setup_helper.py) and does ALL the Google Drive talking
# on the user's behalf. The restricted PC's browser only ever talks to
# THIS server's domain — never google.com.
#
# Config comes entirely from environment variables (set on Render's
# dashboard), so nothing sensitive is ever committed to GitHub:
#
#   APP_USERS_JSON      JSON object: {"username": {"password_hash": "...",
#                        "refresh_token": "..."}, ...}
#   GOOGLE_CLIENT_ID     Same OAuth client you already created
#   GOOGLE_CLIENT_SECRET From the same OAuth client (Credentials page)
#   JWT_SECRET           Any long random string (session signing key)
#   ALLOWED_ORIGIN        Your GitHub Pages origin, e.g.
#                        https://aliasghar1029.github.io
# =====================================================================

import os
import json
import time
import datetime

import bcrypt
import jwt
import requests
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS

app = Flask(__name__)

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGIN}}, supports_credentials=False)

JWT_SECRET = os.environ["JWT_SECRET"]
GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
USERS = json.loads(os.environ.get("APP_USERS_JSON", "{}"))

DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
TOKEN_URL = "https://oauth2.googleapis.com/token"
APP_FOLDER_NAME = "Reel Room Data"
DATA_FILE_NAME = "reel-room-data.json"

# In-memory caches (fine to lose on restart — everything is re-derived
# idempotently from Drive, nothing is lost).
_access_token_cache = {}   # username -> {"token": ..., "expires_at": ...}
_folder_cache = {}         # username -> {folderId, pendingFolderId, uploadedFolderId, dataFileId}


# ---------------------------------------------------------------
# AUTH HELPERS
# ---------------------------------------------------------------
def make_jwt(username):
    payload = {"u": username, "exp": datetime.datetime.utcnow() + datetime.timedelta(days=14)}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def current_username():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[len("Bearer "):]
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


def get_access_token(username):
    cached = _access_token_cache.get(username)
    if cached and cached["expires_at"] > time.time() + 30:
        return cached["token"]

    refresh_token = USERS[username]["refresh_token"]
    resp = requests.post(TOKEN_URL, data={
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    resp.raise_for_status()
    body = resp.json()
    token = body["access_token"]
    expires_in = body.get("expires_in", 3600)
    _access_token_cache[username] = {"token": token, "expires_at": time.time() + expires_in}
    return token


# ---------------------------------------------------------------
# DRIVE HELPERS (server-side — mirrors the old client-side logic)
# ---------------------------------------------------------------
def drive_headers(username):
    return {"Authorization": f"Bearer {get_access_token(username)}"}


def find_or_create_folder(username, name, parent_id):
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false"
    r = requests.get(DRIVE_FILES, headers=drive_headers(username), params={"q": q, "fields": "files(id,name)"})
    r.raise_for_status()
    files = r.json().get("files", [])
    if files:
        return files[0]["id"]

    r = requests.post(
        DRIVE_FILES, headers={**drive_headers(username), "Content-Type": "application/json"},
        json={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
    )
    r.raise_for_status()
    return r.json()["id"]


def find_or_create_data_file(username, folder_id):
    q = f"name='{DATA_FILE_NAME}' and '{folder_id}' in parents and trashed=false"
    r = requests.get(DRIVE_FILES, headers=drive_headers(username), params={"q": q, "fields": "files(id,name)"})
    r.raise_for_status()
    files = r.json().get("files", [])
    if files:
        return files[0]["id"]

    metadata = {"name": DATA_FILE_NAME, "parents": [folder_id], "mimeType": "application/json"}
    files_payload = {
        "metadata": (None, json.dumps(metadata), "application/json"),
        "file": (None, json.dumps({"pages": []}), "application/json"),
    }
    r = requests.post(
        f"{DRIVE_UPLOAD}?uploadType=multipart&fields=id",
        headers=drive_headers(username), files=files_payload,
    )
    r.raise_for_status()
    return r.json()["id"]


def get_folder_ids(username):
    if username in _folder_cache:
        return _folder_cache[username]
    folder_id = find_or_create_folder(username, APP_FOLDER_NAME, "root")
    pending_id = find_or_create_folder(username, "Pending Reels", folder_id)
    uploaded_id = find_or_create_folder(username, "Uploaded Reels", folder_id)
    data_file_id = find_or_create_data_file(username, folder_id)
    ids = {
        "folderId": folder_id, "pendingFolderId": pending_id,
        "uploadedFolderId": uploaded_id, "dataFileId": data_file_id,
    }
    _folder_cache[username] = ids
    return ids


# ---------------------------------------------------------------
# ROUTES
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
    ids = get_folder_ids(username)
    r = requests.get(f"{DRIVE_FILES}/{ids['dataFileId']}", headers=drive_headers(username), params={"alt": "media"})
    r.raise_for_status()
    return jsonify({**ids, "data": r.json()})


@app.route("/api/data", methods=["PUT"])
def save_data():
    username = require_user()
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    ids = get_folder_ids(username)
    body = request.get_data()
    r = requests.patch(
        f"{DRIVE_UPLOAD}/{ids['dataFileId']}?uploadType=media",
        headers={**drive_headers(username), "Content-Type": "application/json"}, data=body,
    )
    r.raise_for_status()
    return jsonify({"ok": True})


@app.route("/api/upload", methods=["POST"])
def upload():
    username = require_user()
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    ids = get_folder_ids(username)
    target = request.form.get("target", "pending")
    folder_id = ids["uploadedFolderId"] if target == "uploaded" else ids["pendingFolderId"]

    f = request.files["file"]
    metadata = {"name": f"{int(time.time()*1000)}_{f.filename}", "parents": [folder_id]}
    files_payload = {
        "metadata": (None, json.dumps(metadata), "application/json"),
        "file": (f.filename, f.stream, f.mimetype),
    }
    r = requests.post(
        f"{DRIVE_UPLOAD}?uploadType=multipart&fields=id,mimeType",
        headers=drive_headers(username), files=files_payload,
    )
    r.raise_for_status()
    return jsonify(r.json())


@app.route("/api/move", methods=["POST"])
def move_file():
    username = require_user()
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    ids = get_folder_ids(username)
    body = request.get_json(force=True)
    file_id = body["fileId"]
    to_uploaded = bool(body.get("toUploaded"))
    to_id = ids["uploadedFolderId"] if to_uploaded else ids["pendingFolderId"]
    from_id = ids["pendingFolderId"] if to_uploaded else ids["uploadedFolderId"]
    r = requests.patch(
        f"{DRIVE_FILES}/{file_id}", headers=drive_headers(username),
        params={"addParents": to_id, "removeParents": from_id, "fields": "id,parents"},
    )
    r.raise_for_status()
    return jsonify({"ok": True})


@app.route("/api/file/<file_id>", methods=["DELETE"])
def delete_file(file_id):
    username = require_user()
    if not username:
        return jsonify({"error": "Unauthorized"}), 401
    requests.delete(f"{DRIVE_FILES}/{file_id}", headers=drive_headers(username))
    return jsonify({"ok": True})


@app.route("/api/file/<file_id>", methods=["GET"])
def get_file(file_id):
    """Streams a thumbnail or video's raw bytes through OUR server, so the
    restricted PC's browser never has to open drive.google.com directly."""
    username = current_username()  # token may arrive as query param for <img>/<video> tags
    if not username:
        token = request.args.get("token", "")
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            username = payload.get("u")
        except jwt.PyJWTError:
            username = None
    if not username or username not in USERS:
        return jsonify({"error": "Unauthorized"}), 401

    upstream = requests.get(
        f"{DRIVE_FILES}/{file_id}", headers=drive_headers(username),
        params={"alt": "media"}, stream=True,
    )
    return Response(
        stream_with_context(upstream.iter_content(chunk_size=65536)),
        content_type=upstream.headers.get("Content-Type", "application/octet-stream"),
    )


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))