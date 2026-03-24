"""Camera Device - video capture and control."""

from typing import Any, Dict
from devices.base_device import BaseDevice


class CameraDevice(BaseDevice):
    """Camera with streaming and control capabilities."""
    
    def __init__(self):
        """Initialize camera device."""
        super().__init__("Camera Feed")
        self.camera_available = False
        self.camera_error = "Camera not initialized"
        self.current_temp = 0.0
        self.frame_id = 0
    
    def set_camera_available(self, available: bool, error: str = "") -> None:
        """Update camera availability status."""
        with self.lock:
            self.camera_available = available
            self.camera_error = error
            if available:
                self.status_message = "Camera ready"
            else:
                self.status_message = f"Camera unavailable: {error}"
    
    def get_camera_info(self) -> tuple:
        """Get camera availability and error status."""
        with self.lock:
            return self.camera_available, self.camera_error
    
    def get_device_type(self) -> str:
        """Return device type identifier."""
        return "camera"
    
    def get_device_status(self) -> Dict[str, Any]:
        """Get complete camera status."""
        with self.lock:
            available, error = self.camera_available, self.camera_error
            return {
                "device_type": self.get_device_type(),
                "name": self.name,
                "is_on": self.is_on,
                "status": self.status_message,
                "camera_available": available,
                "camera_error": error,
                "frame_id": self.frame_id,
            }
