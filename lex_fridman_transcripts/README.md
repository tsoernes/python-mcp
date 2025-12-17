# Lex Fridman Podcast Transcripts

This directory contains transcripts downloaded from the Lex Fridman Podcast YouTube playlist.

## Download Information

- **Playlist**: https://www.youtube.com/playlist?list=PLrAXtmErZgOdP_8GztsuKi9nrraNbKKp4
- **Download Date**: 2025-12-17
- **Language**: English (en)
- **Tool Used**: youtube_transcript_downloader.py with python-dotenv support

## Statistics

- **Successfully Downloaded**: 75 transcripts
- **Format**: Both JSON and TXT formats available

## Directory Structure

```
lex_fridman_transcripts/
├── json/                  # JSON format with metadata and transcript segments
├── text/                  # Plain text format with metadata header
├── transcript_state.db    # SQLite database tracking download state
└── README.md             # This file
```

## Notes

- Some videos may not have transcripts available due to YouTube restrictions or the video owner's settings
- YouTube IP blocking may occur when downloading large numbers of transcripts in a short time
- The downloader supports daemon mode to periodically check for new videos

## Usage

To update or download more transcripts, run:

```bash
uv run --script scripts/youtube_transcript_downloader.py \
  --url "https://www.youtube.com/playlist?list=PLrAXtmErZgOdP_8GztsuKi9nrraNbKKp4" \
  --output lex_fridman_transcripts \
  --language en
```

To run in daemon mode (checks hourly):

```bash
uv run --script scripts/youtube_transcript_downloader.py \
  --daemon \
  --url "https://www.youtube.com/playlist?list=PLrAXtmErZgOdP_8GztsuKi9nrraNbKKp4" \
  --output lex_fridman_transcripts \
  --language en \
  --interval 3600
```
