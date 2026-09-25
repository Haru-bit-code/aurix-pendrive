@echo off
setlocal EnableDelayedExpansion
title AURIX launcher
rem ===== settings (edit these) =====
set NGL=99
set CTX=8192
set PORT=8080
set AURIX_PORT=8765
set MAX_MODELS=auto
set "TOOLS="
rem =================================

set "ROOT=%~dp0"
set "UV=%ROOT%bin\win\uv.exe"
set "MODELS=%ROOT%models"
set "LOG=%ROOT%data\logs\server.log"
set "ALOG=%ROOT%data\logs\aurix.log"
if not exist "%ROOT%data\logs" mkdir "%ROOT%data\logs"
if not exist "%ROOT%data\workspace" mkdir "%ROOT%data\workspace"

rem ---- detect GPU: NVIDIA -> CUDA build in bin\win, otherwise Vulkan or CPU build if present ----
set "BINDIR=%ROOT%bin\win"
set "GPU=no NVIDIA GPU"
where nvidia-smi >nul 2>&1 && (
  for /f "delims=" %%g in ('nvidia-smi --query-gpu^=name^,memory.total --format^=csv^,noheader') do set "GPU=NVIDIA %%g"
) || (
  if exist "%ROOT%bin\win-vulkan\llama-server.exe" ( set "BINDIR=%ROOT%bin\win-vulkan" ) else if exist "%ROOT%bin\win-cpu\llama-server.exe" ( set "BINDIR=%ROOT%bin\win-cpu" )
)
set "BIN=%BINDIR%\llama-server.exe"

rem ---- detect RAM ----
set RAM_GB=0
for /f %%r in ('powershell -NoProfile -Command "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB)"') do set RAM_GB=%%r
if "%MAX_MODELS%"=="auto" ( if !RAM_GB! GEQ 12 ( set MAX_MODELS=2 ) else ( set MAX_MODELS=1 ) )
echo This PC: !GPU!, !RAM_GB! GB RAM - keeping up to !MAX_MODELS! model(s) in memory

if not exist "%BIN%" ( echo [x] llama-server.exe not found in !BINDIR! & pause & exit /b 1 )
if not exist "%UV%" ( echo [x] uv.exe not found in bin\win & pause & exit /b 1 )

rem Remove macOS "._" metadata files copied over from a Mac
del /s /q "%MODELS%\._*" >nul 2>&1

set FOUND=0
echo Models found:
for /r "%MODELS%" %%f in (*.gguf) do (
  echo %%~nf | findstr /i /b "mmproj" >nul || ( echo   - %%~nf & set FOUND=1 )
)
if "!FOUND!"=="0" ( echo [x] No .gguf models in models\ & pause & exit /b 1 )

set "EXTRA="
if defined TOOLS set "EXTRA=--tools %TOOLS%"
if exist "%ROOT%mcp.json" set "EXTRA=!EXTRA! --mcp-servers-config "%ROOT%mcp.json""

echo Starting model server...
start "llama-server" /min /D "%ROOT%data\workspace" "%BIN%" !EXTRA! --models-dir "%MODELS%" --models-max !MAX_MODELS! -ngl %NGL% -c %CTX% --load-mode none --jinja --host 127.0.0.1 --port %PORT% --log-file "%LOG%"

echo Starting AURIX (the first run downloads Python and packages once, a few minutes)...
set "PYTHONDONTWRITEBYTECODE=1"
start "aurix" /min /D "%ROOT%" cmd /c ""%UV%" run --quiet --no-project --python 3.12 --with-requirements aurix\requirements.txt python -m aurix > "%ALOG%" 2>&1"

:wait_llama
timeout /t 2 /nobreak >nul
tasklist /fi "imagename eq llama-server.exe" | find /i "llama-server.exe" >nul || (
  echo [x] Model server stopped. Check data\logs\server.log
  pause & goto stop
)
set "CODE="
for /f %%c in ('curl -s -o nul -w "%%{http_code}" http://127.0.0.1:%PORT%/health') do set "CODE=%%c"
if not "!CODE!"=="200" goto wait_llama

set /a TRIES=0
:wait_aurix
timeout /t 2 /nobreak >nul
set /a TRIES+=1
if !TRIES! GTR 450 (
  echo [x] AURIX did not start. Check data\logs\aurix.log
  pause & goto stop
)
set "CODE="
for /f %%c in ('curl -s -o nul -w "%%{http_code}" http://127.0.0.1:%AURIX_PORT%/api/health') do set "CODE=%%c"
if not "!CODE!"=="200" goto wait_aurix

echo Ready. Opening AURIX in your browser...
start "" http://127.0.0.1:%AURIX_PORT%
echo Background tasks keep running while this window is open.
echo.
echo Press any key here to STOP everything.
pause >nul

:stop
taskkill /im llama-server.exe /f >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%AURIX_PORT% " ^| findstr LISTENING') do taskkill /PID %%p /T /F >nul 2>&1
echo Stopped. You can safely eject the pendrive.
timeout /t 2 >nul
