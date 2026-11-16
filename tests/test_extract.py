from __future__ import annotations

from fakesite import PAGES
from llms_txt_builder.dom import parse_html
from llms_txt_builder.extract import (
    extract_page,
    site_name_from_title,
    strip_site_name,
    summarize_text,
)
from llms_txt_builder.markdown import MarkdownOptions, html_to_markdown


def md(html: str, **options) -> str:
    doc = parse_html(f"<body>{html}</body>")
    return html_to_markdown(doc.find("body"), MarkdownOptions(base_url="https://x.io/a/", **options))


class TestDom:
    def test_implicit_paragraph_and_list_closing(self):
        doc = parse_html("<div><p>one<p>two<ul><li>a<li>b</ul></div>")
        assert [p.text_content() for p in doc.find_all("p")] == ["one", "two"]
        assert [li.text_content() for li in doc.find_all("li")] == ["a", "b"]

    def test_stray_end_tags_and_unclosed(self):
        doc = parse_html("<div><span>x</b></span><section><p>y")
        assert doc.find("section").text_content() == "y"
        assert doc.find("span").text_content() == "x"

    def test_void_and_self_closing(self):
        doc = parse_html("<p>a<br>b<img src=x.png/>c</p>")
        assert doc.find("p").text_content() == "abc"
        assert doc.find("img").get("src") == "x.png/"

    def test_entities_decoded(self):
        assert parse_html("<p>Tom &amp; Jerry &copy;</p>").find("p").text_content() == "Tom & Jerry ©"

    def test_script_content_not_text(self):
        doc = parse_html("<div>a<script>var x = '<p>';</script>b</div>")
        assert doc.find("div").text_content() == "ab"
        assert doc.find("p") is None


class TestMarkdown:
    def test_headings_paragraphs_and_inline(self):
        out = md("<h2>Title</h2><p>Some <strong>bold</strong>, <em>soft</em> and <code>x_y</code> text.</p>")
        assert out == "## Title\n\nSome **bold**, *soft* and `x_y` text.\n"

    def test_links_are_absolute(self):
        assert md('<p>See <a href="../b/">the docs</a>.</p>') == "See [the docs](https://x.io/b/).\n"

    def test_link_without_text_keeps_nothing(self):
        assert md('<p>x <a href="/y"></a> z</p>') == "x z\n"

    def test_images(self):
        assert md('<p><img src="/i.png" alt="Logo"></p>') == "![Logo](https://x.io/i.png)\n"
        assert md('<p>a<img src="/i.png" alt="Logo"></p>', images=False) == "a\n"

    def test_nested_lists(self):
        out = md("<ul><li>one<ul><li>inner</li></ul></li><li>two</li></ul><ol start=3><li>c</li><li>d</li></ol>")
        assert out == "- one\n  - inner\n- two\n\n3. c\n4. d\n"

    def test_list_item_with_paragraphs(self):
        out = md("<ul><li><p>first</p><p>second</p></li></ul>")
        assert out == "- first\n  second\n"

    def test_code_block_with_language_and_backticks(self):
        out = md('<pre><code class="language-python">print("```")\n</code></pre>')
        assert out == '````python\nprint("```")\n````\n'

    def test_table(self):
        out = md(
            "<table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>1</td><td>x|y</td></tr></tbody></table>"
        )
        assert out == "| A | B |\n| --- | --- |\n| 1 | x\\|y |\n"

    def test_table_without_header_uses_first_row(self):
        out = md("<table><tr><td>a</td><td>b</td></tr><tr><td>c</td></tr></table>")
        assert out == "| a | b |\n| --- | --- |\n| c |  |\n"

    def test_blockquote_and_hr(self):
        assert md("<blockquote><p>q1</p><p>q2</p></blockquote><hr>") == "> q1\n>\n> q2\n\n---\n"

    def test_escaping(self):
        out = md("<p>1. not a list, [brackets] and * star and snake_case</p>")
        assert out == "1\\. not a list, \\[brackets\\] and \\* star and snake_case\n"

    def test_line_breaks(self):
        assert md("<p>a<br>b</p>") == "a  \nb\n"

    def test_heading_offset(self):
        assert md("<h1>A</h1><h6>B</h6>", heading_offset=2) == "### A\n\n###### B\n"

    def test_inline_and_block_mixed(self):
        assert md("text <div>block</div> tail") == "text\n\nblock\n\ntail\n"

    def test_definition_list(self):
        assert md("<dl><dt>Term</dt><dd>Meaning</dd></dl>") == "**Term**\n\nMeaning\n"


class TestExtractPage:
    def test_metadata(self):
        info = extract_page(PAGES["/docs/install/"], "https://www.example.com/docs/install/")
        assert info.title == "Installation | Example Docs"
        assert info.description == "Install Example Docs with pip and create your first site."
        assert info.h1 == "Installation"
        assert info.lang == "en"
        assert info.markdown_url == "https://www.example.com/docs/install.md"
        assert "https://www.example.com/blog/" in info.links

    def test_main_content_skips_boilerplate(self):
        info = extract_page(PAGES["/docs/install/"], "https://www.example.com/docs/install/")
        text = info.content_markdown
        assert text.startswith("# Installation")
        assert "## Requirements" in text
        assert "- Python 3.10 or newer" in text
        assert "```bash\npip install example-docs\nexample-docs init\n```" in text
        assert "| OS | Supported |" in text
        for junk in ("Copyright", "Related", "tracking", "Privacy"):
            assert junk not in text

    def test_noindex_and_canonical(self):
        hidden = extract_page(PAGES["/hidden/"], "https://www.example.com/hidden/")
        assert hidden.noindex
        copy = extract_page(PAGES["/docs/config-copy/"], "https://www.example.com/docs/config-copy/")
        assert copy.canonical == "https://www.example.com/docs/config/"

    def test_scoring_without_main_element(self):
        html = (
            "<html><body><div id='menu'><a href='/a'>A</a> <a href='/b'>B</a> <a href='/c'>C</a></div>"
            "<div class='wrapper'><div class='post-body'>"
            + "".join(
                f"<p>Paragraph {i}, with enough words, commas, and text to count as content.</p>" for i in range(5)
            )
            + "</div><div class='comments'><p>Nice post, thanks, really, great.</p></div></div>"
            "<div class='footer-links'>"
            + "".join(f"<p><a href='/{i}'>Link number {i} in the footer area</a></p>" for i in range(5))
            + "</div></body></html>"
        )
        info = extract_page(html, "https://x.io/p/")
        assert "Paragraph 0" in info.content_markdown
        assert "Paragraph 4" in info.content_markdown
        assert "Nice post" not in info.content_markdown
        assert "footer area" not in info.content_markdown

    def test_hidden_elements_removed(self):
        html = "<body><main><p>" + "visible text " * 20 + "</p><div hidden>secret</div>"
        html += "<p style='display: none'>gone</p><p aria-hidden='true'>aria</p></main></body>"
        info = extract_page(html, "https://x.io/")
        assert "secret" not in info.content_markdown
        assert "gone" not in info.content_markdown
        assert "aria" not in info.content_markdown

    def test_falls_back_to_og_title_and_description(self):
        html = (
            '<html><head><meta property="og:title" content="OG title">'
            '<meta property="og:description" content="OG description"></head><body><p>x</p></body></html>'
        )
        info = extract_page(html, "https://x.io/")
        assert info.title == "OG title"
        assert info.description == "OG description"

    def test_base_href(self):
        html = '<html><head><base href="https://cdn.x.io/root/"></head><body><a href="page">p</a></body></html>'
        assert extract_page(html, "https://x.io/").links == ["https://cdn.x.io/root/page"]

    def test_nofollow_links_skipped(self):
        html = '<body><a rel="nofollow" href="/a">a</a><a href="/b#frag">b</a><a href="/b">b</a></body>'
        assert extract_page(html, "https://x.io/").links == ["https://x.io/b"]


def test_site_name_helpers():
    assert site_name_from_title("Pricing | Example Co") == "Example Co"
    assert site_name_from_title("Just a title") == ""
    assert strip_site_name("Pricing | Example Co", "Example Co") == "Pricing"
    assert strip_site_name("Example Co - Pricing", "Example Co") == "Pricing"
    assert strip_site_name("Example Co", "Example Co") == "Example Co"


def test_summarize_text():
    text = "First sentence here. Second sentence is longer than the first one. Third."
    assert summarize_text(text, 30) == "First sentence here."
    assert summarize_text(text, 200) == text
    long_word_text = "word " * 50
    out = summarize_text(long_word_text, 40)
    assert out.endswith("…") and len(out) <= 40
