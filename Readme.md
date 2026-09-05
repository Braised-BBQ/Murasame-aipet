# 叢雨 (Murasame) - AI 桌寵

一款基於 Electron 與 Live2D 的桌面陪伴 AI 寵物，角色為《千戀＊萬花》的叢雨（Murasame）。專案結合大型語言模型（LLM）、視覺辨識、情緒與動作連動、時鐘與天氣外掛，以及日語語音合成（TTS），提供具備高度沉浸感的桌面互動體驗。

系統支援：主要支援 Windows 10 與 Windows 11。
開發聲明：本專案大部分程式碼由 Gemini 提供與協助建構。

## 🌟 核心特色
- **Live2D 互動渲染**：基於 PixiJS 與 Web Audio API，實現滑鼠點擊互動與自動眼神跟隨。
- **AI 智能大腦**：接軌 OpenAI API，支援記憶上下文與傲嬌人設風格，並能自動輸出表情與動作指令。
- **桌面視覺感知**：整合視覺模型，支援主動分析螢幕畫面變動，進行動態吐槽與關心。
- **高音質日語 TTS**：連線至 AutoDL GPU 算力資源進行日語語音即時合成。
- **模組化中轉架構**：採用 FastAPI 建立中間層樞紐，統籌大腦、視覺、時間排程與語音資源。
- **長期記憶與排程**：具備自動識別長期記憶能力，以及排程自動提醒功能。
- **一鍵自動部署**：提供完整環境建置腳本，支援托盤最小化執行、後端連線日誌留存及後端重啟自動連動機制。
- **隨機事件推送**:依照當前時間，若未處於勿擾模式則有概率觸發隨機事件。

## 📁 專案架構

```plaintext
\Murasame-aipet-main
├── setup.bat                  # 一鍵建置與下載依賴環境腳本
├── start.bat                  # Windows 快速啟動檔 (呼叫 launch.js)
├── test_start.bat             # 終端監視啟動腳本
├── launch.js                  # 啟動器 (監控後端狀態並連動前端)
├── main.js                    # Electron 主進程 (系統托盤、視窗與設定頁管理)
├── renderer.js                # Electron 渲染進程 (Live2D 控制與對話 UI)
├── index.html                 # 主視窗 UI 頁面
├── settings.html              # 設定介面 UI 頁面
├── package.json               # 前端套件依賴配置
├── requirements.txt           # Python 依賴清單
├── assets/                    # Live2D 模型與音檔資源
└── pet_backend/               # Python 中轉層
    ├── main.py                # FastAPI 入口與 WebSocket 服務
    ├── config.json            # 系統 API 金鑰與連線設定檔
    ├── logs/                  # 後端日誌目錄
    │   └── backend.log
    ├──random_event_log.json   #隨機事件紀錄
    ├──chroma_db               #長期記憶庫
    └── core/                  # 中轉層核心功能模組
        ├── autodl_tts.py      # AutoDL SSH 隧道連線與 TTS 請求管理
        ├── tts_manager.py     # tts服務管理
        ├── brain.py           # LLM 對話大腦、Prompt 組合與主動發言邏輯
        ├── memory.py          # 對話記憶與上下文歷史紀錄管理
        ├── time_engine.py     # 時間排程引擎與主動推播觸發器
        ├── config_manager.py  # 熱修改檔案處理
        ├── weather.py         # 天氣與定位管理
        └── vision.py          # 螢幕截圖、MSE 差異比對與視覺解析
```

## 🚀 快速開始

### 1. 環境需求
- Node.js: v18+(前往node.js官網:https://nodejs.org/zh-tw)
- Python: v3.10+
- Git (建議安裝)

### 2. 一鍵環境建置
首次下載專案後，直接雙擊執行根目錄下的 `setup.bat`。
腳本將自動幫你完成以下建置：
- 自動執行 `npm install` 安裝前端所需套件。
- 自動執行 `pip install -r requirements.txt` 安裝 Python 中轉層依賴。
- 在執行setup啟動時，會自動建置環境與下載依賴，在最後請確認是否有如下進度條(特別注意總項數108):
 ```
 gle-auth-httplib2, google-api-core, google-api-python-client, chromadb
   ━━━━━━━╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  19/108 [pygments]
 ```
- 若是有成功出現以上進度條，則環境建置成功，靜待程式結束後關閉終端機。
- 若無則請將終端機錯誤訊息回報。
  
### 3. 啟動應用
建置完畢後，雙擊專案根目錄下的 `start.bat` 即可啟動！第一次啟用建議使用`test_start.bat`，若有錯誤方便確認。
啟動器會自動開啟 Python 中轉層，待 FastAPI 與 AutoDL 隧道準備就緒後，將自動拉起 Electron 前端視窗。

### 4. 設定檔配置
您可以於啟動後透過桌面小托盤召喚出設定頁面修改，填入你的 API 金鑰與 TTS 伺服器連線資訊（`login_command`、`password`）。

### 5. 語音模型下載與 TTS 伺服器架設
由於 GitHub 檔案大小限制，叢雨的專屬語音模型（GPT-SoVITS 權重）與情緒參考音檔已獨立存放於雲端硬碟。請依照以下步驟完成 TTS 伺服器配置：
 - **本地部屬**
1. **取得模型包**：請前往  Google Drive 下載 (https://drive.google.com/drive/folders/1ZSxYzQgJkMsCXLOG-xFMHwwmNecEW-oH?usp=drive_link) 下載語音模型檔。
2. **解壓模型**:將資料夾內的兩個壓縮檔解壓並放到專案根目錄下(注意，務必把兩個資料夾從原資料夾內拿出來放到根目錄下)。

 - **雲端部屬**
1. **租用與初始化實例**：於 AutoDL 租用實例，並導入社區鏡像 `kuxiaowo/AIpet-Murasame/AIpet-Murasame_GPT-SoVITs:v1.2.2`。
2. **上傳並部署模型**：第一次開機後請至 AutoDL 網頁控制台，將解壓縮後的檔案上傳至指定路徑：
   - 將 `reference_voices` 壓縮檔上傳並解壓替換掉鏡像中的原資料夾。
預設為雲端部屬，可在啟動後切換。
### 6. 如何互動
- **移動角色**：按住滑鼠右鍵拖曳模型。
- **對話**：點擊滑鼠左鍵開啟對話框。
- **摸頭觸發**：將滑鼠移動至角色頭部並滑動，即可觸發摸頭反應。
- **開啟設定**：點擊系統列右下角的托盤小圖示，即可開啟使用者設定介面。

### 7.退出
- 退出時請從托盤內退出，若只關掉當下視窗後端並不會關閉。
- 也可以選擇關掉終端機(以test_start.bat開啟的話)。
- 若只關掉前端的話，後端會一直執行，此時可以再啟動一遍，啟動器會殺掉舊進程，新的再用托盤關閉即可。

### 8.更新
- 更新程式時，可以選擇保留config.json和chroma_db資料夾，更新程式後把舊的兩個檔案放回原處即可。
- chroma_db為長期記憶庫，如若不想讓桌寵忘記你，建議保留。

## 📡 系統通訊規約 (Data Contract)
前端與 Python 中轉層透過 WebSocket (`ws://localhost:8000/ws`) 傳輸 JSON 數據：

**前端發送 (Request)**
```json
{
  "type": "text",
  "content": "你今天過得好嗎？"
}
```

**中轉層回傳 (Response)**
```json
{
  "reply_zh": "哼！主人這個大笨蛋，我才沒有在等你呢！",
  "reply_jp": "ふん！この大馬鹿者、別に待っていたわけではないのじゃ！",
  "emotion": 4,
  "playMotion": false,
  "motion": "",
  "audio_url": "http://localhost:8000/audio/response_1701234567.wav"
}
```

## 🛠️ 開發與除錯
- **後端運行日誌**：存放在 `pet_backend/logs/system.log`。
- **托盤控制**：點擊右下角系統托盤圖示可隨時隱藏或召喚使用者介面。
- **快捷設定**：可透過系統托盤右鍵選單開啟 `settings.html` 進行參數設定，儲存後熱修改設定。
- **運行時後端終端監視器**：若需要監看後端終端機輸出，請在啟動時選擇 `test_start.bat`。
- **關閉進程**:開啟時會自動尋找並砍掉 8000 埠佔用者，請注意。

## ⚠️ 系統限制與來源版權

### 當前限制
- 未配備語音輸入。


## ⚖️ 許可證與版權聲明 (License & Copyright)

- **Live2D 模型與音訊素材**：本專案包含之 Live2D 角色模型、貼圖及語音素材版權歸原繪師與建模師所有，**不適用於 GPL v3 授權**。僅供非商業學習與交流使用，請勿將美術素材用於任何商業用途或二次散佈。
- **Live2D 模型提供**:穿越電線(bilibili:https://space.bilibili.com/4494100)
- **原專案來源**：[kuxiaowo/AIpet-Murasame](https://github.com/kuxiaowo/AIpet-Murasame)

- **代碼建置**：gemini-3.1-pro

這是一個非官方的粉絲項目，旨在進行學習和技術交流。 Murasame及其包含的第三方美術素材、語音資料和相關資源均屬於其各自的版權所有者（包括YUZUSOFT），且未獲得AGPL許可。未經許可，請勿將這些資源用於商業用途。
