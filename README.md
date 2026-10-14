# Aqua Illumination for Home Assistant

A maintained fork of [razza-t/aqua_illumination-ha](https://github.com/razza-t/aqua_illumination-ha)
(version 2026.4.0), targeting the older **AI 26HD Wi-Fi lights used on Red Sea aquariums**.

## Read-only first

New installations start with **sensors only**. Updates of this fork preserve the
saved IP address, name and manual-control option. Setup, polling,
reconnection, diagnostics and unloading do not change brightness or the internal
lighting schedule.

Available sensors:

- Each color channel's brightness percentage, including HD values above 100%.
- Internal schedule status (Scheduled / Manual).
- Firmware version and connection status.
- Last successful update and the detected number of lights (parent + children).
- **Estimated LED power** in watts, including linked lights.

The power value is calculated from channel intensities and the lamps' own
power tables. It is not measured mains power, excludes unmodelled losses and
standby consumption, and is not an accumulated kWh energy counter. The power
sensor also shows `detected_lights` and `child_lights`; no manual count is needed.
Failed reads never produce a substitute zero-watt reading.

## Temporary Wi-Fi outages

The normal polling interval is **30 seconds**, configurable from 10 to 60 seconds
in the integration options. A transient read failure gets one retry after two
seconds within the same 30-second polling deadline. Repeated failed cycles use
longer intervals (30, 60, then at most 120 seconds with the defaults) and stop
making the extra retry. A successful read restores the configured interval.

For transient transport failures only, sensors retain their last complete
snapshot for **at most 120 seconds from its successful read**. During this grace
period, Connection Status shows **Reconnecting**, the sensors carry
`data_stale: true` and `last_successful_update`, and the Last Successful Update
sensor keeps its original timestamp. These are held readings, not measurements
of what happened during the outage; downstream energy estimates may therefore
be approximate. Automations that need a fresh value should check `data_stale`.

An independent timer makes readings unavailable when the grace period expires,
even while a slow request or backoff is pending. Connection Status then shows
**Disconnected** and stays visible. A valid response restores **Connected**.
Invalid replies, unsupported firmware and a different lamp at the saved IP cause
immediate unavailability. Optional light and switch controls are unavailable
while readings are stale.

Commands are never automatically repeated. A missing write response or failed
confirmation read makes readings unavailable immediately, because the command
may already have reached the lamp. Reconnection only reads the lamp; it does not
replay a brightness setting or toggle its internal schedule.

## Compatibility

- Intended target: older Hydra 26HD Wi-Fi fixtures, including those supplied
  with Red Sea aquariums. The light must expose the local HTTP API.
- Only add the **parent** IP when lamps are paired; children are included through it.
- The existing firmware support range **2.0.0–2.5.1** is retained. Other versions
  are rejected with a clear message, rather than assumed compatible.
- Bluetooth-only Hydra 32HD/64HD and Prime 16HD, and Red Sea ReefLED fixtures,
  are not supported by this Wi-Fi integration.
- Home Assistant 2026.3+ / Python 3.14+. Automated HA tests are pinned to
  2026.3.1 and 2026.10.1, with an additional latest-version job on each change.
  Passing tests does not replace validation on the
  target lamps; the protocol fixtures are synthetic.

## Installation

In HACS, add **https://github.com/PenDrag92/aqua_illumination-ha** as a custom
repository of category **Integration**, download Aqua Illumination and restart
Home Assistant.

For manual installation, copy `custom_components/aqua_illumination` into
`/config/custom_components/`, then restart Home Assistant.

Add **Aqua Illumination** under Settings → Devices & services. Enter the parent
lamp's IP address and a name. There is no cloud login.

## Changing an IP address

Open the integration's options, enter the new IP address, and save. The
integration checks that the same lamp responds before saving and reloading.
Use a DHCP reservation to keep addresses stable.

## Optional manual control

Keep **Enable manual control** off for the first hardware validation.
Only after the automated tests pass and read-only readings have been checked
should you explicitly enable it in the integration options.

Enabling the option adds per-channel light entities and a schedule switch.
It does not itself send a lighting command. Color commands change only the
requested channel; other channels, including their HD values, are preserved.
Writes are serialized and checked against every paired lamp's power limit.

Home Assistant light sliders control 0–100%; their displayed brightness is
clamped to HA's range. The brightness sensors retain HD percentages above 100%.
The internal schedule is changed only through its dedicated switch.

On upgrade from upstream, existing entity unique IDs are retained. Previous
light/switch entities remain unavailable until manual control is explicitly
enabled; automations targeting them need that option. No automatic migration
switches the lamps into manual mode.

## Diagnostics and debugging

Use **Download diagnostics** on the integration's menu. This exports the cached
snapshot and update status, with IP addresses and the configured name redacted;
device identifiers are omitted. Connection diagnostics include retry and failed
update counts since integration startup, the current polling interval and the
last read error's type, endpoint and time.

Home Assistant's **Enable debug logging** action captures integration/coordinator
activity. Disable it again after reproducing a problem. The integration logs
request failures without dumping raw lamp responses.

## Development and tests

On Linux with Python 3.14:

```sh
python -m pip install -r requirements-test.txt
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
```

The protocol-only tests also run on Python 3.12 with `aiohttp`, `pytest` and
`pytest-asyncio` installed: `python -m pytest -q tests/unit`.

Tests cover malformed replies, disconnects, timeouts, firmware rejection,
parent/child power limits, HD conversions, read-only lifecycle, options changes,
unloading and optional concurrent commands. They never contact real lights.
GitHub Actions also runs hassfest and HACS validation.

## Credits and license

Original integration by **zonyl** and **mcclown**, AquaIPy by **mcclown** and
**chkuendig**, and the 2026 upstream modernization by **razza-t**. The bundled
AquaIPy code retains its original Apache-2.0 license; see the repository licenses.
This integration is not affiliated with AquaIllumination or Red Sea.
