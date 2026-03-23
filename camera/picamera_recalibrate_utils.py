"""Functions to set up a Raspberry Pi Camera (v2 and HQ) for scientific use.

This module provides slower, simpler functions to set the
gain, exposure, and white balance of a Raspberry Pi camera, using
the ``picamera2`` Python library.  It's mostly used by the OpenFlexure
Microscope, though it deliberately has no hard dependencies on
said software, so that it's useful on its own.

There are three main calibration steps:

* Setting exposure time and gain to get a reasonably bright
  image.
* Fixing the white balance to get a neutral image
* Taking a uniform white image and using it to calibrate
  the Lens Shading Table

The most reliable way to do this, avoiding any issues relating
to "memory" or nonlinearities in the camera's image processing
pipeline, is to use raw images.  This is quite slow, but very
reliable.  The three steps above can be accomplished by:

.. code-block:: python

    picamera = picamera2.Picamera2()
    sensor_info = IMX219_SENSOR_INFO

    adjust_shutter_and_gain_from_raw(
        picamera,
        sensor_info,
        target_white_level=sensor_info.default_target_white_level,
    )
    adjust_white_balance_from_raw(picamera, sensor_info)
    lst = lst_from_camera(picamera, sensor_info)
    picamera.lens_shading_table = lst

"""

# Disable N806 & 803, which checks that all variables and args are lowercase.
# This is due to the number of matrix calculations and colour channel
# calculations that are clearer using the standard R, G, B, or L, Cr, Cb terms.
# ruff: noqa: N806 N803

from __future__ import annotations

import gc
import logging
import time
from typing import List, Tuple

import numpy as np
import picamera2
from picamera2 import Picamera2
from pydantic import BaseModel

LOGGER = logging.getLogger(__name__)


class SensorInfo(BaseModel):
    """Information about the sensor used for calibration and property setting."""

    sensor_model: str
    """The model of the sensor, as specified by the Picamera2 library."""

    unpacked_pixel_format: str
    """The format of the unpacked pixels."""

    bit_depth: int
    """The bit depth of each pixel."""

    blacklevel: int
    """The sensor black level."""

    default_target_white_level: int
    """The default target white level during exposure setting."""

    short_pause: float
    """The time to pause for actions that update quickly."""

    long_pause: float
    """Time to pause for actions that are known to update slowly."""


IMX219_SENSOR_INFO = SensorInfo(
    sensor_model="imx219",
    unpacked_pixel_format="SBGGR10",
    bit_depth=10,
    blacklevel=64,
    default_target_white_level=400,
    short_pause=0.2,
    long_pause=0.5,
)

IMX477_SENSOR_INFO = SensorInfo(
    sensor_model="imx477",
    unpacked_pixel_format="SBGGR12",
    bit_depth=12,
    blacklevel=256,
    default_target_white_level=1600,
    short_pause=0.2,
    long_pause=1.0,
)


LensShadingTables = tuple[np.ndarray, np.ndarray, np.ndarray]


def adjust_shutter_and_gain_from_raw(
    camera: Picamera2,
    sensor_info: SensorInfo,
    target_white_level: int,
    max_iterations: int = 20,
    tolerance: float = 0.05,
    percentile: float = 99.9,
) -> float:
    """Adjust exposure and analog gain based on raw images.

    This routine is slow but effective.  It uses raw images, so we
    are not affected by white balance or digital gain.

    :param camera: A Picamera2 object.
    :param target_white_level: The raw value we aim for, the raw value of the brightest
        pixels should be approximately this bright. The value to set depends on the
        sensor bit depth. We recommend values of 400 for 10-bit sensors and 1600 for
        12-bit sensors. This is about 40% of saturated once the blacklevel is
        subtracted. The maximum possible value depends on the sensor bit depth, the
        sensor blacklevel and the tolerance argument. While this only uses 40% of the
        sensor range, after gamma this corresponds to pixel value ~200.
    :param max_iterations: We will terminate once we perform this many iterations,
        whether or not we converge.  More than 10 shouldn't happen.
    :param tolerance: How close to the target value we consider "done".  Expressed as a
        fraction of the ``target_white_level`` so 0.05 means +/- 5%
    :param percentile: Rather then use the maximum value for each channel, we calculate
        a percentile.  This makes us robust to single pixels that are bright/noisy.
        99.9% still picks the top of the brightness range, but seems much more reliable
        than just ``np.max()``.

    """
    # Calculate the maximum possible pixel value once blacklevel is subtracted.
    max_level = 2**sensor_info.bit_depth - 1 - sensor_info.blacklevel
    if target_white_level * (tolerance + 1) >= max_level:
        raise ValueError(
            "The target level is too high - a saturated image would be "
            "considered successful.  target_white_level * (tolerance + 1) "
            f"must be less than {max_level}."
        )

    config = camera.create_still_configuration(
        raw={"format": sensor_info.unpacked_pixel_format}
    )
    camera.configure(config)
    camera.start()
    _set_minimum_exposure(camera, sensor_info)

    # We start with very low exposure settings and work up
    # until either the brightness is high enough, or we can't increase the
    # shutter speed any more.
    iterations = 0
    while iterations < max_iterations:
        test = _test_exposure_settings(camera, percentile)
        if _check_convergence(test, target_white_level, tolerance):
            break
        iterations += 1

        # Adjust shutter speed so that the brightness approximates the target
        # NB we put a maximum of 8 on this, to stop it increasing too quickly.
        new_time = int(test.exposure_time * min(target_white_level / test.level, 8))
        camera.controls.ExposureTime = new_time
        camera.controls.AeEnable = False
        time.sleep(sensor_info.long_pause)

        # Check whether the shutter speed is still going up - if not, we've hit a maximum
        if camera.capture_metadata()["ExposureTime"] == test.exposure_time:
            LOGGER.info(f"Shutter speed has maxed out at {test.exposure_time}")
            break

    # Now, if we've not converged, increase gain until we converge or run out of options.
    while iterations < max_iterations:
        test = _test_exposure_settings(camera, percentile)
        if _check_convergence(test, target_white_level, tolerance):
            break
        iterations += 1

        # Adjust gain to make the white level hit the target, again with a maximum
        camera.controls.AnalogueGain = test.analog_gain * min(
            target_white_level / test.level, 2
        )
        time.sleep(sensor_info.long_pause)

        # Check the gain is still changing - if not, we have probably hit the maximum
        if camera.capture_metadata()["AnalogueGain"] == test.analog_gain:
            LOGGER.info(f"Gain has maxed out at {test.analog_gain}")
            break

    if _check_convergence(test, target_white_level, tolerance):
        LOGGER.info(f"Brightness has converged to within {tolerance * 100:.0f}%.")
    else:
        LOGGER.warning(
            f"Failed to reach target brightness of {target_white_level}."
            f"Brightness reached {test.level} after {iterations} iterations."
        )

    return test.level


def lst_from_camera(camera: Picamera2, sensor_info: SensorInfo) -> LensShadingTables:
    """Acquire a raw image and use it to calculate a lens shading table."""
    channels = _raw_channels_from_camera(camera, sensor_info)
    return _lst_from_channels(channels, sensor_info.blacklevel)


def recreate_camera_manager() -> None:
    """Delete and recreate the camera manager.

    This is necessary to ensure the tuning file is re-read.
    """
    del Picamera2._cm
    gc.collect()
    Picamera2._cm = picamera2.picamera2.CameraManager()


class _ExposureTest(BaseModel):
    """Record the results of testing the camera's current exposure settings."""

    level: int
    exposure_time: int
    analog_gain: float


def _set_minimum_exposure(camera: Picamera2, sensor_info: SensorInfo) -> None:
    """Enable manual exposure, with low gain and shutter speed.

    Set exposure mode to manual, analog and digital gain to 1, and
    shutter speed to the minimum (8us for Pi Camera v2)

    Note ISO is left at auto, because this is needed for the gains
    to be set correctly.
    """
    # Disable Automatic exposure and gain algorithm (AeEnable), and set analogue
    # gain and exposure time.
    # Setting the shutter speed to 1us will result in it being set
    # to the minimum possible, which is ~8us for PiCamera v2
    camera.set_controls({"AeEnable": False, "AnalogueGain": 1, "ExposureTime": 1})
    time.sleep(sensor_info.long_pause)


def _test_exposure_settings(camera: Picamera2, percentile: float) -> _ExposureTest:
    """Evaluate current exposure settings using a raw image.

    CAMERA SHOULD BE STARTED!

    We will acquire a raw image and calculate the given percentile
    of the pixel values.  We return a dictionary containing the
    percentile (which will be compared to the target), as well as
    the camera's shutter and gain values.
    """
    camera.capture_array("raw")  # controls might not be updated for the first frame?
    max_brightness = np.percentile(
        _channels_from_bayer_array(camera.capture_array("raw")),
        percentile,
    )
    # The reported brightness can, theoretically, be negative or zero
    # because of black level compensation.  The line below forces a
    # minimum value of 1 which will keep things well-behaved!
    if max_brightness < 1:
        LOGGER.warning(
            f"Measured brightness of {max_brightness}. "
            "This should normally be >= 1, and may indicate the "
            "camera's black level compensation has gone wrong."
        )
        max_brightness = 1
    metadata = camera.capture_metadata()
    result = _ExposureTest(
        level=max_brightness,
        exposure_time=int(metadata["ExposureTime"]),
        analog_gain=float(metadata["AnalogueGain"]),
    )
    LOGGER.info(f"{result.model_dump()}")
    return result


def _check_convergence(test: _ExposureTest, target: int, tolerance: float) -> bool:
    """Check whether the brightness is within the specified target range."""
    return abs(test.level - target) < target * tolerance


def _channels_from_bayer_array(bayer_array: np.ndarray) -> np.ndarray:
    """Given the 'array' from a PiBayerArray, return the 4 channels."""
    bayer_pattern: List[Tuple[int, int]] = [(0, 0), (0, 1), (1, 0), (1, 1)]
    bayer_array = bayer_array.view(np.uint16)
    channels_shape: Tuple[int, int, int] = (
        4,
        bayer_array.shape[0] // 2,
        bayer_array.shape[1] // 2,
    )
    channels: np.ndarray = np.zeros(channels_shape, dtype=bayer_array.dtype)
    for i, offset in enumerate(bayer_pattern):
        # We simplify life by dealing with only one channel at a time.
        channels[i, :, :] = bayer_array[offset[0] :: 2, offset[1] :: 2]

    return channels


def _get_16x12_grid(chan: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Compresses channel down to a 16x12 grid - from libcamera.

    This is taken from
    https://git.linuxtv.org/libcamera.git/tree/utils/raspberrypi/ctt/ctt_alsc.py
    for consistency.
    """
    grid = []

    # since left and bottom border will not necessarily have rectangles of
    # dimension dx x dy, the final iteration has to be handled separately.
    for i in range(11):
        for j in range(15):
            grid.append(np.mean(chan[dy * i : dy * (1 + i), dx * j : dx * (1 + j)]))
        grid.append(np.mean(chan[dy * i : dy * (1 + i), 15 * dx :]))
    for j in range(15):
        grid.append(np.mean(chan[11 * dy :, dx * j : dx * (1 + j)]))
    grid.append(np.mean(chan[11 * dy :, 15 * dx :]))
    # return as np.array, ready for further manipulation
    return np.reshape(np.array(grid), (12, 16))


def _downsampled_channels(channels: np.ndarray, blacklevel: int) -> np.ndarray:
    """Generate a downsampled, un-normalised image from which to calculate the LST."""
    channel_shape = np.array(channels.shape[1:])
    lst_shape = np.array([12, 16])
    step = np.ceil(channel_shape / lst_shape).astype(int)
    return np.stack(
        [
            _get_16x12_grid(
                channels[i, ...].astype(float) - blacklevel, step[1], step[0]
            )
            for i in range(channels.shape[0])
        ],
        axis=0,
    )


def _lst_from_channels(channels: np.ndarray, blacklevel: int) -> LensShadingTables:
    """Given the 4 Bayer colour channels from a white image, generate a LST.

    Internally, is just calls ``_downsampled_channels`` and ``_lst_from_grids``.
    """
    grids = _downsampled_channels(channels, blacklevel)
    return _lst_from_grids(grids)


def _lst_from_grids(grids: np.ndarray) -> LensShadingTables:
    """Given 4 downsampled grids, generate the luminance and chrominance tables.

    The grids are the 4 BAYER channels RGGB

    The LST format has changed with ``picamera2`` and now uses a fixed resolution,
    and is in luminance, Cr, Cb format. This function returns three ndarrays of
    luminance, Cr, Cb, each with shape (12, 16).
    """
    # Calculated red, green, and blue channels from Bayer data
    r: np.ndarray = grids[3, ...]
    g: np.ndarray = np.mean(grids[1:3, ...], axis=0)
    b: np.ndarray = grids[0, ...]

    # What we actually want to calculate is the gains needed to compensate for the
    # lens shading - that's 1/lens_shading_table_float as we currently have it.

    # Minimum luminance gain is 1
    luminance_gains: np.ndarray = np.max(g) / g

    cr_gains: np.ndarray = g / r
    cb_gains: np.ndarray = g / b

    return luminance_gains, cr_gains, cb_gains


def _grids_from_lst(lum: np.ndarray, Cr: np.ndarray, Cb: np.ndarray) -> np.ndarray:
    """Convert form luminance/chrominance dict to four RGGB channels.

    Note that these will be normalised - the maximum green value is always 1.
    Also, note that the channels are BGGR, to be consistent with the
    ``channels_from_raw_image`` function. This should probably change in the
    future.
    """
    G = 1 / np.array(lum)
    R = G / np.array(Cr)
    B = G / np.array(Cb)
    return np.stack([B, G, G, R], axis=0)


def _raw_channels_from_camera(camera: Picamera2, sensor_info: SensorInfo) -> np.ndarray:
    """Acquire a raw image and return a 4xNxM array of the colour channels."""
    if camera.started:
        camera.stop_recording()
    # We will acquire a raw image with unpacked pixels, which is what the
    # format below requests. Bit depth and Bayer order may be overwritten.
    config = camera.create_still_configuration(
        raw={"format": sensor_info.unpacked_pixel_format}
    )
    camera.configure(config)
    camera.start()
    raw_image = camera.capture_array("raw")
    camera.stop()
    # Now we need to calculate a lens shading table that would make this flat.
    # raw_image is a 3D array, with full resolution and 3 colour channels.  No
    # de-mosaicing has been done, so 2/3 of the values are zero (3/4 for R and B
    # channels, 1/2 for green because there's twice as many green pixels).
    raw_format = camera.camera_configuration()["raw"]["format"]
    LOGGER.debug(f"Acquired a raw image in format {raw_format}")
    return _channels_from_bayer_array(raw_image)
