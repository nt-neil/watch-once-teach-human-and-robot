# Export layer (added 11/3)

Reads validated `operations.json` and emits robot/VLA-friendly annotation
files. Read-only: core schema and validation are untouched.

## What it is

Format-aligned, input-ready annotation data for future models or robots.
It is NOT a robot run: no states, actions, sensor data, videos, or
normalization stats are produced. The LeRobot output covers only the
episode/task annotation layer (`meta/episodes` + `meta/tasks` equivalent);
`data/*.parquet`, `videos/*`, and `meta/stats.json` are explicitly absent
(`robot_state_action_data: null`). Nothing here has run on a real robot or VLA.

## Formats

1. `*-lerobot.json` — LeRobot v3-style episode annotation (task + subtasks
   with instruction, closed-set primitive, object, time, source frames).
2. `*-vla.json` — one short instruction per step with timestamps.
3. `*-plan.json` — planning steps with preconditions/effects for behavior
   trees or PDDL-style executors (strings must be grounded by the executor).

## Mapping honesty

`action_primitive` comes from ordered keyword rules (`RULES` in
`exporter.py`); anything unmatched is `other`, never guessed. `instruction`
is a rule-based template (`*_origin` fields say so). Five-field strings,
timestamps, and frame refs are copied verbatim from the validated source.
