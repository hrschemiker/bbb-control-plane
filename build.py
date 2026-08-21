#!/usr/bin/env python3
"""Create deterministic distribution archives."""
from pathlib import Path
import hashlib, shutil, zipfile

root = Path(__file__).resolve().parent
dist = root / "dist"; dist.mkdir(exist_ok=True)
plugin = root / "wordpress" / "gtbp-recording-bridge"
target = dist / "gtbp-recording-bridge-1.0.0.zip"
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
    for path in sorted(plugin.rglob("*")):
        if path.is_file():
            info = zipfile.ZipInfo(path.relative_to(plugin.parent).as_posix(), (2026, 1, 1, 0, 0, 0)); info.external_attr = 0o644 << 16
            z.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
digest = hashlib.sha256(target.read_bytes()).hexdigest()
(dist / "SHA256SUMS").write_text(f"{digest}  {target.name}\n")
print(target)
