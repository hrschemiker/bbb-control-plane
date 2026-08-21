# Security

## Secret handling

- Keep `/etc/bbb-control-plane.env` mode `0600`.
- Keep Local Bot API bound to `127.0.0.1`.
- Do not store credentials in WordPress logs.
- Rotate the bridge secret on suspected disclosure.
- Rotate the bot token through the official bot-management interface on suspected disclosure.
- Do not commit generated environment files.

## SSH

- Use Ed25519 keys.
- Disable password authentication after validation.
- Restrict port 22 at the provider firewall when the administration source is stable.
- Preserve an out-of-band provider console before tightening SSH policy.

## Media authorization

The bridge checks application ownership before reusing a Telegram object reference. The archive channel is private. Telegram `protect_content` is enabled, but it cannot prevent screen capture or all client-side copying.

## Reporting

Do not publish recording identifiers, tokens, student data, server addresses, or diagnostic bundles in public issues. Redact secrets and personal data before sharing logs.
