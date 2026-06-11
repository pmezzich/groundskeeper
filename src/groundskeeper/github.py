"""GitHub REST client — fetches PR diffs, files, and review comments.

Deliberately fetches NO title/description into the judge path: code
verdicts never see author claims (component isolation, borrowed from
pr-agents). The diff is the only input.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

import httpx

from groundskeeper.models import DiffLine, FileDiff, PullRequest

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


def resolve_token() -> str:
    """GITHUB_TOKEN env var, falling back to `gh auth token`."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    gh = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"
    try:
        result = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    raise GitHubError("No GitHub token found. Set GITHUB_TOKEN or run `gh auth login`.")


def _headers(token: str, accept: str = "application/vnd.github.v3+json") -> dict[str, str]:
    return {"Authorization": f"token {token}", "Accept": accept}


def parse_pr_ref(ref: str, default_repo: str) -> tuple[str, int]:
    """Accept '1371', '#1371', or a full PR URL."""
    url_match = re.match(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)", ref)
    if url_match:
        return url_match.group(1), int(url_match.group(2))
    number = int(ref.lstrip("#"))
    return default_repo, number


def _parse_unified_diff(patch: str) -> tuple[list[DiffLine], list[str]]:
    """Extract added lines (with new-file line numbers) and removed lines."""
    added: list[DiffLine] = []
    removed: list[str] = []
    new_line = 0
    for line in patch.splitlines():
        hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if hunk:
            new_line = int(hunk.group(1))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added.append(DiffLine(line_number=new_line, content=line[1:]))
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:])
        elif not line.startswith("\\"):
            new_line += 1
    return added, removed


def fetch_pull_request(repo: str, number: int, token: str) -> PullRequest:
    """Fetch PR metadata + per-file patches."""
    with httpx.Client(timeout=30) as client:
        meta = client.get(f"{API}/repos/{repo}/pulls/{number}", headers=_headers(token))
        if meta.status_code != 200:
            raise GitHubError(f"Failed to fetch PR {repo}#{number}: HTTP {meta.status_code}")
        meta_data = meta.json()

        files: list[FileDiff] = []
        page = 1
        while True:
            resp = client.get(
                f"{API}/repos/{repo}/pulls/{number}/files",
                headers=_headers(token),
                params={"per_page": 100, "page": page},
            )
            if resp.status_code != 200:
                raise GitHubError(f"Failed to fetch PR files: HTTP {resp.status_code}")
            batch = resp.json()
            if not batch:
                break
            for f in batch:
                patch = f.get("patch") or ""
                added, removed = _parse_unified_diff(patch)
                files.append(
                    FileDiff(
                        path=f["filename"],
                        status=f["status"],
                        added_lines=added,
                        removed_lines=removed,
                        patch=patch,
                    )
                )
            if len(batch) < 100:
                break
            page += 1

    return PullRequest(
        number=number,
        repo=repo,
        base_ref=meta_data["base"]["ref"],
        head_sha=meta_data["head"]["sha"],
        files=files,
    )


def fetch_rules_from_base(repo: str, base_ref: str, token: str, rules_path: str) -> dict[str, str]:
    """Fetch rule files from the BASE ref so a PR can't edit rules to pass itself.

    Returns {filename: content} for every .md under the rules path.
    """
    contents: dict[str, str] = {}
    with httpx.Client(timeout=30) as client:
        listing = client.get(
            f"{API}/repos/{repo}/contents/{rules_path}",
            headers=_headers(token),
            params={"ref": base_ref},
        )
        if listing.status_code != 200:
            raise GitHubError(f"Failed to list {rules_path} at {base_ref}: HTTP {listing.status_code}")
        for entry in listing.json():
            if entry["type"] != "file" or not entry["name"].endswith(".md"):
                continue
            raw = client.get(
                entry["download_url"].split("?")[0],
                headers=_headers(token),
                params={"ref": base_ref},
            )
            if raw.status_code == 200:
                contents[entry["name"]] = raw.text
    return contents


def fetch_human_review_comments(repo: str, number: int, token: str) -> list[dict[str, str]]:
    """Fetch inline review comments from human reviewers (bots excluded)."""
    comments: list[dict[str, str]] = []
    with httpx.Client(timeout=30) as client:
        page = 1
        while True:
            resp = client.get(
                f"{API}/repos/{repo}/pulls/{number}/comments",
                headers=_headers(token),
                params={"per_page": 100, "page": page},
            )
            if resp.status_code != 200:
                raise GitHubError(f"Failed to fetch review comments: HTTP {resp.status_code}")
            batch = resp.json()
            if not batch:
                break
            for c in batch:
                login = c["user"]["login"]
                if login.endswith("[bot]") or login.endswith("-bot"):
                    continue
                comments.append({"author": login, "path": c.get("path", ""), "body": c["body"]})
            if len(batch) < 100:
                break
            page += 1
    return comments


def post_pr_comment(repo: str, number: int, token: str, body: str) -> str:
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{API}/repos/{repo}/issues/{number}/comments",
            headers=_headers(token),
            json={"body": body},
        )
        if resp.status_code != 201:
            raise GitHubError(f"Failed to post comment: HTTP {resp.status_code}")
        return resp.json()["html_url"]
