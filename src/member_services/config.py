import json
import math
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    pack: Path
    upstream: str
    database: Path
    reps: dict = field(repr=False)
    upstream_tokens: dict = field(repr=False)
    directory_key: str = field(repr=False)
    model_url: str = ""
    model_name: str = ""
    model_key: str = field(default="", repr=False)
    turn_seconds: float = 3.0
    claim_seconds: float = 3.0
    max_actions: int = 32
    processing_seconds: float = 15.0
    model_budget: float = .02
    input_per_million: float = 0.0
    output_per_million: float = 0.0
    evaluation_as_of: date | None = None
    grievance_durable_replay: bool = False

    def __post_init__(self) -> None:
        for name in ("turn_seconds", "claim_seconds", "processing_seconds"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("model_budget", "input_per_million", "output_per_million"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")

    @property
    def knowledge_path(self) -> Path:
        return self.pack / "kb"

    @property
    def evaluation_path(self) -> Path:
        return self.pack / "eval" / "conversations.json"

    @property
    def mock_script(self) -> Path:
        return self.pack / "mock_services" / "server.py"

    @classmethod
    def from_env(cls) -> "Settings":
        as_of = os.getenv("EVALUATION_AS_OF")
        model_url = os.getenv("MODEL_URL", "")
        pricing = ("MODEL_INPUT_PER_MILLION", "MODEL_OUTPUT_PER_MILLION")
        if model_url and any(not os.getenv(name) for name in pricing):
            raise ValueError("Configure both model token prices explicitly when MODEL_URL is set")
        return cls(
            pack=Path(os.environ["ASSIGNMENT_PACK"]),
            upstream=os.getenv("MOCK_URL", "http://127.0.0.1:8090"),
            database=Path(os.getenv("STATE_DB", "state.sqlite")),
            reps=json.loads(os.environ["REP_CREDENTIALS_JSON"]),
            upstream_tokens=json.loads(os.environ["TENANT_TOKENS_JSON"]),
            directory_key=os.environ["DIRECTORY_KEY"],
            model_url=model_url,
            model_name=os.getenv("MODEL_NAME", ""),
            model_key=os.getenv("MODEL_KEY", ""),
            input_per_million=float(os.getenv("MODEL_INPUT_PER_MILLION", "0")),
            output_per_million=float(os.getenv("MODEL_OUTPUT_PER_MILLION", "0")),
            claim_seconds=float(os.getenv("CLAIM_SECONDS", "3")),
            evaluation_as_of=date.fromisoformat(as_of) if as_of else None,
            grievance_durable_replay=os.getenv("GRIEVANCE_DURABLE_REPLAY") == "true",
        )
