#!/usr/bin/env python3
"""Build WordPress post content for the freelancer guide from Markdown.

Usage:
    python3 tools/wp_export.py --lang ru|en --reference CURRENT.html --out NEW.html

CURRENT.html is the post content currently published in WordPress
(`wp post get <id> --field=post_content`). The script carries over what exists
only in WordPress so that public links keep working:

- legacy bookmark anchors (`<a id="post-248-_Toc...">`) on headings and paragraphs;
- media-library <img> tags, matched in order to the Markdown images;
- WordPress-only blocks: the FAQ Q&A block and the related-reading block.

It refuses to write output if any id from the reference would disappear, if an
internal link would point nowhere, or if the image counts differ.
Requires pandoc.
"""
import argparse
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANGS = {
    "ru": {
        "md": ROOT / "articles/2025/freelancer-gewerbe-guide.md",
        "faq_start": "<h4>Можно ли быть фрилансером",
        "faq_before": '<h2 id="10-типичных-ошибок-новичка">',
    },
    "en": {
        "md": ROOT / "articles/2025/freelancer-gewerbe-guide-en.md",
        "faq_start": '<h3 id="faq">',
        "faq_before": '<h2 id="10-common-mistakes">',
    },
}
RELATED = "<!-- ilya-related-reading -->"
# Cross-links between the editions point to Markdown files in the repo and to posts on the site.
SITE_URLS = {
    "freelancer-gewerbe-guide.md": "https://ilyafedotov.com/freelancer_guide_v4/",
    "freelancer-gewerbe-guide-en.md": "https://ilyafedotov.com/freelancers-handbook-in-germany-v0-4-by-ilya-fedotov/",
}
FIG = '<figure class="wp-block-table" style="overflow-x:auto">'
PRE = '<pre style="white-space:pre-wrap;overflow-wrap:anywhere"'


def fail(msg):
    sys.exit(f"wp_export: {msg}")


def ids(text):
    return re.findall(r'\bid="([^"]+)"', text)


def build(lang, reference):
    cfg = LANGS[lang]
    html = subprocess.run(
        ["pandoc", "-f", "gfm", "-t", "html", "--wrap=none", str(cfg["md"])],
        capture_output=True, text=True, check=True,
    ).stdout
    html = re.sub(r'^<h1 id="[^"]*">.*?</h1>\n', "", html, count=1)
    html = re.sub(
        r'href="([^"#/:]+\.md)(#[^"]*)?"',
        lambda m: f'href="{SITE_URLS[m.group(1)]}{m.group(2) or ""}"' if m.group(1) in SITE_URLS else m.group(0),
        html,
    )
    # Relative links to repository Markdown files; absolute URLs (e.g. GitHub) are fine.
    leftover = re.findall(r'href="(?![a-zA-Z][a-zA-Z0-9+.-]*:|/)([^"]+\.md(?:#[^"]*)?)"', html)
    if leftover:
        fail("links to Markdown files have no site URL: " + ", ".join(leftover))

    # WordPress-only blocks are cut from the reference first, so they are not
    # mistaken for article content below.
    related = reference[reference.index(RELATED):].rstrip("\n") if RELATED in reference else ""
    body = reference[: reference.index(RELATED)] if related else reference
    faq = ""
    if cfg["faq_start"] in body:
        start = body.index(cfg["faq_start"])
        end = body.index(cfg["faq_before"], start)
        faq, body = body[start:end], body[:start] + body[end:]

    # Legacy anchors on headings, matched by heading id.
    heading_anchors = {
        m.group(1): m.group(2)
        for m in re.finditer(r'<h[1-6] id="([^"]+)">((?:<a id="[^"]*"></a>)+)', body)
    }
    new_heading_ids = set(re.findall(r'<h[1-6] id="([^"]+)">', html))
    missing = sorted(set(heading_anchors) - new_heading_ids)
    # A renamed heading keeps its old id as an <a id="old-id"></a> line before it
    # in the Markdown; its legacy anchors go next to that alias.
    for old in list(missing):
        alias = f'<a id="{old}"></a>'
        if alias in html:
            html = html.replace(alias, alias + heading_anchors[old], 1)
            missing.remove(old)
    if missing:
        fail("headings with legacy anchors were renamed or removed; map them first: "
             + ", ".join(missing))
    html = re.sub(
        r'<h([1-6]) id="([^"]+)">',
        lambda m: f'<h{m.group(1)} id="{m.group(2)}">' + heading_anchors.get(m.group(2), ""),
        html,
    )

    # Legacy anchors on paragraphs, matched by the paragraph's opening words.
    for m in re.finditer(r'<p>((?:<a id="[^"]*"></a>)+)(.{40})', body):
        key = "<p>" + m.group(2)
        if key not in html:
            fail(f"paragraph with anchor {m.group(1)} changed; update its opening words")
        html = html.replace(key, "<p>" + m.group(1) + m.group(2), 1)

    # Media-library images, in order.
    ref_imgs = re.findall(r"<img [^>]*>", body)
    new_imgs = re.findall(r"<img [^>]*>", html)
    if len(ref_imgs) != len(new_imgs):
        fail(f"image count differs: published {len(ref_imgs)}, Markdown {len(new_imgs)}")
    it = iter(ref_imgs)
    html = re.sub(r"<img [^>]*>", lambda _: next(it), html)

    html = html.replace("<table>", FIG + "<table>").replace("</table>", "</table></figure>")
    html = html.replace(' type="1"', "").replace("<li></li>\n", "")
    html = html.replace("<pre class=", PRE + " class=").replace("<pre><code>", PRE + "><code>")
    html = re.sub(
        r'href="#([^"]+)"',
        lambda m: 'href="#' + urllib.parse.quote(m.group(1), safe="-_.~") + '"',
        html,
    )

    if faq:
        if cfg["faq_before"] not in html:
            fail("cannot place the FAQ block: anchor heading not found")
        html = html.replace(cfg["faq_before"], faq + cfg["faq_before"], 1)
    if related:
        html = html.rstrip("\n") + "\n" + related

    if "\\" in html:
        snippet = html[max(0, html.index("\\") - 40): html.index("\\") + 20]
        fail(f"output contains a backslash, which WordPress strips on save: ...{snippet}...")
    lost = sorted(set(ids(reference)) - set(ids(html)))
    if lost:
        fail("ids from the published post would be lost: " + ", ".join(lost))
    dup = sorted({i for i in ids(html) if ids(html).count(i) > 1})
    if dup:
        fail("duplicate ids: " + ", ".join(dup))
    targets = set(ids(html))
    broken = sorted({h for h in (urllib.parse.unquote(x) for x in re.findall(r'href="#([^"]+)"', html))
                     if h not in targets})
    if broken:
        fail("internal links without target: " + ", ".join(broken))
    return html


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", choices=LANGS, required=True)
    ap.add_argument("--reference", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    html = build(a.lang, a.reference.read_text(encoding="utf-8"))
    a.out.write_text(html, encoding="utf-8")
    print(f"wp_export: wrote {a.out} ({len(html.encode())} bytes, {len(set(ids(html)))} ids)")


if __name__ == "__main__":
    main()
