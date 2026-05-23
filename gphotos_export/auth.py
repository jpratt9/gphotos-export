#!/usr/bin/env python3
"""Save Google Photos login session for headless downloading."""

import argparse
import logging
import time

from .utils import (
    create_driver, setup_logging,
    SESSION_DIR, SESSION_DIR_BACKWARD,
    STAGING_DIR, STAGING_DIR_BACKWARD,
    GOOGLE_PHOTOS_URL,
)


def main():
    parser = argparse.ArgumentParser(description="Set up Google Photos login session")
    parser.add_argument("--backward", action="store_true",
                        help="Set up session for backward worker (separate browser profile)")
    args = parser.parse_args()

    setup_logging()
    log = logging.getLogger("setup")

    if args.backward:
        session_dir = SESSION_DIR_BACKWARD
        staging_dir = STAGING_DIR_BACKWARD
    else:
        session_dir = SESSION_DIR
        staging_dir = STAGING_DIR

    session_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    log.info("Opening browser -- log in to Google Photos, then close the browser window")
    driver = create_driver(
        headed=True,
        user_data_dir=session_dir,
        download_dir=staging_dir,
    )
    driver.get(GOOGLE_PHOTOS_URL)

    # Wait until user closes the browser
    while True:
        try:
            if not driver.window_handles:
                break
            time.sleep(1)
        except Exception:
            break

    log.info("Session saved to %s", session_dir)


if __name__ == "__main__":
    main()
