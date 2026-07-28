from __future__ import annotations

from pathlib import Path

from .jsonio import read_json, utc_now_iso, write_json_atomic


class StateStore:
    def __init__(self, path: Path, job_id: str, attempt_id: str):
        self.path = path
        self.data = {
            "job_id": job_id,
            "attempt_id": attempt_id,
            "state": "created",
            "updated_at": utc_now_iso(),
            "history": [],
        }
        write_json_atomic(path, self.data)

    def transition(self, state: str, **details: object) -> None:
        event = {"state": state, "at": utc_now_iso(), **details}
        self.data["state"] = state
        self.data["updated_at"] = event["at"]
        self.data["history"].append(event)
        write_json_atomic(self.path, self.data)
