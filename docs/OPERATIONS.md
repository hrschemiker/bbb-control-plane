# Operations

## Routine commands

```bash
sudo bcpctl health
sudo bcpctl queue
sudo bbb-record --list-recent
sudo bbb-record --watch
sudo journalctl -u bcp-worker -n 200 --no-pager
sudo journalctl -u telegram-bot-api -n 200 --no-pager
```

## Update procedure

1. Confirm no meeting is active.
2. Confirm the recording queue is empty.
3. Create a provider snapshot.
4. Back up `/etc/bigbluebutton`, `/etc/bbb-control-plane.env`, and the application database.
5. Update the source checkout.
6. Run preflight.
7. Run provisioning again.
8. Execute health checks and a test recording.
9. Remove the snapshot only after validation.

## Disk pressure

The retention worker warns below `MIN_FREE_GB`. It deletes only locally published composite artifacts associated with completed queue receipts. Presentation and raw retention should be enforced with a separately reviewed policy because those assets are required for rebuild operations.

## Failed uploads

Jobs retry with bounded backoff and move to `/var/lib/bcp/failed` after 20 attempts. Do not move a failed job to `done`. Correct the reported condition, reset `attempts` only after inspection, and move the JSON file back to `/var/lib/bcp/jobs`.

## Telegram object ceiling

The configured ceiling defaults to 1900 MiB. Output above the ceiling is rejected before upload. Adjust encoding or implement reviewed segmentation. Never silently truncate a recording.
