"""A small robots.txt parser following RFC 9309.

The standard library parser does not understand ``*`` and ``$`` in paths and picks the first matching rule instead
of the longest one, so this module implements the matching rules itself:

* groups are selected by the most specific ``User-agent`` token that is a prefix of the product token
  (case-insensitive), falling back to ``*``; several groups for the same agent are merged;
* within a group the longest matching ``Allow``/``Disallow`` path wins, and ``Allow`` wins a tie;
* ``*`` matches any sequence of characters and a trailing ``$`` anchors the end of the path;
* ``Sitemap`` lines are collected regardless of the group, ``Crawl-delay`` is read as a non-standard extension.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class Rule:
    allow: bool
    path: str
    _regex: re.Pattern[str] = field(compare=False, repr=False)

    @classmethod
    def make(cls, allow: bool, path: str) -> Rule:
        return cls(allow, path, _path_regex(path))

    def matches(self, target: str) -> bool:
        return self._regex.match(target) is not None


@dataclass
class Group:
    agents: list[str] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    crawl_delay: float | None = None


def _path_regex(path: str) -> re.Pattern[str]:
    anchored = path.endswith("$")
    if anchored:
        path = path[:-1]
    pattern = "".join(".*" if ch == "*" else re.escape(ch) for ch in _normalise_path(path))
    return re.compile(pattern + ("$" if anchored else ""))


def _normalise_path(path: str) -> str:
    # Compare decoded paths, except for an encoded slash, which has its own meaning.
    return unquote(path.replace("%2F", "%252F").replace("%2f", "%252f"))


class RobotsTxt:
    """Parsed robots.txt rules."""

    def __init__(self, groups: list[Group] | None = None, sitemaps: list[str] | None = None) -> None:
        self.groups = groups or []
        self.sitemaps = sitemaps or []
        self.allow_all = False
        self.disallow_all = False

    @classmethod
    def allowing_all(cls) -> RobotsTxt:
        robots = cls()
        robots.allow_all = True
        return robots

    @classmethod
    def disallowing_all(cls) -> RobotsTxt:
        robots = cls()
        robots.disallow_all = True
        return robots

    @classmethod
    def parse(cls, text: str) -> RobotsTxt:
        groups: list[Group] = []
        sitemaps: list[str] = []
        current: Group | None = None
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                if current is None or not last_was_agent:
                    current = Group()
                    groups.append(current)
                current.agents.append(value.lower())
                last_was_agent = True
                continue
            if key == "sitemap":
                if value:
                    sitemaps.append(value)
                continue
            last_was_agent = False
            if current is None:
                continue
            if key in ("allow", "disallow"):
                if key == "disallow" and not value:
                    continue  # "Disallow:" with an empty path allows everything
                current.rules.append(Rule.make(key == "allow", value))
            elif key == "crawl-delay":
                with contextlib.suppress(ValueError):
                    current.crawl_delay = float(value)
        return cls(groups, sitemaps)

    def _groups_for(self, user_agent: str) -> list[Group]:
        token = _product_token(user_agent)
        best_len = -1
        chosen: list[Group] = []
        for group in self.groups:
            for agent in group.agents:
                if agent == "*":
                    length = 0
                elif token.startswith(agent):
                    length = len(agent)
                else:
                    continue
                if length > best_len:
                    best_len, chosen = length, [group]
                elif length == best_len and group not in chosen:
                    chosen.append(group)
        return chosen

    def can_fetch(self, user_agent: str, url: str) -> bool:
        if self.allow_all:
            return True
        if self.disallow_all:
            return False
        parts = urlsplit(url)
        target = _normalise_path((parts.path or "/") + (f"?{parts.query}" if parts.query else ""))
        if target == "/robots.txt":
            return True
        best: Rule | None = None
        for group in self._groups_for(user_agent):
            for rule in group.rules:
                if not rule.matches(target):
                    continue
                if (
                    best is None
                    or len(rule.path) > len(best.path)
                    or (len(rule.path) == len(best.path) and rule.allow and not best.allow)
                ):
                    best = rule
        return best is None or best.allow

    def crawl_delay(self, user_agent: str) -> float | None:
        delays = [g.crawl_delay for g in self._groups_for(user_agent) if g.crawl_delay is not None]
        return max(delays) if delays else None


def _product_token(user_agent: str) -> str:
    """``"Mozilla/5.0 (compatible; llms-txt-builder/0.1)"`` -> ``"llms-txt-builder"``."""
    ua = user_agent.lower()
    match = re.search(r"([a-z0-9_-]+)/[0-9]", ua.replace("mozilla/", "", 1)) if "compatible" in ua else None
    if match:
        return match.group(1)
    return re.split(r"[/\s]", ua.strip(), maxsplit=1)[0]
