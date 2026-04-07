import io
import threading
from typing import Any, Dict, Optional, Tuple

import numpy as np
from PIL import Image

from devices.camera import CameraDevice


class PylonCameraBackend(CameraDevice):
    """Simple Basler pypylon camera backend with MJPEG output."""

    def __init__(self, camera_index: int = 0) -> None:
        super().__init__()
        self.camera_index = camera_index
        self.camera = None
        self.converter = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._output = None
        self._lock = threading.Lock()
        self._metadata: Dict[str, Any] = {
            "ExposureTime": None,
            "AnalogueGain": None,
            "ColourGains": None,
            "AwbState": None,
            "AeFlickerMode": None,
        }

    def _set_if_writable(self, node_name: str, value: Any) -> None:
        if self.camera is None:
            return
        node = getattr(self.camera, node_name, None)
        if node is None:
            return
        try:
            if hasattr(node, "IsWritable") and not node.IsWritable():
                return
            if hasattr(node, "SetValue"):
                node.SetValue(value)
        except Exception:
            pass

    def _set_stream_size(self, stream_size: Tuple[int, int]) -> None:
        if self.camera is None:
            return

        width, height = int(stream_size[0]), int(stream_size[1])

        try:
            if self.camera.Width.IsWritable():
                width = max(int(self.camera.Width.GetMin()), min(width, int(self.camera.Width.GetMax())))
                self.camera.Width.SetValue(width)
            if self.camera.Height.IsWritable():
                height = max(int(self.camera.Height.GetMin()), min(height, int(self.camera.Height.GetMax())))
                self.camera.Height.SetValue(height)
        except Exception:
            pass

    def _set_framerate(self, stream_fps: float) -> None:
        if self.camera is None:
            return
        try:
            if hasattr(self.camera, "AcquisitionFrameRateEnable") and self.camera.AcquisitionFrameRateEnable.IsWritable():
                self.camera.AcquisitionFrameRateEnable.SetValue(True)
            if hasattr(self.camera, "AcquisitionFrameRate") and self.camera.AcquisitionFrameRate.IsWritable():
                fps = max(float(self.camera.AcquisitionFrameRate.GetMin()), min(float(stream_fps), float(self.camera.AcquisitionFrameRate.GetMax())))
                self.camera.AcquisitionFrameRate.SetValue(fps)
        except Exception:
            pass

    def start(self, output, stream_size: Tuple[int, int], stream_fps: float) -> None:
        from pypylon import pylon

        factory = pylon.TlFactory.GetInstance()
        devices = factory.EnumerateDevices()
        if not devices:
            raise RuntimeError("No pypylon camera detected")

        index = max(0, min(self.camera_index, len(devices) - 1))
        self.camera = pylon.InstantCamera(factory.CreateDevice(devices[index]))
        self.camera.Open()

        self._set_stream_size(stream_size)
        self._set_framerate(stream_fps)

        self.converter = pylon.ImageFormatConverter()
        self.converter.OutputPixelFormat = pylon.PixelType_BGR8packed
        self.converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned

        self.camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
        self._running = True
        self._output = output

        with self._lock:
            self._metadata.update(
                {
                    "DeviceModel": self.camera.GetDeviceInfo().GetModelName(),
                    "DeviceSerial": self.camera.GetDeviceInfo().GetSerialNumber(),
                }
            )

        self._thread = threading.Thread(target=self._grab_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None

        if self.camera is not None:
            try:
                if self.camera.IsGrabbing():
                    self.camera.StopGrabbing()
            except Exception:
                pass
            try:
                if self.camera.IsOpen():
                    self.camera.Close()
            except Exception:
                pass
        self.camera = None

    def _grab_loop(self) -> None:
        while self._running and self.camera is not None and self.camera.IsGrabbing():
            result = self.camera.RetrieveResult(1000, 1)  # TimeoutHandling_Return
            if result is None:
                continue
            try:
                if not result.GrabSucceeded():
                    continue
                converted = self.converter.Convert(result)
                frame = converted.GetArray()
                if frame is None:
                    continue

                # Convert BGR array to RGB before JPEG encoding.
                rgb = np.ascontiguousarray(frame[..., ::-1])
                image = Image.fromarray(rgb)
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=85)
                self._output.write(buffer.getvalue())

                with self._lock:
                    try:
                        self._metadata["ExposureTime"] = float(self.camera.ExposureTime.GetValue())
                    except Exception:
                        pass
                    try:
                        self._metadata["AnalogueGain"] = float(self.camera.Gain.GetValue())
                    except Exception:
                        pass
            finally:
                result.Release()

    def capture_metadata(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._metadata)

    def set_awb(self, enabled: bool) -> None:
        if self.camera is None:
            return
        self._set_if_writable("BalanceWhiteAuto", "Continuous" if enabled else "Off")

    def set_ae(self, enabled: bool) -> None:
        if self.camera is None:
            return
        self._set_if_writable("ExposureAuto", "Continuous" if enabled else "Off")

    def set_gains(self, r: float, b: float) -> None:
        # Most Basler cameras expose a single Gain control, so we use the average.
        gain = float(max(0.0, (float(r) + float(b)) / 2.0))
        self._set_if_writable("Gain", gain)
        with self._lock:
            self._metadata["ColourGains"] = (float(r), float(b))

    def set_image_controls(self, brightness: float, contrast: float, saturation: float) -> None:
        # Kept for API compatibility. Most pypylon cameras do not expose these controls uniformly.
        _ = (brightness, contrast, saturation)

    def set_exposure(self, exposure_us: int) -> None:
        self.set_ae(False)
        self._set_if_writable("ExposureTime", float(exposure_us))

    def set_flicker(self, mode: int) -> None:
        with self._lock:
            self._metadata["AeFlickerMode"] = int(mode)

    def reset_auto(self) -> None:
        self.set_ae(True)
        self.set_awb(True)
