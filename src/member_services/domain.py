"""Validated values shared at the application boundary."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SPECIALTIES = {"dermatolog": "Dermatology", "cardiolog": "Cardiology", "neurolog": "Neurology",
               "pediatric": "Pediatrics", "orthoped": "Orthopedics", "primary care": "Primary Care"}


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member_id: str = Field(pattern=r"^M[0-9]{4,20}$")


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    turn_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    action: Literal["message", "confirm", "cancel"] = "message"
    message: str | None = Field(default=None, min_length=1, max_length=4000, repr=False)
    draft_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class Health(BaseModel):
    status: Literal["ok"] = "ok"


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["benefit", "claim", "claim_count", "appeal", "grievance_status",
                       "draft", "verify", "decline", "provider", "unavailable"]
    subject: Literal["specialist", "emergency", "generic", "video", "therapy",
                     "virtual_behavioral", "urgent", "primary", "deductible", "oop", "pt_limit",
                     "other"] = "other"
