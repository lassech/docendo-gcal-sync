#!/usr/bin/env python3
"""
Run this once on your LOCAL machine (not server) to generate token.json.
Then copy token.json to the server before starting Docker.

Usage:
  1. python3 auth.py
  2. Follow browser prompt to authorize Google Calendar access
  3. Copy token.json to the server:
     scp token.json lassec@192.168.1.47:~/docendo_sync/
"""

import os, sys
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")

if not os.path.exists(CREDENTIALS_FILE):
    print("ERROR: credentials.json not found!")
    print("Download it from Google Cloud Console:")
    print("  APIs & Services → Credentials → OAuth 2.0 Client → Download JSON")
    sys.exit(1)

flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
creds = flow.run_local_server(port=0)

with open(TOKEN_FILE, "w") as f:
    f.write(creds.to_json())

print(f"\nAuthorization successful! token.json saved to: {TOKEN_FILE}")
print("\nNext step - copy to server:")
print(f"  scp token.json lassec@192.168.1.47:~/docendo_sync/")
