"""Base device class with common attributes and methods."""

from threading import Lock
from typing import Any, Dict
from abc import ABC, abstractmethod


class BaseDevice(ABC):
    """Abstract base class for all controllable devices."""
    
    def __init__(self, name: str):
        """
        Initialize base device.
        
        Args:
            name: Device friendly name (e.g., "Thermal Plate", "Vacuum Pump")
        """
        self.lock = Lock()
        self.name = name
        self.is_on = False  # Power state - true for ON, false for OFF
        self.status_message = "Initialized"  # Status message
    
    def set_power(self, enabled: bool) -> None:
        """Control device power state."""
        with self.lock:
            self.is_on = enabled
            self.status_message = f"Device turned {'ON' if enabled else 'OFF'}"
    
    def get_power(self) -> bool:
        """Get device power state."""
        with self.lock:
            return self.is_on
    
    def set_status(self, message: str) -> None:
        """Update device status message."""
        with self.lock:
            self.status_message = message
    
    def get_status(self) -> str:
        """Get device status message."""
        with self.lock:
            return self.status_message
    
    @abstractmethod
    def get_device_status(self) -> Dict[str, Any]:
        """
        Get complete device status for API response.
        Subclasses must implement this to include device-specific data.
        """
        pass
    
    @abstractmethod
    def get_device_type(self) -> str:
        """Return device type identifier (e.g., 'thermal', 'vacuum')."""
        pass
