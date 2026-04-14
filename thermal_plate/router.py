"""
Thermal Plate Control Router
Provides API endpoints for precise temperature control and monitoring.
Hardware driver implementation to be completed.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from devices.thermal import ThermalPlateDevice

router = APIRouter(prefix="/api/thermal", tags=["thermal"])

# Global thermal device instance
thermal_device = ThermalPlateDevice()


class SetTemperatureRequest(BaseModel):
    """Request model for setting temperature."""
    target_temperature: float = Field(..., ge=0, le=100, description="Target temperature in Celsius")


class PowerControlRequest(BaseModel):
    """Request model for power control."""
    enabled: bool = Field(..., description="True to turn on, False to turn off")


@router.get("/status")
async def get_thermal_status():
    """Get current thermal plate status including temperature and power state."""
    status = thermal_device.get_device_status()
    return {
        "ok": True,
        "implemented": True,
        "message": "Thermal device available via shared hardware channel",
        **status,
    }


@router.post("/set-temperature")
async def set_temperature(request: SetTemperatureRequest):
    """Set target temperature for the thermal plate."""
    thermal_device.set_temperature(request.target_temperature)
    return {
        "ok": True,
        "implemented": True,
        "message": "Temperature target updated",
        "target_temperature": thermal_device.get_target_temp(),
    }


@router.post("/power")
async def control_power(request: PowerControlRequest):
    """Turn thermal plate on or off."""
    thermal_device.set_power(request.enabled)
    if not request.enabled:
        thermal_device.set_temperature(0.0)
    return {
        "ok": True,
        "implemented": True,
        "message": "Thermal plate power updated",
        "is_on": thermal_device.get_power(),
        "target_temperature": thermal_device.get_target_temp(),
    }

