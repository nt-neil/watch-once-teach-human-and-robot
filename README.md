# Watch Once, Teach Human and Robot (Meta Hackathon #6)

Teaching video → 📘 manual for humans + 🤖 JSON for robots.
Built during the Meta hackathon; demo AI is all Meta (Muse Spark, Muse Code).

## What this repo contains (built during the event)

- `app/`: video CLI — frame extraction, mock + Meta Muse Spark providers,
  5-field operation JSON (`object/action/target/pre/done`) validation,
  Markdown manual, and Navide task payload export (+ optional MCP adapter).
- `app/desktop-demo/`: isolated local TODO page + loopback server + recording
  script used for the computer-operation reproduce demo.
- `app/fixtures/`, `app/tests/`: self-made synthetic fixtures and unit tests.
- Full component doc: `app/README.md`.

Pre-existing external tools (not built here): Navide, Muse CLI/Muse Code,
Meta models and services, FFmpeg/ffprobe, MCP SDK, Python deps.

## Verified boundaries (read before reusing)

- Only the M1 single demo is verified 4/4 at baseline; time precision is
  claimed from that clip only. W1-class continuous fine motion (identity
  tracking) and L3-class adjacent-step merging are documented limitations;
  granularity hints split steps but induce even time-splitting, so L3 timing
  is not claimed. See the demo video for the honest-boundaries segment.
- Physical assembly is never executed (no robot arm). The closed loop covers
  software artifacts only (assembly checklist); physical todos stay pending.
- Navide plans were created via pane-relayed MCP; the standalone adapter has
  never been live-verified. Observed API spend ≈ US$0.22 + US$0.60 reserves
  as of 2026-11-03 (usage-rate estimates, not bill reconciliation).

## Quickstart (no API key)

```sh
cd '<repo>/app'
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
python3 fixtures/make_synthetic.py
python3 cli.py runs/synthetic-fixture.mp4 --workspace-path '<workspace>'
```

Paid Meta runs need `--provider meta --confirm-paid-api` and `META_API_KEY`
from your own env file (never commit it). Details in `app/README.md`.

## Sources and attribution

- M1: "AIST 0001" from the AIST Bimanual Manipulation Dataset —
  Tomohiro Motoda, Masaki Murooka, Ryoichi Nakajo, Muhammad A. Muttaqien,
  Koshi Makihara, Hanbit Oh, Keisuke Shirai, Floris Erich, Ryo Hanai,
  Yukiyasu Domae; © 2025 AIST; CC BY 4.0
  (https://creativecommons.org/licenses/by/4.0/);
  source https://aistairc.github.io/aist_bimanip_site/ ;
  clip used uncut, no endorsement implied.
- W1: Jiayun Zhang, Petr Byvshev, Yu Xiao (2020),
  "A video dataset for wooden box assembly",
  https://doi.org/10.5281/zenodo.4047982 , CC BY 4.0; excerpt 125–172s of
  the original, re-encoded without audio; no endorsement implied.
- L3/L4: Elina Mäntylä, Teemu O. Ihalainen (2021), Brick Strex,
  https://doi.org/10.1038/s41598-021-97900-5 , CC BY 4.0; video photography
  credit Jukka Lehtiniemi, Markus Hannula; excerpts only, no speed claims.
- Category 4: self-recorded screen demo, no third-party license involved.

## Execution attribution

- Model inference: 100% Meta Muse Spark via api.meta.ai. One MiniMax video
  read was made during development, discarded, and never entered any GT,
  score, product, or deliverable.
- Produced by Muse Code: M1 checklist + execution report, bridge design,
  demo storyboard, recording script, this repo's staging and top docs, repo
  creation, the M1 dismantle re-run, and all independent acceptance checks.
- Executed in Codex panes (pre-handover and crew work): app CLI skeleton and
  tests, all data-collection runs and scoring reports, materials survey, and
  Navide plan operations.

## License

MIT — see `LICENSE`.
