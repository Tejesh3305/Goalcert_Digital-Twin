"""Build print-quality PDFs from the docs/*.md reference set.

Pipeline, all offline after the first npx fetch:

    .md  --(extract mermaid)-->  .mmd  --mermaid-cli-->  .svg
     |                                                     |
     +--(placeholder)--> marked --> HTML body <--(inline)--+
                                        |
                              styled template + print CSS
                                        |
                          Chrome --headless --print-to-pdf
                                        |
                                      .pdf

Nothing is installed into the project's venv; `npx -y` caches Node tools
outside the repo. Chrome is used rather than a Python PDF library because it
renders the SVG diagrams and the CSS identically to what a reviewer sees on
GitHub, which is the whole point of the exercise.
"""
from __future__ import annotations

import html
import re
import shutil
import subprocess
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent
WORK = DOCS / ".pdfbuild"          # scratch, gitignored


def _find_chrome() -> str:
    """Chrome or Edge, wherever this machine keeps it.

    Either will do: both are Chromium, and both implement --print-to-pdf
    and the named-page CSS the landscape figures depend on.
    """
    import os
    pf, pf86 = os.environ.get('ProgramFiles', ''), os.environ.get('ProgramFiles(x86)', '')
    for c in [os.environ.get('CHROME'),
              os.path.join(pf, 'Google', 'Chrome', 'Application', 'chrome.exe'),
              os.path.join(pf86, 'Google', 'Chrome', 'Application', 'chrome.exe'),
              os.path.join(pf86, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
              os.path.join(pf, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
              '/usr/bin/google-chrome', '/usr/bin/chromium',
              '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']:
        if c and Path(c).exists():
            return c
    raise SystemExit('No Chrome or Edge found. Set CHROME to its path.')


CHROME = _find_chrome()

TITLES = {
    "API-SPECIFICATION.md": ("API Specification",
        "188 routes · OpenAPI 3.1.0 · authentication and authorisation model"),
    "ER-DIAGRAM.md": ("Data Model",
        "Relational schema, the Neo4j graph, and the seam between them"),
    "AWS-ARCHITECTURE.md": ("AWS Component Architecture",
        "Runtime topology, the four stores, failure modes and security posture"),
    "DIGITAL-TWIN-FEATURES.md": ("Digital Twin Features",
        "108 features across the platform — what is built, what needs porting, "
        "what is not started"),
}

# Mermaid theme pinned to the product's brand so the diagrams in the PDF match
# the application's own palette rather than mermaid's defaults.
#
# TWO SETTINGS HERE ARE LOAD-BEARING, and both fix clipped node labels:
#
#   fontFamily          Must name a font the RENDERING browser actually has, not
#                       the one the app ships. The first attempt asked for Inter
#                       — loaded from Google Fonts in the app, absent in
#                       mermaid-cli's Chromium — so mermaid measured one font and
#                       drew another, and every node label overflowed its box
#                       ("Application Load Balanc"). Segoe UI is present on every
#                       Windows install, so measurement and drawing agree.
#
#   htmlLabels: true    The obvious fix for the clipping above was SVG text
#                       (htmlLabels:false, measured with getBBox). It is a trap:
#                       on this mermaid version any flowchart containing a
#                       SUBGRAPH then renders as a 10x10 empty SVG — and mmdc
#                       still exits 0, so the failure is silent and would have
#                       shipped two blank figures. Keep HTML labels; the font is
#                       what actually had to be fixed.
MERMAID_CFG = """{
  "theme": "base",
  "themeVariables": {
    "primaryColor": "#f3effc",
    "primaryTextColor": "#16131f",
    "primaryBorderColor": "#6d28d9",
    "lineColor": "#6b7280",
    "secondaryColor": "#f4f3f9",
    "tertiaryColor": "#ffffff",
    "clusterBkg": "#fbfaff",
    "clusterBorder": "#d8d2ea",
    "fontFamily": "Segoe UI, Arial, Helvetica, sans-serif",
    "fontSize": "15px"
  },
  "flowchart": { "curve": "basis", "htmlLabels": true, "padding": 14,
                 "nodeSpacing": 42, "rankSpacing": 46, "useMaxWidth": true },
  "er": { "layoutDirection": "TB", "entityPadding": 14, "useMaxWidth": true },
  "sequence": { "actorMargin": 46, "useMaxWidth": true }
}"""

CSS = """
@page { size: A4 portrait; margin: 18mm 16mm 20mm 16mm; }
/* A diagram wider than it is tall cannot be shrunk to the portrait text column
   without its labels falling below readable size. Those get a landscape page of
   their own — the standard treatment for a wide figure in a technical report,
   and Chrome honours the named page. */
@page landscape { size: A4 landscape; margin: 13mm; }
/* No explicit break-after: a change of PAGE NAME already forces a break, and
   adding one produced an extra blank landscape sheet after every figure. */
figure.diagram.wide { page: landscape; break-before: page;
                      border: none; padding: 0; }
/* Landscape A4 leaves ~184mm of printable height; the caption and figure
   margins take the rest. Without an explicit ceiling a tall-ish wide diagram
   overflows onto a SECOND landscape sheet, which reads as a blank page. */
figure.diagram.wide svg { max-width: 100%; max-height: 168mm;
                          width: auto; height: auto; }
figure.diagram.wide figcaption { margin-top: 5px; }

:root {
  --brand: #6d28d9; --brand-strong: #5b21b6; --brand-soft: #f3effc;
  --text: #16131f; --muted: #5c6270; --hint: #8b90a0;
  --border: #e4e1ee; --surface2: #f7f6fb;
  --red: #e11d48; --amber: #b45309; --green: #15803d;
}

* { box-sizing: border-box; }

body {
  font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
  font-size: 10.2pt; line-height: 1.62; color: var(--text);
  margin: 0; -webkit-print-color-adjust: exact; print-color-adjust: exact;
}

/* ── Cover ─────────────────────────────────────────────────────────── */
.cover { height: 247mm; display: flex; flex-direction: column;
         justify-content: center; page-break-after: always; }
.cover .rule { width: 64px; height: 5px; background: var(--brand); border-radius: 3px;
               margin-bottom: 26px; }
.cover .product { font-size: 12pt; letter-spacing: .16em; text-transform: uppercase;
                  color: var(--brand); font-weight: 700; margin-bottom: 10px; }
.cover h1 { font-size: 34pt; line-height: 1.1; margin: 0 0 14px; font-weight: 700;
            letter-spacing: -0.02em; }
.cover .sub { font-size: 12.5pt; color: var(--muted); max-width: 132mm;
              line-height: 1.5; margin-bottom: 40px; }
.cover .meta { border-top: 1px solid var(--border); padding-top: 16px;
               font-size: 9.4pt; color: var(--muted); }
.cover .meta b { color: var(--text); font-weight: 600; }
.cover .meta div { margin-bottom: 4px; }

/* ── Headings ──────────────────────────────────────────────────────── */
h1, h2, h3, h4 { font-weight: 700; letter-spacing: -0.01em; page-break-after: avoid; }
h1 { font-size: 19pt; margin: 0 0 14px; }
h2 { font-size: 15pt; margin: 26px 0 12px; padding-bottom: 7px;
     border-bottom: 2px solid var(--brand-soft); page-break-before: auto; }
h3 { font-size: 12pt; margin: 20px 0 8px; color: var(--brand-strong); }
h4 { font-size: 10.6pt; margin: 15px 0 6px; }

p { margin: 0 0 10px; }
a { color: var(--brand-strong); text-decoration: none; }

/* ── Tables ────────────────────────────────────────────────────────── */
table { width: 100%; border-collapse: collapse; margin: 12px 0 16px;
        font-size: 9.1pt; page-break-inside: avoid; }
th { background: var(--brand-soft); color: var(--brand-strong); font-weight: 700;
     text-align: left; padding: 7px 9px; border: 1px solid var(--border);
     font-size: 8.7pt; letter-spacing: .02em; }
td { padding: 6px 9px; border: 1px solid var(--border); vertical-align: top; }
tr:nth-child(even) td { background: var(--surface2); }

/* ── Code ──────────────────────────────────────────────────────────── */
code { font-family: 'JetBrains Mono', 'Cascadia Mono', Consolas, monospace;
       font-size: 8.6pt; background: var(--surface2); padding: 1.5px 4px;
       border-radius: 3px; color: var(--brand-strong); }
pre { background: #faf9fd; border: 1px solid var(--border); border-left: 3px solid var(--brand);
      border-radius: 6px; padding: 11px 13px; overflow-x: auto; margin: 12px 0 16px;
      page-break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 8.3pt; color: var(--text);
           line-height: 1.5; }

/* ── Blockquote: used for the "known gap" callouts ─────────────────── */
blockquote { margin: 14px 0; padding: 11px 15px; background: #fdfaf3;
             border-left: 3px solid var(--amber); border-radius: 0 6px 6px 0;
             page-break-inside: avoid; }
blockquote p { margin: 0 0 7px; }
blockquote p:last-child { margin-bottom: 0; }
blockquote h3 { margin-top: 0; color: var(--amber); font-size: 11pt; }

/* ── Diagrams ──────────────────────────────────────────────────────── */
figure.diagram { margin: 16px 0 20px; padding: 14px; background: #fff;
                 border: 1px solid var(--border); border-radius: 8px;
                 text-align: center; page-break-inside: avoid; }
figure.diagram svg { max-width: 100%; height: auto; }
figure.diagram figcaption { margin-top: 9px; font-size: 8.2pt; color: var(--hint);
                            letter-spacing: .04em; text-transform: uppercase; }

ul, ol { margin: 0 0 12px; padding-left: 20px; }
li { margin-bottom: 5px; }
hr { border: none; border-top: 1px solid var(--border); margin: 22px 0; }
strong { font-weight: 700; }
"""


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def build(md_name: str) -> Path:
    src = DOCS / md_name
    stem = src.stem
    text = src.read_text(encoding="utf-8")
    stage = WORK / stem
    stage.mkdir(parents=True, exist_ok=True)

    cfg = WORK / "mermaid.json"
    cfg.write_text(MERMAID_CFG, encoding="utf-8")

    # 1. Pull the mermaid blocks out and render each one to SVG.
    blocks = re.findall(r"```mermaid\n(.*?)```", text, re.S)
    svgs: list[str] = []
    for i, block in enumerate(blocks, 1):
        mmd = stage / f"d{i}.mmd"
        svg = stage / f"d{i}.svg"
        mmd.write_text(block, encoding="utf-8")
        print(f"    diagram {i}/{len(blocks)} …", end="", flush=True)
        run(["npx", "-y", "@mermaid-js/mermaid-cli@11", "-i", str(mmd), "-o", str(svg),
             "-c", str(cfg), "-b", "transparent", "-s", "2"], shell=True)
        body = svg.read_text(encoding="utf-8")
        # Chrome honours the intrinsic size unless it is stripped; drop the fixed
        # width/height so the CSS max-width can scale a wide diagram to the page.
        body = re.sub(r'<svg([^>]*?)\swidth="[^"]*"', r"<svg\1", body, count=1)
        body = re.sub(r'<svg([^>]*?)\sheight="[^"]*"', r"<svg\1", body, count=1)
        svgs.append(body)
        print(" ok")

    # 2. Swap each block for a marker marked() will pass through untouched.
    idx = {"n": 0}

    def placeholder(_m):
        idx["n"] += 1
        return f"\n\nDIAGRAMPLACEHOLDER{idx['n']}\n\n"

    stripped = re.sub(r"```mermaid\n.*?```", placeholder, text, flags=re.S)
    md_tmp = stage / "body.md"
    md_tmp.write_text(stripped, encoding="utf-8")

    # 3. Markdown -> HTML with marked (GFM tables, fences, autolinks).
    html_tmp = stage / "body.html"
    run(["npx", "-y", "marked@15", "--gfm", "-i", str(md_tmp), "-o", str(html_tmp)],
        shell=True)
    body = html_tmp.read_text(encoding="utf-8")

    # 4. Put the rendered diagrams back.
    for i, svg in enumerate(svgs, 1):
        # The viewBox origin is NOT always "0 0" — mermaid emits a negative
        # origin for sequence diagrams ("-50 -10 1398 771"). An origin-anchored
        # pattern silently failed to match those, so every sequence diagram fell
        # back to ratio 1.0 and was laid out portrait however wide it really was.
        m = re.search(r'viewBox="\s*[-\d.]+\s+[-\d.]+\s+([\d.]+)\s+([\d.]+)"', svg)
        ratio = (float(m.group(1)) / float(m.group(2))) if m else 1.0
        cls = "diagram wide" if ratio > 1.45 else "diagram"
        note = " · shown landscape" if ratio > 1.45 else ""
        fig = (f'<figure class="{cls}">{svg}'
               f'<figcaption>Figure {i}{note}</figcaption></figure>')
        body = re.sub(rf"<p>\s*DIAGRAMPLACEHOLDER{i}\s*</p>", fig, body)
        body = body.replace(f"DIAGRAMPLACEHOLDER{i}", fig)

    # The document's own H1 duplicates the cover; drop the first one.
    body = re.sub(r"<h1[^>]*>.*?</h1>", "", body, count=1, flags=re.S)

    title, subtitle = TITLES[md_name]
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>NextXR — {html.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>
<section class="cover">
  <div class="rule"></div>
  <div class="product">NextXR Digital Twin</div>
  <h1>{html.escape(title)}</h1>
  <div class="sub">{html.escape(subtitle)}</div>
  <div class="meta">
    <div><b>Generated</b> 7 September 2026, from the running system</div>
    <div><b>Repository</b> Tejesh3305/Goalcert_Digital-Twin</div>
    <div><b>Source</b> docs/{html.escape(md_name)}</div>
    <div><b>Status</b> Reflects the deployed implementation, not a design proposal</div>
  </div>
</section>
{body}
</body></html>"""

    final_html = stage / f"{stem}.html"
    final_html.write_text(page, encoding="utf-8")

    out_pdf = DOCS / "pdf" / f"NextXR_{stem.replace('-', '_')}.pdf"
    out_pdf.parent.mkdir(exist_ok=True)
    print("    printing PDF …", end="", flush=True)
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                    "--no-pdf-header-footer", "--virtual-time-budget=20000",
                    f"--print-to-pdf={out_pdf}", final_html.as_uri()],
                   check=True, capture_output=True, timeout=240)
    print(f" {out_pdf.stat().st_size // 1024} KB")
    return out_pdf


if __name__ == "__main__":
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    for name in sys.argv[1:] or list(TITLES):
        print(f"\n{name}")
        build(name)
    print("\ndone")
