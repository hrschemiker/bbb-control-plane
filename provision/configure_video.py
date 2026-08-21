#!/usr/bin/env python3
"""Conservatively enable MP4 output without replacing unrelated YAML."""
from pathlib import Path
import re, sys

path = Path(sys.argv[1]); crf = int(sys.argv[2]); height = int(sys.argv[3])
text = path.read_text()
if not 18 <= crf <= 30 or height not in (480, 720, 1080):
    raise SystemExit("unsafe video parameters")
backup = path.with_suffix(path.suffix + ".bcp-backup")
if not backup.exists(): backup.write_text(text)
if re.search(r"(?m)^\s*video_formats:\s*$", text):
    block = "video_formats:\n  - webm\n  - mp4"
    text = re.sub(r"(?ms)^video_formats:\s*\n(?:\s+-\s+\w+\s*\n?)+", block + "\n", text)
else:
    text += "\nvideo_formats:\n  - webm\n  - mp4\n"
path.write_text(text)
