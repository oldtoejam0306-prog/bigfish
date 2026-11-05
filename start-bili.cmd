@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not defined BIGFISH_PERSONA_DATA_DIR set "BIGFISH_PERSONA_DATA_DIR=%LOCALAPPDATA%\BigFish\WhalePersona-20261105-simple-settings"
set "BIGFISH_DATA_DIR=%BIGFISH_PERSONA_DATA_DIR%"
if not defined BIGFISH_FFMPEG_DIR if exist "%LOCALAPPDATA%\BigFish\tools\ffmpeg-9.0.2\bin\ffmpeg.exe" if exist "%LOCALAPPDATA%\BigFish\tools\ffmpeg-9.0.2\bin\ffprobe.exe" set "BIGFISH_FFMPEG_DIR=%LOCALAPPDATA%\BigFish\tools\ffmpeg-9.0.2\bin"
:launch
call "%~dp0python-run.cmd" -m bili_workbench.web --data-dir "%BIGFISH_DATA_DIR%" --port 0 --supervised
set "BIGFISH_LAUNCH_EXIT_CODE=%ERRORLEVEL%"
if "%BIGFISH_LAUNCH_EXIT_CODE%"=="75" goto launch
if not "%BIGFISH_LAUNCH_EXIT_CODE%"=="0" echo Workbench failed. No automatic retry. Check the error above.
pause
exit /b %BIGFISH_LAUNCH_EXIT_CODE%
