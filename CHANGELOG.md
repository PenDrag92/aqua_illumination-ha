# Changes

## 2026.10.1

- Poll every 30 seconds by default; allow 10–60 seconds in the integration options.
- Retry a transient read once, then back off on repeated failed cycles to at most 120 seconds.
- Keep clearly marked cached sensor readings for at most 120 seconds after the last successful read, with independent expiry.
- Show Connected, Reconnecting or Disconnected and a last-successful-update timestamp.
- Keep identity, firmware and invalid-response failures immediately visible; never retry writes or hide uncertain command results.
- Disable optional controls while readings are stale.
- Expose detected parent/child counts from the same snapshot as the estimated power.
- Add redacted connection diagnostics and simulated outage/recovery/lifecycle tests.
- Preserve saved IP addresses, existing entity IDs and enabled controls on update.

The new outage handling is covered by simulated devices; validation on the target lamps follows installation.

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
