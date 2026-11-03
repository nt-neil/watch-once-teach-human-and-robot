# Meta #6：影片 → 手冊／操作概念 JSON → Navide 待辦

本目錄是比賽期間新做的第二階段 CLI 骨架，對齊核准計畫上方當前 Goals：同一步產出人可讀手冊與 `object/action/target/pre/done` 五個字串，再轉成 Navide 任務。Navide 本體既有的派工／執行／驗收不是本次新作。沒有沿用第 423 行起舊附錄的固定角色或多套 schema。

這份 README 記錄本地 CLI 骨架與 M1 smoke 的驗收狀態。這版提供本地 MP4 檢查與抽幀、明示 mock fixture、操作概念驗證、Markdown 手冊、`plan_create` payload，以及可選的真 Meta／MCP adapter。web UI 已於 11/3 後補完成（見下方「Web UI added 11/3」段）；SAM 追蹤、Muse Image 重畫、agent 自動執行與執行紀錄回流仍不列為本骨架已完成項目。所有任務維持 `pending`；由外層人工判斷實體／軟體步驟、執行權限與驗收，不能用此骨架宣稱 Agentic 閉環已完成。

## 比賽期間新做的部分

- Python 影片 CLI：呼叫 ffprobe／ffmpeg 檢查原片及抽幀，保存 SHA、來源時長、decoder frame／PTS 與抽樣時間對照。
- Mock／真 Spark provider 接口與五欄操作 JSON 驗證：真模式上傳原 MP4，檢查回覆狀態、schema、時間順序及來源引用；mock 明示為手寫 fixture。
- 從同一份 JSON 產生帶來源圖片、時間與五欄內容的 Markdown 人類手冊。
- 操作 JSON → Navide pending 任務 payload，以及可選的 MCP adapter 程式。M1 真建單證據來自 root pane 代送並回讀 4 個 pending 待辦；獨立 adapter 尚未實連，不能列為已通過真連線驗收。

## 既有外部系統／資料

Navide 本體、Muse CLI／Muse Code、Meta 模型與服務（包括 Muse Spark）、FFmpeg／ffprobe、官方 MCP SDK 及其他 Python 依賴都是既有外部工具；本次新作是上述串接程式與產物。M1 的 AIST 來源影片與資料集也不是本專案製作。規劃中的 SAM、Muse Image、agent 執行／驗收與回流，不因列入產品構想就成為已完成功能。

## M1 來源、署名與實際變更

「AIST 0001 雙墊圈上軸 — AIST Bimanual Manipulation Dataset」，作者 Tomohiro Motoda、Masaki Murooka、Ryoichi Nakajo、Muhammad A. Muttaqien、Koshi Makihara、Hanbit Oh、Keisuke Shirai、Floris Erich、Ryo Hanai、Yukiyasu Domae；© 2025 AIST。依 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 使用。[資料集／授權來源](https://aistairc.github.io/aist_bimanip_site/)；[原始 MP4](https://aistairc.github.io/aist_bimanip_site/statics/video/0001_task_video.mp4)。不表示作者或 AIST 背書本作品。

本次 `real-smoke` 使用原始 14 秒 MP4，完整上傳，沒有截取時段、重編碼原影片或變速。手冊圖片實際做了抽幀、縮放至最寬 640 px、另存 JPEG；這些圖片不是作者提供的獨立完成證據。素材清單另有準備好的 `local_clip` 與其「僅轉碼」說明，不能套用為本次 `local_original` 輸入的處理紀錄。

署名與處理差異可追溯至 素材清單（未公開） 的 `selected[id=M1]` 及[真手冊來源段](evidence/real-smoke/manual.md)。README 保留完整作者／授權資訊；模型輸出內容及既有 run 不因這次文件補充而改動。

## 安裝

Python >=3.11，FFmpeg/ffprobe 8.x。開發機確認有 Python 3.14.7、FFmpeg/ffprobe 8.0.1、jsonschema 4.26.0、httpx 0.28.1；MCP 是選裝。本次 mock 使用現有依賴，未修改全域 Python。

```sh
cd watch-once-teach-human-and-robot/app
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# 本機沒有 ffmpeg/ffprobe 才安裝：
brew install ffmpeg
```

以下指令可把 `python3` 換成 `.venv/bin/python`。不需要 API key 就能跑完整 mock 鏈。

## 可重跑的本地 mock

先產生一次自製 3 秒測試色卡＋440 Hz 合成音訊（無真人旁白、沒有外部素材）：

```sh
python3 fixtures/make_synthetic.py
```

若檔案已存在，產生器拒絕覆蓋；直接重用 `runs/synthetic-fixture.mp4`。每次 CLI 預設產生新的 `runs/<UTC timestamp>/`，可重跑且不覆蓋其他結果：

```sh
python3 cli.py runs/synthetic-fixture.mp4 \
  --workspace-path <workspace-root>
```

需要指定路徑時加 `--out runs/my-new-run`（必須尚不存在且在 app/ 內）。預設 `--provider mock` 讀取 `fixtures/synthetic-response.json` 的兩個手寫示例步驟，**沒有辨識影片／旁白**，也不宣稱任意影片都能拆成固定步數。換片或抽樣率時須提供匹配的新 fixture：

```sh
python3 cli.py /absolute/path/authorized.mp4 \
  --provider mock --mock-response /absolute/path/custom-response.json \
  --workspace-path <workspace-root>
```

自訂 fixture 只包含 `{"steps": [...]}`；每一步形狀見 `operation.schema.json` 與內建 fixture。source 由本地抽幀程序填入，fixture 不能更改 SHA 或隱藏 mock 標記。選擇 mock 時，每份 JSON／手冊／待辦及抽幀檔名都可辨識 mock；frame 圖片本身仍是來源像素，沒有造假標註。

## 封套與追溯契約

核准 Goals 只定義 concept 的五欄；以下 **新增封套是骨架實作契約**，不是聲稱計畫已核准額外機器人指令格式：

`object/action/target/pre/done` 是文字觀察、條件及待人工確認的判斷；schema 通過不保證物理操作已完成、裝配品質或設備／人員安全。`pending` 任務不代表 agent 已執行；source frames 用於定位來源，不是完成證據。

```json
{
  "schema_version": "1.0",
  "source": {
    "video_sha256": "<原始 MP4 的 64 位 SHA256>",
    "video_name": "synthetic-fixture.mp4",
    "duration_ms": 3000,
    "provider": "mock",
    "mock": true,
    "frame_manifest": "frames.json"
  },
  "steps": [{
    "id": "observe",
    "start_ms": 0,
    "end_ms": 1000,
    "concept": {"object": "色卡", "action": "查看", "target": "畫面", "pre": "已播放", "done": "已檢視"},
    "source_frame_refs": ["frame-000001"]
  }]
}
```

正式 schema：`operation.schema.json`。每個 object 都 `additionalProperties: false`、所有 properties 皆 required；沒有 oneOf/allOf/recursive。concept 原樣維持五個字串。驗證會拒絕空步驟、錯誤型別、多餘欄位、空白字串、重複 id、負／越界時間、反向／重疊／未排序區間、缺少或錯誤 frame 引用，以及圖片缺失／雜湊改變。source 必須完全等於本地 manifest；步驟引用時間必須位於自己的 `[start_ms,end_ms)`。

輸入僅接受本地 `.mp4`，ffprobe 查到視頻且來源／串流時長皆有效、最長 <=60 秒才繼續；不自動裁剪。無視頻、超限、損壞、解碼失敗／逾時明確拒絕。保守拒絕無法可靠確認時長的檔案。

預設每經過 1000 ms 選一個實際解碼 frame，可用 `--sample-interval-ms` 調整（100..60000）。VFR 會選第一個滿足間隔的實際 frame，不假造缺幀或內插。`showinfo@source` 在 select 前提供 decoder `n` 和 PTS；`showinfo@sample` 在 select 後核對同一 PTS。**source_frame 是 decoder 的零起算 frame 編號，不是 log 行號或 sample 序號。** 圖片數量、連續命名、非空、時間與雜湊均核對。時間相對第一個解碼視頻 PTS；保留原始 PTS、stream time_base 與 timeline_origin_ms 供還原。`frame-000001` 是抽樣引用 ID，不能當作來源 frame 編號。

每次 run 產物：

| 檔案 | 內容 |
| --- | --- |
| `frames.json` | SHA、來源時長、視頻時長、音軌有無、stream index/time_base、原點、抽樣間隔、每張圖的來源 frame／PTS／時間與圖片 SHA |
| `frames/mock-frame-*.jpg` 或 `frames/meta-frame-*.jpg` | 實際抽幀（最寬 640 px），無 SAM／插圖生成 |
| `provider-result.json` | 明示 provider/mock、fixture SHA 或 model/usage，及驗證前回覆 |
| `operations.json` | schema＋語意檢查已通過的操作概念 |
| `manual.md` | 同一份概念產生的手冊、圖片、五欄與時間／來源 |
| `navide-plan.json` | 可直接作為 `plan_create` arguments 的 JSON，含絕對 workspace_path、draft stage、pending todos |
| `submission.json` | `exported_not_sent` 或真送出回執；MCP 嘗試前先記 `attempting_once` |
| `failure.json` | 失敗狀態；不含遠端 body、headers、key 或 secret URL |

無效操作輸出不會建立 `operations.json`、手冊或 task payload。若 MCP 在匯出後失敗，已驗證的本地 payload 會保留。`provider-result.json` 是診斷資料，不能繞過 validator 當成任務。

## 真 Meta provider（需明示付費授權）

必須同時 `--provider meta --confirm-paid-api` 才可能呼叫付費服務，不會從 mock 靜默切換。provider 只讀 `os.environ['META_API_KEY']`；不讀 secret 檔、不借其他 token、不使用 MODEL_API_KEY 環境名。

經使用者授權的操作員／root 才執行下列流程。金鑰唯一載入方式為 shell source 已指定的檔案；不 cat、不 print、不寫入本目錄。後續替換 key 時由使用者更新該本機檔案，再重新 source；程式碼不需更動。

```sh
set -a
source ~/.claude/secrets/meta.env
set +a
python3 cli.py /absolute/path/authorized.mp4 \
  --provider meta --confirm-paid-api --model muse-spark-1.2 \
  --workspace-path <workspace-root>
```

`--model` 支援 `muse-spark-1.2`／`muse-spark-1.3`，預設 1.2。真模式 POST `https://api.meta.ai/v1/files`，Bearer META_API_KEY，multipart `purpose=user_data` 與原始 MP4；原檔音軌保留，不把抽幀當作原聲輸入。再以 upload.id 為 `input_file.file_id` 呼叫 `/v1/responses`，message user content 包含 input_text/input_file，使用實際 schema object、strict JSON schema、max_output_tokens=4000、stream=false、reasoning.effort=low。低推理量用來在既定輸出額度內保留可見答案空間；不代表已驗證各推理量的品質差異。

僅接受成功 HTTP、`status=completed`、無 refusal，並遍歷 `output[].content[]` 取非空 `output_text.text`；不假定 output[0] 是答案，不讀 reasoning 文字，不假設 HTTP 有頂層 SDK output_text。接著仍須通過 schema＋語意檢查。incomplete/failed/空輸出明確失敗。usage 保存 input_tokens/output_tokens，若有 reasoning_tokens 亦保存明細，**reasoning 已含在 output_tokens，不重計**。本版不自動重試付費請求；timeout 的計費／完成狀態未知，應先確認再手動決定是否重跑。

失敗回覆會在 `failure.json.provider_metadata` 保存 allowlist 的 HTTP 狀態、response status/id、incomplete_details.reason 與 usage；不保存 headers、原始錯誤 body、金鑰或 secret URL。第一輪真 smoke 發現的舊版缺陷為 noncompleted 先 raise、未保留 usage，已修正；舊失敗的用量無法據此補回。

**未驗證界線：**已完成一支 14 秒無音軌影片的真 API 全鏈 smoke（見下方紀錄），未驗證一般影片準確率、真人原聲理解或精確完成時間。原始 MP4 含音軌不等於模型確實理解旁白；1.3 音訊能力／影音限制由 root 另核實。不要把 synthetic 合成音訊 smoke 當原聲理解驗收。root／指定實測 pane 獨立持有 `runs/model-compare-1030/`，本開發未碰該目錄。

## 可選 Navide MCP 真送出（本次不執行）

一般 CLI 只匯出 JSON。加 `--send-navide` 才嘗試送出，而且只接使用者明示設定的 **NAVIDE_MCP_URL（本 app 的變數約定）**；不尋找 Navide pane／host token、不讀 auth 文件。未配置時明確回報 `exported_not_sent`，不宣稱整合成功。不要把 secret URL 寫入 README、命令參數或 log；由授權操作員在自己的 shell 安全設定環境變數。

```sh
.venv/bin/python -m pip install -r requirements-mcp.txt
# 操作員已安全設定 NAVIDE_MCP_URL 的同一個 shell：
.venv/bin/python cli.py runs/synthetic-fixture.mp4 \
  --workspace-path <workspace-root> --send-navide
```

adapter 使用官方 Python SDK 1.x 的 `ClientSession` ＋ `mcp.client.streamable_http.streamablehttp_client`，依序 initialize、`call_tool('plan_create', args)`。官方 [1.x transport 定義](https://github.com/modelcontextprotocol/python-sdk/blob/v1.x/src/mcp/client/streamable_http.py) 保留該入口；2.x API 不在本次範圍。外部 caller 強制絕對 workspace_path。回執只保留 `rel_path/name/stage`；todos 已包含在 plan_create，不再逐項新增。逾時／回執不明不自動重送，可能已建立 plan，先在 Navide 檢視再決定下一步。此 adapter 不開 agent、不執行任務。

## 最小驗證

串行、裸跑，不 pipe 給 head/tail：

```sh
python3 -m unittest discover -s tests -p test_core.py -v
python3 cli.py runs/synthetic-fixture.mp4 \
  --workspace-path <workspace-root> --out runs/mock-smoke
```

單一 test 檔六個測例涵蓋：輸入拒絕、decoder frame/PTS、schema／語意、mock 全鏈與無效不建任務、Meta 本地 HTTP mock 合約、MCP 本地替身的初始化／單次呼叫／不重試。測試只使用假 key 字串與 in-process HTTP transport，不呼叫網路、不讀真 key。smoke 使用上述短 synthetic 影片；`runs/mock-smoke` 只可第一次指定，重跑請省略 `--out` 或選新路徑。

本次本地驗收（2026-10-30）：上述單一 test 檔 6/6 通過，exit 0；synthetic 生成 exit 0；mock smoke exit 0。產物 `runs/mock-smoke/` 為 3000 ms、有合成音軌，抽樣來源 frame 0/10/20、PTS 0/10240/20480（time_base=1/10240），對應 0/1000/2000 ms。兩個待辦均 pending、明示 MOCK，`submission.json` 為 exported_not_sent。此處沒有真 API／真 MCP／派工驗收。

後續授權真 smoke（同日）：第一次 CLI exit 2，noncompleted 回覆被拒絕；證據保留於 `runs/real-smoke-attempt-1/`，詳細 status／usage 因上述舊缺陷未知，原因不得推定為 token cap。修正診斷保存後，明確 low reasoning、cap 4000 再呼叫一次，CLI exit 0，upload/responses HTTP 200、completed，`runs/real-smoke/` 完成 14 個抽幀 → 真 provider → 五欄／時間／引用驗證 → 手冊 → 4 個 pending 待辦 payload；未送 MCP、未派工。原有六測例未重跑，只對新增失敗紀錄缺陷做聚焦驗證。

成功輪 input 2704（cached 0）、output 2340（已含 reasoning 1542）；依 [Meta Standard 費率](https://dev.meta.ai/docs/pricing-rate-limits) input $1.25/M、output $4.25/M，usage 推算 **US$0.013325**，並非帳單核對。指揮先前比較費用 US$0.026018，加成功輪為已知 **US$0.039343**；首次失敗未知另預留 **US$0.5**，保守額度占用 **US$0.539343**，不可把預留當實際帳單。細節見 `runs/real-smoke/run-report.json`。

真輸出僅代表結構驗證成功，語意及完成判斷仍待人工驗收。零件描述中的 `possibly nut` 保留不確定性，不可改稱確定螺帽；`grasp verified` 仍須人工查證。步驟 2/4 的最終就位／鎖緊未驗證，3.5/7/10.5/14 秒區間邊界不等於精確完成證據。單支無聲影片無法證明通用性或達成準確率／誤差門檻。

M1 是 **4 個操作、2 次裝配**，不是 4 次裝配；目前這段驗收僅 **n=1**，不能泛化到其他物件或工序。接觸時夾爪遮擋、零件類型及穩固夾持仍有未知；原片無音軌，不能據此驗證音訊／旁白理解。

後續 root 已回報以自己的 pane MCP 建立並回讀 mock 與 real 計畫；各 run 的 `navide-mcp-receipt.json` 記錄此事。M1 real 計畫為 draft、4 pending、全部 owner=user，沒有 agent 執行。CLI 原始 `submission.json` 仍維持 exported_not_sent，standalone adapter 仍未真連線驗證。完整費用、產物、人工驗收界線與 Muse 指揮交接見內部 HANDOFF（未公開）。

上述費用是 M1／模型比較的歷史快照，包含首輪未知費用的 US$0.5 預留，不是專案當前總帳單；最新彙總見下方文件收尾紀錄。

## W1 唯一重試結果與後續界線

W1 唯一一次粒度重試已結束：原輪 **0/4 → 重試 1/4，仍未通過**。唯一配對的開始／結束誤差為 **2.5／0.5 秒**；GT 本身約 **±2 秒**，穩健 ≤3 秒仍為 **0/4**，不能稱為精密時間驗收通過。回覆縮成 4 列不等於四步都辨識成功。保留原 GT，不再重試。連續細動作中的板件身份辨識與結構追蹤列為已知限制；Demo 主打 M1，但 M1 通過仍僅 **n=1**，不代表通用能力。素材 **9 支全數核驗**也不等於 9 支都經模型驗收通過。來源：W1 粒度重試報告（未公開；結論：0/4 → 重試 1/4，仍未通過，連續細動作列為已知限制）。

本次 W1 重試新增 usage 推算費用 **US$0.020043**。依指揮最新彙總，已知費用 **US$0.08082825**，加舊失敗未知費用預留 **US$0.50**，保守額度占用 **US$0.58082825**；這不是帳單核對，未知費用與預留不得當成實際支出。

Muse 已指定 B 段改由 root 的 `pane_mcp` 建單／回讀／處理狀態；獨立 adapter 列後續工作，仍未實連驗證，本流程不再需要其憑證。下一輪 **M1 組裝檢查表閉環只提出 US$0.50 預算，尚待 Muse 批准**，不是已獲批或已花費；本次文件收尾不啟動實作、Muse 推論或 Spark 驗收。

## 本地狀態與交件邊界

本 README 只記錄本地 CLI、文件與上述驗收快照，不新增或取代 startup 相關既有交件指示，也不宣稱已通過參賽／交件審核。啟動方式仍是前述 CLI 安裝與執行命令；下述 Web UI 段為 11/3 後補（此前無 web 入口）。

既有 180 秒／3 分鐘 demo 分鏡屬交件準備，不等於已完成錄影、旁白或送件；尚未完成的 SAM、Image、執行回流不得當作實作成果呈現。依目前核准計畫的 review note，**公開 repo、發文與 Submit 仍須本人核准**；規劃要求公開 repo 不等於已取得公開授權。這次只補本地 README，未公開、commit、部署或提交作品。

`.gitignore` 排除 `.env`、憑證、下載影片、生成資料與虛擬環境。fixtures 只有自製公開文字／生成程式。未新增未授權素材，也沒有 commit、部署、長存 server 或設備控制。

## Web UI added 11/3

Muse Code 當日新增的最小本機網頁（`web/server.py`，標準庫、無新依賴），只包 CLI、不改核心與既有 runs。只綁 127.0.0.1。

```sh
cd app/web
python3 server.py --port 18766
# open http://127.0.0.1:18766/ in a browser
```

功能：固定 Meta Muse Spark（無 provider 選單）。拖放／點選上傳影片（ffmpeg 能讀的影片格式都支援，不看副檔名，以 ffprobe 讀到影像軌為準，測過：MP4／MOV 含 HEVC／M4V／WebM 含 VP9／MKV／AVI／3GP／TS／MPG／WMV／OGV／GIF／無音軌 MP4；上限 100 MB；非 H.264＋AAC 由本機 ffmpeg 轉碼，不變速不裁切，VFR 轉固定幀率，provenance 記入 transcode.json）→ 可填一句 task-hint（命名輔助，不代看影片）→ 勾白話付費確認 → Analyze（server 端強制每日上限，見 Settings）→ 左右雙欄（手冊＋截圖／操作 JSON，每步附 evidence 可見證據句）→ 匯出（LeRobot 標註／VLA 序列／規劃步驟，見 `export/README.md`，僅標註層）→ Navide payload 預覽（未實連照實標示）→ 歷史 run 瀏覽（只列真推論）。無金鑰時 Analyze 換成 Settings 引導；首頁 hero 有 M1 v2 真推論 before／after 靜態對照（純說明圖，無連結）。等分步驟（各段差 5% 內）標 needs-review 警告，不擋 run。失敗一律白話（401／逾時／斷線／非影片／轉檔失敗皆有對應文案，不卡進度頁）。驗收：tests 93/93（＋1 環境 skip）、sha256 846/846（7 檔預期變動已更新基準）；live e2e 19/19；畫面經 13 輪截圖自評＋真瀏覽器實機操作驗收。

進度頁（/job/）顯示 7 段白話進度（每段附真實資料：檔名／長度／解析度／SHA、抽幀時刻、驗證細項、費用）＋畫格 filmstrip＋Activity log。reduced-motion 下無動畫。

Settings（首頁右上角／Set up 入口開彈窗，原生 dialog＋fetch 存檔，免整頁重整；整頁版已移除，只有彈窗）：彈窗只放 API key 欄位＋狀態＋Save／Clear。無 JS 時 Settings 不可用（只能用環境變數或手寫 .env）；分析流程本身不依賴 JS（原生表單送出）。本機瀏覽器以 password 欄位提交 META_API_KEY，POST 到 127.0.0.1 server，寫入 `app/.env`（權限 0600，已在 .gitignore），存檔即生效免重啟。頁面只顯示 `configured · ****末4碼`，完整金鑰不出現在任何頁面、API 或 log。防護：僅接受 Host 127.0.0.1/localhost、檢查 Origin/Referer 同源、CSRF token；環境變數優先於 .env。無 Test connection 按鈕（api.meta.ai 無免費驗證端點；實際分析時才驗證）。模型固定 muse-spark-1.2（CLI `--model`／MCP `model` 參數可指定 1.3，UI 不顯示）。每日上限只讀 `.env`（`WEB_MAX_CALLS_PER_DAY`／`WEB_MAX_USD_PER_DAY`，見 `.env.example`），預設 20 次／US$1.00，UTC 每日重置，web＋MCP 共用、server 強制；本機比賽用 3／0.10。上傳區下方 keyline 只顯示金鑰狀態＋今日用量小字，不可調（Advanced 摺疊已移除）。

Development／tests：`provider=mock` 只留給 CLI、MCP 與自動測試（不花錢），UI 不露出。`fixtures/make_synthetic.py` 產生測試片；`web/e2e-http-test.py` 預設 mock 全鏈（加 `--provider meta` 可真跑，會花錢；上傳檔由 `--clip` 指定）。防呆：web／MCP 跑 mock 前比對影片 sha256 與 synthetic 測試片，不一致拒絕（fixture 只描述測試片，套到真片會誤標）。Prompt 版本：v1 是 11/3 白天（含）以前所有 run 的提示詞，公開 evidence 前 4 筆全是 v1 產出，無 evidence 欄位，舊結果頁顯示「not recorded」屬正常。v2 是 11/3 晚上依測試員落差報告改的（切割規則、frame 引用、每步 evidence 必填、done 可見性）。v3 加逐字引用規則（無效，已棄）。v4 加不合併相反動作。v5（預設）＝v4＋整秒邊界。CLI `--prompt-version` 可回退任一版；通用規則不含任何參考答案字眼與具體時間。真實分數（eval/rounds.md，每格 n=1）：M1 上 v2 4/4、v3 2/4、v4 越界失敗、v5 4/4、v5+2fps 4/4（多一個幻覺詞）；OBS 上 v2 4/7（合併2、幻覺2）、v3 失敗、v4 4/7（kw 3/4）、v5 4/7（合併0、幻覺0）、v5+2fps 失敗。v5 最佳但仍有未過項（M1 washer 需 hint；OBS GT1/GT2/GT6 時間精度）。迭代共 8 次約 $0.14。`--task-hint` 只做命名輔助（預設空）並記入 provider-result。每個新 run 的 provider-result 都記 `prompt_version`（mock 記 null）；無此欄即 v1。不可把 v2 效果套到舊結果上。工單 12 測試見 `tests/test_prompt_v2.py`。

## Run it locally（從零部署到本機）

需求：Python 3.11 以上、FFmpeg（含 ffmpeg 與 ffprobe）、git。server 本體只用標準庫；CLI 需要 requirements（jsonschema＋httpx）。

```sh
# 1. 下載
git clone https://github.com/nt-neil/watch-once-teach-human-and-robot.git
cd watch-once-teach-human-and-robot/app

# 2. 建 venv 並裝依賴（環境放在專案內，不要裝到系統 Python）
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# 3. （測試用）產生 synthetic 測試片；M1 原片需自行依授權下載，不附在 repo
.venv/bin/python fixtures/make_synthetic.py

# 4. 啟動（一定要用 .venv 的 python：server 用同一個直譯器跑 CLI）
.venv/bin/python web/server.py --port 18766
# open http://127.0.0.1:18766/ in a browser
```

5. 首頁 hero 有 M1 真推論 before／after 靜態對照（純說明圖）；跑過的真結果出現在 History。
6. 再跑真推論：按首頁 Settings 開彈窗填入 META_API_KEY（或啟動前 `export META_API_KEY=…`、`cp .env.example .env` 填入；.env 已在 .gitignore）。上傳區下方會顯示 `Meta API key: configured / not configured`（只顯示狀態，不顯示值）。上傳影片 → 勾白話確認 → Analyze（模型固定 1.2；每日上限改 `.env`，預設 20 次／US$1.00；14 秒片約 US$0.01，依長度而定）。

常見錯誤：

- `ffmpeg/ffprobe is missing` → 沒裝 FFmpeg（macOS：`brew install ffmpeg`）。
- `Address already in use` → port 被佔用，換一個 `--port` 或先關掉佔用的行程。
- `Server has no META_API_KEY` → 照第 6 步設金鑰；沒設時 Meta 選項旁有設定說明。

## MCP server（給 agent 呼叫）

`mcp_server.py` 用官方 MCP SDK（stdio），只包既有流程、不改核心。tools：`list_examples`、`analyze_video`（mock 免費；meta 需 `confirm_paid: true`，與 Web UI 共用 3 次／US$0.10 上限）、`get_result`、`export`（lerobot／vla／plan）、`list_runs`、`send_to_navide`（payload 預覽，未實連）。路徑只收 app/ 內或內建範例；錯誤不洩漏絕對路徑與金鑰。

```sh
.venv/bin/python -m pip install -r requirements-mcp.txt
.venv/bin/python mcp_server.py   # stdio，給 MCP client 啟動
```

Claude Code／Muse Code 設定範（`mcpServers`，路徑換成你的 clone）：見 `mcp.example.json`。Navide：在 pane 內以 stdio 啟動本 server 即可（本專案即以 pane 代送方式運作）。

## W15：泛化迭代（11/3–11/4，最佳組合 v6＋keyframes＋3 票選項）

出貨預設：prompt v6（video_kind 閘門：procedural／non_procedural／uncertain）＋原解析度關鍵幀（`--no-keyframes` 可關）＋可選 3 票多數決（UI 勾選，只留嚴格多數步驟，少數進 proposal_steps 不計分）。

評分（量尺修正後重算：點事件對稱起點、真多數投票、kw 字界；詳 `eval/rounds.md`「量尺修正後重算」）：
- dev 投票 5/8：m1 4/4、s003 2/2、winter 過、talk 過、uib 4/5；l3 3/4、w1 0/4、obs 4/7 未過。「投票救回 l3」已撤回（真多數下與單次相同）。
- held-out（評一次）4/7：l4 4/4、wok 4/4、postfire 過、stump 過；m1-0008 1/4、aalto-03 2/5、ui-a 6/8 未過。
- 前提揭露：以上投票量的都是 v6 不帶關鍵幀的歷史 runs，不是出貨預設；出貨預設尚無 held-out 數字。
- 幻覺揭露：prompt 禁寫 tightening／locking／securing／nut，正好等於 M1 幻覺詞表；halluc 0 部分靠詞彙禁令，真正的防線是「done 只寫看得到的終態」。
- 已知限制：細粒度裝配合併、外觀相似板件、UI 短事件時間精度、單一連續動作過拆、5 票無增益、SAM 在廣角工作坊不可用。

介面多語系：預設英文，右上角切換繁中／简中／日本語／Español（靜態 `web/i18n/`，日／西為 machine-assisted）；模型產出原文顯示。
