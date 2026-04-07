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
        if interface is not None and getattr(interface, "serial", None) is not None:
            interface.set_vacuum(bool(vacuum_on))
            self.status_message = f"Vacuum set via shared channel: {'ON' if vacuum_on else 'OFF'}"

    def get_vacuum(self) -> bool:
        """Read vacuum state from hardware API or fallback to cached value."""
        interface = oms_channel.get_interface()
        if interface is not None and getattr(interface, "serial", None) is not None:
            try:
                value = bool(interface.get_vacuum())
                self.is_on = value
                return value
            except Exception:
                return self.is_on
        return self.is_on
    
    def get_device_status(self) -> Dict[str, Any]:
        """Get complete vacuum pump status."""
        is_on = self.get_vacuum()
        with self.lock:
            return {
                "device_type": self.get_device_type(),
                "name": self.name,
                "is_on": is_on,
                "status": self.status_message,
                "connected": oms_channel.is_connected(),
            }
