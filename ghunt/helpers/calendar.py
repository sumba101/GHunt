from xmlrpc.client import Boolean
from dateutil.relativedelta import relativedelta
from beautifultable import BeautifulTable
import httpx
import json
from pathlib import Path

from typing import *
from copy import deepcopy

from ghunt.parsers.calendar import Calendar, CalendarEvents
from ghunt.objects.base import GHuntCreds
from ghunt.objects.utils import TMPrinter
from ghunt.apis.calendar import CalendarHttp
from ghunt.objects.encoders import GHuntEncoder


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
    ### Save all events to JSON file
    sanitized_email = email_address.replace("@", "_at_").replace(".", "_")
    output_file = Path(f"calendar_events_{sanitized_email}.json")

    calendar_data = {
        "calendar_info": {
            "id": calendar.id,
            "summary": calendar.summary,
            "time_zone": calendar.time_zone
        },
        "events_count": len(events.items),
        "events": [event.__dict__ for event in events.items]
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(calendar_data, f, cls=GHuntEncoder, indent=2, default=str)

    ### Calendar

    print(f"Calendar ID : {calendar.id}")
    if calendar.summary != calendar.id:
        print(f"[+] Calendar Summary : {calendar.summary}")
    print(f"Calendar Timezone : {calendar.time_zone}\n")

    ### Events
    target_events = events.items[-limit:]
    if target_events:
        print(f"[+] {len(events.items)} event{'s' if len(events.items) > 1 else ''} dumped to {output_file} ! Showing the last {len(target_events)} one{'s' if len(target_events) > 1 else ''}...\n")

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
        print(f"[-] No events dumped. (Data saved to {output_file})")

    ### Names

    names = set()
    for event in events.items:
        if event.creator.email == email_address and (name := event.creator.display_name) and name != display_name:
            names.add(name)
    if names:
        print("\n[+] Found other names used by the target :")
        for name in names:
            print(f"- {name}")