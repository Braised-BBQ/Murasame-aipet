叢雨 (Murasame) - AI 桌寵
一款基於 Electron 與 Live2D 的桌面陪伴 AI 寵物，角色為《千戀＊萬花》的叢雨（Murasame）。專案結合大型語言模型（LLM）、視覺辨識、情緒與動作連動、時鐘與天氣外掛，以及日語語音合成（TTS），提供具備高度沉浸感的桌面互動體驗。

系統支援：主要支援 Windows 10 與 Windows 11。

開發聲明：本專案大部分程式碼由 Gemini 提供與協助建構。

🌟 核心特色
Live2D 互動渲染：基於 PixiJS 與 Web Audio API，實現滑鼠點擊互動與自動眼神跟隨。

AI 智能大腦：接軌 OpenAI API，支援記憶上下文與傲嬌人設風格，並能自動輸出表情與動作指令。

桌面視覺感知：整合視覺模型，支援主動分析螢幕畫面變動，進行動態吐槽與關心。

高音質日語 TTS：連線至 AutoDL GPU 算力資源進行日語語音即時合成。

模組化中轉架構：採用 FastAPI 建立中間層樞紐，統籌大腦、視覺、時間排程與語音資源。

長期記憶與排程：具備自動識別長期記憶能力，以及排程自動提醒功能。

一鍵自動部署：提供完整環境建置腳本，支援托盤最小化執行、後端連線日誌留存及後端重啟自動連動機制。

📁 專案架構
Plaintext
D:\murasame_public\
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
    └── core/                  # 中轉層核心功能模組
        ├── autodl_tts.py      # AutoDL SSH 隧道連線與 TTS 請求管理
        ├── brain.py           # LLM 對話大腦、Prompt 組合與主動發言邏輯
        ├── gcal_helper.py     # Google Calendar API 連線與行程同步
        ├── memory.py          # 對話記憶與上下文歷史紀錄管理
        ├── time_engine.py     # 時間排程引擎與主動推播觸發器
        └── vision.py          # 螢幕截圖、MSE 差異比對與視覺解析
🚀 快速開始
1. 環境需求
Node.js: v18+

Python: v3.10+

Git (建議安裝)

2. 一鍵環境建置
首次下載專案後，直接雙擊執行根目錄下的 setup.bat。

腳本將自動幫你完成以下建置：

自動執行 npm install 安裝前端所需套件。

自動執行 pip install -r requirements.txt 安裝 Python 中轉層依賴。

3. 設定檔配置
請編輯 pet_backend/config.json（或啟動後透過桌面設定頁面修改），填入你的 API 金鑰與 TTS 伺服器連線資訊（login_command、password）。

4. 啟動應用
建置完畢後，雙擊專案根目錄下的 start.bat 即可啟動！

啟動器會自動開啟 Python 中轉層，待 FastAPI 與 AutoDL 隧道準備就緒後，將自動拉起 Electron 前端視窗。

5. TTS 合成伺服器架設
請於 AutoDL 租用實例，並導入社區鏡像 kuxiaowo/AIpet-Murasame/AIpet-Murasame_GPT-SoVITs:v1.2.2。第一次開機後請至控制台，將專案資料夾內的 reference_voices.zip 上傳並解壓替代原資料夾即可。

6. 如何互動
移動角色：按住滑鼠右鍵拖曳模型。

對話：點擊滑鼠左鍵開啟對話框。

摸頭觸發：將滑鼠移動至角色頭部並滑動，即可觸發摸頭反應。

開啟設定：點擊系統列右下角的托盤小圖示，即可開啟使用者設定介面。

7. Google 日曆掛接（可選）
請至 Google Cloud Console 登入帳號並申請 Google Calendar API，取得憑證 JSON 檔並改名為 credentials.json 放入 pet_backend/ 目錄中。預設為關閉狀態，若不使用則無須放置該檔案。

📡 系統通訊規約 (Data Contract)
前端與 Python 中轉層透過 WebSocket (ws://localhost:8000/ws) 傳輸 JSON 數據：

前端發送 (Request)
JSON
{
  "type": "text",
  "content": "你今天過得好嗎？"
}
中轉層回傳 (Response)
JSON
{
  "reply_zh": "哼！主人這個大笨蛋，我才沒有在等你呢！",
  "reply_jp": "ふん！この大馬鹿者、別に待っていたわけではないのじゃ！",
  "emotion": 4,
  "playMotion": false,
  "motion": "",
  "audio_url": "http://localhost:8000/audio/response_1701234567.wav"
}
🛠️ 開發與除錯
後端運行日誌：存放在 pet_backend/logs/system.log。

托盤控制：點擊右下角系統托盤圖示可隨時隱藏或召喚叢雨。

快捷設定：可透過系統托盤右鍵選單開啟 settings.html 進行參數設定，儲存後自動重啟程式。

運行時後端終端監視器：若需要監看後端終端機輸出，請在啟動時選擇 test_start.bat。

⚠️ 系統限制與來源版權
當前限制
暫不支援動態調整視窗與模型大小。

修改系統設定後需重啟應用程式方可生效。

未有本地部屬選項。

版權與來源專案
源專案項目：kuxiaowo/AIpet-Murasame

2dlive模型:

代碼建置:gemini-3.1-pro

這是一個非官方的粉絲項目，旨在進行學習和技術交流。 Murasame及其包含的第三方美術素材、語音資料和相關資源均屬於其各自的版權所有者（包括YUZUSOFT），且未獲得AGPL許可。未經許可，請勿將這些資源用於商業用途。
