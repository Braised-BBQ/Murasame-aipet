@echo off
title Murasame Desktop Pet Launcher

:: 這段指令會在執行後端時，自動在背景以隱藏視窗執行 node launch.js
powershell -Command "Start-Process node -ArgumentList 'launch.js' -WindowStyle Hidden"

exit