#!/usr/bin/env python3

import json
import os
from typing import Dict, Tuple
import xml.etree.ElementTree as ET


def load_from_meetsetup(meetsetupfile: str) -> Tuple[Dict[int, str], Dict[int, str]]:
    """Load event titles and sponsors (optional) from meetsetup.xml.

    The XML is expected to have the structure:

        <MeetSetUp>
          <Events>
            <Event>
              <EventNumber>1</EventNumber>
              <EventDescription>50m Freestyle</EventDescription>
              <Sponsor>Acme Swimwear</Sponsor>
            </Event>
            ...
          </Events>
        </MeetSetUp>

    Returns a dict mapping EventNumber (as string) to EventDescription and
    Sponsor (if present).
    """

    if not os.path.exists(meetsetupfile):
        raise FileNotFoundError(f"meetsetup.xml not found at {meetsetupfile}")

    try:
        tree = ET.parse(meetsetupfile)
        root = tree.getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML in meetsetup.xml: {exc}") from exc

    event_titles: Dict[int, str] = {}
    event_sponsors: Dict[int, str] = {}

    events_elem = root.find("Events")
    if events_elem is None:
        raise ValueError("No Events element found in meetsetup.xml")

    for ev in events_elem.findall("Event"):
        num_text = ev.findtext("EventNumber") or ""
        desc_text = ev.findtext("EventDescription") or ""
        sponsor_text = ev.findtext("Sponsor") or ""

        num = num_text.strip()
        desc = desc_text.strip()
        sponsor = sponsor_text.strip()
        sponsor = "Sponset av " + sponsor if sponsor else ""

        if not num or not desc:
            continue

        event_titles[int(num)] = desc
        if sponsor:
            event_sponsors[int(num)] = sponsor

    return event_titles, event_sponsors


def main():
    meetsetupfile = "meetsetup.xml"
    eventdesc = "eventdesc.json"
    try:
        event_titles, event_sponsors = load_from_meetsetup(meetsetupfile)
        with open(eventdesc, "w", encoding="utf-8") as fh:
            payload = {"titles": event_titles, "sponsors": event_sponsors}
            json.dump(payload, fh, indent=4)
        print(f"Event descriptions and sponsors saved to {eventdesc}")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
