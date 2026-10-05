"""Download the official Dominick's files for one category from the Kilts Center.

    python scripts/download_dominicks.py                      # cereals (the default)
    python scripts/download_dominicks.py --category crackers  # a second category

Each category is a pair of official files named after a three-letter code:
``upc<code>.csv`` (products) and ``w<code>.zip`` (weekly movement). They are
saved under ``data/raw/dominicks/<category>/``, which is git-ignored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import time
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

BASE_URL = (
    "https://www.chicagobooth.edu/research/kilts/research-data/-/media/"
    "enterprise/centers/kilts/datasets/dominicks-dataset"
)
MANUAL_URL = f"{BASE_URL}/dominicks-manual-and-codebook_kiltscenter.pdf"

#: Category name -> the Kilts Center's three-letter file code.
CATEGORIES = {
    "cereals": "cer",
    "crackers": "cra",
    "canned_soup": "cso",
    "cookies": "coo",
    "soft_drinks": "sdr",
}


def upc_url(code: str) -> str:
    return f"{BASE_URL}/upc_csv-files/upc{code}.csv"


def movement_url(code: str) -> str:
    return f"{BASE_URL}/movement_csv-files/w{code}.zip"


UPC_URL = upc_url("cer")
MOVEMENT_URL = movement_url("cer")

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = REPO_ROOT / "data" / "raw" / "dominicks"
RAW_DIR = RAW_ROOT / "cereals"
CHUNK_SIZE = 1024 * 1024
TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 5


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path, force: bool) -> bool:
    if destination.exists() and destination.stat().st_size > 0 and not force:
        print(f"Using existing {destination}")
        return False

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.part")
    if force and partial.exists():
        partial.unlink()

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "dominicks-data-downloader/1.0"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                if response.status not in (200, 206):
                    raise RuntimeError(f"Download failed with HTTP {response.status}: {url}")
                mode = "ab" if response.status == 206 and offset else "wb"
                with partial.open(mode) as temp_file:
                    shutil.copyfileobj(response, temp_file, length=CHUNK_SIZE)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
            if partial.stat().st_size == 0:
                raise RuntimeError(f"Downloaded an empty file: {url}")
            partial.replace(destination)
            print(f"Downloaded {destination} ({destination.stat().st_size:,} bytes)")
            return True
        except Exception as exc:
            last_error = exc
            if attempt == MAX_ATTEMPTS:
                break
            print(f"Attempt {attempt}/{MAX_ATTEMPTS} failed for {destination.name}; retrying...")
            time.sleep(min(2**attempt, 10))

    raise RuntimeError(f"Download failed after {MAX_ATTEMPTS} attempts: {last_error}")


def movement_member(archive: zipfile.ZipFile, expected: str = "wcer.csv") -> zipfile.ZipInfo:
    csv_members: list[zipfile.ZipInfo] = []
    for member in archive.infolist():
        path = PurePosixPath(member.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"Unsafe ZIP member path: {member.filename}")
        if not member.is_dir() and path.suffix.lower() == ".csv":
            csv_members.append(member)

    exact = [m for m in csv_members if PurePosixPath(m.filename).name.lower() == expected]
    candidates = exact or csv_members
    if len(candidates) != 1:
        names = [member.filename for member in csv_members]
        raise RuntimeError(f"Expected one movement CSV in archive; found {names}")
    return candidates[0]


def extract_movement(zip_path: Path, destination: Path, force: bool) -> str:
    expected = destination.name.lower()
    if destination.exists() and destination.stat().st_size > 0 and not force:
        print(f"Using existing {destination}")
        with zipfile.ZipFile(zip_path) as archive:
            return movement_member(archive, expected).filename

    temp_path: Path | None = None
    try:
        with zipfile.ZipFile(zip_path) as archive:
            member = movement_member(archive, expected)
            with archive.open(member) as source, tempfile.NamedTemporaryFile(
                mode="wb", dir=destination.parent, prefix=f".{destination.name}.", delete=False
            ) as temp_file:
                temp_path = Path(temp_file.name)
                shutil.copyfileobj(source, temp_file, length=CHUNK_SIZE)
                temp_file.flush()
                os.fsync(temp_file.fileno())
        if temp_path.stat().st_size == 0:
            raise RuntimeError("Extracted movement CSV is empty")
        temp_path.replace(destination)
        print(f"Extracted {destination} ({destination.stat().st_size:,} bytes)")
        return member.filename
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="replace existing downloads")
    parser.add_argument("--category", default="cereals", choices=sorted(CATEGORIES),
                        help="which Dominick's category to download (default: cereals)")
    args = parser.parse_args()

    code = CATEGORIES[args.category]
    raw_dir = RAW_ROOT / args.category
    upc_name, zip_name, csv_name = f"upc{code}.csv", f"w{code}.zip", f"w{code}.csv"
    raw_dir.mkdir(parents=True, exist_ok=True)
    upc_path = raw_dir / upc_name
    zip_path = raw_dir / zip_name
    movement_path = raw_dir / csv_name
    manual_path = raw_dir / "dominicks-manual-and-codebook.pdf"

    try:
        download(upc_url(code), upc_path, args.force)
        download(movement_url(code), zip_path, args.force)
        download(MANUAL_URL, manual_path, args.force)
        original_member = extract_movement(zip_path, movement_path, args.force)
    except Exception as exc:
        raise SystemExit(
            f"Official data acquisition blocked: {exc}\n"
            f"Download {upc_name} from {upc_url(code)}\n"
            f"Download {zip_name} from {movement_url(code)}, extract its CSV as {csv_name}, "
            f"and place both in {raw_dir}"
        ) from exc

    source = {
        "provider": "Kilts Center for Marketing, University of Chicago Booth School of Business",
        "dataset": "Dominick's Finer Foods Store-Level Scanner Dataset - "
                   + args.category.replace("_", " ").title(),
        "category": args.category,
        "downloaded_at_utc": datetime.now(UTC).isoformat(),
        "usage": "Academic research use; acknowledge the Kilts Center in working papers/publications.",
        "redistribution": "Do not commit or redistribute raw data.",
        "files": {
            upc_name: {"url": upc_url(code), "bytes": upc_path.stat().st_size, "sha256": sha256(upc_path)},
            zip_name: {"url": movement_url(code), "bytes": zip_path.stat().st_size, "sha256": sha256(zip_path)},
            csv_name: {
                "archive_member": original_member,
                "bytes": movement_path.stat().st_size,
                "sha256": sha256(movement_path),
            },
            "dominicks-manual-and-codebook.pdf": {
                "url": MANUAL_URL,
                "bytes": manual_path.stat().st_size,
                "sha256": sha256(manual_path),
            },
        },
    }
    source_path = raw_dir / "SOURCE.json"
    source_path.write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote provenance metadata to {source_path}")


if __name__ == "__main__":
    main()
