"""GitHub Profile README generator for kwisser.

Fetches live GitHub stats via the GraphQL API and writes them into
both SVG template files (light_mode.svg and dark_mode.svg).
"""

import datetime
import hashlib
import os
import textwrap
import time
from pathlib import Path

import requests
from dateutil import relativedelta
from dotenv import load_dotenv
from lxml.etree import parse

load_dotenv()

# GitHub API and local file layout used by the script.
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
CACHE_DIR = Path("cache")
SVG_FILES = ("dark_mode.svg", "light_mode.svg")

# Fixed values that shape the generated README content.
COMMENT_BLOCK_SIZE = 7
# GitHub account creation date (kwisser joined GitHub)
# Will be fetched dynamically from the API; this is a fallback.
ACCOUNT_CREATED_AT = None
CACHE_COMMENT_LINE = "This line is a comment block. Write whatever you want here.\n"

# Visual widths used when inserting dot padding in the SVG text fields.
AGE_DATA_WIDTH = 49
COMMIT_DATA_WIDTH = 24
LOC_DATA_WIDTH = 25
FOLLOWER_DATA_WIDTH = 10
REPO_DATA_WIDTH = 6
STAR_DATA_WIDTH = 14
STATS_SECONDARY_COLUMN_WIDTH = 34
STATS_SECONDARY_SEPARATOR = " |  "

WRAPPED_PROFILE_FIELDS = {
    "stack_ai": ("LLMs, Multi-Agent Systems, RAG, Local LLMs", 22, 34),
    "interests_ai": ("Coding Agents, Multi-Agent Systems", 20, 34),
    "interests_security": ("IT Security, Offensive Research", 28, 34),
    "interests_cloud": ("Cloud Native Development, ML", 28, 34),
    "learning": ("Stable Multi-Agent Systems, Machine Learning, Rust", 27, 34),
    "linkedin": ("in/klemens-wisser-a4618b68", 28, 34),
}

# Simple runtime counters so the script can report how many GraphQL calls each path used.
QUERY_COUNT = {
    "user_getter": 0,
    "follower_getter": 0,
    "graph_repos_stars": 0,
    "contribution_years_getter": 0,
    "contribution_stats_getter": 0,
    "recursive_loc": 0,
    "loc_query": 0,
}

# Runtime state is populated after environment configuration and user lookup.
HEADERS = {}
USER_NAME = ""
OWNER_ID = None


def require_env(name):
    """Read one required environment variable and fail early with a precise message if missing."""
    value = os.getenv(name)
    if value:
        return value
    raise RuntimeError(f"Missing required environment variable: {name}")


def configure_environment():
    """Build the authorization header and target GitHub username used by all later API calls."""
    global HEADERS, USER_NAME
    access_token = require_env("ACCESS_TOKEN")
    USER_NAME = require_env("USER_NAME")
    HEADERS = {"authorization": f"token {access_token}"}


def cache_file_path():
    """Derive the per-user cache filename from the GitHub login."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    hashed_user = hashlib.sha256(USER_NAME.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{hashed_user}.txt"


def format_age(created_at):
    """Convert the account creation date into a human-readable uptime string for the SVG card."""
    if isinstance(created_at, str):
        created_at = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        created_at = created_at.replace(tzinfo=None)
    diff = relativedelta.relativedelta(datetime.datetime.today(), created_at)
    parts = [
        f"{diff.years} year{format_plural(diff.years)}",
        f"{diff.months} month{format_plural(diff.months)}",
        f"{diff.days} day{format_plural(diff.days)}",
    ]
    suffix = " on GitHub" if diff.months == 0 and diff.days == 0 else ""
    return ", ".join(parts) + suffix


def format_plural(value):
    """Return the plural suffix used by the age formatter."""
    return "s" if value != 1 else ""


def raise_request_error(operation_name, response):
    """Turn an HTTP error into a readable exception that includes the current query counters."""
    if response.status_code == 403:
        raise RuntimeError(
            "Too many requests in a short amount of time. GitHub returned 403."
        )
    raise RuntimeError(
        f"{operation_name} failed with status {response.status_code}: "
        f"{response.text}. Query counts: {QUERY_COUNT}"
    )


def graphql_request(operation_name, query, variables, partial_cache=None):
    """Send one GraphQL request and normalize all failure cases in one place."""
    try:
        response = requests.post(
            GITHUB_GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers=HEADERS,
            timeout=30,
        )
    except requests.RequestException as error:
        if partial_cache is not None:
            force_close_file(*partial_cache)
        raise RuntimeError(f"{operation_name} request failed: {error}") from error

    if response.status_code != 200:
        if partial_cache is not None:
            force_close_file(*partial_cache)
        raise_request_error(operation_name, response)

    try:
        payload = response.json()
    except ValueError as error:
        if partial_cache is not None:
            force_close_file(*partial_cache)
        raise RuntimeError(
            f"{operation_name} returned invalid JSON: {response.text}"
        ) from error

    if payload.get("errors"):
        if partial_cache is not None:
            force_close_file(*partial_cache)
        raise RuntimeError(
            f"{operation_name} returned GraphQL errors: {payload['errors']}"
        )

    return payload["data"]


def graph_repos_stars(count_type, owner_affiliation):
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
        query_count("graph_repos_stars")
        variables = {
            "owner_affiliation": owner_affiliation,
            "login": USER_NAME,
            "cursor": cursor,
        }
        data = graphql_request("graph_repos_stars", query, variables)
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


def format_github_datetime(value):
    """Format datetimes exactly as GitHub's GraphQL DateTime scalar expects."""
    return value.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def contribution_years_getter(username):
    """Fetch the list of years that contain visible GitHub contributions for the user."""
    query_count("contribution_years_getter")
    query = """
    query($login: String!){
        user(login: $login) {
            contributionsCollection {
                contributionYears
            }
        }
    }"""
    data = graphql_request("contribution_years_getter", query, {"login": username})
    return data["user"]["contributionsCollection"]["contributionYears"]


def contribution_stats_getter(username):
    """Count lifetime commit contributions and unique non-owned repositories contributed to."""
    years = contribution_years_getter(username)
    total_commits = 0
    contributed_repositories = set()
    username_lower = username.lower()

    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!){
        user(login: $login) {
            contributionsCollection(from: $from, to: $to) {
                totalCommitContributions
                commitContributionsByRepository(maxRepositories: 100) {
                    repository {
                        nameWithOwner
                        owner {
                            login
                        }
                    }
                }
            }
        }
    }"""

    now = datetime.datetime.now(datetime.timezone.utc)
    for year in years:
        query_count("contribution_stats_getter")
        year_start = datetime.datetime(year, 1, 1, tzinfo=datetime.timezone.utc)
        year_end = datetime.datetime(year + 1, 1, 1, tzinfo=datetime.timezone.utc)
        variables = {
            "login": username,
            "from": format_github_datetime(year_start),
            "to": format_github_datetime(min(year_end, now)),
        }
        data = graphql_request("contribution_stats_getter", query, variables)
        collection = data["user"]["contributionsCollection"]
        total_commits += collection["totalCommitContributions"]

        for repo_entry in collection["commitContributionsByRepository"]:
            repository = repo_entry["repository"]
            if repository["owner"]["login"].lower() == username_lower:
                continue
            contributed_repositories.add(repository["nameWithOwner"])

    return total_commits, len(contributed_repositories)


def recursive_loc(
    owner,
    repo_name,
    cache_rows,
    cache_header,
    addition_total=0,
    deletion_total=0,
    my_commits=0,
    cursor=None,
):
    """Traverse commit history for one repository, 100 commits at a time."""
    query_count("recursive_loc")
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
                                            user {
                                                id
                                            }
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
    )


def loc_counter_one_repo(
    owner,
    repo_name,
    cache_rows,
    cache_header,
    history,
    addition_total,
    deletion_total,
    my_commits,
):
    """Consume one page of commit history for a single repository."""
    for edge in history["edges"]:
        author = edge["node"].get("author") or {}
        user = author.get("user") or {}

        if user.get("id") == OWNER_ID:
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
        addition_total,
        deletion_total,
        my_commits,
        history["pageInfo"]["endCursor"],
    )


def loc_query(owner_affiliation, comment_size=0, force_cache=False):
    """Fetch every repository that should contribute to LOC stats."""
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
                                        history {
                                            totalCount
                                        }
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
    edges = []

    while True:
        query_count("loc_query")
        variables = {
            "owner_affiliation": owner_affiliation,
            "login": USER_NAME,
            "cursor": cursor,
        }
        data = graphql_request("loc_query", query, variables)
        repositories = data["user"]["repositories"]
        edges.extend(repositories["edges"])

        if not repositories["pageInfo"]["hasNextPage"]:
            break
        cursor = repositories["pageInfo"]["endCursor"]

    return cache_builder(edges, comment_size, force_cache)


def comment_block_lines(comment_size):
    """Build the placeholder comment block stored at the top of each cache file."""
    return [CACHE_COMMENT_LINE for _ in range(comment_size)]


def cache_builder(edges, comment_size, force_cache, loc_add=0, loc_del=0):
    """Keep a cache file that stores one row per repository."""
    cached = True
    filename = cache_file_path()

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
        expected_hash = hashlib.sha256(repository_name.encode("utf-8")).hexdigest()
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
            additions, deletions, my_commits = recursive_loc(
                owner,
                repo_name,
                cache_rows,
                cache_header,
            )
            cache_rows[index] = (
                f"{stored_hash} {current_commit_count} {my_commits} "
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


def flush_cache(edges, filename, comment_size):
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


def force_close_file(cache_rows, cache_header):
    """Persist partially updated cache data before raising from a failed LOC calculation."""
    filename = cache_file_path()
    with filename.open("w") as handle:
        handle.writelines(cache_header)
        handle.writelines(cache_rows)
    print(f"Saved partial cache data to {filename}.")


def stars_counter(edges):
    """Sum the stargazer counts for the repositories on one GraphQL page."""
    total_stars = 0
    for edge in edges:
        total_stars += edge["node"]["stargazers"]["totalCount"]
    return total_stars


def svg_overwrite(
    filename,
    age_data,
    commit_data,
    star_data,
    repo_data,
    contrib_data,
    follower_data,
    loc_data,
):
    """Open one SVG template and replace the dynamic text fields used by the README card."""
    tree = parse(filename)
    root = tree.getroot()

    justify_format(root, "age_data", age_data, AGE_DATA_WIDTH)
    justify_format(root, "commit_data", commit_data, COMMIT_DATA_WIDTH)
    justify_format(root, "star_data", star_data, STAR_DATA_WIDTH)
    justify_format(root, "repo_data", repo_data, REPO_DATA_WIDTH)
    justify_format(root, "contrib_data", contrib_data)
    justify_format(root, "follower_data", follower_data, FOLLOWER_DATA_WIDTH)
    justify_format(root, "loc_data", loc_data[2], LOC_DATA_WIDTH)
    justify_format(root, "loc_add", format_compact_number(loc_data[0]))
    justify_format(root, "loc_del", format_compact_number(loc_data[1]), 5)
    find_and_replace(
        root,
        "repo_stats_gap",
        secondary_stat_gap(repo_stats_left_width(repo_data, contrib_data)),
    )
    find_and_replace(
        root,
        "commit_stats_gap",
        secondary_stat_gap(commit_stats_left_width(commit_data)),
    )
    update_wrapped_profile_fields(root)
    tree.write(filename, encoding="utf-8", xml_declaration=True)


def justify_format(root, element_id, new_text, length=0):
    """Replace one SVG text node and regenerate its matching dots spacing field."""
    new_text = format_display_text(new_text)
    find_and_replace(root, element_id, new_text)

    dot_string = build_dot_string(new_text, length)
    find_and_replace(root, f"{element_id}_dots", dot_string)


def format_display_text(value):
    """Normalize values to the exact text form shown inside the SVG card."""
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def wrap_profile_value(value, first_width, continuation_width):
    """Wrap one profile field into up to two lines without splitting words."""
    wrapped_lines = textwrap.wrap(
        value,
        width=first_width,
        break_long_words=False,
        break_on_hyphens=False,
    )

    if not wrapped_lines:
        return "", ""
    if len(wrapped_lines) == 1:
        return wrapped_lines[0], ""

    first_line = wrapped_lines[0]
    remainder = " ".join(wrapped_lines[1:])
    continuation_lines = textwrap.wrap(
        remainder,
        width=continuation_width,
        break_long_words=False,
        break_on_hyphens=False,
    )
    second_line = " ".join(continuation_lines) if continuation_lines else remainder
    return first_line, second_line


def update_wrapped_profile_fields(root):
    """Update wrapped profile values so long text uses dedicated continuation rows."""
    for field_id, (
        value,
        first_width,
        continuation_width,
    ) in WRAPPED_PROFILE_FIELDS.items():
        line_one, line_two = wrap_profile_value(value, first_width, continuation_width)
        find_and_replace(root, f"{field_id}_value_1", line_one)
        find_and_replace(root, f"{field_id}_value_2", line_two)


def build_dot_string(value_text, length):
    """Build the dot padding that visually separates a label from its value in the SVG."""
    just_len = max(0, length - len(value_text))
    if just_len <= 2:
        dot_map = {0: "", 1: " ", 2: ". "}
        return dot_map[just_len]
    return " " + ("." * just_len) + " "


def secondary_stat_gap(left_width, target_width=STATS_SECONDARY_COLUMN_WIDTH):
    """Keep the second stat column aligned by filling any slack before the separator."""
    return (" " * max(0, target_width - left_width)) + STATS_SECONDARY_SEPARATOR


def repo_stats_left_width(repo_data, contrib_data):
    """Measure the visible width of the left side of the first GitHub stats row."""
    repo_text = format_display_text(repo_data)
    contrib_text = format_display_text(contrib_data)
    return len(
        f". Repos:{build_dot_string(repo_text, REPO_DATA_WIDTH)}{repo_text}"
        f" {{Contributed: {contrib_text}}}"
    )


def commit_stats_left_width(commit_data):
    """Measure the visible width of the left side of the second GitHub stats row."""
    commit_text = format_display_text(commit_data)
    return len(
        f". Commits:{build_dot_string(commit_text, COMMIT_DATA_WIDTH)}{commit_text}"
    )


def find_and_replace(root, element_id, new_text):
    """Find one SVG element by its id attribute and replace its text if it exists."""
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        element.text = new_text


def format_compact_number(value):
    """Shorten large numeric values so the SVG does not overflow."""
    if isinstance(value, str):
        normalized = value.replace(",", "").strip().upper()
        if normalized.endswith("M"):
            return value
        if normalized.endswith("K"):
            return value
        value = int(normalized)

    absolute_value = abs(value)
    if absolute_value >= 1_000_000:
        formatted = f"{value / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{formatted}M"
    if absolute_value >= 1_000:
        formatted = f"{value / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{formatted}K"
    return str(value)


def commit_counter(comment_size):
    """Read the cache file and sum only the 'my commits' column for the final README stat."""
    total_commits = 0
    filename = cache_file_path()
    with filename.open("r") as handle:
        data = handle.readlines()
    for line in data[comment_size:]:
        total_commits += int(line.split()[2])
    return total_commits


def user_getter(username):
    """Fetch the GitHub user id and account creation date."""
    query_count("user_getter")
    query = """
    query($login: String!){
        user(login: $login) {
            id
            createdAt
        }
    }"""
    data = graphql_request("user_getter", query, {"login": username})
    user_id = data["user"]["id"]
    created_at_str = data["user"]["createdAt"]
    # Parse ISO 8601 format: "2019-03-15T12:00:00Z"
    created_at = datetime.datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
    created_at = created_at.replace(tzinfo=None)
    return user_id, created_at


def follower_getter(username):
    """Fetch the follower count shown on the SVG card."""
    query_count("follower_getter")
    query = """
    query($login: String!){
        user(login: $login) {
            followers {
                totalCount
            }
        }
    }"""
    data = graphql_request("follower_getter", query, {"login": username})
    return int(data["user"]["followers"]["totalCount"])


def query_count(function_name):
    """Increment the per-function GraphQL counters."""
    QUERY_COUNT[function_name] += 1


def perf_counter(function, *args):
    """Run one function and return both its result and the elapsed wall-clock time."""
    start = time.perf_counter()
    result = function(*args)
    return result, time.perf_counter() - start


def print_duration(label, duration):
    """Print one timing line in a compact human-readable format."""
    metric = f"{duration:.4f} s" if duration > 1 else f"{duration * 1000:.4f} ms"
    print(f"   {label + ':':<20}{metric:>12}")


def update_svg_files(
    age_data,
    commit_data,
    star_data,
    repo_data,
    contrib_data,
    follower_data,
    loc_data,
):
    """Apply the same computed values to both SVG variants used by the README."""
    for svg_file in SVG_FILES:
        svg_overwrite(
            svg_file,
            age_data,
            commit_data,
            star_data,
            repo_data,
            contrib_data,
            follower_data,
            loc_data,
        )


def main():
    """Main pipeline: load credentials, fetch GitHub stats, update SVG files."""
    global OWNER_ID

    configure_environment()

    print("Calculation times:")

    (OWNER_ID, account_created_at), user_time = perf_counter(user_getter, USER_NAME)
    print(f"   User ID: {OWNER_ID}")
    print(f"   Account created: {account_created_at}")
    print_duration("account data", user_time)

    age_data, age_time = perf_counter(format_age, account_created_at)
    print_duration("age calculation", age_time)

    total_loc, loc_time = perf_counter(
        loc_query,
        ["OWNER", "COLLABORATOR", "ORGANIZATION_MEMBER"],
        COMMENT_BLOCK_SIZE,
    )
    print_duration("LOC (cached)" if total_loc[-1] else "LOC (no cache)", loc_time)

    (commit_data, contrib_data), contribution_time = perf_counter(
        contribution_stats_getter, USER_NAME
    )
    print_duration("contributions", contribution_time)

    star_data, star_time = perf_counter(graph_repos_stars, "stars", ["OWNER"])
    print_duration("stars", star_time)

    repo_data, repo_time = perf_counter(graph_repos_stars, "repos", ["OWNER"])
    print_duration("repos", repo_time)

    follower_data, follower_time = perf_counter(follower_getter, USER_NAME)
    print_duration("followers", follower_time)

    # Format LOC values for display (keep the boolean cache flag untouched in last slot).
    total_loc[:-1] = [f"{value:,}" for value in total_loc[:-1]]

    update_svg_files(
        age_data,
        commit_data,
        star_data,
        repo_data,
        contrib_data,
        follower_data,
        total_loc[:-1],
    )

    total_runtime = (
        user_time
        + age_time
        + loc_time
        + contribution_time
        + star_time
        + repo_time
        + follower_time
    )
    print(f"{'Total function time:':<21} {total_runtime:>11.4f} s")
    print(f"Total GitHub GraphQL API calls: {sum(QUERY_COUNT.values()):>3}")
    for function_name, count in QUERY_COUNT.items():
        print(f"   {function_name + ':':<25} {count:>6}")


if __name__ == "__main__":
    main()
