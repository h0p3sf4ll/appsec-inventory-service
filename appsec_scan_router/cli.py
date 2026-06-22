from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .constants import (
    DEFAULT_ACTIVITY_MODE,
    DEFAULT_BRANCH_AGE_DAYS,
    DEFAULT_BRANCH_WORKERS,
    DEFAULT_CONTENT_WORKERS,
    DEFAULT_MAX_WORKERS,
    DEFAULT_STORE_COUNTRY,
    DEFAULT_STORE_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
)
from .models import ScanConfig
from .scanner import scan_to_reports


def parse_args(argv: list[str]) -> ScanConfig:
    parser = argparse.ArgumentParser(
        prog="appsec-scan-router",
        description="Identify mobile apps across Azure DevOps or GitHub Enterprise repositories.",
    )
    parser.add_argument(
        "--provider",
        choices=("azure-devops", "github-enterprise"),
        default=os.getenv("APPSEC_SCAN_PROVIDER", "azure-devops"),
        help="Source provider. Defaults to azure-devops.",
    )
    parser.add_argument("--org", required=True, help="Azure DevOps organization or GitHub owner.")
    parser.add_argument("--project", help="Azure DevOps project or GitHub repository name.")
    parser.add_argument("--repo", help="GitHub repository name. Alias for --project.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("APPSEC_SCAN_BASE_URL") or os.getenv("GITHUB_API_URL") or os.getenv("GHE_API_URL") or "",
        help="GitHub Enterprise API URL, for example https://github.example.com/api/v3.",
    )
    parser.add_argument(
        "--pat",
        help="Provider token. Prefer ADO_PAT for Azure DevOps or GITHUB_TOKEN/GHE_TOKEN for GitHub Enterprise.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path.cwd(),
        help="Directory for CSV, JSON, and Excel reports. Defaults to the current directory.",
    )
    parser.add_argument(
        "--out-prefix",
        default="appsec_scan_router",
        help="Output file prefix. Defaults to appsec_scan_router.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help=f"Maximum concurrent repository preparation tasks. Defaults to {DEFAULT_MAX_WORKERS}.",
    )
    parser.add_argument(
        "--branch-workers",
        type=int,
        default=DEFAULT_BRANCH_WORKERS,
        help=f"Maximum concurrent resolved-branch scans. Defaults to {DEFAULT_BRANCH_WORKERS}.",
    )
    parser.add_argument(
        "--content-workers",
        type=int,
        default=DEFAULT_CONTENT_WORKERS,
        help=(
            "Maximum concurrent config/manifest file fetches across resolved repository branches. "
            f"Defaults to {DEFAULT_CONTENT_WORKERS}."
        ),
    )
    parser.add_argument(
        "--max-commits-per-repo",
        type=int,
        default=0,
        help=(
            "Maximum commits to inspect per matched resolved branch for contributors. "
            "Use 0 for all available history. Defaults to 0."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP request timeout in seconds. Defaults to {DEFAULT_TIMEOUT_SECONDS}.",
    )
    parser.add_argument(
        "--min-confidence",
        choices=("low", "medium", "high"),
        default="low",
        help="Minimum confidence to include in reports. Defaults to low.",
    )
    parser.add_argument(
        "--branch-age-days",
        type=int,
        default=DEFAULT_BRANCH_AGE_DAYS,
        help=f"Age cutoff for workbook active/older branch sheets. Defaults to {DEFAULT_BRANCH_AGE_DAYS}.",
    )
    parser.add_argument(
        "--activity-mode",
        choices=("contributors", "latest"),
        default=DEFAULT_ACTIVITY_MODE,
        help=(
            "Commit activity mode. Use contributors for full contributor extraction, "
            "or latest for fast latest-commit-only activity. Defaults to contributors."
        ),
    )
    parser.add_argument(
        "--store-lookup",
        action="store_true",
        help="Enable public Apple App Store and Google Play enrichment from detected app identifiers.",
    )
    parser.add_argument(
        "--store-country",
        default=DEFAULT_STORE_COUNTRY,
        help=f"Two-letter store country code for public lookups. Defaults to {DEFAULT_STORE_COUNTRY}.",
    )
    parser.add_argument(
        "--store-timeout",
        type=int,
        default=DEFAULT_STORE_TIMEOUT_SECONDS,
        help=f"Store lookup HTTP timeout in seconds. Defaults to {DEFAULT_STORE_TIMEOUT_SECONDS}.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args(argv)

    configure_logging(args.verbose)
    validate_args(args)
    token = provider_token(args)
    target_project = provider_project(args)

    return ScanConfig(
        org=args.org,
        pat=token,
        project=target_project,
        out_dir=args.out_dir,
        out_prefix=args.out_prefix,
        max_workers=args.max_workers,
        branch_workers=args.branch_workers,
        content_workers=args.content_workers,
        max_commits_per_repo=args.max_commits_per_repo,
        timeout_seconds=args.timeout,
        min_confidence=args.min_confidence,
        branch_age_days=args.branch_age_days,
        activity_mode=args.activity_mode,
        store_lookup=args.store_lookup,
        store_country=args.store_country.strip().upper(),
        store_timeout_seconds=args.store_timeout,
        provider=args.provider,
        base_url=args.base_url,
    )


def validate_args(args: argparse.Namespace) -> None:
    if args.project and args.repo and args.project != args.repo:
        raise SystemExit("--project and --repo cannot refer to different repositories.")
    if not provider_token(args):
        raise SystemExit(provider_token_message(args.provider))
    if args.provider == "github-enterprise" and not args.base_url:
        raise SystemExit("Missing GitHub Enterprise API URL. Set --base-url or GITHUB_API_URL.")
    if args.max_workers < 1:
        raise SystemExit("--max-workers must be at least 1.")
    if args.branch_workers < 1:
        raise SystemExit("--branch-workers must be at least 1.")
    if args.content_workers < 1:
        raise SystemExit("--content-workers must be at least 1.")
    if args.max_commits_per_repo < 0:
        raise SystemExit("--max-commits-per-repo must be 0 or greater.")
    if args.timeout < 1:
        raise SystemExit("--timeout must be at least 1.")
    if args.branch_age_days < 1:
        raise SystemExit("--branch-age-days must be at least 1.")
    store_country = args.store_country.strip()
    if len(store_country) != 2 or not store_country.isalpha():
        raise SystemExit("--store-country must be a two-letter country code.")
    if args.store_timeout < 1:
        raise SystemExit("--store-timeout must be at least 1.")


def provider_project(args: argparse.Namespace) -> str | None:
    return args.project or args.repo


def provider_token(args: argparse.Namespace) -> str:
    if args.pat:
        return args.pat
    if args.provider == "github-enterprise":
        return os.getenv("GITHUB_TOKEN") or os.getenv("GHE_TOKEN") or ""
    return os.getenv("ADO_PAT") or ""


def provider_token_message(provider: str) -> str:
    if provider == "github-enterprise":
        return "Missing GitHub token. Set GITHUB_TOKEN, GHE_TOKEN, or pass --pat."
    return "Missing Azure DevOps PAT. Set ADO_PAT or pass --pat."


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(sys.argv[1:] if argv is None else argv)
    results, csv_path, json_path, xlsx_path = scan_to_reports(config)
    print(f"Done. Found {len(results)} mobile-specific app branches.")
    print(f"CSV:  {csv_path}")
    print(f"JSON: {json_path}")
    print(f"XLSX: {xlsx_path}")
    return 0
