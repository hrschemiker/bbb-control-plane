# Recovery

## WordPress rollback

1. Deactivate the bridge plugin.
2. Restore the prior booking-plugin package if the compatibility hook must be removed.
3. Leave the bridge table in place for evidence and recovery.

Deactivation does not delete any table. Uninstall intentionally has no destructive handler.

## Node recovery

### Automatic interrupted-install recovery

Provisioning records its current phase in `/var/lib/bcp/provision/state` and its durable log in `/var/log/bcp-provision.log`. It runs in `bcp-provision.service`, so a workstation shutdown or SSH interruption does not terminate server installation. A later Provision action attaches to the active service or starts a new idempotent recovery run.

The recovery order is fixed:

1. Keep a healthy matching BBB installation.
2. Repair `dpkg` and APT state and resume with the upstream installer.
3. Repeat once after package repair.
4. Back up configuration under `/var/backups/bcp`, purge only partial BBB packages, and perform one final bounded retry.

The cleanup stage does not remove `/var/bigbluebutton`, WordPress data, recording queues, Telegram state, or the local private environment file.

Restore the provider snapshot or provision a clean Ubuntu 22.04 node, then restore:

- `/etc/bigbluebutton`
- `/etc/bbb-control-plane.env`
- required raw or published recording directories
- the application bridge secret

Run `bbb-conf --check`, validate Local Bot API, then process one synthetic recording before enabling production traffic.

## Telegram recovery

Retain the private archive channel. Each application row stores archive chat ID, message ID, bot-scoped file ID, unique file ID, size, and SHA-256. If application data is lost, these fields can be reconstructed by exporting channel message metadata with the same bot identity.
