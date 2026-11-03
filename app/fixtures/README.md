此資料夾只有本次自製、可公開的文字與程式。synthetic-response.json 是手寫的兩步 MOCK fixture，並非辨識任意影片的結果。

make_synthetic.py 用 FFmpeg testsrc2 與 440 Hz sine 產生 3 秒、10 fps 的測試 MP4。影片含合成音訊，但沒有真人旁白。輸出放在忽略追蹤的 runs/，沒有下載、複製或使用候選/未授權素材。

預設 fixture 引用第 0 與第 2 秒抽樣圖，只適用於這支自製影片及預設每 1000 ms 抽樣。換影片或抽樣率時使用 --mock-response 指定匹配的手寫 fixture；即使 fixture 合法，仍會標記 MOCK，不代表模型看懂影片。
