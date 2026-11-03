# Contributing (short version)

Small, honest PRs welcome. This repo values traceability over features.

- One change per PR; keep it minimal — no drive-by refactors.
- Never commit: API keys, `.env`, downloaded videos, run outputs with
  personal paths, or internal notes. Check `git status` before every commit.
- Mock stays mock: anything from `--provider mock` must keep its MOCK
  label; never present fixture output as model output.
- New behavior needs a test in `app/tests/` (`python3 -B -m unittest
  discover -s tests` from `app/` must stay green).
- Docs live with code: update `app/README.md` when behavior changes.
- Be honest about boundaries: a failed run is a finding, not a failure —
  report it as-is instead of re-running until it passes.
