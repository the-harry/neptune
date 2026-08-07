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
