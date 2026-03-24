"""Thermal Plate Device - temperature control and monitoring."""

from typing import Any, Dict, List
from devices.base_device import BaseDevice


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
        with self.lock:
            return {
                "device_type": self.get_device_type(),
                "name": self.name,
                "is_on": self.is_on,
                "status": self.status_message,
                "current_temperature": self.current_temp,
                "target_temperature": self.target_temp,
                "temperature_history": self.temperature_history.copy(),
            }
