from xmlrpc.client import Boolean
from dateutil.relativedelta import relativedelta
from beautifultable import BeautifulTable
import httpx
import json
from pathlib import Path

from typing import *
from copy import deepcopy

from ghunt.parsers.calendar import Calendar, CalendarEvents, CalendarEvent
from ghunt.objects.base import GHuntCreds
from ghunt.objects.utils import TMPrinter
from ghunt.apis.calendar import CalendarHttp
from ghunt.objects.encoders import GHuntEncoder


def _serialize_event(event: CalendarEvent) -> dict:
    """Serialize a CalendarEvent to a proper dictionary."""
    return {
        "id": event.id or None,
        "status": event.status or None,
        "html_link": event.html_link or None,
        "created": event.created.isoformat() if event.created else None,
        "updated": event.updated.isoformat() if event.updated else None,
        "summary": event.summary or None,
        "description": event.description or None,
        "location": event.location or None,
        "creator": {
            "email": event.creator.email or None,
            "display_name": event.creator.display_name or None,
            "self": event.creator.self
        } if event.creator else None,
        "organizer": {
            "email": event.organizer.email or None,
            "display_name": event.organizer.display_name or None,
            "self": event.organizer.self
        } if event.organizer else None,
        "start": {
            "date_time": event.start.date_time.isoformat() if event.start and event.start.date_time else None,
            "time_zone": event.start.time_zone if event.start else None
        } if event.start else None,
        "end": {
            "date_time": event.end.date_time.isoformat() if event.end and event.end.date_time else None,
            "time_zone": event.end.time_zone if event.end else None
        } if event.end else None,
        "recurring_event_id": event.recurring_event_id or None,
        "original_start_time": {
            "date_time": event.original_start_time.date_time.isoformat() if event.original_start_time and event.original_start_time.date_time else None,
            "time_zone": event.original_start_time.time_zone if event.original_start_time else None
        } if event.original_start_time else None,
        "visibility": event.visibility or None,
        "ical_uid": event.ical_uid or None,
        "sequence": event.sequence,
        "guest_can_invite_others": event.guest_can_invite_others,
        "reminders": {
            "use_default": event.reminders.use_default if event.reminders else 0,
            "overrides": [{
                "method": reminder.method,
                "minutes": reminder.minutes
            } for reminder in event.reminders.overrides] if event.reminders and event.reminders.overrides else []
        } if event.reminders else None,
        "event_type": event.event_type or None
    }


async def fetch_all(ghunt_creds: GHuntCreds, as_client: httpx.AsyncClient, email_address: str) -> Tuple[Boolean, Calendar, CalendarEvents]:
    calendar_api = CalendarHttp(ghunt_creds)
    found, calendar = await calendar_api.get_calendar(as_client, email_address)
    if not found:
        return False, None, None
    tmprinter = TMPrinter()
    _, events = await calendar_api.get_events(as_client, email_address, params_template="max_from_beginning")
    next_page_token = deepcopy(events.next_page_token)
    while next_page_token:
        tmprinter.out(f"[~] Dumped {len(events.items)} events...")
        _, new_events = await calendar_api.get_events(as_client, email_address, params_template="max_from_beginning", page_token=next_page_token)
        events.items += new_events.items
        next_page_token = deepcopy(new_events.next_page_token)
    tmprinter.clear()
    return True, calendar, events

def out(calendar: Calendar, events: CalendarEvents, email_address: str, display_name="", limit=5):
    """
        Output fetched calendar events.
        if limit = 0, = all events are shown
    """
    ### Filter and deduplicate events
    # Keep only confirmed events
    confirmed_events = [event for event in events.items if event.status == "confirmed"]

    # Deduplicate by summary, keeping the most recent (by updated timestamp)
    seen_summaries = {}
    for event in confirmed_events:
        summary = event.summary or ""
        if summary not in seen_summaries:
            seen_summaries[summary] = event
        else:
            # Keep the more recent event (by updated timestamp)
            existing_event = seen_summaries[summary]
            if event.updated and existing_event.updated:
                if event.updated > existing_event.updated:
                    seen_summaries[summary] = event
            elif event.updated:
                seen_summaries[summary] = event

    unique_events = list(seen_summaries.values())

    ### Save filtered events to JSON file
    sanitized_email = email_address.replace("@", "_at_").replace(".", "_")
    output_file = Path(f"calendar_events_{sanitized_email}.json")

    calendar_data = {
        "calendar_info": {
            "id": calendar.id,
            "summary": calendar.summary,
            "time_zone": calendar.time_zone
        },
        "original_events_count": len(events.items),
        "filtered_events_count": len(unique_events),
        "events": [_serialize_event(event) for event in unique_events]
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(calendar_data, f, cls=GHuntEncoder, indent=2, default=str)

    ### Calendar

    print(f"Calendar ID : {calendar.id}")
    if calendar.summary != calendar.id:
        print(f"[+] Calendar Summary : {calendar.summary}")
    print(f"Calendar Timezone : {calendar.time_zone}")
    print(f"Total events found : {len(events.items)}")
    print(f"Filtered unique confirmed events : {len(unique_events)}\n")

    ### Events
    target_events = unique_events[-limit:]
    if target_events:
        print(f"[+] {len(unique_events)} unique confirmed event{'s' if len(unique_events) > 1 else ''} dumped to {output_file} ! Showing the last {len(target_events)} one{'s' if len(target_events) > 1 else ''}...\n")

        table = BeautifulTable()
        table.set_style(BeautifulTable.STYLE_GRID)
        table.columns.header = ["Name", "Datetime (UTC)", "Duration"]

        for event in target_events:
            title = "/"
            if event.summary:
                title = event.summary
            duration = "?"
            if event.end.date_time and event.start.date_time:
                duration = relativedelta(event.end.date_time, event.start.date_time)
                if duration.days or duration.hours or duration.minutes:
                    duration = (f"{(str(duration.days) + ' day' + ('s' if duration.days > 1 else '')) if duration.days else ''} "
                        f"{(str(duration.hours) + ' hour' + ('s' if duration.hours > 1 else '')) if duration.hours else ''} "
                        f"{(str(duration.minutes) + ' minute' + ('s' if duration.minutes > 1 else '')) if duration.minutes else ''}").strip()         

            date = "?"
            if event.start.date_time:
                date = event.start.date_time.strftime("%Y/%m/%d %H:%M:%S")
            table.rows.append([title, date, duration])

        print(table)

        print(f"\n🗃️ Download link :\n=> https://calendar.google.com/calendar/ical/{email_address}/public/basic.ics")
        print(f"\n📄 Full data saved to: {output_file}")
    else:
        print(f"[-] No confirmed events found after filtering. (Data saved to {output_file})")

    ### Names

    names = set()
    for event in events.items:
        if event.creator.email == email_address and (name := event.creator.display_name) and name != display_name:
            names.add(name)
    if names:
        print("\n[+] Found other names used by the target :")
        for name in names:
            print(f"- {name}")