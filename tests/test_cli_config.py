from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from fakesite import HOST, FakeSite
from llms_txt_builder.cli import EXAMPLE_CONFIG, main
from llms_txt_builder.config import Config, ConfigError, config_from_dict, load_config

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class TestConfig:
    def test_defaults(self):
        config = Config()
        assert config.output.default_section == "Pages"
        assert config.source.respect_robots

    @pytest.mark.parametrize("name", ["llms-txt.toml", "llms-txt.yaml"])
    def test_examples_load(self, name):
        if name.endswith(".yaml"):
            pytest.importorskip("yaml")
        config = load_config(EXAMPLES / name)
        assert config.sections

    def test_init_template_is_valid(self, tmp_path):
        path = tmp_path / "c.toml"
        path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
        assert [s.name for s in load_config(path).sections] == ["Docs", "Product", "Blog", "Legal"]

    def test_string_match_and_notes(self):
        config = config_from_dict({"site": {"notes": "one"}, "sections": [{"name": "A", "match": "/a/**"}]})
        assert config.site.notes == ["one"]
        assert config.sections[0].matches("https://x.io/a/b")

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            ({"nope": {}}, "unknown top-level"),
            ({"site": {"title": "x"}}, "unknown key"),
            ({"output": {"title_source": "slug"}}, "title_source"),
            ({"output": {"sort": "random"}}, "output.sort"),
            ({"output": {"full": "yes"}}, "true or false"),
            ({"source": {"max_pages": "10"}}, "integer"),
            ({"source": {"concurrency": 0}}, "concurrency"),
            ({"filter": {"exclude": "/tag/"}}, "list of strings"),
            ({"sections": [{"name": "A"}, {"name": "A"}]}, "defined twice"),
            ({"sections": [{"name": ""}]}, "needs a name"),
            ({"sections": [{"name": "A", "match": ["re:("]}]}, "bad pattern"),
            ({"sections": [{"name": "A", "overflow": "spill"}]}, "overflow"),
            ({"links": [{"section": "A", "title": "t", "url": "/relative"}]}, "absolute"),
            ({"site": {"name": 3}}, "must be a string"),
        ],
    )
    def test_errors(self, data, message):
        with pytest.raises(ConfigError, match=message):
            config_from_dict(data)

    def test_bad_toml(self, tmp_path):
        path = tmp_path / "c.toml"
        path.write_text("[site\nname=1", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(path)
        with pytest.raises(ConfigError, match="cannot read"):
            load_config(tmp_path / "missing.toml")


class TestBuildCommand:
    def test_build_from_sitemap_writes_both_files(self, site: FakeSite, tmp_path, capsys):
        config = tmp_path / "c.toml"
        config.write_text('[[sections]]\nname = "Docs"\nmatch = ["/docs/**"]\n', encoding="utf-8")
        report = tmp_path / "report.json"
        code = main(
            [
                "build",
                "--sitemap",
                f"{HOST}/sitemap_index.xml",
                "-c",
                str(config),
                "-o",
                str(tmp_path / "out"),
                "--exclude",
                "/tag/**",
                "--report",
                str(report),
            ],
            transport=site.transport,
        )
        assert code == 0
        text = (tmp_path / "out" / "llms.txt").read_text(encoding="utf-8")
        assert text.startswith("# Example Docs\n\n> Example Docs is a static documentation generator")
        assert "## Docs\n\n- [Documentation](https://www.example.com/docs/)" in text
        assert (tmp_path / "out" / "llms-full.txt").read_text(encoding="utf-8").startswith("# Example Docs")
        data = json.loads(report.read_text(encoding="utf-8"))
        assert {"pages", "dropped", "skipped", "errors"} <= set(data)
        assert any(s["reason"] == "disallowed by robots.txt" for s in data["skipped"])
        assert "wrote" in capsys.readouterr().out

    def test_build_writes_markdown_pages(self, site: FakeSite, tmp_path, capsys):
        out = tmp_path / "out"
        report = tmp_path / "report.json"
        args = ["build", "--sitemap", f"{HOST}/sitemap_index.xml", "-o", str(out), "--no-full", "--markdown-pages"]
        assert main([*args, "--report", str(report)], transport=site.transport) == 0
        page = out / "docs" / "install" / "index.html.md"
        assert page.read_text(encoding="utf-8").startswith("# Installation")
        assert (out / "index.html.md").is_file()
        assert not (out / "llms-full.txt").exists()
        assert "docs/install/index.html.md" in json.loads(report.read_text(encoding="utf-8"))["markdown_pages"]
        assert "Markdown page(s)" in capsys.readouterr().out

    def test_build_stdout_with_crawl_and_overrides(self, site: FakeSite, capsys):
        code = main(
            ["build", "--crawl", f"{HOST}/", "--stdout", "--name", "Docs!", "--summary", "S.", "--max-pages", "3"],
            transport=site.transport,
        )
        out = capsys.readouterr().out
        assert code == 0
        assert out.startswith("# Docs!\n\n> S.\n")
        assert out.count("\n- [") == 3

    def test_build_from_url_list(self, site: FakeSite, tmp_path, capsys):
        urls = tmp_path / "urls.txt"
        urls.write_text(f"{HOST}/docs/\n{HOST}/docs/install/\n", encoding="utf-8")
        assert main(["build", "--urls", str(urls), "--stdout", "--no-full"], transport=site.transport) == 0
        out = capsys.readouterr().out
        assert "[Installation](https://www.example.com/docs/install/)" in out

    def test_build_site_discovers_sitemap_from_robots(self, site: FakeSite, capsys):
        assert main(["build", "--site", HOST, "--stdout"], transport=site.transport) == 0
        assert "/sitemap_index.xml" in site.paths()
        assert "Release 1.2" in capsys.readouterr().out

    def test_build_site_falls_back_to_crawl(self, capsys):
        site = FakeSite(robots="User-agent: *\nDisallow:\n")
        site.extra.pop("/sitemap_index.xml")
        assert main(["build", "--site", HOST, "--stdout"], transport=site.transport) == 0
        assert "/sitemap.xml" in site.paths()
        assert "Installation" in capsys.readouterr().out

    def test_ignore_robots(self, site: FakeSite, capsys):
        main(
            ["build", "--sitemap", f"{HOST}/page-sitemap.xml", "--stdout", "--ignore-robots"], transport=site.transport
        )
        assert "/robots.txt" not in site.paths()
        assert "/private/" in capsys.readouterr().out

    def test_no_source(self, capsys):
        assert main(["build"]) == 2

    def test_bad_config_exit_code(self, tmp_path):
        config = tmp_path / "c.toml"
        config.write_text("[output]\nsort = 'random'\n", encoding="utf-8")
        assert main(["build", "--crawl", f"{HOST}/", "-c", str(config)]) == 2

    def test_unreachable_sitemap(self):
        site = FakeSite()
        site.extra["/sitemap_index.xml"] = httpx.Response(500)
        assert (
            main(
                ["build", "--sitemap", f"{HOST}/sitemap_index.xml", "--stdout", "--retries", "0"],
                transport=site.transport,
            )
            == 3
        )


class TestValidateCommand:
    def test_valid_file(self, capsys):
        assert main(["validate", str(EXAMPLES / "llms.txt")]) == 0
        assert "0 error(s), 0 warning(s)" in capsys.readouterr().out

    def test_errors_exit_1_and_json(self, tmp_path, capsys):
        bad = tmp_path / "llms.txt"
        bad.write_text("## No title\n\n- not a link\n", encoding="utf-8")
        assert main(["validate", str(bad), "-f", "json"]) == 1
        data = json.loads(capsys.readouterr().out)
        assert {i["code"] for i in data["issues"]} >= {"LT002", "LT007"}

    def test_strict_warnings(self, tmp_path, capsys):
        warn = tmp_path / "llms.txt"
        warn.write_text("# T\n\n## S\n\n- [a](https://x.io/)\n", encoding="utf-8")
        assert main(["validate", str(warn)]) == 0
        assert main(["validate", str(warn), "--strict", "-f", "github"]) == 1
        assert "::warning file=" in capsys.readouterr().out

    def test_remote_file_with_link_check(self, capsys):
        site = FakeSite()
        site.extra["/llms.txt"] = httpx.Response(
            200, text=f"# Docs\n\n> s\n\n## S\n\n- [ok]({HOST}/docs/)\n- [missing]({HOST}/missing/)\n"
        )
        code = main(["validate", f"{HOST}/llms.txt", "--check-links"], transport=site.transport)
        out = capsys.readouterr().out
        assert code == 1
        assert "LT016" in out and "2 link(s) checked" in out

    def test_missing_file(self, tmp_path):
        assert main(["validate", str(tmp_path / "nope.txt")]) == 2

    def test_remote_404(self):
        assert main(["validate", f"{HOST}/llms.txt", "--retries", "0"], transport=FakeSite().transport) == 3


class TestDiffCommand:
    def test_identical_and_different(self, tmp_path, capsys):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("# A\n\n> s\n\n## S\n\n- [x](https://x.io/1)\n", encoding="utf-8")
        b.write_text("# A\n\n> s\n\n## S\n\n- [x](https://x.io/1)\n- [y](https://x.io/2)\n", encoding="utf-8")
        assert main(["diff", str(a), str(a)]) == 0
        assert main(["diff", str(a), str(b)]) == 1
        assert "+ [S] https://x.io/2" in capsys.readouterr().out
        assert main(["diff", str(a), str(b), "-f", "unified"]) == 1
        assert "+- [y](https://x.io/2)" in capsys.readouterr().out
        assert main(["diff", str(a), str(b), "-f", "json"]) == 1
        assert json.loads(capsys.readouterr().out)["counts"] == {"link-added": 1}

    def test_diff_against_live(self, tmp_path, capsys):
        site = FakeSite()
        site.extra["/llms.txt"] = httpx.Response(200, text=(EXAMPLES / "llms.txt").read_text(encoding="utf-8"))
        assert main(["diff", f"{HOST}/llms.txt", str(EXAMPLES / "llms.txt")], transport=site.transport) == 0

    def test_diff_fetch_error(self, tmp_path):
        assert (
            main(["diff", f"{HOST}/llms.txt", f"{HOST}/llms.txt", "--retries", "0"], transport=FakeSite().transport)
            == 3
        )


class TestInit:
    def test_init(self, tmp_path, capsys):
        path = tmp_path / "llms-txt.toml"
        assert main(["init", str(path)]) == 0
        assert path.read_text(encoding="utf-8") == EXAMPLE_CONFIG
        assert main(["init", str(path)]) == 2
        assert main(["init", str(path), "--force"]) == 0


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "llms-txt-builder 0.1.0" in capsys.readouterr().out
