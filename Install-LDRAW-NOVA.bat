@echo off
rem Double-click this file to install LDRAW-NOVA on Windows.
rem It runs install.ps1 from this folder, or downloads it if this file is on its own.
title LDRAW-NOVA installer
echo.
echo  LDRAW-NOVA installer
echo  ====================
echo  This sets up everything needed to build LEGO models with Claude.
echo  It will ask a few questions and may open folder pickers.
echo.
if exist "%~dp0install.ps1" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -FromBat %*
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $f = Join-Path $env:TEMP 'ldraw-nova-install.ps1'; Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/Kerklord/ldraw-nova/master/install.ps1' -OutFile $f; & $f -FromBat; exit $LASTEXITCODE"
)
echo.
if errorlevel 2 (
  echo  Almost there: finish the WSL steps listed above, then double-click this file again.
) else if errorlevel 1 (
  echo  Something went wrong. Read the messages above, fix the problem, and run this again.
) else (
  echo  All done. Double-click LDRAW-NOVA on your desktop to start building.
)
echo.
pause
