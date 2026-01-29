#!/usr/bin/env python3
"""Replay captured SerialDebugRecorder output to a serial port.

This reads the JSONL log file produced by SerialDebugRecorder.record()
(in comms.py) and writes each captured payload to the given serial port.
Optionally preserves the original timing between frames.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

import serial

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scoreboard.utils import parse_serial_settings

def _iter_entries(log_path: Path) -> Iterable[dict]:
    with log_path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(f"[replay] Invalid JSON on line {line_no}: {exc}")
                continue
            if "data_hex" not in entry:
                print(f"[replay] Skipping line {line_no}: missing data_hex")
                continue
            yield entry


def _entry_time(entry: dict) -> Optional[float]:
    ts_epoch = entry.get("ts_epoch")
    try:
        return float(ts_epoch)
    except (TypeError, ValueError):
        return None


def replay(log_path: Path, port: str, *, settings: str, speed: float,
           no_sleep: bool, skip: int) -> None:
    entries = list(_iter_entries(log_path))
    if not entries:
        print("[replay] No valid entries to replay.")
        return

    if skip > 0:
        if skip >= len(entries):
            print("[replay] Skip count exceeds available entries.")
            return
        entries = entries[skip:]

    log_start_ts: float = 0.0
    replay_start_epoch: float = 0.0
    serial_kwargs = parse_serial_settings(settings)
    serial_kwargs["port"] = port
    serial_kwargs["timeout"] = 0
    with serial.Serial(**serial_kwargs) as ser:  # type: ignore[call-arg]
        for idx, entry in enumerate(entries, start=1):
            payload_hex = entry.get("data_hex")
            try:
                payload = bytes.fromhex(payload_hex)
            except (TypeError, ValueError):
                print(f"[replay] Skipping entry {idx}: invalid hex payload")
                continue

            if not no_sleep:
                current_ts = _entry_time(entry)
                if idx == 1:
                    log_start_ts = current_ts
                    replay_start_epoch = time.time()
                target_epoch = replay_start_epoch + (
                    (current_ts - log_start_ts) / max(speed, 1e-6)
                )
                delay = target_epoch - time.time()
                if delay > 0:
                    time.sleep(delay)

            if not payload:
                continue

            print(f"[replay] Emitting entry {idx+skip} with "
                  f"timestamp {entry.get('ts')}")
            ser.write(payload)
            ser.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay a SerialDebugRecorder log to a serial port.",
    )
    parser.add_argument(
        "--port",
        required=True,
        help="Serial port to write to (e.g., /dev/pts/3)",
    )
    parser.add_argument(
        "--settings",
        default="9600,7,n,1",
        help=(
            "Serial settings as 'baud,data,parity,stop' (default: 9600,7,n,1)"
        ),
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Replay speed multiplier for log timestamps (default: 1.0)",
    )
    parser.add_argument(
        "--no-sleep",
        action="store_true",
        help="Disable timing gaps between log entries",
    )
    parser.add_argument(
        "--skip",
        type=int,
        default=0,
        help="Skip the first N log entries before replaying (default: 0)",
    )
    parser.add_argument(
        "logfile",
        help="Path to results.log produced by SerialDebugRecorder",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    log_path = Path(args.logfile).expanduser()
    if not log_path.exists():
        raise SystemExit(f"Log file not found: {log_path}")

    replay(
        log_path,
        args.port,
        settings=args.settings,
        speed=args.speed,
        no_sleep=args.no_sleep,
        skip=max(0, args.skip),
    )


if __name__ == "__main__":
    main()

# Make a virtual serial port pair: socat -d -d pty,raw,echo=0 pty,raw,echo=0
