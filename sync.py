#!/usr/bin/env python3
"""
Docendo → Google Calendar sync
Fetches ICS from Docendo and syncs events to Google Calendar.
"""

import os
import sys
import logging
import hashlib
import requests
from datetime import datetime, timezone, date
from icalendar import Calendar
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ── Configuration ────────────────────────────────────────────────────────────
DOCENDO_ICS_URL = (
    "https://app.docendo.dk/calendars/ical/"
    "a3ae5263-7ab9-4864-bdb3-2270efe70cec"
)
GOOGLE_CALENDAR_ID = "7m7qj3i33ot9m0o0r0883i3lck@group.calendar.google.com"
SCOPES = ["https://www.googleapis.com/auth/calendar"]
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")
SOURCE_TAG = "docendo-sync"

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


# ── Docendo ──────────────────────────────────────────────────────────────────
def fetch_docendo_events() -> list[dict]:
    """Fetch and parse events from Docendo ICS feed."""
    log.info("Fetching Docendo ICS from %s", DOCENDO_ICS_URL)
    resp = requests.get(DOCENDO_ICS_URL, timeout=30)
    resp.raise_for_status()

    cal = Calendar.from_ical(resp.content)
    events = []

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        uid = str(component.get("UID", ""))
        summary = str(component.get("SUMMARY", "Ingen titel"))
        description = str(component.get("DESCRIPTION", "") or "")
        location = str(component.get("LOCATION", "") or "")

        dtstart = component.get("DTSTART").dt
        dtend = component.get("DTEND").dt if component.get("DTEND") else None

        # Handle both date-only and datetime events
        if isinstance(dtstart, date) and not isinstance(dtstart, datetime):
            start = {"date": dtstart.isoformat()}
            end = {"date": (dtend or dtstart).isoformat()}
        else:
            if dtstart.tzinfo is None:
                dtstart = dtstart.replace(tzinfo=timezone.utc)
            if dtend and dtend.tzinfo is None:
                dtend = dtend.replace(tzinfo=timezone.utc)
            start = {"dateTime": dtstart.isoformat(), "timeZone": "Europe/Copenhagen"}
            end = {
                "dateTime": (dtend or dtstart).isoformat(),
                "timeZone": "Europe/Copenhagen",
            }

        events.append(
            {
                "uid": uid,
                "summary": summary,
                "description": description,
                "location": location,
                "start": start,
                "end": end,
            }
        )

    log.info("Found %d events in Docendo ICS", len(events))
    return events


# ── Google Calendar ───────────────────────────────────────────────────────────
def get_google_service():
    """Authenticate and return Google Calendar API service."""
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.info("Refreshing Google credentials")
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                log.error(
                    "credentials.json not found. "
                    "Download it from Google Cloud Console and place it next to sync.py"
                )
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        log.info("Saved token to %s", TOKEN_FILE)

    return build("calendar", "v3", credentials=creds)


def get_existing_synced_events(service) -> dict[str, dict]:
    """Return all events previously synced from Docendo, keyed by Docendo UID."""
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
            uid = (
                ev.get("extendedProperties", {})
                .get("private", {})
                .get("docendo_uid")
            )
            if uid:
                existing[uid] = ev

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    log.info("Found %d existing synced events in Google Calendar", len(existing))
    return existing


def event_fingerprint(ev: dict) -> str:
    """Return a hash of event fields for change detection."""
    key = "|".join([
        ev["summary"],
        ev["description"],
        ev["location"],
        str(ev["start"]),
        str(ev["end"]),
    ])
    return hashlib.md5(key.encode()).hexdigest()


def build_google_event(docendo_ev: dict) -> dict:
    """Build a Google Calendar event dict from a Docendo event."""
    return {
        "summary": docendo_ev["summary"],
        "description": docendo_ev["description"] or None,
        "location": docendo_ev["location"] or None,
        "start": docendo_ev["start"],
        "end": docendo_ev["end"],
        "extendedProperties": {
            "private": {
                "source": SOURCE_TAG,
                "docendo_uid": docendo_ev["uid"],
                "fingerprint": event_fingerprint(docendo_ev),
            }
        },
    }


# ── Sync logic ────────────────────────────────────────────────────────────────
def sync(service, docendo_events: list[dict]):
    existing = get_existing_synced_events(service)

    docendo_by_uid = {ev["uid"]: ev for ev in docendo_events}
    created = updated = deleted = skipped = 0

    # Create or update
    for uid, dev in docendo_by_uid.items():
        new_fp = event_fingerprint(dev)
        gev = build_google_event(dev)

        if uid in existing:
            old_fp = (
                existing[uid]
                .get("extendedProperties", {})
                .get("private", {})
                .get("fingerprint")
            )
            if old_fp == new_fp:
                skipped += 1
                continue
            try:
                service.events().update(
                    calendarId=GOOGLE_CALENDAR_ID,
                    eventId=existing[uid]["id"],
                    body=gev,
                ).execute()
                log.info("Updated: %s", dev["summary"])
                updated += 1
            except HttpError as e:
                log.warning("Failed to update %s: %s", uid, e)
        else:
            try:
                service.events().insert(
                    calendarId=GOOGLE_CALENDAR_ID, body=gev
                ).execute()
                log.info("Created: %s", dev["summary"])
                created += 1
            except HttpError as e:
                log.warning("Failed to create %s: %s", uid, e)

    # Delete events no longer in Docendo
    for uid, gev in existing.items():
        if uid not in docendo_by_uid:
            try:
                service.events().delete(
                    calendarId=GOOGLE_CALENDAR_ID, eventId=gev["id"]
                ).execute()
                log.info("Deleted: %s", gev.get("summary"))
                deleted += 1
            except HttpError as e:
                log.warning("Failed to delete %s: %s", uid, e)

    log.info(
        "Sync done — created: %d, updated: %d, deleted: %d, unchanged: %d",
        created, updated, deleted, skipped,
    )


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("=== Docendo sync started ===")
    try:
        docendo_events = fetch_docendo_events()
        service = get_google_service()
        sync(service, docendo_events)
    except Exception as e:
        log.exception("Sync failed: %s", e)
        sys.exit(1)
    log.info("=== Sync complete ===")


if __name__ == "__main__":
    main()
