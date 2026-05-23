"""GitHub API client — only the operations the polling agent needs."""
import base64
from typing import Optional

import httpx

from app.config import GITHUB_TOKEN

GITHUB_API = "https://api.github.com"

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


# Polling operations ----------------------------------------------------

async def list_open_prs(repo: str) -> list[dict]:
    """All open PRs on the repo. Includes head sha for change detection."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{GITHUB_API}/repos/{repo}/pulls",
            headers=HEADERS,
            params={"state": "open", "per_page": 50},
        )
        r.raise_for_status()
        return r.json()


async def list_recently_closed_prs(repo: str) -> list[dict]:
    """Recently closed PRs — we look at these to detect merges."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{GITHUB_API}/repos/{repo}/pulls",
            headers=HEADERS,
            params={"state": "closed", "per_page": 20, "sort": "updated", "direction": "desc"},
        )
        r.raise_for_status()
        return r.json()


async def list_review_comments(repo: str, since_iso: Optional[str] = None) -> list[dict]:
    """
    All review comments across the repo, optionally filtered to those updated
    after `since_iso`. Note: this returns *inline* comments on diffs.
    """
    params = {"per_page": 100, "sort": "created", "direction": "desc"}
    if since_iso:
        params["since"] = since_iso
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{GITHUB_API}/repos/{repo}/pulls/comments",
            headers=HEADERS,
            params=params,
        )
        r.raise_for_status()
        return r.json()


async def list_issue_comments(repo: str, pr_number: int) -> list[dict]:
    """General comments on a PR (the 'conversation' tab, not inline)."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments",
            headers=HEADERS,
            params={"per_page": 100},
        )
        r.raise_for_status()
        return r.json()


# PR content -----------------------------------------------------------

async def get_pr_diff(repo: str, pr_number: int) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {**HEADERS, "Accept": "application/vnd.github.v3.diff"}
        r = await client.get(
            f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}",
            headers=headers,
        )
        r.raise_for_status()
        return r.text


async def get_pr_files(repo: str, pr_number: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/files",
            headers=HEADERS,
        )
        r.raise_for_status()
        return r.json()


async def post_pr_comment(repo: str, pr_number: int, body: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments",
            headers=HEADERS,
            json={"body": body},
        )
        r.raise_for_status()
        return r.json()


# Repo content (for memory storage) ------------------------------------

async def get_file_content(
    repo: str, path: str, ref: str = "main"
) -> Optional[tuple[str, str]]:
    """Returns (content, sha) or None if file doesn't exist."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{GITHUB_API}/repos/{repo}/contents/{path}",
            headers=HEADERS,
            params={"ref": ref},
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return content, data["sha"]


async def put_file_content(
    repo: str,
    path: str,
    content: str,
    commit_message: str,
    branch: str = "main",
    sha: Optional[str] = None,
) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        body = {
            "message": commit_message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if sha:
            body["sha"] = sha
        r = await client.put(
            f"{GITHUB_API}/repos/{repo}/contents/{path}",
            headers=HEADERS,
            json=body,
        )
        r.raise_for_status()
        return r.json()


async def list_directory(
    repo: str, path: str, ref: str = "main"
) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{GITHUB_API}/repos/{repo}/contents/{path}",
            headers=HEADERS,
            params={"ref": ref},
        )
        if r.status_code == 404:
            return []
        r.raise_for_status()
        return r.json()


# Identity (used to skip the bot's own comments) ----------------------

async def get_authenticated_user() -> dict:
    """Returns info about whose PAT we're using — `login` is the username."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{GITHUB_API}/user", headers=HEADERS)
        r.raise_for_status()
        return r.json()
