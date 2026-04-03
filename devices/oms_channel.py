"""Shared OpenMicroStage hardware channel for all device modules."""

from threading import Lock
from typing import Optional

try:
    from open_micro_stage_api import OpenMicroStageInterface
    from open_micro_stage_api.api import SerialInterface
except Exception:  # pragma: no cover - optional hardware dependency
    OpenMicroStageInterface = None
    SerialInterface = None


class OpenMicroStageChannel:
    """Singleton-like shared serial channel for stage, thermal, and vacuum controls."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._interface: Optional[object] = None
        self._last_error = "Not connected"
        self._port: Optional[str] = None
        self._baud_rate: Optional[int] = None

    def connect(self, port: str, baud_rate: int = 921600, show_communication: bool = True, show_log_messages: bool = True) -> bool:
        """Create a shared OpenMicroStageInterface and connect to hardware."""
        if OpenMicroStageInterface is None:
            self._last_error = "OpenMicroStage API is unavailable"
            return False

        with self._lock:
            if self._interface is not None:
                self._interface.disconnect()
                self._interface = None

            interface = OpenMicroStageInterface(
                show_communication=show_communication,
                show_log_messages=show_log_messages,
            )
            interface.connect(port, baud_rate)

            if getattr(interface, "serial", None) is None:
                self._last_error = f"Failed to connect on {port}"
                self._port = None
                self._baud_rate = None
                return False

            self._interface = interface
            self._last_error = ""
            self._port = port
            self._baud_rate = baud_rate
            return True

    def disconnect(self) -> None:
        """Disconnect and clear the shared interface."""
        with self._lock:
            if self._interface is not None:
                self._interface.disconnect()
            self._interface = None
            self._last_error = "Disconnected"
            self._port = None
            self._baud_rate = None

    def get_interface(self) -> Optional[object]:
        """Return the active shared interface instance."""
        with self._lock:
            return self._interface

    def is_connected(self) -> bool:
        """Return whether the shared channel is connected."""
        with self._lock:
            return self._interface is not None and getattr(self._interface, "serial", None) is not None

    def status(self) -> dict:
        """Return current channel metadata for API responses."""
        with self._lock:
            return {
                "connected": self._interface is not None and getattr(self._interface, "serial", None) is not None,
                "port": self._port,
                "baud_rate": self._baud_rate,
                "error": self._last_error,
                "api_available": OpenMicroStageInterface is not None,
            }


oms_channel = OpenMicroStageChannel()
