"""Tests for the gphotos_export.utils module."""

from unittest.mock import MagicMock, patch

import pytest

from gphotos_export.utils import (
    clean_url,
    file_already_downloaded,
    load_skiplist,
    organize_file,
    read_progress,
    save_progress,
)


class TestCleanUrl:
    def test_strips_user_segment(self):
        assert clean_url("https://photos.google.com/u/0/photo/abc") == \
            "https://photos.google.com/photo/abc"

    def test_no_user_segment(self):
        url = "https://photos.google.com/photo/abc"
        assert clean_url(url) == url


class TestFileAlreadyDownloaded:
    def test_finds_existing_file(self, tmp_path):
        (tmp_path / "2024" / "1").mkdir(parents=True)
        (tmp_path / "2024" / "1" / "video.mp4").write_bytes(b"\x00")
        assert file_already_downloaded("video.mp4", tmp_path) is True

    def test_returns_false_when_not_found(self, tmp_path):
        (tmp_path / "2024" / "1").mkdir(parents=True)
        assert file_already_downloaded("video.mp4", tmp_path) is False

    def test_skips_staging_dirs(self, tmp_path):
        (tmp_path / ".staging").mkdir()
        (tmp_path / ".staging" / "video.mp4").write_bytes(b"\x00")
        assert file_already_downloaded("video.mp4", tmp_path) is False

    def test_skips_staging_backward_dirs(self, tmp_path):
        (tmp_path / ".staging-backward").mkdir()
        (tmp_path / ".staging-backward" / "video.mp4").write_bytes(b"\x00")
        assert file_already_downloaded("video.mp4", tmp_path) is False


class TestOrganizeFile:
    def test_moves_file_to_year_month(self, tmp_path):
        src = tmp_path / "staging" / "photo.jpg"
        src.parent.mkdir()
        src.write_bytes(b"\xff\xd8\xff")
        downloads = tmp_path / "downloads"
        downloads.mkdir()

        driver = MagicMock()
        with patch("gphotos_export.utils.get_media_date", return_value=(2024, 6)):
            result = organize_file(src, downloads, driver)

        assert result == downloads / "2024" / "6" / "photo.jpg"
        assert result.exists()
        assert not src.exists()

    def test_appends_counter_for_duplicates(self, tmp_path):
        downloads = tmp_path / "downloads"
        (downloads / "2024" / "6").mkdir(parents=True)
        (downloads / "2024" / "6" / "photo.jpg").write_bytes(b"\xff")

        src = tmp_path / "staging" / "photo.jpg"
        src.parent.mkdir()
        src.write_bytes(b"\xff\xd8\xff")

        driver = MagicMock()
        with patch("gphotos_export.utils.get_media_date", return_value=(2024, 6)):
            result = organize_file(src, downloads, driver, overwrite=False)

        assert result.name == "photo 2.jpg"
        assert result.exists()

    def test_appends_counter_3_for_third_dup(self, tmp_path):
        downloads = tmp_path / "downloads"
        (downloads / "2024" / "6").mkdir(parents=True)
        (downloads / "2024" / "6" / "photo.jpg").write_bytes(b"\xff")
        (downloads / "2024" / "6" / "photo 2.jpg").write_bytes(b"\xff")

        src = tmp_path / "staging" / "photo.jpg"
        src.parent.mkdir()
        src.write_bytes(b"\xff\xd8\xff")

        driver = MagicMock()
        with patch("gphotos_export.utils.get_media_date", return_value=(2024, 6)):
            result = organize_file(src, downloads, driver, overwrite=False)

        assert result.name == "photo 3.jpg"

    def test_overwrite_replaces_existing(self, tmp_path):
        downloads = tmp_path / "downloads"
        (downloads / "2024" / "6").mkdir(parents=True)
        (downloads / "2024" / "6" / "photo.jpg").write_bytes(b"old")

        src = tmp_path / "staging" / "photo.jpg"
        src.parent.mkdir()
        src.write_bytes(b"new")

        driver = MagicMock()
        with patch("gphotos_export.utils.get_media_date", return_value=(2024, 6)):
            result = organize_file(src, downloads, driver, overwrite=True)

        assert result.name == "photo.jpg"
        assert result.read_bytes() == b"new"


class TestOrganizeFileZip:
    def test_extracts_zip_into_dest_folder(self, tmp_path):
        import zipfile

        # Create a zip with two files.
        staging = tmp_path / "staging"
        staging.mkdir()
        zip_path = staging / "photos.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("photo1.jpg", b"\xff\xd8\xff")
            zf.writestr("photo2.jpg", b"\xff\xd8\xff")

        downloads = tmp_path / "downloads"
        downloads.mkdir()

        driver = MagicMock()
        with patch("gphotos_export.utils.get_media_date", return_value=(2024, 6)), \
             patch("gphotos_export.utils.get_date_from_html", return_value=(2024, 6)):
            result = organize_file(zip_path, downloads, driver)

        assert (downloads / "2024" / "6" / "photo1.jpg").exists()
        assert (downloads / "2024" / "6" / "photo2.jpg").exists()
        assert not zip_path.exists()  # zip deleted after extraction

    def test_non_zip_with_zip_extension_treated_normally(self, tmp_path):
        staging = tmp_path / "staging"
        staging.mkdir()
        fake_zip = staging / "notazip.zip"
        fake_zip.write_bytes(b"this is not a zip")

        downloads = tmp_path / "downloads"
        downloads.mkdir()

        driver = MagicMock()
        with patch("gphotos_export.utils.get_media_date", return_value=(2024, 6)):
            result = organize_file(fake_zip, downloads, driver)

        assert result == downloads / "2024" / "6" / "notazip.zip"


class TestReadProgress:
    def test_reads_url(self, tmp_path):
        f = tmp_path / ".lastdone"
        f.write_text("https://photos.google.com/photo/abc\n")
        assert read_progress(f) == "https://photos.google.com/photo/abc"

    def test_raises_on_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_progress(tmp_path / ".lastdone")

    def test_raises_on_empty(self, tmp_path):
        f = tmp_path / ".lastdone"
        f.write_text("")
        with pytest.raises(ValueError):
            read_progress(f)


class TestLoadSkiplist:
    def test_loads_urls(self, tmp_path):
        f = tmp_path / "skiplist.txt"
        f.write_text("https://photos.google.com/photo/abc\nhttps://photos.google.com/photo/def\n")
        result = load_skiplist(f)
        assert len(result) == 2
        assert "https://photos.google.com/photo/abc" in result

    def test_ignores_comments(self, tmp_path):
        f = tmp_path / "skiplist.txt"
        f.write_text("# this is a comment\nhttps://photos.google.com/photo/abc\n")
        result = load_skiplist(f)
        assert len(result) == 1

    def test_ignores_blank_lines(self, tmp_path):
        f = tmp_path / "skiplist.txt"
        f.write_text("\nhttps://photos.google.com/photo/abc\n\n")
        result = load_skiplist(f)
        assert len(result) == 1

    def test_returns_empty_when_missing(self, tmp_path):
        result = load_skiplist(tmp_path / "nope.txt")
        assert result == set()

    def test_strips_user_segment(self, tmp_path):
        f = tmp_path / "skiplist.txt"
        f.write_text("https://photos.google.com/u/0/photo/abc\n")
        result = load_skiplist(f)
        assert "https://photos.google.com/photo/abc" in result


class TestSaveProgress:
    def test_saves_url(self, tmp_path):
        f = tmp_path / ".lastdone"
        save_progress(f, "https://photos.google.com/photo/abc")
        assert f.read_text() == "https://photos.google.com/photo/abc"

    def test_ignores_non_photos_url(self, tmp_path):
        f = tmp_path / ".lastdone"
        save_progress(f, "https://accounts.google.com/signin")
        assert not f.exists()
