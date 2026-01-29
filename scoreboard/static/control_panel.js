async function postJson(url, data) {
    const resp = await fetch(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify(data),
    });
    if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
    }
    return resp.json();
}

function setStatus(el, ok, message) {
    if (!el) return;
    el.textContent = message;
    el.classList.remove("ok", "error");
    el.classList.add(ok ? "ok" : "error");
    if (message) {
        setTimeout(() => {
            el.textContent = "";
        }, 2500);
    }
}

window.addEventListener("DOMContentLoaded", () => {
    const headerForm = document.getElementById("header-form");
    const headerStatus = document.getElementById("header-status");
    const eventTextInput = document.getElementById("event-text");

    const sortLaneBtn = document.getElementById("sort-lane");
    const sortRankBtn = document.getElementById("sort-rank");
    const sortStatus = document.getElementById("sort-status");

    const settingsForm = document.getElementById("settings-form");
    const settingsStatus = document.getElementById("settings-status");

    const fontScaleInput = document.getElementById("font-scale");
    const fontScaleValue = document.getElementById("font-scale-value");

    const poolUpdateBtn = document.getElementById("pool-update");
    const poolStatus = document.getElementById("pool-status");
    const laneCountInput = document.getElementById("lane-count");
    const firstLaneInput = document.getElementById("first-lane");
    const poolMetersInput = document.getElementById("pool-meters");

    const timingForm = document.getElementById("timing-form");
    const timingStatus = document.getElementById("timing-status");

    if (headerForm) {
        headerForm.addEventListener("submit", async (ev) => {
            ev.preventDefault();
            const raceTitle = document.getElementById("race-title").value;
            const heat = document.getElementById("heat").value;
            const eventText = eventTextInput ? eventTextInput.value : "";
            try {
                await postJson("/api/header", { race_title: raceTitle, heat, event_text: eventText });
                setStatus(headerStatus, true, "Header updated");
            } catch (e) {
                console.error(e);
                setStatus(headerStatus, false, "Failed to update header");
            }
        });
    }

    if (sortLaneBtn) {
        sortLaneBtn.addEventListener("click", async () => {
            try {
                await postJson("/api/sort_by_lane", {});
                setStatus(sortStatus, true, "Sorted by lane");
            } catch (e) {
                console.error(e);
                setStatus(sortStatus, false, "Failed to sort by lane");
            }
        });
    }

    if (sortRankBtn) {
        sortRankBtn.addEventListener("click", async () => {
            try {
                await postJson("/api/sort_by_rank", {});
                setStatus(sortStatus, true, "Sorted by rank");
            } catch (e) {
                console.error(e);
                setStatus(sortStatus, false, "Failed to sort by rank");
            }
        });
    }

    if (settingsForm) {
        settingsForm.addEventListener("submit", async (ev) => {
            ev.preventDefault();
            const backgroundColor = document.getElementById("background-color").value;
            const fontColor = document.getElementById("font-color").value;

            try {
                await postJson("/api/settings", {
                    background_color: backgroundColor,
                    font_color: fontColor,
                    font_scale: fontScaleInput ? parseInt(fontScaleInput.value, 10) : undefined,
                });
                setStatus(settingsStatus, true, "Colors updated");
            } catch (e) {
                console.error(e);
                setStatus(settingsStatus, false, "Failed to update colors");
            }
        });
    }

    if (fontScaleInput && fontScaleValue) {
        fontScaleInput.addEventListener("input", () => {
            const v = parseInt(fontScaleInput.value, 10);
            if (!Number.isNaN(v)) {
                fontScaleValue.textContent = `${v}%`;
            }
        });
    }

    if (poolUpdateBtn) {
        poolUpdateBtn.addEventListener("click", async () => {
            const laneCountVal = laneCountInput ? parseInt(laneCountInput.value, 10) : NaN;
            const firstLaneVal = firstLaneInput ? parseInt(firstLaneInput.value, 10) : NaN;
            const poolMetersVal = poolMetersInput ? parseFloat(poolMetersInput.value) : NaN;

            const body = {};
            if (!Number.isNaN(laneCountVal)) {
                body.lane_count = laneCountVal;
            }
            if (!Number.isNaN(firstLaneVal)) {
                body.first_lane = firstLaneVal;
            }
            if (!Number.isNaN(poolMetersVal)) {
                body.lap_meters = poolMetersVal;
            }

            try {
                await postJson("/api/pool", body);
                setStatus(poolStatus, true, "Pool updated");
            } catch (e) {
                console.error(e);
                setStatus(poolStatus, false, "Failed to update pool");
            }
        });
    }

    if (timingForm) {
        timingForm.addEventListener("submit", async (ev) => {
            ev.preventDefault();
            const lstPath = document.getElementById("lst-path").value;
            const comPort = document.getElementById("com-port").value;
            const comSettings = document.getElementById("com-settings").value;
            const debugPathInput = document.getElementById("debug-path");
            const debugPath = debugPathInput ? debugPathInput.value : "";
            const holdResultsInput = document.getElementById("hold-results-time");
            const holdResultsRaw = holdResultsInput ? holdResultsInput.value : "";
            const holdResultsTime = holdResultsRaw === "" ? undefined : parseFloat(holdResultsRaw);

            try {
                await postJson("/api/timing_config", {
                    lst_path: lstPath,
                    com_port: comPort,
                    com_settings: comSettings,
                    debug_path: debugPath,
                    hold_results_time: Number.isNaN(holdResultsTime) ? undefined : holdResultsTime,
                });
                setStatus(timingStatus, true, "Timing settings saved");
            } catch (e) {
                console.error(e);
                setStatus(timingStatus, false, "Failed to save timing settings");
            }
        });
    }
});
