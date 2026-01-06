(function () {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const wsUrl = `${protocol}://${window.location.host}/ws/scoreboard`;

    const body = document.getElementById("scoreboard-body");
    const raceTitle = document.getElementById("race-title");
    const heatDescription = document.getElementById("heat-description");
    const lanesBody = document.getElementById("lanes-body");
    const timerDisplay = document.getElementById("heat-timer");
    const scoreboardContainer = document.querySelector(".scoreboard-container");
    const dayClockDisplay = document.getElementById("day-clock");
    const eventTextRow = document.getElementById("event-text-row");
    const eventTextDisplay = document.getElementById("event-text-display");

    let timerState = {
        running: false,
        start_timestamp: null,
        elapsed_ms: 0,
    };

    function setConnectionLost(isLost) {
        if (timerDisplay) {
            timerDisplay.style.display = isLost ? "none" : "";
        }
        if (dayClockDisplay) {
            dayClockDisplay.style.display = isLost ? "none" : "";
        }
        if (heatDescription) {
            if (isLost) {
                heatDescription.textContent = "Connection to server lost";
            }
        }
    }

    function formatTime(ms) {
        const totalMs = Math.max(0, ms | 0);
        const totalSeconds = Math.floor(totalMs / 1000);
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        const tenths = Math.floor((totalMs % 1000) / 100);
        const mm = String(minutes).padStart(2, "0");
        const ss = String(seconds).padStart(2, "0");
        return `${mm}:${ss}.${tenths}`;
    }

    function updateTimerDisplay() {
        if (!timerDisplay) return;
        const running = !!timerState.running;
        const startTs = timerState.start_timestamp;
        const baseElapsed = timerState.elapsed_ms || 0;

        let displayMs = baseElapsed;
        if (running && typeof startTs === "number") {
            const now = Date.now();
            displayMs = Math.max(0, now - startTs);
        }

        timerDisplay.textContent = formatTime(displayMs);
    }

    function updateDayClock() {
        if (!dayClockDisplay) return;
        const now = new Date();
        const hh = String(now.getHours()).padStart(2, "0");
        const mm = String(now.getMinutes()).padStart(2, "0");
        const ss = String(now.getSeconds()).padStart(2, "0");
        dayClockDisplay.textContent = `${hh}:${mm}:${ss}`;
    }

    function applyState(state) {
        try {
            const settings = state.settings || {};
            if (settings.background_color) {
                body.style.backgroundColor = settings.background_color;
            }
            if (settings.font_color) {
                body.style.color = settings.font_color;
            }
            if (typeof settings.font_scale === "number") {
                const v = settings.font_scale;
                document.documentElement.style.fontSize = `${v}%`;
                if (scoreboardContainer) {
                    scoreboardContainer.style.fontSize = `${v}%`;
                }
            }
        } catch (e) {
            console.error("Failed to apply settings", e);
        }

        if (raceTitle) {
            raceTitle.textContent = `${state.race_title || ""}`;
        }

        if (heatDescription) {
            heatDescription.textContent = `${state.heat || ""}`;
        }

        if (eventTextDisplay) {
            const text = `${state.event_text || ""}`;
            const hasText = text.trim().length > 0;
            eventTextDisplay.textContent = text;
            if (eventTextRow) {
                eventTextRow.style.display = hasText ? "flex" : "none";
            }
        }

        if (state.timer) {
            timerState = {
                ...timerState,
                ...state.timer,
            };
            updateTimerDisplay();
        }

        if (lanesBody && Array.isArray(state.lanes)) {
            lanesBody.innerHTML = "";

            const sortMode = state.sort_mode || "lane";

            const lanes = state.lanes.slice();
            if (sortMode === "rank") {
                lanes.sort((a, b) => {
                    const ra = a.rank === undefined || a.rank === null || a.rank === "" ? null : Number(a.rank);
                    const rb = b.rank === undefined || b.rank === null || b.rank === "" ? null : Number(b.rank);

                    if (ra === null && rb === null) return (a.lane ?? 0) - (b.lane ?? 0);
                    if (ra === null) return 1;
                    if (rb === null) return -1;
                    if (Number.isNaN(ra) && Number.isNaN(rb)) return (a.lane ?? 0) - (b.lane ?? 0);
                    if (Number.isNaN(ra)) return 1;
                    if (Number.isNaN(rb)) return -1;
                    if (ra !== rb) return ra - rb;
                    return (a.lane ?? 0) - (b.lane ?? 0);
                });
            } else {
                lanes.sort((a, b) => (a.lane ?? 0) - (b.lane ?? 0));
            }

            lanes.forEach((lane) => {
                const tr = document.createElement("tr");
                const finished = lane.finished === true || lane.finished === 1;

                const laneTd = document.createElement("td");
                laneTd.className = "lane";
                laneTd.textContent = lane.lane ?? "";

                const nameTd = document.createElement("td");
                nameTd.className = "name";
                nameTd.textContent = lane.name ?? "";

                const distTd = document.createElement("td");
                distTd.className = "dist";
                distTd.textContent =
                    lane.dist !== undefined && lane.dist !== null && lane.dist !== ""
                        ? String(lane.dist)
                        : "";

                const splitTd = document.createElement("td");
                splitTd.className = "split";
                splitTd.textContent = lane.split ?? "";

                const rankTd = document.createElement("td");
                rankTd.className = "rank";
                rankTd.textContent = lane.rank ?? "";

                const timeTd = document.createElement("td");
                timeTd.className = "time";
                timeTd.textContent = lane.time ?? "";

                if (finished) {
                    rankTd.classList.add("finished-metric");
                    timeTd.classList.add("finished-metric");
                }

                tr.appendChild(laneTd);
                tr.appendChild(nameTd);
                tr.appendChild(splitTd);
                tr.appendChild(distTd);
                tr.appendChild(rankTd);
                tr.appendChild(timeTd);

                lanesBody.appendChild(tr);
            });
        }
    }

    // Apply initial state rendered from the server
    if (window.INITIAL_SCOREBOARD_STATE) {
        applyState(window.INITIAL_SCOREBOARD_STATE);
    }

    // Keep the timer display updated locally between server broadcasts
    updateTimerDisplay();
    updateDayClock();
    setInterval(updateTimerDisplay, 100);
    setInterval(updateDayClock, 1000);

    let socket = null;
    let reconnectTimer = null;
    const reconnectDelayMs = 1000;

    function scheduleReconnect() {
        if (reconnectTimer) return;
        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            connect();
        }, reconnectDelayMs);
    }

    function connect() {
        if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
            return;
        }

        socket = new WebSocket(wsUrl);

        socket.addEventListener("open", () => {
            if (reconnectTimer) {
                clearTimeout(reconnectTimer);
                reconnectTimer = null;
            }
            setConnectionLost(false);
            // Optional: send a ping to keep the connection. The server ignores it.
            try {
                socket.send("hello");
            } catch (e) {
                console.warn("Unable to send initial ping", e);
            }
        });

        socket.addEventListener("message", (event) => {
            try {
                const data = JSON.parse(event.data);
                applyState(data);
            } catch (e) {
                console.error("Invalid scoreboard update", e);
            }
        });

        socket.addEventListener("close", () => {
            console.warn("Scoreboard connection closed");
            setConnectionLost(true);
            scheduleReconnect();
        });

        socket.addEventListener("error", (e) => {
            console.error("Scoreboard WebSocket error", e);
            try {
                socket.close();
            } catch (err) {
                // ignore
            }
        });
    }

    connect();
})();
