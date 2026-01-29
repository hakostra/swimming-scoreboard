# HTTP and WebSocket API

This document describes the HTTP and WebSocket endpoints exposed by the
Swimming Scoreboard server.

## Pages

- `GET /` – Redirects to `/scoreboard`.
- `GET /scoreboard` – Scoreboard view (read-only, connects to the WebSocket
  for live updates).
- `GET /control` – Control panel UI for operators.

## WebSocket

- `WS /ws/scoreboard`
  - Broadcasts the full scoreboard state whenever anything changes.
  - The scoreboard page connects here; you normally do not need to call this
    manually.

## Settings and pool configuration

- `POST /api/settings`
  - Body (JSON, all fields optional):
    - `background_color`: string, CSS color (e.g. "#000033").
    - `font_color`: string, CSS color (e.g. "#FFFFFF").
    - `font_scale`: integer 50-200, logical font scaling percentage.
  - Updates appearance for all connected scoreboards.

- `POST /api/pool`
  - Body (JSON, all fields optional):
    - `lane_count`: integer 1-10.
    - `first_lane`: integer 0-10.
    - `lap_meters`: float, lap length in meters (e.g. 25.0 or 50.0).
      This is the distance between two touchpads. I.e. in a 25 meter pool with
      a touchpad in one end only, this length must be 50 meter.
  - Changes lane configuration (rebuilds the internal lane list when
    lane_count/first_lane change) and updates the pool length used when
    computing distances from laps.

## Header

- `POST /api/header`
  - Body (JSON, fields optional):
    - `race_title`: string.
    - `heat`: string.
    - `event_text`: string.
  - Updates the header shown on the scoreboard.

## Lanes

- `POST /api/lanes`
  - Body (JSON):
    - `lanes`: array of lane objects.
  - Each lane object can contain:
    - `lane` (required): integer lane number.
    - `rank`: string or number.
    - `name`: swimmer name.
    - `time`: total time string.
    - `split`: last-lap time string.
    - `lap`: float number of laps completed.
    - `finished`: boolean/0/1 flag indicating whether the swimmer has finished
      (optional, defaults to false).
  - For bulk updates, the payload is authoritative: on each call the server
    rebuilds all configured lanes from scratch. Lanes not mentioned in the
    payload remain visible but empty, and any field not supplied for a lane is
    cleared. If `lap` is provided, the server computes `dist` (distance
    column on the scoreboard) as `lap * lap_meters`.

Example:

```bash
curl -X POST http://localhost:8000/api/lanes \
  -H "Content-Type: application/json" \
  -d '{
    "lanes": [
      {"lane": 1, "rank": 2, "name": "Swimmer One", "time": "00:54.32", "split": "00:27.10", "lap": 4},
      {"lane": 2, "rank": 1, "name": "Swimmer Two", "time": "00:53.01", "split": "00:26.50", "lap": 4}
    ]
  }'
```

- `POST /api/lane`
  - Body (JSON):
    - `lane` (required): integer lane number.
    - Optional: `rank`, `name`, `time`, `split`, `lap`, `finished`.
  - Patches a single lane; fields you omit are left unchanged. If `lap` is
    provided, `dist` is recomputed from `lap` and `lap_meters`.

## Timer control

- `POST /api/timer/start`
  - Starts the race timer and resets elapsed time.

- `POST /api/timer/stop`
  - Stops the race timer and freezes the elapsed time.

- `POST /api/timer/reset`
  - Resets the race timer to 00:00.0 without starting it.

## Time sync

- `GET /api/time`
  - Returns the current server time in milliseconds. Used by the
    scoreboard client to synchronize its local clock.

## Sorting

- `POST /api/sort_by_lane`
  - Sets sort mode so the scoreboard lists swimmers by lane number.

- `POST /api/sort_by_rank`
  - Sets sort mode so the scoreboard lists swimmers by rank (with tie-break
    by lane).

## Heat lifecycle

- `POST /api/prepare_heat`
  - One-call setup used by the timing comms process.
  - Body (JSON):
    - `race_title`: string (event title).
    - `heat`: string (heat label, e.g. "Heat 2").
    - `event_text`: string (optional sponsor/extra text).
    - `lanes`: array of lane objects (same shape as `/api/lanes`).
  - Performs: sort by lane, timer reset, header update, lanes update.

- `POST /api/finish_heat`
  - One-call finish used by the timing comms process.
  - Clears lanes where `finished` is false, resets the timer, and sorts
    by rank.

## Timing configuration (used by comms)

- `GET /api/timing_config`
  - Returns the current timing integration configuration.

- `POST /api/timing_config`
  - Body (JSON, all fields optional):
    - `lst_path`: string path to timing LST files.
    - `com_port`: string serial port.
    - `com_settings`: string like "9600,7,n,1".
    - `debug_capture_enabled`: boolean-like value.
    - `debug_path`: string file path for serial debug log.
    - `hold_results_time`: float seconds.
  - Persists settings and restarts the comms helper process.

Endpoints that modify the scoreboard state trigger a WebSocket broadcast so
connected scoreboard clients update automatically. Timing configuration
endpoints do not broadcast.
