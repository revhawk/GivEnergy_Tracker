"""Pydantic v2 Data Contracts & Schemas — GivEnergy Tariff Optimiser.

Single source of truth for all external API payload schemas, inverter telemetry,
tariff rate structures, and ChatGPT LLM response validation.
"""
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class GivTCPWriteSlot(BaseModel):
    """Payload contract for POST /setChargeSlot on GivTCP API."""
    start: str = Field(..., description="Start time in HH:MM format", json_schema_extra={"example": "02:30"})
    finish: str = Field(..., description="Finish time in HH:MM format", json_schema_extra={"example": "04:00"})
    slot: str = Field(..., description="Slot index string ('1' to '10')", json_schema_extra={"example": "1"})
    chargeToPercent: Optional[int] = Field(None, description="Target charge percentage (1-100)", json_schema_extra={"example": 100})

    @field_validator("slot")
    def validate_slot_index(cls, v: str) -> str:
        if not v.isdigit() or not (1 <= int(v) <= 10):
            raise ValueError(f"Slot must be a string integer between 1 and 10, got {v}")
        return v


class GivTCPTarget(BaseModel):
    """Payload contract for POST /setChargeTarget on GivTCP API."""
    chargeToPercent: int = Field(..., ge=0, le=100, description="Battery target SoC %", json_schema_extra={"example": 100})


class GivTCPBatteryMode(BaseModel):
    """Payload contract for POST /setBatteryMode on GivTCP API."""
    mode: str = Field(..., description="Inverter battery mode: 'Timed Demand' or 'Eco'", json_schema_extra={"example": "Timed Demand"})


class InverterTelemetry(BaseModel):
    """Inverter live telemetry contract parsed from GivTCP GET /getCache."""
    soc: float = Field(..., ge=0.0, le=100.0, description="Battery state of charge %", json_schema_extra={"example": 45.0})
    pv_power: float = Field(..., ge=0.0, description="Solar PV generation power in Watts", json_schema_extra={"example": 1200.0})
    load_power: float = Field(..., ge=0.0, description="Home electrical demand power in Watts", json_schema_extra={"example": 450.0})


class OctopusRateSlot(BaseModel):
    """Octopus Agile import or Outgoing export half-hour rate contract."""
    valid_from: str = Field(..., description="Slot start time in ISO 8601 UTC format")
    valid_to: str = Field(..., description="Slot end time in ISO 8601 UTC format")
    value_inc_vat: float = Field(..., description="Electricity price in pence per kWh", json_schema_extra={"example": 15.42})


class LLMVetoDecision(BaseModel):
    """ChatGPT plan evaluation JSON response contract."""
    approve: bool = Field(..., description="Whether ChatGPT approves the grid charge plan")
    score: int = Field(..., ge=1, le=10, description="Plan economic quality score (1 to 10)")
    reason: str = Field(..., min_length=5, description="Plain-English explanation of decision")


class HiveHotWaterState(BaseModel):
    """Decoupled Hive hot water controller payload contract."""
    mode: str = Field(..., description="Operation mode: 'off' or 'schedule'", json_schema_extra={"example": "off"})
    tank_temperature: Optional[float] = Field(None, description="Cylinder temperature in °C if sensor present", json_schema_extra={"example": 48.5})
