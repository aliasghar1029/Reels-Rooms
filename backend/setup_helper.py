# =====================================================================
# Reel Room — One-time setup helper
#
# Run this ONCE on a normal, unrestricted computer (not the restricted
# PC). It will:
#   1) Ask you to choose a username + password for the restricted PC,
#      and print a secure password hash.
#   2) Open Google sign-in in your browser (once) and capture a
#      "refresh token" that lets the backend talk to your Drive forever
#      after, without you signing in to Google again.
#
# You'll paste both results into Render's environment variables.
# This script itself does not need to be uploaded anywhere.
#
# Before running:
#   pip install bcrypt requests --break-system-packages
#
# You also need, from Google Cloud Console -> Credentials -> your OAuth
# client:
#   - the Client ID
#   - the Client Secret  (click the client, it's shown on the page)
# And you must add this Authorized redirect URI to that same client:
#   http://localhost:8080/callback
# =====================================================================

import json
import webbrowser
import http.server
import urllib.parse
import bcrypt
import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
REDIRECT_URI = "http://localhost:8080/callback"
SCOPE = "https://www.googleapis.com/auth/drive.file"

print("=" * 60)
print("STEP 1 — Choose the login for the restricted PC")
print("=" * 60)
username = input("Choose a username (e.g. aliasghar): ").strip()
password = input("Choose a password: ").strip()
password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

print("\n=== Password hash (save this — you'll paste it into Render) ===")
print(password_hash)

print("\n" + "=" * 60)
print("STEP 2 — Link your Google Drive (one-time)")
print("=" * 60)
client_id = input("Paste your Google OAuth Client ID: ").strip()
client_secret = input("Paste your Google OAuth Client Secret: ").strip()

auth_params = {
    "client_id": client_id,
    "redirect_uri": REDIRECT_URI,
    "response_type": "code",
    "scope": SCOPE,
    "access_type": "offline",   # <-- this is what gives us a refresh token
    "prompt": "consent",        # <-- forces Google to issue a refresh token every time
}
auth_url = AUTH_URL + "?" + urllib.parse.urlencode(auth_params)

captured_code = {}

class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if "code" in qs:
            captured_code["code"] = qs["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Done! You can close this tab and go back to the terminal.</h2>")
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, *args):
        pass  # keep terminal output clean

print("\nOpening your browser for Google sign-in...")
print("If it doesn't open automatically, visit this URL:\n")
print(auth_url, "\n")
webbrowser.open(auth_url)

server = http.server.HTTPServer(("localhost", 8080), CallbackHandler)
while "code" not in captured_code:
    server.handle_request()

print("Got authorization code, exchanging it for a refresh token...")
resp = requests.post(TOKEN_URL, data={
    "client_id": client_id,
    "client_secret": client_secret,
    "code": captured_code["code"],
    "grant_type": "authorization_code",
    "redirect_uri": REDIRECT_URI,
})
resp.raise_for_status()
tokens = resp.json()
refresh_token = tokens.get("refresh_token")

if not refresh_token:
    print("\n⚠️  No refresh token returned. This usually means you already")
    print("authorized this app before without revoking it. Go to")
    print("https://myaccount.google.com/permissions, remove 'Reel Room',")
    print("then run this script again.")
else:
    print("\n=== Refresh token (save this too) ===")
    print(refresh_token)

    users_json = {
        username: {
            "password_hash": password_hash,
            "refresh_token": refresh_token,
        }
    }
    print("\n" + "=" * 60)
    print("PASTE THIS into Render's APP_USERS_JSON environment variable:")
    print("=" * 60)
    print(json.dumps(users_json))

print("\nDone. See BACKEND_SETUP_GUIDE.md for what to do with these values.")