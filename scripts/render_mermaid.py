"""Render the Mermaid diagrams in the full report to PNG.

Word cannot render Mermaid, so the DOCX edition of
``reports/AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md`` embeds pre-rendered
images instead. This script extracts every ```mermaid fence from the report,
renders it with a locally cached mermaid.js in headless Chrome, and writes one
PNG per diagram.

    python scripts/render_mermaid.py

Requires: selenium + a Chrome/Chromium binary on PATH, and
``artifacts/report_figures/mermaid/mermaid.min.js`` (downloaded once).
"""

from __future__ import annotations

import base64
import re
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "AI_PRICING_REVENUE_OPTIMIZATION_FULL_REPORT.md"
OUT = ROOT / "artifacts" / "report_figures" / "mermaid"
LIB = OUT / "mermaid.min.js"

HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html,body {{ margin:0; padding:24px; background:#ffffff;
               font-family:'Segoe UI',Arial,sans-serif; }}
  #d {{ display:inline-block; }}
</style>
<script>{lib}</script>
</head><body>
<div id="d" class="mermaid">{code}</div>
<script>
  mermaid.initialize({{
    startOnLoad: true,
    theme: 'base',
    themeVariables: {{
      background: '#ffffff',
      primaryColor: '#eef4fb',
      primaryTextColor: '#1a202c',
      primaryBorderColor: '#2b6cb0',
      lineColor: '#4a5568',
      secondaryColor: '#f7fafc',
      tertiaryColor: '#ffffff',
      fontSize: '15px',
      fontFamily: "'Segoe UI',Arial,sans-serif"
    }},
    flowchart: {{ htmlLabels: true, curve: 'basis', nodeSpacing: 45, rankSpacing: 55 }}
  }});
</script>
</body></html>
"""


def main() -> int:
    if not LIB.exists():
        print(f"missing {LIB} - download mermaid.min.js first", file=sys.stderr)
        return 1

    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By

    text = REPORT.read_text(encoding="utf-8")
    blocks = re.findall(r"```mermaid\n(.*?)```", text, re.S)
    print(f"found {len(blocks)} mermaid blocks")
    OUT.mkdir(parents=True, exist_ok=True)
    lib = LIB.read_text(encoding="utf-8")

    opts = Options()
    for a in ("--headless=new", "--hide-scrollbars", "--force-device-scale-factor=2",
              "--window-size=2200,2400", "--allow-file-access-from-files"):
        opts.add_argument(a)
    driver = webdriver.Chrome(options=opts)

    written = []
    for i, code in enumerate(blocks, start=1):
        page = OUT / f"_render_{i:02d}.html"
        page.write_text(HTML.format(lib=lib, code=code.strip()), encoding="utf-8")
        driver.set_window_size(2200, 2400)
        driver.get(page.as_uri())
        time.sleep(3.0)
        try:
            driver.find_element(By.CSS_SELECTOR, "#d svg")
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"  diagram {i}: RENDER FAILED ({type(exc).__name__})")
            page.unlink(missing_ok=True)
            continue

        # Mermaid caps the SVG at the container width, so a wide LR flowchart is
        # scaled down and would be screenshotted at that reduced size. Release the
        # cap and pin the SVG to its intrinsic viewBox dimensions first.
        nat = driver.execute_script(
            """
            const s = document.querySelector('#d svg');
            const vb = (s.getAttribute('viewBox') || '').split(/[\\s,]+/).map(Number);
            const w = vb.length === 4 ? vb[2] : s.getBBox().width;
            const h = vb.length === 4 ? vb[3] : s.getBBox().height;
            s.style.maxWidth = 'none';
            s.style.width  = w + 'px';
            s.style.height = h + 'px';
            return [Math.ceil(w), Math.ceil(h)];
            """
        )
        w, h = int(nat[0]), int(nat[1])
        driver.set_window_size(min(max(w + 90, 520), 4000), min(max(h + 90, 320), 2400))
        time.sleep(1.2)

        # element.screenshot() is clipped to the viewport for tall diagrams, so use
        # CDP with captureBeyondViewport and an explicit clip around the SVG.
        rect = driver.execute_script(
            "const r = document.querySelector('#d svg').getBoundingClientRect();"
            "return [r.left + window.scrollX, r.top + window.scrollY, r.width, r.height];"
        )
        shot = driver.execute_cdp_cmd(
            "Page.captureScreenshot",
            {
                "format": "png",
                "captureBeyondViewport": True,
                "clip": {
                    "x": max(rect[0] - 14, 0),
                    "y": max(rect[1] - 14, 0),
                    "width": rect[2] + 28,
                    "height": rect[3] + 28,
                    "scale": 2,
                },
            },
        )
        dest = OUT / f"mermaid_{i:02d}.png"
        dest.write_bytes(base64.b64decode(shot["data"]))
        px = Image.open(dest).size
        ok = "" if px[1] >= int(rect[3] * 2 * 0.97) else "   <-- TRUNCATED"
        written.append(dest)
        print(f"  diagram {i}: {dest.name}  {w}x{h} css px -> {px[0]}x{px[1]} px"
              f"  {dest.stat().st_size:,} bytes{ok}")
        page.unlink(missing_ok=True)

    driver.quit()
    print(f"wrote {len(written)} of {len(blocks)} diagrams to {OUT}")
    return 0 if len(written) == len(blocks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
