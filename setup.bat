@echo off
chcp 65001 >nul
:: 強制將執行路徑鎖定在 bat 檔所在的資料夾
cd /d "%~dp0"

echo =========================================
echo 開始配置專案環境與設定
echo =========================================

echo.
echo [1/5] 檢查並建立基礎設定檔 (config.json)...
if not exist "pet_backend" mkdir "pet_backend"
if not exist "pet_backend\config.json" (
    echo 找不到 config.json，正在為您自動建立預設設定...
    (
        echo {
        echo     "api_key": "",
        echo     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        echo     "model": "gemini-3.6-flash",
        echo     "sub_model": "gemini-3.1-flash-lite",
        echo     "tts_mode": "autodl",
        echo     "tts_engine_root": "GPT-SoVITS",
        echo     "tts_model_dir": "Murasame_SoVITS",
        echo     "max_history_turns": 15,
        echo     "auto_location": true,
        echo     "weather_location": "Taipei",
        echo     "random_event_interval_minutes": 30,
        echo     "random_event_probability": 0.15,
        echo     "model_scale": 1.0,
        echo     "autodl": {
        echo         "login_command": "",
        echo         "password": "",
        echo         "remote_command": "bash -lc 'bash run.sh; bash'"
        echo     },
        echo     "enable_google_calendar": false,
        echo     "gcal_credentials_path": "credentials.json",
        echo     "vision_cooldown_seconds": 300,
        echo     "vision_mse_threshold": 500,
        echo     "do_not_disturb": 0,
        echo     "enable_voice_chat": true,
        echo     "enable_mcp": true,
        echo     "show_terminal": false
        echo }
    ) > "pet_backend\config.json"
    set FIRST_RUN=1
)

echo.
echo [2/5] 檢查 FFmpeg 環境...
where ffmpeg >nul 2>nul
if %errorlevel% equ 0 (
    echo 已偵測到可用 FFmpeg，略過安裝。
    goto :ffmpeg_done
)
if exist "pet_backend\bin\ffmpeg.exe" (
    echo 偵測到專案目錄已有 FFmpeg，略過安裝。
    set "PATH=%CD%\pet_backend\bin;%PATH%"
    goto :ffmpeg_done
)

echo 正在下載 FFmpeg 工具包 (約 107MB)...
if not exist "pet_backend\bin" mkdir "pet_backend\bin"

powershell -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " ^
    "$rawUrl = 'https://github.com/Braised-BBQ/Murasame-aipet/releases/download/v1.0.0/ffmpeg.zip'; " ^
    "$fastUrl = 'https://ghfast.top/' + $rawUrl; " ^
    "$out = 'pet_backend\bin\ffmpeg.zip'; " ^
    "$headers = @{ 'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' }; " ^
    "try { " ^
    "    Write-Host '嘗試經由高速節點下載...'; " ^
    "    Invoke-WebRequest -Uri $fastUrl -OutFile $out -Headers $headers -TimeoutSec 15; " ^
    "} catch { " ^
    "    Write-Host '高速節點無回應或逾時，切換為 GitHub 官方連線...'; " ^
    "    Invoke-WebRequest -Uri $rawUrl -OutFile $out -Headers $headers; " ^
    "} " ^
    "if (Test-Path $out) { " ^
    "    Write-Host '下載成功，正在解壓縮...'; " ^
    "    Expand-Archive -Path $out -DestinationPath 'pet_backend\bin' -Force; " ^
    "    Remove-Item $out -Force; " ^
    "}"

:: 若 zip 解開後帶有一層資料夾，自動搬到 bin 根目錄
if not exist "pet_backend\bin\ffmpeg.exe" (
    powershell -Command "Get-ChildItem -Path 'pet_backend\bin' -Recurse -Filter 'ffmpeg.exe' | Move-Item -Destination 'pet_backend\bin\' -Force -ErrorAction SilentlyContinue"
    powershell -Command "Get-ChildItem -Path 'pet_backend\bin' -Recurse -Filter 'ffprobe.exe' | Move-Item -Destination 'pet_backend\bin\' -Force -ErrorAction SilentlyContinue"
)

if exist "pet_backend\bin\ffmpeg.exe" (
    set "PATH=%CD%\pet_backend\bin;%PATH%"
    echo FFmpeg 配置成功！
) else (
    echo [錯誤] 下載或解壓失敗，請檢查網路。
    pause
    exit /b 1
)

:ffmpeg_done
echo [3/5] 檢查 Python 虛擬環境...
if not exist "venv\Scripts\activate.bat" (
    echo 正在尋找可用的 Python 指令並建立虛擬環境...
    
    :: 嘗試 1：使用 py (防禦 msys64 污染的最佳解)
    py -m venv venv >nul 2>&1
    
    :: 嘗試 2：如果 py 失敗，改用標準 python
    if not exist "venv\Scripts\activate.bat" (
        python -m venv venv >nul 2>&1
    )
    
    :: 嘗試 3：如果 python 也失敗，嘗試 python3 (某些環境的預設)
    if not exist "venv\Scripts\activate.bat" (
        python3 -m venv venv >nul 2>&1
    )
    
    :: 最終檢查
    if not exist "venv\Scripts\activate.bat" (
        echo [錯誤] 建立失敗！請確認這台電腦是否已安裝 Python，並且在安裝時有勾選 "Add Python to PATH"。
        pause
        exit /b 1
    ) else (
        echo 虛擬環境建立完成。
    )
) else (
    echo 虛擬環境已存在，略過建立。
)

:: 啟動虛擬環境
call "venv\Scripts\activate.bat"

echo.
echo [4/5] 安裝 Python 依賴套件...
:: 👇 [關鍵修正 2] 絕對路徑呼叫 venv 內的 python.exe，無視全域變數干擾
"venv\Scripts\python.exe" -m pip install --upgrade pip
if exist requirements.txt (
    "venv\Scripts\python.exe" -m pip install -r requirements.txt
) else (
    echo [警告] 找不到 requirements.txt
)

echo.
echo [5/5] 安裝 Node.js 前端套件...
if exist package.json (
    call npm install
) else (
    echo [警告] 找不到 package.json
)

echo.
echo =========================================
echo 環境安裝完成！請嘗試透過test_start.bat啟動
echo =========================================