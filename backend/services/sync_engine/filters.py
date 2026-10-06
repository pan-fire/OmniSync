"""The filter rules of a push or pull, in an order that keeps OmniSync's excludes first.

rclone collects filter rules by kind, not in command-line order (verified
with rclone 1.75.1, fs/filter): every ``--include`` flag, then every
``--exclude`` flag, then the ``--filter`` flags, then the ``--filter-from``
files, and, when there was an ``--include``, an implicit ``- /**`` at the
end. The first rule that matches a path decides. A rule ``!`` clears every
rule collected before it.

OmniSync's protective excludes (manually flagged files, unresolved
conflicts, local symbolic links) must win over the profile's own rules: an
include-style profile filter (``+ /Docs/**``, ``- **``) or an ``--include``
flag would otherwise include a flagged file before the exclude is reached,
and a push or pull would overwrite it. So a push or pull passes no
filtering flag of the profile's at all: the profile's ``--include``,
``--exclude`` and ``--filter`` flags become rules in rclone's own order,
after the protective excludes, in one filter file. Which files the profile
selects does not change; only OmniSync's excludes now always come first,
and a ``!`` of the profile's can no longer clear them.
"""

from __future__ import annotations

from dataclasses import dataclass

# The profile flags that are filter rules, and the rule each one is.
_RULE_FLAGS = {"--include": "+ ", "--exclude": "- ", "--filter": ""}


def after_last_clear(rules: list[str]) -> list[str]:
    """``rules`` as rclone ends up with them: what follows the last ``!``.

    rclone's ``!`` clears every rule before it, OmniSync's included; the
    profile's rules before a ``!`` are cleared anyway, so dropping them
    (and the ``!``) keeps what the profile selects.
    """
    for i in range(len(rules) - 1, -1, -1):
        if rules[i].strip() == "!":
            return rules[i + 1:]
    return list(rules)


@dataclass
class ProfileFilters:
    """A profile's filtering, as ordered rules plus the flags that are not rules."""

    rules: list[str]
    other_args: list[str]


def profile_filters(rclone_filter: list[str], rclone_args: list[str]) -> ProfileFilters:
    """The profile's filter rules (``rclone_filter``) and filter flags as rules.

    The flags take their value from the next argument (as rclone's flag
    parser does, also one that starts with ``-``, such as ``--filter "- x"``)
    or after ``=``. The order is rclone's: includes, excludes, the rules
    (``rclone_filter`` is passed before the flags), the ``--filter`` flags,
    and ``- /**`` when there was an include.
    """
    kinds: dict[str, list[str]] = {flag: [] for flag in _RULE_FLAGS}
    other: list[str] = []
    i = 0
    while i < len(rclone_args):
        arg = rclone_args[i]
        name, eq, value = arg.partition("=")
        if name in _RULE_FLAGS and (eq or i + 1 < len(rclone_args)):
            if not eq:
                i += 1
                value = rclone_args[i]
            kinds[name].append(_RULE_FLAGS[name] + value)
        else:
            other.append(arg)
        i += 1
    rules = after_last_clear([*kinds["--include"], *kinds["--exclude"], *rclone_filter, *kinds["--filter"]])
    if kinds["--include"]:
        rules.append("- /**")
    return ProfileFilters(rules=rules, other_args=other)
