"""
Nanopositioner Control Router
Provides API endpoints for 3-axis stage control.
Hardware driver implementation to be completed.
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from devices.nanopositioner import NanopositionerDevice

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


@router.get("/status")
def nanopositioner_status() -> dict:
    """Get current nanopositioner status."""
    status = nanopositioner_device.get_device_status()
    return {
        "ok": True,
        "implemented": False,
        "message": "Hardware driver not implemented yet",
        **status,
    }


@router.post("/move")
def nanopositioner_move(cmd: MoveCommand) -> dict:
    """Move stage in specified direction."""
    move_result = nanopositioner_device.move(cmd.axis, cmd.direction, cmd.step_mode)
    return {
        "ok": True,
        "implemented": False,
        "message": "Hardware driver not implemented yet",
        "applied": move_result,
        "position": nanopositioner_device.get_position(),
    }


@router.post("/home")
def nanopositioner_home() -> dict:
    """Reset stage to home position (0, 0, 0)."""
    nanopositioner_device.home()
    return {
        "ok": True,
        "implemented": False,
        "message": "Hardware driver not implemented yet",
        "position": nanopositioner_device.get_position(),
    }


@router.post("/stop")
def nanopositioner_stop() -> dict:
    """Stop stage movement."""
    return {
        "ok": True,
        "implemented": False,
        "message": "Hardware driver not implemented yet",
    }


@router.post("/step-config")
def nanopositioner_step_config(config: StepConfig) -> dict:
    """Configure fine and coarse step sizes."""
    nanopositioner_device.set_step_sizes(config.fine_step, config.coarse_step)
    return {
        "ok": True,
        "implemented": False,
        "message": "Hardware driver not implemented yet",
        "fine_step": nanopositioner_device.fine_step,
        "coarse_step": nanopositioner_device.coarse_step,
    }

