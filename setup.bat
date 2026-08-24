@echo off
:: 設定字元集為 UTF-8，避免中文亂碼
chcp 65001 >nul

echo =========================================
echo 開始配置專案環境與設定
echo =========================================

echo.
echo [1/4] 檢查並建立基礎設定檔 (config.json)...

if not exist "pet_backend" mkdir "pet_backend"

:: 設定一個變數用來記錄是否為第一次啟動
set FIRST_RUN=0

if not exist "pet_backend\config.json" (
    echo 找不到 config.json，正在為您自動建立預設設定...
    (
        echo {
        echo     "api_key": "",
        echo     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        echo     "model": "gemini-3.6-flash",
        echo     "sub_model": "gemini-3.1-flash-lite",
        echo     "weather_location": "Taipei",
        echo     "autodl": {
        echo         "login_command": "",
        echo         "password": "",
        echo         "remote_command": "bash -lc 'bash run.sh; bash'"
        echo     },
        echo     "enable_google_calendar": false,
        echo     "gcal_credentials_path": "credentials.json",
        echo     "vision_cooldown_seconds": 300,
        echo     "vision_mse_threshold": 500,
        echo     "do_not_disturb": true,
        echo     "show_terminal": false
        echo }
    ) > "pet_backend\config.json"
    echo 設定檔建立完成。
    set FIRST_RUN=1
) else (
    echo config.json 已存在，略過建立。
)

echo.
echo [2/4] 檢查 Python 虛擬環境...
if not exist venv\Scripts\activate.bat (
    python -m venv venv
    echo 虛擬環境建立完成。
) else (
    echo 虛擬環境已存在。
)

call "venv\Scripts\activate.bat"

echo.
echo [3/4] 安裝 Python 依賴套件...
python -m pip install --upgrade pip
if exist requirements.txt (
    pip install -r requirements.txt
) else (
    echo [警告] 找不到 requirements.txt
)

echo.
echo [4/4] 安裝 Node.js 前端套件...
if exist package.json (
    call npm install
) else (
    echo [警告] 找不到 package.json
)

echo.
echo =========================================
echo 環境安裝完成！正在啟動應用程式...
echo =========================================

:: 根據是否為第一次啟動，決定是否加上 --show-settings 參數
if exist package.json (
    if %FIRST_RUN%==1 (
        start npm start -- --show-settings
    ) else (
        start npm start
    )
)

pause
