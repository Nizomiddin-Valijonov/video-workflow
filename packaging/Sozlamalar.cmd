@echo off
chcp 65001 >nul
setlocal
title VideoWorkflow — sozlamalar

set "ENV=%~dp0app\backend\.env"
if not exist "%ENV%" (
  echo Sozlamalar fayli hali yaratilmagan.
  echo Avval VideoWorkflow.exe ni bir marta ishga tushiring.
  pause
  exit /b 1
)

echo .env fayli Notepad'da ochilmoqda:
echo   %ENV%
echo.
echo Video tahlili uchun OPENROUTER_API_KEY qatorini to'ldiring.
echo Batch (online-mahalla) uchun AI kaliti shart emas.
start "" notepad "%ENV%"
endlocal
