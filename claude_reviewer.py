"""
Claude Code CLI wrapper.

Instead of calling the Anthropic API directly, this module shells out to the
`claude` CLI in headless mode (`claude -p`). The CLI handles auth via your
existing Claude subscription / login — no API key needed.

Two functions mirror what the old api-based reviewer did:
  - review_pr(diff, memory, title) -> markdown review
  - extract_learning(comment, diff_hunk, file_path) -> {category, title, rule, ...}

Both use `--bare` for fast, reproducible startup and `--output-format json` so we
can reliably parse Claude's response (with the structured-output schema for the
learning extractor).
"""
import asyncio
import json
import logging
from typing import Optional

from app.config import CLAUDE_CLI, CLAUDE_TIMEOUT_SECONDS

log = logging.getLogger(__name__)


REVIEW_SYSTEM_PROMPT = """You are a senior software engineer performing a code review on a pull request.

Read the diff and produce a thorough, constructive review. Focus on:
  - Correctness bugs and edge cases
  - Security issues (injection, auth, secrets, unsafe deserialization)
  - Performance concerns (N+1 queries, blocking calls, unnecessary allocations)
  - Maintainability (naming, structure, duplication)
  - Test coverage gaps

You will be given a TEAM MEMORY section containing rules, patterns from past reviews,
and peer comments. Treat memory as authoritative. If the diff violates a remembered
rule, flag it explicitly and cite the rule.

Output format: a markdown review with these sections:
  ## Summary
  one paragraph high-level take

  ## Findings
  - **[severity]** brief title — explanation. File: `path/to/file.py:42`.
  (severity: blocker, major, minor, or nit)

  ## Suggestions
  optional improvements that aren't blockers

If the diff looks great, say so plainly. Don't manufacture problems."""


LEARNING_SYSTEM_PROMPT = """You analyze code review comments to extract durable team rules.

You'll receive a peer review comment with its code context. Decide:
  1. Is this a generalizable rule (applies to future code), or a one-off note?
  2. If generalizable, state the rule in one crisp sentence.
  3. Category: "rule" (durable convention), "pattern" (recurring scenario), or "skip" (one-off).

Return JSON only with this schema, no prose:
{
  "category": "rule" | "pattern" | "skip",
  "title": "short title for the memory file",
  "rule": "the durable rule in one sentence",
  "explanation": "why this matters, with code example if helpful"
}"""


async def _run_claude(
    prompt: str,
    system_prompt: str,
    output_format: str = "text",
) -> Optional[str]:
    """
    Run `claude -p` with the given prompt and return stdout.
    Returns None on timeout or non-zero exit.
    """
    cmd = [
        CLAUDE_CLI,
        "--bare",                                  # skip auto-discovery for speed
        "-p", prompt,
        "--append-system-prompt", system_prompt,
        "--output-format", output_format,
        "--allowedTools", "",                      # disable all tools; pure text-in/text-out
    ]

    log.debug("Running claude CLI: %d-char prompt, format=%s", len(prompt), output_format)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=CLAUDE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            log.error("claude CLI timed out after %ds, killing", CLAUDE_TIMEOUT_SECONDS)
            proc.kill()
            await proc.wait()
            return None

        if proc.returncode != 0:
            log.error(
                "claude CLI exited %d. stderr:\n%s",
                proc.returncode,
                stderr.decode("utf-8", errors="replace")[:2000],
            )
            return None

        return stdout.decode("utf-8", errors="replace")

    except FileNotFoundError:
        log.error(
            "Could not find '%s' on PATH. Is Claude Code installed? "
            "Try: npm install -g @anthropic-ai/claude-code", CLAUDE_CLI
        )
        return None
    except Exception as e:
        log.exception("Failed to invoke claude CLI: %s", e)
        return None


async def review_pr(diff: str, memory_context: str, pr_title: str) -> Optional[str]:
    """Generate a markdown review for the diff. Returns None on failure."""
    memory_block = (
        f"# Team memory\n\n{memory_context}\n\n---\n\n"
        if memory_context
        else "# Team memory\n\n(No memory yet — this is the team's first review.)\n\n---\n\n"
    )

    prompt = (
        f"{memory_block}"
        f"# Pull request: {pr_title}\n\n"
        f"## Diff\n\n```diff\n{diff}\n```\n\n"
        f"Please review this PR following the format in your instructions."
    )

    return await _run_claude(prompt, REVIEW_SYSTEM_PROMPT, output_format="text")


async def extract_learning(
    comment_body: str, diff_hunk: str, file_path: str
) -> Optional[dict]:
    """Decide whether a peer comment should become a durable memory."""
    prompt = (
        f"File: {file_path}\n\n"
        f"Code being commented on:\n```\n{diff_hunk}\n```\n\n"
        f"Reviewer comment:\n{comment_body}"
    )

    # Use JSON schema so the CLI returns structured output reliably
    schema = json.dumps({
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": ["rule", "pattern", "skip"]},
            "title": {"type": "string"},
            "rule": {"type": "string"},
            "explanation": {"type": "string"},
        },
        "required": ["category", "title", "rule", "explanation"],
    })

    cmd = [
        CLAUDE_CLI, "--bare",
        "-p", prompt,
        "--append-system-prompt", LEARNING_SYSTEM_PROMPT,
        "--output-format", "json",
        "--json-schema", schema,
        "--allowedTools", "",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=CLAUDE_TIMEOUT_SECONDS
        )

        if proc.returncode != 0:
            log.error(
                "claude CLI failed for learning extraction: %s",
                stderr.decode("utf-8", errors="replace")[:1000],
            )
            return None

        raw = stdout.decode("utf-8", errors="replace")
        envelope = json.loads(raw)
        return envelope.get("structured_output")

    except asyncio.TimeoutError:
        log.error("claude CLI timed out during learning extraction")
        return None
    except json.JSONDecodeError as e:
        log.warning("Failed to parse claude JSON output: %s", e)
        return None
    except Exception as e:
        log.exception("Failed to extract learning: %s", e)
        return None