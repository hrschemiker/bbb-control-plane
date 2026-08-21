#!/usr/bin/env python3
"""BigBlueButton post-publish hook. It only enqueues immutable job metadata."""
import json, os, sys, tempfile, time
from pathlib import Path

record_id = sys.argv[1] if len(sys.argv) > 1 else ""
if not record_id or "/" in record_id or ".." in record_id:
    raise SystemExit(2)
queue = Path("/var/lib/bcp/jobs")
queue.mkdir(parents=True, exist_ok=True)
job = {"record_id": record_id, "created_at": int(time.time()), "attempts": 0}
fd, name = tempfile.mkstemp(prefix=".job-", dir=queue)
with os.fdopen(fd, "w") as handle:
    json.dump(job, handle); handle.flush(); os.fsync(handle.fileno())
os.replace(name, queue / f"{record_id}.json")
