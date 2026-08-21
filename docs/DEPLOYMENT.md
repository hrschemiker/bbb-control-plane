# Deployment

## DNS prerequisite

Create one DNS record before provisioning:

| Type | Name | Value | Proxy |
|---|---|---|---|
| `A` | selected subdomain, for example `class` | public IPv4 of the media node | DNS only |

If Cloudflare manages the zone, disable the orange-cloud proxy. WebRTC media and the required UDP range cannot traverse the standard HTTP proxy. Use a TTL of 300 during installation. An `AAAA` record is optional and must not be created unless IPv6 is correctly routed to the node.

Verify from two independent resolvers:

```bash
dig +short A class.example.com @1.1.1.1
dig +short A class.example.com @8.8.8.8
```

The returned address must equal the server's public IPv4 before provisioning begins.

## Provider firewall

The provider-level firewall and the node firewall must permit:

| Protocol | Ports | Source |
|---|---:|---|
| TCP | 22 | Administration address where possible |
| TCP | 80, 443 | Any |
| UDP | 16384-32768 | Any |

Do not expose ports 8081, 9090, 3000, PostgreSQL, Redis, or Docker sockets publicly.

## Credential preparation

1. Generate a dedicated Ed25519 SSH key.
2. Install only the public key on the node.
3. Add the node host key to the workstation `known_hosts` file.
4. Obtain Telegram `api_id` and `api_hash` from the Telegram developer portal.
5. Create a private archive channel and grant the bot permission to post.
6. Generate the bridge secret with `openssl rand -hex 32`.
7. Keep the Telegram bot token, API hash, and server password available locally.

Never paste private keys, bot tokens, API hashes, shared secrets, or root passwords into issues, commits, screenshots, or chat messages.

The controller generates `BRIDGE_SHARED_SECRET` automatically. It builds the private environment payload in memory, uploads it with mode `0600`, and stores one local recovery copy under `~/.bbb-control-plane`. Private values are never part of the source tree.

## Controller workflow

1. Start `controller.py`.
2. Enter the server connection, hostname, WordPress, Telegram, and Greenlight administrator values directly in the controller.
3. Select `Preflight`.
4. Correct every failure.
5. Select `Provision`.
6. Keep the generated recovery file from the path reported by the controller.
7. Open Greenlight and its administrator panel from the Management tab.
8. Install the bridge ZIP in WordPress.
9. Copy the same bridge secret into Tools, Recording Transport.
10. Apply the minimal compatibility patch to the booking plugin.
11. Run a short meeting with presentation, screen share, audio, and webcam.
12. End the meeting and observe the recording queue from the Management tab.

## Non-destructive application upgrade

The bridge creates only `${table_prefix}gtbp_recording_transport`. It does not drop, truncate, rename, or rewrite existing tables. Existing manual video URLs remain authoritative. The bridge writes an automatic URL only when the current booking URL is empty and does not replace a session URL whose source is `manual`.

Create a database backup and a copy of the current plugin ZIP before installing any update.
