"""Entry point for the kwisser README generator.

Run with:
    uv run kwisser
or:
    python -m kwisser
"""

from kwisser.config import COMMENT_BLOCK_SIZE, RuntimeState, Settings
from kwisser.formatting import format_age, perf_counter, print_duration
from kwisser.github_api import (
    contribution_stats_getter,
    follower_getter,
    graph_repos_stars,
    starred_getter,
    user_getter,
)
from kwisser.loc import loc_query
from kwisser.svg import update_svg_files


def main() -> None:
    """Main pipeline: load credentials, fetch GitHub stats, update SVG files."""
    settings = Settings()
    state = RuntimeState()

    print("Calculation times:")

    (state.owner_id, account_created_at), user_time = perf_counter(
        user_getter, settings.user_name, settings, state
    )
    print(f"   User ID: {state.owner_id}")
    print(f"   Account created: {account_created_at}")
    print_duration("account data", user_time)

    age_data, age_time = perf_counter(format_age, account_created_at)
    print_duration("age calculation", age_time)

    total_loc, loc_time = perf_counter(
        loc_query,
        ["OWNER", "COLLABORATOR", "ORGANIZATION_MEMBER"],
        COMMENT_BLOCK_SIZE,
        settings,
        state,
    )
    print_duration("LOC (cached)" if total_loc[-1] else "LOC (no cache)", loc_time)

    (commit_data, contrib_data), contribution_time = perf_counter(
        contribution_stats_getter, settings.user_name, settings, state
    )
    print_duration("contributions", contribution_time)

    star_data, star_time = perf_counter(
        graph_repos_stars, "stars", ["OWNER"], settings, state
    )
    print_duration("stars", star_time)

    repo_data, repo_time = perf_counter(
        graph_repos_stars, "repos", ["OWNER"], settings, state
    )
    print_duration("repos", repo_time)

    follower_data, follower_time = perf_counter(
        follower_getter, settings.user_name, settings, state
    )
    print_duration("followers", follower_time)

    starred_data, starred_time = perf_counter(
        starred_getter, settings.user_name, settings, state
    )
    print_duration("starred", starred_time)

    # Format LOC values for display (keep the boolean cache flag untouched in last slot).
    total_loc[:-1] = [f"{value:,}" for value in total_loc[:-1]]

    update_svg_files(
        age_data,
        commit_data,
        star_data,
        repo_data,
        contrib_data,
        follower_data,
        starred_data,
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
        + starred_time
    )
    print(f"{'Total function time:':<21} {total_runtime:>11.4f} s")
    print(f"Total GitHub GraphQL API calls: {sum(state.query_count.values()):>3}")
    for function_name, count in state.query_count.items():
        print(f"   {function_name + ':':<25} {count:>6}")


if __name__ == "__main__":
    main()
