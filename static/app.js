let draggingWidget = null;
let currDragDropZone = null;
const WIDGET_SIZE_KEY = "dashboard_widget_sizes_v2";
const STATUS_COLLAPSE_KEY = "dashboard_status_collapsed";
const COMMAND_LOG_LIMIT = 200;
const commandLogEntries = [];
let stageSpeedSetTimer = null;
const stageStepState = {
  mode: "fine",
  label: "1",
};
const pollInFlight = {
  status: false,
  nanopositioner: false,
  thermal: false,
  vacuum: false,
  rotation: false,
};

async function runDedupedPoll(key, fn) {
  if (pollInFlight[key]) {
    return;
  }
  pollInFlight[key] = true;
  try {
    await fn();
  } finally {
    pollInFlight[key] = false;
  }
}

function startDashboardPolling() {
  if (window.__dashboardPollingInitialized) {
    return;
  }
  window.__dashboardPollingInitialized = true;

  const tasks = [
    { key: "status", fn: loadStatus, intervalMs: 3000 },
    { key: "nanopositioner", fn: loadNanopositionerStatus, intervalMs: 3000 },
    { key: "thermal", fn: loadThermalStatus, intervalMs: 2000 },
    { key: "vacuum", fn: loadVacuumStatus, intervalMs: 2000 },
    { key: "rotation", fn: loadRotationStatus, intervalMs: 2000 },
  ];

  tasks.forEach((task) => {
    runDedupedPoll(task.key, task.fn);
    setInterval(() => {
      if (document.hidden) {
        return;
      }
      runDedupedPoll(task.key, task.fn);
    }, task.intervalMs);
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      tasks.forEach((task) => runDedupedPoll(task.key, task.fn));
    }
  });
}

function timestampNow() {
  const d = new Date();
  return d.toLocaleTimeString();
}

function truncateForLog(value, maxLen = 300) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (text.length <= maxLen) {
    return text;
  }
  return `${text.slice(0, maxLen)} ...`;
}

function appendCommandLog(line) {
  commandLogEntries.push(`[${timestampNow()}] ${line}`);
  if (commandLogEntries.length > COMMAND_LOG_LIMIT) {
    commandLogEntries.shift();
  }
  const logPre = document.getElementById("command_log");
  if (logPre) {
    logPre.textContent = commandLogEntries.join("\n");
    logPre.scrollTop = logPre.scrollHeight;
  }
}

function clearCommandLog() {
  commandLogEntries.length = 0;
  const logPre = document.getElementById("command_log");
  if (logPre) {
    logPre.textContent = "No commands yet.";
  }
}

async function loggedFetch(path, options = {}, commandLabel = "") {
  const method = options.method || "GET";
  const label = commandLabel || `${method} ${path}`;
  appendCommandLog(`CMD ${label}`);
  try {
    const response = await fetch(path, options);
    appendCommandLog(`RES ${label} -> ${response.status}`);
    return response;
  } catch (error) {
    appendCommandLog(`ERR ${label} -> ${truncateForLog(error?.message || String(error))}`);
    throw error;
  }
}

function getCollapsedStatusMap() {
  try {
    const raw = localStorage.getItem(STATUS_COLLAPSE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (_err) {
    return {};
  }
}

function saveCollapsedStatusMap(map) {
  localStorage.setItem(STATUS_COLLAPSE_KEY, JSON.stringify(map));
}

function applyStatusCollapse(key, collapsed) {
  const statusBox = document.querySelector(`.status-box[data-status-key="${key}"]`);
  const toggleBtn = document.querySelector(`.status-toggle[data-status-key="${key}"]`);
  if (!statusBox || !toggleBtn) {
    return;
  }
  statusBox.classList.toggle("collapsed", collapsed);
  toggleBtn.textContent = collapsed ? "Expand" : "Collapse";
}

function initStatusToggles() {
  const collapsedState = getCollapsedStatusMap();
  const toggles = Array.from(document.querySelectorAll(".status-toggle"));

  toggles.forEach((btn) => {
    const key = btn.getAttribute("data-status-key");
    if (!key) {
      return;
    }

    applyStatusCollapse(key, Boolean(collapsedState[key]));

    btn.addEventListener("click", () => {
      const nextState = !Boolean(collapsedState[key]);
      collapsedState[key] = nextState;
      applyStatusCollapse(key, nextState);
      saveCollapsedStatusMap(collapsedState);
    });
  });
}

async function callApi(path) {
  const response = await loggedFetch(path, {}, path);
  await Promise.all([loadStatus(), loadNanopositionerStatus()]);
  return response;
}

function initDashboardDrag() {
  const dashboard = document.getElementById("dashboard");
  if (!dashboard) {
    return;
  }

  const widgets = Array.from(dashboard.querySelectorAll(".widget"));

  widgets.forEach((widget) => {
    widget.addEventListener("dragstart", (e) => {
      draggingWidget = widget;
      widget.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/html", widget.innerHTML);
    });

    widget.addEventListener("dragend", () => {
      widget.classList.remove("dragging");
      Array.from(dashboard.querySelectorAll(".drop-target")).forEach((w) => 
        w.classList.remove("drop-target")
      );
      Array.from(dashboard.querySelectorAll(".drop-left")).forEach((w) => 
        w.classList.remove("drop-left")
      );
      Array.from(dashboard.querySelectorAll(".drop-right")).forEach((w) => 
        w.classList.remove("drop-right")
      );
      draggingWidget = null;
      currDragDropZone = null;
    });

    widget.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      if (!draggingWidget || draggingWidget === widget) {
        return;
      }

      // Get widget position and dimensions
      const rect = widget.getBoundingClientRect();
      const dropX = event.clientX;
      const relativeX = dropX - rect.left;
      const thirdWidth = rect.width / 3;

      // Remove previous highlights
      Array.from(dashboard.querySelectorAll(".drop-target")).forEach((w) => 
        w.classList.remove("drop-target")
      );
      Array.from(dashboard.querySelectorAll(".drop-left")).forEach((w) => 
        w.classList.remove("drop-left")
      );
      Array.from(dashboard.querySelectorAll(".drop-right")).forEach((w) => 
        w.classList.remove("drop-right")
      );

      widget.classList.add("drop-target");

      // Determine drop zone (left, center, right)
      if (relativeX < thirdWidth) {
        widget.classList.add("drop-left");
        currDragDropZone = "left";
      } else if (relativeX > rect.width - thirdWidth) {
        widget.classList.add("drop-right");
        currDragDropZone = "right";
      } else {
        currDragDropZone = "center";
      }
    });

    widget.addEventListener("dragleave", (event) => {
      if (!widget.contains(event.relatedTarget)) {
        widget.classList.remove("drop-target", "drop-left", "drop-right");
        currDragDropZone = null;
      }
    });

    widget.addEventListener("drop", (event) => {
      event.preventDefault();
      event.stopPropagation();
      widget.classList.remove("drop-target", "drop-left", "drop-right");

      if (!draggingWidget || draggingWidget === widget) {
        return;
      }

      const allWidgets = Array.from(dashboard.querySelectorAll(".widget"));
      const draggingIndex = allWidgets.indexOf(draggingWidget);
      const targetIndex = allWidgets.indexOf(widget);

      if (currDragDropZone === "left") {
        // Place to the left by inserting before
        dashboard.insertBefore(draggingWidget, widget);
      } else if (currDragDropZone === "right") {
        // Place to the right by inserting after
        dashboard.insertBefore(draggingWidget, widget.nextSibling);
      } else {
        // Center drop - vertical reordering
        if (draggingIndex < targetIndex) {
          dashboard.insertBefore(draggingWidget, widget.nextSibling);
        } else {
          dashboard.insertBefore(draggingWidget, widget);
        }
      }

      // Save layout to storage
      saveLayoutState();
    });
  });
}

// Save dashboard layout state to localStorage
function saveLayoutState() {
  const dashboard = document.getElementById("dashboard");
  if (!dashboard) return;

  const layout = Array.from(dashboard.querySelectorAll(".widget"))
    .map(w => w.getAttribute("data-widget"))
    .filter(Boolean);

  localStorage.setItem("dashboard_layout", JSON.stringify(layout));
}

// Restore dashboard layout from localStorage
function restoreLayoutState() {
  const dashboard = document.getElementById("dashboard");
  if (!dashboard) return;

  const saved = localStorage.getItem("dashboard_layout");
  if (!saved) return;

  try {
    const layout = JSON.parse(saved);
    const widgets = Array.from(dashboard.querySelectorAll(".widget"));
    
    layout.forEach(dataWidget => {
      const widget = widgets.find(w => w.getAttribute("data-widget") === dataWidget);
      if (widget) {
        dashboard.appendChild(widget);
      }
    });
  } catch (e) {
    console.error("Failed to restore layout:", e);
  }
}

function saveWidgetSizes() {
  const dashboard = document.getElementById("dashboard");
  if (!dashboard) return;

  const sizes = {};
  Array.from(dashboard.querySelectorAll(".widget")).forEach((widget) => {
    const id = widget.getAttribute("data-widget");
    if (!id) return;

    const span = widget.style.gridColumn.replace("span", "").trim();
    const height = widget.style.height.replace("px", "").trim();

    if (span || height) {
      sizes[id] = {};
      if (span) sizes[id].span = Number(span);
      if (height) sizes[id].height = Number(height);
    }
  });

  localStorage.setItem(WIDGET_SIZE_KEY, JSON.stringify(sizes));
}

function restoreWidgetSizes() {
  const dashboard = document.getElementById("dashboard");
  if (!dashboard) return;

  const saved = localStorage.getItem(WIDGET_SIZE_KEY);
  if (!saved) return;

  try {
    const sizes = JSON.parse(saved);
    Array.from(dashboard.querySelectorAll(".widget")).forEach((widget) => {
      const id = widget.getAttribute("data-widget");
      if (!id || !sizes[id]) return;

      const { span, height } = sizes[id];
      if (typeof span === "number" && Number.isFinite(span)) {
        widget.style.gridColumn = `span ${Math.max(1, Math.round(span))}`;
      }
      if (typeof height === "number" && Number.isFinite(height)) {
        widget.style.height = `${Math.max(140, Math.round(height))}px`;
      }
    });
  } catch (e) {
    console.error("Failed to restore widget sizes:", e);
  }
}

function initWidgetResize() {
  const dashboard = document.getElementById("dashboard");
  if (!dashboard) return;

  const widgets = Array.from(dashboard.querySelectorAll(".widget"));

  widgets.forEach((widget) => {
    if (widget.querySelector(".widget-resize-handle")) return;

    const handle = document.createElement("div");
    handle.className = "widget-resize-handle";
    handle.title = "Resize card";
    widget.appendChild(handle);

    let resizing = false;
    let startX = 0;
    let startY = 0;
    let startWidth = 0;
    let startHeight = 0;

    handle.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      event.stopPropagation();

      resizing = true;
      startX = event.clientX;
      startY = event.clientY;

      const rect = widget.getBoundingClientRect();
      startWidth = rect.width;
      startHeight = rect.height;

      widget.classList.add("resizing");
      document.body.classList.add("resizing-dashboard");
      widget.draggable = false;
      handle.setPointerCapture(event.pointerId);
    });

    handle.addEventListener("pointermove", (event) => {
      if (!resizing) return;

      const width = Math.max(280, startWidth + (event.clientX - startX));
      const height = Math.max(140, startHeight + (event.clientY - startY));

      const styles = window.getComputedStyle(dashboard);
      const columns = styles.gridTemplateColumns.split(" ").filter(Boolean).length || 1;
      const gap = parseFloat(styles.columnGap || "0") || 0;
      const usableWidth = dashboard.clientWidth - Math.max(0, columns - 1) * gap;
      const colWidth = usableWidth / columns;
      const span = Math.max(1, Math.min(columns, Math.round((width + gap) / (colWidth + gap))));

      widget.style.gridColumn = `span ${span}`;
      widget.style.height = `${Math.round(height)}px`;
    });

    const finishResize = (event) => {
      if (!resizing) return;
      resizing = false;
      widget.classList.remove("resizing");
      document.body.classList.remove("resizing-dashboard");
      widget.draggable = true;
      if (event && typeof event.pointerId === "number") {
        try {
          handle.releasePointerCapture(event.pointerId);
        } catch (_err) {
          // Ignore capture release errors from interrupted pointer sequences.
        }
      }
      saveWidgetSizes();
    };

    handle.addEventListener("pointerup", finishResize);
    handle.addEventListener("pointercancel", finishResize);
  });
}

function updateLabel(id, decimals) {
  const el = document.getElementById(id);
  document.getElementById(id + "_val").textContent = Number(el.value).toFixed(decimals);
}

function updateFlickerLabel() {
  const mode = Number(document.getElementById("flicker").value);
  document.getElementById("flicker_val").textContent = mode === 0 ? "Off" : "60Hz";
}

function updateCameraAvailability(data) {
  const streamWrap = document.querySelector(".stream-wrap");
  const notice = document.getElementById("camera_notice");
  const noticeText = document.getElementById("camera_notice_text");

  if (!streamWrap || !notice || !noticeText) {
    return;
  }

  if (data.CameraAvailable === false) {
    streamWrap.classList.add("hidden");
    notice.classList.remove("hidden");
    noticeText.textContent = data.CameraError || "No Raspberry Pi camera is currently connected.";
  } else {
    notice.classList.add("hidden");
    streamWrap.classList.remove("hidden");
  }
}

async function pushGains() {
  const r = document.getElementById("r_gain").value;
  const b = document.getElementById("b_gain").value;
  await callApi(`/api/set_gains?r=${encodeURIComponent(r)}&b=${encodeURIComponent(b)}`);
}

async function pushImageControls() {
  const brightness = document.getElementById("brightness").value;
  const contrast = document.getElementById("contrast").value;
  const saturation = document.getElementById("saturation").value;
  await callApi(
    `/api/set_image?brightness=${encodeURIComponent(brightness)}&contrast=${encodeURIComponent(contrast)}&saturation=${encodeURIComponent(saturation)}`
  );
}

async function pushExposure() {
  const exposure = document.getElementById("exposure").value;
  await callApi(`/api/set_exposure?exposure=${encodeURIComponent(exposure)}`);
}

async function pushFlicker() {
  const mode = document.getElementById("flicker").value;
  await callApi(`/api/set_flicker?mode=${encodeURIComponent(mode)}`);
}

async function setNeutral() {
  document.getElementById("r_gain").value = "1.0";
  document.getElementById("b_gain").value = "1.0";
  updateLabel("r_gain", 2);
  updateLabel("b_gain", 2);
  await pushGains();
}

async function loadStatus() {
  const response = await fetch("/api/status");
  const data = await response.json();
  document.getElementById("status").textContent = JSON.stringify(data, null, 2);
  updateCameraAvailability(data);

  if (Array.isArray(data.ColourGains) && data.ColourGains.length === 2) {
    document.getElementById("r_gain").value = data.ColourGains[0];
    document.getElementById("b_gain").value = data.ColourGains[1];
    updateLabel("r_gain", 2);
    updateLabel("b_gain", 2);
  }

  if (typeof data.Brightness === "number") {
    document.getElementById("brightness").value = data.Brightness;
    updateLabel("brightness", 2);
  }

  if (typeof data.Contrast === "number") {
    document.getElementById("contrast").value = data.Contrast;
    updateLabel("contrast", 2);
  }

  if (typeof data.Saturation === "number") {
    document.getElementById("saturation").value = data.Saturation;
    updateLabel("saturation", 2);
  }

  if (typeof data.ExposureTime === "number") {
    document.getElementById("exposure").value = data.ExposureTime;
    updateLabel("exposure", 0);
  }

  if (typeof data.AeFlickerMode === "number") {
    document.getElementById("flicker").value = data.AeFlickerMode;
    updateFlickerLabel();
  }
}

async function stageMove(axis, direction, stepMode, stepValue = null) {
  await loggedFetch("/api/nanopositioner/move", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ axis, direction, step_mode: stepMode, step_value: stepValue }),
  }, `stageMove ${axis} ${direction} ${stepMode} ${stepValue ?? ""}`.trim());
  await loadNanopositionerStatus();
}

function updateStageStepButtons() {
  const buttons = Array.from(document.querySelectorAll(".stage-step-btn"));
  buttons.forEach((button) => {
    const mode = button.getAttribute("data-step-mode");
    const label = button.getAttribute("data-step-label");
    const selected = mode === stageStepState.mode && label === stageStepState.label;
    button.classList.toggle("stage-step-active", selected);
  });
}

function stageSelectStep(mode, label) {
  stageStepState.mode = mode === "coarse" ? "coarse" : "fine";
  stageStepState.label = String(label || "1");
  updateStageStepButtons();
}

function stageNudge(axis, direction) {
  const stepValue = Number.parseFloat(stageStepState.label);
  return stageMove(axis, direction, stageStepState.mode, Number.isFinite(stepValue) ? stepValue : null);
}

async function stageSetSpeed() {
  const speedInput = document.getElementById("stage_speed_input");
  const speed = Number.parseFloat(speedInput?.value || "");
  if (!Number.isFinite(speed) || speed <= 0) {
    appendCommandLog("ERR stageSetSpeed -> invalid speed value");
    return;
  }

  await loggedFetch("/api/nanopositioner/speed", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ speed }),
  }, `stageSetSpeed ${speed}`);
  await loadNanopositionerStatus();
}

function scheduleStageSetSpeed() {
  if (stageSpeedSetTimer !== null) {
    clearTimeout(stageSpeedSetTimer);
  }
  stageSpeedSetTimer = window.setTimeout(() => {
    stageSetSpeed();
    stageSpeedSetTimer = null;
  }, 220);
}

async function stageMoveAbsolute(x, y, z) {
  await loggedFetch("/api/nanopositioner/move-absolute", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ x, y, z }),
  }, `stageMoveAbsolute ${x} ${y} ${z}`);
  await loadNanopositionerStatus();
}

function readNumericInput(id) {
  const el = document.getElementById(id);
  const value = Number.parseFloat(el?.value || "");
  return Number.isFinite(value) ? value : null;
}

async function stageMoveAbsoluteFromInputs() {
  const x = readNumericInput("stage_x_input");
  const y = readNumericInput("stage_y_input");
  const z = readNumericInput("stage_z_input");
  if (x === null || y === null || z === null) {
    appendCommandLog("ERR stageMoveAbsolute -> invalid XYZ input");
    return;
  }
  await stageMoveAbsolute(x, y, z);
}

function syncInputValue(id, value, decimals = 1) {
  const el = document.getElementById(id);
  if (!el) return;
  if (document.activeElement === el) return;
  if (typeof value !== "number" || !Number.isFinite(value)) return;
  el.value = value.toFixed(decimals);
}

function updateStagePositionHints(position, travelRange = null) {
  if (!position || typeof position !== "object") return;
  const x = Number.isFinite(position.x) ? position.x : 0;
  const y = Number.isFinite(position.y) ? position.y : 0;
  const z = Number.isFinite(position.z) ? position.z : 0;
  const rangeText = Array.isArray(travelRange) && travelRange.length === 2
    ? ` | Range: ${travelRange[0]} to ${travelRange[1]} mm`
    : "";
  const text = `Current position (mm): X ${x.toFixed(3)}, Y ${y.toFixed(3)}, Z ${z.toFixed(3)}${rangeText}`;

  const speedHint = document.getElementById("stage_position_hint_speed");
  if (speedHint) {
    speedHint.textContent = text;
  }

  const xyzHint = document.getElementById("stage_position_hint_xyz");
  if (xyzHint) {
    xyzHint.textContent = text;
  }
}

async function stageHomeAxis(axis) {
  await loggedFetch("/api/nanopositioner/home-axis", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ axis }),
  }, `stageHomeAxis ${axis}`);
  await loadNanopositionerStatus();
}

async function stageHome() {
  await loggedFetch("/api/nanopositioner/home", { method: "POST" }, "stageHome");
  await loadNanopositionerStatus();
}

async function stageStop() {
  await loggedFetch("/api/nanopositioner/stop", { method: "POST" }, "stageStop");
  await loadNanopositionerStatus();
}

async function loadNanopositionerStatus() {
  const response = await fetch("/api/nanopositioner/status");
  const data = await response.json();
  const target = document.getElementById("stage_status");
  if (target) {
    target.textContent = JSON.stringify(data, null, 2);
  }

  const position = data?.position;
  const travelRange = Array.isArray(data?.travel_range_mm) ? data.travel_range_mm : null;
  if (position && typeof position === "object") {
    syncInputValue("stage_x_input", position.x, 3);
    syncInputValue("stage_y_input", position.y, 3);
    syncInputValue("stage_z_input", position.z, 3);
    updateStagePositionHints(position, travelRange);
  }

  if (typeof data?.jog_speed === "number") {
    syncInputValue("stage_speed_input", data.jog_speed, 2);
  }
}

// Thermal Plate Control Functions
let thermalCanvas = null;
let thermalChart = null;
let thermalPowerState = false;

function initThermalGraph() {
  const canvas = document.getElementById("thermal_graph");
  if (!canvas) return;

  thermalCanvas = canvas;
  const ctx = canvas.getContext("2d");

  // Simple temperature graph with canvas
  function drawThermalGraph(history) {
    const padding = 30;
    const width = canvas.width;
    const height = canvas.height;

    ctx.fillStyle = "#f9fbfd";
    ctx.fillRect(0, 0, width, height);

    // Draw grid and labels
    ctx.strokeStyle = "#e5e7eb";
    ctx.fillStyle = "#6b7280";
    ctx.font = "11px IBM Plex Mono";

    // Temperature scale (0-100°C)
    const minTemp = 0;
    const maxTemp = 100;

    // Draw Y-axis labels
    for (let temp = 0; temp <= 100; temp += 20) {
      const y = height - padding - ((temp - minTemp) / (maxTemp - minTemp)) * (height - 2 * padding);
      ctx.fillText(temp + "°", 5, y + 4);

      // Grid lines
      ctx.strokeStyle = "#e5e7eb";
      ctx.beginPath();
      ctx.moveTo(padding, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }

    // Draw X-axis time labels for a 5-minute window.
    const xLabelY = height - 8;
    const xStart = padding;
    const xEnd = width - 4;
    ctx.fillStyle = "#64748b";
    ctx.fillText("-5m", xStart, xLabelY);
    ctx.fillText("-2.5m", Math.round((xStart + xEnd) / 2) - 20, xLabelY);
    ctx.fillText("now", xEnd - 24, xLabelY);

    // Vertical guide lines for time ticks.
    ctx.strokeStyle = "#e5e7eb";
    [xStart, Math.round((xStart + xEnd) / 2), xEnd].forEach((x) => {
      ctx.beginPath();
      ctx.moveTo(x, padding);
      ctx.lineTo(x, height - padding);
      ctx.stroke();
    });

    // Draw data line
    if (history && history.length > 0) {
      ctx.strokeStyle = "#ef4444";
      ctx.lineWidth = 2;
      ctx.beginPath();

      const step = (width - 2 * padding) / Math.max(history.length - 1, 1);

      history.forEach((temp, idx) => {
        const x = padding + idx * step;
        const y = height - padding - ((temp - minTemp) / (maxTemp - minTemp)) * (height - 2 * padding);

        if (idx === 0) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      });

      ctx.stroke();

      // Draw latest point
      const lastTemp = history[history.length - 1];
      const lastX = padding + (history.length - 1) * step;
      const lastY = height - padding - ((lastTemp - minTemp) / (maxTemp - minTemp)) * (height - 2 * padding);

      ctx.fillStyle = "#ef4444";
      ctx.beginPath();
      ctx.arc(lastX, lastY, 4, 0, 2 * Math.PI);
      ctx.fill();
    }
  }

  // Override the draw function
  window.drawThermalGraph = drawThermalGraph;
}

async function loadThermalStatus() {
  try {
    const response = await fetch("/api/thermal/status");
    const data = await response.json();

    // Update display values
    const currentTemp = Math.round(data.current_temperature * 10) / 10;
    const targetTemp = Math.round(data.target_temperature * 10) / 10;

    document.getElementById("thermal_current").textContent = currentTemp;
    document.getElementById("thermal_target").textContent = targetTemp;

    // Update power button state
    const powerBtn = document.getElementById("thermal_power_btn");
    thermalPowerState = data.is_on;
    if (thermalPowerState) {
      powerBtn.classList.remove("danger");
      powerBtn.classList.add("success");
      powerBtn.textContent = "Turn OFF";
    } else {
      powerBtn.classList.add("danger");
      powerBtn.classList.remove("success");
      powerBtn.textContent = "Turn ON";
    }

    // Update graph
    if (window.drawThermalGraph && data.temperature_history) {
      window.drawThermalGraph(data.temperature_history);
    }

    // Update status display
    const statusEl = document.getElementById("thermal_status");
    if (statusEl) {
      statusEl.textContent = JSON.stringify(data, null, 2);
    }
  } catch (error) {
    console.error("Error loading thermal status:", error);
  }
}

async function setThermalTemperature(temp) {
  // Allow passing temp as parameter or reading from input
  if (temp === undefined) {
    const input = document.getElementById("thermal_temp_input");
    temp = parseFloat(input.value) || 25;
  }

  try {
    const response = await loggedFetch("/api/thermal/set-temperature", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_temperature: temp }),
    }, `thermalSetTemperature ${temp}`);

    const data = await response.json();
    document.getElementById("thermal_target").textContent = Math.round(temp * 10) / 10;

    // Also update status text
    const statusEl = document.getElementById("thermal_status");
    if (statusEl) {
      statusEl.textContent = JSON.stringify(data, null, 2);
    }
  } catch (error) {
    console.error("Error setting thermal temperature:", error);
  }
}

async function toggleThermalPower() {
  const newState = !thermalPowerState;

  try {
    const response = await loggedFetch("/api/thermal/power", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: newState }),
    }, `thermalPower ${newState ? "ON" : "OFF"}`);

    const data = await response.json();
    thermalPowerState = newState;

    // Update UI
    const powerBtn = document.getElementById("thermal_power_btn");
    if (thermalPowerState) {
      powerBtn.classList.remove("danger");
      powerBtn.classList.add("success");
      powerBtn.textContent = "Turn OFF";
    } else {
      powerBtn.classList.add("danger");
      powerBtn.classList.remove("success");
      powerBtn.textContent = "Turn ON";
    }

    if (typeof data.target_temperature === "number") {
      document.getElementById("thermal_target").textContent = Math.round(data.target_temperature * 10) / 10;
    }

    // Update status
    const statusEl = document.getElementById("thermal_status");
    if (statusEl) {
      statusEl.textContent = JSON.stringify(data, null, 2);
    }
  } catch (error) {
    console.error("Error toggling thermal power:", error);
  }
}

// Vacuum Pump Control Functions
let vacuumPowerState = false;
let rotationCurrentAngle = 0;

async function loadVacuumStatus() {
  try {
    const response = await fetch("/api/vacuum/status");
    const data = await response.json();

    // Update power state
    vacuumPowerState = data.is_on;

    // Update status indicator and button
    const indicator = document.getElementById("vacuum_status_indicator");
    const statusValue = document.getElementById("vacuum_status_value");
    const powerBtn = document.getElementById("vacuum_power_btn");

    if (vacuumPowerState) {
      indicator.classList.add("active");
      statusValue.textContent = "ON";
      powerBtn.classList.remove("danger");
      powerBtn.classList.add("success");
      powerBtn.textContent = "Turn OFF";
    } else {
      indicator.classList.remove("active");
      statusValue.textContent = "OFF";
      powerBtn.classList.add("danger");
      powerBtn.classList.remove("success");
      powerBtn.textContent = "Turn ON";
    }

    // Update status display
    const statusEl = document.getElementById("vacuum_status");
    if (statusEl) {
      statusEl.textContent = JSON.stringify(data, null, 2);
    }
  } catch (error) {
    console.error("Error loading vacuum status:", error);
  }
}

async function toggleVacuumPower() {
  const newState = !vacuumPowerState;

  try {
    const response = await loggedFetch("/api/vacuum/power", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: newState }),
    }, `vacuumPower ${newState ? "ON" : "OFF"}`);

    const data = await response.json();
    vacuumPowerState = newState;

    // Update UI
    const indicator = document.getElementById("vacuum_status_indicator");
    const statusValue = document.getElementById("vacuum_status_value");
    const powerBtn = document.getElementById("vacuum_power_btn");

    if (vacuumPowerState) {
      indicator.classList.add("active");
      statusValue.textContent = "ON";
      powerBtn.classList.remove("danger");
      powerBtn.classList.add("success");
      powerBtn.textContent = "Turn OFF";
    } else {
      indicator.classList.remove("active");
      statusValue.textContent = "OFF";
      powerBtn.classList.add("danger");
      powerBtn.classList.remove("success");
      powerBtn.textContent = "Turn ON";
    }

    // Update status
    const statusEl = document.getElementById("vacuum_status");
    if (statusEl) {
      statusEl.textContent = JSON.stringify(data, null, 2);
    }
  } catch (error) {
    console.error("Error toggling vacuum power:", error);
  }
}

// Rotation Plate Control Functions
function setRotationIndicator(angle) {
  const indicator = document.getElementById("rotation_indicator");
  if (!indicator) {
    return;
  }
  indicator.style.transform = `rotate(${Number(angle) || 0}deg)`;
}

async function loadRotationStatus() {
  try {
    const response = await fetch("/api/rotation/status");
    const data = await response.json();

    const angle = Number(data.current_angle) || 0;
    rotationCurrentAngle = angle;

    const currentLabel = document.getElementById("rotation_current");
    if (currentLabel) {
      currentLabel.textContent = angle.toFixed(1);
    }

    const targetInput = document.getElementById("rotation_target_input");
    if (targetInput && document.activeElement !== targetInput) {
      targetInput.value = angle.toFixed(1);
    }

    setRotationIndicator(angle);

    const statusEl = document.getElementById("rotation_status");
    if (statusEl) {
      statusEl.textContent = JSON.stringify(data, null, 2);
    }
  } catch (error) {
    console.error("Error loading rotation status:", error);
  }
}

async function rotateDirection(direction) {
  const stepInput = document.getElementById("rotation_step_input");
  const step = Number.parseFloat(stepInput?.value || "5") || 5;

  await loggedFetch("/api/rotation/nudge", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ direction, step_degrees: Math.abs(step) }),
  }, `rotationNudge ${direction} ${step}`);
  await loadRotationStatus();
}

async function setRotationAngle(value) {
  let angle = value;
  if (angle === undefined) {
    const targetInput = document.getElementById("rotation_target_input");
    angle = Number.parseFloat(targetInput?.value || "");
  }

  if (!Number.isFinite(angle)) {
    appendCommandLog("ERR rotationSet -> invalid angle input");
    return;
  }

  await loggedFetch("/api/rotation/set", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ angle }),
  }, `rotationSet ${angle}`);
  await loadRotationStatus();
}

initDashboardDrag();
restoreLayoutState();
initWidgetResize();
restoreWidgetSizes();
initStatusToggles();
initThermalGraph();
updateStageStepButtons();
startDashboardPolling();
