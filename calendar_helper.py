# calendar_helper.py
# Google Calendar integration for Mikhelassist bot.
#
# SETUP:
# 1. Go to https://console.cloud.google.com/
# 2. Create a project and enable the Google Calendar API
# 3. Create OAuth 2.0 credentials and download as credentials.json
# 4. Place credentials.json in the same folder as bot.py
# 5. Run the bot once locally — a browser will open to authorize
# 6. token.json will be created automatically — keep it safe, don't commit it
#
# If you don't need Calendar, leave this file as-is.
# Calendar commands will show an error but everything else works fine.

from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import os, pickle, datetime

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_service():
    creds = None
    if os.path.exists("token.json"):
        import google.oauth2.credentials
        import json
        with open("token.json") as f:
            data = json.load(f)
        creds = google.oauth2.credentials.Credentials(
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            token_uri=data.get("token_uri"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scopes=data.get("scopes"),
        )
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            import json
            f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def get_upcoming_events(days=7, max_results=10):
    service = get_service()
    now = datetime.datetime.utcnow().isoformat() + "Z"
    end = (datetime.datetime.utcnow() + datetime.timedelta(days=days)).isoformat() + "Z"
    result = service.events().list(
        calendarId="primary",
        timeMin=now,
        timeMax=end,
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    events = []
    for e in result.get("items", []):
        start = e["start"].get("dateTime", e["start"].get("date"))
        events.append({"id": e["id"], "summary": e.get("summary", "No title"), "start": start})
    return events


def get_events_by_day(date_str):
    service = get_service()
    start = date_str + "T00:00:00Z"
    end = date_str + "T23:59:59Z"
    result = service.events().list(
        calendarId="primary",
        timeMin=start,
        timeMax=end,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    events = []
    for e in result.get("items", []):
        start_time = e["start"].get("dateTime", e["start"].get("date"))
        events.append({"id": e["id"], "summary": e.get("summary", "No title"), "start": start_time})
    return events


def create_event(title, start_datetime_str, duration_minutes=60, recurrence=None):
    service = get_service()
    start = datetime.datetime.fromisoformat(start_datetime_str)
    end = start + datetime.timedelta(minutes=duration_minutes)
    event = {
        "summary": title,
        "start": {"dateTime": start.isoformat(), "timeZone": "Africa/Addis_Ababa"},
        "end":   {"dateTime": end.isoformat(),   "timeZone": "Africa/Addis_Ababa"},
    }
    if recurrence:
        event["recurrence"] = [recurrence]
    return service.events().insert(calendarId="primary", body=event).execute()


def delete_event(event_id):
    service = get_service()
    service.events().delete(calendarId="primary", eventId=event_id).execute()


def update_event(event_id, new_title=None, new_datetime=None, duration_minutes=60):
    service = get_service()
    event = service.events().get(calendarId="primary", eventId=event_id).execute()
    if new_title:
        event["summary"] = new_title
    if new_datetime:
        start = datetime.datetime.fromisoformat(new_datetime)
        end = start + datetime.timedelta(minutes=duration_minutes)
        event["start"] = {"dateTime": start.isoformat(), "timeZone": "Africa/Addis_Ababa"}
        event["end"]   = {"dateTime": end.isoformat(),   "timeZone": "Africa/Addis_Ababa"}
    return service.events().update(calendarId="primary", eventId=event_id, body=event).execute()
