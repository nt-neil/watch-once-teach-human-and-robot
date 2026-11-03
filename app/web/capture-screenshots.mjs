// Design-review capture: 3 pages x 2 widths. Run OUTSIDE the agent sandbox
// (it cannot launch browsers), on the recording machine:
//   1. cd app/web && python3 server.py --port 18766
//   2. ego-browser nodejs < app/web/capture-screenshots.mjs
// Shots land in app/web/design-review/round-2/ for Muse's own-vision review.
const ROUND = process.argv[2] || "round-3";
const OUT = new URL(`design-review/${ROUND}/`, import.meta.url).pathname;
const BASE = "http://127.0.0.1:18766";

const fs = await import("node:fs");
fs.mkdirSync(OUT, { recursive: true });

const task = await taskSpace("watchonce-design-review");
const pages = [
  ["home", "/"],
  ["result", "/result/real-smoke"],
  ["checklist", "/result/m1-checklist-loop-1030-r2"],
];
for (const [w, vp] of [["1280", { width: 1280, height: 720 }], ["390", { width: 390, height: 700 }]]) {
  const page = task.page("p-" + w);
  await page.setViewport(vp);
  for (const [name, path] of pages) {
    await page.goto(BASE + path);
    await page.screenshot({ path: `${OUT}/${name}-${w}.png` });
  }
}
console.log(JSON.stringify({ out: OUT, shots: 6 }));
