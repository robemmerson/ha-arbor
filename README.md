# Arbor School Integration for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square)](https://hacs.xyz/)
[![Validate](https://github.com/robemmerson/ha-arbor/actions/workflows/validate.yml/badge.svg)](https://github.com/robemmerson/ha-arbor/actions/workflows/validate.yml)
[![CodeQL](https://github.com/robemmerson/ha-arbor/actions/workflows/codeql.yml/badge.svg)](https://github.com/robemmerson/ha-arbor/actions/workflows/codeql.yml)

A custom Home Assistant integration for the [Arbor](https://arbor.sc) school management system. Surfaces your children's attendance, behaviour points, assignments, and daily timetable as native HA entities.

## Features

**Per child, the integration creates:**

| Entity | Type | Description |
|--------|------|-------------|
| Attendance (Year) | Sensor | Year-to-date attendance percentage |
| Attendance (Last 4 Weeks) | Sensor | Rolling 4-week attendance percentage |
| Positive Points (Current Term) | Sensor | Behaviour points this term |
| Positive Points (Previous Term) | Sensor | Behaviour points last term |
| Positive Points (Year) | Sensor | Behaviour points year total |
| Negative Points (Current Term) | Sensor | Negative incidents this term |
| Negative Points (Previous Term) | Sensor | Negative incidents last term |
| Negative Points (Year) | Sensor | Negative incidents year total |
| Assignments Due | Sensor | Count of assignments due |
| Assignments Overdue | Sensor | Count of overdue assignments |
| Assignments Submitted | Sensor | Count of submitted assignments |
| Assignments Due List | Sensor | Full list of due assignments (in attributes) |
| Assignments Overdue List | Sensor | Full list of overdue assignments (in attributes) |
| Assignments Submitted List | Sensor | Full list of submitted assignments (in attributes) |
| Timetable | Sensor | Today's lesson count with full schedule in attributes |
| School Timetable | Calendar | Native HA calendar entity with lessons as events |

All entities are grouped under a device per child (e.g. "Arbor - Lauren Emmerson").

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu → **Custom repositories**
3. Add this repository URL with category **Integration**
4. Search for "Arbor School" and install
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/arbor` folder into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for **Arbor School**
3. Enter your Arbor parent portal email and password
4. The integration will discover your children and school automatically

## How It Works

The integration authenticates via Arbor's OAuth2 flow (the same one used by the Arbor parent mobile app), then polls for updated data every 15 minutes. Tokens are refreshed automatically before they expire. If a refresh token becomes invalid, the integration will attempt a full re-authentication using your stored credentials.

### Polling Intervals

- **Sensors** (KPIs, assignments, timetable): Every 15 minutes
- **Calendar** (when viewing date ranges): On demand

You can adjust the default polling interval in `const.py` by changing `DEFAULT_SCAN_INTERVAL`.

## Dashboard Examples

### Assignment card using Markdown

```yaml
type: markdown
title: Assignments Due
content: >
  {% set items = state_attr('sensor.arbor_lauren_emmerson_assignments_due_list', 'assignments') %}
  {% if items %}
    {% for a in items %}
  - **{{ a.title }}** — Due {{ a.due_date }}
    {% endfor %}
  {% else %}
  No assignments due!
  {% endif %}
```

### Timetable card using Markdown

```yaml
type: markdown
title: Today's Timetable
content: >
  {% set lessons = state_attr('sensor.arbor_lauren_emmerson_timetable', 'lessons') %}
  {% if lessons %}
    {% for l in lessons %}
  - {{ l.start[11:16] }}–{{ l.end[11:16] }} **{{ l.subject }}** ({{ l.location }})
    {% endfor %}
  {% else %}
  No lessons today.
  {% endif %}
```

### Automation: notify on overdue assignments

```yaml
automation:
  - alias: "Notify overdue assignments"
    trigger:
      - platform: numeric_state
        entity_id: sensor.arbor_lauren_emmerson_assignments_overdue
        above: 0
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Overdue Assignments"
          message: >
            {{ state_attr('sensor.arbor_lauren_emmerson_assignments_overdue_list', 'assignments')
               | map(attribute='title') | join(', ') }}
```

## Privacy

This integration communicates directly with Arbor's servers from your Home Assistant instance. No data is sent to any third party. Your credentials are stored in Home Assistant's config entry storage (encrypted at rest if you use the default HA secrets management).

## License

MIT
