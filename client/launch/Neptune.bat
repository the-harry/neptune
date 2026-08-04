@echo off
rem NEPTUNE — one-click launcher for the ROG Ally.
rem Double-click me the FIRST time; it sets everything up (shortcut, cert, server) and
rem opens the dashboard fullscreen. After that, use the "Neptune" icon it puts on the desktop.
rem   Neptune.bat -PiHost 192.168.1.88   (set the Pi IP)   |   -NoKiosk   |   -Port 8090
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0neptune.ps1" %*
