<#
  NEPTUNE - one-click topside launcher (ROG Ally / Windows).

  Double-click this (or the desktop shortcut it creates) and it does EVERYTHING in order:
    1. finds the Pi (probes the tether IP, neptune.local, then the saved address)
    2. creates a Desktop shortcut with the trident icon (first run only)
    3. starts a small CONCURRENT local static server for the dashboard (127.0.0.1)
    4. opens Chrome / Edge FULLSCREEN, pointed at the Pi
       ...then serves until you close the window, then stops and cleans up after itself.

  The Pi is backend-only and plain HTTP (sealed tether, no TLS) - no certificate to deal with.

  SAFETY / RECOVERY (why this is not a kiosk):
    The Ally has no physical keyboard, so a locked --kiosk window cannot be closed by the
    operator and the only way out is a hard reboot. The default here is a fullscreen APP
    window, which can be closed normally and Alt-Tabbed away from. Pass -Kiosk if you
    really want the locked variant.

  Options:
    -PiHost 192.168.42.1   skip discovery, use this address
    -Port 8080             local static-server port (auto-advances if busy)
    -Kiosk                 locked kiosk window (NOT recommended on the handheld)
    -Setup                 steps 1-2 only, don't launch
    -Stop                  kill any running Neptune server/browser and exit
#>
param(
  [string]$PiHost = "",
  [int]$Port = 8080,
  [switch]$Kiosk,
  [switch]$Setup,
  [switch]$Stop
)

$ErrorActionPreference = "Stop"
$root      = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$icon      = Join-Path $root "icon.ico"
$hostFile  = Join-Path $PSScriptRoot "neptune-host.txt"
$userdata  = Join-Path $env:LOCALAPPDATA "Neptune\browser"

# Tether default: the deterministic point-to-point address install.sh pins on the Pi's eth0.
# (See client/launch/README.md - the Ally holds 192.168.42.2/24 on its Ethernet adapter.)
$TetherHost = "192.168.42.1"

function Step([string]$n, [string]$m) { Write-Host "`n[$n/4] $m" -ForegroundColor Cyan }
function OK([string]$m)   { Write-Host "      OK  $m" -ForegroundColor Green }
function Info([string]$m) { Write-Host "      --  $m" -ForegroundColor DarkGray }
function Nope([string]$m) { Write-Host "      !!  $m" -ForegroundColor Yellow }

# Never block forever on a handheld with no keyboard: pause, but time out.
# [Console]::KeyAvailable throws outright when there is no console attached or when
# stdin is redirected (shortcut launches, piped output), so a naive keypress wait
# turned a harmless message into an unhandled error. Detect that once and just sleep.
function PauseBriefly([int]$seconds = 20) {
  Write-Host "`n(closing in $seconds s)" -ForegroundColor DarkGray
  $canPeek = $true
  try { $null = [Console]::KeyAvailable } catch { $canPeek = $false }
  for ($i = 0; $i -lt $seconds * 10; $i++) {
    if ($canPeek) {
      try { if ([Console]::KeyAvailable) { $null = [Console]::ReadKey($true); return } }
      catch { $canPeek = $false }
    }
    Start-Sleep -Milliseconds 100
  }
}

# ---------------------------------------------------------------------------
# Browser process bookkeeping - keyed on OUR user-data-dir so we never touch the
# operator's own browser windows.
# ---------------------------------------------------------------------------
function Get-NeptuneBrowsers {
  $pattern = "*" + ($userdata -replace '\\', '\') + "*"
  Get-CimInstance Win32_Process -Filter "Name='chrome.exe' OR Name='msedge.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*--user-data-dir=*" -and $_.CommandLine -like $pattern }
}
function Stop-NeptuneBrowsers {
  $procs = @(Get-NeptuneBrowsers)
  if (-not $procs.Count) { return 0 }
  foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Milliseconds 600
  return $procs.Count
}

# ---- -Stop: clean up and get out --------------------------------------------
if ($Stop) {
  $n = Stop-NeptuneBrowsers
  Write-Host "stopped $n Neptune browser process(es)." -ForegroundColor Magenta
  # the server dies with its own process; nothing else to do
  return
}

# ---------------------------------------------------------------------------
# SINGLE INSTANCE. Two servers on one port is the classic wedge: the second
# instance used to throw, hit `Read-Host`, and block invisibly behind a kiosk.
# ---------------------------------------------------------------------------
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, 'Global\NeptuneTopside', [ref]$createdNew)
if (-not $createdNew) {
  Write-Host "`nNeptune is already running." -ForegroundColor Yellow
  Write-Host "Close that window first, or run:  Neptune.bat -Stop" -ForegroundColor DarkGray
  PauseBriefly 10
  return
}

# NOTE: no $browser handle on purpose. Liveness is decided by scanning for processes
# using OUR user-data-dir, because the PID returned by Start-Process exits instantly
# whenever Chromium hands the command line to an existing instance for that profile.
$listener   = $null
$pool       = $null
$shared     = [hashtable]::Synchronized(@{ quit = $false })

try {
  Write-Host "`n========  NEPTUNE  ========" -ForegroundColor Magenta

  # =========================================================================
  Step 1 "Pi address"
  # =========================================================================
  # Probe candidates and use whichever actually answers. A wrong-but-pingable
  # address and a dead Pi used to look identical; now we ask each one directly.
  function Test-Pi([string]$h) {
    if (-not $h) { return $false }
    try {
      $r = Invoke-WebRequest -Uri "http://$h/api/status" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
      return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500)
    } catch {
      # A refused/errored HTTP response still proves something is listening.
      if ($_.Exception.Response) { return $true }
      return $false
    }
  }

  $saved = ""
  if (Test-Path $hostFile) { $saved = (Get-Content $hostFile -First 1 -ErrorAction SilentlyContinue).Trim() }

  if ($PiHost) {
    OK "Pi = $PiHost   (given on the command line)"
  } else {
    $candidates = @($TetherHost, 'neptune.local') + @($saved | Where-Object { $_ }) | Select-Object -Unique
    Info "probing: $($candidates -join ', ')"
    foreach ($c in $candidates) {
      if (Test-Pi $c) { $PiHost = $c; break }
    }
    if ($PiHost) {
      OK "Pi = $PiHost   (answered /api/status)"
      Set-Content -Path $hostFile -Value $PiHost -Encoding ASCII
    } else {
      # Not found is NOT fatal - the dashboard is offline-first and must still open.
      $PiHost = if ($saved) { $saved } else { $TetherHost }
      Nope "no Pi answered - starting anyway, pointed at $PiHost"
      Info "the dashboard runs offline; it will connect by itself when the Pi comes up"

      # The overwhelmingly common cause is that this machine was never given its
      # side of the point-to-point tether, so say so instead of leaving the
      # operator to guess. A direct cable has no DHCP, so Windows sits on a
      # 169.254.x.x link-local address and can never reach 192.168.42.1.
      $subnet = ($TetherHost -replace '\.\d+$', '.')
      $haveTetherIp = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                        Where-Object { $_.IPAddress -like "$subnet*" }).Count -gt 0
      if (-not $haveTetherIp) {
        Nope "this handheld has no $subnet* address - the tether is not set up"
        Info "run ONCE as Administrator:  $(Join-Path $PSScriptRoot 'tether-setup.ps1')"
      } else {
        $eth = Get-NetAdapter -Name Ethernet -ErrorAction SilentlyContinue
        if ($eth -and $eth.Status -ne 'Up') { Info "Ethernet adapter is '$($eth.Status)' - check the cable" }
        else { Info "tether address is set; the Pi itself may still be booting (give it ~40s)" }
      }
    }
  }

  # =========================================================================
  Step 2 "Desktop shortcut"
  # =========================================================================
  $lnkPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "Neptune.lnk"
  if (Test-Path $lnkPath) {
    Info "already there"
  } else {
    try {
      $ws  = New-Object -ComObject WScript.Shell
      $ps  = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
      $lnk = $ws.CreateShortcut($lnkPath)
      $lnk.TargetPath       = $ps
      $lnk.Arguments        = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
      $lnk.WorkingDirectory = $PSScriptRoot
      $lnk.IconLocation     = "$icon,0"
      $lnk.Description      = "Neptune ROV dashboard"
      $lnk.Save()
      OK "created $lnkPath"
    } catch { Nope "could not create the shortcut ($($_.Exception.Message))" }
  }

  if ($Setup) {
    Write-Host "`nSetup complete. Use the Neptune desktop icon to launch.`n" -ForegroundColor Magenta
    return
  }

  # =========================================================================
  Step 3 "Local server"
  # =========================================================================
  # Concurrent: browsers open half a dozen sockets at once (and speculative ones
  # that send nothing at all). The old single-threaded accept+3s-blocking-read
  # served one at a time and stalled the whole dashboard on any silent socket.
  $started = $false
  for ($p = $Port; $p -lt $Port + 12; $p++) {
    try {
      $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $p)
      $listener.Start()
      $Port = $p; $started = $true; break
    } catch {
      $listener = $null
      Info "port $p busy, trying $($p + 1)"
    }
  }
  if (-not $started) { throw "no free port in $Port..$($Port + 11)" }
  OK "dashboard served on http://localhost:$Port"

  $mime = @{
    ".html" = "text/html; charset=utf-8"; ".js" = "text/javascript"; ".css" = "text/css"; ".svg" = "image/svg+xml";
    ".json" = "application/json"; ".webmanifest" = "application/manifest+json"; ".ico" = "image/x-icon";
    ".png" = "image/png"; ".jpg" = "image/jpeg"; ".map" = "application/json"; ".woff2" = "font/woff2";
    ".woff" = "font/woff"; ".ttf" = "font/ttf"
  }

  # One connection, start to finish. Runs on a pool thread so a slow or silent
  # socket can never stall another request.
  $handler = {
    param($client, $root, $mime, $shared)
    try {
      $client.ReceiveTimeout = 5000
      $client.SendTimeout    = 5000
      $stream = $client.GetStream()
      $stream.ReadTimeout    = 5000

      # Read until end of headers (a request can arrive split across packets).
      $buf = New-Object byte[] 16384
      $sb  = New-Object System.Text.StringBuilder
      $total = 0
      while ($true) {
        $n = $stream.Read($buf, 0, $buf.Length)
        if ($n -le 0) { break }
        $total += $n
        [void]$sb.Append([System.Text.Encoding]::ASCII.GetString($buf, 0, $n))
        if ($sb.ToString().Contains("`r`n`r`n") -or $total -gt 262144) { break }
      }
      if ($total -le 0) { return }          # speculative socket that never spoke - just drop it

      $requestLine = ($sb.ToString() -split "`r`n")[0]
      $parts = $requestLine -split ' '
      $path  = if ($parts.Length -gt 1) { $parts[1] } else { "/" }
      $path  = ($path -split '\?')[0]
      $path  = [System.Uri]::UnescapeDataString($path)

      # The dashboard's EXIT button hits this so the operator can always get out.
      if ($path -eq "/__quit") {
        $shared.quit = $true
        $body = [System.Text.Encoding]::UTF8.GetBytes("bye")
        $head = "HTTP/1.1 200 OK`r`nContent-Type: text/plain`r`nContent-Length: $($body.Length)`r`nConnection: close`r`n`r`n"
        $hb = [System.Text.Encoding]::ASCII.GetBytes($head)
        $stream.Write($hb, 0, $hb.Length); $stream.Write($body, 0, $body.Length); $stream.Flush()
        return
      }

      if ($path -eq "/") { $path = "/index.html" }
      $relative = ($path.TrimStart('/')) -replace '/', '\'
      $full = [System.IO.Path]::GetFullPath((Join-Path $root $relative))

      if ($full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase) -and (Test-Path $full -PathType Leaf)) {
        $body = [System.IO.File]::ReadAllBytes($full)
        $ext  = [System.IO.Path]::GetExtension($full).ToLowerInvariant()
        $ct   = if ($mime.ContainsKey($ext)) { $mime[$ext] } else { "application/octet-stream" }
        $head = "HTTP/1.1 200 OK`r`nContent-Type: $ct`r`nContent-Length: $($body.Length)`r`nCache-Control: no-cache`r`nConnection: close`r`n`r`n"
      } else {
        $body = [System.Text.Encoding]::UTF8.GetBytes("404: $path")
        $head = "HTTP/1.1 404 Not Found`r`nContent-Type: text/plain; charset=utf-8`r`nContent-Length: $($body.Length)`r`nConnection: close`r`n`r`n"
      }
      $hb = [System.Text.Encoding]::ASCII.GetBytes($head)
      $stream.Write($hb, 0, $hb.Length)
      $stream.Write($body, 0, $body.Length)
      $stream.Flush()
    } catch {
      # a dead client is normal; never let it reach the accept loop
    } finally {
      try { $client.Close() } catch {}
    }
  }

  $pool = [runspacefactory]::CreateRunspacePool(1, 8)
  $pool.Open()
  $inflight = New-Object System.Collections.ArrayList

  # =========================================================================
  Step 4 "Dashboard"
  # =========================================================================
  # Chrome first, then Edge. (Brave removed - Chrome is the supported browser.)
  $candidatesExe = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
  )
  $exe = $null
  foreach ($c in $candidatesExe) { if ($c -and (Test-Path $c)) { $exe = $c; break } }

  # An orphaned window from a previous run owns the Chromium process-singleton for
  # this profile. A new launch would hand its command line to that orphan and exit
  # immediately - which used to look like "the browser closed" and tore the server
  # down ~180 ms after start. Clear them out first.
  $killed = Stop-NeptuneBrowsers
  if ($killed) { Info "closed $killed orphaned Neptune window(s) from a previous run" }

  $url = "http://localhost:$Port/?host=$PiHost"
  New-Item -ItemType Directory -Path $userdata -Force | Out-Null

  $common = @("--user-data-dir=$userdata", "--no-first-run", "--no-default-browser-check",
              "--disable-features=Translate", "--disable-background-networking")
  if ($Kiosk) {
    $bargs = @("--kiosk", $url) + $common
    if ($exe -like "*msedge.exe") { $bargs += "--edge-kiosk-type=fullscreen" }
    Nope "kiosk mode - there is no on-screen way out of this window"
  } else {
    $bargs = @("--app=$url", "--start-fullscreen", "--start-maximized") + $common
  }

  if ($exe) {
    $null = Start-Process -FilePath $exe -ArgumentList $bargs -PassThru
    Start-Sleep -Milliseconds 1200          # let the window register before we start watching
    OK "$(Split-Path $exe -Leaf) fullscreen"
    Info "close the window (or the EXIT button in the dashboard) to stop the server"
  } else {
    Nope "no Chrome/Edge found - open $url yourself (server is running)"
  }

  # ---- accept loop --------------------------------------------------------
  # Liveness is checked by scanning for OUR browser processes, not by the PID we
  # spawned: with the Chromium singleton that PID legitimately exits immediately.
  # The scan is expensive, so it runs every ~2.5 s while accepts stay responsive.
  $lastCheck = [DateTime]::UtcNow
  $sawBrowser = $false
  while ($true) {
    if ($shared.quit) { Info "EXIT requested from the dashboard"; break }

    if ($exe -and ([DateTime]::UtcNow - $lastCheck).TotalSeconds -ge 2.5) {
      $lastCheck = [DateTime]::UtcNow
      $alive = @(Get-NeptuneBrowsers).Count -gt 0
      if ($alive) { $sawBrowser = $true }
      elseif ($sawBrowser) { Info "dashboard window closed"; break }
    }

    if ($listener.Pending()) {
      $client = $listener.AcceptTcpClient()
      $ps = [powershell]::Create()
      $ps.RunspacePool = $pool
      [void]$ps.AddScript($handler).AddArgument($client).AddArgument($root).AddArgument($mime).AddArgument($shared)
      [void]$inflight.Add(@{ ps = $ps; handle = $ps.BeginInvoke() })
    } else {
      Start-Sleep -Milliseconds 25
    }

    # reap finished handlers
    if ($inflight.Count -gt 0) {
      for ($i = $inflight.Count - 1; $i -ge 0; $i--) {
        $job = $inflight[$i]
        if ($job.handle.IsCompleted) {
          try { $null = $job.ps.EndInvoke($job.handle) } catch {}
          try { $job.ps.Dispose() } catch {}
          $inflight.RemoveAt($i)
        }
      }
    }
  }
} catch {
  Write-Host "`nERROR: $($_.Exception.Message)" -ForegroundColor Red
  PauseBriefly 30
} finally {
  # Leave nothing behind: an orphaned browser is what arms the next launch to fail.
  try { if ($listener) { $listener.Stop() } } catch {}
  try { if ($pool) { $pool.Close(); $pool.Dispose() } } catch {}
  try { $null = Stop-NeptuneBrowsers } catch {}
  try { if ($mutex) { $mutex.ReleaseMutex(); $mutex.Dispose() } } catch {}
  Write-Host "`nNeptune closed - server stopped.`n" -ForegroundColor Magenta
}
