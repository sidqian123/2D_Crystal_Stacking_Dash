import logging
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.camera_service import CameraService
from nanopositioner.router import router as nanopositioner_router
from thermal_plate.router import router as thermal_router
from vacuum.router import router as vacuum_router

BASE_DIR = Path(__file__).resolve().parent.parent

app = FastAPI(title="Alignment Microscope")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(nanopositioner_router)
app.include_router(thermal_router)
app.include_router(vacuum_router)

camera_service = CameraService()


@app.on_event("startup")
def on_startup() -> None:
    camera_service.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    camera_service.stop()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")


@app.get("/index.html", response_class=HTMLResponse)
def index_alias() -> str:
    return index()


@app.get("/api/awb", response_class=PlainTextResponse)
def set_awb(enabled: str = Query("1")) -> str:
    camera_service.set_awb(enabled.strip().lower() in ("1", "true", "on", "yes"))
    return "OK\n"


@app.get("/api/ae", response_class=PlainTextResponse)
def set_ae(enabled: str = Query("1")) -> str:
    camera_service.set_ae(enabled.strip().lower() in ("1", "true", "on", "yes"))
    return "OK\n"


@app.get("/api/reset_auto", response_class=PlainTextResponse)
def reset_auto() -> str:
    camera_service.reset_auto()
    return "Auto reset: AE+AWB enabled\n"


@app.get("/api/set_gains", response_class=PlainTextResponse)
def set_gains(r: float = Query(1.5), b: float = Query(1.5)) -> str:
    camera_service.set_gains(r, b)
    return f"Set gains: r={r:.2f} b={b:.2f}\n"


@app.get("/api/set_image", response_class=PlainTextResponse)
def set_image(
    brightness: float = Query(0.0),
    contrast: float = Query(1.0),
    saturation: float = Query(1.0),
) -> str:
    camera_service.set_image_controls(brightness, contrast, saturation)
    return (
        "Set image controls: "
        f"brightness={brightness:.2f}, contrast={contrast:.2f}, saturation={saturation:.2f}\n"
    )


@app.get("/api/set_exposure", response_class=PlainTextResponse)
def set_exposure(exposure: int = Query(8333)) -> str:
    camera_service.set_exposure(exposure)
    return f"Set exposure: {exposure} us\n"


@app.get("/api/set_flicker", response_class=PlainTextResponse)
def set_flicker(mode: int = Query(1)) -> str:
    camera_service.set_flicker(mode)
    return f"Set flicker mode: {mode}\n"


@app.get("/api/status")
def status() -> dict:
    return camera_service.status_payload()


@app.get("/api/calibrate", response_class=PlainTextResponse)
def calibrate() -> str:
    gains = camera_service.calibrate_camera(warmup_seconds=1.5)
    return f"Calibrated. Locked gains={gains}\n"


@app.get("/api/flat_field_calibrate")
def flat_field_calibrate() -> dict:
    try:
        return camera_service.flat_field_calibrate()
    except Exception as exc:
        logging.exception("Flat-field calibration failed")
        return {"ok": False, "error": str(exc)}


@app.get("/stream.mjpg")
def stream_mjpg():
    if not camera_service.camera_available:
        msg = camera_service.camera_error or "Camera not connected"
        return PlainTextResponse(f"Camera unavailable: {msg}\n", status_code=503)
    return StreamingResponse(
        camera_service.mjpeg_stream(),
        media_type="multipart/x-mixed-replace; boundary=FRAME",
        headers={"Cache-Control": "no-cache, private", "Pragma": "no-cache"},
    )
