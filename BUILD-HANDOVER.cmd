@echo off
chcp 65001 >nul
setlocal EnableExtensions
title VideoWorkflow Studio — tarqatish paketini yig'ish
cd /d "%~dp0"

set "PYEXE=%LOCALAPPDATA%\VideoWorkflow\runtime\python\python.exe"
set "PYARGS=-I"
if exist "%PYEXE%" goto RUN

set "PYEXE=py"
set "PYARGS=-3 -I"
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 goto RUN

set "PYEXE=python"
set "PYARGS=-I"

:RUN
echo ============================================================
echo   Tarqatish paketi yig'ilmoqda
echo   Python: %PYEXE%
echo ============================================================
echo.

"%PYEXE%" %PYARGS% tools\build_release.py --smoke
if errorlevel 1 goto FAIL

echo.
echo ============================================================
echo   TAYYOR. "dist" papkasidagi README.txt ni o'qing:
echo   u yerda qaysi faylni yuborish kerakligi yozilgan.
echo ============================================================
pause
exit /b 0

:FAIL
echo.
echo ============================================================
echo   XATO: paket yig'ilmadi. Yuqoridagi xabarni o'qing.
echo   Tarqatishdan oldin albatta tuzating.
echo ============================================================
pause
exit /b 1
