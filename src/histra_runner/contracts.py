from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HrxManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class BuilderProvenance(BaseModel):
    model_config = ConfigDict(extra="allow")
    builder_version: str
    job_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    template_id: str
    template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hrx_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_path: str


class PackageManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: Literal["1.0"]
    job_id: str
    attempt_id: str
    created_at: str
    job_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hrx: HrxManifest
    builder: BuilderProvenance

    @model_validator(mode="after")
    def provenance_is_self_consistent(self) -> "PackageManifest":
        if self.builder.job_sha256 != self.job_sha256:
            raise ValueError("builder JOB digest differs from manifest")
        if self.builder.hrx_sha256 != self.hrx.sha256:
            raise ValueError("builder HRX digest differs from manifest")
        if self.builder.output_path != self.hrx.path:
            raise ValueError("builder output path differs from manifest")
        return self


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    attempt_id: str
    job_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hrx_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lease_expires_at: str
    package_url: str


class ResultEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runner_id: str
    job_id: str
    attempt_id: str
    job_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hrx_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    results: dict[str, Any]
    run: dict[str, Any]
    logs: str = ""
