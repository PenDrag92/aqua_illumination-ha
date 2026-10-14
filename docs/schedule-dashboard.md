# Show the daily lighting program

Version 2026.10.2 adds a **Schedule Plan** sensor and a **Refresh Schedule**
button. Find their actual entity IDs on the Aqua Illumination device page;
Home Assistant may include the room name. Replace the example IDs below.

The program is a base curve. Check Schedule Status, the `modifiers` attribute
and the `last_read` timestamp before comparing it with live readings. In manual
mode the lamp is not following this curve. When a modifier is active, output
may differ. Power is calculated LED power for all detected lights, not measured
wall power. The integration does not change the schedule.

## Basic overview (built-in cards)

```yaml
type: entities
title: Aquarium lighting
entities:
  - entity: sensor.reef_schedule_status
  - entity: sensor.reef_schedule_plan
  - entity: button.reef_refresh_schedule
  - entity: sensor.reef_estimated_led_power
```

## Full day and future points (ApexCharts Card)

The optional [ApexCharts Card](https://github.com/RomRider/apexcharts-card),
available through HACS, can plot `channels` and `power_curve` using its documented
`data_generator`. A normal history graph only shows recorded past values.
Install that card separately if it is not already present.

This example shows all seven channels. It uses the browser's local date and
wall-clock time; **the browser and lamp must use the same time zone**. Using
`setHours` rather than adding milliseconds keeps clock labels correct across
daylight-saving transitions (the repeated autumn hour remains ambiguous).
The 24-hour graph span also needs adjusting on 23/25-hour DST transition days.

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Daily base program · lamp local time
graph_span: 24h
span:
  start: day
now:
  show: true
all_series_config:
  type: line
  curve: straight
  stroke_width: 2
  unit: "%"
  extend_to: false
  show:
    legend_value: false
series:
  - entity: sensor.reef_schedule_plan
    name: UV
    data_generator: |
      return (entity.attributes.channels?.uv || []).map(p => {
        const t = new Date(start);
        t.setHours(0, 0, p.minute * 60, 0);
        return [t.getTime(), p.brightness];
      });
  - entity: sensor.reef_schedule_plan
    name: Violet
    data_generator: |
      return (entity.attributes.channels?.violet || []).map(p => {
        const t = new Date(start);
        t.setHours(0, 0, p.minute * 60, 0);
        return [t.getTime(), p.brightness];
      });
  - entity: sensor.reef_schedule_plan
    name: Blue
    data_generator: |
      return (entity.attributes.channels?.blue || []).map(p => {
        const t = new Date(start);
        t.setHours(0, 0, p.minute * 60, 0);
        return [t.getTime(), p.brightness];
      });
  - entity: sensor.reef_schedule_plan
    name: Royal blue
    data_generator: |
      return (entity.attributes.channels?.royal || []).map(p => {
        const t = new Date(start);
        t.setHours(0, 0, p.minute * 60, 0);
        return [t.getTime(), p.brightness];
      });
  - entity: sensor.reef_schedule_plan
    name: Cool white
    data_generator: |
      return (entity.attributes.channels?.cool_white || []).map(p => {
        const t = new Date(start);
        t.setHours(0, 0, p.minute * 60, 0);
        return [t.getTime(), p.brightness];
      });
  - entity: sensor.reef_schedule_plan
    name: Green
    data_generator: |
      return (entity.attributes.channels?.green || []).map(p => {
        const t = new Date(start);
        t.setHours(0, 0, p.minute * 60, 0);
        return [t.getTime(), p.brightness];
      });
  - entity: sensor.reef_schedule_plan
    name: Deep red
    data_generator: |
      return (entity.attributes.channels?.deep_red || []).map(p => {
        const t = new Date(start);
        t.setHours(0, 0, p.minute * 60, 0);
        return [t.getTime(), p.brightness];
      });
```

For a second card with planned and actual estimated power:

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Estimated LED power · parent and linked lights
graph_span: 24h
span:
  start: day
now:
  show: true
series:
  - entity: sensor.reef_schedule_plan
    name: Base program
    unit: W
    curve: straight
    extend_to: false
    show:
      legend_value: false
    data_generator: |
      return (entity.attributes.power_curve || []).map(p => {
        const t = new Date(start);
        t.setHours(0, 0, p.minute * 60, 0);
        return [t.getTime(), p.power];
      });
  - entity: sensor.reef_estimated_led_power
    name: Actual reported channels (calculated watts)
    unit: W
    curve: stepline
    extend_to: false
    fill_raw: "null"
```

The actual power sensor can hold its last reading during short Wi-Fi gaps.
Its `data_stale` attribute and Connection Status identify those periods.
Plan changes are displayed after the next successful plan refresh; old plans
are not archived by this feature. A comparison earlier in the day therefore
uses the current saved program, which may have been edited since then.
