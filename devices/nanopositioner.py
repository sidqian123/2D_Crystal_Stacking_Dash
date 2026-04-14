"""Nanopositioner Device - 3-axis stage control."""

from typing import Any, Dict

from devices.base_device import BaseDevice
from devices.oms_channel import SerialInterface, oms_channel


class NanopositionerDevice(BaseDevice):
    """3-axis nanopositioner for precise stage positioning."""

    MIN_TRAVEL_MM = -13.0
    MAX_TRAVEL_MM = 13.0
    
    def __init__(self):
        """Initialize nanopositioner device."""
        super().__init__("Nanopositioner Stage")
        self.position = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.connected = False
        self.fine_step = 0.1
        self.coarse_step = 1.0
        self.jog_speed = 3.0

    @staticmethod
    def _axis_index(axis: str) -> int | None:
        """Map axis names to OpenMicroStage axis indices."""
        axis_map = {"x": 0, "y": 1, "z": 2}
        return axis_map.get(axis)

    def _feed_rate_for_step(self, step_mm: float) -> float:
        """Choose a practical feed rate in mm/s for jog moves."""
        if self.jog_speed > 0:
            return self.jog_speed
        if step_mm >= 10:
            return 6.0
        if step_mm >= 1:
            return 3.0
        return 1.0

    @classmethod
    def _clamp_mm(cls, value: float) -> float:
        """Clamp an axis target to configured travel range in millimeters."""
        return max(cls.MIN_TRAVEL_MM, min(cls.MAX_TRAVEL_MM, float(value)))

    def set_jog_speed(self, speed: float) -> None:
        """Set jog movement speed in mm/s for button and manual moves."""
        with self.lock:
            self.jog_speed = max(0.1, float(speed))
            self.status_message = f"Jog speed updated: {self.jog_speed} mm/s"

    def _reply_ok(self, reply: Any) -> bool:
        """Check whether a hardware reply indicates success."""
        return SerialInterface is not None and reply == SerialInterface.ReplyStatus.OK

    def _refresh_position_from_hardware(self) -> tuple[float, float, float] | None:
        """Update cached XYZ position from hardware if available."""
        interface = self._connected_interface()
        if interface is None:
            return None
        try:
            x, y, z = interface.read_current_position()
        except Exception:
            return None
        if x is None or y is None or z is None:
            return None
        with self.lock:
            self.position = {"x": float(x), "y": float(y), "z": float(z)}
        return float(x), float(y), float(z)

    def _connected_interface(self):
        """Return the OpenMicroStage interface only when serial is connected."""
        interface = oms_channel.get_interface()
        if interface is None:
            return None
        if getattr(interface, "serial", None) is None:
            return None
        return interface

    def connect(self, port: str | None = None, baud_rate: int | None = None) -> None:
        """Connect to the OpenMicroStage hardware interface if available."""
        self.connected = oms_channel.connect(port, baud_rate)
        channel = oms_channel.status()
        active_port = channel.get("port") or port or oms_channel.default_port
        self.status_message = f"Connected on {active_port}" if self.connected else f"Failed to connect ({channel.get('error', 'unknown error')})"

    def disconnect(self) -> None:
        """Disconnect from the OpenMicroStage hardware interface."""
        oms_channel.disconnect()
        self.connected = False
        self.status_message = "Disconnected"

    def read_firmware_version(self) -> tuple[int, int, int]:
        """Read the stage firmware version."""
        interface = self._connected_interface()
        if interface is None:
            return 0, 0, 0
        return interface.read_firmware_version()

    def home(self, axis_list: list[int] = None):
        """Home the stage and update the local position cache."""
        interface = self._connected_interface()
        if interface is not None:
            reply = interface.home(axis_list)
            if self._reply_ok(reply):
                refreshed = self._refresh_position_from_hardware()
                if refreshed is not None:
                    x, y, z = refreshed
                    self.status_message = f"Home command accepted; current position ({x:.3f}, {y:.3f}, {z:.3f})"
                else:
                    self.status_message = "Home command accepted; awaiting updated position"
            return reply

        with self.lock:
            self.position = {"x": 0.0, "y": 0.0, "z": 0.0}
            self.status_message = "Homed to origin (0, 0, 0)"

    def home_axis(self, axis: str):
        """Home a single axis and keep local cache aligned."""
        axis_index = self._axis_index(axis)
        if axis_index is None:
            return {"status": "ERROR", "message": f"Invalid axis: {axis}"}

        interface = self._connected_interface()
        if interface is not None:
            reply = interface.home([axis_index])
            if self._reply_ok(reply):
                refreshed = self._refresh_position_from_hardware()
                if refreshed is not None:
                    x, y, z = refreshed
                    self.status_message = f"Axis {axis.upper()} home accepted; current position ({x:.3f}, {y:.3f}, {z:.3f})"
                else:
                    self.status_message = f"Axis {axis.upper()} home accepted; awaiting updated position"
            return reply

        with self.lock:
            self.position[axis] = 0.0
            self.status_message = f"Axis {axis.upper()} homed (simulated)"
        return {"status": "OK"}

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
        interface = self._connected_interface()
        if interface is not None:
            reply = interface.move_to(x, y, z, f, move_immediately=move_immediately, blocking=blocking, timeout=timeout)
            if self._reply_ok(reply):
                refreshed = self._refresh_position_from_hardware()
                if refreshed is not None:
                    rx, ry, rz = refreshed
                    self.status_message = f"Move accepted; current position ({rx:.3f}, {ry:.3f}, {rz:.3f})"
                else:
                    self.status_message = "Move accepted; awaiting updated position"
            return reply

        with self.lock:
            self.position = {"x": x, "y": y, "z": z}
            self.status_message = f"Moved to ({x}, {y}, {z})"
        return {"status": "OK"}

    def move_absolute(self, x: float, y: float, z: float, speed: float | None = None):
        """Move to an absolute XYZ position using configured or provided speed."""
        feed_rate = float(speed) if speed is not None else self.jog_speed
        clamped_x = self._clamp_mm(x)
        clamped_y = self._clamp_mm(y)
        clamped_z = self._clamp_mm(z)
        return self.move_to(clamped_x, clamped_y, clamped_z, f=max(0.1, feed_rate), move_immediately=False, blocking=True, timeout=2)

    def wait_for_stop(self, disable_callbacks: bool = True):
        """Wait for the stage to stop moving."""
        interface = self._connected_interface()
        if interface is None:
            return {"status": "OK"}
        return interface.wait_for_stop(disable_callbacks=disable_callbacks)

    def read_current_position(self) -> tuple[float, float, float] | tuple[None, None, None]:
        """Get the current position from hardware or the local cache."""
        refreshed = self._refresh_position_from_hardware()
        if refreshed is not None:
            return refreshed

        with self.lock:
            return self.position["x"], self.position["y"], self.position["z"]

    def set_pose(self, x: float, y: float, z: float):
        """Set the stage pose as fast as possible."""
        interface = self._connected_interface()
        if interface is not None:
            reply = interface.set_pose(x, y, z)
            if self._reply_ok(reply):
                refreshed = self._refresh_position_from_hardware()
                if refreshed is not None:
                    rx, ry, rz = refreshed
                    self.status_message = f"Pose set; current position ({rx:.3f}, {ry:.3f}, {rz:.3f})"
                else:
                    self.status_message = "Pose set; awaiting updated position"
            return reply

        with self.lock:
            self.position = {"x": x, "y": y, "z": z}
            self.status_message = f"Pose set to ({x}, {y}, {z})"
        return {"status": "OK"}

    def read_device_state_info(self):
        """Read the controller state information if hardware is connected."""
        interface = self._connected_interface()
        if interface is None:
            return None
        return interface.read_device_state_info()
    
    def set_position(self, axis: str, value: float) -> None:
        """Set position for a single axis."""
        with self.lock:
            if axis in self.position:
                self.position[axis] = value
                self.status_message = f"Position updated: {self.position}"
    
    def move(self, axis: str, direction: str, step_mode: str, step_value: float | None = None) -> Dict[str, Any]:
        """Move stage in specified direction using OpenMicroStage move_to semantics.

        step_value is interpreted as microns from UI jog controls.
        """
        if axis not in {"x", "y", "z"}:
            return {"status": "ERROR", "message": f"Invalid axis: {axis}"}

        if step_value is not None:
            step_um = float(step_value)
            resolved_step = step_um / 1000.0
        else:
            resolved_step = self.fine_step if step_mode == "fine" else self.coarse_step
            step_um = resolved_step * 1000.0

        delta = resolved_step if direction == "positive" else -resolved_step

        current_x, current_y, current_z = self.read_current_position()
        if current_x is None or current_y is None or current_z is None:
            with self.lock:
                current_x, current_y, current_z = self.position["x"], self.position["y"], self.position["z"]

        target = {
            "x": float(current_x),
            "y": float(current_y),
            "z": float(current_z),
        }
        target[axis] += delta
        unclamped_target = dict(target)
        target = {
            "x": self._clamp_mm(target["x"]),
            "y": self._clamp_mm(target["y"]),
            "z": self._clamp_mm(target["z"]),
        }
        clamped = target != unclamped_target

        interface = self._connected_interface()
        if interface is not None:
            feed_rate = self._feed_rate_for_step(abs(resolved_step))
            reply = self.move_to(target["x"], target["y"], target["z"], feed_rate, move_immediately=False, blocking=True, timeout=2)
            status = getattr(reply, "name", str(reply))
            measured = self._refresh_position_from_hardware()
            measured_pos = None
            if measured is not None:
                measured_pos = {"x": float(measured[0]), "y": float(measured[1]), "z": float(measured[2])}
            return {
                "axis": axis,
                "direction": direction,
                "step_mode": step_mode,
                "step_um": step_um,
                "step_value": resolved_step,
                "delta": delta,
                "target": target,
                "measured_position": measured_pos,
                "clamped": clamped,
                "travel_range_mm": [self.MIN_TRAVEL_MM, self.MAX_TRAVEL_MM],
                "feed_rate": feed_rate,
                "reply": status,
            }

        with self.lock:
            self.position = target
            self.status_message = f"Moved {axis} {direction} by {delta} (simulated)"

        return {
            "axis": axis,
            "direction": direction,
            "step_mode": step_mode,
            "step_um": step_um,
            "step_value": resolved_step,
            "delta": delta,
            "target": target,
            "clamped": clamped,
            "travel_range_mm": [self.MIN_TRAVEL_MM, self.MAX_TRAVEL_MM],
            "reply": "SIMULATED",
        }
    
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
        self._refresh_position_from_hardware()
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
                "jog_speed": self.jog_speed,
                "travel_range_mm": [self.MIN_TRAVEL_MM, self.MAX_TRAVEL_MM],
            }
