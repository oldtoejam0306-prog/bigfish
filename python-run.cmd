@echo off
setlocal
if defined BIGFISH_PYTHON goto configured
if exist "D:\DafeiyuVoice\venv\Scripts\python.exe" set "BIGFISH_PYTHON=D:\DafeiyuVoice\venv\Scripts\python.exe"
if defined BIGFISH_PYTHON goto configured
where py >nul 2>&1
if errorlevel 1 (echo Python 3.10+ not found. Set BIGFISH_PYTHON to its full path. & exit /b 1)
py -3 %*
exit /b %errorlevel%
:configured
if not exist "%BIGFISH_PYTHON%" (echo BIGFISH_PYTHON path does not exist. & exit /b 1)
"%BIGFISH_PYTHON%" %*
exit /b %errorlevel%
