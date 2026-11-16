"""Configuration: dataclasses plus a strict TOML / YAML loader."""

from __future__ import annotations

import sys
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10 only
    import tomli as tomllib

from llms_txt_builder.fetch import DEFAULT_USER_AGENT, ROBOTS_AGENT
from llms_txt_builder.urls import UrlPattern, compile_patterns

OPTIONAL_SECTION = "Optional"


class ConfigError(ValueError):
    """The configuration file is invalid."""


@dataclass
class SiteConfig:
    name: str = ""
    summary: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class SourceConfig:
    sitemaps: list[str] = field(default_factory=list)
    crawl: str = ""
    urls_file: str = ""
    max_pages: int = 500
    max_depth: int = 5
    concurrency: int = 8
    delay: float = 0.0
    timeout: float = 20.0
    retries: int = 2
    respect_robots: bool = True
    user_agent: str = DEFAULT_USER_AGENT
    robots_agent: str = ROBOTS_AGENT
    sitemap_depth: int = 3


@dataclass
class FilterConfig:
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    skip_noindex: bool = True
    drop_non_canonical: bool = True
    allow_query_urls: bool = False


@dataclass
class OutputConfig:
    title_source: str = "auto"  # auto | title | h1
    strip_site_name: bool = True
    description_source: str = "auto"  # auto | meta | content | none
    max_description_length: int = 160
    link_to_markdown: bool = False
    default_section: str = "Pages"
    sort: str = "input"  # input | url | title
    full: bool = True
    full_max_chars_per_page: int = 0
    full_demote_headings: bool = True
    images: bool = False


@dataclass
class SectionRule:
    name: str
    match: list[str] = field(default_factory=list)
    max_items: int = 0
    sort: str = ""
    optional: bool = False
    overflow: str = "drop"  # drop | optional
    patterns: list[UrlPattern] = field(default_factory=list, repr=False, compare=False)

    def matches(self, url: str) -> bool:
        return any(p.matches(url) for p in self.patterns)


@dataclass
class ExtraLink:
    section: str
    title: str
    url: str
    description: str = ""


@dataclass
class Config:
    site: SiteConfig = field(default_factory=SiteConfig)
    source: SourceConfig = field(default_factory=SourceConfig)
    filter: FilterConfig = field(default_factory=FilterConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    sections: list[SectionRule] = field(default_factory=list)
    links: list[ExtraLink] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        o = self.output
        if o.title_source not in ("auto", "title", "h1"):
            raise ConfigError(f"output.title_source must be auto, title or h1, not {o.title_source!r}")
        if o.description_source not in ("auto", "meta", "content", "none"):
            raise ConfigError(
                f"output.description_source must be auto, meta, content or none, not {o.description_source!r}"
            )
        if o.sort not in ("input", "url", "title"):
            raise ConfigError(f"output.sort must be input, url or title, not {o.sort!r}")
        if o.max_description_length < 20:
            raise ConfigError("output.max_description_length must be at least 20")
        if self.source.concurrency < 1 or self.source.concurrency > 64:
            raise ConfigError("source.concurrency must be between 1 and 64")
        if self.source.max_pages < 1:
            raise ConfigError("source.max_pages must be at least 1")
        names: set[str] = set()
        for rule in self.sections:
            if not rule.name.strip():
                raise ConfigError("every [[sections]] entry needs a name")
            if rule.name in names:
                raise ConfigError(f"section {rule.name!r} is defined twice")
            names.add(rule.name)
            if rule.sort not in ("", "input", "url", "title"):
                raise ConfigError(f"section {rule.name!r}: sort must be input, url or title")
            if rule.overflow not in ("drop", "optional"):
                raise ConfigError(f"section {rule.name!r}: overflow must be drop or optional")
            if rule.max_items < 0:
                raise ConfigError(f"section {rule.name!r}: max_items cannot be negative")
            try:
                rule.patterns = compile_patterns(rule.match)
            except Exception as exc:  # re.error
                raise ConfigError(f"section {rule.name!r}: bad pattern: {exc}") from exc
        for name in ("include", "exclude"):
            try:
                compile_patterns(getattr(self.filter, name))
            except Exception as exc:
                raise ConfigError(f"filter.{name}: bad pattern: {exc}") from exc
        for link in self.links:
            if not link.url.startswith(("http://", "https://")):
                raise ConfigError(f"[[links]] {link.title!r}: url must be absolute (http or https)")


def _default(f: Any) -> Any:
    if f.default is not MISSING:
        return f.default
    if f.default_factory is not MISSING:
        return f.default_factory()
    return ""  # required fields (section and link names) are strings


def _build(cls: type, data: Any, where: str) -> Any:
    if not isinstance(data, dict):
        raise ConfigError(f"{where} must be a table/mapping")
    known = {f.name: f for f in fields(cls) if f.name != "patterns"}
    unknown = sorted(set(data) - set(known))
    if unknown:
        raise ConfigError(f"unknown key(s) in {where}: {', '.join(unknown)}")
    values: dict[str, Any] = {}
    for key, value in data.items():
        default = _default(known[key])
        if isinstance(default, str) and not isinstance(value, str):
            raise ConfigError(f"{where}.{key} must be a string")
        if key in ("notes", "sitemaps") and isinstance(value, str):
            value = [value]
        if isinstance(default, bool) and not isinstance(value, bool):
            raise ConfigError(f"{where}.{key} must be true or false")
        if isinstance(default, int) and not isinstance(default, bool) and not isinstance(value, int):
            raise ConfigError(f"{where}.{key} must be an integer")
        if isinstance(default, float) and not isinstance(value, (int, float)):
            raise ConfigError(f"{where}.{key} must be a number")
        if isinstance(default, list) and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            raise ConfigError(f"{where}.{key} must be a list of strings")
        values[key] = value
    try:
        return cls(**values)
    except TypeError as exc:
        raise ConfigError(f"{where}: {exc}") from exc


def config_from_dict(data: dict[str, Any]) -> Config:
    allowed = {"site", "source", "filter", "output", "sections", "links"}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigError(f"unknown top-level key(s): {', '.join(unknown)}")
    sections = data.get("sections", [])
    links = data.get("links", [])
    if not isinstance(sections, list) or not isinstance(links, list):
        raise ConfigError("sections and links must be lists of tables")
    rules = []
    for i, s in enumerate(sections):
        if isinstance(s, dict) and isinstance(s.get("match"), str):
            s = {**s, "match": [s["match"]]}
        rules.append(_build(SectionRule, s, f"sections[{i}]"))
    return Config(
        site=_build(SiteConfig, data.get("site", {}), "site"),
        source=_build(SourceConfig, data.get("source", {}), "source"),
        filter=_build(FilterConfig, data.get("filter", {}), "filter"),
        output=_build(OutputConfig, data.get("output", {}), "output"),
        sections=rules,
        links=[_build(ExtraLink, link, f"links[{i}]") for i, link in enumerate(links)],
    )


def load_config(path: str | Path) -> Config:
    """Load a ``.toml``, ``.yaml`` or ``.yml`` configuration file."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ConfigError("YAML configuration needs PyYAML: pip install 'llms-txt-builder[yaml]'") from exc
        try:
            data = yaml.safe_load(raw.decode("utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path}: {exc}") from exc
    else:
        try:
            data = tomllib.loads(raw.decode("utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the top level must be a mapping")
    return config_from_dict(data)
