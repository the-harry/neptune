@echo off
setlocal
rem NEPTUNE - one-click launcher for the ROG Ally.
rem Double-click me the FIRST time; it sets everything up (shortcut, server) and
rem opens the dashboard fullscreen. After that, use the "Neptune" icon on the desktop.
rem
rem   Neptune.bat                      find the Pi automatically and launch
rem   Neptune.bat -PiHost 192.168.42.1 skip discovery, use this address
rem   Neptune.bat -Port 8090           different local port
rem   Neptune.bat -Stop                close a stuck dashboard + server
rem   Neptune.bat -Kiosk               locked kiosk window (no way out on a handheld)
rem
rem The script never blocks waiting for a keypress - on a handheld there is no
rem keyboard to press. Any error is shown and the window closes by itself.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0neptune.ps1" %*
endlocal
