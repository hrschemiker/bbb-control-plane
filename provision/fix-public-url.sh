#!/usr/bin/env bash
set -Eeuo pipefail
. /etc/bbb-control-plane.env
expected="https://$BBB_HOSTNAME"
python3 - "$expected" /etc/bigbluebutton/bbb-web.properties /usr/share/bbb-web/WEB-INF/classes/bigbluebutton.properties <<'PY'
from pathlib import Path
import sys

expected = sys.argv[1]
for filename in sys.argv[2:]:
    path = Path(filename)
    if not path.exists():
        continue
    lines = path.read_text(encoding="utf-8").splitlines()
    updated = []
    found = False
    for line in lines:
        if line.lstrip().startswith("bigbluebutton.web.serverURL="):
            updated.append(f"bigbluebutton.web.serverURL={expected}")
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(f"bigbluebutton.web.serverURL={expected}")
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY
