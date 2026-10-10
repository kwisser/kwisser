"""SVG template manipulation and layout constants.

All functions here are stateless with respect to GitHub API calls and runtime
counters.  They only depend on the pure helpers in *formatting*.
"""

import textwrap
from pathlib import Path

from lxml.etree import _Element, _ElementTree, parse

from kwisser.config import SVG_FILES, ProfileConfig
from kwisser.formatting import format_compact_number, format_display_text

# ---------------------------------------------------------------------------
# Visual layout constants – tweak these to adjust SVG spacing.
# ---------------------------------------------------------------------------

AGE_DATA_WIDTH: int = 49
COMMIT_DATA_WIDTH: int = 24
LOC_DATA_WIDTH: int = 25
FOLLOWER_DATA_WIDTH: int = 10
REPO_DATA_WIDTH: int = 6
STAR_DATA_WIDTH: int = 14
STARRED_DATA_WIDTH: int = 12
STATS_SECONDARY_COLUMN_WIDTH: int = 34
STATS_SECONDARY_SEPARATOR: str = " |  "

OS_DATA_WIDTH: int = 44
ROLE_DATA_WIDTH: int = 42
LOCATION_DATA_WIDTH: int = 41
STACK_BACKEND_DATA_WIDTH: int = 40
STACK_FRONTEND_DATA_WIDTH: int = 33
STACK_CLOUD_DATA_WIDTH: int = 34

# Layout specifications for single-line profile fields:
# field_name -> (element_id, target_width)
SINGLE_LINE_PROFILE_FIELDS: dict[str, tuple[str, int]] = {
    "os": ("os_data", OS_DATA_WIDTH),
    "role": ("role_data", ROLE_DATA_WIDTH),
    "location": ("location_data", LOCATION_DATA_WIDTH),
    "stack_backend": ("stack_backend_data", STACK_BACKEND_DATA_WIDTH),
    "stack_frontend": ("stack_frontend_data", STACK_FRONTEND_DATA_WIDTH),
    "stack_cloud": ("stack_cloud_data", STACK_CLOUD_DATA_WIDTH),
}

# Layout specifications for multi-line wrapped profile fields:
# field_name -> (svg_id_prefix, first_line_max_width, continuation_max_width)
WRAPPED_PROFILE_FIELD_SPECS: dict[str, tuple[str, int, int]] = {
    "stack_ai": ("stack_ai", 22, 34),
    "interests_ai": ("interests_ai", 20, 34),
    "interests_security": ("interests_security", 28, 34),
    "interests_cloud": ("interests_cloud", 28, 34),
    "learning": ("learning", 27, 34),
    "linkedin": ("linkedin", 28, 34),
}


# ---------------------------------------------------------------------------
# Low-level SVG element helpers
# ---------------------------------------------------------------------------


def find_and_replace(root: _Element, element_id: str, new_text: str) -> None:
    """Find one SVG element by its id attribute and replace its text."""
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        element.text = new_text


def build_dot_string(value_text: str, length: int) -> str:
    """Build the dot padding that visually separates a label from its value."""
    just_len = max(0, length - len(value_text))
    if just_len <= 2:
        dot_map = {0: "", 1: " ", 2: ". "}
        return dot_map[just_len]
    return " " + ("." * just_len) + " "


def justify_format(
    root: _Element,
    element_id: str,
    new_text: int | str,
    length: int = 0,
) -> None:
    """Replace one SVG text node and regenerate its matching dots spacing field."""
    text = format_display_text(new_text)
    find_and_replace(root, element_id, text)
    dot_string = build_dot_string(text, length)
    find_and_replace(root, f"{element_id}_dots", dot_string)


def secondary_stat_gap(
    left_width: int,
    target_width: int = STATS_SECONDARY_COLUMN_WIDTH,
) -> str:
    """Fill the slack before the separator to keep the second stat column aligned."""
    return (" " * max(0, target_width - left_width)) + STATS_SECONDARY_SEPARATOR


def repo_stats_left_width(repo_data: int | str, contrib_data: int | str) -> int:
    """Measure the visible width of the left side of the first GitHub stats row."""
    repo_text = format_display_text(repo_data)
    contrib_text = format_display_text(contrib_data)
    return len(
        f". Repos:{build_dot_string(repo_text, REPO_DATA_WIDTH)}{repo_text}"
        f" {{Contributed: {contrib_text}}}"
    )


def commit_stats_left_width(commit_data: int | str) -> int:
    """Measure the visible width of the left side of the second GitHub stats row."""
    commit_text = format_display_text(commit_data)
    return len(
        f". Commits:{build_dot_string(commit_text, COMMIT_DATA_WIDTH)}{commit_text}"
    )


# ---------------------------------------------------------------------------
# Profile field wrapping
# ---------------------------------------------------------------------------


def wrap_profile_value(
    value: str,
    first_width: int,
    continuation_width: int,
) -> tuple[str, str]:
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


def update_profile_fields(root: _Element, profile: ProfileConfig) -> None:
    """Update all static profile and interest fields in the SVG from configuration."""
    for field_name, (element_id, target_width) in SINGLE_LINE_PROFILE_FIELDS.items():
        value = getattr(profile, field_name)
        justify_format(root, element_id, value, target_width)

    for field_name, (svg_id_prefix, first_width, cont_width) in (
        WRAPPED_PROFILE_FIELD_SPECS.items()
    ):
        value = getattr(profile, field_name)
        line_one, line_two = wrap_profile_value(value, first_width, cont_width)
        find_and_replace(root, f"{svg_id_prefix}_value_1", line_one)
        find_and_replace(root, f"{svg_id_prefix}_value_2", line_two)


# ---------------------------------------------------------------------------
# Top-level SVG update
# ---------------------------------------------------------------------------


def svg_overwrite(
    filename: str | Path,
    age_data: str,
    commit_data: int | str,
    star_data: int | str,
    repo_data: int | str,
    contrib_data: int | str,
    follower_data: int | str,
    starred_data: int | str,
    loc_data: list[int | str],
    profile: ProfileConfig | None = None,
) -> None:
    """Open one SVG template and replace all dynamic text fields."""
    if profile is None:
        profile = ProfileConfig()

    tree: _ElementTree = parse(filename)
    root = tree.getroot()

    justify_format(root, "age_data", age_data, AGE_DATA_WIDTH)
    justify_format(root, "commit_data", commit_data, COMMIT_DATA_WIDTH)
    justify_format(root, "star_data", star_data, STAR_DATA_WIDTH)
    justify_format(root, "repo_data", repo_data, REPO_DATA_WIDTH)
    justify_format(root, "contrib_data", contrib_data)
    justify_format(root, "follower_data", follower_data, FOLLOWER_DATA_WIDTH)
    justify_format(root, "starred_data", starred_data, STARRED_DATA_WIDTH)
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
    update_profile_fields(root, profile)
    tree.write(filename, encoding="utf-8", xml_declaration=True)


def update_svg_files(
    age_data: str,
    commit_data: int | str,
    star_data: int | str,
    repo_data: int | str,
    contrib_data: int | str,
    follower_data: int | str,
    starred_data: int | str,
    loc_data: list[int | str],
    profile: ProfileConfig | None = None,
) -> None:
    """Apply the computed values to both SVG variants used by the README."""
    for svg_file in SVG_FILES:
        svg_overwrite(
            svg_file,
            age_data,
            commit_data,
            star_data,
            repo_data,
            contrib_data,
            follower_data,
            starred_data,
            loc_data,
            profile=profile,
        )


def update_profile_svgs(profile: ProfileConfig | None = None) -> None:
    """Update profile and text fields in SVG files without re-fetching GitHub API stats."""
    if profile is None:
        profile = ProfileConfig()
    for svg_file in SVG_FILES:
        tree: _ElementTree = parse(svg_file)
        root = tree.getroot()
        update_profile_fields(root, profile)
        tree.write(svg_file, encoding="utf-8", xml_declaration=True)
