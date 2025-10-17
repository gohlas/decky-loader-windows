@echo off
title Decky Loader Server Updater
echo Updating Decky Loader server files...

setlocal

:: Set server folder path
set "SERVER_DIR=%USERPROFILE%\homebrew\server"

:: Specify the fixed version you want to install
set "FIXED_VERSION=v1.2.3"  :: <-- Replace with the version you want

echo Installing Decky Loader version: %FIXED_VERSION%

:: Construct download URLs using the fixed version
set "PLUGIN1_URL=https://github.com/gohlas/decky-loader-windows/releases/download/%FIXED_VERSION%/PluginLoader.exe"
set "PLUGIN2_URL=https://github.com/gohlas/decky-loader-windows/releases/download/%FIXED_VERSION%/PluginLoader_noconsole.exe"

:: Force close running instances
echo Closing any running PluginLoader processes...
taskkill /F /IM PluginLoader.exe >nul 2>&1
taskkill /F /IM PluginLoader_noconsole.exe >nul 2>&1

:: Ensure server directory exists
if not exist "%SERVER_DIR%" mkdir "%SERVER_DIR%"

:: Download the files with retries
echo Downloading PluginLoader.exe...
curl -L --retry 5 -o "%SERVER_DIR%\PluginLoader.exe" "%PLUGIN1_URL%"

echo Downloading PluginLoader_noconsole.exe...
curl -L --retry 5 -o "%SERVER_DIR%\PluginLoader_noconsole.exe" "%PLUGIN2_URL%"

echo Update completed successfully.

:: Restart PluginLoader_noconsole.exe
echo Starting PluginLoader_noconsole.exe...
start "" "%SERVER_DIR%\PluginLoader_noconsole.exe"

:: Loop to check if PluginLoader_noconsole.exe is running (max 10 seconds)
set "FOUND=0"
for /L %%i in (1,1,10) do (
    tasklist /FI "IMAGENAME eq PluginLoader_noconsole.exe" | findstr /I "PluginLoader_noconsole.exe" >nul
    if %ERRORLEVEL%==0 (
        set "FOUND=1"
        goto :Running
    )
    timeout /t 1 >nul
)

:Running
if %FOUND%==1 (
    echo PluginLoader_noconsole.exe is running successfully.
) else (
    echo ERROR: PluginLoader_noconsole.exe failed to start within 10 seconds.
)

:: Auto-close after 10 seconds
echo This window will close automatically in 10 seconds...
timeout /t 10 >nul

endlocal
exit /b 0