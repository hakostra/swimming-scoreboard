"""Communication helper process for the swimming scoreboard.

On startup this module contacts the scoreboard server running on
localhost and retrieves the timing configuration via the
`/api/timing_config` endpoint defined in the server module.

The retrieved configuration can then be used by whatever logic
is responsible for talking to the external timing system
(reading LST files, opening a serial port, etc.).
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys
import signal
import time
import queue
import threading
from typing import Any, Dict, Optional, TextIO, Tuple

from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import serial

from .utils import LstDataStore, parse_serial_settings, parse_to_centis


SCOREBOARD_API = "http://127.0.0.1:8000/api"


# Global state populated at startup and reused by helper functions.
lst_data: Optional[LstDataStore] = None

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

    def configure(self, file_name: str) -> None:
        path_str = (file_name or "").strip()
        if not path_str:
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
        print(f"[comms] Serial debug capture enabled; writing to {path}")
        self.enabled = True
        self.file_path = path
        self._fh = fh

    def record(self, ts: float, payload: bytes) -> None:
        if not payload or not self.enabled or self._fh is None:
            return

        iso = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc) \
            .isoformat()
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


def call(endpoint: str, payload: dict | None = None, timeout: float = 5.0) \
        -> str:
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
        raise RuntimeError(f"Failed to POST to {url}: {exc}") from exc

    return body


def _serial_listener(serial_kwargs: Dict[str, Any], debug_path: str,
                     message_queue: "queue.Queue[Tuple[float, bytes]]",
                     stop_event: "threading.Event") -> None:
    """Listen to the serial port and enqueue raw messages with timestamps."""

    serial_debug = SerialDebugRecorder()
    serial_debug.configure(debug_path)

    # Use a timeout so we can periodically check stop_event
    serial_kwargs_with_timeout = serial_kwargs.copy()
    serial_kwargs_with_timeout["timeout"] = 2.0

    try:
        while not stop_event.is_set():
            try:
                with serial.Serial(**serial_kwargs_with_timeout) as ser:
                    print(f"[comms] {serial_kwargs_with_timeout['port']} "
                          "opened; listening for messages...")
                    buffer = b""
                    while not stop_event.is_set():
                        data = ser.read_until(eot)
                        if not data:
                            # Timeout, no data received; continue to check
                            # stop_event
                            continue

                        # Record timestamp immediately when bytes arrive
                        ts = time.time()

                        buffer += data
                        while True:
                            eot_idx = buffer.find(eot)
                            if eot_idx < 0:
                                break
                            frame = buffer[:eot_idx + 1]
                            buffer = buffer[eot_idx + 1:]
                            if frame:
                                serial_debug.record(ts, frame)
                                message_queue.put((ts, frame))

                        # Safety: prevent unbounded buffer growth if no EOT
                        if len(buffer) > 256:
                            print("[comms] WARNING: serial buffer overflow; "
                                  "truncating buffer")
                            buffer = buffer[-64:]

            except serial.SerialException as exc:
                print(f"[comms] ERROR: serial loop failed: {exc}",
                      file=sys.stderr)
                if stop_event.is_set():
                    break
                print("[comms] Re-trying to open serial port in 5 seconds...")
                time.sleep(5.0)
            except Exception as exc:
                print(f"[comms] ERROR: serial listener failed: {exc}",
                      file=sys.stderr)
                if stop_event.is_set():
                    break
                time.sleep(5.0)
    finally:
        serial_debug.close()

    print("[serial] Listener process shutting down.")


def _next_message_from_queue(
    message_queue: "queue.Queue[Tuple[float, bytes]]",
    stop_event: "threading.Event",
) -> Tuple[bytes, bytes, float]:
    """Yield (pt1, pt2, ts) message pairs from the queue.

    Alive message:
    [SOH][DC2]9[DC4]TP[EOT]

    Other messages:
    [SOH][STX][HOME] <data> [EOT]

    Alive messages are immediately discarded. Other messages are sent in pairs:

    Part 1 is : [SOH][STX][HOME]ABCDDEEFFFGG¬¬HH[EOT]            (20 bytes)
    Part 2 is : [SOH][STX][HOME][LF]JKK[STX]Hh:Mm:Ss.dc¬[EOT]    (21 bytes)

    The symbol ¬ represents a space character (0x20).

    This generator always yields complete pairs ``(pt1, pt2, ts)``. If a
    message that looks like pt2 (identified by the 4th byte being LF) is
    received before any valid pt1 has been seen, that message is discarded
    and the generator waits for a new pt1.
    """

    pending_pt1: Optional[bytes] = None
    pending_ts: Optional[float] = None

    header_len = len(soh) + len(stx) + len(home)

    while not stop_event.is_set():
        try:
            ts, data = message_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        # Check if data starts with SOH, discard if not.
        if not data.startswith(soh):
            print(f"[comms] WARNING: unknown serial message: {data!r}",
                  file=sys.stderr)
            continue

        # Discard alive messages exactly matching the known pattern.
        if data == soh + dc2 + b"9" + dc4 + b"TP" + eot:
            continue

        # Other messages must have [SOH][STX][HOME] prefix and end with EOT.
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
            pending_ts = ts
            continue

        # From here on, the message is pt2.
        if pending_pt1 is None:
            # We received a pt2 without a corresponding pt1; discard.
            print("[comms] WARNING: received pt2 without"
                  "preceding pt1; discarding", file=sys.stderr)
            continue

        pt1 = pending_pt1
        pt2 = payload
        result_ts = pending_ts
        pending_pt1 = None
        pending_ts = None
        yield pt1, pt2, result_ts


def _handle_timing_message(pt1, pt2, ts: float):
    """Handle a timing message received from the serial port.

    Expects two parts: pt1 and pt2 as bytes, and ts as the epoch timestamp
    when the message was received on the serial port. The messages are:

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
    # C = pt1[2:3].decode("ascii")
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
        start_timer(ts)

    # Split times and finish times are handled similarly
    elif (A == "2") and (B in ("I", "A")):
        event_id = int(FFF)
        heat_id = int(GG)
        lane_id = int(J)
        lap_num = int(KK)
        rank = int(HH)
        finished = (B == "A")
        split(event_id, heat_id, lane=lane_id, lap=lap_num, rank=rank,
              timestr=time_str, finished=finished)

    # Official end: finish heat
    elif A == "1":
        finish_heat()

    else:
        pass  # Unknown message type; ignore.


def fetch_timing_config() -> Dict[str, Any]:
    """Fetch timing configuration JSON from the scoreboard server.

    Returns only the `timing_config` document from the API response.
    Raises an exception if the request fails or the response is invalid.
    """

    body = call("/timing_config")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Timing config did not return valid JSON") from exc

    if not isinstance(payload, dict):
        raise ValueError("Unexpected timing config response structure "
                         "(expected JSON object)")

    cfg = payload.get("timing_config")
    if not isinstance(cfg, dict):
        raise ValueError("Response missing 'timing_config' object")

    return cfg


def _comp_split(time0: str, time1: str) -> str:
    """Compute split time between two time strings in format Hh:Mm:Sd.dc.

    Leading parts (hours, minutes) are optional; at minimum seconds must be
    present. Returns the split as a string in Hh:Mm:SS.dc format, where
    leading hours or minutes are omitted if zero. Returns an empty string
    on any error or if time1 <= time0.
    """

    try:
        t0 = parse_to_centis(time0)
        t1 = parse_to_centis(time1)
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

    if lst_data is None:
        print("[comms] ERROR: LST data not initialized", file=sys.stderr)
        sys.exit(1)

    try:
        lst_data.reload_if_changed()
    except Exception as exc:
        print(f"[comms] ERROR: could not reload LST files: {exc}",
              file=sys.stderr)
        sys.exit(1)

    event_title = lst_data.event_titles.get(event_id, f"Event {event_id}")
    heat_title = f"Heat {heat_id}"
    event_text = lst_data.event_texts.get(event_id, "")

    lanes_payload = []
    heats = lst_data.events.get(event_id)
    lanes_map = heats.get(heat_id) if heats else None
    if lanes_map:
        for lane_id in sorted(lanes_map.keys()):
            id_bib = lanes_map[lane_id]
            swimmer = lst_data.contestants.get(id_bib, {})
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

            splits.setdefault(lane_id, {})
    else:
        print(
            f"[comms] Unknown event/heat (event {event_id!r}, "
            f"heat {heat_id!r}); sending empty lanes",
            file=sys.stderr,
        )

    payload = {
        "race_title": event_title,
        "heat": heat_title,
        "event_text": event_text,
        "lanes": lanes_payload,
    }

    try:
        call("/prepare_heat", payload)
    except Exception as exc:
        print(f"[comms] ERROR: could not prepare heat: {exc}", file=sys.stderr)
        sys.exit(1)


def finish_heat():
    """Finish the current heat after the last swimmer has finished.

    This resets the timer and sorts the lanes by rank.
    """

    try:
        call("/finish_heat", {})
    except Exception as exc:
        print(f"[comms] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if hold_results_time > 0.0:
        time.sleep(hold_results_time)


def start_timer(ts: float):
    """Start the timer for the current heat.

    The ts parameter is the epoch timestamp when the start signal was
    received on the serial port. This can be used for more accurate
    timer synchronization.
    """

    try:
        payload = {}
        if ts is not None:
            payload["ts"] = ts
        call("/timer/start", payload)
    except Exception as exc:
        print(f"[comms] ERROR: could not start timer: {exc}", file=sys.stderr)
        sys.exit(1)


def split(event: int, heat: int, lane: int, lap: int, rank: int, timestr: str,
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
        split_time = timestr
    elif (lap - 1) in lane_splits:
        prev_time = lane_splits[lap - 1]
        split_time = _comp_split(prev_time, timestr)
    else:
        split_time = ""

    # Store the current lap time for next split computation
    lane_splits[lap] = timestr

    # Construct update to scoreboard
    payload = {"lane": lane, "rank": str(rank), "time": timestr,
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

    global lst_data
    lst_data = LstDataStore(timing_cfg["lst_path"])

    try:
        lst_data.load_all()
    except Exception as exc:
        print(f"[comms] ERROR: could not load LST files: {exc}",
              file=sys.stderr)
        sys.exit(1)

    global hold_results_time
    hold_results_time = timing_cfg.get("hold_results_time", 0.0)

    serial_kwargs = parse_serial_settings(timing_cfg.get("com_settings"))
    serial_kwargs["port"] = timing_cfg.get("com_port") or ""

    # Debug configuration to pass to the serial listener process
    debug_path = timing_cfg.get("debug_path", "")

    message_queue: "queue.Queue[Tuple[float, bytes]]" = queue.Queue()
    stop_event = threading.Event()

    def _shutdown_handler(signum, _frame) -> None:
        stop_event.set()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        signal.signal(sigbreak, _shutdown_handler)

    listener = threading.Thread(
        target=_serial_listener,
        args=(serial_kwargs, debug_path, message_queue, stop_event),
        daemon=True,
        name="serial-listener",
    )
    listener.start()

    try:
        for pt1, pt2, ts in _next_message_from_queue(
                message_queue, stop_event):
            _handle_timing_message(pt1, pt2, ts)
    except KeyboardInterrupt:
        print("[comms] Interrupted, shutting down.")
    finally:
        stop_event.set()
        listener.join(timeout=4.0)
        if listener.is_alive():
            print("[comms] Serial listener thread did not stop; continuing.")

    print("[comms] Shutdown complete.")


if __name__ == "__main__":
    main()
