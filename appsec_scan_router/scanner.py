from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .activity import extract_repo_activity, parse_ado_datetime
from .azure import AzureDevOpsClient
from .constants import (
    DEFAULT_ACTIVITY_MODE,
    FALLBACK_BRANCH_PRIORITY,
    KNOWN_CATEGORIES,
    active_sheet_name,
    older_sheet_name,
)
from .detection import detect_mobile_repo
from .github import GitHubEnterpriseClient
from .metadata import extract_mobile_metadata
from .models import (
    AzureDevOpsError,
    BranchScanTarget,
    DetectionEvidence,
    MobileAppMetadata,
    RepoActivityMetadata,
    RepoScanTarget,
    ScanConfig,
)
from .reports import StreamingReportWriter
from .store_lookup import StoreLookupClient, store_columns
from .utils import confidence_rank, should_fetch_content


LOGGER = logging.getLogger("appsec_scan_router")


def scan_to_reports(config: ScanConfig) -> tuple[list[dict[str, Any]], Path, Path, Path]:
    with StreamingReportWriter(config.out_dir, config.out_prefix, config.branch_age_days) as writer:
        LOGGER.info("Streaming CSV report to %s", writer.csv_path)
        LOGGER.info("Streaming JSON report to %s", writer.json_path)
        LOGGER.info("Streaming Excel report to %s", writer.xlsx_path)
        results = scan(config, on_result=writer.write_result)
        return results, writer.csv_path, writer.json_path, writer.xlsx_path


def scan(
    config: ScanConfig,
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    start = time.monotonic()
    client = create_source_client(config)
    store_client = create_store_client(config)
    try:
        targets = collect_targets(client, config.project)
        LOGGER.info("Scanning resolved default or fallback branches for %s repositories", len(targets))

        results: list[dict[str, Any]] = []
        repo_workers = max(1, min(config.max_workers, len(targets) or 1))
        branch_workers = max(1, config.branch_workers)
        content_workers = max(1, config.content_workers)
        min_rank = confidence_rank(config.min_confidence)

        with (
            ThreadPoolExecutor(max_workers=repo_workers) as repo_executor,
            ThreadPoolExecutor(max_workers=branch_workers) as branch_executor,
            ThreadPoolExecutor(max_workers=content_workers) as content_executor,
        ):
            completed_branch_lists = iter_completed_branch_target_lists(
                repo_executor=repo_executor,
                client=client,
                targets=targets,
                max_in_flight=max(repo_workers * 4, repo_workers),
            )
            pending_branch_scans: set[Future[dict[str, Any] | None]] = set()
            submitted_branches = 0
            completed_branches = 0

            for repo_index, future in completed_branch_lists:
                try:
                    branch_targets = future.result()
                except Exception as exc:
                    LOGGER.warning("Failed to resolve repository branch: %s", exc)
                    continue

                for branch_target in branch_targets:
                    while len(pending_branch_scans) >= max(branch_workers * 4, branch_workers):
                        completed_branches += drain_branch_scans(
                            pending_branch_scans=pending_branch_scans,
                            results=results,
                            on_result=on_result,
                            block=True,
                        )

                    pending_branch_scans.add(
                        branch_executor.submit(
                            scan_branch_target,
                            client,
                            branch_target,
                            content_executor,
                            min_rank,
                            config.max_commits_per_repo,
                            config.branch_age_days,
                            config.activity_mode,
                            store_client,
                        )
                    )
                    submitted_branches += 1

                completed_branches += drain_branch_scans(
                    pending_branch_scans=pending_branch_scans,
                    results=results,
                    on_result=on_result,
                    block=False,
                )

                if repo_index % 25 == 0:
                    LOGGER.info(
                        "Progress: %s/%s repositories prepared; %s/%s resolved branches scanned",
                        repo_index,
                        len(targets),
                        completed_branches,
                        submitted_branches,
                    )

            while pending_branch_scans:
                completed_branches += drain_branch_scans(
                    pending_branch_scans=pending_branch_scans,
                    results=results,
                    on_result=on_result,
                    block=True,
                )
                if completed_branches % 100 == 0:
                    LOGGER.info("Progress: %s/%s resolved branches scanned", completed_branches, submitted_branches)

        results.sort(key=row_sort_key)
        LOGGER.info("Finished in %.1fs; found %s app branches", time.monotonic() - start, len(results))
        return results
    finally:
        client.close()
        if store_client:
            store_client.close()


def create_source_client(config: ScanConfig) -> AzureDevOpsClient | GitHubEnterpriseClient:
    if config.provider == "github-enterprise":
        return GitHubEnterpriseClient(
            base_url=config.base_url,
            owner=config.org,
            token=config.pat,
            timeout_seconds=config.timeout_seconds,
        )
    return AzureDevOpsClient(config.org, config.pat, config.timeout_seconds)


def drain_branch_scans(
    pending_branch_scans: set[Future[dict[str, Any] | None]],
    results: list[dict[str, Any]],
    on_result: Callable[[dict[str, Any]], None] | None,
    block: bool,
) -> int:
    if not pending_branch_scans:
        return 0

    done, pending = wait(
        pending_branch_scans,
        timeout=0 if not block else None,
        return_when=FIRST_COMPLETED,
    )
    pending_branch_scans.clear()
    pending_branch_scans.update(pending)

    for future in done:
        result = handle_branch_scan_future(future, on_result)
        if result:
            results.append(result)

    return len(done)


def handle_branch_scan_future(
    future: Future[dict[str, Any] | None],
    on_result: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any] | None:
    try:
        result = future.result()
    except Exception as exc:
        LOGGER.warning("Failed to scan branch: %s", exc)
        return None

    if result and on_result:
        on_result(result)
    if result:
        log_detected_result(result)
    return result


def scan_branch_target(
    client: AzureDevOpsClient,
    target: BranchScanTarget,
    content_executor: ThreadPoolExecutor,
    min_confidence_rank: int,
    max_commits_per_repo: int,
    branch_age_days: int,
    activity_mode: str,
    store_client: StoreLookupClient | None,
) -> dict[str, Any] | None:
    return scan_branch(
        client=client,
        target=RepoScanTarget(project_name=target.project_name, repo=target.repo),
        branch_name=target.branch_name,
        content_executor=content_executor,
        min_confidence_rank=min_confidence_rank,
        max_commits_per_repo=max_commits_per_repo,
        branch_age_days=branch_age_days,
        activity_mode=activity_mode,
        store_client=store_client,
    )


def scan_repo(
    client: AzureDevOpsClient,
    target: RepoScanTarget,
    content_executor: ThreadPoolExecutor,
    min_confidence_rank: int,
    max_commits_per_repo: int,
    branch_age_days: int,
    store_client: StoreLookupClient | None,
    activity_mode: str = DEFAULT_ACTIVITY_MODE,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for branch_target in list_branch_targets(client, target):
        try:
            row = scan_branch_target(
                client=client,
                target=branch_target,
                content_executor=content_executor,
                min_confidence_rank=min_confidence_rank,
                max_commits_per_repo=max_commits_per_repo,
                branch_age_days=branch_age_days,
                activity_mode=activity_mode,
                store_client=store_client,
            )
        except AzureDevOpsError as exc:
            LOGGER.info(
                "Skipping branch %s/%s@%s: %s",
                branch_target.project_name,
                branch_target.repo.get("name", ""),
                branch_target.branch_name,
                exc,
            )
            continue
        if row:
            rows.append(row)
    return rows


def list_branch_targets(
    client: AzureDevOpsClient,
    target: RepoScanTarget,
) -> list[BranchScanTarget]:
    repo = target.repo
    repo_id = repo.get("id", "")
    repo_name = repo.get("name", "")

    if not repo_id:
        LOGGER.warning("Skipping repo without id in project %s: %s", target.project_name, repo)
        return []
    if repo.get("isDisabled"):
        LOGGER.info("Skipping disabled repo: %s/%s", target.project_name, repo_name)
        return []

    branch_name = default_branch_name_from_repo(repo)
    if not branch_name:
        branch_name = fallback_branch_name(client, target)
    if not branch_name:
        LOGGER.info(
            "Skipping repo without a scannable default or fallback branch: %s/%s",
            target.project_name,
            repo_name,
        )
        return []

    return [
        BranchScanTarget(
            project_name=target.project_name,
            repo=repo,
            branch_name=branch_name,
        )
    ]


def scan_branch(
    client: AzureDevOpsClient,
    target: RepoScanTarget,
    branch_name: str,
    content_executor: ThreadPoolExecutor,
    min_confidence_rank: int,
    max_commits_per_repo: int,
    branch_age_days: int,
    activity_mode: str,
    store_client: StoreLookupClient | None,
) -> dict[str, Any] | None:
    repo = target.repo
    repo_id = repo.get("id", "")
    repo_name = repo.get("name", "")

    try:
        items = client.list_repo_items(target.project_name, repo_id, branch_name)
    except AzureDevOpsError as exc:
        if exc.status_code == 404:
            LOGGER.debug("Skipping unavailable branch contents: %s/%s@%s", target.project_name, repo_name, branch_name)
            return None
        raise

    paths = [item.get("path", "") for item in items if item.get("path")]
    if not paths:
        LOGGER.debug("Skipping empty branch: %s/%s@%s", target.project_name, repo_name, branch_name)
        return None

    content_paths = [path for path in paths if should_fetch_content(path)]
    contents = fetch_contents(client, target.project_name, repo_id, branch_name, content_paths, content_executor)
    confidence, evidence, score = detect_mobile_repo(paths, contents)
    metadata = extract_mobile_metadata(contents)

    if confidence == "none" or confidence_rank(confidence) < min_confidence_rank:
        LOGGER.debug("No match: %s/%s@%s", target.project_name, repo_name, branch_name)
        return None

    activity = fetch_repo_activity(
        client=client,
        project_name=target.project_name,
        repo_id=repo_id,
        branch_name=branch_name,
        max_commits=max_commits_per_repo,
        activity_mode=activity_mode,
    )
    categories = sorted({item.category for item in evidence})

    return build_scan_row(
        target=target,
        branch_name=branch_name,
        metadata=metadata,
        activity=activity,
        confidence=confidence,
        score=score,
        categories=categories,
        evidence=evidence,
        branch_age_days=branch_age_days,
        store_client=store_client,
    )


def build_scan_row(
    target: RepoScanTarget,
    branch_name: str,
    metadata: MobileAppMetadata,
    activity: RepoActivityMetadata,
    confidence: str,
    score: int,
    categories: list[str],
    evidence: list[DetectionEvidence],
    branch_age_days: int,
    store_client: StoreLookupClient | None,
) -> dict[str, Any]:
    repo = target.repo
    age_bucket = branch_age_bucket(activity.last_updated, branch_age_days)
    store_metadata = store_columns(metadata.identifier, categories, store_client)
    return {
        "project": target.project_name,
        "repo_name": repo.get("name", ""),
        "branch_name": branch_name,
        "branch_last_updated": activity.last_updated,
        "branch_age_bucket": age_bucket,
        "web_url": repo.get("webUrl", ""),
        "mobile_name": metadata.name,
        "mobile_version": metadata.version,
        "mobile_identifier": metadata.identifier,
        "mobile_identifier_source": metadata.identifier_source,
        "mobile_identifier_status": identifier_status(metadata.identifier),
        "contributing_developers": "; ".join(activity.contributing_developers),
        "last_updated": activity.last_updated,
        "confidence": confidence,
        "score": score,
        "categories": "; ".join(categories),
        **category_columns(categories),
        **store_metadata,
        "detection_evidence": json.dumps([item.as_dict() for item in evidence], sort_keys=True),
    }


def row_sort_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("project", "")).lower(),
        str(row.get("repo_name", "")).lower(),
        str(row.get("branch_name", "")).lower(),
    )


def log_detected_result(result: dict[str, Any]) -> None:
    LOGGER.info(
        "DETECTED app=%s version=%s id=%s confidence=%s repo=%s/%s branch=%s age=%s categories=%s",
        result["mobile_name"] or "(unknown)",
        result["mobile_version"] or "(unknown)",
        result["mobile_identifier"] or "(unknown)",
        result["confidence"],
        result["project"],
        result["repo_name"],
        result["branch_name"],
        result["branch_age_bucket"],
        result["categories"],
    )


def create_store_client(config: ScanConfig) -> StoreLookupClient | None:
    if not config.store_lookup:
        return None
    return StoreLookupClient(config.store_country, config.store_timeout_seconds)


def branch_name_from_ref(ref_name: str) -> str:
    prefix = "refs/heads/"
    if ref_name.startswith(prefix):
        return ref_name[len(prefix):]
    return ref_name


def default_branch_name_from_repo(repo: dict[str, Any]) -> str:
    return branch_name_from_ref(str(repo.get("defaultBranch") or ""))


def fallback_branch_name(client: AzureDevOpsClient, target: RepoScanTarget) -> str:
    repo = target.repo
    repo_id = str(repo.get("id") or "")
    repo_name = str(repo.get("name") or "")
    try:
        refs = client.list_branches(target.project_name, repo_id)
    except AzureDevOpsError as exc:
        LOGGER.info("Could not list fallback branches for %s/%s: %s", target.project_name, repo_name, exc)
        return ""

    branch_names = branch_names_from_refs(refs)
    if not branch_names:
        LOGGER.info("No fallback branches found for %s/%s", target.project_name, repo_name)
        return ""

    pipeline_branch = pipeline_fallback_branch_name(client, target, branch_names)
    if pipeline_branch:
        LOGGER.info(
            "Using pipeline-associated fallback branch for %s/%s: %s",
            target.project_name,
            repo_name,
            pipeline_branch,
        )
        return pipeline_branch

    selected = select_fallback_branch_name(branch_names)
    if selected:
        LOGGER.info(
            "Using deployment-name fallback branch for %s/%s: %s",
            target.project_name,
            repo_name,
            selected,
        )
    return selected


def pipeline_fallback_branch_name(
    client: AzureDevOpsClient,
    target: RepoScanTarget,
    branch_names: list[str],
) -> str:
    repo = target.repo
    repo_id = str(repo.get("id") or "")
    repo_name = str(repo.get("name") or "")
    try:
        definitions = client.list_build_definitions_for_repo(target.project_name, repo_id)
    except AzureDevOpsError as exc:
        LOGGER.debug(
            "Could not inspect build definitions for %s/%s: %s",
            target.project_name,
            repo_name,
            exc,
        )
        return ""
    return select_pipeline_branch_name(branch_names, branch_names_from_build_definitions(definitions))


def branch_names_from_refs(refs: Iterable[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        name = branch_name_from_ref(str(ref.get("name") or ""))
        key = name.lower()
        if name and key not in seen:
            names.append(name)
            seen.add(key)
    return names


def branch_names_from_build_definitions(definitions: Iterable[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for definition in definitions:
        repository = definition.get("repository") if isinstance(definition.get("repository"), dict) else {}
        names.extend(extract_branch_values([repository.get("defaultBranch")]))
        for trigger in definition.get("triggers") or []:
            if not isinstance(trigger, dict):
                continue
            filters = trigger.get("branchFilters") or []
            names.extend(extract_branch_values([filters] if isinstance(filters, str) else filters))
    return [name for name in names if name]


def extract_branch_values(values: Iterable[Any]) -> list[str]:
    names: list[str] = []
    for value in values:
        raw_value = str(value or "").strip()
        if not raw_value or raw_value.startswith("-") or "*" in raw_value:
            continue
        if raw_value.startswith("+"):
            raw_value = raw_value[1:]
        name = branch_name_from_ref(raw_value)
        if name:
            names.append(name)
    return names


def select_pipeline_branch_name(branch_names: list[str], pipeline_branch_names: Iterable[str]) -> str:
    available = {branch.lower(): branch for branch in branch_names}
    counts: Counter[str] = Counter()
    for candidate in pipeline_branch_names:
        branch_name = available.get(candidate.lower())
        if branch_name:
            counts[branch_name] += 1
    if not counts:
        return ""

    ranked = sorted(
        counts,
        key=lambda branch: (
            -branch_deployment_score(branch),
            -counts[branch],
            branch.count("/"),
            len(branch),
            branch.lower(),
        ),
    )
    selected = ranked[0]
    if branch_deployment_score(selected) or len(counts) == 1:
        return selected
    return ""


def select_fallback_branch_name(branch_names: list[str]) -> str:
    candidates = [branch for branch in branch_names if branch_deployment_score(branch)]
    if not candidates:
        return ""
    return sorted(
        candidates,
        key=lambda branch: (
            -branch_deployment_score(branch),
            -int(is_direct_deployment_branch_name(branch)),
            branch.count("/"),
            len(branch),
            branch.lower(),
        ),
    )[0]


def branch_deployment_score(branch_name: str) -> int:
    direct_keys, token_keys = branch_name_match_keys(branch_name)
    for keyword, score in FALLBACK_BRANCH_PRIORITY:
        if normalized_branch_key(keyword) in direct_keys:
            return score
    for keyword, score in FALLBACK_BRANCH_PRIORITY:
        if normalized_branch_key(keyword) in token_keys:
            return score
    return 0


def is_direct_deployment_branch_name(branch_name: str) -> bool:
    direct_keys, _ = branch_name_match_keys(branch_name)
    return any(
        normalized_branch_key(keyword) in direct_keys
        for keyword, _ in FALLBACK_BRANCH_PRIORITY
    )


def branch_name_match_keys(branch_name: str) -> tuple[set[str], set[str]]:
    lowered = branch_name_from_ref(branch_name).strip().lower()
    last_segment = lowered.rsplit("/", 1)[-1]
    direct_keys = {
        lowered,
        last_segment,
        normalized_branch_key(lowered),
        normalized_branch_key(last_segment),
    }
    token_keys = {
        normalized_branch_key(token)
        for token in re.split(r"[^a-z0-9]+", lowered)
        if token
    }
    return direct_keys, token_keys


def normalized_branch_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def branch_age_bucket(
    last_updated: str,
    branch_age_days: int,
    now: datetime | None = None,
) -> str:
    updated_at = parse_ado_datetime(last_updated)
    if updated_at is None:
        return older_sheet_name(branch_age_days)
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=branch_age_days)
    if updated_at >= cutoff:
        return active_sheet_name(branch_age_days)
    return older_sheet_name(branch_age_days)


def identifier_status(identifier: str) -> str:
    if identifier:
        return "found"
    return "missing_from_scanned_files"


def category_columns(categories: Iterable[str]) -> dict[str, str]:
    category_set = set(categories)
    return {
        f"category_{category}": "TRUE" if category in category_set else "FALSE"
        for category in KNOWN_CATEGORIES
    }


def fetch_repo_activity(
    client: AzureDevOpsClient,
    project_name: str,
    repo_id: str,
    branch_name: str,
    max_commits: int,
    activity_mode: str,
) -> RepoActivityMetadata:
    try:
        commit_limit = 1 if activity_mode == "latest" else max_commits
        commits = client.list_commits(
            project_name=project_name,
            repo_id=repo_id,
            max_commits=commit_limit,
            branch_name=branch_name,
        )
    except AzureDevOpsError as exc:
        LOGGER.info("Could not fetch commit activity for %s/%s@%s: %s", project_name, repo_id, branch_name, exc)
        return RepoActivityMetadata()

    activity = extract_repo_activity(commits)
    if activity_mode == "latest":
        return RepoActivityMetadata(last_updated=activity.last_updated)
    return activity


def fetch_contents(
    client: AzureDevOpsClient,
    project_name: str,
    repo_id: str,
    branch_name: str,
    paths: list[str],
    executor: ThreadPoolExecutor,
) -> dict[str, str]:
    if not paths:
        return {}

    contents: dict[str, str] = {}
    futures = {
        executor.submit(client.fetch_file_content, project_name, repo_id, path, branch_name): path
        for path in paths
    }
    for future in as_completed(futures):
        path = futures[future]
        content = future.result()
        if content:
            contents[path] = content
    return contents


def collect_targets(client: AzureDevOpsClient, project_name: str | None) -> list[RepoScanTarget]:
    projects = [{"name": project_name}] if project_name else client.list_projects()
    targets: list[RepoScanTarget] = []
    seen_repo_ids: set[str] = set()

    for project in projects:
        name = project.get("name")
        if not name:
            continue

        LOGGER.info("Listing repositories in project: %s", name)
        try:
            repos = client.list_repos(name)
        except Exception as exc:
            LOGGER.warning("Failed to list repos for %s: %s", name, exc)
            continue

        for repo in repos:
            repo_id = repo.get("id")
            if not repo_id or repo_id in seen_repo_ids:
                continue
            seen_repo_ids.add(repo_id)
            targets.append(RepoScanTarget(project_name=name, repo=repo))

    return targets


def iter_completed_branch_target_lists(
    repo_executor: ThreadPoolExecutor,
    client: AzureDevOpsClient,
    targets: list[RepoScanTarget],
    max_in_flight: int,
) -> Iterable[tuple[int, Future[list[BranchScanTarget]]]]:
    target_iter = iter(targets)
    pending: set[Future[list[BranchScanTarget]]] = set()
    submitted = 0
    completed = 0

    def submit_next() -> bool:
        nonlocal submitted
        try:
            target = next(target_iter)
        except StopIteration:
            return False
        pending.add(repo_executor.submit(list_branch_targets, client, target))
        submitted += 1
        return True

    for _ in range(max(1, max_in_flight)):
        if not submit_next():
            break

    while pending:
        done, pending = wait(pending, return_when=FIRST_COMPLETED)
        for future in done:
            completed += 1
            yield completed, future

        while len(pending) < max_in_flight and submitted < len(targets):
            if not submit_next():
                break


def iter_completed_repo_scans(
    repo_executor: ThreadPoolExecutor,
    client: AzureDevOpsClient,
    targets: list[RepoScanTarget],
    content_executor: ThreadPoolExecutor,
    max_in_flight: int,
    min_confidence_rank: int,
    max_commits_per_repo: int,
    branch_age_days: int,
    store_client: StoreLookupClient | None,
    activity_mode: str = DEFAULT_ACTIVITY_MODE,
) -> Iterable[tuple[int, Future[list[dict[str, Any]]]]]:
    target_iter = iter(targets)
    pending: set[Future[list[dict[str, Any]]]] = set()
    submitted = 0
    completed = 0

    def submit_next() -> bool:
        nonlocal submitted
        try:
            target = next(target_iter)
        except StopIteration:
            return False
        pending.add(
            repo_executor.submit(
                scan_repo,
                client,
                target,
                content_executor,
                min_confidence_rank,
                max_commits_per_repo,
                branch_age_days,
                store_client,
                activity_mode,
            )
        )
        submitted += 1
        return True

    for _ in range(max(1, max_in_flight)):
        if not submit_next():
            break

    while pending:
        done, pending = wait(pending, return_when=FIRST_COMPLETED)
        for future in done:
            completed += 1
            yield completed, future

        while len(pending) < max_in_flight and submitted < len(targets):
            if not submit_next():
                break
