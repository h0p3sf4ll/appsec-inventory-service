from __future__ import annotations

import base64
import logging
import threading
from typing import Any
from urllib.parse import quote

from .constants import DEFAULT_COMMIT_PAGE_SIZE, MISSING_REQUESTS_MESSAGE
from .models import AzureDevOpsError
from .utils import clean_value

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    requests = None
    HTTPAdapter = None
    Retry = None


LOGGER = logging.getLogger("appsec_scan_router")
GITHUB_DEPLOYMENT_ENVIRONMENTS = ("production", "prod", "preprod", "pre-prod")
GITHUB_SUCCESSFUL_DEPLOYMENT_STATES = frozenset({"success"})


class GitHubEnterpriseClient:
    def __init__(self, base_url: str, owner: str, token: str, timeout_seconds: int) -> None:
        if requests is None or HTTPAdapter is None or Retry is None:
            raise SystemExit(MISSING_REQUESTS_MESSAGE)

        self.base_url = normalize_github_api_url(base_url)
        self.owner = owner
        self.timeout_seconds = timeout_seconds
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "appsec-scan-router/1.0",
        }
        self._retry = Retry(
            total=5,
            connect=3,
            read=3,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        self._thread_local = threading.local()
        self._sessions: list[requests.Session] = []
        self._sessions_lock = threading.Lock()

    def close(self) -> None:
        with self._sessions_lock:
            for session in self._sessions:
                session.close()
            self._sessions.clear()

    @property
    def session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(self._headers)
            adapter = HTTPAdapter(max_retries=self._retry, pool_connections=8, pool_maxsize=8)
            session.mount("https://", adapter)
            self._thread_local.session = session
            with self._sessions_lock:
                self._sessions.append(session)
        return session

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self.session.get(self._url(path), params=params, timeout=self.timeout_seconds)
        self._raise_for_status(response)
        try:
            return response.json()
        except ValueError as exc:
            raise AzureDevOpsError(f"Expected JSON from {response.url}") from exc

    @staticmethod
    def _raise_for_status(response: requests.Response) -> None:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            message = response.text[:500].replace("\n", " ")
            raise AzureDevOpsError(
                f"HTTP {response.status_code} from {response.url}: {message}",
                status_code=response.status_code,
            ) from exc

    def _get_paginated(self, path: str, params: dict[str, Any] | None = None) -> list[Any]:
        results: list[Any] = []
        next_url = self._url(path)
        request_params = dict(params or {})

        while next_url:
            response = self.session.get(next_url, params=request_params, timeout=self.timeout_seconds)
            self._raise_for_status(response)
            data = response.json()
            if isinstance(data, list):
                results.extend(data)
            elif isinstance(data, dict) and isinstance(data.get("items"), list):
                results.extend(data["items"])
            else:
                break
            next_url = response.links.get("next", {}).get("url", "")
            request_params = {}

        return results

    def list_projects(self) -> list[dict[str, Any]]:
        return [{"name": self.owner}]

    def list_repos(self, project_name: str) -> list[dict[str, Any]]:
        if project_name and project_name != self.owner:
            return [self.repo_from_api(self.get_json(f"/repos/{self.owner}/{quote(project_name)}"))]

        try:
            repos = self._get_paginated(
                f"/orgs/{quote(self.owner)}/repos",
                {"type": "all", "per_page": 100},
            )
        except AzureDevOpsError as exc:
            if exc.status_code != 404:
                raise
            repos = self._get_paginated(
                f"/users/{quote(self.owner)}/repos",
                {"type": "all", "per_page": 100},
            )
        return [self.repo_from_api(repo) for repo in repos if isinstance(repo, dict)]

    def repo_from_api(self, repo: dict[str, Any]) -> dict[str, Any]:
        full_name = clean_value(repo.get("full_name"))
        name = clean_value(repo.get("name"))
        default_branch = clean_value(repo.get("default_branch"))
        return {
            "id": full_name or name,
            "name": name,
            "fullName": full_name,
            "defaultBranch": f"refs/heads/{default_branch}" if default_branch else "",
            "webUrl": clean_value(repo.get("html_url")),
            "isDisabled": bool(repo.get("disabled") or repo.get("archived")),
        }

    def list_branches(self, project_name: str, repo_id: str) -> list[dict[str, Any]]:
        branches = self._get_paginated(f"/repos/{quote(repo_id, safe='/')}/branches", {"per_page": 100})
        return [
            {"name": f"refs/heads/{clean_value(branch.get('name'))}"}
            for branch in branches
            if isinstance(branch, dict) and clean_value(branch.get("name"))
        ]

    def list_build_definitions_for_repo(self, project_name: str, repo_id: str) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []
        for environment in GITHUB_DEPLOYMENT_ENVIRONMENTS:
            try:
                deployments = self._get_paginated(
                    f"/repos/{quote(repo_id, safe='/')}/deployments",
                    {"environment": environment, "per_page": 100},
                )
            except AzureDevOpsError as exc:
                if exc.status_code in {403, 404}:
                    continue
                raise
            for deployment in deployments:
                if not isinstance(deployment, dict):
                    continue
                ref = clean_value(deployment.get("ref"))
                if ref and self.deployment_is_successful(repo_id, deployment):
                    definitions.append({"repository": {"defaultBranch": ref}})
        return definitions

    def deployment_is_successful(self, repo_id: str, deployment: dict[str, Any]) -> bool:
        deployment_id = clean_value(deployment.get("id"))
        if not deployment_id:
            return True
        try:
            statuses = self._get_paginated(
                f"/repos/{quote(repo_id, safe='/')}/deployments/{deployment_id}/statuses",
                {"per_page": 1},
            )
        except AzureDevOpsError as exc:
            if exc.status_code in {403, 404}:
                return True
            raise
        if not statuses:
            return True
        latest = statuses[0] if isinstance(statuses[0], dict) else {}
        return clean_value(latest.get("state")).lower() in GITHUB_SUCCESSFUL_DEPLOYMENT_STATES

    def list_repo_items(self, project_name: str, repo_id: str, branch_name: str | None = None) -> list[dict[str, Any]]:
        ref = quote(self.tree_ref_for_branch(repo_id, branch_name), safe="")
        data = self.get_json(f"/repos/{quote(repo_id, safe='/')}/git/trees/{ref}", {"recursive": "1"})
        tree = data.get("tree") if isinstance(data, dict) else []
        return [
            {"path": f"/{item.get('path', '').lstrip('/')}"}
            for item in tree
            if isinstance(item, dict) and item.get("path")
        ]

    def tree_ref_for_branch(self, repo_id: str, branch_name: str | None) -> str:
        if not branch_name:
            return "HEAD"
        try:
            branch = self.get_json(f"/repos/{quote(repo_id, safe='/')}/branches/{quote(branch_name, safe='')}")
        except AzureDevOpsError as exc:
            if exc.status_code == 404:
                return branch_name
            raise
        commit = branch.get("commit") if isinstance(branch, dict) else {}
        return clean_value(commit.get("sha")) or branch_name

    def list_commits(
        self,
        project_name: str,
        repo_id: str,
        max_commits: int = 0,
        page_size: int = DEFAULT_COMMIT_PAGE_SIZE,
        branch_name: str | None = None,
    ) -> list[dict[str, Any]]:
        per_page = max(1, min(page_size, 100))
        params: dict[str, Any] = {"per_page": per_page}
        if branch_name:
            params["sha"] = branch_name

        commits: list[dict[str, Any]] = []
        next_url = self._url(f"/repos/{quote(repo_id, safe='/')}/commits")
        request_params = params

        while next_url:
            response = self.session.get(next_url, params=request_params, timeout=self.timeout_seconds)
            self._raise_for_status(response)
            batch = response.json()
            if not isinstance(batch, list) or not batch:
                break

            commits.extend(github_commit_to_activity_commit(item) for item in batch if isinstance(item, dict))
            if max_commits and len(commits) >= max_commits:
                return commits[:max_commits]
            next_url = response.links.get("next", {}).get("url", "")
            request_params = {}

        return commits

    def fetch_file_content(
        self,
        project_name: str,
        repo_id: str,
        file_path: str,
        branch_name: str | None = None,
    ) -> str:
        clean_path = quote(file_path.lstrip("/"), safe="/")
        params: dict[str, Any] = {}
        if branch_name:
            params["ref"] = branch_name
        try:
            data = self.get_json(f"/repos/{quote(repo_id, safe='/')}/contents/{clean_path}", params)
        except (AzureDevOpsError, requests.RequestException) as exc:
            LOGGER.debug("Failed to fetch %s in repo %s: %s", file_path, repo_id, exc)
            return ""

        if not isinstance(data, dict):
            return ""
        content = clean_value(data.get("content"))
        encoding = clean_value(data.get("encoding")).lower()
        if encoding != "base64" or not content:
            return ""
        try:
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except ValueError:
            return ""


def github_commit_to_activity_commit(commit: dict[str, Any]) -> dict[str, Any]:
    details = commit.get("commit") if isinstance(commit.get("commit"), dict) else {}
    author = details.get("author") if isinstance(details.get("author"), dict) else {}
    committer = details.get("committer") if isinstance(details.get("committer"), dict) else {}
    return {
        "author": {
            "name": clean_value(author.get("name")),
            "email": clean_value(author.get("email")),
        },
        "committer": {
            "name": clean_value(committer.get("name")),
            "email": clean_value(committer.get("email")),
            "date": clean_value(committer.get("date") or author.get("date")),
        },
    }


def normalize_github_api_url(base_url: str) -> str:
    text = clean_value(base_url).rstrip("/")
    if not text:
        return "https://api.github.com"
    if text.endswith("/api/v3"):
        return text
    return f"{text}/api/v3"
