"""Rotation Plate Control Router.
Provides API endpoints for rotation control and monitoring.
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from devices.rotation import RotationPlateDevice

router = APIRouter(prefix="/api/rotation", tags=["rotation"])
rotation_device = RotationPlateDevice()


class SetRotationRequest(BaseModel):
    """Request model for setting absolute angle."""
    angle: float = Field(..., description="Target angle in degrees")


class NudgeRotationRequest(BaseModel):
    """Request model for relative step rotation."""
    direction: Literal["left", "right"]
    step_degrees: float = Field(default=5.0, gt=0)


@router.get("/status")
async def get_rotation_status() -> dict:
    """Get current rotation plate status."""
    status = rotation_device.get_device_status()
    return {
        "ok": True,
        "implemented": True,
        "message": "Rotation device available via shared hardware channel",
        **status,
    }


@router.post("/set")
async def set_rotation(request: SetRotationRequest) -> dict:
    """Set absolute rotation angle."""
    rotation_device.set_rotation(request.angle)
    status = rotation_device.get_device_status()
    return {
        "ok": True,
        "implemented": True,
        "message": "Rotation angle updated",
        "current_angle": status["current_angle"],
        "target_angle": status["target_angle"],
    }


@router.post("/nudge")
async def nudge_rotation(request: NudgeRotationRequest) -> dict:
    """Rotate left/right by a configurable step in degrees."""
    direction_sign = -1.0 if request.direction == "left" else 1.0
    new_target = rotation_device.nudge(direction_sign * request.step_degrees)
    return {
        "ok": True,
        "implemented": True,
        "message": f"Rotation nudged {request.direction}",
        "target_angle": new_target,
    }
