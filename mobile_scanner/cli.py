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
    parser = argparse.ArgumentParser(description="Identify mobile-specific Azure DevOps repository branches.")
    parser.add_argument("--org", required=True, help="Azure DevOps organization name")
    parser.add_argument("--project", help="Optional project name. Omit to scan all projects.")
    parser.add_argument(
        "--pat",
        default=os.getenv("ADO_PAT"),
        help="Azure DevOps PAT. Prefer setting ADO_PAT instead of passing this on the command line.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path.cwd(),
        help="Directory for CSV, JSON, and Excel reports. Defaults to the current directory.",
    )
    parser.add_argument(
        "--out-prefix",
        default="ado_mobile_repos",
        help="Output file prefix. Defaults to ado_mobile_repos.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help=f"Maximum concurrent repository branch-listing requests. Defaults to {DEFAULT_MAX_WORKERS}.",
    )
    parser.add_argument(
        "--branch-workers",
        type=int,
        default=DEFAULT_BRANCH_WORKERS,
        help=f"Maximum concurrent branch scans. Defaults to {DEFAULT_BRANCH_WORKERS}.",
    )
    parser.add_argument(
        "--content-workers",
        type=int,
        default=DEFAULT_CONTENT_WORKERS,
        help=(
            "Maximum concurrent config/manifest file fetches across all repository branches. "
            f"Defaults to {DEFAULT_CONTENT_WORKERS}."
        ),
    )
    parser.add_argument(
        "--max-commits-per-repo",
        type=int,
        default=0,
        help=(
            "Maximum commits to inspect per matched branch for contributors. "
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

    return ScanConfig(
        org=args.org,
        pat=args.pat,
        project=args.project,
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
    )


def validate_args(args: argparse.Namespace) -> None:
    if not args.pat:
        raise SystemExit("Missing Azure DevOps PAT. Set ADO_PAT or pass --pat.")
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
