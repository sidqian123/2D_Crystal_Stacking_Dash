import io
import json
import logging
import os
import sys
import time
from pathlib import Path
from threading import Condition
from typing import Any, Dict, Generator, Optional

from devices.camera import CameraDevice

BASE_DIR = Path(__file__).resolve().parent.parent
CAMERA_DIR = BASE_DIR / "camera"
TUNING_DIR = CAMERA_DIR / "tuning_files" / "vc4"

if str(CAMERA_DIR) not in sys.path:
    sys.path.insert(0, str(CAMERA_DIR))


# Default sensor info for non-camera systems
class DefaultSensorInfo:
    sensor_model = "imx219"
    default_target_white_level = 200


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


class CameraService(CameraDevice):
    def __init__(self):
        super().__init__()
        self.picam2 = None
        self.pylon = None
        self.output = StreamingOutput()

        self.sensor_info = DefaultSensorInfo()
        self.camera_backend = os.getenv("CAMERA_BACKEND", "picamera2").strip().lower()
        self.camera_num = int(os.getenv("CAMERA_NUM", "0"))

        self.current_image_controls = {
            "Brightness": 0.0,
            "Contrast": 1.0,
            "Saturation": 1.0,
        }

    def _read_stream_config(self) -> tuple[tuple[int, int], float]:
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

        return stream_size, stream_fps


    def _detect_sensor_model(self, cam_num: int) -> str:
        from picamera2 import Picamera2

        probe = Picamera2(camera_num=cam_num)
        try:
            return str(probe.camera_properties.get("Model", "")).lower().strip()
        finally:
            probe.close()

    def _load_tuning(self, sensor_model: str) -> Optional[dict]:
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

    def start(self) -> None:
        if self.camera_backend == "pypylon":
            try:
                from pylon_camera import PylonCameraBackend

                stream_size, stream_fps = self._read_stream_config()
                self.pylon = PylonCameraBackend(camera_index=self.camera_num)
                self.pylon.start(self.output, stream_size=stream_size, stream_fps=stream_fps)
                self.set_camera_available(True, "")
                return
            except Exception as exc:
                error_msg = f"Pypylon unavailable: {exc}"
                self.set_camera_available(False, error_msg)
                logging.warning(error_msg)
                self.pylon = None
                return

        try:
            from picamera2 import Picamera2
            from picamera2.encoders import MJPEGEncoder
            from picamera2.outputs import FileOutput
            import picamera_recalibrate_utils as ru
        except Exception as exc:
            error_msg = f"Picamera2 unavailable: {exc}"
            self.set_camera_available(False, error_msg)
            logging.warning(error_msg)
            return

        try:
            detected_model = os.getenv("CAMERA_SENSOR_MODEL", "").strip().lower() or self._detect_sensor_model(self.camera_num)
            if detected_model == "imx477":
                self.sensor_info = ru.IMX477_SENSOR_INFO
            else:
                self.sensor_info = ru.IMX219_SENSOR_INFO

            tuning = self._load_tuning(self.sensor_info.sensor_model)

            if tuning is None:
                self.picam2 = Picamera2(camera_num=self.camera_num)
            else:
                self.picam2 = Picamera2(camera_num=self.camera_num, tuning=tuning)

            stream_size, stream_fps = self._read_stream_config()

            config = self.picam2.create_video_configuration(
                main={"size": stream_size},
                controls={
                    "FrameRate": stream_fps,
                    "Brightness": self.current_image_controls["Brightness"],
                    "Contrast": self.current_image_controls["Contrast"],
                    "Saturation": self.current_image_controls["Saturation"],
                },
            )
            self.picam2.configure(config)
            self.picam2.start_recording(MJPEGEncoder(), FileOutput(self.output))

            # Start with auto exposure + auto white balance enabled.
            self.reset_auto()
            self.set_camera_available(True, "")
        except Exception as exc:
            error_msg = f"Camera unavailable: {exc}"
            self.set_camera_available(False, error_msg)
            logging.warning(error_msg)
            self.picam2 = None

    def stop(self) -> None:
        if self.pylon is not None:
            self.pylon.stop()
            self.pylon = None
        if self.picam2 is not None:
            self.picam2.stop_recording()

    def _capture_metadata(self) -> Dict[str, Any]:
        if self.pylon is not None:
            return self.pylon.capture_metadata()
        if self.picam2 is None:
            return {}
        with self.lock:
            return self.picam2.capture_metadata()

    def reset_auto(self) -> None:
        if self.pylon is not None:
            self.pylon.reset_auto()
            return
        if self.picam2 is None:
            return
        with self.lock:
            self.picam2.set_controls({"AeEnable": True, "AwbEnable": True})

    def set_awb(self, enabled: bool) -> None:
        if self.pylon is not None:
            self.pylon.set_awb(enabled)
            return
        if self.picam2 is None:
            return
        with self.lock:
            self.picam2.set_controls({"AwbEnable": bool(enabled)})

    def set_ae(self, enabled: bool) -> None:
        if self.pylon is not None:
            self.pylon.set_ae(enabled)
            return
        if self.picam2 is None:
            return
        with self.lock:
            self.picam2.set_controls({"AeEnable": bool(enabled)})

    def set_gains(self, r: float, b: float) -> None:
        if self.pylon is not None:
            self.pylon.set_gains(r, b)
            return
        if self.picam2 is None:
            return
        with self.lock:
            self.picam2.set_controls({"AwbEnable": False, "ColourGains": (float(r), float(b))})

    def set_image_controls(self, brightness: float, contrast: float, saturation: float) -> None:
        self.current_image_controls["Brightness"] = float(brightness)
        self.current_image_controls["Contrast"] = float(contrast)
        self.current_image_controls["Saturation"] = float(saturation)
        if self.pylon is not None:
            self.pylon.set_image_controls(brightness, contrast, saturation)
            return
        if self.picam2 is None:
            return
        with self.lock:
            self.picam2.set_controls(self.current_image_controls)

    def set_exposure(self, exposure_us: int) -> None:
        if self.pylon is not None:
            self.pylon.set_exposure(exposure_us)
            return
        if self.picam2 is None:
            return
        with self.lock:
            self.picam2.set_controls({"AeEnable": False, "ExposureTime": int(exposure_us)})

    def set_flicker(self, mode: int) -> None:
        mode = int(mode)
        if mode not in (0, 1):
            mode = 1
        if self.pylon is not None:
            self.pylon.set_flicker(mode)
            return
        if self.picam2 is None:
            return
        with self.lock:
            self.picam2.set_controls({"AeFlickerMode": mode})

    def calibrate_camera(self, warmup_seconds: float = 1.5):
        if self.pylon is not None:
            return None
        if self.picam2 is None:
            return None
        self.reset_auto()
        time.sleep(max(0.0, warmup_seconds))
        md = self._capture_metadata()
        gains = md.get("ColourGains", None)

        with self.lock:
            if gains:
                self.picam2.set_controls({"AwbEnable": False, "ColourGains": gains})

        logging.warning("CALIBRATE locked gains=%s", gains)
        return gains

    def flat_field_calibrate(self) -> Dict[str, Any]:
        if self.pylon is not None:
            return {"ok": False, "error": "Flat-field calibration is not supported for pypylon backend"}
        if self.picam2 is None:
            _, error = self.get_camera_info()
            return {"ok": False, "error": error or "Camera unavailable"}
        
        try:
            import picamera_recalibrate_utils as ru
        except Exception as exc:
            return {"ok": False, "error": f"Calibration unavailable: {exc}"}
        
        with self.lock:
            ru.adjust_shutter_and_gain_from_raw(
                self.picam2,
                self.sensor_info,
                target_white_level=self.sensor_info.default_target_white_level,
            )
            ru.adjust_white_balance_from_raw(self.picam2, self.sensor_info)
            lst = ru.lst_from_camera(self.picam2, self.sensor_info)
            self.picam2.lens_shading_table = lst

        md = self._capture_metadata()
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

    def status_payload(self) -> Dict[str, Any]:
        md = self._capture_metadata()
        available, error = self.get_camera_info()
        return {
            "ColourGains": md.get("ColourGains", None),
            "ExposureTime": md.get("ExposureTime", None),
            "AnalogueGain": md.get("AnalogueGain", None),
            "AwbState": md.get("AwbState", None),
            "AeFlickerMode": md.get("AeFlickerMode", None),
            "Brightness": self.current_image_controls["Brightness"],
            "Contrast": self.current_image_controls["Contrast"],
            "Saturation": self.current_image_controls["Saturation"],
            "CameraAvailable": available,
            "CameraError": error,
        }

    def mjpeg_stream(self) -> Generator[bytes, None, None]:
        last_frame_id = 0
        while True:
            with self.output.condition:
                self.output.condition.wait_for(
                    lambda: self.output.frame is not None and self.output.frame_id != last_frame_id
                )
                frame = self.output.frame
                last_frame_id = self.output.frame_id

            yield b"--FRAME\r\n"
            yield b"Content-Type: image/jpeg\r\n"
            yield f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
            yield frame
            yield b"\r\n"
