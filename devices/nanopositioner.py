"""Nanopositioner Device - 3-axis stage control."""

from typing import Any, Dict
from devices.base_device import BaseDevice


class NanopositionerDevice(BaseDevice):
    """3-axis nanopositioner for precise stage positioning."""
    
    def __init__(self):
        """Initialize nanopositioner device."""
        super().__init__("Nanopositioner Stage")
        self.position = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.connected = False
        self.fine_step = 0.1
        self.coarse_step = 1.0
    
    def set_position(self, axis: str, value: float) -> None:
        """Set position for a single axis."""
        with self.lock:
            if axis in self.position:
                self.position[axis] = value
                self.status_message = f"Position updated: {self.position}"
    
    def move(self, axis: str, direction: str, step_mode: str) -> Dict[str, Any]:
        """Move stage in specified direction."""
        with self.lock:
            step = self.fine_step if step_mode == "fine" else self.coarse_step
            delta = step if direction == "positive" else -step
            
            if axis in self.position:
                self.position[axis] += delta
                self.status_message = f"Moved {axis} {direction} by {delta}"
                return {
                    "axis": axis,
                    "direction": direction,
                    "step_mode": step_mode,
                    "delta": delta,
                    "new_position": dict(self.position),
                }
            return {}
    
    def home(self) -> None:
        """Reset position to origin."""
        with self.lock:
            self.position = {"x": 0.0, "y": 0.0, "z": 0.0}
            self.status_message = "Homed to origin (0, 0, 0)"
    
    def set_step_sizes(self, fine: float, coarse: float) -> None:
        """Configure fine and coarse step sizes."""
        with self.lock:
            self.fine_step = fine
            self.coarse_step = coarse
            self.status_message = f"Step sizes updated: fine={fine}, coarse={coarse}"
    
    def get_position(self) -> Dict[str, float]:
        """Get current position."""
        with self.lock:
            return dict(self.position)
    
    def get_device_type(self) -> str:
        """Return device type identifier."""
        return "nanopositioner"
    
    def get_device_status(self) -> Dict[str, Any]:
        """Get complete nanopositioner status."""
        with self.lock:
            return {
                "device_type": self.get_device_type(),
                "name": self.name,
                "is_on": self.is_on,
                "status": self.status_message,
                "connected": self.connected,
                "position": dict(self.position),
                "fine_step": self.fine_step,
                "coarse_step": self.coarse_step,
            }
