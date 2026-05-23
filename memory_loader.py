"""
Memory loader — reads .agent-memory/ markdown files from the repo and
assembles a context blob for Claude.
"""
import logging
from typing import Optional

from app.services.github_client import get_file_content, list_directory

log = logging.getLogger(__name__)

MEMORY_DIR = ".agent-memory"


async def load_memory_context(
    repo: str,
    changed_files: list[str],
    branch: str = "main",
) -> str:
    sections: list[str] = []

    rules = await _load_file(repo, f"{MEMORY_DIR}/rules.md", branch)
    if rules:
        sections.append(f"## Team rules (always apply)\n\n{rules}")

    index = await _load_file(repo, f"{MEMORY_DIR}/index.md", branch)
    if index:
        sections.append(f"## Memory index\n\n{index}")

    patterns = await _load_directory(repo, f"{MEMORY_DIR}/patterns", branch)
    if patterns:
        joined = "\n\n---\n\n".join(
            f"### {name}\n\n{content}" for name, content in patterns
        )
        sections.append(f"## Recurring patterns\n\n{joined}")

    comments = await _load_directory(
        repo, f"{MEMORY_DIR}/comments", branch, limit=20
    )
    if comments:
        joined = "\n\n---\n\n".join(content for _, content in comments)
        sections.append(f"## Recent peer review comments\n\n{joined}")

    feedback = await _load_directory(
        repo, f"{MEMORY_DIR}/feedback", branch, limit=10
    )
    if feedback:
        joined = "\n\n---\n\n".join(content for _, content in feedback)
        sections.append(f"## Past review feedback\n\n{joined}")

    if not sections:
        return ""

    return "\n\n".join(sections)


async def _load_file(repo: str, path: str, branch: str) -> Optional[str]:
    try:
        result = await get_file_content(repo, path, branch)
        return result[0] if result else None
    except Exception as e:
        log.warning("Failed to load %s: %s", path, e)
        return None


async def _load_directory(
    repo: str, path: str, branch: str, limit: Optional[int] = None
) -> list[tuple[str, str]]:
    try:
        items = await list_directory(repo, path, branch)
    except Exception as e:
        log.warning("Failed to list %s: %s", path, e)
        return []

    md_files = [item for item in items if item.get("name", "").endswith(".md")]
    md_files.sort(key=lambda f: f["name"], reverse=True)
    if limit:
        md_files = md_files[:limit]

    results = []
    for item in md_files:
        content = await _load_file(repo, item["path"], branch)
        if content:
            results.append((item["name"], content))
    return results
