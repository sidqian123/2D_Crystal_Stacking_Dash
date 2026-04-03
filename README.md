# 2D Crystal Stacking - Microscope Control System

A modern web-based microscope control interface for crystal alignment and stacking. Built with FastAPI backend and interactive web dashboard.

## System Architecture

```
2d_stacking/
├── main.py                      # Application entry point (Uvicorn server)
├── app/
│   ├── web.py                   # FastAPI app instance & camera endpoints
│   └── camera_service.py        # CameraDevice service for image capture
├── devices/                     # Device abstraction layer
│   ├── base_device.py          # Abstract BaseDevice class
│   ├── camera.py               # CameraDevice implementation
│   ├── nanopositioner.py       # NanopositionerDevice implementation
│   ├── thermal.py              # ThermalPlateDevice implementation
│   └── vacuum.py               # VacuumDevice implementation
├── nanopositioner/
│   └── router.py               # 3-axis stage control endpoints
├── thermal_plate/
│   └── router.py               # Temperature control endpoints
├── vacuum/
│   └── router.py               # Vacuum pump control endpoints
├── templates/
│   └── index.html              # Single-page dashboard HTML
└── static/
    ├── app.js                  # Dashboard interactivity & API client
    └── app.css                 # Dashboard styling (Flexbox grid layout)
```

## Device Architecture

### BaseDevice (Abstract)
All controllable devices inherit from `BaseDevice`, providing:
- **Thread-safe state management** via `Lock()`
- **Universal power control** (`is_on`, `set_power()`, `get_power()`)
- **Status tracking** (`status_message`, `set_status()`, `get_status()`)
- **Abstract methods** enforced for all subclasses:
  - `get_device_status()` → Full device status for API responses
  - `get_device_type()` → Device identifier string

**Location:** `devices/base_device.py`

### Device Implementations

#### CameraDevice
**File:** `devices/camera.py`
- Inherits from: `BaseDevice`
- Manages: Image capture availability, camera error tracking
- Methods:
  - `set_camera_available(available: bool, error_msg: str = "")` - Track camera state
  - `get_camera_info()` - Retrieve camera capabilities
  - `get_device_status()` → Returns camera status including availability

#### NanopositionerDevice
**File:** `devices/nanopositioner.py`
- Inherits from: `BaseDevice`
- Manages: 3-axis position tracking (x, y, z), step sizes
- Attributes:
  - `position: Dict[str, float]` - Current position {x, y, z}
  - `fine_step: float` - Fine movement step size
  - `coarse_step: float` - Coarse movement step size
- Methods:
  - `connect(port: str, baud_rate: int = 921600)` - Connect to `OpenMicroStageInterface`
  - `disconnect()` - Close the hardware connection
  - `read_firmware_version() -> tuple[int, int, int]`
  - `read_device_state_info()` - Read raw controller state when connected
  - `set_position(axis: str, value: float)` - Set axis position
  - `move(axis, direction, step_mode) → Dict` - Move in direction
  - `home() → Dict` - Return to origin
  - `stop() → Dict` - Stop movement
  - `get_device_status()` → Returns position and connectivity

#### ThermalPlateDevice
**File:** `devices/thermal.py`
- Inherits from: `BaseDevice`
- Manages: Temperature control and history
- Attributes:
  - `current_temp: float` - Current temperature (°C)
  - `target_temp: float` - Target temperature (°C)
  - `temperature_history: List[float]` - Last 60 readings
- Methods:
  - `set_temperature(temperature: float)` - Compatibility wrapper for the stage API
  - `set_target_temp(target: float)` - Set target (0-100°C)
  - `get_target_temp() → float`
  - `add_reading(temp: float)` - Add temperature to history
  - `get_history() → List[float]` - Get temperature history
  - `get_device_status()` → Returns temperature and power state

#### VacuumDevice
**File:** `devices/vacuum.py`
- Inherits from: `BaseDevice`
- Manages: Vacuum pump on/off control
- Methods:
  - `set_vacuum(vacuum_on: bool)` - Compatibility wrapper for the stage API
  - `get_device_status()` → Returns power state

## API Endpoints

### Camera Endpoints (Web Service)

**Base URL:** `/api`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Dashboard HTML |
| GET | `/stream.mjpg` | MJPEG video stream |
| GET | `/api/status` | Camera status (gains, exposure, etc.) |
| GET | `/api/awb?enabled=1` | Enable/disable Auto White Balance |
| GET | `/api/ae?enabled=1` | Enable/disable Auto Exposure |
| GET | `/api/reset_auto` | Enable AE+AWB |
| GET | `/api/set_gains?r=1.5&b=1.5` | Set color gains (red, blue) |
| GET | `/api/set_image?brightness=0&contrast=1&saturation=1` | Set image controls |
| GET | `/api/set_exposure?exposure=8333` | Set exposure time (µs) |
| GET | `/api/set_flicker?mode=1` | Set flicker mode (0=Off, 1=60Hz) |
| GET | `/api/calibrate` | Lock current AWB |
| GET | `/api/flat_field_calibrate` | Generate flat-field calibration |

**Service:** `CameraService` (via `app/web.py`)

### Nanopositioner Endpoints

**Prefix:** `/api/nanopositioner`

| Method | Endpoint | Request Body | Description |
|--------|----------|--------------|-------------|
| GET | `/status` | — | Get current position and status |
| POST | `/connect` | `{port, baud_rate}` | Connect stage controller |
| POST | `/disconnect` | — | Disconnect stage controller |
| GET | `/firmware-version` | — | Read controller firmware version |
| GET | `/state-info` | — | Read raw controller state |
| POST | `/move` | `{axis, direction, step_mode}` | Move stage |
| POST | `/home` | — | Return to home (0,0,0) |
| POST | `/stop` | — | Stop movement |
| POST | `/step-config` | `{fine_step, coarse_step}` | Configure step sizes |

**Request Model:** `MoveCommand`
```python
{
  "axis": "x" | "y" | "z",
  "direction": "positive" | "negative",
  "step_mode": "fine" | "coarse"
}
```

**Response Model:**
```python
{
  "ok": bool,
  "implemented": false,
  "message": "Hardware driver not implemented yet",
  "device_type": "nanopositioner",
  "position": {"x": float, "y": float, "z": float},
  "is_on": bool,
  "status_message": str
}
```

**Service:** `NanopositionerDevice` (via `nanopositioner/router.py`)

### Thermal Plate Endpoints

**Prefix:** `/api/thermal`

| Method | Endpoint | Request Body | Description |
|--------|----------|--------------|-------------|
| GET | `/status` | — | Get current temperature and status |
| POST | `/set-temperature` | `{target_temperature}` | Set target temperature (0-100°C) |
| POST | `/power` | `{enabled}` | Turn on/off |

**Request Models:**
```python
# SetTemperatureRequest
{
  "target_temperature": float  # 0-100°C
}

# PowerControlRequest
{
  "enabled": bool
}
```

**Response Model:**
```python
{
  "ok": bool,
  "implemented": false,
  "message": "Hardware driver not implemented yet",
  "device_type": "thermal",
  "is_on": bool,
  "current_temperature": float,
  "target_temperature": float,
  "temperature_history": [float, ...],
  "status_message": str
}
```

**Service:** `ThermalPlateDevice` (via `thermal_plate/router.py`)

### Vacuum Pump Endpoints

**Prefix:** `/api/vacuum`

| Method | Endpoint | Request Body | Description |
|--------|----------|--------------|-------------|
| GET | `/status` | — | Get pump status |
| POST | `/power` | `{enabled}` | Turn on/off |

**Request Model:**
```python
{
  "enabled": bool
}
```

**Response Model:**
```python
{
  "ok": bool,
  "implemented": false,
  "message": "Hardware driver not implemented yet",
  "device_type": "vacuum",
  "is_on": bool,
  "status_message": str
}
```

**Service:** `VacuumDevice` (via `vacuum/router.py`)

## Class Hierarchy

```
BaseDevice (ABC)  [devices/base_device.py]
├── CameraDevice  [devices/camera.py]
├── NanopositionerDevice  [devices/nanopositioner.py]
├── ThermalPlateDevice  [devices/thermal.py]
└── VacuumDevice  [devices/vacuum.py]

CameraService (inherits CameraDevice)  [app/camera_service.py]
```

## Dashboard Structure

**Location:** `templates/index.html` + `static/app.js` + `static/app.css`

### UI Layout
- **2D Grid Layout:** CSS Grid with auto-fit columns (360px min-width)
- **5 Draggable Widgets:**
  1. **Camera Feed** - MJPEG stream display
  2. **Camera Controls** - Gain/exposure/contrast sliders + buttons
  3. **Nanopositioner Stage** - X/Y/Z axis controls
  4. **Thermal Plate Control** - Temperature graph + presets
  5. **Vacuum Pump Control** - Simple on/off switch

### Drag-and-Drop Features
- Drag widgets anywhere on the dashboard
- Drop zones:
  - **Left third** → Place side-by-side (left)
  - **Right third** → Place side-by-side (right)
  - **Center** → Vertical reordering
- **Layout persistence** via localStorage

### Auto-Refresh Polling
- Camera status: Every 3 seconds
- Nanopositioner: Every 3 seconds
- Thermal: Every 2 seconds
- Vacuum: Every 2 seconds

## Data Flow

```
Browser/Dashboard
    ↓
app.js (callApi, loadStatus functions)
    ↓
FastAPI Router (web.py, router.py)
    ↓
Device Instances (camera_service, thermal_device, etc.)
    ↓
BaseDevice (thread-safe access)
    ↓
Hardware (Stubbed - `implemented: false`)
```

## Environment Configuration

**Environment Variables:**
- `HOST` (default: "0.0.0.0")
- `PORT` (default: 8000)

**Launch Command:**
```bash
python3 main.py
```

**Server Details:**
- Framework: FastAPI 0.115.0+
- ASGI Server: Uvicorn
- URL: http://localhost:8000
