"""The order of a push or pull's filter rules (filters.py), without rclone.

test_filter_order_integration.py runs the same cases with the real binary.
"""

from __future__ import annotations

import pytest

from backend.services.sync_engine.common import filter_escape
from backend.services.sync_engine.filters import after_last_clear, profile_filters


def test_flags_become_rules_in_rclones_order():
    """rclone: --include, then --exclude, then --filter rules, then - /** after an include."""
    result = profile_filters(
        ["- *.tmp"],
        ["--filter", "- *.bak", "--exclude=cache/**", "--transfers", "4", "--include", "/Docs/**",
         "--include=/Notes/**", "--fast-list", "--exclude", "-weird"],
    )
    assert result.rules == ["+ /Docs/**", "+ /Notes/**", "- cache/**", "- -weird", "- *.tmp", "- *.bak", "- /**"]
    assert result.other_args == ["--transfers", "4", "--fast-list"]


def test_no_filtering_gives_no_rules():
    assert profile_filters([], ["--bwlimit", "1M"]).rules == []
    # A flag without its value is left for rclone to refuse.
    assert profile_filters([], ["--include"]).other_args == ["--include"]


@pytest.mark.parametrize(("rules", "kept"), [
    (["- a", "!", "+ b", "- **"], ["+ b", "- **"]),
    (["- a", " ! ", "+ b", "!"], []),
    (["- a", "+ b"], ["- a", "+ b"]),
])
def test_a_clear_rule_keeps_only_what_follows_it(rules, kept):
    assert after_last_clear(rules) == kept


def test_a_clear_rule_among_the_flags_clears_the_flags_too():
    result = profile_filters(["!", "+ /Docs/**"], ["--include", "x", "--exclude", "y"])
    # The implicit exclude of an --include is added after everything, also after a clear.
    assert result.rules == ["+ /Docs/**", "- /**"]


@pytest.mark.parametrize(("path", "escaped"), [
    ("a[1]*.txt", "a\\[1\\]\\*.txt"),
    ("notes.txt ", "notes.txt[ ]"),
    (" both \t", " both[ ][\t]"),
    ("sub dir/x", "sub dir/x"),
])
def test_filter_escape_keeps_whitespace_at_the_end(path, escaped):
    assert filter_escape(path) == escaped
