"""GitHub GraphQL API client and all data-fetching helpers.

Every function that performs a network request accepts *settings* (for the
authorization header and username) and *state* (to record query counts and
surface them in error messages).
"""

import datetime
from typing import Any

import requests

from kwisser.config import RuntimeState, Settings
from kwisser.formatting import format_github_datetime


def raise_request_error(
    operation_name: str,
    response: requests.Response,
    state: RuntimeState,
) -> None:
    """Turn an HTTP error into a readable exception with current query counters."""
    if response.status_code == 403:
        raise RuntimeError(
            "Too many requests in a short amount of time. GitHub returned 403."
        )
    raise RuntimeError(
        f"{operation_name} failed with status {response.status_code}: "
        f"{response.text}. Query counts: {state.query_count}"
    )


def graphql_request(
    operation_name: str,
    query: str,
    variables: dict[str, Any],
    settings: Settings,
    state: RuntimeState,
    partial_cache: tuple[list[str], list[str]] | None = None,
) -> dict[str, Any]:
    """Send one GraphQL request and normalize all failure cases in one place."""
    from kwisser.loc import force_close_file  # local import to avoid circular deps

    try:
        response = requests.post(
            "https://api.github.com/graphql",
            json={"query": query, "variables": variables},
            headers=settings.headers,
            timeout=30,
        )
    except requests.RequestException as error:
        if partial_cache is not None:
            force_close_file(*partial_cache, user_name=settings.user_name)
        raise RuntimeError(f"{operation_name} request failed: {error}") from error

    if response.status_code != 200:
        if partial_cache is not None:
            force_close_file(*partial_cache, user_name=settings.user_name)
        raise_request_error(operation_name, response, state)

    try:
        payload = response.json()
    except ValueError as error:
        if partial_cache is not None:
            force_close_file(*partial_cache, user_name=settings.user_name)
        raise RuntimeError(
            f"{operation_name} returned invalid JSON: {response.text}"
        ) from error

    if payload.get("errors"):
        if partial_cache is not None:
            force_close_file(*partial_cache, user_name=settings.user_name)
        raise RuntimeError(
            f"{operation_name} returned GraphQL errors: {payload['errors']}"
        )

    return payload["data"]


def stars_counter(edges: list[dict[str, Any]]) -> int:
    """Sum the stargazer counts for the repositories on one GraphQL page."""
    total_stars = 0
    for edge in edges:
        total_stars += edge["node"]["stargazers"]["totalCount"]
    return total_stars


def graph_repos_stars(
    count_type: str,
    owner_affiliation: list[str],
    settings: Settings,
    state: RuntimeState,
) -> int:
    """Count either repositories or stars across all pages of a repository connection."""
    total_repositories = 0
    total_stars = 0
    cursor = None

    query = """
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            stargazers {
                                totalCount
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

    while True:
        state.increment_query("graph_repos_stars")
        variables = {
            "owner_affiliation": owner_affiliation,
            "login": settings.user_name,
            "cursor": cursor,
        }
        data = graphql_request(
            "graph_repos_stars", query, variables, settings, state
        )
        repositories = data["user"]["repositories"]

        total_repositories = repositories["totalCount"]
        total_stars += stars_counter(repositories["edges"])

        if not repositories["pageInfo"]["hasNextPage"]:
            break
        cursor = repositories["pageInfo"]["endCursor"]

    if count_type == "repos":
        return total_repositories
    if count_type == "stars":
        return total_stars
    return 0


def contribution_years_getter(
    username: str,
    settings: Settings,
    state: RuntimeState,
) -> list[int]:
    """Fetch the list of years that contain visible GitHub contributions."""
    state.increment_query("contribution_years_getter")
    query = """
    query($login: String!){
        user(login: $login) {
            contributionsCollection {
                contributionYears
            }
        }
    }"""
    data = graphql_request(
        "contribution_years_getter", query, {"login": username}, settings, state
    )
    return data["user"]["contributionsCollection"]["contributionYears"]


def contribution_stats_getter(
    username: str,
    settings: Settings,
    state: RuntimeState,
) -> tuple[int, int]:
    """Count lifetime commits and unique non-owned repositories contributed to."""
    years = contribution_years_getter(username, settings, state)
    total_commits = 0
    contributed_repositories: set[str] = set()
    username_lower = username.lower()

    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!){
        user(login: $login) {
            contributionsCollection(from: $from, to: $to) {
                totalCommitContributions
                commitContributionsByRepository(maxRepositories: 100) {
                    repository {
                        nameWithOwner
                        owner { login }
                    }
                }
                issueContributionsByRepository(maxRepositories: 100) {
                    repository {
                        nameWithOwner
                        owner { login }
                    }
                }
                pullRequestContributionsByRepository(maxRepositories: 100) {
                    repository {
                        nameWithOwner
                        owner { login }
                    }
                }
                pullRequestReviewContributionsByRepository(maxRepositories: 100) {
                    repository {
                        nameWithOwner
                        owner { login }
                    }
                }
            }
        }
    }"""

    now = datetime.datetime.now(datetime.timezone.utc)
    for year in years:
        state.increment_query("contribution_stats_getter")
        year_start = datetime.datetime(year, 1, 1, tzinfo=datetime.timezone.utc)
        year_end = datetime.datetime(year + 1, 1, 1, tzinfo=datetime.timezone.utc)
        variables = {
            "login": username,
            "from": format_github_datetime(year_start),
            "to": format_github_datetime(min(year_end, now)),
        }
        data = graphql_request(
            "contribution_stats_getter", query, variables, settings, state
        )
        collection = data["user"]["contributionsCollection"]
        total_commits += collection["totalCommitContributions"]

        for collection_name in (
            "commitContributionsByRepository",
            "issueContributionsByRepository",
            "pullRequestContributionsByRepository",
            "pullRequestReviewContributionsByRepository",
        ):
            for repo_entry in collection[collection_name]:
                repository = repo_entry["repository"]
                if repository["owner"]["login"].lower() == username_lower:
                    continue
                contributed_repositories.add(repository["nameWithOwner"])

    return total_commits, len(contributed_repositories)


def user_getter(
    username: str,
    settings: Settings,
    state: RuntimeState,
) -> tuple[str, datetime.datetime]:
    """Fetch the GitHub user node ID and account creation date."""
    state.increment_query("user_getter")
    query = """
    query($login: String!){
        user(login: $login) {
            id
            createdAt
        }
    }"""
    data = graphql_request(
        "user_getter", query, {"login": username}, settings, state
    )
    user_id = data["user"]["id"]
    created_at_str = data["user"]["createdAt"]
    created_at = datetime.datetime.fromisoformat(
        created_at_str.replace("Z", "+00:00")
    )
    created_at = created_at.replace(tzinfo=None)
    return user_id, created_at


def follower_getter(
    username: str,
    settings: Settings,
    state: RuntimeState,
) -> int:
    """Fetch the follower count shown on the SVG card."""
    state.increment_query("follower_getter")
    query = """
    query($login: String!){
        user(login: $login) {
            followers { totalCount }
        }
    }"""
    data = graphql_request(
        "follower_getter", query, {"login": username}, settings, state
    )
    return int(data["user"]["followers"]["totalCount"])


def starred_getter(
    username: str,
    settings: Settings,
    state: RuntimeState,
) -> int:
    """Fetch how many repositories the user has starred."""
    state.increment_query("starred_getter")
    query = """
    query($login: String!){
        user(login: $login) {
            starredRepositories { totalCount }
        }
    }"""
    data = graphql_request(
        "starred_getter", query, {"login": username}, settings, state
    )
    return int(data["user"]["starredRepositories"]["totalCount"])
