#!/usr/bin/env python3
"""
PDF vagtplan → Google Calendar sync

Parses a monthly schedule PDF (e.g. "Sigurd marts 26.pdf") using AI
and syncs the extracted events to Google Calendar.

Configuration via environment variables:
  AI_PROVIDER        "openai" (default) or "claude"
  OPENAI_API_KEY     required if AI_PROVIDER=openai
  ANTHROPIC_API_KEY  required if AI_PROVIDER=claude

Usage:
  python3 pdf_sync.py "Sigurd marts 26.pdf"
"""

import os
import sys
import json
import logging
import hashlib
import base64
import re
import fitz  # pymupdf — converts PDF pages to images
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ── Configuration ────────────────────────────────────────────────────────────
GOOGLE_CALENDAR_ID = "7m7qj3i33ot9m0o0r0883i3lck@group.calendar.google.com"
SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")
SOURCE_TAG = "docendo-pdf"
TIMEZONE = "Europe/Copenhagen"
AI_PROVIDER = os.environ.get("AI_PROVIDER", "openai").lower()

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(os.path.dirname(__file__), "sync.log")),
    ],
)
log = logging.getLogger(__name__)

# ── Prompt ───────────────────────────────────────────────────────────────────
PARSE_PROMPT = """This is a monthly work schedule (vagtplan) for someone named Sigurd.
Extract ALL calendar events. Skip days marked "Ingen vagter".

For each time slot return:
- date: YYYY-MM-DD (year is 2026 unless clearly stated otherwise)
- start_time: HH:MM
- end_time: HH:MM
- worker: full name
- activity: activity/task description

Return ONLY a valid JSON array, no explanation:
[{"date":"2026-03-07","start_time":"08:30","end_time":"09:00","worker":"Sofie Juliane Skjærba","activity":"Transport Weekend"}, ...]"""


# ── PDF → images ──────────────────────────────────────────────────────────────
def pdf_to_base64_images(pdf_path: str) -> list[str]:
    """Convert each PDF page to a base64-encoded PNG."""
    doc = fitz.open(pdf_path)
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=150)
        images.append(base64.b64encode(pix.tobytes("png")).decode())
    log.info("Converted %d PDF page(s) to images", len(images))
    return images


# ── AI parsing ────────────────────────────────────────────────────────────────
def parse_with_openai(pdf_path: str) -> list[dict]:
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        log.error("OPENAI_API_KEY environment variable not set")
        sys.exit(1)

    client = OpenAI(api_key=api_key)
    images = pdf_to_base64_images(pdf_path)

    content = [{"type": "text", "text": PARSE_PROMPT}]
    for img in images:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{img}", "detail": "high"},
        })

    log.info("Sending %d page(s) to OpenAI GPT-4o...", len(images))
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": content}],
        max_tokens=4096,
    )
    return extract_json(response.choices[0].message.content)


def parse_with_claude(pdf_path: str) -> list[dict]:
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY environment variable not set")
        sys.exit(1)

    with open(pdf_path, "rb") as f:
        pdf_data = base64.standard_b64encode(f.read()).decode()

    client = anthropic.Anthropic(api_key=api_key)
    log.info("Sending PDF to Claude claude-opus-4-6...")
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_data}},
                {"type": "text", "text": PARSE_PROMPT},
            ],
        }],
    )
    return extract_json(response.content[0].text)


def extract_json(raw: str) -> list[dict]:
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        log.error("No JSON array found in AI response:\n%s", raw)
        sys.exit(1)
    events = json.loads(match.group())
    log.info("Extracted %d events from PDF", len(events))
    return events


def parse_pdf(pdf_path: str) -> list[dict]:
    if AI_PROVIDER == "claude":
        return parse_with_claude(pdf_path)
    return parse_with_openai(pdf_path)


# ── Google Calendar ───────────────────────────────────────────────────────────
def get_google_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def get_existing_pdf_events(service) -> dict[str, dict]:
    existing = {}
    page_token = None
    while True:
        result = (
            service.events()
            .list(
                calendarId=GOOGLE_CALENDAR_ID,
                privateExtendedProperty=f"source={SOURCE_TAG}",
                pageToken=page_token,
                maxResults=250,
                singleEvents=True,
            )
            .execute()
        )
        for ev in result.get("items", []):
            fp = ev.get("extendedProperties", {}).get("private", {}).get("fingerprint")
            if fp:
                existing[fp] = ev
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    log.info("Found %d existing PDF events in Google Calendar", len(existing))
    return existing


def event_fingerprint(ev: dict) -> str:
    key = "|".join([ev["date"], ev["start_time"], ev["end_time"], ev["worker"], ev["activity"]])
    return hashlib.md5(key.encode()).hexdigest()


def build_google_event(ev: dict) -> dict:
    return {
        "summary": f"{ev['activity']} ({ev['worker']})",
        "start": {"dateTime": f"{ev['date']}T{ev['start_time']}:00", "timeZone": TIMEZONE},
        "end":   {"dateTime": f"{ev['date']}T{ev['end_time']}:00",   "timeZone": TIMEZONE},
        "extendedProperties": {
            "private": {"source": SOURCE_TAG, "fingerprint": event_fingerprint(ev)}
        },
    }


def sync(service, pdf_events: list[dict]):
    existing = get_existing_pdf_events(service)
    created = skipped = 0
    for ev in pdf_events:
        fp = event_fingerprint(ev)
        if fp in existing:
            skipped += 1
            continue
        try:
            service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=build_google_event(ev)).execute()
            log.info("Created: %s %s-%s %s", ev["date"], ev["start_time"], ev["end_time"], ev["worker"])
            created += 1
        except HttpError as e:
            log.warning("Failed to create event: %s", e)
    log.info("PDF sync done — created: %d, already existed: %d", created, skipped)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} <path-to-pdf>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    if not os.path.exists(pdf_path):
        log.error("PDF not found: %s", pdf_path)
        sys.exit(1)

    log.info("=== PDF sync started: %s (provider: %s) ===", pdf_path, AI_PROVIDER)
    events = parse_pdf(pdf_path)
    service = get_google_service()
    sync(service, events)
    log.info("=== PDF sync complete ===")


if __name__ == "__main__":
    main()
