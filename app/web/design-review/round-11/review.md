# Design review round-9 (spot review; round-10/11 are re-capture confirmations)

Date: server PID 46276 with key loaded. Viewed by Muse Code (built-in
vision only): home-1280, result-synthetic-1280, result-synthetic-fullmanual-1280,
result-m1-bottom-1280. Operator measured all scrollWidth == innerWidth.

## Verdict: all three fixes verified

- M1 預設：home 下拉顯示 M1 AIST washers 預設選中。OK。
- TEST PATTERN：synthetic 4 縮圖（時間軸 2＋卡片 2）皆掛牌；M1 無。OK。
- Full manual H1：字級正常且長標題換行（round-8 起的 .manual h1–h3 上限）。OK。
- 底部條帶：result-m1-bottom 只有 Navide 卡＋乾淨留白，無條帶。同意指揮判斷
  （macOS 截圖視窗邊緣／Dock 模糊），結案。

Round-10 (16:54) / round-11 (16:59): same 8 shots re-captured; byte-compared
against round-9 — home-1280/390 and result-synthetic-1280 identical, others
differ only by re-render/scroll noise. No new findings; report keeps round-9
bytes. No review needed for 10/11 beyond this note.

Remaining visual evidence gaps (code-verified, screenshots pending):
key-status line below the fold (unit-tested), MCP terminal post-v2 grammar
(free re-run).
