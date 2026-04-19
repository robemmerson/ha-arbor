# Arbor School

Home Assistant integration for the [Arbor](https://arbor.sc) school management
system. Surfaces your children's attendance, behaviour points, assignments,
and daily timetable as native HA entities.

## What you get, per child

- Attendance sensors (year-to-date, last 4 weeks)
- Positive and negative behaviour points (current term, previous term, year)
- Assignment counts and full lists (due, overdue, submitted) — including a
  native todo list entity
- Today's timetable as a sensor plus a full calendar entity

Entities are grouped under one device per child.

## Setup

1. Install via HACS, restart Home Assistant.
2. **Settings → Devices & Services → Add Integration → Arbor School**.
3. Enter your Arbor parent portal email and password. The integration
   discovers your children and school automatically.

Data refreshes every 15 minutes. Tokens are refreshed automatically.

See the [README](https://github.com/robemmerson/ha-arbor/blob/main/README.md)
for dashboard examples and automation recipes.
