#!/usr/bin/env python3
"""
YouTube Transcript Downloader

Downloads transcripts from YouTube videos, playlists, or channels with daemon mode support.

Usage:
    # Download from a single video
    python youtube_transcript_downloader.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --output ./transcripts --language en

    # Download from a playlist
    python youtube_transcript_downloader.py --url "https://www.youtube.com/playlist?list=PLAYLIST_ID" --output ./transcripts --language en

    # Download from a channel
    python youtube_transcript_downloader.py --url "https://www.youtube.com/@channel" --output ./transcripts --language en

    # Run as daemon (checks every hour by default)
    python youtube_transcript_downloader.py --daemon --url "https://www.youtube.com/playlist?list=PLAYLIST_ID" --output ./transcripts --language en --interval 3600

    # Multiple sources
    python youtube_transcript_downloader.py --url URL1 URL2 URL3 --output ./transcripts --language en

    # With date range filter
    python youtube_transcript_downloader.py --url URL --output ./transcripts --language en --after 2024-01-01 --before 2024-12-31

Dependencies:
    pip install yt-dlp youtube-transcript-api

# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "yt-dlp>=2024.0.0",
#   "youtube-transcript-api>=0.6.0",
#   "python-dotenv>=1.0.0",
# ]
# ///
"""

import argparse
import hashlib
import json
import logging
import os
import re
import signal
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import yt_dlp
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

# Load environment variables from .env file in scripts directory
load_dotenv(dotenv_path=Path(__file__).parent / ".env")


class TranscriptDatabase:
    """Manages SQLite database for tracking download state."""

    def __init__(self, db_path: Path):
        self.db_path: Path = db_path
        self.conn: sqlite3.Connection
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        _ = self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS videos (
                video_id TEXT PRIMARY KEY,
                title TEXT,
                uploader TEXT,
                upload_date TEXT,
                duration INTEGER,
                view_count INTEGER,
                downloaded_at TEXT,
                source_url TEXT,
                language TEXT,
                content_hash TEXT
            )
        """
        )
        _ = self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY,
                source_type TEXT,
                last_check TEXT,
                language TEXT
            )
        """
        )
        _ = self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS failed_downloads (
                video_id TEXT PRIMARY KEY,
                error TEXT,
                retry_count INTEGER DEFAULT 0,
                last_attempt TEXT
            )
        """
        )
        # Create FTS5 full-text search virtual table for transcripts
        _ = self.conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
                video_id UNINDEXED,
                title,
                uploader,
                transcript_text,
                upload_date UNINDEXED,
                tokenize = 'porter unicode61'
            )
        """
        )
        self.conn.commit()

    def is_downloaded(self, video_id: str) -> bool:
        """Check if video has been downloaded."""
        cursor = self.conn.execute(
            "SELECT 1 FROM videos WHERE video_id = ?", (video_id,)
        )
        return cursor.fetchone() is not None

    def add_video(
        self,
        video_id: str,
        metadata: dict[str, Any],
        source_url: str,
        language: str,
        content_hash: str | None = None,
    ) -> None:
        """Record a downloaded video."""
        self.conn.execute(
            """
            INSERT OR REPLACE INTO videos
            (video_id, title, uploader, upload_date, duration, view_count, downloaded_at, source_url, language, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,  # type: ignore[arg-type]
            (
                video_id,
                metadata.get("title"),
                metadata.get("uploader"),
                metadata.get("upload_date"),
                metadata.get("duration"),
                metadata.get("view_count"),
                datetime.now().isoformat(),
                source_url,
                language,
                content_hash,
            ),
        )
        self.conn.commit()

    def update_source_check(
        self, source_id: str, source_type: str, language: str
    ) -> None:
        """Update last check time for a source."""
        self.conn.execute(
            """
            INSERT OR REPLACE INTO sources (source_id, source_type, last_check, language)
            VALUES (?, ?, ?, ?)
        """,  # type: ignore[arg-type]
            (source_id, source_type, datetime.now().isoformat(), language),
        )
        self.conn.commit()

    def get_last_check(self, source_id: str) -> datetime | None:
        """Get last check time for a source."""
        cursor = self.conn.execute(
            "SELECT last_check FROM sources WHERE source_id = ?", (source_id,)
        )
        result = cursor.fetchone()
        if result:
            return datetime.fromisoformat(result[0])
        return None

    def add_failed_download(self, video_id: str, error: str):
        """Record a failed download attempt."""
        self.conn.execute(
            """
            INSERT OR REPLACE INTO failed_downloads (video_id, error, retry_count, last_attempt)
            VALUES (
                ?,
                ?,
                COALESCE((SELECT retry_count FROM failed_downloads WHERE video_id = ?), 0) + 1,
                ?
            )
        """,  # type: ignore[arg-type]
            (video_id, error, video_id, datetime.now().isoformat()),
        )
        self.conn.commit()

    def get_failed_count(self, video_id: str) -> int:
        """Get number of failed attempts for a video."""
        cursor = self.conn.execute(
            "SELECT retry_count FROM failed_downloads WHERE video_id = ?", (video_id,)
        )
        result = cursor.fetchone()
        return result[0] if result else 0

    def add_to_fts(
        self,
        video_id: str,
        title: str,
        uploader: str,
        transcript_text: str,
        upload_date: str,
    ) -> None:
        """Add transcript to full-text search index."""
        self.conn.execute(
            """
            INSERT INTO transcripts_fts (video_id, title, uploader, transcript_text, upload_date)
            VALUES (?, ?, ?, ?, ?)
        """,  # type: ignore[arg-type]
            (video_id, title, uploader, transcript_text, upload_date),
        )
        self.conn.commit()

    def find_duplicate_by_hash(self, content_hash: str) -> str | None:
        """Find video with matching content hash (duplicate detection)."""
        cursor = self.conn.execute(
            "SELECT video_id FROM videos WHERE content_hash = ?", (content_hash,)
        )
        result = cursor.fetchone()
        return result[0] if result else None

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()


class YouTubeTranscriptDownloader:
    """Downloads YouTube transcripts with daemon mode support."""

    def __init__(
        self,
        output_dir: Path,
        language: str = "en",
        max_workers: int = 4,
        max_retries: int = 3,
        after_date: str | None = None,
        before_date: str | None = None,
        enable_deduplication: bool = True,
    ):
        self.output_dir: Path = Path(output_dir).resolve()
        self.language: str = language
        self.max_workers: int = max_workers
        self.max_retries: int = max_retries
        self.enable_deduplication: bool = enable_deduplication
        self.after_date: datetime | None = (
            datetime.strptime(after_date, "%Y-%m-%d") if after_date else None
        )
        self.before_date: datetime | None = (
            datetime.strptime(before_date, "%Y-%m-%d") if before_date else None
        )

        # Create output directories
        self.json_dir: Path = self.output_dir / "json"
        self.text_dir: Path = self.output_dir / "text"
        self.json_dir.mkdir(parents=True, exist_ok=True)
        self.text_dir.mkdir(parents=True, exist_ok=True)

        # Initialize database
        db_path = self.output_dir / "transcript_state.db"
        self.db: TranscriptDatabase = TranscriptDatabase(db_path)

        # Setup logging
        log_path = self.output_dir / "downloader.log"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler(log_path),
                logging.StreamHandler(),
            ],
        )
        self.logger: logging.Logger = logging.getLogger(__name__)

        # Shutdown flag for daemon mode
        self.shutdown_flag: bool = False

    def extract_video_id(self, url: str) -> str | None:
        """Extract video ID from YouTube URL."""
        patterns = [
            r"(?:v=|\/videos\/|embed\/|youtu.be\/|\/v\/|watch\?v=|&v=)([^#&?\n]+)",
            r"^([a-zA-Z0-9_-]{11})$",  # Direct video ID
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def extract_playlist_id(self, url: str) -> str | None:
        """Extract playlist ID from YouTube URL."""
        patterns = [
            r"list=([^#&?\n]+)",
            r"^(PL[a-zA-Z0-9_-]+)$",  # Direct playlist ID
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def get_video_metadata(self, video_id: str) -> dict[str, Any] | None:
        """Fetch video metadata using yt-dlp."""
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }  # type: ignore[var-annotated]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={video_id}", download=False
                )
                if not info:
                    return None

                return {
                    "video_id": video_id,
                    "title": info.get("title", "Unknown"),
                    "uploader": info.get("uploader", "Unknown"),
                    "uploader_id": info.get("uploader_id", "Unknown"),
                    "channel": info.get("channel", "Unknown"),
                    "channel_id": info.get("channel_id", "Unknown"),
                    "upload_date": info.get("upload_date", "Unknown"),
                    "duration": info.get("duration", 0),
                    "view_count": info.get("view_count", 0),
                    "like_count": info.get("like_count", 0),
                    "description": info.get("description", ""),
                    "tags": info.get("tags", []),
                    "categories": info.get("categories", []),
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                }
        except Exception as e:
            self.logger.error(f"Failed to get metadata for {video_id}: {e}")
            return None

    def get_transcript(self, video_id: str) -> list[Any] | None:
        """Fetch transcript using youtube-transcript-api with language fallback."""
        try:
            # Try to get transcript in specified language
            api = YouTubeTranscriptApi()
            transcript_list = api.list(video_id)

            # Try specified language first
            try:
                transcript = transcript_list.find_transcript([self.language])
                return list(transcript.fetch())
            except NoTranscriptFound:
                # Fall back to any available transcript
                try:
                    transcript = transcript_list.find_generated_transcript(
                        ["en", "en-US", "en-GB"]
                    )
                    self.logger.warning(
                        f"Language '{self.language}' not found for {video_id}, using auto-generated English"
                    )
                    return list(transcript.fetch())
                except NoTranscriptFound:
                    # Try any available transcript
                    available = list(transcript_list)
                    if available:
                        transcript = available[0]
                        self.logger.warning(
                            f"Using fallback language '{transcript.language_code}' for {video_id}"
                        )
                        return list(transcript.fetch())
                    raise

        except (TranscriptsDisabled, VideoUnavailable) as e:
            self.logger.warning(f"No transcript available for {video_id}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Error fetching transcript for {video_id}: {e}")
            return None

    def format_metadata_header(self, metadata: dict[str, Any]) -> str:
        """Format metadata as a header for the transcript."""
        upload_date = metadata.get("upload_date", "Unknown")
        if upload_date != "Unknown" and len(upload_date) == 8:
            upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

        duration = metadata.get("duration", 0)
        hours = duration // 3600
        minutes = (duration % 3600) // 60
        seconds = duration % 60
        duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        header = f"""Video ID: {metadata.get("video_id")}
Title: {metadata.get("title")}
Uploader: {metadata.get("uploader")}
Channel: {metadata.get("channel")}
Upload Date: {upload_date}
Duration: {duration_str}
View Count: {metadata.get("view_count", 0):,}
Like Count: {metadata.get("like_count", 0):,}
URL: {metadata.get("url")}
Description: {metadata.get("description", "")[:500]}{"..." if len(metadata.get("description", "")) > 500 else ""}
Tags: {", ".join(metadata.get("tags", [])[:10])}
Categories: {", ".join(metadata.get("categories", []))}

---TRANSCRIPT---

"""
        return header

    def should_download_video(self, metadata: dict[str, Any]) -> bool:
        """Check if video matches date filters."""
        if not self.after_date and not self.before_date:
            return True

        upload_date_str = metadata.get("upload_date")
        if not upload_date_str or upload_date_str == "Unknown":
            return True

        try:
            upload_date = datetime.strptime(upload_date_str, "%Y%m%d")

            if self.after_date and upload_date < self.after_date:
                return False
            if self.before_date and upload_date > self.before_date:
                return False

            return True
        except ValueError:
            return True

    def download_transcript(self, video_id: str, source_url: str) -> tuple[bool, str]:
        """Download transcript for a single video."""
        # Check if already downloaded in database
        if self.db.is_downloaded(video_id):
            return True, f"Already downloaded: {video_id}"

        # Additional check: verify files actually exist
        json_path = self.json_dir / f"{video_id}.json"
        text_path = self.text_dir / f"{video_id}.txt"
        if json_path.exists() and text_path.exists():
            self.logger.info(f"Files already exist for {video_id}, skipping download")
            return True, f"Already downloaded (files exist): {video_id}"

        # Check retry count
        retry_count = self.db.get_failed_count(video_id)
        if retry_count >= self.max_retries:
            return False, f"Max retries exceeded for {video_id}"

        # Get metadata
        metadata = self.get_video_metadata(video_id)
        if not metadata:
            error_msg = f"Failed to get metadata for {video_id}"
            self.db.add_failed_download(video_id, error_msg)
            return False, error_msg

        # Check date filters
        if not self.should_download_video(metadata):
            return (
                True,
                f"Skipped {video_id} - outside date range: {metadata.get('upload_date')}",
            )

        # Get transcript
        transcript = self.get_transcript(video_id)
        if not transcript:
            error_msg = f"No transcript available for {video_id}"
            self.db.add_failed_download(video_id, error_msg)
            return False, error_msg

        # Generate plain text for deduplication check
        plain_text = "\n".join(segment.text for segment in transcript)
        content_hash = hashlib.sha256(plain_text.encode("utf-8")).hexdigest()

        # Check for duplicates
        if self.enable_deduplication:
            duplicate_id = self.db.find_duplicate_by_hash(content_hash)
            if duplicate_id and duplicate_id != video_id:
                self.logger.warning(
                    f"Skipping {video_id} - duplicate of {duplicate_id} (same transcript content)"
                )
                return (
                    True,
                    f"Skipped {video_id} - duplicate of {duplicate_id}",
                )

        # Save JSON format with metadata
        # Convert transcript objects to dicts for JSON serialization
        transcript_dicts = [
            {"text": segment.text, "start": segment.start, "duration": segment.duration}
            for segment in transcript
        ]

        json_data = {
            "metadata": metadata,
            "transcript": transcript_dicts,
            "downloaded_at": datetime.now().isoformat(),
            "language": self.language,
        }

        json_path = self.json_dir / f"{video_id}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)

        # Save plain text format
        text_content = self.format_metadata_header(metadata)
        text_content += "\n".join(segment.text for segment in transcript)

        text_path = self.text_dir / f"{video_id}.txt"
        with open(text_path, "w", encoding="utf-8") as f:
            f.write(text_content)

        # Record in database with content hash
        self.db.add_video(video_id, metadata, source_url, self.language, content_hash)

        # Add to full-text search index
        self.db.add_to_fts(
            video_id,
            metadata.get("title", "Unknown"),
            metadata.get("uploader", "Unknown"),
            plain_text,
            metadata.get("upload_date", "Unknown"),
        )

        return True, f"Successfully downloaded: {video_id} - {metadata.get('title')}"

    def get_playlist_videos(self, playlist_id: str) -> list[str]:
        """Get all video IDs from a playlist."""
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
        }  # type: ignore[var-annotated]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/playlist?list={playlist_id}",
                    download=False,
                )
                if not info or "entries" not in info:
                    return []

                video_ids = []
                for entry in info["entries"]:
                    if entry and "id" in entry:
                        video_ids.append(entry["id"])

                return video_ids
        except Exception as e:
            self.logger.error(f"Failed to get playlist videos for {playlist_id}: {e}")
            return []

    def get_channel_videos(self, channel_url: str) -> list[str]:
        """Get all video IDs from a channel."""
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "playlistend": 1000,  # Limit to recent videos for performance
        }  # type: ignore[var-annotated]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"{channel_url}/videos", download=False)
                if not info or "entries" not in info:
                    return []

                video_ids = []
                for entry in info["entries"]:
                    if entry and "id" in entry:
                        video_ids.append(entry["id"])

                return video_ids
        except Exception as e:
            self.logger.error(f"Failed to get channel videos for {channel_url}: {e}")
            return []

    def process_url(self, url: str) -> tuple[int, int]:
        """Process a URL (video, playlist, or channel) and return (success, total) counts."""
        success_count = 0
        total_count = 0

        # Determine URL type and get video IDs
        video_ids = []
        source_type = "unknown"
        source_id = url

        video_id = self.extract_video_id(url)
        playlist_id = self.extract_playlist_id(url)

        if playlist_id and "list=" in url:
            self.logger.info(f"Processing playlist: {playlist_id}")
            video_ids = self.get_playlist_videos(playlist_id)
            source_type = "playlist"
            source_id = playlist_id
        elif video_id and ("watch?v=" in url or len(url) == 11):
            self.logger.info(f"Processing single video: {video_id}")
            video_ids = [video_id]
            source_type = "video"
            source_id = video_id
        elif "@" in url or "/c/" in url or "/channel/" in url or "/user/" in url:
            self.logger.info(f"Processing channel: {url}")
            video_ids = self.get_channel_videos(url)
            source_type = "channel"
            source_id = url

        if not video_ids:
            self.logger.error(f"No videos found for URL: {url}")
            return 0, 0

        total_count = len(video_ids)
        self.logger.info(f"Found {total_count} videos to process")

        # Download transcripts in parallel
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.download_transcript, vid, url): vid
                for vid in video_ids
            }

            for future in as_completed(futures):
                video_id: str = futures[future]
                try:
                    success, message = future.result()
                    if success:
                        success_count += 1
                    self.logger.info(message)
                except Exception as e:
                    self.logger.error(
                        f"Unexpected error processing {video_id}: {e}", exc_info=True
                    )

        # Update source check time
        self.db.update_source_check(source_id, source_type, self.language)

        return success_count, total_count

    def run_daemon(self, urls: list[str], interval: int) -> None:
        """Run in daemon mode, checking for new videos periodically."""
        self.logger.info(
            f"Starting daemon mode with {len(urls)} sources, checking every {interval} seconds"
        )

        # Setup signal handlers for graceful shutdown
        def signal_handler(signum: int, frame: Any) -> None:
            self.logger.info(f"Received signal {signum}, shutting down gracefully...")
            self.shutdown_flag = True

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        iteration = 0
        while not self.shutdown_flag:
            iteration += 1
            self.logger.info(f"=== Daemon iteration {iteration} starting ===")

            for url in urls:
                if self.shutdown_flag:
                    break

                try:
                    success, total = self.process_url(url)
                    self.logger.info(
                        f"Completed {url}: {success}/{total} transcripts downloaded"
                    )
                except Exception as e:
                    self.logger.error(f"Error processing {url}: {e}", exc_info=True)

            if not self.shutdown_flag:
                self.logger.info(
                    f"=== Daemon iteration {iteration} complete, sleeping for {interval} seconds ==="
                )
                # Sleep in smaller intervals to allow for responsive shutdown
                sleep_interval = min(interval, 60)
                for _ in range(0, interval, sleep_interval):
                    if self.shutdown_flag:
                        break
                    time.sleep(sleep_interval)

        self.logger.info("Daemon shutdown complete")

    def close(self) -> None:
        """Clean up resources."""
        self.db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download YouTube transcripts from videos, playlists, or channels",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single video
  %(prog)s --url "https://www.youtube.com/watch?v=VIDEO_ID" --output ./transcripts --language en

  # Playlist
  %(prog)s --url "https://www.youtube.com/playlist?list=PLAYLIST_ID" --output ./transcripts --language en

  # Channel
  %(prog)s --url "https://www.youtube.com/@channel" --output ./transcripts --language en

  # Multiple sources
  %(prog)s --url URL1 URL2 URL3 --output ./transcripts --language en

  # Daemon mode (checks every hour)
  %(prog)s --daemon --url "https://www.youtube.com/playlist?list=PLAYLIST_ID" --output ./transcripts --language en --interval 3600

  # With date filters
  %(prog)s --url URL --output ./transcripts --language en --after 2024-01-01 --before 2024-12-31
        """,
    )

    parser.add_argument(
        "--url",
        "-u",
        nargs="+",
        required=False,
        help="YouTube URL(s) or ID(s) - can be video, playlist, or channel",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        required=True,
        help="Output directory for transcripts",
    )
    parser.add_argument(
        "--language",
        "-l",
        type=str,
        default="en",
        help="Preferred transcript language (default: en)",
    )
    parser.add_argument(
        "--daemon",
        "-d",
        action="store_true",
        help="Run in daemon mode, checking for new videos periodically",
    )
    parser.add_argument(
        "--interval",
        "-i",
        type=int,
        default=3600,
        help="Check interval in seconds for daemon mode (default: 3600 = 1 hour)",
    )
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=4,
        help="Number of parallel download workers (default: 4)",
    )
    parser.add_argument(
        "--max-retries",
        "-r",
        type=int,
        default=3,
        help="Maximum retry attempts for failed downloads (default: 3)",
    )
    parser.add_argument(
        "--after",
        "-a",
        type=str,
        help="Only download videos uploaded after this date (format: YYYY-MM-DD)",
    )
    parser.add_argument(
        "--before",
        "-b",
        type=str,
        help="Only download videos uploaded before this date (format: YYYY-MM-DD)",
    )
    parser.add_argument(
        "--no-deduplication",
        action="store_true",
        help="Disable duplicate detection (download all videos even if transcripts are identical)",
    )
    parser.add_argument(
        "--rebuild-index",
        action="store_true",
        help="Rebuild the full-text search index from existing transcript files",
    )

    args = parser.parse_args()

    # Handle rebuild index mode
    if args.rebuild_index:
        output_path = Path(args.output).resolve()
        json_dir = output_path / "json"
        db_path = output_path / "transcript_state.db"

        if not json_dir.exists():
            print(f"Error: No JSON directory found at {json_dir}")
            print("Download transcripts first before rebuilding the index.")
            sys.exit(1)

        if not db_path.exists():
            print(f"Error: No database found at {db_path}")
            print("Download transcripts first before rebuilding the index.")
            sys.exit(1)

        print("Rebuilding full-text search index from existing transcripts...")
        db = TranscriptDatabase(db_path)

        # Clear existing FTS entries
        db.conn.execute("DELETE FROM transcripts_fts")
        db.conn.commit()

        count = 0
        for json_file in json_dir.glob("*.json"):
            try:
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)

                video_id = data["metadata"]["video_id"]
                title = data["metadata"].get("title", "Unknown")
                uploader = data["metadata"].get("uploader", "Unknown")
                upload_date = data["metadata"].get("upload_date", "Unknown")

                # Extract plain text from transcript
                transcript_text = " ".join(seg["text"] for seg in data["transcript"])

                # Add to FTS index
                db.add_to_fts(video_id, title, uploader, transcript_text, upload_date)
                count += 1

                if count % 10 == 0:
                    print(f"Indexed {count} transcripts...")

            except Exception as e:
                print(f"Warning: Failed to index {json_file.name}: {e}")
                continue

        print(f"\nSuccessfully rebuilt index with {count} transcripts!")
        db.close()
        sys.exit(0)

    # Validate required arguments for download mode
    if not args.url:
        print("Error: --url is required for download mode")
        print(
            "Use --search to search existing transcripts, or provide --url to download"
        )
        sys.exit(1)

    # Create downloader instance
    downloader = YouTubeTranscriptDownloader(
        output_dir=args.output,
        language=args.language,
        max_workers=args.workers,
        max_retries=args.max_retries,
        after_date=args.after,
        before_date=args.before,
        enable_deduplication=not args.no_deduplication,
    )

    try:
        if args.daemon:
            downloader.run_daemon(args.url, args.interval)
        else:
            total_success = 0
            total_videos = 0

            for url in args.url:
                success, total = downloader.process_url(url)
                total_success += success
                total_videos += total

            print(
                f"\n{'=' * 60}\nDownload complete: {total_success}/{total_videos} transcripts downloaded\n{'=' * 60}"
            )

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n\nFatal error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        downloader.close()


if __name__ == "__main__":
    main()
