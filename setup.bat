@echo off
chcp 65001 >nul

echo =========================================
echo 開始配置專案環境 Python 後端與 Electron
echo =========================================

echo.
echo [1/3] 檢查並建立 Python 虛擬環境...
if not exist venv (
    python -m venv venv
    echo 虛擬環境建立完成。
) else (
    echo 虛擬環境已存在，直接套用。
)

:: 啟動虛擬環境
call venv\Scripts\activate.bat

echo.
echo [2/3] 更新 pip 並安裝 Python 依賴套件...
python -m pip install --upgrade pip

if exist requirements.txt (
    pip install -r requirements.txt
    echo Python 套件安裝完成。
) else (
    echo [警告] 找不到 requirements.txt！請確保該檔案與此 bat 檔在同一目錄下。
)

echo.
echo [3/3] 安裝 Node.js 前端套件...
if exist package.json (
    call npm install
    echo NPM 套件安裝完成。
) else (
    echo [警告] 找不到 package.json！請確認目前的目錄是否包含前端設定檔。
)

echo.
echo =========================================
echo 環境配置已全部完成！
echo =========================================
pause