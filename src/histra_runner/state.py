from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .jsonio import utc_now_iso, write_json_atomic


@dataclass
class StateStore:
    path: Path
    job_id: str
    attempt_id: str
    current: str = "received"
    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.transition(self.current)

    def transition(self, state: str, **details: Any) -> None:
        self.current = state
        event = {"state": state, "at": utc_now_iso()}
        if details:
            event["details"] = details
        self.history.append(event)
        write_json_atomic(
            self.path,
            {
                "job_id": self.job_id,
                "attempt_id": self.attempt_id,
                "state": self.current,
                "history": self.history,
            },
        )
