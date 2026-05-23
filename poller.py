"""
The polling agent.

On each tick:
  1. List open PRs. For each, check if HEAD sha differs from what we last
     reviewed — if so, review it.
  2. List recent review comments across the repo. For each new comment from a
     human (not the bot itself), run the learning pipeline.
  3. List recently closed PRs. For each merged one we haven't logged, log it.

State (which PRs we've reviewed, which comments we've processed) lives in
state.json so we can stop/restart the agent without duplicating work.
"""
import asyncio
import logging
from datetime import datetime, timezone

from app.config import (
    POLL_INTERVAL_SECONDS,
    REVIEW_EXISTING_ON_STARTUP,
    STATE_FILE,
    TARGET_REPO,
)
from app.state import State
from app.services.github_client import (
    get_authenticated_user,
    list_open_prs,
    list_recently_closed_prs,
    list_review_comments,
)
from app.services.handlers import (
    learn_from_comment,
    log_merged_pr,
    review_pull_request,
)

log = logging.getLogger(__name__)


class Poller:
    def __init__(self, repo: str, state: State, bot_username: str):
        self.repo = repo
        self.state = state
        self.bot_username = bot_username
        self.first_tick = True

    async def tick(self):
        """One iteration of the polling loop."""
        log.info("─── Poll tick at %s ───", datetime.now(timezone.utc).isoformat())
        try:
            await self._check_open_prs()
            await self._check_new_comments()
            await self._check_merged_prs()
        except Exception as e:
            log.exception("Tick failed: %s", e)
        finally:
            self.state.save()
            self.first_tick = False
            log.info("─── Tick done. Sleeping %ds ───\n", POLL_INTERVAL_SECONDS)

    async def _check_open_prs(self):
        """Review any open PR whose HEAD sha we haven't seen before."""
        prs = await list_open_prs(self.repo)
        log.info("Found %d open PR(s)", len(prs))

        for pr in prs:
            pr_number = pr["number"]
            head_sha = pr["head"]["sha"]
            author = pr["user"]["login"]

            # Don't review the bot's own PRs (e.g. if the agent ever opens
            # one for memory updates — currently it commits directly).
            if author == self.bot_username:
                continue

            if not self.state.needs_review(pr_number, head_sha):
                log.debug("PR #%s already reviewed at %s", pr_number, head_sha[:7])
                continue

            # On first ever tick, optionally skip pre-existing PRs to avoid
            # bombing the repo with reviews after a long downtime.
            if self.first_tick and not REVIEW_EXISTING_ON_STARTUP:
                last_seen = self.state.data["reviewed_pr_heads"].get(str(pr_number))
                if last_seen is None:
                    log.info(
                        "PR #%s exists from before agent started — marking as seen "
                        "(set REVIEW_EXISTING_ON_STARTUP=true to review)",
                        pr_number,
                    )
                    self.state.mark_reviewed(pr_number, head_sha)
                    continue

            await review_pull_request(self.repo, pr)
            self.state.mark_reviewed(pr_number, head_sha)

    async def _check_new_comments(self):
        """Process any review comments we haven't seen yet."""
        # On first tick, the API returns recent comments — we just mark them
        # all as seen without learning, to avoid re-learning historical comments.
        comments = await list_review_comments(self.repo)
        log.info("Fetched %d recent comment(s)", len(comments))

        new_comments = [
            c for c in comments
            if not self.state.is_comment_processed(c["id"])
            and c["user"]["login"] != self.bot_username
        ]

        if self.first_tick and new_comments:
            log.info(
                "First tick: marking %d existing comments as seen "
                "(won't learn from history)", len(new_comments)
            )
            for c in new_comments:
                self.state.mark_comment_processed(c["id"])
            return

        for comment in new_comments:
            await learn_from_comment(self.repo, comment)
            self.state.mark_comment_processed(comment["id"])

    async def _check_merged_prs(self):
        """Log any newly merged PRs we haven't already logged."""
        prs = await list_recently_closed_prs(self.repo)

        new_merged = [
            pr for pr in prs
            if pr.get("merged_at") is not None
            and not self.state.is_merge_processed(pr["number"])
        ]

        if self.first_tick and new_merged:
            log.info(
                "First tick: marking %d existing merges as seen", len(new_merged)
            )
            for pr in new_merged:
                self.state.mark_merge_processed(pr["number"])
            return

        for pr in new_merged:
            await log_merged_pr(self.repo, pr)
            self.state.mark_merge_processed(pr["number"])


async def run():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    log.info("🤖 PR Review Agent starting")
    log.info("    Target repo:     %s", TARGET_REPO)
    log.info("    Poll interval:   %ds", POLL_INTERVAL_SECONDS)
    log.info("    State file:      %s", STATE_FILE)

    user = await get_authenticated_user()
    bot_username = user["login"]
    log.info("    Auth user:       %s", bot_username)

    state = State(STATE_FILE)
    poller = Poller(TARGET_REPO, state, bot_username)

    while True:
        await poller.tick()
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Stopping agent")
