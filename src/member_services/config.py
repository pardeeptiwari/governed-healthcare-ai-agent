import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    pack: Path
    upstream: str
    database: Path
    reps: dict
    upstream_tokens: dict
    directory_key: str
    model_url: str = ""
    model_name: str = ""
    model_key: str = ""
    turn_seconds: float = 3.0
    claim_seconds: float = 3.0
    max_actions: int = 32
    processing_seconds: float = 15.0
    model_budget: float = .02
    input_per_million: float = 0.0
    output_per_million: float = 0.0

    @classmethod
    def from_env(cls):
        return cls(
            pack=Path(os.environ["ASSIGNMENT_PACK"]),
            upstream=os.getenv("MOCK_URL", "http://127.0.0.1:8090"),
            database=Path(os.getenv("STATE_DB", "state.sqlite")),
            reps=json.loads(os.environ["REP_CREDENTIALS_JSON"]),
            upstream_tokens=json.loads(os.environ["TENANT_TOKENS_JSON"]),
            directory_key=os.environ["DIRECTORY_KEY"],
            model_url=os.getenv("MODEL_URL", ""),
            model_name=os.getenv("MODEL_NAME", ""),
            model_key=os.getenv("MODEL_KEY", ""),
            input_per_million=float(os.getenv("MODEL_INPUT_PER_MILLION", "0")),
            output_per_million=float(os.getenv("MODEL_OUTPUT_PER_MILLION", "0")),
            claim_seconds=float(os.getenv("CLAIM_SECONDS", "3")),
        )
