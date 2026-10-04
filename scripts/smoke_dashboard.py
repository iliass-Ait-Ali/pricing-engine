"""Smoke-test the Streamlit dashboard without a browser.

    python scripts/smoke_dashboard.py

Uses Streamlit's AppTest harness to render every page headlessly and fail on
any exception. This is what backs the "dashboard smoke test" line in
reports/VALIDATION_SUMMARY.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

APP = REPO_ROOT / "dashboard" / "app.py"
TIMEOUT = 300


def main() -> int:
    import ast

    from streamlit.testing.v1 import AppTest

    # Read the page list from the app itself, so a new page is always covered.
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    pages = next(
        [k.value for k in node.value.keys]
        for node in tree.body
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "PAGES"
    )

    failures = 0
    for i, page in enumerate(pages):
        at = AppTest.from_file(str(APP), default_timeout=TIMEOUT)
        at.run()
        if at.exception:
            print(f"[FAIL] initial render: {at.exception}")
            return 1
        if i > 0:
            at.sidebar.radio[0].set_value(page).run()
        if at.exception:
            print(f"[FAIL] {page}: {at.exception}")
            failures += 1
        else:
            n_widgets = len(at.markdown) + len(at.metric) + len(at.dataframe)
            print(f"[ok  ] {page:34s} rendered ({n_widgets} elements)")

    print()
    if failures:
        print(f"{failures} page(s) failed to render.")
        return 1
    print(f"All {len(pages)} dashboard pages rendered without exceptions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
