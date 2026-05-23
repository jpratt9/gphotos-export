"""Shared utilities for Google Photos scraper."""

import json
import logging
import os
import random
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path


SESSION_DIR = Path("./session")
SESSION_DIR_BACKWARD = Path("./session-backward")
DOWNLOADS_DIR = Path("./downloads-2")
STAGING_DIR = Path("./downloads-2/.staging")
STAGING_DIR_BACKWARD = Path("./downloads-2/.staging-backward")
LASTDONE_FILE = Path(".lastdone")
LASTDONE_FILE_BACKWARD = Path("backward.lastdone")
SKIPLIST_FILE = Path("skiplist.txt")
GOOGLE_PHOTOS_URL = "https://photos.google.com"
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2
DOWNLOAD_TIMEOUT = 60
HUMAN_DELAY_RANGE = (1.0, 3.0)


def setup_logging():
    log_dir = Path("./logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"download-{time.strftime('%Y%m%d-%H%M%S')}.log"

    fmt = logging.Formatter(
        "[%(asctime)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    stdout_handler = logging.StreamHandler()
    stdout_handler.setFormatter(fmt)
    root.addHandler(stdout_handler)

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    for name in ("seleniumbase", "selenium", "urllib3", "uc", "nodriver"):
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger("setup").info("Logging to %s", log_file)


def create_driver(headed, user_data_dir, download_dir):
    from seleniumbase import Driver

    driver = Driver(
        uc=True,
        headed=headed,
        user_data_dir=str(user_data_dir),
        chromium_arg="--window-size=1280,720",
    )
    driver.execute_cdp_cmd("Page.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": str(download_dir.resolve()),
    })
    return driver


def read_progress(lastdone_path):
    if not lastdone_path.exists():
        raise FileNotFoundError(
            f"No {lastdone_path} file found. Create it with the URL of your oldest photo."
        )
    url = lastdone_path.read_text().strip()
    if not url:
        raise ValueError(f"{lastdone_path} is empty. Add the URL of your oldest photo.")
    return url


def save_progress(lastdone_path, url):
    if not url.startswith("https://photos.google.com"):
        return
    tmp = lastdone_path.with_suffix(".tmp")
    tmp.write_text(url)
    os.replace(tmp, lastdone_path)


def clean_url(url):
    return re.sub(r"/u/\d+/", "/", url)


def get_media_date(file_path):
    try:
        result = subprocess.run(
            ["exiftool", "-json", "-DateTimeOriginal", "-CreateDate", str(file_path)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)[0]
            for field in ("DateTimeOriginal", "CreateDate"):
                val = data.get(field)
                if val and "0000" not in val:
                    parts = val.split(":")
                    return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return 1970, 1


def get_date_from_html(driver):
    try:
        html = driver.page_source
        pattern = r'aria-label="(?:Photo|Video) - (?:Landscape|Portrait|Square) - ([A-Za-z]{3} \d{1,2}, \d{4}, \d{1,2}:\d{2}:\d{2}\s*[APM]{2})"'
        match = re.search(pattern, html)
        if match:
            date_str = match.group(1).replace("\u202f", " ")
            dt = datetime.strptime(date_str, "%b %d, %Y, %I:%M:%S %p")
            return dt.year, dt.month
    except Exception:
        pass
    return 1970, 1


def load_skiplist(skiplist_path):
    """Load URLs to skip from a text file (one per line)."""
    if not skiplist_path.exists():
        return set()
    urls = set()
    for line in skiplist_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.add(clean_url(line))
    return urls


def file_already_downloaded(filename, downloads_dir):
    """Check if a file with this name already exists anywhere in downloads."""
    for dirpath, _, filenames in os.walk(downloads_dir):
        if os.path.basename(dirpath).startswith(".staging"):
            continue
        if filename in filenames:
            return True
    return False


def _place_file(src_path, dest_dir, overwrite=False):
    """Move a single file into dest_dir, handling name truncation and duplicates."""
    filename = src_path.name
    if len(filename) > 200:
        ext = src_path.suffix
        filename = filename[:200 - len(ext)] + ext

    dest = dest_dir / filename

    if not overwrite:
        counter = 2
        while dest.exists():
            stem = src_path.stem[:200]
            dest = dest_dir / f"{stem} {counter}{src_path.suffix}"
            counter += 1

    src_path.rename(dest)
    return dest


def organize_file(src_path, downloads_dir, driver, overwrite=False):
    log = logging.getLogger("organize")

    # If it's a zip, extract files into the same destination folder.
    if src_path.suffix.lower() == ".zip":
        import zipfile
        if zipfile.is_zipfile(src_path):
            log.info("Got zip file: %s — extracting", src_path.name)
            year, month = get_media_date(src_path)
            if year == 1970:
                year, month = get_date_from_html(driver)
            dest_dir = downloads_dir / str(year) / str(month)
            dest_dir.mkdir(parents=True, exist_ok=True)
            extracted = []
            with zipfile.ZipFile(src_path, "r") as zf:
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    extracted_path = Path(zf.extract(member, src_path.parent))
                    final = _place_file(extracted_path, dest_dir, overwrite=overwrite)
                    log.info("  Extracted: %s -> %s", member.filename, final.name)
                    extracted.append(final)
            src_path.unlink()
            return extracted[0] if extracted else src_path

    year, month = get_media_date(src_path)
    if year == 1970:
        year, month = get_date_from_html(driver)
        if year != 1970:
            log.info("Date from HTML: %d/%d", year, month)

    dest_dir = downloads_dir / str(year) / str(month)
    dest_dir.mkdir(parents=True, exist_ok=True)
    return _place_file(src_path, dest_dir, overwrite=overwrite)


def wait_for_download(staging_dir, timeout, known_files):
    log = logging.getLogger("download")
    deadline = time.time() + timeout

    while time.time() < deadline:
        current = set(staging_dir.iterdir())
        new_files = current - known_files

        # Filter out in-progress downloads
        completed = [f for f in new_files if not f.name.endswith(".crdownload")]
        in_progress = [f for f in new_files if f.name.endswith(".crdownload")]

        if completed:
            return completed[0]

        if in_progress:
            # Reset deadline while download is actively happening
            deadline = time.time() + timeout

        time.sleep(0.5)

    raise TimeoutError("Download timed out")


def human_delay(min_s=None, max_s=None):
    if min_s is None:
        min_s = HUMAN_DELAY_RANGE[0]
    if max_s is None:
        max_s = HUMAN_DELAY_RANGE[1]
    time.sleep(random.uniform(min_s, max_s))
