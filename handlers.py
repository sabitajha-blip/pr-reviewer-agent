"""
Action handlers — the actual work performed when the poller detects
something new. Each function is invoked by the poller, not by webhooks.
"""
import logging
import re
from datetime import datetime

from app.services.github_client import (
    get_file_content,
    get_pr_diff,
    get_pr_files,
    post_pr_comment,
    put_file_content,
)
from app.services.memory_loader import load_memory_context
from app.services.claude_reviewer import review_pr, extract_learning

log = logging.getLogger(__name__)

MEMORY_DIR = ".agent-memory"


def _slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9\s-]", "", text).strip().lower()
    return re.sub(r"\s+", "-", text)[:60]


# ────────────────────────────────────────────────────────────────
# PR review
# ────────────────────────────────────────────────────────────────

async def review_pull_request(repo: str, pr: dict):
    """Generate and post an AI review for the given PR."""
    pr_number = pr["number"]
    pr_title = pr["title"]
    base_branch = pr["base"]["ref"]

    log.info("Reviewing PR #%s: %s", pr_number, pr_title)

    try:
        diff = await get_pr_diff(repo, pr_number)
        files = await get_pr_files(repo, pr_number)
        changed_paths = [f["filename"] for f in files]

        memory = await load_memory_context(repo, changed_paths, branch=base_branch)
        log.info("Loaded %d chars of memory context", len(memory))

        review_text = await review_pr(diff, memory, pr_title)

        header = "🤖 **AI Review** _(informed by team memory in `.agent-memory/`)_\n\n"
        await post_pr_comment(repo, pr_number, header + review_text)
        log.info("✓ Posted review on PR #%s", pr_number)

    except Exception as e:
        log.exception("Failed to review PR #%s: %s", pr_number, e)


# ────────────────────────────────────────────────────────────────
# Memory ingestion from peer comments
# ────────────────────────────────────────────────────────────────

async def learn_from_comment(repo: str, comment: dict):
    """Process a peer review comment — decide if it's a rule to remember."""
    body = comment["body"]
    file_path = comment.get("path", "unknown")
    diff_hunk = comment.get("diff_hunk", "")
    author = comment["user"]["login"]
    pr_number = comment["pull_request_url"].rsplit("/", 1)[-1]

    log.info("Analyzing comment %s by %s on %s", comment["id"], author, file_path)

    learning = await extract_learning(body, diff_hunk, file_path)
    if not learning:
        return

    category = learning.get("category")
    title = learning.get("title", "untitled")

    if category == "skip":
        log.info("→ classified as one-off, logging to comments/")
        await _append_to_comments_log(repo, pr_number, author, file_path, body)
        return

    # Store as a pattern
    rule = learning.get("rule", "")
    explanation = learning.get("explanation", "")
    filename = f"{datetime.utcnow():%Y%m%d}-{_slugify(title)}.md"
    path = f"{MEMORY_DIR}/patterns/{filename}"

    content = (
        f"# {title}\n\n"
        f"**Rule:** {rule}\n\n"
        f"**Explanation:** {explanation}\n\n"
        f"---\n"
        f"_Learned from PR #{pr_number}, comment by @{author} on `{file_path}`._\n\n"
        f"_Original comment:_\n\n> {body}\n"
    )

    try:
        await put_file_content(
            repo,
            path,
            content,
            commit_message=f"agent: learn '{title}' from PR #{pr_number}",
            branch="main",
        )
        log.info("✓ Stored new memory: %s", path)
    except Exception as e:
        log.warning("Failed to commit memory file %s: %s", path, e)


async def _append_to_comments_log(
    repo: str, pr_number: str, author: str, file_path: str, body: str
):
    log_path = f"{MEMORY_DIR}/comments/{datetime.utcnow():%Y%m}.md"
    existing = await get_file_content(repo, log_path)
    prev_content, sha = (
        (existing[0], existing[1])
        if existing
        else (f"# Peer comments — {datetime.utcnow():%B %Y}\n\n", None)
    )

    entry = (
        f"## PR #{pr_number} · `{file_path}` · @{author}\n\n"
        f"{body}\n\n"
        f"---\n\n"
    )
    new_content = prev_content + entry

    try:
        await put_file_content(
            repo, log_path, new_content,
            commit_message=f"agent: log comment from PR #{pr_number}",
            branch="main", sha=sha,
        )
    except Exception as e:
        log.warning("Failed to append comment log: %s", e)


# ────────────────────────────────────────────────────────────────
# Merge logging
# ────────────────────────────────────────────────────────────────

async def log_merged_pr(repo: str, pr: dict):
    pr_number = pr["number"]
    title = pr["title"]
    merged_by = pr["merged_by"]["login"] if pr.get("merged_by") else "unknown"

    log.info("Logging merge of PR #%s", pr_number)

    log_path = f"{MEMORY_DIR}/feedback/{datetime.utcnow():%Y%m}-merges.md"
    existing = await get_file_content(repo, log_path)
    prev_content, sha = (
        (existing[0], existing[1]) if existing else ("# Merge log\n\n", None)
    )

    entry = (
        f"- PR #{pr_number}: _{title}_ merged by @{merged_by} "
        f"on {datetime.utcnow():%Y-%m-%d}\n"
    )
    new_content = prev_content + entry

    try:
        await put_file_content(
            repo, log_path, new_content,
            commit_message=f"agent: log merge of PR #{pr_number}",
            branch="main", sha=sha,
        )
        log.info("✓ Logged merge of PR #%s", pr_number)
    except Exception as e:
        log.warning("Failed to log merge: %s", e)
