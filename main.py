#!/usr/bin/python3
# USE 0.5 A 5 V
import io
import json
import logging
import os
import socketserver
import sys
import time
from http import server
from pathlib import Path
from threading import Condition, Lock
from urllib.parse import urlparse, parse_qs

from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput

BASE_DIR = Path(__file__).resolve().parent
CAMERA_DIR = BASE_DIR / "camera"
TUNING_DIR = CAMERA_DIR / "tuning_files" / "vc4"

if str(CAMERA_DIR) not in sys.path:
    sys.path.insert(0, str(CAMERA_DIR))

import picamera_recalibrate_utils as ru

PAGE = """\
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Alignment Microscope</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      margin: 0;
      background: #111;
      color: #eee;
    }
    .wrap {
      display: grid;
      grid-template-columns: 1fr 360px;
      gap: 16px;
      padding: 16px;
      height: 100vh;
      box-sizing: border-box;
    }
    .video-panel, .control-panel {
      background: #1b1b1b;
      border-radius: 12px;
      padding: 16px;
      box-sizing: border-box;
    }
    .video-panel {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-width: 0;
    }
    img {
      max-width: 100%;
      max-height: calc(100vh - 100px);
      border-radius: 8px;
      background: black;
    }
    h1, h2 {
      margin-top: 0;
    }
    .row {
      margin-bottom: 14px;
    }
    label {
      display: block;
      margin-bottom: 6px;
      font-size: 14px;
    }
    input[type=range] {
      width: 100%;
    }
    .btns {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-bottom: 12px;
    }
    button {
      padding: 10px 12px;
      border: none;
      border-radius: 8px;
      background: #2d6cdf;
      color: white;
      cursor: pointer;
      font-size: 14px;
    }
    button.secondary {
      background: #444;
    }
    button.warn {
      background: #a33;
    }
    pre {
      background: #0d0d0d;
      padding: 10px;
      border-radius: 8px;
      overflow: auto;
      font-size: 12px;
      white-space: pre-wrap;
    }
    .small {
      font-size: 12px;
      color: #bbb;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="video-panel">
      <h1>Stamping Robot Alignment Microscope</h1>
      <img id="stream" src="/stream.mjpg" alt="Live stream">
      <p class="small">
        Use a uniform white target, then click Flat-Field Calibrate to remove pink edges.
      </p>
    </div>

    <div class="control-panel">
      <h2>Controls</h2>

      <div class="btns">
        <button onclick="call('/api/reset_auto')">Reset Auto</button>
        <button onclick="call('/api/calibrate')">Lock Current AWB</button>
        <button onclick="call('/api/flat_field_calibrate')">Flat-Field Calibrate</button>
        <button class="secondary" onclick="call('/api/awb?enabled=1')">AWB ON</button>
        <button class="secondary" onclick="call('/api/awb?enabled=0')">AWB OFF</button>
        <button class="secondary" onclick="call('/api/ae?enabled=1')">AE ON</button>
        <button class="secondary" onclick="call('/api/ae?enabled=0')">AE OFF</button>
        <button class="warn" onclick="setNeutral()">Set Neutral Gains</button>
      </div>

      <div class="row">
        <label for="r_gain">Red Gain: <span id="r_gain_val">1.50</span></label>
        <input id="r_gain" type="range" min="0.1" max="8.0" step="0.01" value="1.50"
               oninput="updateLabel('r_gain')" onchange="pushGains()">
      </div>

      <div class="row">
        <label for="b_gain">Blue Gain: <span id="b_gain_val">1.50</span></label>
        <input id="b_gain" type="range" min="0.1" max="8.0" step="0.01" value="1.50"
               oninput="updateLabel('b_gain')" onchange="pushGains()">
      </div>

      <div class="row">
        <label for="brightness">Brightness: <span id="brightness_val">0.00</span></label>
        <input id="brightness" type="range" min="-1.0" max="1.0" step="0.01" value="0.00"
               oninput="updateLabel('brightness')" onchange="pushImageControls()">
      </div>

      <div class="row">
        <label for="contrast">Contrast: <span id="contrast_val">1.00</span></label>
        <input id="contrast" type="range" min="0.0" max="4.0" step="0.01" value="1.00"
               oninput="updateLabel('contrast')" onchange="pushImageControls()">
      </div>

      <div class="row">
        <label for="saturation">Saturation: <span id="saturation_val">1.00</span></label>
        <input id="saturation" type="range" min="0.0" max="4.0" step="0.01" value="1.00"
               oninput="updateLabel('saturation')" onchange="pushImageControls()">
      </div>

      <div class="row">
        <label for="exposure">Exposure (us): <span id="exposure_val">8333</span></label>
        <input id="exposure" type="range" min="100" max="30000" step="50" value="8333"
               oninput="updateLabel('exposure')" onchange="pushExposure()">
      </div>

      <div class="row">
        <label for="flicker">Flicker: <span id="flicker_val">60Hz</span></label>
        <input id="flicker" type="range" min="0" max="1" step="1" value="1"
               oninput="updateFlickerLabel()" onchange="pushFlicker()">
      </div>

      <div class="btns">
        <button class="secondary" onclick="loadStatus()">Refresh Status</button>
      </div>

      <h2>Status</h2>
      <pre id="status">Loading...</pre>
    </div>
  </div>

<script>
async function call(path) {
  const res = await fetch(path);
  const text = await res.text();
  await loadStatus();
  return text;
}

function updateLabel(id) {
  document.getElementById(id + "_val").textContent =
    parseFloat(document.getElementById(id).value).toFixed(2);
}

function updateFlickerLabel() {
  const v = parseInt(document.getElementById("flicker").value, 10);
  document.getElementById("flicker_val").textContent = v === 0 ? "Off" : "60Hz";
}

async function pushGains() {
  const r = document.getElementById("r_gain").value;
  const b = document.getElementById("b_gain").value;
  await call(`/api/set_gains?r=${encodeURIComponent(r)}&b=${encodeURIComponent(b)}`);
}

async function pushImageControls() {
  const brightness = document.getElementById("brightness").value;
  const contrast = document.getElementById("contrast").value;
  const saturation = document.getElementById("saturation").value;
  await call(`/api/set_image?brightness=${encodeURIComponent(brightness)}&contrast=${encodeURIComponent(contrast)}&saturation=${encodeURIComponent(saturation)}`);
}

async function pushExposure() {
  const exposure = document.getElementById("exposure").value;
  await call(`/api/set_exposure?exposure=${encodeURIComponent(exposure)}`);
}

async function pushFlicker() {
  const flicker = document.getElementById("flicker").value;
  await call(`/api/set_flicker?mode=${encodeURIComponent(flicker)}`);
}

async function setNeutral() {
  document.getElementById("r_gain").value = 1.0;
  document.getElementById("b_gain").value = 1.0;
  updateLabel("r_gain");
  updateLabel("b_gain");
  await pushGains();
}

async function loadStatus() {
  const res = await fetch("/api/status");
  const data = await res.json();
  document.getElementById("status").textContent = JSON.stringify(data, null, 2);

  if (data.ColourGains && data.ColourGains.length === 2) {
    document.getElementById("r_gain").value = data.ColourGains[0];
    document.getElementById("b_gain").value = data.ColourGains[1];
    updateLabel("r_gain");
    updateLabel("b_gain");
  }

  if (typeof data.Brightness === "number") {
    document.getElementById("brightness").value = data.Brightness;
    updateLabel("brightness");
  }
  if (typeof data.Contrast === "number") {
    document.getElementById("contrast").value = data.Contrast;
    updateLabel("contrast");
  }
  if (typeof data.Saturation === "number") {
    document.getElementById("saturation").value = data.Saturation;
    updateLabel("saturation");
  }

  if (typeof data.ExposureTime === "number") {
    document.getElementById("exposure").value = data.ExposureTime;
    updateLabel("exposure");
  }

  if (typeof data.AeFlickerMode === "number") {
    document.getElementById("flicker").value = data.AeFlickerMode;
    updateFlickerLabel();
  }
}

loadStatus();
setInterval(loadStatus, 3000);
</script>
</body>
</html>
"""

class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.frame_id = 0
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.frame_id += 1
            self.condition.notify_all()

camera_lock = Lock()
sensor_info = ru.IMX219_SENSOR_INFO
camera_num = int(os.getenv("CAMERA_NUM", "0"))


def _detect_sensor_model(cam_num: int) -> str:
    probe = Picamera2(camera_num=cam_num)
    try:
        return str(probe.camera_properties.get("Model", "")).lower().strip()
    finally:
        probe.close()


def _load_tuning(sensor_model: str) -> dict | None:
    tuning_file = os.getenv("CAMERA_TUNING_FILE", "").strip()
    if tuning_file:
        tuning_path = Path(tuning_file)
    else:
        tuning_path = TUNING_DIR / f"{sensor_model}.json"

    if not tuning_path.exists():
        logging.warning("No tuning file found at %s. Using Picamera2 defaults.", tuning_path)
        return None

    try:
        with tuning_path.open("r", encoding="utf-8") as fh:
            tuning = json.load(fh)
        logging.warning("Loaded tuning file: %s", tuning_path)
        return tuning
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("Failed to load tuning file %s: %s", tuning_path, exc)
        return None

def get_md():
    with camera_lock:
        return picam2.capture_metadata()

def reset_auto():
    with camera_lock:
        picam2.set_controls({
            "AeEnable": True,
            "AwbEnable": True
        })

def set_awb(enabled: bool):
    with camera_lock:
        picam2.set_controls({"AwbEnable": bool(enabled)})

def set_ae(enabled: bool):
    with camera_lock:
        picam2.set_controls({"AeEnable": bool(enabled)})

def set_gains(r: float, b: float):
    with camera_lock:
        picam2.set_controls({
            "AwbEnable": False,
            "ColourGains": (float(r), float(b))
        })

def set_image_controls(brightness: float, contrast: float, saturation: float):
    with camera_lock:
        picam2.set_controls({
            "Brightness": float(brightness),
            "Contrast": float(contrast),
            "Saturation": float(saturation)
        })

def set_exposure(exposure_us: int):
    with camera_lock:
        picam2.set_controls({
            "AeEnable": False,
            "ExposureTime": int(exposure_us)
        })

def set_flicker(mode: int):
    mode = int(mode)
    if mode not in (0, 1):
        mode = 1
    with camera_lock:
        picam2.set_controls({
            "AeFlickerMode": mode,
        })

def calibrate_camera(warmup_seconds: float = 1.5):
    reset_auto()
    time.sleep(max(0.0, warmup_seconds))
    md = get_md()
    gains = md.get("ColourGains", None)

    with camera_lock:
        if gains:
            picam2.set_controls({
                "AwbEnable": False,
                "ColourGains": gains
            })

    logging.warning("CALIBRATE locked gains=%s", gains)
    return gains

def flat_field_calibrate():
    """
    OpenFlexure-style flat-field calibration.
    Point the microscope at a uniform white target before calling this.

    This:
      1. adjusts shutter/gain from raw white frame
      2. adjusts white balance from raw
      3. computes lens shading table
      4. applies the lens shading table
    """
    with camera_lock:
        ru.adjust_shutter_and_gain_from_raw(
            picam2,
            sensor_info,
            target_white_level=sensor_info.default_target_white_level,
        )
        ru.adjust_white_balance_from_raw(picam2, sensor_info)
        lst = ru.lst_from_camera(picam2, sensor_info)
        picam2.lens_shading_table = lst

    md = get_md()
    logging.warning(
        "FLAT FIELD APPLIED: gains=%s exposure=%s analogue=%s",
        md.get("ColourGains"),
        md.get("ExposureTime"),
        md.get("AnalogueGain"),
    )

    return {
        "ok": True,
        "ColourGains": md.get("ColourGains"),
        "ExposureTime": md.get("ExposureTime"),
        "AnalogueGain": md.get("AnalogueGain"),
    }

def status_payload():
    md = get_md()
    payload = {
        "ColourGains": md.get("ColourGains", None),
        "ExposureTime": md.get("ExposureTime", None),
        "AnalogueGain": md.get("AnalogueGain", None),
        "AwbState": md.get("AwbState", None),
        "AeFlickerMode": md.get("AeFlickerMode", None),
        "Brightness": current_image_controls["Brightness"],
        "Contrast": current_image_controls["Contrast"],
        "Saturation": current_image_controls["Saturation"],
    }
    return payload

current_image_controls = {
    "Brightness": 0.0,
    "Contrast": 1.0,
    "Saturation": 1.0,
}

class StreamingHandler(server.BaseHTTPRequestHandler):
    def _send_text(self, code: int, msg: str):
        content = msg.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, obj):
        content = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == '/':
            self.send_response(301)
            self.send_header('Location', '/index.html')
            self.end_headers()
            return

        if path == '/index.html':
            content = PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if path == '/api/awb':
            enabled = qs.get("enabled", ["1"])[0].strip().lower()
            set_awb(enabled in ("1", "true", "on", "yes"))
            self._send_text(200, "OK\n")
            return
        if path == '/api/ae':
            enabled = qs.get("enabled", ["1"])[0].strip().lower()
            set_ae(enabled in ("1", "true", "on", "yes"))
            self._send_text(200, "OK\n")
            return

        if path == '/api/reset_auto':
            reset_auto()
            self._send_text(200, "Auto reset: AE+AWB enabled\n")
            return

        if path == '/api/set_gains':
            r = float(qs.get("r", ["1.5"])[0])
            b = float(qs.get("b", ["1.5"])[0])
            set_gains(r, b)
            self._send_text(200, f"Set gains: r={r:.2f} b={b:.2f}\n")
            return

        if path == '/api/set_image':
            brightness = float(qs.get("brightness", [str(current_image_controls["Brightness"])])[0])
            contrast = float(qs.get("contrast", [str(current_image_controls["Contrast"])])[0])
            saturation = float(qs.get("saturation", [str(current_image_controls["Saturation"])])[0])

            current_image_controls["Brightness"] = brightness
            current_image_controls["Contrast"] = contrast
            current_image_controls["Saturation"] = saturation

            set_image_controls(brightness, contrast, saturation)
            self._send_text(
                200,
                f"Set image controls: brightness={brightness:.2f}, contrast={contrast:.2f}, saturation={saturation:.2f}\n"
            )
            return
        if path == '/api/set_exposure':
            exposure = int(qs.get("exposure", ["8333"])[0])
            set_exposure(exposure)
            self._send_text(200, f"Set exposure: {exposure} us\n")
            return
        if path == '/api/set_flicker':
            mode = int(qs.get("mode", ["1"])[0])
            set_flicker(mode)
            self._send_text(200, f"Set flicker mode: {mode}\n")
            return

        if path == '/api/status':
            self._send_json(status_payload())
            return

        if path == '/api/calibrate':
            gains = calibrate_camera(warmup_seconds=1.5)
            self._send_text(200, f"Calibrated. Locked gains={gains}\n")
            return

        if path == '/api/flat_field_calibrate':
            try:
                result = flat_field_calibrate()
                self._send_json(result)
            except Exception as e:
                logging.exception("Flat-field calibration failed")
                self._send_json({
                    "ok": False,
                    "error": str(e)
                })
            return

        if path == '/stream.mjpg':
            self.send_response(200)
            self.send_header('Age', '0')
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            try:
                last_frame_id = 0
                while True:
                    with output.condition:
                        output.condition.wait_for(lambda: output.frame is not None and output.frame_id != last_frame_id)
                        frame = output.frame
                        last_frame_id = output.frame_id
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', str(len(frame)))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
                    self.wfile.flush()
            except Exception as e:
                logging.warning('Removed streaming client %s: %s', self.client_address, str(e))
            return

        self.send_error(404)
        self.end_headers()

class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

logging.basicConfig(level=logging.WARNING)

detected_model = os.getenv("CAMERA_SENSOR_MODEL", "").strip().lower() or _detect_sensor_model(camera_num)
if detected_model == "imx477":
    sensor_info = ru.IMX477_SENSOR_INFO
else:
    sensor_info = ru.IMX219_SENSOR_INFO

tuning = _load_tuning(sensor_info.sensor_model)

if tuning is None:
    picam2 = Picamera2(camera_num=camera_num)
else:
    picam2 = Picamera2(camera_num=camera_num, tuning=tuning)

stream_size_env = os.getenv("STREAM_SIZE", "1280x720").lower().strip()
try:
    stream_w, stream_h = (int(v) for v in stream_size_env.split("x", 1))
    stream_size = (stream_w, stream_h)
except Exception:
    logging.warning("Invalid STREAM_SIZE=%s. Falling back to 1280x720.", stream_size_env)
    stream_size = (1280, 720)

stream_fps_env = os.getenv("STREAM_FPS", "30").strip()
try:
    stream_fps = float(stream_fps_env)
except Exception:
    logging.warning("Invalid STREAM_FPS=%s. Falling back to 30.", stream_fps_env)
    stream_fps = 30.0

config = picam2.create_video_configuration(
    main={"size": stream_size},
    controls={
        "FrameRate": stream_fps,
        "Brightness": 0.0,
        "Contrast": 1.0,
        "Saturation": 1.0,
    }
)
picam2.configure(config)

output = StreamingOutput()
picam2.start_recording(MJPEGEncoder(), FileOutput(output))

# Start with auto exposure + auto white balance enabled.
reset_auto()

try:
    address = ('', 8000)
    httpd = StreamingServer(address, StreamingHandler)
    print("Serving on http://localhost:8000")
    httpd.serve_forever()
finally:
    picam2.stop_recording()
