"""OpenFlexure Microscope OpenCV Camera.

This module defines a camera Thing that uses OpenCV's
``VideoCapture``.

See repository root for licensing information.
"""

from __future__ import annotations

import logging
from threading import Thread
from types import TracebackType
from typing import Literal, Optional, Self

import cv2
from PIL import Image

import labthings_fastapi as lt
from labthings_fastapi.types.numpy import NDArray

from openflexure_microscope_server.ui import PropertyControl, property_control_for

from . import BaseCamera, opencv_utils

LOGGER = logging.getLogger(__name__)


class OpenCVCamera(BaseCamera):
    """A Thing that provides and interface to an OpenCV Camera."""

    def __init__(self, thing_server_interface: lt.ThingServerInterface) -> None:
        """Iniatilise the thing storing the index of the camera to use.

        :param thing_server_interface: The thing server interface to be passed to to
            the parent class.
        """
        super().__init__(thing_server_interface)

        self.cameras: dict[str, int] = {}
        self._capture_thread: Optional[Thread] = None
        self._capture_enabled = False

    def __enter__(self) -> Self:
        """Start the capture thread when the Thing context manager is opened."""
        super().__enter__()
        all_camera_ids = opencv_utils.find_all_cameras()
        if not all_camera_ids:
            raise RuntimeError("No cameras available.")
        self.cameras = opencv_utils.identify_cameras(all_camera_ids)
        if self.camera_name not in self.cameras:
            if self.camera_name:
                self.logger.warning(f"{self.camera_name} not found.")
            self._camera_name = next(iter(self.cameras))

        self._start_stream()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        """Release the camera when the Thing context manager is closed.

        Before releasing the camera the capture thread is closed.
        """
        self._stop_stream()
        super().__exit__(exc_type, exc_value, traceback)

    _camera_name = ""

    @lt.setting
    def camera_name(self) -> str:
        """The name of the camera."""
        return self._camera_name

    @camera_name.setter
    def _set_camera_name(self, value: str) -> None:
        """Set the name of the camera."""
        if not self.cameras:
            # Don't try to validate if cameras dict is empty, just set the value.
            # As this is the startup behaviour, on __enter__ we check if the
            # initial camera is valid and that at least 1 camera exists.
            self._camera_name = value
            # Return so we don't try to start the stream
            return
        self._camera_name = value
        if value not in self.cameras:
            raise ValueError(f"{value} is not a valid camera name.")
        self._start_stream()

    def _start_stream(self) -> None:
        """Start the camera stream or restart if running."""
        if self.stream_active:
            self._stop_stream()
        self.cap = cv2.VideoCapture(
            self.cameras[self.camera_name], opencv_utils.BACKEND
        )
        self._capture_enabled = True
        self._capture_thread = Thread(target=self._capture_frames)
        self._capture_thread.start()

    def _stop_stream(self) -> None:
        """Stop the camera stream."""
        if self.stream_active:
            self._capture_enabled = False
            if self._capture_thread is not None:
                self._capture_thread.join()
        self.cap.release()

    @lt.property
    def stream_active(self) -> bool:
        """Whether the MJPEG stream is active."""
        if self._capture_enabled and self._capture_thread:
            return self._capture_thread.is_alive()
        return False

    def _capture_frames(self) -> None:
        while self._capture_enabled:
            ret, frame = self.cap.read()
            if not ret:
                LOGGER.error("Failed to capture frame from camera.")
                break
            jpeg = cv2.imencode(".jpg", frame)[1].tobytes()
            self.mjpeg_stream.add_frame(jpeg)
            jpeg_lores = cv2.imencode(".jpg", cv2.resize(frame, (320, 240)))[
                1
            ].tobytes()
            self.lores_mjpeg_stream.add_frame(jpeg_lores)

    @lt.action
    def discard_frames(self) -> None:
        """Discard frames so that the next frame captured is fresh."""
        self.capture_array()

    @lt.action
    def capture_array(
        self,
        stream_name: Literal["main", "lores", "raw", "full"] = "full",
        wait: Optional[float] = None,
    ) -> NDArray:
        """Acquire one image from the camera and return as an array.

        This function will produce a nested list containing an uncompressed RGB image.
        It's likely to be highly inefficient - raw and/or uncompressed captures using
        binary image formats will be added in due course.
        """
        if wait is not None:
            LOGGER.warning("OpenCV camera has no wait option. Use None.")
        LOGGER.warning(f"OpenCV camera doesn't respect {stream_name=}")
        ret, frame = self.cap.read()
        if not ret:
            raise RuntimeError("Failed to capture frame from camera.")
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def capture_image(
        self,
        stream_name: Literal["main", "lores", "full"] = "main",
        wait: Optional[float] = None,
    ) -> Image.Image:
        """Acquire one image from the camera and return as a PIL image.

        This function will produce a JPEG image.
        """
        if wait is not None:
            LOGGER.warning("OpenCV camera has no wait option. Use None.")
        LOGGER.warning(f"OpenCV camera doesn't respect {stream_name=}")
        return Image.fromarray(self.capture_array())

    @lt.property
    def manual_camera_settings(self) -> list[PropertyControl]:
        """The camera settings to expose as property controls in the settings panel.

        The options for the camera selector are populated with camera names once the
        server starts and available cameras are have been detected.
        """
        return [
            property_control_for(
                self,
                "camera_name",
                label="Camera",
                options={key: key for key in self.cameras},
            ),
        ]
