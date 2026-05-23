"""
State tracking — remembers what we've already processed so we don't
review the same PR twice or learn from the same comment twice.

State is a single JSON file on disk:
{
  "reviewed_pr_heads": {"42": "sha-abc123", "43": "sha-def456"},
  "processed_comment_ids": [12345, 12346, ...],
  "processed_merge_ids": [42, 43, ...],
  "last_poll_at": "2026-05-23T10:00:00Z"
}

We track the PR's HEAD sha so we can re-review when new commits are pushed
to an open PR (the "synchronize" case).
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


class State:
    def __init__(self, path: str):
        self.path = Path(path)
        self.data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {
                "reviewed_pr_heads": {},
                "processed_comment_ids": [],
                "processed_merge_ids": [],
                "last_poll_at": None,
            }
        try:
            return json.loads(self.path.read_text())
        except Exception as e:
            log.warning("State file corrupt, starting fresh: %s", e)
            return {
                "reviewed_pr_heads": {},
                "processed_comment_ids": [],
                "processed_merge_ids": [],
                "last_poll_at": None,
            }

    def save(self):
        self.data["last_poll_at"] = datetime.now(timezone.utc).isoformat()
        self.path.write_text(json.dumps(self.data, indent=2))

    # PR review tracking ------------------------------------------------

    def needs_review(self, pr_number: int, head_sha: str) -> bool:
        """True if this PR has never been reviewed, or has new commits since."""
        last_sha = self.data["reviewed_pr_heads"].get(str(pr_number))
        return last_sha != head_sha

    def mark_reviewed(self, pr_number: int, head_sha: str):
        self.data["reviewed_pr_heads"][str(pr_number)] = head_sha

    # Comment tracking --------------------------------------------------

    def is_comment_processed(self, comment_id: int) -> bool:
        return comment_id in self.data["processed_comment_ids"]

    def mark_comment_processed(self, comment_id: int):
        # Keep only the most recent 1000 to bound the file size
        ids = self.data["processed_comment_ids"]
        ids.append(comment_id)
        if len(ids) > 1000:
            self.data["processed_comment_ids"] = ids[-1000:]

    # Merge tracking ----------------------------------------------------

    def is_merge_processed(self, pr_number: int) -> bool:
        return pr_number in self.data["processed_merge_ids"]

    def mark_merge_processed(self, pr_number: int):
        self.data["processed_merge_ids"].append(pr_number)
        # Bound this too
        if len(self.data["processed_merge_ids"]) > 500:
            self.data["processed_merge_ids"] = self.data["processed_merge_ids"][-500:]
