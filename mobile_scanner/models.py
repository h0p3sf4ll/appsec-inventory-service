from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import (
    DEFAULT_ACTIVITY_MODE,
    DEFAULT_BRANCH_AGE_DAYS,
    DEFAULT_BRANCH_WORKERS,
    DEFAULT_STORE_COUNTRY,
    DEFAULT_STORE_TIMEOUT_SECONDS,
)


class AzureDevOpsError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ScanConfig:
    org: str
    pat: str
    project: str | None
    out_dir: Path
    out_prefix: str
    max_workers: int
    content_workers: int
    max_commits_per_repo: int
    timeout_seconds: int
    min_confidence: str
    branch_workers: int = DEFAULT_BRANCH_WORKERS
    branch_age_days: int = DEFAULT_BRANCH_AGE_DAYS
    activity_mode: str = DEFAULT_ACTIVITY_MODE
    store_lookup: bool = False
    store_country: str = DEFAULT_STORE_COUNTRY
    store_timeout_seconds: int = DEFAULT_STORE_TIMEOUT_SECONDS


@dataclass(frozen=True)
class RepoScanTarget:
    project_name: str
    repo: dict[str, Any]


@dataclass(frozen=True)
class BranchScanTarget:
    project_name: str
    repo: dict[str, Any]
    branch_name: str


@dataclass(frozen=True)
class MobileAppMetadata:
    name: str = ""
    version: str = ""
    identifier: str = ""
    identifier_source: str = ""


@dataclass(frozen=True)
class RepoActivityMetadata:
    contributing_developers: tuple[str, ...] = ()
    last_updated: str = ""


@dataclass(frozen=True)
class StoreListing:
    platform: str
    status: str
    name: str = ""
    identifier: str = ""
    url: str = ""
    version: str = ""
    last_updated: str = ""
    error: str = ""


@dataclass(frozen=True)
class DetectionEvidence:
    category: str
    source: str
    detail: str
    weight: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "source": self.source,
            "detail": self.detail,
            "weight": self.weight,
        }
