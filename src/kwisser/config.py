"""Application configuration and runtime state.

Settings are loaded from environment variables (and optionally a .env file)
via pydantic-settings.  RuntimeState holds mutable counters and IDs that are
populated during the execution pipeline and passed explicitly to every function
that needs them.
"""

import dataclasses
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Module-level constants – these never change between environments.
# ---------------------------------------------------------------------------

GITHUB_GRAPHQL_URL: str = "https://api.github.com/graphql"
CACHE_DIR: Path = Path("cache")
SVG_FILES: tuple[str, ...] = ("dark_mode.svg", "light_mode.svg")

# Number of free-text comment lines written at the top of every cache file.
COMMENT_BLOCK_SIZE: int = 7
CACHE_COMMENT_LINE: str = (
    "This line is a comment block. Write whatever you want here.\n"
)


# ---------------------------------------------------------------------------
# Profile configuration – customizable card content and tech stack.
# ---------------------------------------------------------------------------


class ProfileConfig(BaseSettings):
    """Static profile information displayed on the GitHub README card.

    All fields have sensible defaults matching the developer profile and can
    be customized either directly in this class or overridden via environment
    variables / .env file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    os: str = "macOS / Linux / Windows 11"
    role: str = "Senior Fullstack Data/AI Engineer"
    location: str = "Berlin, Germany"
    stack_backend: str = "Python, FastAPI, Terraform, Rust"
    stack_frontend: str = "React, TypeScript, Next.js"
    stack_cloud: str = "GCP, Docker, Kubernetes"
    stack_ai: str = "LLMs, Multi-Agent Systems, RAG, Local LLMs"
    interests_ai: str = "Coding Agents, Multi-Agent Systems"
    interests_security: str = "IT Security, Offensive Research"
    interests_cloud: str = "Cloud Native Development, ML"
    learning: str = "Stable Multi-Agent Systems, Machine Learning, Rust"
    linkedin: str = "in/klemens-wisser-a4618b68"


# ---------------------------------------------------------------------------
# Environment / secrets – validated by pydantic-settings on startup.
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Typed, validated application settings loaded from the environment.

    Required environment variables
    --------------------------------
    ACCESS_TOKEN  GitHub personal-access token used for API requests.
    USER_NAME     GitHub login of the profile owner.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Surface a clear error for any unexpected env vars that look like
        # typos of the required fields instead of silently ignoring them.
        extra="ignore",
    )

    access_token: str
    user_name: str
    profile: ProfileConfig = Field(default_factory=ProfileConfig)

    @property
    def headers(self) -> dict[str, str]:
        """Authorization header ready to pass to every GitHub API request."""
        return {"authorization": f"token {self.access_token}"}


# ---------------------------------------------------------------------------
# Runtime state – mutable, created once in main() and threaded through.
# ---------------------------------------------------------------------------

_DEFAULT_QUERY_COUNT: dict[str, int] = {
    "user_getter": 0,
    "follower_getter": 0,
    "graph_repos_stars": 0,
    "contribution_years_getter": 0,
    "contribution_stats_getter": 0,
    "starred_getter": 0,
    "recursive_loc": 0,
    "loc_query": 0,
}


@dataclasses.dataclass
class RuntimeState:
    """Mutable state that accumulates during one pipeline run.

    Attributes
    ----------
    owner_id:
        GitHub node ID of the authenticated user; set after ``user_getter``
        is called and used by the LOC counter to identify the user's commits.
    query_count:
        Per-function counter of GraphQL API calls made during this run.
    """

    owner_id: str | None = None
    query_count: dict[str, int] = dataclasses.field(
        default_factory=lambda: dict(_DEFAULT_QUERY_COUNT)
    )

    def increment_query(self, function_name: str) -> None:
        """Increment the GraphQL call counter for *function_name*."""
        self.query_count[function_name] += 1
