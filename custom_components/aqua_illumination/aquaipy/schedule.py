"""Validate and describe the daily base ramp without executing a preview."""

import math
from bisect import bisect_right


def _number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def parse_ramps(data, channels):
    """Accept complete, ordered ramps anchored at midnight, as on Wi-Fi 26HD."""
    if not isinstance(data, dict):
        raise ValueError("Invalid schedule")
    if not isinstance(data.get("name"), str) or len(data["name"]) > 255:
        raise ValueError("Invalid schedule name")
    ramps = data.get("ramps")
    if not isinstance(ramps, list) or len(ramps) != len(channels):
        raise ValueError("Missing schedule channels")
    result = {}
    for ramp in ramps:
        if not isinstance(ramp, dict):
            raise ValueError("Invalid ramp")
        color = ramp.get("color")
        if not isinstance(color, str) or color not in channels or color in result:
            raise ValueError("Unknown or duplicate schedule channel")
        points = ramp.get("points")
        if not isinstance(points, list) or len(points) > 1440:
            raise ValueError("Invalid schedule points")
        clean = []
        previous = -1
        for point in points:
            if not isinstance(point, dict):
                raise ValueError("Invalid schedule point")
            minute, intensity = point.get("time"), point.get("intensity")
            if (
                not isinstance(minute, int)
                or isinstance(minute, bool)
                or not previous < minute < 1440
                or not _number(intensity)
                or not 0 <= intensity <= 2000
            ):
                raise ValueError("Invalid schedule time or intensity")
            clean.append((minute, intensity))
            previous = minute
        # Empty channels are off. Nonempty ramps in the observed protocol start
        # at 00:00; reject other shapes instead of guessing their midnight value.
        if clean and clean[0][0] != 0:
            raise ValueError("Schedule must start at midnight")
        if not clean:
            clean = [(0, 0)]
        clean.append((1440, clean[0][1]))
        result[color] = clean
    return result


def intensity_at(points, minute):
    """Interpolate the base curve in raw intensity space, including midnight."""
    if not _number(minute) or not 0 <= minute <= 1440:
        raise ValueError("Minute must be in the displayed day")
    index = min(bisect_right(points, (minute, math.inf)) - 1, len(points) - 2)
    start, end = points[index], points[index + 1]
    fraction = (minute - start[0]) / (end[0] - start[0])
    return start[1] + fraction * (end[1] - start[1])


def describe_schedule(data, primary, calculate_power, device_count):
    """Export chart points in percent and estimated watts for all linked lights."""
    ramps = parse_ramps(data, primary._mw_norm)
    channels = {}
    all_minutes = set()
    for color, points in ramps.items():
        minutes = {point[0] for point in points}
        # Percentage and power have a different slope above raw intensity 1000.
        # Include that corner so a chart never interpolates straight across it.
        for start, end in zip(points, points[1:]):
            if min(start[1], end[1]) < 1000 < max(start[1], end[1]):
                minutes.add(
                    start[0]
                    + (1000 - start[1]) * (end[0] - start[0]) / (end[1] - start[1])
                )
        channels[color] = [
            {
                "minute": round(minute, 6),
                "brightness": round(
                    primary.convert_to_percentage(color, intensity_at(points, minute)),
                    4,
                ),
            }
            for minute in sorted(minutes)
        ]
        all_minutes.update(minutes)
    return {
        "name": data["name"],
        "channels": channels,
        "power_curve": [
            {
                "minute": round(minute, 6),
                "power": calculate_power(
                    {
                        color: intensity_at(points, minute)
                        for color, points in ramps.items()
                    }
                ),
            }
            for minute in sorted(all_minutes)
        ],
        "device_count": device_count,
        "base_schedule": True,
    }
