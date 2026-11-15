@echo off
chcp 65001 >nul
setlocal EnableExtensions
title VideoWorkflow Studio — o'rnatish

set "SRC=%~dp0"
set "DEST=%LOCALAPPDATA%\VideoWorkflow"
set "EXE=%DEST%\VideoWorkflow.exe"

echo ==============================================
echo   VideoWorkflow Studio — desktop o'rnatish
echo ==============================================
echo.
echo Manba:  %SRC%
echo Manzil: %DEST%
echo.

if /I "%SRC%"=="%DEST%\" goto RUN

if not exist "%DEST%" (
  mkdir "%DEST%" >nul 2>&1
  if errorlevel 1 (
    echo XATOLIK: %DEST% papkasini yaratib bo'lmadi.
    pause
    exit /b 1
  )
  echo Birinchi o'rnatish: barcha fayllar nusxalanadi...
  robocopy "%SRC%" "%DEST%" /E /R:2 /W:2 /NFL /NDL /NJH /NJS /XD downloads
) else (
  echo Avvalgi o'rnatish topildi. Ma'lumotlar (storage, data) saqlanadi.
  if exist "%EXE%" "%EXE%" --stop --no-pause >nul 2>&1
  echo Dastur fayllari yangilanadi...
  robocopy "%SRC%" "%DEST%" /E /R:2 /W:2 /NFL /NDL /NJH /NJS /XD downloads storage data
)

if errorlevel 8 (
  echo.
  echo XATOLIK: fayllarni nusxalashda xato yuz berdi.
  pause
  exit /b 1
)

if not exist "%EXE%" (
  echo XATOLIK: VideoWorkflow.exe topilmadi.
  pause
  exit /b 1
)

echo.
echo Ish stolida va Start menyuda yorliq yaratilmoqda...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; $desktop = [Environment]::GetFolderPath('Desktop'); $s = $ws.CreateShortcut((Join-Path $desktop 'VideoWorkflow.lnk')); $s.TargetPath = '%EXE%'; $s.WorkingDirectory = '%DEST%'; $s.Description = 'VideoWorkflow Studio'; $s.Save(); $start = [Environment]::GetFolderPath('StartMenu'); $p = Join-Path $start 'Programs'; $s2 = $ws.CreateShortcut((Join-Path $p 'VideoWorkflow.lnk')); $s2.TargetPath = '%EXE%'; $s2.WorkingDirectory = '%DEST%'; $s2.Description = 'VideoWorkflow Studio'; $s2.Save()" >nul 2>&1

:RUN
echo.
echo VideoWorkflow oynasi ochilmoqda...
start "" "%EXE%"
echo.
echo Tayyor. Dastur alohida oynada ochiladi (brauzer kerak emas).
echo Oynani yopish uchun 10 soniya kutamiz...
timeout /t 10 >nul
endlocal
