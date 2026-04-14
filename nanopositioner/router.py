"""
Nanopositioner Control Router
Provides API endpoints for 3-axis stage control.
Hardware driver implementation to be completed.
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from devices.nanopositioner import NanopositionerDevice
from devices.oms_channel import oms_channel

router = APIRouter(prefix="/api/nanopositioner", tags=["nanopositioner"])

# Global nanopositioner device instance
nanopositioner_device = NanopositionerDevice()


class MoveCommand(BaseModel):
    """Command to move stage."""
    axis: Literal["x", "y", "z"]
    direction: Literal["positive", "negative"]
    step_mode: Literal["fine", "coarse"]
    step_value: float | None = Field(default=None, gt=0)


class HomeAxisCommand(BaseModel):
    """Command to home a single axis."""
    axis: Literal["x", "y", "z"]


class StepConfig(BaseModel):
    """Step size configuration."""
    fine_step: float = Field(gt=0)
    coarse_step: float = Field(gt=0)


class SpeedConfig(BaseModel):
    """Jog speed configuration."""
    speed: float = Field(gt=0)


class MoveAbsoluteCommand(BaseModel):
    """Manual absolute XYZ move command."""
    x: float
    y: float
    z: float
    speed: float | None = Field(default=None, gt=0)


class ConnectRequest(BaseModel):
    """Stage connection request."""
    port: str | None = None
    baud_rate: int | None = Field(default=None, gt=0)


@router.get("/status")
def nanopositioner_status() -> dict:
    """Get current nanopositioner status."""
    status = nanopositioner_device.get_device_status()
    return {
        "ok": True,
        "implemented": True,
        "message": "Nanopositioner control available",
        "channel": oms_channel.status(),
        **status,
    }


@router.post("/connect")
def nanopositioner_connect(request: ConnectRequest) -> dict:
    """Connect the stage controller."""
    nanopositioner_device.connect(request.port, request.baud_rate)
    return {
        "ok": True,
        "implemented": True,
        "message": nanopositioner_device.get_status(),
        "connected": nanopositioner_device.connected,
        "channel": oms_channel.status(),
    }


@router.post("/disconnect")
def nanopositioner_disconnect() -> dict:
    """Disconnect the stage controller."""
    nanopositioner_device.disconnect()
    return {
        "ok": True,
        "implemented": True,
        "message": nanopositioner_device.get_status(),
        "connected": nanopositioner_device.connected,
        "channel": oms_channel.status(),
    }


@router.get("/firmware-version")
def nanopositioner_firmware_version() -> dict:
    """Read the firmware version from the stage controller."""
    major, minor, patch = nanopositioner_device.read_firmware_version()
    return {
        "ok": True,
        "implemented": True,
        "firmware_version": {"major": major, "minor": minor, "patch": patch},
    }


@router.get("/state-info")
def nanopositioner_state_info() -> dict:
    """Return raw controller state information when connected."""
    return {
        "ok": True,
        "implemented": True,
        "state_info": nanopositioner_device.read_device_state_info(),
    }


@router.post("/move")
def nanopositioner_move(cmd: MoveCommand) -> dict:
    """Move stage in specified direction."""
    if nanopositioner_device.is_motion_in_progress():
        return {
            "ok": False,
            "implemented": True,
            "message": "Stage is busy with another motion command",
            "position": nanopositioner_device.get_measured_position(),
        }
    move_result = nanopositioner_device.move(cmd.axis, cmd.direction, cmd.step_mode, cmd.step_value)
    move_ok = move_result.get("status") != "ERROR"
    return {
        "ok": move_ok,
        "implemented": True,
        "message": "Stage move applied" if move_ok else move_result.get("message", "Stage move failed"),
        "applied": move_result,
        "position": nanopositioner_device.get_measured_position(),
    }


@router.post("/home-axis")
def nanopositioner_home_axis(cmd: HomeAxisCommand) -> dict:
    """Home a single stage axis using OpenMicroStage axis list semantics."""
    if nanopositioner_device.is_motion_in_progress():
        return {
            "ok": False,
            "implemented": True,
            "message": "Stage is busy with another motion command",
            "position": nanopositioner_device.get_measured_position(),
        }
    result = nanopositioner_device.home_axis(cmd.axis)
    return {
        "ok": result.get("status") != "ERROR" if isinstance(result, dict) else True,
        "implemented": True,
        "message": f"Axis {cmd.axis.upper()} home requested",
        "result": result,
        "position": nanopositioner_device.get_measured_position(),
    }


@router.post("/home")
def nanopositioner_home() -> dict:
    """Reset stage to home position (0, 0, 0)."""
    if nanopositioner_device.is_motion_in_progress():
        return {
            "ok": False,
            "implemented": True,
            "message": "Stage is busy with another motion command",
            "position": nanopositioner_device.get_measured_position(),
        }
    nanopositioner_device.home()
    return {
        "ok": True,
        "implemented": True,
        "message": "Stage homed",
        "position": nanopositioner_device.get_measured_position(),
    }


@router.post("/stop")
def nanopositioner_stop() -> dict:
    """Stop stage movement."""
    return {
        "ok": True,
        "implemented": True,
        "message": "Stage stop requested",
    }


@router.post("/step-config")
def nanopositioner_step_config(config: StepConfig) -> dict:
    """Configure fine and coarse step sizes."""
    nanopositioner_device.set_step_sizes(config.fine_step, config.coarse_step)
    return {
        "ok": True,
        "implemented": True,
        "message": "Step sizes updated",
        "fine_step": nanopositioner_device.fine_step,
        "coarse_step": nanopositioner_device.coarse_step,
    }


@router.post("/speed")
def nanopositioner_set_speed(config: SpeedConfig) -> dict:
    """Set jog speed in mm/s."""
    nanopositioner_device.set_jog_speed(config.speed)
    return {
        "ok": True,
        "implemented": True,
        "message": "Jog speed updated",
        "jog_speed": nanopositioner_device.jog_speed,
    }


@router.post("/move-absolute")
def nanopositioner_move_absolute(cmd: MoveAbsoluteCommand) -> dict:
    """Move to an absolute XYZ position."""
    if nanopositioner_device.is_motion_in_progress():
        return {
            "ok": False,
            "implemented": True,
            "message": "Stage is busy with another motion command",
            "position": nanopositioner_device.get_measured_position(),
            "requested": {"x": cmd.x, "y": cmd.y, "z": cmd.z},
            "travel_range_mm": [nanopositioner_device.MIN_TRAVEL_MM, nanopositioner_device.MAX_TRAVEL_MM],
        }
    reply = nanopositioner_device.move_absolute(cmd.x, cmd.y, cmd.z, speed=cmd.speed)
    position = nanopositioner_device.get_measured_position()
    return {
        "ok": True,
        "implemented": True,
        "message": "Absolute move applied",
        "reply": getattr(reply, "name", str(reply)),
        "position": position,
        "requested": {"x": cmd.x, "y": cmd.y, "z": cmd.z},
        "travel_range_mm": [nanopositioner_device.MIN_TRAVEL_MM, nanopositioner_device.MAX_TRAVEL_MM],
    }

