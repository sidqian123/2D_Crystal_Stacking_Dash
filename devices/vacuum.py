"""Vacuum Pump Device - simple on/off control."""

from typing import Any, Dict

from devices.base_device import BaseDevice
from devices.oms_channel import oms_channel


class VacuumDevice(BaseDevice):
    """Vacuum pump with simple on/off control."""
    
    def __init__(self):
        """Initialize vacuum device."""
        super().__init__("Vacuum Pump")
    
    def get_device_type(self) -> str:
        """Return device type identifier."""
        return "vacuum"

    def set_vacuum(self, vacuum_on: bool) -> None:
        """Compatibility wrapper for OpenMicroStageInterface-style vacuum control."""
        self.set_power(vacuum_on)
        interface = oms_channel.get_interface()
        if interface is not None:
            interface.set_vacuum(bool(vacuum_on))
            self.status_message = f"Vacuum set via shared channel: {'ON' if vacuum_on else 'OFF'}"
    
    def get_device_status(self) -> Dict[str, Any]:
        """Get complete vacuum pump status."""
        with self.lock:
            return {
                "device_type": self.get_device_type(),
                "name": self.name,
                "is_on": self.is_on,
                "status": self.status_message,
                "connected": oms_channel.is_connected(),
            }
