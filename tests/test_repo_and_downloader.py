"""Repository-hygiene and downloader-safety tests.

These guard two promises made in the README: the licensed raw data never enters
Git, and the archive extraction cannot be used to write outside the data
directory (zip-slip).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


def _load_downloader(repo_root: Path):
    spec = importlib.util.spec_from_file_location(
        "download_dominicks", repo_root / "scripts" / "download_dominicks.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["download_dominicks"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# git hygiene
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    [
        "data/raw/dominicks/cereals/wcer.csv",
        "data/raw/dominicks/cereals/upccer.csv",
        "data/raw/dominicks/cereals/wcer.zip",
        "data/processed/dominicks_cereals.parquet",
    ],
)
def test_raw_and_processed_data_are_git_ignored(repo_root: Path, path: str):
    result = subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 128:
        pytest.skip("not a git repository")
    assert result.returncode == 0, f"{path} is NOT ignored by .gitignore"


def test_gitignore_documents_raw_data(repo_root: Path):
    text = (repo_root / ".gitignore").read_text(encoding="utf-8")
    assert "data/raw/" in text


# ---------------------------------------------------------------------------
# downloader safety
# ---------------------------------------------------------------------------
def test_downloader_uses_only_official_kilts_urls(repo_root: Path):
    module = _load_downloader(repo_root)
    for url in (module.UPC_URL, module.MOVEMENT_URL, module.MANUAL_URL):
        assert url.startswith("https://www.chicagobooth.edu/research/kilts/"), url


def test_downloader_rejects_zip_slip_member(repo_root: Path, tmp_path: Path):
    module = _load_downloader(repo_root)
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../escaped.csv", "store,upc\n1,2\n")
    with zipfile.ZipFile(evil) as zf, pytest.raises(RuntimeError):
        module.movement_member(zf)


def test_downloader_rejects_absolute_member(repo_root: Path, tmp_path: Path):
    module = _load_downloader(repo_root)
    evil = tmp_path / "abs.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("/etc/passwd.csv", "x\n")
    with zipfile.ZipFile(evil) as zf, pytest.raises(RuntimeError):
        module.movement_member(zf)


def test_downloader_picks_the_movement_csv(repo_root: Path, tmp_path: Path):
    module = _load_downloader(repo_root)
    archive = tmp_path / "wcer.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", "not data")
        zf.writestr("WCER.CSV", "STORE,UPC\n1,2\n")
    with zipfile.ZipFile(archive) as zf:
        member = module.movement_member(zf)
    assert member.filename.lower().endswith("wcer.csv")


def test_downloader_extract_writes_normalised_name(repo_root: Path, tmp_path: Path):
    module = _load_downloader(repo_root)
    archive = tmp_path / "wcer.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("WCER.CSV", "STORE,UPC\n1,2\n")
    dest = tmp_path / "wcer.csv"
    original = module.extract_movement(archive, dest, force=True)
    assert dest.exists() and dest.stat().st_size > 0
    assert original == "WCER.CSV"  # original archive member name is reported
