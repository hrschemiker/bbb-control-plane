#!/usr/bin/env python3
import hashlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


base, secret = sys.argv[1].rstrip("/"), sys.argv[2]
meeting_id = f"bcp-validation-{int(time.time())}"
moderator_password = "bcp-validation-moderator"


def api_url(call, parameters):
    query = urllib.parse.urlencode(parameters)
    checksum = hashlib.sha1(f"{call}{query}{secret}".encode()).hexdigest()
    return f"{base}/bigbluebutton/api/{call}?{query}&checksum={checksum}"


create = api_url("create", {
    "name": "BCP URL validation",
    "meetingID": meeting_id,
    "attendeePW": "bcp-validation-attendee",
    "moderatorPW": moderator_password,
    "record": "false",
})
end = api_url("end", {"meetingID": meeting_id, "password": moderator_password})


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


try:
    response = urllib.request.urlopen(create, timeout=20).read().decode("utf-8", "replace")
    if "<returncode>SUCCESS</returncode>" not in response:
        raise RuntimeError("synthetic meeting creation failed")
    join = api_url("join", {
        "fullName": "BCP Validator",
        "meetingID": meeting_id,
        "password": moderator_password,
        "redirect": "true",
    })
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(join, timeout=20)
        raise RuntimeError("join API did not return a redirect")
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise
        location = exc.headers.get("Location", "")
    expected = f"{base}/html5client"
    if not location.startswith(expected):
        raise RuntimeError(f"join redirect uses an unexpected origin: {location}")
    print(f"[bcp] join redirect verified: {expected}")
finally:
    try:
        urllib.request.urlopen(end, timeout=20).read()
    except Exception:
        pass
