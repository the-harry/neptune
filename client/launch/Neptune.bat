@echo off
setlocal
rem NEPTUNE - one-click launcher for the ROG Ally.
rem Double-click me the FIRST time; it sets everything up (shortcut, server) and
rem opens the dashboard fullscreen. After that, use the "Neptune" icon on the desktop.
rem
rem It also starts the MAP BACKEND on this machine, so the chart layers, the offline
rem areas and the downloader work with no Pi attached. That backend serves the MAP, not
rem a vehicle: its hardware is the bench mock, and the sub stays simulated and flagged.
rem
rem   Neptune.bat                      find the Pi automatically and launch
rem   Neptune.bat -PiHost 192.168.42.1 skip discovery, use this address
rem   Neptune.bat -Port 8090           different local port for the dashboard files
rem   Neptune.bat -ApiPort 8010        different local port for the map backend
rem   Neptune.bat -NoApi               do not start the map backend at all
rem   Neptune.bat -Stop                close a stuck dashboard + server + map backend
rem   Neptune.bat -Kiosk               locked kiosk window (no way out on a handheld)
rem   Neptune.bat -Test                run both check suites and show the result
rem   Neptune.bat -Test client         one half only (or: -Test api)
rem
rem The script never blocks waiting for a keypress - on a handheld there is no
rem keyboard to press. Any error is shown and the window closes by itself.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0neptune.ps1" %*
rem Hand the script's exit status back to whoever ran us. -Test is meant to be usable
rem as a gate, and a launcher that always returned 0 would make a failing suite look
rem exactly like a passing one to anything that only reads the code.
endlocal & exit /b %ERRORLEVEL%
