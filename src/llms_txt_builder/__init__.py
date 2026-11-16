"""Generate and validate llms.txt / llms-full.txt files for a website."""

from llms_txt_builder._version import __version__
from llms_txt_builder.build import BuildResult, build
from llms_txt_builder.collect import Collection, Collector
from llms_txt_builder.config import Config, ConfigError, load_config
from llms_txt_builder.diff import DiffResult, diff_texts
from llms_txt_builder.extract import PageInfo, extract_page
from llms_txt_builder.fetch import Fetcher, FetchSettings, make_client
from llms_txt_builder.llmstxt import Document, LinkItem, Section, parse
from llms_txt_builder.validate import Report, lint

__all__ = [
    "BuildResult",
    "Collection",
    "Collector",
    "Config",
    "ConfigError",
    "DiffResult",
    "Document",
    "FetchSettings",
    "Fetcher",
    "LinkItem",
    "PageInfo",
    "Report",
    "Section",
    "__version__",
    "build",
    "diff_texts",
    "extract_page",
    "lint",
    "load_config",
    "make_client",
    "parse",
]
