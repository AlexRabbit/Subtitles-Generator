@echo off
setlocal EnableDelayedExpansion
title Subtitles Generator
cd /d "%~dp0"

echo.
echo  ========================================
echo   Subtitles Generator - Setup and Launch
echo  ========================================
echo.

:: Log startup output
set "STARTUP_LOG=logs\startup.log"
if not exist "logs" mkdir logs
echo === Run %date% %time% ===>> "%STARTUP_LOG%"

:: ---- Find Python 3.10+ (prefer 3.10 for torch/whisper) ----
set "PY_CMD="
for %%V in (3.10 3.11 3.12 3.13) do (
    if not defined PY_CMD (
        py -%%V -c "import sys" >nul 2>&1
        if !errorlevel! equ 0 set "PY_CMD=py -%%V"
    )
)
if not defined PY_CMD (
    python -c "import sys; assert sys.version_info[:2] >= (3,10)" >nul 2>&1
    if !errorlevel! equ 0 set "PY_CMD=python"
)
if not defined PY_CMD (
    echo [ERROR] Python 3.10+ not found.
    echo Install from https://www.python.org/downloads/ then re-run this file.
    echo [ERROR] Python 3.10+ not found.>> "%STARTUP_LOG%"
    goto :pause_end
)

echo [OK] Using: %PY_CMD%
%PY_CMD% --version

:: ---- Virtual environment (portable, inside project) ----
if not exist "venv\Scripts\python.exe" (
    echo.
    echo [..] Creating virtual environment...
    %PY_CMD% -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv.
        goto :pause_end
    )
)
set "PYTHON=venv\Scripts\python.exe"
set "PIP=venv\Scripts\pip.exe"

:: ---- .env from example ----
if not exist ".env" (
    echo [..] Creating .env from .env.example...
    copy /Y ".env.example" ".env" >nul
)

:: ---- Upgrade pip (pin setuptools for torch) ----
echo.
echo [..] Upgrading pip...
"%PYTHON%" -m pip install --upgrade "pip<26" "setuptools<82" wheel -q

:: ---- Install dependencies ----
echo [..] Installing Python packages (first run may take several minutes)...
"%PIP%" install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed.
    goto :pause_end
)

:: ---- CUDA PyTorch if NVIDIA GPU ----
echo [..] Checking GPU / CUDA PyTorch...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\install_cuda_torch.ps1"

:: ---- FFmpeg ----
set "FFMPEG_LOCAL=ffmpeg\bin\ffmpeg.exe"
if exist "%FFMPEG_LOCAL%" (
    echo [OK] FFmpeg found: %FFMPEG_LOCAL%
    set "PATH=%CD%\ffmpeg\bin;%PATH%"
) else (
    where ffmpeg >nul 2>&1
    if errorlevel 1 (
        echo.
        echo [..] FFmpeg not found. Downloading portable build...
        powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\install_ffmpeg.ps1"
        if exist "%FFMPEG_LOCAL%" (
            echo [OK] FFmpeg installed to ffmpeg\bin\
            set "PATH=%CD%\ffmpeg\bin;%PATH%"
        ) else (
            echo [WARN] FFmpeg auto-download failed. Install manually or add to PATH.
            echo        https://www.gyan.dev/ffmpeg/builds/
        )
    ) else (
        echo [OK] FFmpeg found in system PATH.
    )
)

:: ---- Directories ----
if not exist "logs" mkdir logs
if not exist "models" mkdir models

:: ---- Launch app ----
echo.
echo [..] Starting Subtitles Generator...
echo     This PC:  http://127.0.0.1:8765
echo     Phone:    enable "Allow LAN access" in Settings (off by default)
echo     Firewall: if phone cannot connect, run scripts\allow_lan_firewall.ps1 as Admin
echo     Close this window to stop the app.
echo.
"%PYTHON%" subtitles_generator.py
set "EXIT_CODE=%ERRORLEVEL%"
if %EXIT_CODE% neq 0 (
    "%PYTHON%" subtitles_generator.py >> "%STARTUP_LOG%" 2>&1
    set "EXIT_CODE=%ERRORLEVEL%"
)

if %EXIT_CODE% neq 0 (
    echo.
    echo [ERROR] App exited with code %EXIT_CODE%.
    echo [ERROR] See logs\startup.log and logs\errors.log
    echo [ERROR] App exit %EXIT_CODE%>> "%STARTUP_LOG%"
)

:pause_end
echo.
echo Press any key to close...
pause >nul
endlocal
