# hermes-config-ui

Web UI to edit the `approvals:` block of the Hermes `config.yaml` on Asus-Hammer.

- Save on edit (800 ms debounce). Each save is one atomic write and one git commit in a
  private history repository (`~/.local/share/hermes-config-ui/history`).
- Undo, redo and restore write an old revision again as a new commit. History is never rewritten.
- A disabled deny rule is a comment line `  #  - <glob>`. Only the lines of the
  `approvals:` block change. All other bytes of the file stay the same.
- A save is refused (HTTP 409) if the block changed on disk after the page loaded.
- The host helper (`helper/`) restarts only the gateway, the WebUI and the dashboard
  units, and sends a Telegram message on each save and restart.

## Deploy

1. Install the helper:

   ```bash
   ln -sf ~/repo/tools/hermes-config-ui/helper/hermes-config-ui-helper.service ~/.config/systemd/user/
   systemctl --user daemon-reload && systemctl --user enable --now hermes-config-ui-helper
   ```

2. Start the container: `docker compose up -d --build`.
3. nginx-proxy must give basic auth and an IP allow-list for `hermes-config.privet.one`.
   The app refuses `/api/` requests that have no `Authorization: Basic` header.

## Test

```bash
docker run --rm -v $PWD:/src:ro -w /src hermes-config-ui python tests/test_block.py <copy-of-config.yaml>
```
