"""Thermal Plate Device - temperature control and monitoring."""

from typing import Any, Dict, List

from devices.base_device import BaseDevice
from devices.oms_channel import oms_channel


class ThermalPlateDevice(BaseDevice):
    """Thermal plate with precise temperature control."""
    
    def __init__(self):
        """Initialize thermal plate device."""
        super().__init__("Thermal Plate")
        self.current_temp = 25.0  # Current temperature in Celsius
        self.target_temp = 25.0   # Target temperature in Celsius
        self.temperature_history: List[float] = [25.0]  # History for graphing (last 60 readings)
    
    def set_target_temp(self, target: float) -> None:
        """Set target temperature (0-100°C)."""
        with self.lock:
            self.target_temp = max(0, min(100, target))  # Clamp 0-100°C
            self.status_message = f"Target temperature set to {self.target_temp}°C"

    def set_temperature(self, temperature: float) -> None:
        """Compatibility wrapper for OpenMicroStageInterface-style temperature control."""
        self.set_target_temp(temperature)
        interface = oms_channel.get_interface()
        if interface is not None and getattr(interface, "serial", None) is not None:
            interface.set_temperature(float(self.target_temp))
            self.status_message = f"Temperature set via shared channel to {self.target_temp}°C"

    def get_temperature(self) -> float:
        """Read current temperature from hardware API or fallback to cached value."""
        interface = oms_channel.get_interface()
        if interface is not None and getattr(interface, "serial", None) is not None:
            try:
                value = float(interface.get_temperature())
                self.current_temp = value
                return value
            except Exception:
                return self.current_temp
        return self.current_temp
    
    def get_target_temp(self) -> float:
        """Get target temperature."""
        with self.lock:
            return self.target_temp
    
    def add_reading(self, temp: float) -> None:
        """Add temperature reading to history."""
        with self.lock:
            self.current_temp = temp
            self.temperature_history.append(temp)
            # Keep last 60 readings
            if len(self.temperature_history) > 60:
                self.temperature_history.pop(0)
    
    def get_history(self) -> List[float]:
        """Get temperature history."""
        with self.lock:
            return self.temperature_history.copy()
    
    def get_device_type(self) -> str:
        """Return device type identifier."""
        return "thermal"
    
    def get_device_status(self) -> Dict[str, Any]:
        """Get complete thermal plate status."""
        current = self.get_temperature()
        with self.lock:
            return {
                "device_type": self.get_device_type(),
                "name": self.name,
                "is_on": self.is_on,
                "status": self.status_message,
                "connected": oms_channel.is_connected(),
                "current_temperature": current,
                "target_temperature": self.target_temp,
                "temperature_history": self.temperature_history.copy(),
            }
