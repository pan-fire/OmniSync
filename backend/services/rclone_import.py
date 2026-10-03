"""Reading an uploaded rclone.conf for POST /remotes/import.

The file is parsed in memory; nothing of it is logged. Values are kept
verbatim: rclone has already obscured its passwords, and tokens are JSON
that must not change. A section is offered for import only if OmniSync
can take it safely:

- its name is a valid remote name (it goes onto rclone's command line);
- it has a ``type``, and that is not ``local``: a local remote would let a
  profile reach any folder on the host, OmniSync's data folder included,
  past the checks local folders get;
- no option runs a command on this machine (the SFTP backend's ``ssh``,
  WebDAV's ``bearer_token_command``): an import must not be a way to
  execute code;
- wrapping backends (crypt, alias, union, ...) point at another remote,
  never at a local path (``remote = /data`` or ``:local:/data``), and not
  at an existing remote that is ``local`` or itself wraps a local path
  (local_reference_problems, which needs the current rclone.conf);
- options that name a file rclone reads on this machine (SFTP's
  ``key_file`` and ``known_hosts_file``, ``service_account_file``, ...)
  do not point into OmniSync's data directory, which holds rclone.conf,
  the API token and the database; a value with ``$`` is refused, since
  rclone would expand environment variables in it;
- no value spans several lines (rclone.conf has no continuation lines).
"""

from __future__ import annotations

import configparser
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from backend.services.path_guard import check_not_data_dir
from backend.services.provider_registry import split_remote_path, validate_remote_name
from backend.services.rclone import RESERVED_NAME_MESSAGE, is_reserved_remote_name

# Options that make rclone run a program on this machine. The SFTP
# backend's *sum_command and server_command options run on the server.
_COMMAND_OPTIONS = frozenset({"ssh", "bearer_token_command"})
_REMOTE_SIDE_COMMANDS = frozenset({"sftp"})
_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,63}$")
# Options that name the remote(s) a wrapping backend uses.
_REFERENCE_OPTIONS = ("remote", "upstreams")
# Options naming a file on this machine that rclone reads, besides every
# option ending in "_file" (key_file, pubkey_file, known_hosts_file,
# service_account_file, ...). rclone also takes global options in a section
# with a "global." or "override." prefix.
_LOCAL_FILE_OPTIONS = frozenset({"ca_cert", "client_cert", "client_key"})
_OPTION_PREFIXES = ("global.", "override.")
# How many wrappers deep local_reference_problems follows existing remotes.
_MAX_REFERENCE_DEPTH = 16
# A configparser defaults section would be merged into every other one.
_NO_DEFAULT_SECTION = "\x00no-default"

ENCRYPTED_MARKER = "RCLONE_ENCRYPT_V0:"


class ImportParseError(ValueError):
    """The text is not an rclone.conf OmniSync can read."""


@dataclass
class ImportSection:
    """One remote of the uploaded file. ``options`` holds its values (type
    first): never return or log them."""

    name: str
    type: str
    options: dict[str, str]
    problems: list[str] = field(default_factory=list)

    @property
    def importable(self) -> bool:
        return not self.problems


def _reference_problems(key: str, value: str) -> list[str]:
    """A wrapping backend's target must be '<remote>:<path>', never a local path."""
    targets = value.split() if key == "upstreams" else [value]
    for target in targets:
        # combine: "dir=remote:path"; union: "remote:path[:ro|:nc|:writeback]".
        _, eq, rest = target.partition("=")
        if key == "upstreams" and eq and ":" not in target.split("=", 1)[0]:
            target = rest
        if split_remote_path(target) is None:
            return [f"'{key}' points at a local path or an on-the-fly backend, not at a remote"]
    return []


def _reads_local_file(key: str) -> bool:
    for prefix in _OPTION_PREFIXES:
        key = key.removeprefix(prefix)
    return key.endswith("_file") or key in _LOCAL_FILE_OPTIONS


def _local_file_problems(key: str, value: str) -> list[str]:
    """A file option may not point into OmniSync's data directory."""
    path = value.strip()
    if not path:
        return []
    if "$" in path:
        return [f"'{key}' uses an environment variable; give the file's full path"]
    try:
        check_not_data_dir(path, key)
    except ValueError:
        return [f"'{key}' points into OmniSync's data directory, which holds the remote credentials"]
    return []


def _section_problems(name: str, remote_type: str, options: dict[str, str]) -> list[str]:
    problems: list[str] = []
    if not validate_remote_name(name):
        problems.append("the name is not a valid remote name (letters, digits, '_' and '-', "
                        "not starting with '-')")
    elif is_reserved_remote_name(name):
        problems.append(RESERVED_NAME_MESSAGE)
    if not remote_type:
        problems.append("it has no type")
    elif not _TYPE_RE.match(remote_type):
        problems.append("its type is not a valid rclone backend name")
    elif remote_type == "local":
        problems.append("local remotes are not imported: profiles name local folders directly")
    for key, value in options.items():
        if not _KEY_RE.match(key):
            problems.append("it has an option with an invalid name")
        elif key in _COMMAND_OPTIONS or (key.endswith("_command") and remote_type not in _REMOTE_SIDE_COMMANDS):
            problems.append(f"'{key}' runs a program on this machine and is not imported")
        elif "\n" in value or "\r" in value:
            problems.append(f"'{key}' spans several lines")
        elif key in _REFERENCE_OPTIONS:
            problems += _reference_problems(key, value)
        elif _reads_local_file(key):
            problems += _local_file_problems(key, value)
    return list(dict.fromkeys(problems))


def parse_rclone_config(content: str) -> list[ImportSection]:
    """The sections of an rclone.conf, each with the reasons it cannot be imported.

    Raises ImportParseError for text that is not an (unencrypted) rclone.conf.
    """
    text = content.lstrip("﻿")
    if text.lstrip().startswith(ENCRYPTED_MARKER) or ENCRYPTED_MARKER in text[:4096]:
        raise ImportParseError(
            "This rclone.conf is encrypted. Remove the encryption first "
            "(rclone config encryption remove) and import the plain file."
        )
    parser = configparser.RawConfigParser(
        delimiters=("=",),
        comment_prefixes=("#", ";"),
        strict=True,
        empty_lines_in_values=False,
        default_section=_NO_DEFAULT_SECTION,
        interpolation=None,
    )
    parser.optionxform = str  # type: ignore[assignment,method-assign]  # keep key casing
    try:
        parser.read_string(text)
    except configparser.DuplicateSectionError as e:
        raise ImportParseError(f"The remote '{e.section}' appears twice.") from None
    except configparser.DuplicateOptionError as e:
        raise ImportParseError(f"The remote '{e.section}' sets '{e.option}' twice.") from None
    except configparser.MissingSectionHeaderError:
        raise ImportParseError("This is not an rclone.conf: it does not start with a [remote] section.") from None
    except configparser.Error:
        # The parser's message quotes the offending line, which may hold a secret.
        raise ImportParseError("This is not a valid rclone.conf.") from None

    sections: list[ImportSection] = []
    for name in parser.sections():
        raw = {key: (value or "") for key, value in parser.items(name)}
        remote_type = raw.pop("type", "").strip()
        options = {"type": remote_type, **raw}
        sections.append(ImportSection(
            name=name, type=remote_type, options=options,
            problems=_section_problems(name, remote_type, raw),
        ))
    return sections


def referenced_remotes(options: Mapping[str, str]) -> list[str]:
    """The names of the remotes a wrapping backend's options point at."""
    names: list[str] = []
    for key in _REFERENCE_OPTIONS:
        value = options.get(key, "")
        targets = value.split() if key == "upstreams" else ([value] if value else [])
        for target in targets:
            prefix, eq, rest = target.partition("=")
            if key == "upstreams" and eq and ":" not in prefix:
                target = rest
            split = split_remote_path(target)
            if split is not None and split[0] not in names:
                names.append(split[0])
    return names


SectionLookup = Callable[[str], Awaitable[Mapping[str, str] | None]]


async def local_reference_problems(options: Mapping[str, str], lookup: SectionLookup) -> list[str]:
    """Why a wrapping backend would reach this machine's files through
    another remote: a ``local`` one, or one wrapping a local path, directly
    or through further wrappers. ``lookup`` gives a remote's settings by
    name (the remotes being imported, then the current rclone.conf), or None.
    """
    seen: set[str] = set()
    pending = [(name, 1) for name in referenced_remotes(options)]
    while pending:
        name, depth = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        section = await lookup(name)
        if section is None:
            continue
        if section.get("type", "").strip() == "local":
            return [f"it points at '{name}', a local remote: profiles name local folders directly"]
        if any(_reference_problems(key, section[key]) for key in _REFERENCE_OPTIONS if section.get(key)):
            return [f"it points at '{name}', which wraps a local path"]
        if depth >= _MAX_REFERENCE_DEPTH:
            return ["it points at a chain of remotes too long to check"]
        pending += [(n, depth + 1) for n in referenced_remotes(section)]
    return []


def rewrite_references(options: dict[str, str], renames: dict[str, str]) -> dict[str, str]:
    """``options`` with references to renamed remotes of the same import
    updated, e.g. a crypt remote's ``remote = old:Secret`` -> ``new:Secret``."""
    if not renames:
        return dict(options)

    def rename(target: str) -> str:
        name, sep, rest = target.partition(":")
        return f"{renames[name]}{sep}{rest}" if sep and name in renames else target

    out = dict(options)
    for key in _REFERENCE_OPTIONS:
        value = out.get(key)
        if not value:
            continue
        if key == "remote":
            out[key] = rename(value)
        else:
            parts = []
            for token in value.split():
                prefix, eq, rest = token.partition("=")
                if eq and ":" not in prefix:
                    parts.append(f"{prefix}={rename(rest)}")
                else:
                    parts.append(rename(token))
            out[key] = " ".join(parts)
    return out
