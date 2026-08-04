<#
  NEPTUNE — one-click topside launcher (ROG Ally / Windows).

  Double-click this (or the desktop shortcut it creates) and it does EVERYTHING in order:
    1. figures out the Pi's address (asks once, remembers it)
    2. creates a Desktop shortcut with the trident icon (first run only)
    3. starts a tiny local static server for the dashboard  (127.0.0.1 — no admin, no deps)
    4. opens Brave / Chrome / Edge FULLSCREEN, pointed at the Pi
       ...then serves until you close the app (Alt+F4), then stops.

  Every step is idempotent — it checks "is this already done?" and skips if so, so you can
  just run it and trust it, like a normal program. The Pi is backend-only and plain HTTP
  (sealed tether, no TLS), so there's no certificate to deal with.

  Options:  -PiHost 192.168.1.88   -Port 8090   -NoKiosk   -Setup (steps 1-2 only, don't launch)
#>
param([string]$PiHost = "", [int]$Port = 8080, [switch]$NoKiosk, [switch]$Setup)

$root     = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$icon     = Join-Path $root "icon.ico"
$hostFile = Join-Path $PSScriptRoot "neptune-host.txt"
$DefaultHost = "192.168.1.88"           # fallback if you never set one

function Step($n,$m){ Write-Host "`n[$n/4] $m" -ForegroundColor Cyan }
function OK($m){ Write-Host "      OK  $m" -ForegroundColor Green }
function Info($m){ Write-Host "      --  $m" -ForegroundColor DarkGray }
function Nope($m){ Write-Host "      !!  $m" -ForegroundColor Yellow }

Write-Host "`n========  NEPTUNE  ========" -ForegroundColor Magenta

# --- 1. Pi address (arg > saved file > ask once > default) ------------------
Step 1 "Pi address"
if (-not $PiHost -and (Test-Path $hostFile)) { $PiHost = (Get-Content $hostFile -First 1).Trim() }
if (-not $PiHost) {
  $PiHost = Read-Host "      Enter the Pi's IP (e.g. 192.168.1.88)"
  if ($PiHost) { Set-Content -Path $hostFile -Value $PiHost }
}
if (-not $PiHost) { $PiHost = $DefaultHost }
OK "Pi = $PiHost   (edit launch\neptune-host.txt to change)"

# --- 2. Desktop shortcut (self-install, first run only) ---------------------
Step 2 "Desktop shortcut"
try {
  $lnkPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "Neptune.lnk"
  if (Test-Path $lnkPath) { Info "already there" }
  else {
    $ws  = New-Object -ComObject WScript.Shell
    $ps  = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $lnk = $ws.CreateShortcut($lnkPath)
    $lnk.TargetPath       = $ps
    $lnk.Arguments        = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    $lnk.WorkingDirectory = $PSScriptRoot
    $lnk.IconLocation     = "$icon,0"
    $lnk.Description       = "Neptune ROV dashboard"
    $lnk.Save()
    OK "created $lnkPath"
  }
} catch { Nope "couldn't create the shortcut ($_)" }

if ($Setup) { Write-Host "`nSetup complete. Use the Neptune desktop icon to launch.`n" -ForegroundColor Magenta; return }

# --- 3. Local static server (raw socket, no admin) --------------------------
Step 3 "Local server"
try { $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port); $listener.Start() }
catch { Nope "port $Port is busy — is Neptune already running? Try -Port 8090."; return }
OK "dashboard served on http://localhost:$Port"

# --- 4. Open the browser fullscreen (Brave > Chrome > Edge) -----------------
Step 4 "Dashboard"
$cands = @(
  "$env:ProgramFiles\BraveSoftware\Brave-Browser\Application\brave.exe",
  "${env:ProgramFiles(x86)}\BraveSoftware\Brave-Browser\Application\brave.exe",
  "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\Application\brave.exe",
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
)
$exe = $cands | Where-Object { Test-Path $_ } | Select-Object -First 1
$url = "http://localhost:$Port/?host=$PiHost"
$userdata = Join-Path $env:LOCALAPPDATA "Neptune\browser"
$common = @("--user-data-dir=$userdata", "--no-first-run", "--no-default-browser-check")
if ($NoKiosk) { $bargs = @("--app=$url", "--start-fullscreen") + $common }
else {
  $bargs = @("--kiosk", $url) + $common
  if ($exe -like "*msedge.exe") { $bargs += "--edge-kiosk-type=fullscreen" }
}
$browser = $null
if ($exe) { $browser = Start-Process $exe -ArgumentList $bargs -PassThru; OK "$(Split-Path $exe -Leaf) fullscreen — Alt+F4 to exit" }
else { Nope "no Brave/Chrome/Edge found — open $url yourself (server is running)" }

# --- 6. Serve until the app closes ------------------------------------------
$mime = @{
  ".html"="text/html; charset=utf-8"; ".js"="text/javascript"; ".css"="text/css"; ".svg"="image/svg+xml";
  ".json"="application/json"; ".webmanifest"="application/manifest+json"; ".ico"="image/x-icon";
  ".png"="image/png"; ".jpg"="image/jpeg"; ".map"="application/json"; ".woff2"="font/woff2"; ".woff"="font/woff"; ".ttf"="font/ttf"
}
try {
  while ($true) {
    if ($browser -and $browser.HasExited) { break }
    if ($listener.Pending()) {
      $c = $listener.AcceptTcpClient()
      try {
        $ns = $c.GetStream(); $ns.ReadTimeout = 3000
        $buf = New-Object byte[] 8192
        $n = $ns.Read($buf, 0, $buf.Length)
        $line = ([System.Text.Encoding]::ASCII.GetString($buf, 0, $n) -split "`r`n")[0]
        $path = (($line -split ' ')[1]); if (-not $path) { $path = "/" }
        $path = (($path -split '\?')[0]); $path = [System.Uri]::UnescapeDataString($path)
        if ($path -eq "/") { $path = "/index.html" }
        $full = [System.IO.Path]::GetFullPath((Join-Path $root ($path.TrimStart('/') -replace '/','\')))
        if ($full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path $full -PathType Leaf)) {
          $bytes = [System.IO.File]::ReadAllBytes($full)
          $ext = [System.IO.Path]::GetExtension($full).ToLower(); $ct = $mime[$ext]; if (-not $ct) { $ct = "application/octet-stream" }
          $hdr = "HTTP/1.1 200 OK`r`nContent-Type: $ct`r`nContent-Length: $($bytes.Length)`r`nCache-Control: no-cache`r`nConnection: close`r`n`r`n"
        } else {
          $bytes = [System.Text.Encoding]::UTF8.GetBytes("404: $path")
          $hdr = "HTTP/1.1 404 Not Found`r`nContent-Type: text/plain`r`nContent-Length: $($bytes.Length)`r`nConnection: close`r`n`r`n"
        }
        $hb = [System.Text.Encoding]::ASCII.GetBytes($hdr)
        $ns.Write($hb, 0, $hb.Length); $ns.Write($bytes, 0, $bytes.Length); $ns.Flush()
      } catch {} finally { $c.Close() }
    } else { Start-Sleep -Milliseconds 80 }
  }
} finally { $listener.Stop(); Write-Host "`nNeptune closed — server stopped.`n" -ForegroundColor Magenta }
