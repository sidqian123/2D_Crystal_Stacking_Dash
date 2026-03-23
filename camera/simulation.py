"""OpenFlexure Microscope OpenCV Camera.

This module defines a Thing that is responsible for using the stage and
camera together to perform an autofocus routine.

See repository root for licensing information.
"""

from __future__ import annotations

import io
import logging
import re
import time
from threading import Thread
from types import TracebackType
from typing import Literal, Optional, Self, overload

import numpy as np
from PIL import Image, ImageFilter

import labthings_fastapi as lt
from labthings_fastapi.types.numpy import NDArray

from openflexure_microscope_server.ui import (
    ActionButton,
    PropertyControl,
    action_button_for,
    property_control_for,
)

from ..stage.dummy import DummyStage
from . import BaseCamera

LOGGER = logging.getLogger(__name__)

# The ratio between "motor" steps and pixels in (x, y, z)
# higher related to a faster movement
RATIO = (2, 2, 0.07)

# Some colour variation, for bg detect.
BG_COLOR = [220, 215, 217]

# Random Number Generator
RNG = np.random.default_rng()


DOWNSAMPLE = 2
LOW_MAG_DOWNSAMPLE = 8
# Upsample for sprites and then downsample to create sharp edges for each sprite
# as these are small and calculated once there is almost no performance penalty
# for a nice gain in quality.
SPRITE_UPSAMPLE = 4

# A list of 6 digit hex colour codes separated by ;. Allow a trailing ;
# For example, OpenFlexure pink would be #C5247F;
COLOUR_LIST_REGEX = re.compile(
    r"^\s*(#[0-9a-fA-F]{6})\s*(?:;\s*(#[0-9a-fA-F]{6})\s*)*;?\s*$"
)
# regex to separate R, G and B from a 6 digit hex code with preceding #
COLOUR_REGEX = re.compile(r"^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$")


@overload
def _downsample_shape(
    shape: tuple[int, int], factor: float | int
) -> tuple[int, int]: ...


@overload
def _downsample_shape(
    shape: tuple[int, int, int], factor: float | int
) -> tuple[int, int, int]: ...


def _downsample_shape(
    shape: tuple[int, int] | tuple[int, int, int], factor: float | int
) -> tuple[int, int] | tuple[int, int, int]:
    if len(shape) == 2:
        return (int(shape[0] // factor), int(shape[1] // factor))
    if len(shape) == 3:
        return (int(shape[0] // factor), int(shape[1] // factor), shape[2])
    raise ValueError("Shape should be a 2 or 3 element tuple.")


def colour_str_to_colour(colour_str: str) -> tuple[int, int, int]:
    """Convert a colour string into RGB colour values.

    :param colour_str: Should be a hex colour such as #33aa33 or a list of hex
        colours separated by semicolons (with optional spaces).
    :return: The colour as a tuple of 3 integers from 0 to 255 in value
    :raises ValueError: If the hex string is not valid. This should never happen if the
        user enters a bad colour string as the colour property setter checks the
        whole string regex.
    """
    if ";" in colour_str:
        colours = colour_str.split(";")
        if len(colours) > 1 and colours[-1].strip() == "":
            colours.pop(-1)
        single_colour_str = colours[RNG.integers(0, len(colours))]
    else:
        single_colour_str = colour_str
    single_colour_str = single_colour_str.lower().strip()
    colour_match = COLOUR_REGEX.match(single_colour_str)
    if colour_match is None:
        raise ValueError(
            f"{colour_str} is not a valid colour. Please use HTML hex notation."
        )

    r = int("0x" + colour_match.group(1), 16)
    g = int("0x" + colour_match.group(2), 16)
    b = int("0x" + colour_match.group(3), 16)
    return r, g, b


class SimulatedCamera(BaseCamera):
    """A Thing that simulates a camera for testing."""

    _stage: DummyStage = lt.thing_slot()

    _show_sample: bool = True

    _objective: int = 40  # default 40x, our standard build

    @lt.property
    def objective(self) -> int:
        """Objective magnification (e.g. 4, 10, 20, 40, 60, 100)."""
        return self._objective

    @objective.setter
    def _set_objective(self, value: int) -> None:
        if value not in (4, 10, 20, 40, 60, 100):
            raise ValueError("Objective must be one of 4, 10, 20, 40, 60, 100.")
        self._objective = value

    def __init__(
        self,
        thing_server_interface: lt.ThingServerInterface,
        shape: tuple[int, int, int] = (616, 820, 3),
        canvas_shape: tuple[int, int, int] = (1500, 2000, 3),
        frame_interval: float = 0.1,
    ) -> None:
        """Initialise the simulated with settings for how images are generated.

        :param shape: The shape (size) of the generated image.
        :param canvas_shape: The shape (size) of the canvas generated on initialisation
            that images are cropped from. If this is too large the it uses resources,
            but its size limits the range of motion of the simulation.
        :param frame_interval: Nominally the time between frames on the MJPEG stream,
            however the rate may be slower due to calculation time for focus.
        """
        super().__init__(thing_server_interface)
        self.shape = shape
        self.glyph_size = 105 // DOWNSAMPLE
        self.canvas_shape = _downsample_shape(canvas_shape, DOWNSAMPLE)
        self.low_mag_canvas_shape = _downsample_shape(canvas_shape, LOW_MAG_DOWNSAMPLE)
        self.frame_interval = frame_interval
        self._capture_thread: Optional[Thread] = None
        self._capture_enabled = False
        self.generate_sprites()
        # Whether the LED is on
        self.led_on = True

    repeating: bool = lt.property(default=False)

    _blob_density: int = 400

    @lt.property
    def blob_density(self) -> int:
        """The number of blobs per million pixels."""
        return self._blob_density

    @blob_density.setter
    def _set_blob_density(self, value: int) -> None:
        if value < 0:
            raise ValueError("Sample density must be >= 0")
        self._blob_density = value
        if self._capture_enabled:
            self.generate_canvas()

    _colour: str = "#b937b9"

    @lt.property
    def colour(self) -> str:
        """The colour of the blobs as a HTML hex string.

        The string can either be a single colour (e.g. "#c5247f") or a list of
        colours separated by semicolons (e.g. "#c5247f; #b937b9"). Additional
        spaces are allowed between colours.
        """
        return self._colour

    @colour.setter
    def _set_colour(self, colour_value: str) -> None:
        if COLOUR_LIST_REGEX.match(colour_value) is None:
            self.logger.warning(f"{colour_value} is not a valid colour string.")
            return

        self._colour = colour_value
        if self._capture_enabled:
            self.generate_canvas()

    @lt.property
    def calibration_required(self) -> bool:
        """Whether the camera needs calibrating."""
        if self.background_detector is None:
            return True
        return not self.background_detector.ready

    def generate_sprites(self) -> None:
        """Generate sprites to populate the image."""
        sprite_sizes = [10, 21, 36, 40, 50]
        sprite_sizes = [s * SPRITE_UPSAMPLE for s in sprite_sizes]
        self.sprites = []

        block_size = self.glyph_size * DOWNSAMPLE * SPRITE_UPSAMPLE
        channel_block = np.zeros((block_size, block_size))
        x = np.arange(channel_block.shape[0])
        y = np.arange(channel_block.shape[1])
        # 2D grid of radii
        r_coord = np.sqrt(
            (x[:, None] - np.mean(x)) ** 2 + (y[None, :] - np.mean(y)) ** 2
        )

        for sprite_size in sprite_sizes:
            # Mask of where this sprite is
            sprite_mask = r_coord < sprite_size
            # Calculate a sharp edged circle with value varying from 0 in centre to 255
            # at the edge
            sprite_px = r_coord[sprite_mask]
            sprite_px -= np.min(sprite_px)
            sprite_px /= np.max(sprite_px)
            sprite = channel_block.copy()
            sprite[sprite_mask] = 255 * sprite_px

            # Convert to uint8
            sprite = sprite.astype(np.uint8)
            # Convert to PIL (and back) to resize then append to list of sprites
            sprite_pil = Image.fromarray(sprite)
            sprite_pil = sprite_pil.resize(
                (self.glyph_size, self.glyph_size), Image.Resampling.BILINEAR
            )
            # Convert back and ensure all edges are zero as these are repeated at sample
            # edge
            sprite = np.array(sprite_pil)
            sprite[0, :] = 0
            sprite[-1, :] = 0
            sprite[:, 0] = 0
            sprite[:, -1] = 0
            self.sprites.append(sprite)

    def generate_blobs(self, n_blobs: int = 1000) -> None:
        """Generate coordinates of blobs and their sizes, centered around (0,0).

        Note that blob density is determined by sample size and n_blobs, and for larger
        samples n_blobs will need increasing to keep a high level of sample coverage per
        field of view.

        :param n_blobs: The number of blobs to generate.
        """
        self.blobs = np.zeros((n_blobs, 3))
        w = self.glyph_size

        self.blobs[:, 0] = RNG.uniform(w // 2, self.canvas_shape[1] - w // 2, n_blobs)
        self.blobs[:, 1] = RNG.uniform(w // 2, self.canvas_shape[0] - w // 2, n_blobs)
        self.blobs[:, 2] = RNG.choice(len(self.sprites), n_blobs)

    def generate_canvas(self) -> None:
        """Generate a canvas with generated blobs centered at the middle.

        Canvas is int16 so that random noise can be added to simulation image before
        changing to unit8 to stop wrapping.
        """
        n_pixels = self.canvas_shape[0] * self.canvas_shape[1] * DOWNSAMPLE**2
        self.generate_blobs(int(self.blob_density * 1e-6 * n_pixels))
        self.blank_canvas = np.ones(self.canvas_shape, dtype=np.int16)
        self.blank_canvas[:, :, 0] *= BG_COLOR[0]
        self.blank_canvas[:, :, 1] *= BG_COLOR[1]
        self.blank_canvas[:, :, 2] *= BG_COLOR[2]
        self.blank_canvas_low_mag = np.ones(self.low_mag_canvas_shape, dtype=np.int16)
        self.blank_canvas_low_mag[:, :, 0] *= BG_COLOR[0]
        self.blank_canvas_low_mag[:, :, 1] *= BG_COLOR[1]
        self.blank_canvas_low_mag[:, :, 2] *= BG_COLOR[2]
        new_canvas = self.blank_canvas.copy()

        for blob_x, blob_y, sprite_index in self.blobs:
            self.draw_sprite_on_canvas(
                new_canvas, self.sprites[int(sprite_index)], int(blob_y), int(blob_x)
            )
        self.canvas = np.clip(new_canvas, 0, 255)
        # Create a further downsized canvas for low mag. This has a minimal memory
        # footprint but speeds up indexing the canvas when simulation uses low magnification
        # objectives
        self.canvas_low_mag = fast_resize_and_blur(
            self.canvas, sigma=0, shape=self.low_mag_canvas_shape
        )
        # Check edge pixels are blank as these are repeated for finite samples.
        self.canvas_low_mag[0, :, :] = self.blank_canvas_low_mag[0, :, :]
        self.canvas_low_mag[-1, :, :] = self.blank_canvas_low_mag[-1, :, :]
        self.canvas_low_mag[:, 0, :] = self.blank_canvas_low_mag[:, 0, :]
        self.canvas_low_mag[:, -1, :] = self.blank_canvas_low_mag[:, -1, :]

    def draw_sprite_on_canvas(
        self, canvas: np.ndarray, sprite: np.ndarray, centre_y: int, centre_x: int
    ) -> None:
        """Place one sprite on canvas at given centre coordinates.

        Note that self.canvas is modified in place.

        :param sprite: The sprite array to place on the canvas.
        :param centre_y: The y coordinate to place the centre of the sprite.
        :param centre_x: The x coordinate to place the centre of the sprite.
        """
        canvas_h, canvas_w, _ = canvas.shape
        sprite_h, sprite_w = sprite.shape

        sprite_f = sprite.astype(float) / 255
        r, g, b = colour_str_to_colour(self.colour)
        sprite_r = (255 - r) * sprite_f
        sprite_g = (255 - g) * sprite_f
        sprite_b = (255 - b) * sprite_f
        sprite_rgb = np.stack([sprite_r, sprite_g, sprite_b], axis=2)

        # Canvas region containing the sprite
        top = max(centre_y - sprite_h // 2, 0)
        left = max(centre_x - sprite_w // 2, 0)
        bottom = min(centre_y + (sprite_h - sprite_h // 2), canvas_h)
        right = min(centre_x + (sprite_w - sprite_w // 2), canvas_w)

        canvas[top:bottom, left:right] -= sprite_rgb.astype("int16")

    def generate_image(self, pos: tuple[int, int, int]) -> Image.Image:
        """Generate an image with blobs based on supplied coordinates.

        :param pos: a 3-item tuple containing the x,y,z coordinates of the 'stage'
        """
        canvas_width, canvas_height, _ = self.low_mag_canvas_shape
        # Base image size

        objective_downsample = self.objective / 40
        if objective_downsample >= 0.4:
            canvas = self.canvas if self._show_sample else self.blank_canvas
            canvas_width, canvas_height, _ = self.canvas_shape
            canvas_ds = DOWNSAMPLE
            img_downsample = DOWNSAMPLE * objective_downsample
        else:
            canvas = (
                self.canvas_low_mag if self._show_sample else self.blank_canvas_low_mag
            )
            canvas_width, canvas_height, _ = self.low_mag_canvas_shape
            canvas_ds = LOW_MAG_DOWNSAMPLE
            img_downsample = LOW_MAG_DOWNSAMPLE * objective_downsample
        image_width, image_height, _ = _downsample_shape(self.shape, img_downsample)

        im_pos = (
            pos[0] * RATIO[0] / canvas_ds,
            pos[1] * RATIO[1] / canvas_ds,
            pos[2] * RATIO[2],
        )

        top_left = (
            int(im_pos[0]) - image_width // 2 + canvas_width // 2,
            int(im_pos[1]) - image_height // 2 + canvas_height // 2,
        )

        x_indices = np.arange(top_left[0], top_left[0] + image_width)
        y_indices = np.arange(top_left[1], top_left[1] + image_height)

        if self.repeating:
            # Create index list with modulo rather than slicing to handle wrapping at the
            # canvas edge.
            x_indices = x_indices % canvas_width
            y_indices = y_indices % canvas_height
        else:
            # Rather than use a modulo for the index list, as above when wrapping,
            # this uses np.clip to coerce all out of bound indices to repeat the
            # first or last pixel in the canvas. This works because no sprite touches
            # the very edge of the canvas (to prevent partial sprites).
            x_indices = np.clip(x_indices, 0, canvas_width - 1)
            y_indices = np.clip(y_indices, 0, canvas_height - 1)

        z_indices = np.arange(self.shape[2])

        # Use npx to make each 1d index list 3D
        focused_np_img = canvas[np.ix_(x_indices, y_indices, z_indices)]
        np_img = fast_resize_and_blur(
            focused_np_img, sigma=np.abs(im_pos[2]) / 5, shape=self.shape
        )
        # Generate random noise by repeating 500 noise points, as the speed rather
        # than randomness is important for simulation.
        noise = RNG.normal(scale=self.noise_level, size=500).astype("int16")
        np_img += np.resize(noise, np_img.shape)
        # Clip then convert to uint8
        np.clip(np_img, 0, 255, out=np_img)
        return Image.fromarray(np_img.astype("uint8"))

    def set_led(self, led_on: bool = True) -> None:
        """Set the simulated LED to on or off."""
        self.led_on = led_on

    def generate_frame(self) -> Image.Image:
        """Generate a frame with blobs based on the stage coordinates."""
        # Simulate LED turning off by setting all channels to 0
        if not self.led_on:
            return Image.new(mode="RGB", size=(self.shape[1], self.shape[0]), color=0)
        # Otherwise, generate a frame from current position
        pos = self._stage.instantaneous_position
        return self.generate_image((pos["y"], pos["x"], pos["z"]))

    def __enter__(self) -> Self:
        """Start the capture thread when the Thing context manager is opened."""
        super().__enter__()
        self.generate_canvas()
        self.start_streaming()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        """Close the capture thread when the Thing context manager is closed."""
        if self._capture_thread is not None and self._capture_thread.is_alive():
            self._capture_enabled = False
            self._capture_thread.join()
        super().__exit__(exc_type, exc_value, traceback)

    @lt.action
    def start_streaming(
        self, main_resolution: tuple[int, int] = (820, 616), buffer_count: int = 1
    ) -> None:
        """Start the live stream.

        The start_streaming method is used a camera ``Thing`` to begin streaming
        images or to adjust the stream resolution if streaming is already active.

        The simulation camera does not currently support the resolution argument.
        It will always issue a warning that the resolution is not respected.
        If called while already streaming, the warning will be emitted and no other
        action will be taken.

        :param main_resolution: Currently ignored, this argument exists to ensure consistent API across camera Things.
        :param buffer_count: Currently ignored, this argument exists to ensure consistent API across camera Things.
        """
        LOGGER.warning(
            f"Simulation camera doesn't respect {main_resolution=} or {buffer_count=} "
            "arguments."
        )
        if not self.stream_active:
            self._capture_enabled = True
            self._capture_thread = Thread(target=self._capture_frames)
            self._capture_thread.start()

    @lt.property
    def stream_active(self) -> bool:
        """Whether the MJPEG stream is active."""
        if self._capture_enabled and self._capture_thread:
            return self._capture_thread.is_alive()
        return False

    noise_level: float = lt.property(default=2.0, ge=0, le=50)

    def _capture_frames(self) -> None:
        last_frame_t = time.time()
        while self._capture_enabled:
            wait_time = self.frame_interval - (time.time() - last_frame_t)
            if wait_time > 0:
                time.sleep(wait_time)
            last_frame_t = time.time()

            frame = self.generate_frame()
            self.mjpeg_stream.add_frame(_frame2bytes(frame))
            ds_frame = frame.resize((320, 240), resample=Image.Resampling.NEAREST)
            self.lores_mjpeg_stream.add_frame(_frame2bytes(ds_frame))

    @lt.action
    def discard_frames(self) -> None:
        """Discard frames so that the next frame captured is fresh.

        There is nothing to do as this is a simulation!
        """

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

        :param stream_name: Currently ignored, this argument exists to ensure consistent API across camera Things.
        :param wait: Currently ignored, this argument exists to ensure consistent API across camera Things.
        """
        if wait is not None:
            LOGGER.warning("Simulation camera has no wait option. Use None.")
        LOGGER.warning(f"Simulation camera camera doesn't respect {stream_name=}")
        return np.array(self.generate_frame())

    def capture_image(
        self,
        stream_name: Literal["main", "lores", "full"],
        wait: Optional[float] = None,
    ) -> Image.Image:
        """Capture to a PIL image. This is not exposed as a ThingAction.

        It is used for capture to memory.

        :param stream_name: Currently ignored, this argument exists to ensure consistent API across camera Things.
        :param wait: Currently ignored, this argument exists to ensure consistent API across camera Things.
        """
        if wait is not None:
            LOGGER.warning("Simulation camera has no wait option. Use None.")
        LOGGER.warning(f"Simulation camera camera doesn't respect {stream_name=}")
        return self.generate_frame()

    @lt.action
    def full_auto_calibrate(self) -> None:
        """Perform a full auto-calibration.

        For the simulation microscope the process is:

        * ``remove_sample``
        * ``set_background``
        * ``load_sample``
        """
        self.remove_sample()
        time.sleep(0.2)
        if self.background_detector is not None:
            self.set_background()
        time.sleep(0.2)
        self.load_sample()

    @lt.action
    def remove_sample(self) -> None:
        """Show the simulated background with no sample."""
        if not self._show_sample:
            raise RuntimeError("Sample is already removed.")
        self._show_sample = False

    @lt.action
    def load_sample(self) -> None:
        """Show the simulated sample."""
        if self._show_sample:
            raise RuntimeError("Sample is already in place.")
        self._show_sample = True

    @lt.property
    def primary_calibration_actions(self) -> list[ActionButton]:
        """The calibration actions for both calibration wizard and settings panel."""
        return [
            action_button_for(
                self, "full_auto_calibrate", submit_label="Full Auto-Calibrate"
            ),
        ]

    @lt.property
    def secondary_calibration_actions(self) -> list[ActionButton]:
        """The calibration actions that appear only in settings panel."""
        return [
            action_button_for(self, "load_sample", submit_label="Load Sample"),
            action_button_for(self, "remove_sample", submit_label="Remove Sample"),
        ]

    @lt.property
    def manual_camera_settings(self) -> list[PropertyControl]:
        """The camera settings to expose as property controls in the settings panel."""
        return [
            property_control_for(self, "repeating", label="Infinite Sample"),
            property_control_for(self, "blob_density", label="Sample Density"),
            property_control_for(self, "colour", label="Sample Colour"),
            property_control_for(self, "noise_level", label="Noise Level"),
            property_control_for(
                self,
                "objective",
                label="Objective Magnification",
                options={
                    "4x": 4,
                    "10x": 10,
                    "20x": 20,
                    "40x": 40,
                    "60x": 60,
                    "100x": 100,
                },
            ),
        ]


def _frame2bytes(frame: Image.Image) -> bytes:
    """Convert frame to bytes."""
    with io.BytesIO() as buf:
        # Save in low quality for speed.
        frame.save(buf, format="JPEG", quality=85)
        return buf.getvalue()


def fast_resize_and_blur(
    array: np.ndarray, sigma: float, shape: tuple[int, ...]
) -> np.ndarray:
    """Apply Gaussian blur using PIL (faster than scipy)."""
    img_pil = Image.fromarray(array.astype(np.uint8))
    img_pil = img_pil.resize((shape[1], shape[0]), Image.Resampling.BILINEAR)
    if sigma > 0.5:
        img_pil = img_pil.filter(ImageFilter.GaussianBlur(radius=sigma))

    # Convert back to NumPy array
    return np.array(img_pil, dtype=array.dtype)
