# =====================================================================
# Reel Room — Facebook Graph API helpers
#
# Uses the free, official Meta Graph API. No paid tools involved.
# Publishing works by handing Facebook a temporary public link to the
# file (already sitting in the user's Drive) — Facebook's own servers
# fetch it, so our backend never has to stream large video bytes
# itself (important on a free hosting tier with limited resources).
# =====================================================================

import requests

GRAPH = "https://graph.facebook.com/v19.0"
OAUTH_DIALOG = "https://www.facebook.com/v19.0/dialog/oauth"

SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts"


def build_login_url(app_id, redirect_uri, state):
    return (
        f"{OAUTH_DIALOG}?client_id={app_id}&redirect_uri={redirect_uri}"
        f"&state={state}&scope={SCOPES}&response_type=code"
    )


def exchange_code_for_user_token(app_id, app_secret, redirect_uri, code):
    r = requests.get(f"{GRAPH}/oauth/access_token", params={
        "client_id": app_id, "client_secret": app_secret,
        "redirect_uri": redirect_uri, "code": code,
    })
    r.raise_for_status()
    return r.json()["access_token"]


def get_long_lived_user_token(app_id, app_secret, short_token):
    r = requests.get(f"{GRAPH}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": app_id,
        "client_secret": app_secret, "fb_exchange_token": short_token,
    })
    r.raise_for_status()
    return r.json()["access_token"]


def list_managed_pages(user_token):
    """Returns [{id, name, access_token}] — Page access tokens obtained
    this way do not expire as long as the user token stays valid."""
    r = requests.get(f"{GRAPH}/me/accounts", params={"access_token": user_token, "fields": "id,name,access_token"})
    r.raise_for_status()
    return r.json().get("data", [])


def publish_reel(page_id, page_token, video_public_url, caption):
    """Resumable Reels publish flow using a hosted URL (Facebook fetches
    the video itself — we never upload the bytes)."""
    start = requests.post(f"{GRAPH}/{page_id}/video_reels", data={
        "upload_phase": "start", "access_token": page_token,
    })
    start.raise_for_status()
    video_id = start.json()["video_id"]
    upload_url = start.json()["upload_url"]

    up = requests.post(upload_url, headers={
        "Authorization": f"OAuth {page_token}",
        "file_url": video_public_url,
    })
    up.raise_for_status()

    finish = requests.post(f"{GRAPH}/{page_id}/video_reels", data={
        "upload_phase": "finish", "video_id": video_id,
        "video_state": "PUBLISHED", "description": caption,
        "access_token": page_token,
    })
    finish.raise_for_status()
    return video_id


def publish_photo(page_id, page_token, image_public_url, caption):
    r = requests.post(f"{GRAPH}/{page_id}/photos", data={
        "url": image_public_url, "caption": caption, "access_token": page_token,
    })
    r.raise_for_status()
    return r.json().get("id")