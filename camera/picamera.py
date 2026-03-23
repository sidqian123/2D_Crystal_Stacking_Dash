"""Submodule for interacting with a Raspberry Pi camera using the Picamera2 library.

The Picamera2 library uses LibCamera as the underlying camera stack. This gives us
some control of the GPU pipeline for the image.

The API documentation for PiCamera2 is unfortunately not in a standard auto-generated
website. For documentation of the PiCamera2 API there is a PDF called
"The Picamera2 Library" available at:
https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf

For information on the algorithms used to tune/calibrate the Raspberry Pi Camera see
the guide called "Raspberry Pi Camera Algorithm and Tuning Guide"
Available at:
https://datasheets.raspberrypi.com/camera/raspberry-pi-camera-guide.pdf
"""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from threading import RLock
from types import TracebackType
from typing import Annotated, Any, Iterator, Literal, Mapping, Optional, Self

import numpy as np
from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import Output
from PIL import Image
from pydantic import BaseModel, BeforeValidator

import labthings_fastapi as lt
from labthings_fastapi.exceptions import ServerNotRunningError
from labthings_fastapi.types.numpy import NDArray

from openflexure_microscope_server.things.background_detect import ChannelBlankError
from openflexure_microscope_server.ui import (
    ActionButton,
    PropertyControl,
    action_button_for,
    property_control_for,
)

from . import BaseCamera
from . import picamera_recalibrate_utils as recalibrate_utils
from . import picamera_tuning_file_utils as tf_utils

LOGGER = logging.getLogger(__name__)

SUPPORTED_CAMS_SENSOR_INFO = {
    "picamera_v2": recalibrate_utils.IMX219_SENSOR_INFO,
    "picamera_hq": recalibrate_utils.IMX477_SENSOR_INFO,
}


class PicameraModelError(RuntimeError):
    """There is a problem Picamera sensor model set by the configuration."""


class MissingCalibrationError(RuntimeError):
    """Picamera tuning file is missing or doesn't contain the requested algorithm."""


class PicameraStreamOutput(Output):
    """An Output class that sends frames to a stream."""

    def __init__(self, stream: lt.outputs.MJPEGStream) -> None:
        """Create an output that puts frames in an MJPEGStream.

        :param stream: The labthings MJPEGStream to send frames to.
        """
        Output.__init__(self)
        self.stream = stream

    def outputframe(
        self,
        frame: bytes,
        _keyframe: Optional[bool] = True,
        _timestamp: Optional[int] = None,
        _packet: Any = None,
        _audio: bool = False,
    ) -> None:
        """Add a frame to the stream's ringbuffer."""
        self.stream.add_frame(frame)


class SensorMode(BaseModel):
    """A Pydantic model holding all the information about a specific sensor mode.

    This data is as reported by the PiCamera2 module.
    """

    unpacked: str
    bit_depth: int
    size: tuple[int, int]
    fps: float
    crop_limits: tuple[int, int, int, int]
    exposure_limits: tuple[Optional[int], Optional[int], Optional[int]]
    format: Annotated[str, BeforeValidator(repr)]


class SensorModeSelector(BaseModel):
    """A Pydantic model holding the two values needed to select a PiCamera Sensor mode.

    These values are the output size and the bit depth.

    This is a Pydantic model so that it can sent by FastAPI
    """

    output_size: tuple[int, int]
    bit_depth: int


class StreamingPiCamera2(BaseCamera):
    """A Thing that provides and interface to the Raspberry Pi Camera.

    Currently the Thing only supports the PiCamera v2 board. This needs
    generalisation.
    """

    tuning: dict = lt.setting(default_factory=dict, readonly=True)
    """The Raspberry PiCamera Tuning File JSON."""

    def __init__(
        self,
        thing_server_interface: lt.ThingServerInterface,
        camera_num: int = 0,
        camera_board: str = "picamera_v2",
    ) -> None:
        """Initialise the camera with the given camera number.

        This makes no connection to the camera (except to get the default tuning file).

        :param camera_num: The number of the camera. This should generally be left as 0
            as most Raspberry Pi boards only support 1 camera.
        :param camera_board: The camera board used. Supported options are "picamera_v2"
           and "picamera_hq".
        """
        super().__init__(thing_server_interface)
        self._setting_save_in_progress = False
        self._camera_num = camera_num
        self._camera_board = camera_board
        if camera_board not in SUPPORTED_CAMS_SENSOR_INFO:
            raise PicameraModelError(
                f"The camera_board {camera_board} is not supported. Supported boards "
                f"are {SUPPORTED_CAMS_SENSOR_INFO.keys()}."
            )
        self._sensor_info = SUPPORTED_CAMS_SENSOR_INFO[camera_board]
        self._picamera_lock = RLock()
        self._picamera = None

        # Load the tuning file for the specified sensor mode.
        self.default_tuning = tf_utils.load_default_tuning(
            self._sensor_info.sensor_model
        )

        # Set tuning to default tuning. This will be overwritten when the Thing is
        # connected to the server if tuning is saved to disk.
        try:
            self.tuning = copy.deepcopy(self.default_tuning)
        except ServerNotRunningError as e:
            # This will throw an error after setting as we are not connected to
            # a server. But we know this, so we ignore the error as long as the
            # tuning data is set.
            if "version" not in self.tuning:
                raise RuntimeError("Tuning file could not be set.") from e

        # Also set the colour gains based on the tuning. Set to _colour_gains to not
        # trigger a ServerNotRunningError
        self._colour_gains = tf_utils.get_colour_gains_from_lst(self.tuning)

    stream_resolution: tuple[int, int] = lt.property(default=(820, 616))
    """Resolution to use for the MJPEG stream."""

    mjpeg_bitrate: Optional[int] = lt.property(default=100000000)
    """Bitrate for MJPEG stream (None for default)."""

    stream_active: bool = lt.property(default=False, readonly=True)
    """Whether the MJPEG stream is active."""

    def save_settings(self) -> None:
        """Override save_settings to ensure that camera properties don't recurse.

        This method is run by any Thing when a setting is saved. However, the
        method reads the setting. As reading the setting talks to the
        camera and calls save_settings if the value is not as expected, this could
        cause recursion. Also this means that saving one setting causes all others
        to be read each time.
        """
        try:
            self._setting_save_in_progress = True
            super().save_settings()
        finally:
            self._setting_save_in_progress = False

    @lt.property
    def calibration_required(self) -> bool:
        """Whether the camera needs calibrating."""
        # Check if the lens shading table is calibrated.
        return not tf_utils.lst_calibrated(self.tuning)

    ## Persistent controls! These are settings

    _analogue_gain: float = 1.0

    @lt.setting
    def analogue_gain(self) -> float:
        """The Analogue gain applied by the camera sensor."""
        if not self._setting_save_in_progress and self.streaming:
            with self._streaming_picamera() as cam:
                cam_value = cam.capture_metadata()["AnalogueGain"]
            if cam_value != self._analogue_gain:
                self._analogue_gain = cam_value
                self.save_settings()
        return self._analogue_gain

    @analogue_gain.setter
    def _set_analogue_gain(self, value: float) -> None:
        self._analogue_gain = value
        if self.streaming:
            with self._streaming_picamera() as cam:
                cam.set_controls({"AnalogueGain": value})

    _colour_gains: tuple[float, float] = (1.0, 1.0)

    @lt.setting
    def colour_gains(self) -> tuple[float, float]:
        """The red and blue colour gains, must be between 0.0 and 32.0."""
        if not self._setting_save_in_progress and self.streaming:
            with self._streaming_picamera() as cam:
                cam_value = cam.capture_metadata()["ColourGains"]
            if cam_value != self._colour_gains:
                self._colour_gains = cam_value
                self.save_settings()
        return self._colour_gains

    @colour_gains.setter
    def _set_colour_gains(self, value: tuple[float, float]) -> None:
        self._colour_gains = value
        if self.streaming:
            with self._streaming_picamera() as cam:
                cam.set_controls({"ColourGains": value})

    _exposure_time: int = 500

    @lt.setting
    def exposure_time(self) -> int:
        """The camera exposure time in microseconds.

        When setting this property the camera will adjust the set value
        to the nearest allowed value that is lower than the current setting.
        """
        if not self._setting_save_in_progress and self.streaming:
            with self._streaming_picamera() as cam:
                cam_value = cam.capture_metadata()["ExposureTime"]
            if cam_value != self._exposure_time:
                self._exposure_time = cam_value
                self.save_settings()
        return self._exposure_time

    @exposure_time.setter
    def _set_exposure_time(self, value: int) -> None:
        self._exposure_time = value
        if self.streaming:
            with self._streaming_picamera() as cam:
                # Note: This set a value 1 higher than requested as picamera2 always
                # sets a lower value than requested, even if the requested is allowed
                cam.set_controls({"ExposureTime": value + 1})

    def _get_persistent_controls(self) -> dict:
        if self.streaming:
            self.discard_frames()
        return {
            "AeEnable": False,
            "AnalogueGain": self.analogue_gain,
            "AwbEnable": False,
            "Brightness": 0,
            "ColourGains": self.colour_gains,
            "Contrast": 1,
            # Must also set plus 1 or the exposure drifts with start and stop stream.
            "ExposureTime": self.exposure_time + 1,
            "Saturation": 1,
            "Sharpness": 1,
        }

    _sensor_modes: Optional[list[SensorMode]] = None

    @lt.property
    def sensor_modes(self) -> list[SensorMode]:
        """All the available modes the current sensor supports."""
        if not self._sensor_modes:
            with self._streaming_picamera() as cam:
                self._sensor_modes = cam.sensor_modes
        return self._sensor_modes

    _sensor_mode: Optional[dict] = None

    @lt.property
    def sensor_mode(self) -> Optional[SensorModeSelector]:
        """The intended sensor mode of the camera."""
        if self._sensor_mode is None:
            return None
        return SensorModeSelector(**self._sensor_mode)

    @sensor_mode.setter
    def _set_sensor_mode(self, new_mode: Optional[SensorModeSelector | dict]) -> None:
        """Change the sensor mode used."""
        if new_mode is None:
            self._sensor_mode = None
        elif isinstance(new_mode, SensorModeSelector):
            self._sensor_mode = new_mode.model_dump()
        elif isinstance(new_mode, dict):
            self._sensor_mode = new_mode

        # By pausing the stream on when accessing, streaming_picamera
        # self._sensor_mode will be read and set when the stream restarts
        # after the context manager closes.
        with self._streaming_picamera(pause_stream=True):
            pass

    @lt.property
    def sensor_resolution(self) -> Optional[tuple[int, int]]:
        """The native resolution of the camera's sensor."""
        with self._streaming_picamera() as cam:
            return cam.sensor_resolution

    def _initialise_picamera(self, check_sensor_model: bool = False) -> None:
        """Acquire the picamera device and store it as ``self._picamera``.

        This duplicates logic in ``Picamera2.__init__`` to provide a tuning file that
        will be read when the camera system initialises.

        :param check_sensor_model: Set to true to check the sensor model is the
            expected sensor model. This is used on ``__enter__`` to confirm that the
            real camera matches the expected camera.

        :raises PicameraModelError: If check_sensor_model is True and the real
            camera sensor model doesn't match the expected sensor model.
        """
        with self._picamera_lock, tempfile.NamedTemporaryFile("w") as tuning_file:
            json.dump(self.tuning, tuning_file)
            tuning_file.flush()  # but leave it open as closing it will delete it
            os.environ["LIBCAMERA_RPI_TUNING_FILE"] = tuning_file.name

            if self._picamera is not None:
                LOGGER.info("Closing picamera object for reinitialisation")
                LOGGER.info(
                    "Camera object already exists, closing for reinitialisation"
                )
                self._picamera.close()
                LOGGER.info("Picamera closed, deleting picamera")
                del self._picamera
                recalibrate_utils.recreate_camera_manager()

            LOGGER.info("Creating new Picamera2 object")
            # Specify tuning file otherwise it will be overwritten with None.
            self._picamera = Picamera2(
                camera_num=self._camera_num,
                tuning=self.tuning,
            )
            if self._picamera is None:
                # Type narrow (error if failure)
                raise RuntimeError("Failed to start Picamera")
            if check_sensor_model:
                hw_sensor_model = self._picamera.camera_properties["Model"]
                if hw_sensor_model != self._sensor_info.sensor_model:
                    raise PicameraModelError(
                        f"Wrong Picamera model. Expecting {self._sensor_info.sensor_model}, "
                        f"but found {hw_sensor_model}."
                    )

    def __enter__(self) -> Self:
        """Start streaming when the Thing context manager is opened.

        This opens the picamera connection, initialises the camera, sets the
        sensor_modes property, and then starts the streams.
        """
        super().__enter__()
        self._initialise_picamera(check_sensor_model=True)
        # Sensor modes is a cached property read it once after initialising the camera
        _modes = self.sensor_modes
        self.start_streaming()
        return self

    @property
    def streaming(self) -> bool:
        """True if the camera is streaming."""
        return self._picamera is not None and self._picamera.started

    @contextmanager
    def _streaming_picamera(self, pause_stream: bool = False) -> Iterator[Picamera2]:
        """Lock access to picamera and return the underlying ``Picamera2`` instance.

        Optionally the stream can be paused to allow updating the camera settings.

        :param pause_stream: If False the ``Picamera2`` instance is simply yielded.
            If True:

                * Stop the MJPEG Stream
                * Yield the ``Picamera2`` instance for function calling the context manager to
                    make changes.
                * On closing of the context manager the stream will restart.
        """
        already_streaming = self.stream_active
        with self._picamera_lock:
            if pause_stream and already_streaming:
                self.stop_streaming(stop_web_stream=False)
            try:
                yield self._picamera
            finally:
                if pause_stream and already_streaming:
                    self.start_streaming()

    def __exit__(
        self,
        exc_type: type[BaseException],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        """Close the picamera connection when the Thing context manager is closed."""
        self.stop_streaming()
        with self._streaming_picamera() as cam:
            cam.close()
        del self._picamera
        super().__exit__(exc_type, exc_value, traceback)

    @lt.action
    def start_streaming(
        self, main_resolution: tuple[int, int] = (820, 616), buffer_count: int = 6
    ) -> None:
        """Start the MJPEG stream. This is where persistent controls are sent to camera.

        Sets the camera resolutions based on input parameters, and sets the low-res
        resolution to (320, 240). Note: (320, 240) is a standard from the Pi Camera
        manual.

        Create two streams:

        * ``lores_mjpeg_stream`` for autofocus at low-res resolution
        * ``mjpeg_stream`` for preview. This is the ``main_resolution`` if this is less
            than (1280, 960), or the low-res resolution if above. This allows for
            high resolution capture without streaming high resolution video.

        main_resolution: the resolution for the main configuration. Defaults to
        (820, 616), 1/4 sensor size.
        buffer_count: the number of frames to hold in the buffer. Higher uses more memory,
        lower may cause dropped frames. Value must be between 1 and 8, Defaults to 6.
        """
        controls = self._get_persistent_controls()
        # Buffer count can't be negative, zero, or too high.
        if buffer_count < 1 or buffer_count > 8:
            # 8 is slightly arbitrary. 6 is the PiCamera default for video
            # and the documentation only says that setting values higher gives
            # diminishing returns, and that the true maximum is hardware dependent
            raise ValueError(
                f"Can't set a buffer count of {buffer_count}. "
                "Buffer count must be an integer from 1-8"
            )
        with self._streaming_picamera() as picam:
            try:
                if picam.started:
                    picam.stop()
                    picam.stop_encoder()  # make sure there are no other encoders going
                stream_config = picam.create_video_configuration(
                    main={"size": main_resolution},
                    lores={"size": (320, 240), "format": "YUV420"},
                    sensor=self._sensor_mode,
                    controls=controls,
                )
                stream_config["buffer_count"] = buffer_count
                picam.configure(stream_config)
                LOGGER.info("Starting picamera MJPEG stream...")
                stream_name = "lores" if main_resolution[0] > 1280 else "main"
                picam.start_recording(
                    MJPEGEncoder(self.mjpeg_bitrate),
                    PicameraStreamOutput(self.mjpeg_stream),
                    name=stream_name,
                )
                picam.start_encoder(
                    MJPEGEncoder(100000000),
                    PicameraStreamOutput(self.lores_mjpeg_stream),
                    name="lores",
                )
            except Exception as e:
                LOGGER.exception("Error while starting preview: {e}")
                LOGGER.exception(e)
            else:
                self.stream_active = True
                LOGGER.debug(
                    "Started MJPEG stream at %s on port %s", self.stream_resolution, 1
                )

    @lt.action
    def stop_streaming(self, stop_web_stream: bool = True) -> None:
        """Stop the MJPEG stream."""
        with self._streaming_picamera() as picam:
            try:
                picam.stop_recording()  # This should also stop the extra lores encoder
            except Exception as e:
                LOGGER.info("Stopping recording failed")
                LOGGER.exception(e)
            else:
                self.stream_active = False
                if stop_web_stream:
                    self.mjpeg_stream.stop()
                    self.lores_mjpeg_stream.stop()
                LOGGER.info("Stopped MJPEG stream.")

            # Adding a sleep to prevent camera getting confused by rapid commands
            time.sleep(self._sensor_info.short_pause)

    @lt.action
    def discard_frames(self) -> None:
        """Discard frames so that the next frame captured is fresh."""
        with self._streaming_picamera() as cam:
            cam.capture_metadata()

    @contextmanager
    def _switch_to_still_capture_mode(self) -> Iterator[Picamera2]:
        """Get the picamera lock, pause stream and switch into still capture config.

        Restarts stream when complete.
        """
        with self._streaming_picamera(pause_stream=True) as cam:
            LOGGER.debug("Reconfiguring camera for full resolution capture")
            cam.configure(cam.create_still_configuration(sensor=self._sensor_mode))
            cam.start()
            time.sleep(self._sensor_info.short_pause)
            yield cam

    def capture_image(
        self,
        stream_name: Literal["main", "lores", "full"] = "main",
        wait: Optional[float] = 0.9,
    ) -> Image.Image:
        """Acquire one image from the camera and return it as a PIL Image.

        If the ``stream_name`` parameter is ``main`` or ``lores``, it will be captured
        from the main preview stream, or the low-res preview stream, respectively. This
        means the camera won't be reconfigured, and the stream will not pause (though
        it may miss one frame).

        If ``full`` resolution is requested, we will briefly pause the MJPEG stream and
        reconfigure the camera to capture a full resolution image. This will capture an
        image at the full resolution of the current sensor mode. If the current sensor
        mode bins or crops the image, this may not be the native resolution of the
        camera sensor.

        :param stream_name: (Optional) The PiCamera2 stream to use, should be one of
            ["main", "lores", "full"]. Default = "main". Note that "raw" images cannot
            be captured as PIL images. Use capture_array
        :param wait: (Optional, float) Set a timeout in seconds. Default = 0.9s,
            lower than the 1s timeout for the camera. This ensures that our code times
            out and returns before the camera times out. If None is set the default
            value of 0.9 will be used to prevent the possibility of the camera locking.

        :raises TimeoutError: if this time is exceeded during capture.
        """
        if wait is None:
            wait = 0.9
        if stream_name in ["main", "lores", "raw"]:
            with self._streaming_picamera() as cam:
                return cam.capture_image(stream_name, wait=wait)
        elif stream_name == "full":
            with self._switch_to_still_capture_mode() as cam:
                return cam.capture_image(name="main", wait=wait)
        else:
            raise ValueError(f'Unknown stream name "{stream_name}"')

    @lt.action
    def capture_array(
        self,
        stream_name: Literal["main", "lores", "raw", "full"] = "main",
        wait: Optional[float] = 0.9,
    ) -> NDArray:
        """Acquire one image from the camera and return as an array.

        This function will produce a nested list containing an uncompressed RGB image.
        It's likely to be highly inefficient - raw and/or uncompressed captures using
        binary image formats will be added in due course.

        :param stream_name: (Optional) The PiCamera2 stream to use, should be one of
            ["main", "lores", "raw", "full"]. Default = "main"
        :param wait: (Optional, float) Set a timeout in seconds. Default = 0.9s,
            lower than the 1s timeout for the camera. This ensures that our code times
            out and returns before the camera times out. If None is set the default
            value of 0.9 will be used to prevent the possibility of the camera locking.

        :raises TimeoutError: if this time is exceeded during capture.
        """
        if stream_name == "raw":
            # Raw cannot used capture_image.
            if wait is None:
                wait = 0.9
            with self._switch_to_still_capture_mode() as cam:
                return cam.capture_array(name="raw", wait=wait)
        # Note that internally the PiCamera creates a PIL image and then converts to
        # numpy with ``np.array(Image.open(io.BytesIO(self.make_buffer(name))))``.
        # As such we use capture_image to get an Image from the picamera and return
        # as array
        return np.array(self.capture_image(stream_name, wait))

    @lt.property
    def camera_configuration(self) -> Mapping:
        """The "configuration" dictionary of the picamera2 object.

        The "configuration" sets the resolution and format of the camera's streams.
        Together with the "tuning" it determines how the sensor is configured and
        how the data is processed.

        Note that the configuration may be modified when taking still images, and
        this property refers to whatever configuration is currently in force -
        usually the one used for the preview stream.
        """
        with self._streaming_picamera() as cam:
            return cam.camera_configuration()

    @lt.property
    def capture_metadata(self) -> dict:
        """Return the metadata from the camera."""
        with self._streaming_picamera() as cam:
            return cam.capture_metadata()

    @lt.action
    def auto_expose_from_minimum(
        self,
        target_white_level: Optional[int] = None,
        percentile: float = 99.9,
    ) -> None:
        """Adjust exposure until a the target white level is reached.

        Starting from the minimum exposure, gradually increase exposure until
        the image reaches the specified white level.

        :param target_white_level: Raw target white level, this should be an integer
            within the range set by the bit-depth of the camera sensor (10-bit for
            PiCamera v2, 12 Bit for Picamera HQ. If None the default will be used for
            the current sensor. This is approximately 40% saturated, but after gamma
            curve is applied, the pixel values will have a value around 200.
        :param percentile: The percentile to use instead of maximum. Default 99.9. When
            calculating the brightest pixel, a percentile is used rather than the
            maximum in order to be robust to a small number of noisy/bright pixels.
        """
        if target_white_level is None:
            target_white_level = self._sensor_info.default_target_white_level

        with self._streaming_picamera(pause_stream=True) as cam:
            recalibrate_utils.adjust_shutter_and_gain_from_raw(
                cam,
                self._sensor_info,
                target_white_level=target_white_level,
                percentile=percentile,
            )

    @lt.action
    def calibrate_lens_shading(self) -> None:
        """Take an image and use it for flat-field correction.

        This method requires an empty (i.e. bright) field of view. It will take
        a raw image and effectively divide every subsequent image by the current
        one. This uses the camera's "tuning" file to correct the preview and
        the processed images. It should not affect raw images.
        """
        with self._streaming_picamera(pause_stream=True) as cam:
            # Suppress lint warning that L, Cr, and Cb are not lowercase, as these are
            # the standard mathematical terms for:
            # luminance (L), red-difference chroma (Cr), and blue-difference chroma
            # (Cb).
            L, Cr, Cb = recalibrate_utils.lst_from_camera(cam, self._sensor_info)  # noqa: N806
            self.tuning = tf_utils.set_lst(
                self.tuning,
                luminance=L,
                cr=Cr,
                cb=Cb,
                colour_temp=tf_utils.CALIBRATED_COLOUR_TEMP,
            )

            # Re-initialise the picamera to reload the tuning file.
            self._initialise_picamera()

        self.colour_gains = tf_utils.get_colour_gains_from_lst(self.tuning)

    @lt.property
    def colour_correction_matrix(
        self,
    ) -> tuple[float, float, float, float, float, float, float, float, float]:
        """The ``colour_correction_matrix`` from the tuning file.

        This is broken out into its own property for convenience and compatibility with
        the micromanager API

        It is a 9 value tuple used to specify the 3x3 matrix that the GPU pipeline uses
        to convert from the camera R,G,B vector to the standard R,G,B.
        """
        return tuple(tf_utils.get_ccm(self.tuning))

    @colour_correction_matrix.setter  # type: ignore
    def colour_correction_matrix(
        self,
        value: tuple[float, float, float, float, float, float, float, float, float],
    ) -> None:
        self.tuning = tf_utils.set_ccm(self.tuning, value)

        if self._picamera is not None:
            with self._streaming_picamera(pause_stream=True):
                self._initialise_picamera()

    @lt.action
    def reset_ccm(self) -> None:
        """Overwrite the colour correction matrix in camera tuning with default values."""
        self.tuning = tf_utils.copy_algo_from_other_tuning(
            algo="rpi.ccm",
            base_tuning_file=self.tuning,
            copy_from=self.default_tuning,
        )

    @lt.property
    def gamma_correction(self) -> list[int]:
        """Return the gamma correction curve from the tuning file."""
        return tf_utils.get_gamma_curve(self.tuning)

    @lt.action
    def set_static_green_equalisation(self, offset: int = 65535) -> None:
        """Set the green equalisation to a static value.

        Green equalisation avoids the debayering algorithm becoming confused
        by the two green channels having different values, which is a problem
        when the chief ray angle isn't what the sensor was designed for, and
        that's the case in e.g. a microscope using camera module v2.

        A value of 0 here does nothing, a value of 65535 is maximum correction.
        """
        with self._streaming_picamera(pause_stream=True):
            self.tuning = tf_utils.set_static_geq(self.tuning, offset)
            self._initialise_picamera()

    @lt.action
    def set_ce_enable_to_off(self) -> None:
        """Set the contrast enhancement to disabled.

        Adaptive contrast enhancement modifies settings to adapt to each field
        of view, causing inconsistent settings when capturing.
        """
        with self._streaming_picamera(pause_stream=True):
            self.tuning = tf_utils.set_ce_to_disabled(self.tuning)
            self._initialise_picamera()

    @lt.action
    def full_auto_calibrate(self) -> None:
        """Perform a full auto-calibration.

        This function will call the other calibration actions in sequence:

        * ``flat_lens_shading`` to disable flat-field
        * ``auto_expose_from_minimum``
        * ``set_static_green_equalisation`` to set geq offset to max
        * ``calibrate_lens_shading`` (also sets colour gains for white balance)
        * ``set_background``
        """
        self.flat_lens_shading()
        self.auto_expose_from_minimum()
        self.set_static_green_equalisation()
        self.set_ce_enable_to_off()
        self.calibrate_lens_shading()
        if self.background_detector is not None:
            for _i in range(3):
                try:
                    time.sleep(self._sensor_info.long_pause)
                    self.set_background()
                    # Return if background is set
                    return
                except ChannelBlankError:
                    # If channel is blank, sleep a second and try again.
                    pass
        raise RuntimeError("Couldn't set background")

    @lt.property
    def primary_calibration_actions(self) -> list[ActionButton]:
        """The calibration actions for both calibration wizard and settings panel."""
        return [
            action_button_for(
                self,
                "full_auto_calibrate",
                submit_label="Full Auto-Calibrate",
                can_terminate=False,
                requires_confirmation=True,
                confirmation_message=(
                    "Start recalibration? This may take a while, and the microscope "
                    "will be locked during this time."
                ),
                notify_on_success=True,
                success_message="Finished recalibration.",
            ),
        ]

    @lt.property
    def secondary_calibration_actions(self) -> list[ActionButton]:
        """The calibration actions that appear only in settings panel."""
        return [
            action_button_for(
                self,
                "auto_expose_from_minimum",
                submit_label="Auto Gain & Shutter Speed",
                can_terminate=False,
                button_primary=False,
            ),
            action_button_for(
                self,
                "calibrate_lens_shading",
                submit_label="Auto Flat Field Correction",
                can_terminate=False,
                button_primary=False,
                requires_confirmation=True,
                confirmation_message=(
                    "Is the microscope looking at an evenly illuminated, empty field "
                    "of view? If not, the current image will show through in any "
                    "images captured afterwards."
                ),
            ),
            action_button_for(
                self,
                "flat_lens_shading",
                submit_label="Disable Flat Field Correction",
                can_terminate=False,
                button_primary=False,
            ),
            action_button_for(
                self,
                "flat_lens_shading_chrominance",
                submit_label="Disable Flat Field Chrominance",
                can_terminate=False,
                button_primary=False,
            ),
            action_button_for(
                self,
                "reset_lens_shading",
                submit_label="Reset Flat Field Correction",
                can_terminate=False,
                button_primary=False,
            ),
        ]

    @lt.property
    def manual_camera_settings(self) -> list[PropertyControl]:
        """The camera settings to expose as property controls in the settings panel."""
        return [
            property_control_for(
                self,
                "exposure_time",
                label="Exposure Time (0-33251)",
                read_back=True,
                read_back_delay=1000,
            ),
            property_control_for(
                self,
                "analogue_gain",
                label="Analogue Gain",
                read_back=True,
                read_back_delay=1000,
            ),
            property_control_for(
                self,
                "colour_gains",
                label="Colour Gains",
                read_back=True,
                read_back_delay=1000,
            ),
        ]

    @lt.property
    def lens_shading_tables(self) -> Optional[tf_utils.LensShadingModel]:
        """The current lens shading (i.e. flat-field correction).

        Return the current lens shading correction, as three 2D lists each with
        dimensions 16x12.

        The colour temperature is returned. If the colour temperature us 5000 then this
        means the lens shading tables have been calibrated (with our illumination which
        has a 5000k colour temperature). Other numbers are set when flatening or
        resetting the table.
        """
        return tf_utils.get_lst(self.tuning)

    @lt.action
    def flat_lens_shading(self) -> None:
        """Disable flat-field correction.

        This method will set a completely flat lens shading table. It is not the
        same as the default behaviour, which is to use an adaptive lens shading
        table.

        This flat table is used to take an image with no lens shading so that the
        correct lens shading table can be calibrated.
        """
        with self._streaming_picamera(pause_stream=True):
            self.tuning = tf_utils.flatten_lst(self.tuning)
            self._initialise_picamera()

    @lt.action
    def flat_lens_shading_chrominance(self) -> None:
        """Disable flat-field correction for colour only.

        This method will set the chrominance of the lens shading table to be
        flat, i.e. we'll correct vignetting of intensity, but not any change in
        colour across the image.
        """
        with self._streaming_picamera(pause_stream=True):
            self.tuning = tf_utils.flatten_lst(self.tuning, keep_luminance=True)
            self._initialise_picamera()

    @lt.action
    def reset_lens_shading(self) -> None:
        """Revert to default lens shading settings.

        This method will restore the default "adaptive" lens shading method used
        by the Raspberry Pi camera.
        """
        with self._streaming_picamera(pause_stream=True):
            self.tuning = tf_utils.copy_algo_from_other_tuning(
                algo="rpi.alsc",
                base_tuning_file=self.tuning,
                copy_from=self.default_tuning,
            )
            self._initialise_picamera()

    @property
    def thing_state(self) -> Mapping[str, Any]:
        """Update generic camera metadata with Picamera-specific data."""
        state = dict(super().thing_state)
        state["camera_board"] = self._camera_board
        state["tuning"] = {
            "exposure_time": self.exposure_time,
            "colour_gains": self.colour_gains,
            "analogue_gain": self.analogue_gain,
            "gamma_correction": self.gamma_correction,
        }
        return state
