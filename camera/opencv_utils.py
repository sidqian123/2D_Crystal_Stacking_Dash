"""Utilities for managing CV2 cameras."""

import logging
import sys
from typing import Callable, Optional

import cv2

try:
    from cv2_enumerate_cameras.camera_info import CameraInfo

    enumerate_cameras: Optional[Callable[[int], list[CameraInfo]]]
    # MyPy thinks enumerate_cameras is being redefined. We just needed to set the type
    # before defining.
    from cv2_enumerate_cameras import enumerate_cameras  # type: ignore[no-redef]
except ModuleNotFoundError:
    enumerate_cameras = None

LOGGER = logging.getLogger(__name__)

if sys.platform.startswith("win"):
    BACKEND = cv2.CAP_DSHOW
elif sys.platform.startswith("linux"):
    BACKEND = cv2.CAP_V4L2
elif sys.platform == "darwin":
    BACKEND = cv2.CAP_AVFOUNDATION
else:
    raise RuntimeError(f"Unsupported platform {sys.platform}")

# Max cameras is set to balance finding all cameras with not taking too long to start up.
# Note that due to extra camera modes in Linux 1 camera can take up multiple camera numbers.
MAX_CAMERAS = 12


def find_all_cameras() -> list[int]:
    """Find all accessible USB cameras on the device."""
    available = []
    for i in range(MAX_CAMERAS):
        cap = cv2.VideoCapture(i, BACKEND)

        if cap.isOpened():
            available.append(i)
            cap.release()
    return available


def identify_cameras(camera_ids: list[int]) -> dict[str, int]:
    """For a list of camera IDs return a dictionary of name -> ID."""
    # When first creating the mapping with default names it goes from
    # id -> camera name. This makes it easy to replace the name (based on a fixed camera
    # id as the key) if the name is then found.
    # Before returning this is swapped to be a mapping from camera name -> id. As this
    # is what is needed to switch the cameras based on a name.
    name_dict = {n: f"Unknown Camera {n}" for n in camera_ids}

    # enumerate cameras works for all backends if it is installed:
    if enumerate_cameras is not None:
        for camera_info in enumerate_cameras(BACKEND):
            if camera_info.index in name_dict:
                name_dict[camera_info.index] = camera_info.name
    elif BACKEND == cv2.CAP_V4L2:
        # If Linux, try to read video4linux name as a fallback option.
        for camera_id in camera_ids:
            try:
                if not isinstance(camera_id, int):
                    raise TypeError("Camera ID must be an integer.")
                # Linux only so just use direct path.
                path = f"/sys/class/video4linux/video{camera_id}/name"
                with open(path, "r") as f_obj:
                    name_dict[camera_id] = f_obj.read().strip()
            except IOError:
                pass
    else:
        LOGGER.warning(
            "Cannot determine camera names. Please install the Optional `manual` "
            "dependencies."
        )

    # Swap order for return
    return {name: cam_id for cam_id, name in name_dict.items()}
