"""
Vacuum Pump Control Router
Provides API endpoints for vacuum pump on/off control.
Hardware driver implementation to be completed.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from devices.vacuum import VacuumDevice

router = APIRouter(prefix="/api/vacuum", tags=["vacuum"])

# Global vacuum device instance
vacuum_device = VacuumDevice()


class PowerControlRequest(BaseModel):
    """Request model for power control."""
    enabled: bool = Field(..., description="True to turn on, False to turn off")


@router.get("/status")
async def get_vacuum_status():
    """Get current vacuum pump status."""
    status = vacuum_device.get_device_status()
    return {
        "ok": True,
        "implemented": False,
        "message": "Hardware driver not implemented yet",
        **status,
    }


@router.post("/power")
async def control_power(request: PowerControlRequest):
    """Turn vacuum pump on or off."""
    vacuum_device.set_power(request.enabled)
    return {
        "ok": True,
        "implemented": False,
        "message": "Hardware driver not implemented yet",
        "is_on": vacuum_device.get_power(),
    }
