# =====================================================================
# Reel Room — Shared Google Drive helpers
# Used by both app.py (the web server) and daily_publish.py (the
# scheduled automation script), so the two never drift out of sync.
# =====================================================================

import time
import json
import requests

DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
TOKEN_URL = "https://oauth2.googleapis.com/token"
APP_FOLDER_NAME = "Reel Room Data"
DATA_FILE_NAME = "reel-room-data.json"

_access_token_cache = {}  # username -> {"token": ..., "expires_at": ...}
_folder_cache = {}        # username -> {folderId, pendingFolderId, uploadedFolderId, dataFileId}


def get_access_token(username, users_config, google_client_id, google_client_secret):
    cached = _access_token_cache.get(username)
    if cached and cached["expires_at"] > time.time() + 30:
        return cached["token"]

    refresh_token = users_config[username]["refresh_token"]
    resp = requests.post(TOKEN_URL, data={
        "client_id": google_client_id,
        "client_secret": google_client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    resp.raise_for_status()
    body = resp.json()
    token = body["access_token"]
    _access_token_cache[username] = {"token": token, "expires_at": time.time() + body.get("expires_in", 3600)}
    return token


def drive_headers(username, users_config, client_id, client_secret):
    return {"Authorization": f"Bearer {get_access_token(username, users_config, client_id, client_secret)}"}


def find_or_create_folder(username, users_config, client_id, client_secret, name, parent_id):
    h = drive_headers(username, users_config, client_id, client_secret)
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false"
    r = requests.get(DRIVE_FILES, headers=h, params={"q": q, "fields": "files(id,name)"})
    r.raise_for_status()
    files = r.json().get("files", [])
    if files:
        return files[0]["id"]
    r = requests.post(
        DRIVE_FILES, headers={**h, "Content-Type": "application/json"},
        json={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
    )
    r.raise_for_status()
    return r.json()["id"]


def find_or_create_data_file(username, users_config, client_id, client_secret, folder_id):
    h = drive_headers(username, users_config, client_id, client_secret)
    q = f"name='{DATA_FILE_NAME}' and '{folder_id}' in parents and trashed=false"
    r = requests.get(DRIVE_FILES, headers=h, params={"q": q, "fields": "files(id,name)"})
    r.raise_for_status()
    files = r.json().get("files", [])
    if files:
        return files[0]["id"]
    metadata = {"name": DATA_FILE_NAME, "parents": [folder_id], "mimeType": "application/json"}
    files_payload = {
        "metadata": (None, json.dumps(metadata), "application/json"),
        "file": (None, json.dumps({"pages": []}), "application/json"),
    }
    r = requests.post(f"{DRIVE_UPLOAD}?uploadType=multipart&fields=id", headers=h, files=files_payload)
    r.raise_for_status()
    return r.json()["id"]


def get_folder_ids(username, users_config, client_id, client_secret):
    if username in _folder_cache:
        return _folder_cache[username]
    folder_id = find_or_create_folder(username, users_config, client_id, client_secret, APP_FOLDER_NAME, "root")
    pending_id = find_or_create_folder(username, users_config, client_id, client_secret, "Pending Reels", folder_id)
    uploaded_id = find_or_create_folder(username, users_config, client_id, client_secret, "Uploaded Reels", folder_id)
    data_file_id = find_or_create_data_file(username, users_config, client_id, client_secret, folder_id)
    ids = {"folderId": folder_id, "pendingFolderId": pending_id, "uploadedFolderId": uploaded_id, "dataFileId": data_file_id}
    _folder_cache[username] = ids
    return ids


def load_data(username, users_config, client_id, client_secret, data_file_id):
    h = drive_headers(username, users_config, client_id, client_secret)
    r = requests.get(f"{DRIVE_FILES}/{data_file_id}", headers=h, params={"alt": "media"})
    r.raise_for_status()
    return r.json()


def save_data(username, users_config, client_id, client_secret, data_file_id, data):
    h = drive_headers(username, users_config, client_id, client_secret)
    r = requests.patch(
        f"{DRIVE_UPLOAD}/{data_file_id}?uploadType=media",
        headers={**h, "Content-Type": "application/json"}, data=json.dumps(data),
    )
    r.raise_for_status()


def move_file(username, users_config, client_id, client_secret, file_id, from_id, to_id):
    if not file_id:
        return
    h = drive_headers(username, users_config, client_id, client_secret)
    requests.patch(
        f"{DRIVE_FILES}/{file_id}", headers=h,
        params={"addParents": to_id, "removeParents": from_id, "fields": "id,parents"},
    )


def make_file_public(username, users_config, client_id, client_secret, file_id):
    """Temporarily grants 'anyone with the link can view' so Facebook's
    servers can fetch the file directly. Returns the permission id, so
    it can be revoked again afterwards."""
    h = drive_headers(username, users_config, client_id, client_secret)
    r = requests.post(
        f"{DRIVE_FILES}/{file_id}/permissions", headers={**h, "Content-Type": "application/json"},
        json={"role": "reader", "type": "anyone"},
    )
    r.raise_for_status()
    return r.json()["id"]


def revoke_public_permission(username, users_config, client_id, client_secret, file_id, permission_id):
    h = drive_headers(username, users_config, client_id, client_secret)
    requests.delete(f"{DRIVE_FILES}/{file_id}/permissions/{permission_id}", headers=h)


def public_download_link(file_id):
    return f"https://drive.google.com/uc?export=download&id={file_id}"