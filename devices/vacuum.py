"""Vacuum Pump Device - simple on/off control."""

from typing import Any, Dict
from devices.base_device import BaseDevice


class VacuumDevice(BaseDevice):
    """Vacuum pump with simple on/off control."""
    
    def __init__(self):
        """Initialize vacuum device."""
        super().__init__("Vacuum Pump")
    
    def get_device_type(self) -> str:
        """Return device type identifier."""
        return "vacuum"
    
    def get_device_status(self) -> Dict[str, Any]:
        """Get complete vacuum pump status."""
        with self.lock:
            return {
                "device_type": self.get_device_type(),
                "name": self.name,
                "is_on": self.is_on,
                "status": self.status_message,
            }
