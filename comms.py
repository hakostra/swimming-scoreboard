"""Communication helper process for the swimming scoreboard.

On startup this module contacts the scoreboard server running on
localhost and retrieves the timing configuration via the
`/api/timing_config` endpoint defined in main.py.

The retrieved configuration can then be used by whatever logic
is responsible for talking to the external timing system
(reading LST files, opening a serial port, etc.).
"""

from __future__ import annotations

import atexit
import csv
import datetime
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, Optional, TextIO, Tuple
import xml.etree.ElementTree as ET

from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import serial


SCOREBOARD_API = "http://localhost:8000/api"


# Global state populated at startup and reused by helper functions.
contestants: Dict[int, Dict[str, str]] = {}
events: Dict[int, Dict[int, Dict[int, int]]] = {}
event_titles: Dict[int, str] = {}
event_texts: Dict[int, str] = {}

lst_files_last_mtime: Optional[float] = None
startfile: Optional[str] = None
concfile: Optional[str] = None
meetsetupfile: Optional[str] = None
eventdescfile: Optional[str] = None

current_event: Optional[int] = None
current_heat: Optional[int] = None
splits: Dict[int, Dict[int, str]] = {}

hold_results_time: float = 0.0


# Serial port special bytes
soh = b'\x01'
home = b'\x08'
stx = b'\x02'
lf = b'\x0A'
eot = b'\x04'
space = b'\x20'
dc2 = b'\x12'
dc4 = b'\x14'


class SerialDebugRecorder:
    """Write each raw serial payload directly to disk with timestamps."""

    def __init__(self) -> None:
        self.enabled = False
        self.file_path: Optional[Path] = None
        self._fh: Optional[TextIO] = None

    def configure(self, enabled: bool, file_name: Optional[str]) -> None:
        if not enabled:
            self._disable()
            return

        path_str = (file_name or "").strip()
        if not path_str:
            print("[comms] WARNING: debug mode enabled but no debug_path "
                  "provided; disabling capture", file=sys.stderr)
            self._disable()
            return

        path = Path(path_str).expanduser()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            print("[comms] WARNING: could not create directories for "
                  f"debug_path {path}: {exc}; disabling capture",
                  file=sys.stderr)
            self._disable()
            return

        try:
            fh = path.open("a", encoding="utf-8")
        except Exception as exc:
            print("[comms] WARNING: could not open debug capture "
                  f"file {path}: {exc}; disabling capture", file=sys.stderr)
            self._disable()
            return

        self._disable()
        self.enabled = True
        self.file_path = path
        self._fh = fh

    def record(self, payload: bytes) -> None:
        if not payload or not self.enabled or self._fh is None:
            return

        ts = time.time()
        iso = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()
        entry = {
            "ts": iso,
            "ts_epoch": ts,
            "byte_count": len(payload),
            "data_hex": payload.hex(),
        }
        try:
            self._fh.write(json.dumps(entry))
            self._fh.write("\n")
            self._fh.flush()
        except Exception as exc:
            print("[comms] WARNING: failed to write serial debug "
                  f"entry: {exc}", file=sys.stderr)
            self._disable()

    def close(self) -> None:
        self._disable()

    def _disable(self) -> None:
        self.enabled = False
        if self._fh is not None:
            try:
                self._fh.flush()
            except Exception:
                pass
            try:
                self._fh.close()
            except Exception:
                pass
        self._fh = None
        self.file_path = None


serial_debug = SerialDebugRecorder()
atexit.register(serial_debug.close)


def call(endpoint: str, payload: dict | None = None, timeout: float = 5.0) -> str:
    url = SCOREBOARD_API + endpoint

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json",
                     "Accept": "application/json"},
            method="POST",
        )
    else:
        req = Request(url, headers={"Accept": "application/json"})

    try:
        with urlopen(req, timeout=timeout) as resp:  # type: ignore[call-arg]
            charset = resp.headers.get_content_charset() or "utf-8"
            body = resp.read().decode(charset, errors="replace")
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"Failed to POST lanes to {url}: {exc}") from exc

    return body


def _parse_serial_settings(timing_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Parse serial settings from timing configuration.

    Expects timing_cfg to contain keys:
      - "com_port": e.g. "/dev/ttyUSB0" or "COM3"
      - "com_settings": e.g. "9600,7,n,1" (baud,data bits,parity,stop bits)
    """

    port = timing_cfg.get("com_port") or ""
    settings_str = str(timing_cfg.get("com_settings", "9600,7,n,1") or "")

    # Defaults
    baudrate = 9600
    bytesize = 7
    parity = "N"
    stopbits = 1

    try:
        parts = [p.strip() for p in settings_str.split(",") if p.strip()]
        if len(parts) >= 1:
            baudrate = int(parts[0])
        if len(parts) >= 2:
            bytesize = int(parts[1])
        if len(parts) >= 3:
            parity = parts[2].upper()
        if len(parts) >= 4:
            stopbits = int(parts[3])
    except Exception:
        # Fall back to defaults if parsing fails
        pass

    # Map generic settings to pyserial constants
    bytesize_map = {
        5: serial.FIVEBITS,
        6: serial.SIXBITS,
        7: serial.SEVENBITS,
        8: serial.EIGHTBITS,
    }
    parity_map = {
        "N": serial.PARITY_NONE,
        "E": serial.PARITY_EVEN,
        "O": serial.PARITY_ODD,
        "M": serial.PARITY_MARK,
        "S": serial.PARITY_SPACE,
    }
    stopbits_map = {
        1: serial.STOPBITS_ONE,
        2: serial.STOPBITS_TWO,
    }

    serial_kwargs: Dict[str, Any] = {
        "port": port,
        "baudrate": baudrate,
        "bytesize": bytesize_map.get(bytesize, serial.EIGHTBITS),
        "parity": parity_map.get(parity, serial.PARITY_NONE),
        "stopbits": stopbits_map.get(stopbits, serial.STOPBITS_ONE),
        # Reasonable defaults; can be adjusted if needed.
        "timeout": None,
    }

    return serial_kwargs


def _next_message(ser: "serial.Serial"):
    """Yield (pt1, pt2) message pairs from the given serial port.

    Alive message:
    [SOH][DC2]9[DC4]TP[EOT]

    Other messages:
    [SOH][STX][HOME] <data> [EOT]

    Alive messages are immediately discarded. Other messages are sent in pairs:

    Part 1 is : [SOH][STX][HOME]ABCDDEEFFFGG¬¬HH[EOT]            (20 bytes)
    Part 2 is : [SOH][STX][HOME][LF]JKK[STX]Hh:Mm:Ss.dc¬[EOT]    (21 bytes)

    The symbol ¬ represents a space character (0x20).

    This generator always yields complete pairs ``(pt1, pt2)``. If a message
    that looks like pt2 (identified by the 4th byte being LF) is received
    before any valid pt1 has been seen, that message is discarded and the
    generator waits for a new pt1.
    """

    pending_pt1: Optional[bytes] = None

    header_len = len(soh) + len(stx) + len(home)

    while True:
        data = ser.read_until(eot)
        if not data:
            # Timeout or no data; continue waiting.
            continue

        serial_debug.record(data)

        # Check if data starts with SOH, discard if not.
        if not data.startswith(soh):
            print(f"[comms] WARNING: unknown serial message: {data!r}",
                  file=sys.stderr)
            continue

        # Discard alive messages exactly matching the known pattern.
        if data == soh + dc2 + b"9" + dc4 + b"TP" + eot:
            continue

        # Other messages must have the [SOH][STX][HOME] prefix and end with EOT.
        if not (data.startswith(soh + stx + home) and data.endswith(eot)):
            print(f"[comms] WARNING: unknown serial message: {data!r}",
                  file=sys.stderr)
            continue

        # Determine whether this is pt1 or pt2.
        # pt2 is identified by the 4th byte being LF.
        is_pt2 = False
        if len(data) > header_len:
            fourth_byte = data[header_len:header_len + 1]
            if fourth_byte == lf:
                is_pt2 = True

        payload = data[header_len:-len(eot)]

        if not is_pt2:
            # This is a pt1 message.
            if pending_pt1 is not None:
                print("[comms] WARNING: dropping unmatched pt1 "
                      "before storing new pt1", file=sys.stderr,)
            pending_pt1 = payload
            continue

        # From here on, the message is pt2.
        if pending_pt1 is None:
            # We received a pt2 without a corresponding pt1; discard.
            print("[comms] WARNING: received pt2 without"
                  "preceding pt1; discarding", file=sys.stderr)
            continue

        pt1 = pending_pt1
        pt2 = payload
        pending_pt1 = None
        yield pt1, pt2


def _handle_timing_message(pt1, pt2):
    """Handle a timing message received from the serial port.

    Expects two parts: pt1 and pt2 as bytes. The messages are:

    Part 1 is : ABCDDEEFFFGG¬¬HH
    Part 2 is : [LF]JKK[STX]Hh:Mm:Ss.dc¬

    The header [SOH][STX][HOME] and trailer [EOT] have already been stripped.

    The fields are:

        - A: message type
            - "0": Ready at start
            - "1": Official end
            - "2": On line time
            - "3": Current race results
            - "4": -
            - "5": Previous race results
        - B: kind of time
            - "S": Start
            - "I": Split time
            - "A": Finish
            - "D": Take over time in a relay
            - "R": Reaction time at start
            - "B": Button only at finish
        - C: time type
            - " ": Normal time
            - "E": Edited time
            - "+": Platform time after touchpad time
            - "-": Platform time before touchpad time
            - "1"/"2"/"3": Number of the button
        - DD: used lanes
        - EE: number of laps
        - FFF: event
        - GG: heat
        - HH: rank
        - J: lane
        - KK: current lap

        - Hh:Mm:Ss.dc: time string in hours, minutes, seconds, centiseconds
        - ¬: space character (0x20)
    """

    A = pt1[0:1].decode("ascii")
    B = pt1[1:2].decode("ascii")
    C = pt1[2:3].decode("ascii")
    # DD = pt1[3:5].decode("ascii")
    # EE = pt1[5:7].decode("ascii")
    FFF = pt1[7:10].decode("ascii")
    GG = pt1[10:12].decode("ascii")
    # 2 whitespace between GG and HH
    HH = pt1[14:16].decode("ascii")

    # pt 2 start with lf
    J = pt2[1:2].decode("ascii")
    KK = pt2[2:4].decode("ascii")
    # STX between KK and time
    time_str = pt2[5:16].decode("ascii")

    # Ready at start: prepare heat
    if A == "0":
        event_id = int(FFF)
        heat_id = int(GG)
        prepare_heat(event_id, heat_id)
        return

    # Start time: start timer
    elif (A == "2") and (B == "S"):
        start_timer()

    # Split times and finish times are handled similarly
    elif (A == "2") and (B in ("I", "A")):
        event_id = int(FFF)
        heat_id = int(GG)
        lane_id = int(J)
        lap_num = int(KK)
        rank = int(HH)
        finished = (B == "A")
        split(event_id, heat_id, lane=lane_id, lap=lap_num, rank=rank,
              time=time_str, finished=finished)

    # Official end: finish heat
    elif A == "1":
        finish_heat()

    else:
        print(f"[comms] WARNING: unhandled timing message:", file=sys.stderr)
        debug_struct = {
            "A": A,
            "B": B,
            "C": C,
            "DD": pt1[3:5].decode("ascii"),
            "EE": pt1[5:7].decode("ascii"),
            "FFF": FFF,
            "GG": GG,
            "HH": HH,
            "J": J,
            "KK": KK,
            "time_str": time_str,
        }
        print(json.dumps(debug_struct, indent=2, sort_keys=True))


def fetch_timing_config() -> Dict[str, Any]:
    """Fetch timing configuration JSON from the scoreboard server.

    Returns only the `timing_config` document from the API response.
    Raises an exception if the request fails or the response is invalid.
    """

    body = call("/timing_config")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Timing config endpoint did not return valid JSON") from exc

    if not isinstance(payload, dict):
        raise ValueError("Unexpected timing config response structure "
                         "(expected JSON object)")

    cfg = payload.get("timing_config")
    if not isinstance(cfg, dict):
        raise ValueError("Response missing 'timing_config' object")

    # Set global LST file paths based on the retrieved configuration. If the
    # configuration change, the easiest is to restart the comms process.
    global startfile, concfile, meetsetupfile, eventdescfile
    base_dir = cfg["lst_path"]
    startfile = os.path.join(base_dir, "lststart.txt")
    concfile = os.path.join(base_dir, "lstconc.txt")
    meetsetupfile = os.path.join(base_dir, "meetsetup.xml")
    eventdescfile = os.path.join(base_dir, "eventdesc.json")

    return cfg


def load_contestants_from_lstconc(lst_file: str) -> Dict[int, Dict[str, str]]:
    """Load contestants from lstconc.txt using timing configuration.

    The file is expected to be a semicolon-separated file with at
    least the following columns: id, lastname, firstname, abNat.

    It is read as ISO-8859-1 and converted to Python str (Unicode),
    which makes it safe to later encode as UTF-8 for the scoreboard.
    Returns a dict keyed by the `id` field (as string); each value is
    a dict with `name` and `club` keys.
    """

    if not os.path.exists(lst_file):
        raise FileNotFoundError(f"lstconc file not found at {lst_file}")

    contestants: Dict[int, Dict[str, str]] = {}

    with open(lst_file, "r", encoding="iso-8859-1", newline="") as f:
        reader = csv.DictReader(
            f,
            delimiter=";",
            quotechar='"',
            skipinitialspace=True,
        )
        for row in reader:
            if not row:
                continue

            raw_id = row.get("id")
            if not raw_id:
                continue
            key = int(str(raw_id).strip())
            if not key:
                continue

            firstname = (row.get("firstname") or "").strip()
            lastname = (row.get("lastname") or "").strip()
            # Combine into a single display name; adjust format as needed.
            if firstname and lastname:
                name = f"{firstname} {lastname}"
            else:
                name = firstname or lastname

            club = (row.get("abNat") or "").strip()

            contestants[key] = {
                "name": name,
                "club": club,
            }

    return contestants


def load_events_from_lststart(lst_file: str) -> Dict[int, Dict[int, Dict[int, int]]]:
    """Load event/heat/lane start list from lststart.txt.

    Builds a nested mapping: events[event_id][heat_id][lane_id] -> idBib.
    All keys are taken directly from the file (as stripped strings).
    """

    if not os.path.exists(lst_file):
        raise FileNotFoundError(f"lststart file not found at {lst_file}")

    events: Dict[int, Dict[int, Dict[int, int]]] = {}

    with open(lst_file, "r", encoding="iso-8859-1", newline="") as f:
        reader = csv.DictReader(
            f,
            delimiter=";",
            quotechar='"',
            skipinitialspace=True,
        )
        for row in reader:
            if not row:
                continue

            event_id = (row.get("event") or "").strip()
            heat_id = (row.get("heat") or "").strip()
            lane_id = (row.get("lane") or "").strip()
            id_bib = (row.get("idBib") or "").strip()

            if not (event_id and heat_id and lane_id and id_bib):
                continue

            heats = events.setdefault(int(event_id), {})

            # First heat is heat 0 in the file, but the timing system sends
            # heats starting from 1!!!
            lanes = heats.setdefault(int(heat_id)+1, {})
            lanes[int(lane_id)] = int(id_bib)

    return events


def load_event_titles_from_json(eventdescfile: str) -> Tuple[Dict[int, str], Dict[int, str]]:
    """Load event titles from eventdesc.json.

    The JSON file is expected to contain a mapping of EventNumber (as string)
    to EventDescription.

    Returns a dict mapping EventNumber (as string) to EventDescription.
    The EventNumber keys are intended to match the `event` field
    values used in the LST files.
    """

    if not os.path.exists(eventdescfile):
        raise FileNotFoundError(f"eventdesc.json not found at {eventdescfile}")

    try:
        with open(eventdescfile, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in eventdesc.json: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("eventdesc.json does not contain a JSON object")

    event_titles: Dict[int, str] = {}
    event_texts: Dict[int, str] = {}

    for key, value in data["titles"].items():
        event_num = int(key)
        event_desc = str(value).strip()
        if event_desc:
            event_titles[event_num] = event_desc

    for key, value in data["sponsors"].items():
        event_num = int(key)
        event_sponsor = str(value).strip()
        if event_sponsor:
            event_texts[event_num] = event_sponsor

    return event_titles, event_texts


def load_event_titles_from_meetsetup(meetsetupfile: str) -> Dict[int, str]:
    """Load event titles from meetsetup.xml.

    The XML is expected to have the structure:

        <MeetSetUp>
          <Events>
            <Event>
              <EventNumber>1</EventNumber>
              <EventDescription>50m Freestyle</EventDescription>
            </Event>
            ...
          </Events>
        </MeetSetUp>

    Returns a dict mapping EventNumber (as string) to EventDescription.
    The EventNumber keys are intended to match the `event` field
    values used in the LST files.
    """

    if not os.path.exists(meetsetupfile):
        raise FileNotFoundError(f"meetsetup.xml not found at {meetsetupfile}")

    try:
        tree = ET.parse(meetsetupfile)
        root = tree.getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML in meetsetup.xml: {exc}") from exc

    event_titles: Dict[int, str] = {}

    events_elem = root.find("Events")
    if events_elem is None:
        raise ValueError("No Events element found in meetsetup.xml")

    for ev in events_elem.findall("Event"):
        num_text = ev.findtext("EventNumber") or ""
        desc_text = ev.findtext("EventDescription") or ""

        num = num_text.strip()
        desc = desc_text.strip()

        if not num or not desc:
            continue

        event_titles[int(num)] = desc

    return event_titles


def load_all_files() -> None:
    global contestants, events, event_titles, event_texts, lst_files_last_mtime

    lst_files_last_mtime = get_mtime((startfile, concfile,
                                      meetsetupfile, eventdescfile))

    try:
        contestants = load_contestants_from_lstconc(concfile)
    except Exception as exc:
        print(f"[comms] ERROR: could not load lstconc contestants: {exc}",
              file=sys.stderr)
        sys.exit(1)

    try:
        events = load_events_from_lststart(startfile)
    except Exception as exc:
        print(f"[comms] ERROR: could not load lststart events: {exc}",
              file=sys.stderr)
        sys.exit(1)

    try:
        event_titles, event_texts = load_event_titles_from_json(eventdescfile)
    except Exception:
        try:
            event_titles = load_event_titles_from_meetsetup(meetsetupfile)
        except Exception as exc:
            print("[comms] ERROR: could not load event "
                  f"titles: {exc}", file=sys.stderr)
            sys.exit(1)

    print(f"[comms] Loaded {len(contestants)} contestants")
    print(f"[comms] Loaded {len(events)} events")
    print(f"[comms] Loaded {len(event_titles)} event titles")
    print(f"[comms] Loaded {len(event_texts)} event texts")


def get_mtime(files: tuple[str]) -> Optional[float]:
    """Get the last modification time of any of the LST files directory.

    Returns None if the files do not exist or their mtimes cannot be determined.
    """

    try:
        mtimes = [os.path.getmtime(path)
                  for path in files
                  if os.path.exists(path)]
        lst_files_last_mtime = max(mtimes) if mtimes else None
    except Exception:
        lst_files_last_mtime = None

    return lst_files_last_mtime


def _comp_split(time0: str, time1: str) -> str:
    """Compute split time between two time strings in format Hh:Mm:Sd.dc.

    Leading parts (hours, minutes) are optional; at minimum seconds must be
    present. Returns the split as a string in Hh:Mm:SS.dc format, where
    leading hours or minutes are omitted if zero. Returns an empty string
    on any error or if time1 <= time0.
    """

    def _parse_to_centis(value: str) -> int:
        """Parse a time string into centiseconds.

        Supported forms:
          - "SS" or "SS.dc" (seconds with optional decimals)
          - "M:SS" or "M:SS.dc" (minutes and seconds)
          - "H:M:SS" or "H:M:SS.dc" (hours, minutes, seconds)
        """

        text = (value or "").strip()
        if not text:
            raise ValueError("empty time string")

        parts = text.split(":")
        if len(parts) == 1:
            hours = 0
            minutes = 0
            sec_str = parts[0]
        elif len(parts) == 2:
            hours = 0
            minutes = int(parts[0])
            sec_str = parts[1]
        elif len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            sec_str = parts[2]
        else:
            raise ValueError("too many ':' separators in time string")

        sec_str = sec_str.strip()
        if not sec_str:
            raise ValueError("missing seconds component")

        seconds = float(sec_str)
        if hours < 0 or minutes < 0 or seconds < 0:
            raise ValueError("negative time component")

        total_centis = int(round(seconds * 100))
        total_centis += (minutes * 60 + hours * 3600) * 100
        return total_centis

    try:
        t0 = _parse_to_centis(time0)
        t1 = _parse_to_centis(time1)
    except (ValueError, TypeError):
        return ""

    if t1 <= t0:
        return ""

    diff = t1 - t0

    hours = diff // (3600 * 100)
    rem = diff % (3600 * 100)
    minutes = rem // (60 * 100)
    rem = rem % (60 * 100)
    seconds = rem // 100
    centis = rem % 100

    sec_str = f"{seconds:02d}.{centis:02d}"

    if hours > 0:
        return f"{hours}:{minutes:02d}:{sec_str}"
    if minutes > 0:
        return f"{minutes}:{sec_str}"
    return sec_str


def send_lanes_for_heat(event_id: int, heat_id: int, timeout: float = 5.0):
    """Build a lanes structure for the given event/heat and POST to /api/lanes.

    The resulting payload matches the structure expected by main.py's
    /api/lanes endpoint:

        {"lanes": [
            {"lane": 1, "name": "...", "rank": "", "time": "", "split": "", "dist": ""},
            ...
        ]}

    Only the `lane` and `name` fields are populated; the other fields
    are sent as empty strings so the scoreboard can fill them later.
    """

    heats = events.get(event_id)
    if not heats:
        print(f"[comms] Unknown event id {event_id!r} in events mapping")
        empty_lanes()
        return

    lanes_map = heats.get(heat_id)
    if not lanes_map:
        print(f"[comms] Unknown heat id {heat_id!r} for event {event_id!r}")
        empty_lanes()
        return

    lanes_payload = []

    for lane_id in sorted(lanes_map.keys()):
        id_bib = lanes_map[lane_id]
        swimmer = contestants.get(id_bib, {})
        name = swimmer.get("name", "")

        lanes_payload.append(
            {
                "lane": lane_id,
                "name": name,
                "rank": "",
                "time": "",
                "split": "",
                "dist": "",
                "finished": False,
            }
        )

        # Initialize splits for the lane, this is to be able to remove the
        # names for the swimemrs who did not finish later.
        splits.setdefault(lane_id, {})

    call("/lanes", {"lanes": lanes_payload})


def empty_lanes(timeout : float = 5.0):
    """Send an empty lanes structure to the scoreboard.

    This resets all lanes on the scoreboard.
    """

    lanes_payload = []
    call("/lanes", {"lanes": lanes_payload})


def set_heat_title(event_id: int, heat_id: int, timeout: float = 5.0):
    """Set the current event and heat titles on the scoreboard.
    """

    # Check if any of the lst files have changed since last load - if so,
    # reload.
    mtime = get_mtime((startfile, concfile, meetsetupfile))
    if mtime > lst_files_last_mtime:
        print("[comms] Detected LST file changes, reloading LST files...")
        load_all_files()

    event_title = event_titles.get(event_id, f"Event {event_id}")
    heat_title = f"Heat {heat_id}"
    payload = {"race_title": event_title,  "heat": heat_title}
    event_text = event_texts.get(event_id, "")
    payload["event_text"] = event_text

    call("/header", payload)


def prepare_heat(event_id: int, heat_id: int):
    """Notify the scoreboard of a new heat starting.

    This sets the event and heat titles accordingly and fills the lanes.
    """

    # Reset all splits for the new heat
    global splits
    splits = {}

    global current_event, current_heat
    current_event = event_id
    current_heat = heat_id

    try:
        call("/sort_by_lane", {})
        call("/timer/reset", {})
    except Exception as exc:
        print(f"[comms] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        set_heat_title(event_id, heat_id)
    except Exception as exc:
        print(f"[comms] ERROR: could not set heat title: {exc}",
              file=sys.stderr)
        sys.exit(1)

    try:
        send_lanes_for_heat(event_id, heat_id)
    except Exception as exc:
        print(f"[comms] ERROR: could not send lanes: {exc}", file=sys.stderr)


def _clear_unfinished_lanes():
    """Clear lanes that have not finished the lap yet.
    """

    maxlaps = 0
    for lane_splits in splits.values():
        this_max = max(lane_splits.keys()) if lane_splits else 0
        maxlaps = max(maxlaps, this_max)

    for lane, lane_splits in splits.items():
        if maxlaps not in lane_splits:
            # This lane did not finish the last lap; clear its name.
            try:
                payload = {
                    "lane": lane,
                    "name": "",
                    "rank": "",
                    "time": "",
                    "split": "",
                    "dist": "",
                }
                call("/lane", payload)
            except Exception as exc:
                print(f"[comms] ERROR: could not clear lane {lane}: {exc}",
                      file=sys.stderr)
                sys.exit(1)


def finish_heat():
    """Finish the current heat after the last swimmer has finished.

    This resets the timer and sorts the lanes by rank.
    """

    # Clear unfinished lanes
    _clear_unfinished_lanes()

    try:
        call("/timer/reset", {})
        call("/sort_by_rank", {})
    except Exception as exc:
        print(f"[comms] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if hold_results_time > 0.0:
        time.sleep(hold_results_time)


def start_timer():
    """Start the timer for the current heat.
    """

    try:
        call("/timer/start", {})
    except Exception as exc:
        print(f"[comms] ERROR: could not start timer: {exc}", file=sys.stderr)
        sys.exit(1)


def split(event: int, heat: int, lane: int, lap: int, rank: int, time: str,
          finished: bool = False):
    """Process a split time for the given lane.
    """

    # Verify that the split corresponds to the current event and heat.
    if event != current_event or heat != current_heat:
        print(f"[comms] WARNING: received split for event {event}, "
              f"heat {heat}, but current event is {current_event}, "
              f"heat {current_heat}; ignoring.", file=sys.stderr)
        return

    # If lap-1 time exists, compute split time for current lap
    lane_splits = splits.setdefault(lane, {})
    if lap == 1:
        # First lap, no previous split
        split_time = time
    elif (lap - 1) in lane_splits:
        prev_time = lane_splits[lap - 1]
        split_time = _comp_split(prev_time, time)
    else:
        split_time = ""

    # Store the current lap time for next split computation
    lane_splits[lap] = time

    # Construct update to scoreboard
    payload = {"lane": lane, "rank": str(rank), "time": time,
               "split": split_time, "lap": lap, "finished": finished}
    try:
        call("/lane", payload)
    except Exception as exc:
        print(f"[comms] ERROR: could not set lane: {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    """Entry point for the comms process.

    The first action is to retrieve timing configuration from the
    scoreboard server. Further communication logic can then use
    this configuration (e.g. LST path, serial port settings).
    """

    # The scoreboard server may not be ready the instant this process
    # starts, so retry the timing-config request for a short period.
    timing_cfg: Dict[str, Any] | None = None
    max_attempts = 10
    delay_seconds = 1.0

    for attempt in range(1, max_attempts + 1):
        try:
            timing_cfg = fetch_timing_config()
            break
        except Exception as exc:
            print(
                f"[comms] ERROR: could not fetch timing configuration "
                f"(attempt {attempt}/{max_attempts}): {exc}",
                file=sys.stderr,
            )
            if attempt == max_attempts:
                sys.exit(1)
            time.sleep(delay_seconds)

    debug_path = timing_cfg.get("debug_path", "")
    debug_enabled = debug_path != ""
    serial_debug.configure(debug_enabled, debug_path)
    if serial_debug.enabled and serial_debug.file_path is not None:
        print("[comms] Serial debug capture enabled; writing to "
              f"{serial_debug.file_path}")

    # Load LST-based data once at startup.
    load_all_files()

    global hold_results_time
    hold_results_time = timing_cfg.get("hold_results_time", 0.0)

    serial_kwargs = _parse_serial_settings(timing_cfg)

    # Open the serial port, keep trying every 5 seconds if something fails.
    while True:
        try:
            with serial.Serial(**serial_kwargs) as ser:  # type: ignore[call-arg]
                print("[comms] Serial port opened; listening for messages...")
                for pt1, pt2 in _next_message(ser):
                    _handle_timing_message(pt1, pt2)

        except KeyboardInterrupt:
            print("[comms] Interrupted, shutting down.")
            break
        except serial.SerialException as exc:
            # Print the exception type for easier debugging
            print(f"[comms] ERROR: serial loop failed: {exc}", file=sys.stderr)
            print("[comms] Re-trying to open serial port in 5 seconds...")
            time.sleep(5.0)


if __name__ == "__main__":
    main()
