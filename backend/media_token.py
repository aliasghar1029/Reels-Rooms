# =====================================================================
# Reel Room — signed media tokens
#
# Facebook's servers need to fetch a video/image file directly. Rather
# than making the file "public" on Google Drive (which serves an HTML
# virus-scan warning page instead of the real file for anything not
# tiny — the exact bug that caused reels to silently fail), we stream
# the bytes through OUR OWN backend instead, at a temporary, signed URL
# that only Facebook (or anyone with the link, for a short window) can
# use — no Google Drive sharing involved at all.
# =====================================================================

import os
import time
import hmac
import hashlib
import base64

SECRET = os.environ["JWT_SECRET"]


def make_media_token(username, file_id, ttl_seconds=3600):
    expires = int(time.time()) + ttl_seconds
    payload = f"{username}:{file_id}:{expires}"
    sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(payload.encode()).decode() + "." + sig


def verify_media_token(token, expected_file_id=None):
    try:
        payload_b64, sig = token.split(".", 1)
        payload = base64.urlsafe_b64decode(payload_b64.encode()).decode()
        expected_sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        username, file_id, expires = payload.split(":")
        if int(expires) < time.time():
            return None
        if expected_file_id and file_id != expected_file_id:
            return None
        return username, file_id
    except Exception:
        return None
