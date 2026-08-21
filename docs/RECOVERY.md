# Recovery

## WordPress rollback

1. Deactivate the bridge plugin.
2. Restore the prior booking-plugin package if the compatibility hook must be removed.
3. Leave the bridge table in place for evidence and recovery.

Deactivation does not delete any table. Uninstall intentionally has no destructive handler.

## Node recovery

Restore the provider snapshot or provision a clean Ubuntu 22.04 node, then restore:

- `/etc/bigbluebutton`
- `/etc/bbb-control-plane.env`
- required raw or published recording directories
- the application bridge secret

Run `bbb-conf --check`, validate Local Bot API, then process one synthetic recording before enabling production traffic.

## Telegram recovery

Retain the private archive channel. Each application row stores archive chat ID, message ID, bot-scoped file ID, unique file ID, size, and SHA-256. If application data is lost, these fields can be reconstructed by exporting channel message metadata with the same bot identity.
