# WatchOnce Web design notes (v3, 11/3 — round-4 feedback)

Direction: lab instrument with an identity. Teal accent + IBM Plex (offline
WOFF2 in web/static/fonts, OFL license), text-first screenshots.
Hero = value line + real sample frame; Upload/Example option cards;
result opens on a clickable step timeline, provenance folded.

Round-4 fixes: brand palette (page #f1f3f1, ink #1a2420, accent #0b5c4c,
tint #e3efe9, all pairs >= 4.5 in check_contrast.py); overflow-x:clip
guard; result left column = timeline first, provenance collapsed
(attribution kept), full manual below; META/MOCK + steps badges shown
once; manual h1 mapped to English labels, `## step-NN` headings syncable;
timeline/card/manual three-way click sync; "Try M1 example" one-click.
/static/ serves only woff2/jpg/png, jailed; CSP adds font-src 'self'.

Type: "Plex Sans" 400/600/700 + "Plex Mono" 400, font-display:swap,
system-ui fallback. Scale 38 hero / 19 / 16 / 15 body / 13 mono.

Motion: 120ms hovers + smooth scroll sync, all off under
prefers-reduced-motion.

Round-6 fixes: hero left CTA "Try an example" (jumps to #pick, preselects
m1), hero shot capped 340px, pipe margins tightened; timeline = thumb +
time + one plain sentence (done text, 110 chars), structured fields only
in right cards; meta line keeps video name only (provider left to badge).

Round-7 fixes: error pages gain viewport meta + explanation + "Back to
examples" CTA (.errpage max 660px); CSP adds `connect-src 'self'` — without
it the browser blocked all fetch/XHR including same-origin (job polling and
console fetch), while curl/urllib ignored CSP. Covered by
tests/test_web_regression.py (socketpair, no bind, no spawn).

Round-8: home shows "Meta API key: configured / not configured" (status only,
never the value) with setup hints when unset; server reads app/.env as
fallback (.env.example shipped, .env gitignored). No web key-input field by
design (key must not enter browser/form traffic).

---
# WatchOnce Web design notes (v2, 11/3 — round-2 feedback)

Direction: plain lab instrument, not a landing page. Dense information,
flat surfaces, one accent color. No gradients, no glow, no emoji icons.
Product hero added per review (pipeline strip + value line, CSS-only).

Round-2 fixes: grid children min-width:0 (kills 12kpx overflow) + card
min-width:0; long hashes wrap; styled file/select/checkbox; step cards
with frame thumb + expandable JSON + copy; manual↔card click sync;
120ms micro-transitions, all disabled under prefers-reduced-motion.
Internal subtitle replaced with product language.

- Type: system-ui stack only (offline by definition). Scale 26 / 19 / 16 /
  15 body / 13 mono. One family, weight contrast (400 / 600 / 700).
- Palette (all pairs measured in check_contrast.py, ≥ 4.5 for text):
  page #f2f4f7, surface #ffffff, line #d8dee6, ink #1a2433,
  secondary #3d4753, accent #16508e (white text), ok #166534,
  warn #8a4d00, err #b42318.
- Rhythm: 8px base spacing; cards 16/18px padding; max width 1180px.
- Radius: 6px cards, 4px controls (deliberately not one radius).
- States: focus-visible 3px outline; disabled button gray; empty history
  sentence; job page polls with text log; errors render as full pages.
- Responsive: two-column grid collapses under 900px; tables scroll
  horizontally inside cards under 640px; inputs full-width on narrow.
- Motion: none (no animation to disable; `prefers-reduced-motion` respected
  trivially).
- Privacy: server strips absolute paths/keys before render; verified by tests.
