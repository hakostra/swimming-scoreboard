#!/usr/bin/env python3
"""Replay captured SerialDebugRecorder output to a serial port.

This reads the JSONL log file produced by SerialDebugRecorder.record()
(in comms.py) and writes each captured payload to the given serial port.
Optionally preserves the original timing between frames.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Iterable, Optional

import serial


def _iter_entries(log_path: Path) -> Iterable[dict]:
    with log_path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(f"[replay] Skipping invalid JSON on line {line_no}: {exc}")
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


def replay(log_path: Path, port: str, *, baudrate: int, speed: float,
           no_sleep: bool) -> None:
    entries = list(_iter_entries(log_path))
    if not entries:
        print("[replay] No valid entries to replay.")
        return

    last_ts: Optional[float] = None
    with serial.Serial(port=port, baudrate=baudrate, timeout=0) as ser:
        for idx, entry in enumerate(entries, start=1):
            payload_hex = entry.get("data_hex")
            try:
                payload = bytes.fromhex(payload_hex)
            except (TypeError, ValueError):
                print(f"[replay] Skipping entry {idx}: invalid hex payload")
                continue

            if not no_sleep:
                current_ts = _entry_time(entry)
                if current_ts is not None and last_ts is not None:
                    delta = max(0.0, (current_ts - last_ts) / max(speed, 1e-6))
                    if delta > 0:
                        time.sleep(delta)
                last_ts = current_ts

            if not payload:
                continue

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
        "--baudrate",
        type=int,
        default=9600,
        help="Baudrate to open the serial port with (default: 9600)",
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
        baudrate=args.baudrate,
        speed=args.speed,
        no_sleep=args.no_sleep,
    )


if __name__ == "__main__":
    main()

# Make a virtual serial port pair: socat -d -d pty,raw,echo=0 pty,raw,echo=0
