@echo off
chcp 65001 >nul
setlocal EnableExtensions
title VideoWorkflow Studio — o'chirish

set "DEST=%LOCALAPPDATA%\VideoWorkflow"

echo ==============================================
echo   VideoWorkflow Studio — o'chirish
echo ==============================================
echo.
echo Papka: %DEST%
echo.
echo DIQQAT: batch holati, natijalar va baza ham o'chadi.
choice /C HO /N /M "Davom etasizmi? [H]a / [O]yo'q: "
if errorlevel 2 goto CANCEL

if exist "%DEST%\VideoWorkflow.exe" "%DEST%\VideoWorkflow.exe" --stop --no-pause >nul 2>&1
timeout /t 3 >nul

del /q "%USERPROFILE%\Desktop\VideoWorkflow.lnk" >nul 2>&1
del /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\VideoWorkflow.lnk" >nul 2>&1

cd /d "%LOCALAPPDATA%"
set "DEST_PS=%DEST:'=''%"
start "" /b powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Sleep -Seconds 2; Remove-Item -LiteralPath '%DEST_PS%' -Recurse -Force -ErrorAction SilentlyContinue"
echo O'chirilmoqda...
timeout /t 6 >nul
if exist "%DEST%" (
  echo XATOLIK: papkani o'chirib bo'lmadi. Qo'lda o'chiring: %DEST%
) else (
  echo Papka o'chirildi.
)
exit /b 0

:CANCEL
echo Bekor qilindi.
timeout /t 3 >nul
endlocal
