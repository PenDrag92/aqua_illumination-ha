# Changes

## 2026.10.0

Based on razza-t/aqua_illumination-ha 2026.4.0, commit 926839b.

- Start in read-only mode, including on upgrade; controls require explicit opt-in.
- Use Home Assistant's shared HTTP session and a single DataUpdateCoordinator.
- Add bounded requests, initial setup retries, disconnect/reconnect handling and identity checks.
- Fix the options flow, validate IP changes and reload the integration.
- Preserve existing unique IDs, read HD percentages without clipping, and serialize optional writes.
- Check command responses and power limits for the parent and every linked light.
- Calculate estimated LED power from the same channel snapshot; never substitute zero after a failed read.
- Add a read-only schedule sensor and redacted downloadable diagnostics.
- Add offline protocol tests and Home Assistant lifecycle tests in GitHub Actions.

Hardware validation is still required on the target 26HD lamps. Automated tests use simulated devices.
