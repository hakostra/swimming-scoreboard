from typing import List, Dict, Any
import json
import os
import multiprocessing
import sys
from pathlib import Path
import time

import comms

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Swimming Scoreboard Server")

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except WebSocketDisconnect:
                self.disconnect(connection)
            except Exception:
                # Ignore send errors for individual connections
                self.disconnect(connection)


manager = ConnectionManager()


# In-memory scoreboard state
scoreboard_state: Dict[str, Any] = {
    "race_title": "Swimming Scoreboard",
    "heat": "Heat 1",
    "event_text": "",
    "sort_mode": "lane",
    "settings": {
        "background_color": "#000033",
        "font_color": "#FFFFFF",
        "font_scale": 100,
    },
    "timer": {
        "running": False,
        "start_timestamp": None,
        "elapsed_ms": 0,
    },
    "lanes": [
        {"lane": 1, "rank": "", "name": "", "time": "", "split": "", "dist": "", "finished": False},
        {"lane": 2, "rank": "", "name": "", "time": "", "split": "", "dist": "", "finished": False},
        {"lane": 3, "rank": "", "name": "", "time": "", "split": "", "dist": "", "finished": False},
        {"lane": 4, "rank": "", "name": "", "time": "", "split": "", "dist": "", "finished": False},
        {"lane": 5, "rank": "", "name": "", "time": "", "split": "", "dist": "", "finished": False},
        {"lane": 6, "rank": "", "name": "", "time": "", "split": "", "dist": "", "finished": False},
        {"lane": 7, "rank": "", "name": "", "time": "", "split": "", "dist": "", "finished": False},
        {"lane": 8, "rank": "", "name": "", "time": "", "split": "", "dist": "", "finished": False},
    ],
}


# Timing system configuration (does not affect the scoreboard directly)
timing_config: Dict[str, Any] = {
    "lst_path": "",
    "com_port": "",
    "com_settings": "9600,7,n,1",
    "debug_path": "",
    "hold_results_time": 0.0,
}


# Pool configuration (does not affect the scoreboard directly)
pool_config: Dict[str, Any] = {
    "lane_count": 8,
    "first_lane": 1,
    "lap_meters": 50.0,
}


# External comms helper process handle
comms_process: multiprocessing.Process | None = None


def _get_config_dir() -> Path:
    """Return platform-appropriate configuration directory for this app.

    Linux:  $XDG_CONFIG_HOME/swimming-scoreboard
            or ~/.config/swimming-scoreboard
    Windows: %APPDATA%/swimming-scoreboard
             or ~/AppData/Roaming/swimming-scoreboard
    Other: treat like Linux and use ~/.config/swimming-scoreboard.
    """

    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        if base:
            base_path = Path(base)
        else:
            base_path = Path.home() / "AppData" / "Roaming"
        return base_path / "swimming-scoreboard"

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        base_path = Path(xdg)
    else:
        base_path = Path.home() / ".config"
    return base_path / "swimming-scoreboard"


_CONFIG_FILE = _get_config_dir() / "config.json"


def _load_persistent_config() -> None:
    """Load timing and pool configuration from disk if available.

    Values from disk override the in-code defaults on startup.
    """

    try:
        if not _CONFIG_FILE.is_file():
            return
        with _CONFIG_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            timing_data = data.get("timing_config")
            if isinstance(timing_data, dict):
                timing_config.update(timing_data)

            pool_data = data.get("pool_config")
            if isinstance(pool_data, dict):
                pool_config.update(pool_data)

            settings_data = data.get("scoreboard_settings")
            if isinstance(settings_data, dict):
                settings = scoreboard_state.get("settings", {})
                settings.update(settings_data)
                scoreboard_state["settings"] = settings
    except Exception:
        # Ignore config load errors; fall back to defaults.
        pass


def _save_persistent_config() -> None:
    """Persist timing and pool configuration to disk.

    This is called after any change to timing_config or pool_config.
    """

    try:
        cfg_dir = _CONFIG_FILE.parent
        cfg_dir.mkdir(parents=True, exist_ok=True)

        content = {
            "timing_config": timing_config,
            "pool_config": pool_config,
            "scoreboard_settings": scoreboard_state.get("settings", {}),
        }

        tmp_file = _CONFIG_FILE.with_suffix(_CONFIG_FILE.suffix + ".tmp")
        with tmp_file.open("w", encoding="utf-8") as f:
            json.dump(content, f, indent=4)
        tmp_file.replace(_CONFIG_FILE)
    except Exception:
        # Ignore persistence errors; they should not break the server.
        pass


_load_persistent_config()


def _compute_dist_from_laps(laps_raw: Any, settings: Dict[str, Any]) -> str:
    """Compute distance string from a lap count and pool length.

    Returns an empty string if laps or pool length are invalid or non-positive.
    """

    try:
        laps = float(laps_raw)
    except (TypeError, ValueError):
        return ""

    if laps <= 0:
        return ""

    pool_len = settings.get("lap_meters", 50.0)
    try:
        pool_len = float(pool_len)
    except (TypeError, ValueError):
        pool_len = 50.0
    if pool_len <= 0:
        return ""

    dist_val = float(laps) * pool_len
    if dist_val <= 0:
        return ""

    # Nicely formatted: integer if whole meters, otherwise compact decimal.
    if float(dist_val).is_integer():
        return str(int(round(dist_val))) + " m"
    return f"{dist_val:.1f}".rstrip("0").rstrip(".") + " m"


def _coerce_finished(value: Any) -> bool:
    """Normalize various payload representations into a boolean finished flag."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return False


def _coerce_bool(value: Any) -> bool:
    """Return True for truthy user inputs like "yes", 1, or True."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _rebuild_lanes(first_lane: int, lane_count: int) -> None:
    """Rebuild lane list according to lane configuration
    preserving existing data by lane number."""

    existing = {lane.get("lane"): lane for lane in scoreboard_state.get("lanes", [])}
    new_lanes: List[Dict[str, Any]] = []
    for idx in range(max(1, lane_count)):
        lane_no = first_lane + idx
        prev = existing.get(lane_no, {})
        new_lanes.append(
            {
                "lane": lane_no,
                "rank": prev.get("rank", ""),
                "name": prev.get("name", ""),
                "time": prev.get("time", ""),
                "split": prev.get("split", ""),
                "dist": prev.get("dist", ""),
                "finished": bool(prev.get("finished", False)),
            }
        )

    scoreboard_state["lanes"] = new_lanes


def _start_comms_process() -> None:
    """Start the external comms helper process using multiprocessing.

    Failures here must never prevent the web server from starting, so any
    error during spawn is swallowed after logging.
    """

    global comms_process

    # If a previous process is still running, do nothing.
    try:
        if comms_process is not None and comms_process.is_alive():
            return
    except Exception:
        # If is_alive() fails for any reason, we'll try to start a fresh
        # process.
        pass

    try:
        proc = multiprocessing.Process(target=comms.main,
                                       name="swimming-comms",
                                       daemon=True)
        proc.start()
        comms_process = proc
    except Exception:
        # Do not raise: the scoreboard must still be usable without comms.
        print("Warning: failed to start comms subprocess", file=sys.stderr)
        comms_process = None


def _stop_comms_process() -> None:
    """Internal helper to terminate the comms process if it is running."""

    global comms_process
    proc = comms_process
    comms_process = None

    if proc is None:
        return

    try:
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2.0)
    except Exception:
        # Best-effort only; ignore termination errors.
        return


@app.on_event("startup")
async def _apply_persisted_pool_config() -> None:
    """Ensure lanes reflect any persisted pool configuration on startup."""

    try:
        first_lane = int(pool_config.get("first_lane", 1) or 1)
        lane_count = int(pool_config.get("lane_count", 8) or 8)
    except Exception:
        first_lane = 1
        lane_count = 8

    _rebuild_lanes(first_lane=first_lane, lane_count=lane_count)

    # Spawn the separate comms process once the configuration is applied.
    _start_comms_process()


@app.on_event("shutdown")
async def _stop_comms_subprocess() -> None:
    """Terminate the comms helper process on server shutdown, if running."""

    _stop_comms_process()


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/scoreboard")


@app.get("/scoreboard")
async def scoreboard(request: Request):
    return templates.TemplateResponse(
        "scoreboard.html",
        {
            "request": request,
            "scoreboard": scoreboard_state,
        },
    )


@app.get("/control")
async def control_panel(request: Request):
    return templates.TemplateResponse(
        "control_panel.html",
        {
            "request": request,
            "scoreboard": scoreboard_state,
            "pool": pool_config,
            "timing": timing_config,
        },
    )


@app.websocket("/ws/scoreboard")
async def scoreboard_ws(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send the current state immediately after connect
        await manager.broadcast(scoreboard_state)
        while True:
            # The scoreboard client does not need to send anything;
            # just keep the connection open. We still read to detect
            # disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


@app.post("/api/settings")
async def update_settings(payload: Dict[str, Any]):
    settings = scoreboard_state.get("settings", {})
    background = payload.get("background_color")
    font = payload.get("font_color")
    changed = False
    if background is not None:
        settings["background_color"] = str(background)
        changed = True
    if font is not None:
        settings["font_color"] = str(font)
        changed = True
    font_scale_raw = payload.get("font_scale")
    if font_scale_raw is not None:
        try:
            value = int(font_scale_raw)
            # Clamp to a reasonable range (50% - 150%)
            if value < 50:
                value = 50
            if value > 150:
                value = 150
            settings["font_scale"] = value
            changed = True
        except (TypeError, ValueError):
            pass
    scoreboard_state["settings"] = settings
    if changed:
        _save_persistent_config()
    await manager.broadcast(scoreboard_state)
    return {"status": "ok", "settings": settings}


@app.post("/api/pool")
async def update_pool(payload: Dict[str, Any]):
    """Update lane configuration (lane count and first lane) and
    rebuild lanes."""

    lane_count_raw = payload.get("lane_count")
    first_lane_raw = payload.get("first_lane")
    lap_meters_raw = payload.get("lap_meters")

    lane_count = pool_config.get("lane_count", 8)
    first_lane = pool_config.get("first_lane", 1)

    changed_lanes = False
    changed_settings = False

    if lane_count_raw is not None:
        try:
            value = int(lane_count_raw)
            if 1 <= value <= 10:
                lane_count = value
                pool_config["lane_count"] = value
                changed_lanes = True
                changed_settings = True
        except (TypeError, ValueError):
            pass

    if first_lane_raw is not None:
        try:
            value = int(first_lane_raw)
            if 0 <= value <= 10:
                first_lane = value
                pool_config["first_lane"] = value
                changed_lanes = True
                changed_settings = True
        except (TypeError, ValueError):
            pass

    if lap_meters_raw is not None:
        try:
            value = float(lap_meters_raw)
            if 10 <= value <= 200:
                pool_config["lap_meters"] = value
                changed_settings = True
        except (TypeError, ValueError):
            pass

    if changed_lanes:
        _rebuild_lanes(first_lane=first_lane, lane_count=lane_count)

    if changed_settings or changed_lanes:
        _save_persistent_config()
        await manager.broadcast(scoreboard_state)

    return {
        "status": "ok",
        "lane_count": pool_config.get("lane_count"),
        "first_lane": pool_config.get("first_lane"),
        "lap_meters": pool_config.get("lap_meters"),
    }


@app.post("/api/header")
async def update_header(payload: Dict[str, Any]):
    race_title = payload.get("race_title")
    heat = payload.get("heat")
    event_text = payload.get("event_text")
    if race_title is not None:
        scoreboard_state["race_title"] = str(race_title)
    if heat is not None:
        scoreboard_state["heat"] = str(heat)
    if event_text is not None:
        scoreboard_state["event_text"] = str(event_text)
    await manager.broadcast(scoreboard_state)
    return {
        "status": "ok",
        "race_title": scoreboard_state["race_title"],
        "heat": scoreboard_state["heat"],
        "event_text": scoreboard_state["event_text"],
    }


@app.post("/api/lanes")
async def update_lanes(payload: Dict[str, Any]):
    lanes_payload = payload.get("lanes")
    if isinstance(lanes_payload, list):
        settings = scoreboard_state.get("settings", {})
        lane_count = int(settings.get("lane_count", 8) or 8)
        first_lane = int(settings.get("first_lane", 1) or 1)

        # Start from a fresh set of lanes for the configured pool,
        # so that only the current payload defines what is shown.
        lanes_by_no: Dict[int, Dict[str, Any]] = {}
        for idx in range(max(1, lane_count)):
            lane_no = first_lane + idx
            lanes_by_no[lane_no] = {
                "lane": lane_no,
                "rank": "",
                "name": "",
                "time": "",
                "split": "",
                "dist": "",
                "finished": False,
            }

        for lane in lanes_payload:
            try:
                lane_no = int(lane.get("lane"))
            except Exception:
                continue

            current = lanes_by_no.get(
                lane_no,
                {"lane": lane_no, "rank": "", "name": "",
                 "time": "", "split": "", "dist": "", "finished": False},
            )
            current.setdefault("finished", False)

            # For bulk updates we treat the payload as authoritative:
            # any field not present becomes empty.
            current["rank"] = lane.get("rank", "")
            current["name"] = lane.get("name", "")
            current["time"] = lane.get("time", "")
            current["split"] = lane.get("split", "")
            if "finished" in lane:
                current["finished"] = _coerce_finished(lane.get("finished"))
            else:
                current["finished"] = False

            if "lap" in lane:
                laps_raw = lane.get("lap")
                current["dist"] = _compute_dist_from_laps(laps_raw, settings)
            else:
                # No lap information supplied: distance is cleared.
                current["dist"] = ""

            lanes_by_no[lane_no] = current

        # Keep all lanes, sorted by lane number; lanes not in the
        # payload remain as empty rows.
        updated_lanes = list(lanes_by_no.values())
        updated_lanes.sort(key=lambda x: x["lane"])
        scoreboard_state["lanes"] = updated_lanes
        await manager.broadcast(scoreboard_state)
    return {"status": "ok", "lanes": scoreboard_state["lanes"]}


@app.post("/api/lane")
async def update_single_lane(payload: Dict[str, Any]):
    """Patch a single lane's data, keeping all other lanes unchanged.

    Payload must contain `lane` and may optionally contain any of
    `rank`, `name`, `time`, `split`, or `lap`.
    """

    lane_raw = payload.get("lane")
    try:
        lane_no = int(lane_raw)
    except Exception:
        return {"status": "error", "message": "Invalid or missing lane"}

    lanes = scoreboard_state.get("lanes", [])
    settings = scoreboard_state.get("settings", {})
    target = None
    for lane in lanes:
        if lane.get("lane") == lane_no:
            target = lane
            break

    if target is None:
        target = {"lane": lane_no, "rank": "", "name": "",
                  "time": "", "split": "", "dist": "", "finished": False}
        lanes.append(target)
    else:
        target.setdefault("finished", False)

    for field in ("rank", "name", "time", "split"):
        if field in payload:
            target[field] = payload.get(field, "")

    if "lap" in payload:
        laps_raw = payload.get("lap")
        target["dist"] = _compute_dist_from_laps(laps_raw, settings)

    if "finished" in payload:
        target["finished"] = _coerce_finished(payload.get("finished"))

    lanes.sort(key=lambda x: x.get("lane", 0))
    scoreboard_state["lanes"] = lanes
    await manager.broadcast(scoreboard_state)
    return {"status": "ok", "lane": target}


@app.post("/api/timer/start")
async def start_timer():
    now_ms = int(time.time() * 1000)
    timer = scoreboard_state.setdefault("timer", {})
    timer["running"] = True
    timer["start_timestamp"] = now_ms
    timer["elapsed_ms"] = 0
    await manager.broadcast(scoreboard_state)
    return {"status": "ok", "timer": timer}


@app.post("/api/timer/stop")
async def stop_timer():
    now_ms = int(time.time() * 1000)
    timer = scoreboard_state.setdefault("timer", {})
    start_ts = timer.get("start_timestamp")
    if timer.get("running") and isinstance(start_ts, int):
        elapsed = max(0, now_ms - start_ts)
        timer["elapsed_ms"] = elapsed
    timer["running"] = False
    await manager.broadcast(scoreboard_state)
    return {"status": "ok", "timer": timer}


@app.post("/api/timer/reset")
async def reset_timer():
    """Reset the race timer to 0 without starting it."""

    timer = scoreboard_state.setdefault("timer", {})
    timer["running"] = False
    timer["elapsed_ms"] = 0
    timer["start_timestamp"] = None
    await manager.broadcast(scoreboard_state)
    return {"status": "ok", "timer": timer}


@app.post("/api/sort_by_lane")
async def sort_by_lane():
    """Set scoreboard sort mode to lane and broadcast state
    (client applies sort)."""

    scoreboard_state["sort_mode"] = "lane"
    await manager.broadcast(scoreboard_state)
    return {"status": "ok", "sort_mode": "lane"}


@app.post("/api/sort_by_rank")
async def sort_by_rank():
    """Set scoreboard sort mode to rank and broadcast state
    (client applies sort)."""

    scoreboard_state["sort_mode"] = "rank"
    await manager.broadcast(scoreboard_state)
    return {"status": "ok", "sort_mode": "rank"}


@app.get("/api/timing_config")
async def get_timing_config():
    """Return the current timing system configuration as JSON."""

    return {"status": "ok", "timing_config": timing_config}


@app.post("/api/timing_config")
async def update_timing_config(payload: Dict[str, Any]):
    """Update timing system configuration.

    These settings are not part of the public scoreboard state and
    are intended for use by an external timing integration process.
    """

    lst_path = payload.get("lst_path")
    com_port = payload.get("com_port")
    com_settings = payload.get("com_settings")
    debug_enabled_raw = payload.get("debug_capture_enabled")
    debug_path = payload.get("debug_path")
    hold_results_raw = payload.get("hold_results_time")

    changed = False

    if lst_path is not None:
        timing_config["lst_path"] = str(lst_path)
        changed = True
    if com_port is not None:
        timing_config["com_port"] = str(com_port)
        changed = True
    if com_settings is not None:
        timing_config["com_settings"] = str(com_settings)
        changed = True
    if debug_enabled_raw is not None:
        timing_config["debug_capture_enabled"] = _coerce_bool(debug_enabled_raw)
        changed = True
    if debug_path is not None:
        timing_config["debug_path"] = str(debug_path)
        changed = True
    if hold_results_raw is not None:
        print(f"Received hold_results_time: {hold_results_raw}")
        try:
            timing_config["hold_results_time"] = float(hold_results_raw)
            changed = True
        except (TypeError, ValueError):
            pass

    if changed:
        _save_persistent_config()

        # Timing configuration changed: restart the comms helper process so it
        # picks up the new settings (e.g. LST path, serial port).
        _stop_comms_process()
        _start_comms_process()

    # No scoreboard broadcast here; these settings only affect
    # external timing-system integration.
    return {"status": "ok", "timing_config": timing_config}


# Convenience: run with `uvicorn main:app --reload`
