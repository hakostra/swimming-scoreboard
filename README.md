# Swimming Scoreboard Server

This is a simple FastAPI-based web server that presents a swimming event scoreboard and a control panel.

## Features

- Scoreboard page showing race name, heat, and a table with lane, rank, name, dist (m), split (last lap), and total time.
- Control panel for updating race name, heat, and basic appearance (background and font color).
- Live updates pushed to all connected scoreboard clients over WebSockets.

## Requirements

- Python 3.9+

## Installation

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

## Running the server

Linux: The user running the server needs permissions to open /dev/ttyUSB0
(or whatever serial port is used). This is typically done by adding the user to
the `dialout` group:

```bash
sudo usermod -a -G dialout $USER
```

Then start the server:

```bash
uvicorn main:app --reload
```

Then open the following in a browser:

- Scoreboard: http://localhost:8000/scoreboard
- Control panel: http://localhost:8000/control

## HTTP and WebSocket endpoints

### Pages

- `GET /` – Redirects to `/scoreboard`.
- `GET /scoreboard` – Scoreboard view (read-only, connects to the WebSocket for live updates).
- `GET /control` – Control panel UI for operators.

### WebSocket

- `WS /ws/scoreboard`
  - Broadcasts the full scoreboard state whenever anything changes.
  - The scoreboard page connects here; you normally do not need to call this manually.

### Settings and pool configuration

- `POST /api/settings`
  - Body (JSON, all fields optional):
    - `background_color`: string, CSS color (e.g. `"#000033"`).
    - `font_color`: string, CSS color (e.g. `"#FFFFFF"`).
    - `font_scale`: integer 50–200, logical font scaling percentage.
  - Updates appearance for all connected scoreboards.

- `POST /api/pool`
  - Body (JSON, all fields optional):
    - `lane_count`: integer 1–10.
    - `first_lane`: integer 0–10.
    - `lap_meters`: float 10–200, pool length in meters (e.g. `25`, `50`, `16.33`).
  - Changes lane configuration (rebuilds the internal lane list when lane_count/first_lane change) and updates the pool length used when computing distances from laps.

- `POST /api/header`
  - Body (JSON, fields optional):
    - `race_name`: string.
    - `heat`: string.
  - Updates the race title shown on the scoreboard.

### Lanes

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
    - `finished`: boolean/0/1 flag indicating whether the swimmer has finished (optional, defaults to false).
  - For bulk updates, the payload is authoritative: on each call the server rebuilds all configured lanes from scratch. Lanes not mentioned in the payload remain visible but empty, and any field not supplied for a lane is cleared. If `lap` is provided, the server computes `dist` (distance column on the scoreboard) as `lap * lap_meters`.

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
  - Patches a single lane; fields you omit are left unchanged. If `lap` is provided, `dist` is recomputed from `lap` and `lap_meters`.

### Timer control

- `POST /api/timer/start`
  - Starts the race timer and resets elapsed time.

- `POST /api/timer/stop`
  - Stops the race timer and freezes the elapsed time.

- `POST /api/timer/reset`
  - Resets the race timer to 00:00.0 without starting it.

### Sorting

- `POST /api/sort_by_lane`
  - Sets sort mode so the scoreboard lists swimmers by lane number.

- `POST /api/sort_by_rank`
  - Sets sort mode so the scoreboard lists swimmers by rank (with tie-break by lane).

All of these endpoints trigger a WebSocket broadcast so connected scoreboard clients update automatically.
