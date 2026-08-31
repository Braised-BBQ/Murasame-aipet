@echo off
chcp 65001 >nul
echo 正在檢查並清理殘留的背景行程...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000') do (
    if not "%%a"=="0" (
        taskkill /F /PID %%a >nul 2>&1
    )
)
echo 清理完成，準備啟動系統！
title Murasame Desktop Pet Launcher

:: 這段指令會在執行後端時，自動在背景以隱藏視窗執行 node launch.js
powershell -Command "Start-Process node -ArgumentList 'launch.js' -WindowStyle Hidden"

exit