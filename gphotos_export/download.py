#!/usr/bin/env python3
"""Download all Google Photos in original quality via browser automation."""

import argparse
import logging
import sys
import time

from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

from .utils import (
    create_driver, setup_logging, read_progress, save_progress, clean_url,
    organize_file, wait_for_download, human_delay, load_skiplist,
    SESSION_DIR, SESSION_DIR_BACKWARD, DOWNLOADS_DIR,
    STAGING_DIR, STAGING_DIR_BACKWARD,
    LASTDONE_FILE, LASTDONE_FILE_BACKWARD, SKIPLIST_FILE,
    GOOGLE_PHOTOS_URL, MAX_RETRIES, RETRY_BACKOFF_BASE,
    DOWNLOAD_TIMEOUT,
)

log = logging.getLogger("download")


def get_latest_photo(driver):
    """Navigate to Google Photos and find the latest (most recent) photo URL."""
    driver.get(GOOGLE_PHOTOS_URL)
    time.sleep(3)
    ActionChains(driver).send_keys(Keys.ARROW_RIGHT).perform()
    time.sleep(1)
    url = driver.execute_script(
        "return document.activeElement.href || document.activeElement.toString()"
    )
    return url


def trigger_download(driver):
    """Press Shift+D to trigger Google Photos' native download."""
    ActionChains(driver).key_down(Keys.SHIFT).send_keys("d").key_up(Keys.SHIFT).perform()


def download_single(driver, staging_dir, downloads_dir, overwrite=False):
    """Download the currently viewed photo/video with retry logic."""
    for attempt in range(MAX_RETRIES):
        try:
            known = set(staging_dir.iterdir())
            trigger_download(driver)
            time.sleep(0.2)
            try:
                driver.find_element("xpath", "//div[contains(text(),'Video is still processing')]")
                log.warning("Video still processing, skipping: %s", driver.current_url)
                return "failed", None
            except Exception:
                pass
            downloaded = wait_for_download(staging_dir, DOWNLOAD_TIMEOUT, known)
            final_path = organize_file(downloaded, downloads_dir, driver, overwrite=overwrite)
            return "ok", final_path
        except TimeoutError:
            if attempt < MAX_RETRIES - 1:
                backoff = RETRY_BACKOFF_BASE ** attempt
                log.warning(
                    "Download timeout (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1, MAX_RETRIES, backoff, driver.current_url,
                )
                time.sleep(backoff)
            else:
                log.error(
                    "Download failed after %d attempts: %s",
                    MAX_RETRIES, driver.current_url,
                )
                return "failed", None
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                backoff = RETRY_BACKOFF_BASE ** attempt
                log.warning(
                    "Download error (attempt %d/%d): %s — retrying in %ds",
                    attempt + 1, MAX_RETRIES, e, backoff,
                )
                time.sleep(backoff)
            else:
                log.error(
                    "Download failed after %d attempts: %s — %s",
                    MAX_RETRIES, driver.current_url, e,
                )
                return "failed", None
    return "failed", None


def _navigate(driver, current_url, direction, timeout=30):
    """Navigate to the next or previous photo.

    direction: "forward" (older->newer, left arrow) or "backward" (newer->older, right arrow)
    """
    if direction == "forward":
        label_keyword = "previous"
        arrow_key = Keys.ARROW_LEFT
        js_key = "ArrowLeft"
    else:
        label_keyword = "next"
        arrow_key = Keys.ARROW_RIGHT
        js_key = "ArrowRight"

    strategies = [
        # Strategy 1: Click the nav div via JS
        lambda: driver.execute_script(f"""
            var divs = document.querySelectorAll('div[aria-label]');
            for (var div of divs) {{
                var label = div.getAttribute('aria-label').toLowerCase();
                if (label.includes('{label_keyword}') &&
                    (label.includes('photo') || label.includes('video') || label.includes('view'))) {{
                    div.click(); return true;
                }}
            }}
            return false;
        """),
        # Strategy 2: Keyboard arrow on body
        lambda: (driver.execute_script("document.body.focus()"),
                 ActionChains(driver).send_keys(arrow_key).perform()),
        # Strategy 3: Direct keyboard press via JS
        lambda: driver.execute_script(f"""
            document.dispatchEvent(new KeyboardEvent('keydown', {{key: '{js_key}', bubbles: true}}));
        """),
    ]

    for i, strategy in enumerate(strategies):
        try:
            strategy()
        except Exception:
            continue

        # Wait for URL change
        for _ in range(timeout * 2):
            new_url = driver.current_url
            if clean_url(new_url) != clean_url(current_url):
                return True
            time.sleep(0.5)

        log.debug("Strategy %d didn't navigate, trying next", i + 1)

    return False


def navigate_next(driver, current_url, timeout=30):
    """Navigate forward in time (older -> newer)."""
    return _navigate(driver, current_url, "forward", timeout)


def navigate_backward(driver, current_url, timeout=30):
    """Navigate backward in time (newer -> older)."""
    return _navigate(driver, current_url, "backward", timeout)


def main():
    parser = argparse.ArgumentParser(description="Download Google Photos")
    parser.add_argument("--headed", action="store_true", help="Run with visible browser")
    parser.add_argument("--dry-run", action="store_true", help="Navigate but don't download")
    parser.add_argument("--backward", action="store_true",
                        help="Run as backward worker (newest to oldest, separate browser)")
    args = parser.parse_args()

    setup_logging()

    # Pick session/staging/progress files based on direction.
    # Each worker needs its own Chrome profile dir (Chrome locks it).
    if args.backward:
        session_dir = SESSION_DIR_BACKWARD
        staging_dir = STAGING_DIR_BACKWARD
        lastdone_file = LASTDONE_FILE_BACKWARD
        nav_fn = navigate_backward
        mode_label = "BACKWARD"
    else:
        session_dir = SESSION_DIR
        staging_dir = STAGING_DIR
        lastdone_file = LASTDONE_FILE
        nav_fn = navigate_next
        mode_label = "FORWARD"

    if not session_dir.exists():
        flag = " --backward" if args.backward else ""
        log.error("No session found. Run: gphotos-export-login%s", flag)
        sys.exit(1)

    # For backward mode, if no .lastdone-backward exists, start from latest photo.
    if args.backward and not lastdone_file.exists():
        log.info("No %s found — will start from latest photo", lastdone_file)

    start_url = None
    if lastdone_file.exists():
        text = lastdone_file.read_text().strip()
        if text:
            start_url = text
            log.info("[%s] Resuming from: %s", mode_label, start_url)
        else:
            log.info("[%s] %s is empty — will auto-detect start", mode_label, lastdone_file)

    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    driver = create_driver(
        headed=args.headed,
        user_data_dir=session_dir,
        download_dir=staging_dir,
    )

    stats = {"downloaded": 0, "skipped": 0, "failed": 0}
    skiplist = load_skiplist(SKIPLIST_FILE)
    if skiplist:
        log.info("[%s] Loaded %d URLs to skip", mode_label, len(skiplist))

    try:
        if args.backward:
            if start_url is None:
                start_url = get_latest_photo(driver)
                log.info("[%s] Starting from latest photo: %s", mode_label, start_url)
            stop_url = None
            if LASTDONE_FILE.exists():
                text = LASTDONE_FILE.read_text().strip()
                if text:
                    stop_url = text
                    log.info("[%s] Will stop at forward worker position: %s", mode_label, stop_url)
        else:
            stop_url = get_latest_photo(driver)
            log.info("[%s] Latest photo: %s", mode_label, stop_url)

        log.info("---")

        driver.get(start_url)
        time.sleep(3)

        # Download first photo (overwrite OK — may be resume point).
        if not args.dry_run:
            status, path = download_single(driver, staging_dir, DOWNLOADS_DIR, overwrite=True)
            if status == "ok":
                stats["downloaded"] += 1
                log.info("[%s] [%d] %s", mode_label, stats["downloaded"], path.name)
            elif status == "skipped":
                stats["skipped"] += 1
            elif status == "failed":
                stats["failed"] += 1

        consecutive_errors = 0
        while True:
            try:
                current_url = driver.current_url
            except Exception as e:
                consecutive_errors += 1
                log.warning("[%s] Failed to get current URL (%d/3): %s", mode_label, consecutive_errors, e)
                if consecutive_errors >= 3:
                    log.error("[%s] 3 consecutive errors, waiting 30s before retry...", mode_label)
                    time.sleep(30)
                    consecutive_errors = 0
                continue

            if stop_url and clean_url(current_url) == clean_url(stop_url):
                log.info("[%s] Reached stop point, done!", mode_label)
                break

            if "photos.google.com" not in current_url:
                log.error("[%s] Navigated away from Google Photos: %s", mode_label, current_url)
                log.error("Session may have expired. Re-run gphotos-export-login to log in again.")
                break

            try:
                nav_ok = nav_fn(driver, current_url)
            except Exception as e:
                log.warning("[%s] Navigation error: %s — retrying...", mode_label, e)
                time.sleep(5)
                continue

            if not nav_ok:
                log.error("[%s] Navigation stuck at %s", mode_label, current_url)
                break

            # Check skiplist.
            try:
                if clean_url(driver.current_url) in skiplist:
                    log.info("[%s] Skipping (in skiplist): %s", mode_label, driver.current_url)
                    stats["skipped"] += 1
                    continue
            except Exception:
                pass

            if args.dry_run:
                total = stats["downloaded"] + stats["skipped"] + stats["failed"] + 1
                log.info("[%s] [dry-run] [%d] %s", mode_label, total, driver.current_url)
                human_delay()
                continue

            status, path = download_single(driver, staging_dir, DOWNLOADS_DIR, overwrite=False)
            if status == "ok":
                stats["downloaded"] += 1
                consecutive_errors = 0
                log.info("[%s] [%d] %s", mode_label, stats["downloaded"], path.name)
            elif status == "skipped":
                stats["skipped"] += 1
                consecutive_errors = 0
            elif status == "failed":
                stats["failed"] += 1
                consecutive_errors += 1
                log.warning("[%s] FAILED: %s", mode_label, driver.current_url)
                if consecutive_errors >= 3:
                    log.warning("[%s] 3 consecutive failures, waiting 30s...", mode_label)
                    time.sleep(30)
                    consecutive_errors = 0

            try:
                save_progress(lastdone_file, driver.current_url)
            except Exception:
                pass
            human_delay()

            # Progress report every 50 photos.
            total = stats["downloaded"] + stats["failed"] + stats["skipped"]
            if total > 0 and total % 50 == 0:
                log.info(
                    "[%s] --- Progress: %d downloaded, %d skipped, %d failed ---",
                    mode_label, stats["downloaded"], stats["skipped"], stats["failed"],
                )

    except KeyboardInterrupt:
        log.info("[%s] Interrupted by user. Progress saved.", mode_label)
        try:
            save_progress(lastdone_file, driver.current_url)
        except Exception:
            pass
    finally:
        log.info(
            "[%s] FINAL: %d downloaded, %d skipped, %d failed",
            mode_label, stats["downloaded"], stats["skipped"], stats["failed"],
        )
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
