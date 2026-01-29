"""Shared utilities for the swimming scoreboard."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, Optional, Tuple
import xml.etree.ElementTree as ET

import serial


def parse_to_centis(value: str) -> int:
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


def parse_serial_settings(value: str) -> Dict[str, object]:
    """Parse serial settings from a settings string.

    The expected form is "9600,7,n,1" (baud,data bits,parity,stop bits).
    """

    settings_str = value or "9600,7,n,1"

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

    serial_kwargs: Dict[str, object] = {
        "baudrate": baudrate,
        "bytesize": bytesize_map.get(bytesize, serial.EIGHTBITS),
        "parity": parity_map.get(parity, serial.PARITY_NONE),
        "stopbits": stopbits_map.get(stopbits, serial.STOPBITS_ONE),
        # Reasonable defaults; can be adjusted if needed.
        "timeout": None,
    }

    return serial_kwargs


class LstDataStore:
    """Load and cache contestant/event data from the timing system files."""

    def __init__(self, lst_path: str | Path) -> None:
        base_dir = Path(lst_path)
        self.startfile = base_dir / "lststart.txt"
        self.concfile = base_dir / "lstconc.txt"
        self.meetsetupfile = base_dir / "meetsetup.xml"
        self.racesfile = base_dir / "lstrace.txt"
        self.longfile = base_dir / "lstlong.txt"
        self.roundfile = base_dir / "lstround.txt"
        self.eventsfile = base_dir / "events.json"
        self.clubsfile = base_dir / "clubs.json"

        self.contestants: Dict[int, Dict[str, str]] = {}
        self.events: Dict[int, Dict[int, Dict[int, int]]] = {}
        self.event_titles: Dict[int, str] = {}
        self.event_texts: Dict[int, str] = {}
        self.clubs: Dict[str, str] = {}
        self.lst_files_last_mtime: Optional[float] = None

    def load_all(self) -> None:
        """Load all LST-based data into memory."""

        self.lst_files_last_mtime = self._get_mtime(
              (self.startfile, self.concfile, self.eventsfile, self.clubsfile)
        )

        contestants = self._load_contestants_from_lstconc(self.concfile)
        clubs = self._load_clubs_json(self.clubsfile)
        events = self._load_events_from_lststart(self.startfile)

        if self.eventsfile.exists():
            event_titles, event_texts = self._load_event_from_json(
                self.eventsfile
            )
        else:
            if self.meetsetupfile.exists():
                event_titles, event_texts = self._load_event_from_meetsetup(
                    self.meetsetupfile, self.eventsfile
                )
            else:
                event_titles, event_texts = self._load_event_from_lstrace(
                    self.racesfile, self.longfile, self.roundfile,
                    self.eventsfile
                )

        self.contestants = contestants
        self.clubs = clubs
        self.events = events
        self.event_titles = event_titles
        self.event_texts = event_texts

        self.load_summary()

    def load_summary(self, prefix: str = "[comms]") -> None:
        """Print a one-shot summary of currently loaded LST data."""

        print(f"{prefix} Loaded {len(self.contestants)} contestants")
        print(f"{prefix} Loaded {len(self.events)} events")
        print(f"{prefix} Loaded {len(self.event_titles)} event titles")
        print(f"{prefix} Loaded {len(self.event_texts)} event texts")
        print(f"{prefix} Loaded {len(self.clubs)} club mappings")

    def reload_if_changed(self) -> bool:
        """Reload data if any source files have changed.

        Returns True if a reload occurred.
        """

        current_mtime = self._get_mtime(
            (self.startfile, self.concfile, self.eventsfile, self.clubsfile)
        )
        if current_mtime is None:
            return False

        if self.lst_files_last_mtime is None or \
                current_mtime > self.lst_files_last_mtime:
            self.load_all()
            return True

        return False

    @staticmethod
    def _get_mtime(files: tuple[Optional[str | Path], ...]) -> Optional[float]:
        """Get the last modification time of any of the LST files."""

        try:
            mtimes = []
            for path in files:
                if not path:
                    continue
                p = Path(path)
                if p.exists():
                    mtimes.append(p.stat().st_mtime)
            return max(mtimes) if mtimes else None
        except Exception:
            return None

    @staticmethod
    def _load_contestants_from_lstconc(lst_file: str | Path) \
            -> Dict[int, Dict[str, str]]:
        """Load contestants from lstconc.txt using timing configuration."""

        path = Path(lst_file)
        if not path.exists():
            raise FileNotFoundError(f"lstconc file not found at {path}")

        contestants: Dict[int, Dict[str, str]] = {}
        clubs_seen: set[str] = set()

        with path.open("r", encoding="iso-8859-1", newline="") as f:
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
                if firstname and lastname:
                    name = f"{firstname} {lastname}"
                else:
                    name = firstname or lastname

                club = (row.get("abNat") or "").strip()
                if club:
                    clubs_seen.add(club)

                contestants[key] = {
                    "name": name,
                    "club": club,
                }

        LstDataStore._update_clubs_json(path.parent / "clubs.json",
                                        clubs_seen)

        return contestants

    @staticmethod
    def _update_clubs_json(clubs_file: Path, clubs_seen: set[str]) -> None:
        """Create or update clubs.json with any newly seen clubs."""

        if not clubs_seen:
            return

        existing: Dict[str, str] = {}
        if clubs_file.exists():
            try:
                with clubs_file.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    existing = {str(k): str(v) for k, v in data.items()}
            except Exception:
                existing = {}

        new_keys = False
        for club in sorted(clubs_seen):
            if club not in existing:
                existing[club] = ""
                new_keys = True

        if not new_keys:
            return

        try:
            with clubs_file.open("w", encoding="utf-8") as fh:
                json.dump(existing, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
        except Exception:
            pass

    @staticmethod
    def _load_clubs_json(clubs_file: Path) -> Dict[str, str]:
        """Load clubs.json mapping (short code -> display name)."""

        if not clubs_file.exists():
            return {}

        try:
            with clubs_file.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            return {}

        return {}

    @staticmethod
    def _load_events_from_lststart(lst_file: str | Path) \
            -> Dict[int, Dict[int, Dict[int, int]]]:
        """Load event/heat/lane start list from lststart.txt."""

        path = Path(lst_file)
        if not path.exists():
            raise FileNotFoundError(f"lststart file not found at {path}")

        events: Dict[int, Dict[int, Dict[int, int]]] = {}

        with path.open("r", encoding="iso-8859-1", newline="") as f:
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
                lanes = heats.setdefault(int(heat_id) + 1, {})
                lanes[int(lane_id)] = int(id_bib)

        return events

    @staticmethod
    def _load_event_from_json(eventsfile: str | Path) \
            -> Tuple[Dict[int, str], Dict[int, str]]:
        """Load event titles and texts from events.json."""

        path = Path(eventsfile)
        if not path.exists():
            raise FileNotFoundError(f"events.json not found at {path}")

        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in events.json: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise ValueError("events.json does not contain a JSON object")

        event_titles: Dict[int, str] = {}
        event_texts: Dict[int, str] = {}

        for key, value in data.items():
            try:
                event_num = int(str(key).strip())
            except (TypeError, ValueError):
                continue

            if not isinstance(value, dict):
                continue

            title = str(value.get("title") or "").strip()
            text = str(value.get("text") or "").strip()

            if title:
                event_titles[event_num] = title
            event_texts[event_num] = text

        return event_titles, event_texts

    @staticmethod
    def _load_event_from_meetsetup(meetsetupfile: str | Path,
                                   eventsfile: str | Path) \
            -> Tuple[Dict[int, str], Dict[int, str]]:
        """Load event titles/texts from meetsetup.xml and write events.json."""

        path = Path(meetsetupfile)
        if not path.exists():
            raise FileNotFoundError(f"meetsetup.xml not found at {path}")

        try:
            tree = ET.parse(path)
            root = tree.getroot()
        except ET.ParseError as exc:
            raise ValueError(f"Invalid XML in meetsetup.xml: {exc}") from exc

        event_titles: Dict[int, str] = {}
        event_texts: Dict[int, str] = {}
        payload: Dict[str, Dict[str, str]] = {}

        events_elem = root.find("Events")
        if events_elem is None:
            raise ValueError("No Events element found in meetsetup.xml")

        for ev in events_elem.findall("Event"):
            num = (ev.findtext("EventNumber") or "").strip()
            desc = (ev.findtext("EventDescription") or "").strip()
            sponsor = (ev.findtext("Sponsor") or "").strip()

            if not num or not desc:
                continue

            event_num = int(num)
            text = f"Sponsor: {sponsor}" if sponsor else ""

            event_titles[event_num] = desc
            event_texts[event_num] = text
            payload[str(event_num)] = {
                "title": desc,
                "text": text,
            }

        try:
            events_path = Path(eventsfile)
            with events_path.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
        except Exception:
            pass

        return event_titles, event_texts

    @staticmethod
    def _load_event_from_lstrace(racesfile: str | Path,
                                 longfile: str | Path,
                                 roundfile: str | Path,
                                 eventsfile: str | Path) \
            -> Tuple[Dict[int, str], Dict[int, str]]:
        """Load event titles/texts from lstrace/lstlong/lstround."""

        races_path = Path(racesfile)
        long_path = Path(longfile)
        round_path = Path(roundfile)

        if not races_path.exists():
            raise FileNotFoundError(f"lstrace file not found at {races_path}")
        if not long_path.exists():
            raise FileNotFoundError(f"lstlong file not found at {long_path}")
        if not round_path.exists():
            raise FileNotFoundError(f"lstround file not found at {round_path}")

        style_map = {
            0: "Freestyle",
            1: "Backstroke",
            2: "Breaststroke",
            3: "Butterfly",
            4: "Medley",
        }

        lengths: Dict[int, str] = {}
        with long_path.open("r", encoding="iso-8859-1", newline="") as f:
            reader = csv.DictReader(f, delimiter=";", quotechar='"',
                                    skipinitialspace=True)
            for row in reader:
                if not row:
                    continue
                try:
                    key = int(row["idLength"])
                    label = row["Longueur"].strip()
                    lengths[key] = label
                except (KeyError, ValueError):
                    print(f"Warning: Skipping invalid length row: {row}")
                    continue

        rounds: Dict[int, str] = {}
        with round_path.open("r", encoding="iso-8859-1", newline="") as f:
            reader = csv.DictReader(f, delimiter=";", quotechar='"',
                                    skipinitialspace=True)
            for row in reader:
                if not row:
                    continue
                try:
                    key = int(row["idRound"])
                    title = row["TITLE"].strip()
                    rounds[key] = title
                except (KeyError, ValueError):
                    print(f"Warning: Skipping invalid round row: {row}")
                    continue

        event_titles: Dict[int, str] = {}
        event_texts: Dict[int, str] = {}
        payload: Dict[str, Dict[str, str]] = {}

        with races_path.open("r", encoding="iso-8859-1", newline="") as f:
            reader = csv.DictReader(f, delimiter=";", quotechar='"',
                                    skipinitialspace=True)
            for row in reader:
                if not row:
                    continue

                try:
                    event_num = int(row["event"])
                    len_id = int(row["idLen"])
                    style_id = int(row["idStyle"])
                    round_id = int(row["round"])
                    gender = row["abCat"].strip()
                except (KeyError, ValueError):
                    print(f"Warning: Skipping row with invalid event: {row}")
                    continue

                try:
                    length = lengths[len_id]
                    style = style_map[style_id]
                    round = rounds[round_id]
                    gender = gender if gender != "X" else "Mixed"

                    title = (f"Event {event_num}: {length} {style}, "
                             f"{round}, {gender}")
                except KeyError:
                    title = f"Event {event_num}"

                text = ""
                event_titles[event_num] = title
                event_texts[event_num] = text
                payload[str(event_num)] = {
                    "title": title,
                    "text": text,
                }

        try:
            events_path = Path(eventsfile)
            with events_path.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
        except Exception:
            pass

        return event_titles, event_texts
