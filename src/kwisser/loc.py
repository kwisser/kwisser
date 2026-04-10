"""Lines-of-code counting and cache management.

The cache file stores one row per repository so that unchanged repos do not
require repeated GraphQL traversals.  Every function that touches the cache or
calls the GitHub API receives *settings* and *state* explicitly.
"""

import hashlib
from pathlib import Path
from typing import Any

from kwisser.config import CACHE_DIR, CACHE_COMMENT_LINE, RuntimeState, Settings


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def cache_file_path(user_name: str) -> Path:
    """Derive the per-user cache filename from the GitHub login."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    hashed_user = hashlib.sha256(user_name.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{hashed_user}.txt"


def comment_block_lines(comment_size: int) -> list[str]:
    """Build the placeholder comment block stored at the top of each cache file."""
    return [CACHE_COMMENT_LINE for _ in range(comment_size)]


def flush_cache(
    edges: list[dict[str, Any]],
    filename: Path,
    comment_size: int,
) -> None:
    """Rewrite the cache file with one empty row per repository."""
    try:
        with filename.open("r") as handle:
            cache_header = handle.readlines()[:comment_size]
    except FileNotFoundError:
        cache_header = []

    if len(cache_header) < comment_size:
        cache_header.extend(comment_block_lines(comment_size - len(cache_header)))

    with filename.open("w") as handle:
        handle.writelines(cache_header[:comment_size])
        for edge in edges:
            repository_name = edge["node"]["nameWithOwner"]
            repository_hash = hashlib.sha256(
                repository_name.encode("utf-8")
            ).hexdigest()
            handle.write(f"{repository_hash} 0 0 0 0\n")


def force_close_file(
    cache_rows: list[str],
    cache_header: list[str],
    *,
    user_name: str,
) -> None:
    """Persist partially updated cache data before raising from a failed LOC run."""
    filename = cache_file_path(user_name)
    with filename.open("w") as handle:
        handle.writelines(cache_header)
        handle.writelines(cache_rows)
    print(f"Saved partial cache data to {filename}.")


def commit_counter(comment_size: int, user_name: str) -> int:
    """Read the cache file and sum only the 'my commits' column."""
    total_commits = 0
    filename = cache_file_path(user_name)
    with filename.open("r") as handle:
        data = handle.readlines()
    for line in data[comment_size:]:
        total_commits += int(line.split()[2])
    return total_commits


# ---------------------------------------------------------------------------
# LOC calculation
# ---------------------------------------------------------------------------


def loc_counter_one_repo(
    owner: str,
    repo_name: str,
    cache_rows: list[str],
    cache_header: list[str],
    history: dict[str, Any],
    addition_total: int,
    deletion_total: int,
    my_commits: int,
    settings: Settings,
    state: RuntimeState,
) -> tuple[int, int, int]:
    """Consume one page of commit history for a single repository."""
    for edge in history["edges"]:
        author = edge["node"].get("author") or {}
        user = author.get("user") or {}

        if user.get("id") == state.owner_id:
            my_commits += 1
            addition_total += edge["node"]["additions"]
            deletion_total += edge["node"]["deletions"]

    if not history["pageInfo"]["hasNextPage"]:
        return addition_total, deletion_total, my_commits

    return recursive_loc(
        owner,
        repo_name,
        cache_rows,
        cache_header,
        settings,
        state,
        addition_total,
        deletion_total,
        my_commits,
        history["pageInfo"]["endCursor"],
    )


def recursive_loc(
    owner: str,
    repo_name: str,
    cache_rows: list[str],
    cache_header: list[str],
    settings: Settings,
    state: RuntimeState,
    addition_total: int = 0,
    deletion_total: int = 0,
    my_commits: int = 0,
    cursor: str | None = None,
) -> tuple[int, int, int]:
    """Traverse commit history for one repository, 100 commits at a time."""
    from kwisser.github_api import graphql_request  # local import avoids circular dep

    state.increment_query("recursive_loc")
    query = """
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            edges {
                                node {
                                    ... on Commit {
                                        author {
                                            user { id }
                                        }
                                        deletions
                                        additions
                                    }
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }"""
    variables = {"repo_name": repo_name, "owner": owner, "cursor": cursor}
    data = graphql_request(
        "recursive_loc",
        query,
        variables,
        settings,
        state,
        partial_cache=(cache_rows, cache_header),
    )
    branch = data["repository"]["defaultBranchRef"]

    if branch is None:
        return 0, 0, 0

    history = branch["target"]["history"]
    return loc_counter_one_repo(
        owner,
        repo_name,
        cache_rows,
        cache_header,
        history,
        addition_total,
        deletion_total,
        my_commits,
        settings,
        state,
    )


def cache_builder(
    edges: list[dict[str, Any]],
    comment_size: int,
    force_cache: bool,
    settings: Settings,
    state: RuntimeState,
    loc_add: int = 0,
    loc_del: int = 0,
) -> list[int | bool]:
    """Keep a cache file with one row per repository and return LOC totals."""
    cached = True
    filename = cache_file_path(settings.user_name)

    try:
        with filename.open("r") as handle:
            data = handle.readlines()
    except FileNotFoundError:
        data = comment_block_lines(comment_size)
        with filename.open("w") as handle:
            handle.writelines(data)

    if len(data) - comment_size != len(edges) or force_cache:
        cached = False
        flush_cache(edges, filename, comment_size)
        with filename.open("r") as handle:
            data = handle.readlines()

    cache_header = data[:comment_size]
    cache_rows = data[comment_size:]

    for index, edge in enumerate(edges):
        repository_name = edge["node"]["nameWithOwner"]
        expected_hash = hashlib.sha256(
            repository_name.encode("utf-8")
        ).hexdigest()
        stored_hash, stored_commit_count, *_ = cache_rows[index].split()

        if stored_hash != expected_hash:
            cache_rows[index] = f"{expected_hash} 0 0 0 0\n"
            stored_hash = expected_hash
            stored_commit_count = "0"

        branch = edge["node"].get("defaultBranchRef")
        history = None if branch is None else branch["target"]["history"]
        current_commit_count = 0 if history is None else history["totalCount"]

        if int(stored_commit_count) != current_commit_count:
            cached = False
            if current_commit_count == 0:
                cache_rows[index] = f"{stored_hash} 0 0 0 0\n"
                continue

            owner, repo_name = repository_name.split("/", 1)
            additions, deletions, repo_commits = recursive_loc(
                owner,
                repo_name,
                cache_rows,
                cache_header,
                settings,
                state,
            )
            cache_rows[index] = (
                f"{stored_hash} {current_commit_count} {repo_commits} "
                f"{additions} {deletions}\n"
            )

    with filename.open("w") as handle:
        handle.writelines(cache_header)
        handle.writelines(cache_rows)

    for line in cache_rows:
        _, _, _, added_lines, deleted_lines = line.split()
        loc_add += int(added_lines)
        loc_del += int(deleted_lines)

    return [loc_add, loc_del, loc_add - loc_del, cached]


def loc_query(
    owner_affiliation: list[str],
    comment_size: int,
    settings: Settings,
    state: RuntimeState,
    force_cache: bool = False,
) -> list[int | bool]:
    """Fetch every repository that should contribute to LOC stats."""
    from kwisser.github_api import graphql_request  # local import avoids circular dep

    query = """
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            defaultBranchRef {
                                target {
                                    ... on Commit {
                                        history { totalCount }
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }"""

    cursor = None
    edges: list[dict[str, Any]] = []

    while True:
        state.increment_query("loc_query")
        variables = {
            "owner_affiliation": owner_affiliation,
            "login": settings.user_name,
            "cursor": cursor,
        }
        data = graphql_request("loc_query", query, variables, settings, state)
        repositories = data["user"]["repositories"]
        edges.extend(repositories["edges"])

        if not repositories["pageInfo"]["hasNextPage"]:
            break
        cursor = repositories["pageInfo"]["endCursor"]

    return cache_builder(edges, comment_size, force_cache, settings, state)
