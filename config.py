"""Configuration loaded from environment variables."""
import os
from dotenv import load_dotenv

load_dotenv()

# Required
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
TARGET_REPO = os.environ["TARGET_REPO"]  # e.g. "yourname/pr-review-agent-test"

# Optional
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))  # 5 min
STATE_FILE = os.environ.get("STATE_FILE", "./state.json")

# When the agent starts for the first time, should it review PRs that already exist?
REVIEW_EXISTING_ON_STARTUP = (
    os.environ.get("REVIEW_EXISTING_ON_STARTUP", "false").lower() == "true"
)
