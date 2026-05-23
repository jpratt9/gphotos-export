# gphotos-export

Bulk-download your entire Google Photos library in original quality via undetected browser automation.

## Why this exists

Getting your *own* photos back out of Google Photos in original quality is weirdly hard in 2026:

- **Google Takeout is extremely flaky and unreliable** — it's slow, manual, non-incremental, and for large libraries it routinely hands you broken, incomplete, or impossible-to-reassemble archives.
- **The Photos API can't help** — Google removed the read scopes that backup tools relied on in March 2025 (which also broke `rclone`-based backups).
- **I couldn't find a tool that actually fit** — specifically one that is (a) still maintained in 2026, (b) written in Python, and (c) does the *downloading* for you using safe, reliable scraping — i.e. undetected automation that behaves like a real logged-in user, with retries and resume instead of brittle hacks. The closest prior art (see [Credits](#credits--acknowledgements)) is an unmaintained Node/Playwright project.

So this exists to fill that gap: a maintained, Python, hardened downloader.

> Worth knowing: Google has been known to disable entire accounts with little recourse (see [theywillbanyou.com](https://theywillbanyou.com)) — which is exactly why keeping your own local copy matters. Run this on the account you're backing up, and understand that automating *any* account carries its own ban risk.

## How it works

Uses **SeleniumBase** in undetected-Chrome (`uc`) mode to drive the Google Photos web UI the way a real logged-in person would:

1. Opens each photo in the viewer
2. Presses **Shift+D** (Google Photos' native download shortcut)
3. Reads EXIF metadata (falls back to page HTML) to sort files into `year/month/` folders
4. Auto-extracts any zip bundles Google returns
5. Saves progress to a checkpoint file so you can stop and resume anytime

## Install

```bash
pip install gphotos-export
```

Optional but recommended: install **exiftool** for the most accurate photo dates (it falls back to parsing the page when exiftool isn't present):

- macOS: `brew install exiftool`
- Debian/Ubuntu: `sudo apt-get install libimage-exiftool-perl`

> The tool operates in the **current directory** — it creates `session/`, `downloads-2/`, and `logs/` wherever you run it. Pick a working folder and run it from there.

## Usage

```bash
# 1. Log in once (opens a browser; sign into Google Photos, then close the window)
gphotos-export-login

# 2. Seed the starting point with your OLDEST photo's URL
echo "https://photos.google.com/photo/YOUR_OLDEST_PHOTO_ID" > .lastdone

# 3. Download (a visible browser is recommended for the first run)
gphotos-export --headed

# Headless
gphotos-export

# Dry run (navigate without downloading)
gphotos-export --dry-run --headed
```

### ~2x speed: run both directions at once

```bash
gphotos-export-login --backward          # one-time: set up a second browser profile
gphotos-export --headed &                # forward:  oldest -> newest
gphotos-export --headed --backward &     # backward: newest -> oldest
```

## Features

- Original-quality downloads via the Shift+D shortcut
- Undetected automation (`uc` mode) that behaves like a real logged-in user
- EXIF date extraction (falls back to HTML parsing)
- Organized output: `downloads-2/year/month/filename`
- Resume support via `.lastdone` / `backward.lastdone` checkpoints
- Two-worker mode (`--backward`) for ~2x throughput
- Auto-extracts zip bundles from Google into the right folder
- Skip list: add URLs to `skiplist.txt` to skip specific items
- 3-attempt retry with exponential backoff + error resilience
- Human-realistic random delays
- Ctrl+C safe (saves progress on interrupt)

## Credits / Acknowledgements

This project began as a Python port of **[vikas5914/google-photos-backup](https://github.com/vikas5914/google-photos-backup)** by **Vikas Kapadiya** (MIT-licensed, JavaScript/Playwright). The original's core approach — driving the Photos web UI, Shift+D downloads, year/month sorting, and `.lastdone` resume — is his work, and this wouldn't exist without it.

This version is a substantial rework: rewritten in Python on SeleniumBase undetected-Chrome, with retry/backoff, multi-strategy navigation, a backward worker, zip auto-extraction, a skiplist, file logging, and a test suite.

Huge thanks to Vikas. Both copyrights are preserved under MIT — see [LICENSE](LICENSE).

## License

MIT — see [LICENSE](LICENSE). Original work © Vikas Kapadiya; Python port and rework © 2026 John Pratt.
