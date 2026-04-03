"""Nanopositioner Device - 3-axis stage control."""

from typing import Any, Dict

from devices.base_device import BaseDevice
from devices.oms_channel import SerialInterface, oms_channel


class NanopositionerDevice(BaseDevice):
    """3-axis nanopositioner for precise stage positioning."""
    
    def __init__(self):
        """Initialize nanopositioner device."""
        super().__init__("Nanopositioner Stage")
        self.position = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.connected = False
        self.fine_step = 0.1
        self.coarse_step = 1.0

    def _reply_ok(self, reply: Any) -> bool:
        """Check whether a hardware reply indicates success."""
        return SerialInterface is not None and reply == SerialInterface.ReplyStatus.OK

    def connect(self, port: str, baud_rate: int = 921600) -> None:
        """Connect to the OpenMicroStage hardware interface if available."""
        self.connected = oms_channel.connect(port, baud_rate, show_communication=True, show_log_messages=True)
        self.status_message = f"Connected on {port}" if self.connected else "Failed to connect"

    def disconnect(self) -> None:
        """Disconnect from the OpenMicroStage hardware interface."""
        oms_channel.disconnect()
        self.connected = False
        self.status_message = "Disconnected"

    def read_firmware_version(self) -> tuple[int, int, int]:
        """Read the stage firmware version."""
        interface = oms_channel.get_interface()
        if interface is None:
            return 0, 0, 0
        return interface.read_firmware_version()

    def home(self, axis_list: list[int] = None):
        """Home the stage and update the local position cache."""
        interface = oms_channel.get_interface()
        if interface is not None:
            reply = interface.home(axis_list)
            if self._reply_ok(reply):
                self.position = {"x": 0.0, "y": 0.0, "z": 0.0}
                self.status_message = "Homed to origin (0, 0, 0)"
            return reply

        with self.lock:
            self.position = {"x": 0.0, "y": 0.0, "z": 0.0}
            self.status_message = "Homed to origin (0, 0, 0)"

    def move_to(
        self,
        x: float,
        y: float,
        z: float,
        f: float,
        move_immediately: bool = False,
        blocking: bool = True,
        timeout: float = 1,
    ):
        """Move the stage using the OpenMicroStage interface when connected."""
        interface = oms_channel.get_interface()
        if interface is not None:
            reply = interface.move_to(x, y, z, f, move_immediately=move_immediately, blocking=blocking, timeout=timeout)
            if self._reply_ok(reply):
                with self.lock:
                    self.position = {"x": x, "y": y, "z": z}
                    self.status_message = f"Moved to ({x}, {y}, {z})"
            return reply

        with self.lock:
            self.position = {"x": x, "y": y, "z": z}
            self.status_message = f"Moved to ({x}, {y}, {z})"
        return {"status": "OK"}

    def wait_for_stop(self, disable_callbacks: bool = True):
        """Wait for the stage to stop moving."""
        interface = oms_channel.get_interface()
        if interface is None:
            return {"status": "OK"}
        return interface.wait_for_stop(disable_callbacks=disable_callbacks)

    def read_current_position(self) -> tuple[float, float, float] | tuple[None, None, None]:
        """Get the current position from hardware or the local cache."""
        interface = oms_channel.get_interface()
        if interface is not None:
            return interface.read_current_position()

        with self.lock:
            return self.position["x"], self.position["y"], self.position["z"]

    def set_pose(self, x: float, y: float, z: float):
        """Set the stage pose as fast as possible."""
        interface = oms_channel.get_interface()
        if interface is not None:
            reply = interface.set_pose(x, y, z)
            if self._reply_ok(reply):
                with self.lock:
                    self.position = {"x": x, "y": y, "z": z}
            return reply

        with self.lock:
            self.position = {"x": x, "y": y, "z": z}
            self.status_message = f"Pose set to ({x}, {y}, {z})"
        return {"status": "OK"}

    def read_device_state_info(self):
        """Read the controller state information if hardware is connected."""
        interface = oms_channel.get_interface()
        if interface is None:
            return None
        return interface.read_device_state_info()
    
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
            connected = oms_channel.is_connected()
            self.connected = connected
            return {
                "device_type": self.get_device_type(),
                "name": self.name,
                "is_on": self.is_on,
                "status": self.status_message,
                "connected": connected,
                "position": dict(self.position),
                "fine_step": self.fine_step,
                "coarse_step": self.coarse_step,
            }
