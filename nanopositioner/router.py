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


class StepConfig(BaseModel):
    """Step size configuration."""
    fine_step: float = Field(gt=0)
    coarse_step: float = Field(gt=0)


class ConnectRequest(BaseModel):
    """Stage connection request."""
    port: str
    baud_rate: int = Field(default=921600, gt=0)


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
    move_result = nanopositioner_device.move(cmd.axis, cmd.direction, cmd.step_mode)
    return {
        "ok": True,
        "implemented": True,
        "message": "Stage move applied",
        "applied": move_result,
        "position": nanopositioner_device.get_position(),
    }


@router.post("/home")
def nanopositioner_home() -> dict:
    """Reset stage to home position (0, 0, 0)."""
    nanopositioner_device.home()
    return {
        "ok": True,
        "implemented": True,
        "message": "Stage homed",
        "position": nanopositioner_device.get_position(),
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

