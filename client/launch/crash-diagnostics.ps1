<#
  NEPTUNE - crash diagnostics and GPU-stall mitigation for the ROG Ally.
  RUN AS ADMINISTRATOR. Reboot afterwards.

  WHY THIS EXISTS
  ---------------
  The handheld has been hard-crashing every 20-40 minutes. The Windows logs show,
  on every occurrence, a matched pair:

     LiveKernelEvent 141  = VIDEO_ENGINE_TIMEOUT_DETECTED   (the GPU stalled)
     Kernel_d1            = DRIVER_IRQL_NOT_LESS_OR_EQUAL   (a driver faulted)

  plus "AMD Crash Defender Service" running, which exists precisely to catch GPU
  driver crashes and try to recover them instead of blue-screening. So the dominant
  failure is the GPU (or its driver) hanging, being force-reset by Windows, and
  occasionally taking the kernel down with it.

  This is NOT the dashboard. A web page cannot bugcheck Windows. But the dashboard
  is a heavy, sustained GPU workload - fullscreen H.264 decode plus continuous
  compositing - so it reliably provokes a latent fault that lighter use does not.

  WHAT THIS DOES
  --------------
   1. Makes the next crash DIAGNOSABLE. Right now no dump is being written at all,
      so the faulting driver has never actually been named - everything so far is
      inference. This switches on a kernel memory dump.

   2. Gives the GPU more headroom before Windows declares it hung. The default
      TdrDelay is 2 SECONDS: if the GPU does not respond within that, Windows
      resets it. Raising it to 10s lets a slow-but-alive GPU finish instead of
      being killed. This is a standard mitigation for repeated TDRs.

      Trade-off, stated plainly: a genuinely wedged GPU now takes 10s to recover
      instead of 2. For a console that must not crash, tolerating a stall beats
      being reset - but it is a mitigation, not a cure. The cure is a driver fix.

  Undo with:  crash-diagnostics.ps1 -Revert
#>
param([switch]$Revert, [int]$TdrSeconds = 10)

$ErrorActionPreference = "Stop"
function OK([string]$m)   { Write-Host "  OK  $m" -ForegroundColor Green }
function Info([string]$m) { Write-Host "  --  $m" -ForegroundColor DarkGray }
function Nope([string]$m) { Write-Host "  !!  $m" -ForegroundColor Yellow }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  Write-Host "`nThis needs Administrator. Relaunching with an elevation prompt..." -ForegroundColor Yellow
  $a = @("-NoProfile","-ExecutionPolicy","Bypass","-File","`"$PSCommandPath`"","-TdrSeconds",$TdrSeconds)
  if ($Revert) { $a += "-Revert" }
  Start-Process -FilePath (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -ArgumentList $a -Verb RunAs
  return
}

$gfx   = "HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers"
$crash = "HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl"

Write-Host "`n========  NEPTUNE crash diagnostics  ========" -ForegroundColor Magenta

if ($Revert) {
  Write-Host "`n[1/2] GPU timeout -> Windows defaults" -ForegroundColor Cyan
  foreach ($n in @('TdrDelay','TdrDdiDelay')) { Remove-ItemProperty -Path $gfx -Name $n -ErrorAction SilentlyContinue }
  OK "TdrDelay / TdrDdiDelay cleared (back to 2s / 5s)"
  Write-Host "`n[2/2] Crash dump -> small (256 KB)" -ForegroundColor Cyan
  Set-ItemProperty -Path $crash -Name CrashDumpEnabled -Value 3 -Type DWord
  OK "restored"
  Write-Host "`nReverted. Reboot to apply.`n" -ForegroundColor Magenta
  return
}

# ---------------------------------------------------------------------------
Write-Host "`n[1/3] Make the next crash diagnosable" -ForegroundColor Cyan
# 2 = kernel memory dump. Big enough to name the faulting driver, far smaller than
# a complete dump. Without this there is nothing to analyse and every conclusion
# about "which driver" stays a guess.
Set-ItemProperty -Path $crash -Name CrashDumpEnabled -Value 2 -Type DWord
Set-ItemProperty -Path $crash -Name AlwaysKeepMemoryDump -Value 1 -Type DWord
Set-ItemProperty -Path $crash -Name Overwrite -Value 1 -Type DWord
OK "kernel memory dump enabled -> C:\Windows\MEMORY.DMP"
New-Item -ItemType Directory -Path "C:\Windows\Minidump" -Force | Out-Null
OK "C:\Windows\Minidump ready"

$pf = Get-CimInstance Win32_PageFileUsage
$ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1)
Info "RAM ${ram} GB, page file $($pf.AllocatedBaseSize) MB on $($pf.Name)"
if ($pf.AllocatedBaseSize -lt 2048) {
  Nope "page file may be too small to capture a kernel dump - set it to system-managed"
} else { OK "page file is large enough for a kernel dump" }

# ---------------------------------------------------------------------------
Write-Host "`n[2/3] Give the GPU headroom before Windows resets it" -ForegroundColor Cyan
Info "current TdrDelay: $((Get-ItemProperty $gfx).TdrDelay)  (blank = default 2s)"
Set-ItemProperty -Path $gfx -Name TdrDelay    -Value $TdrSeconds        -Type DWord
Set-ItemProperty -Path $gfx -Name TdrDdiDelay -Value ($TdrSeconds + 5)  -Type DWord
OK "TdrDelay = ${TdrSeconds}s, TdrDdiDelay = $($TdrSeconds + 5)s"
Info "this tolerates GPU stalls; it does not fix whatever is causing them"

# ---------------------------------------------------------------------------
Write-Host "`n[3/3] Evidence for whoever fixes the driver" -ForegroundColor Cyan
$nic = Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.DeviceName -match 'Realtek USB GbE|RTL8153' } | Select-Object -First 1
if ($nic) { Info "tether NIC driver : $($nic.DriverVersion)  ($($nic.DriverDate))" }
$gpu = Get-CimInstance Win32_VideoController | Select-Object -First 1
Info "GPU driver        : $($gpu.DriverVersion)  ($($gpu.DriverDate))"
$cd = Get-Service "AMD Crash Defender Service" -ErrorAction SilentlyContinue
if ($cd) { Info "AMD Crash Defender: $($cd.Status) / $($cd.StartType)" }
$n141 = @(Get-WinEvent -FilterHashtable @{LogName='Application';Id=1001;StartTime=(Get-Date).AddDays(-1)} -ErrorAction SilentlyContinue |
          Where-Object { $_.Message -match 'LiveKernelEvent' }).Count
Info "LiveKernelEvent (GPU timeout) reports in the last 24h: $n141"

# ---------------------------------------------------------------------------
# ANALYSE the dump. This is the step that ends the guessing: it names the driver.
#
# It has already been run once, and the answer was:
#
#     DPC_WATCHDOG_VIOLATION (0x133)
#     Failure.Bucket: 0x133_ISR_amdkmdag!unknown_function
#     amdkmdag.sys  32.0.23027.3001   (AMD Radeon kernel display driver)
#
# so the fault is the AMD display driver overrunning in its interrupt handler.
# Nothing in this repo can repair that - the fix is an AMD driver change - but the
# dashboard no longer piles sustained compositing load onto it (CONFIG.ui.reduceGpu).
Write-Host "`n[4/4] Analyse the crash dump" -ForegroundColor Cyan
$dump = "C:\Windows\MEMORY.DMP"
if (-not (Test-Path $dump)) {
  Info "no MEMORY.DMP yet - nothing to analyse"
} else {
  $kd = Get-ChildItem "$env:ProgramFiles\WindowsApps" -Directory -ErrorAction SilentlyContinue |
        Where-Object Name -like 'Microsoft.WinDbg*' |
        ForEach-Object { Join-Path $_.FullName "amd64\kd.exe" } |
        Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $kd) {
    Nope "WinDbg not installed - install it, then re-run this script:"
    Info  "  winget install --id Microsoft.WinDbg -e"
  } else {
    $report = Join-Path $env:TEMP "neptune-bugcheck.txt"
    Info "analysing $dump (symbols download on first run; this takes a minute)..."
    & $kd -z $dump -y "srv*C:\Symbols*https://msdl.microsoft.com/download/symbols" `
          -c ".symfix; .reload; !analyze -v; q" 2>&1 | Out-File -FilePath $report -Encoding utf8
    $bucket = Select-String -Path $report -Pattern 'Failure\.Bucket' | Select-Object -First 1
    $img    = Select-String -Path $report -Pattern '^IMAGE_NAME:|^MODULE_NAME:' | Select-Object -First 2
    if ($bucket) { OK "$($bucket.Line.Trim())" }
    foreach ($i in $img) { Info $i.Line.Trim() }
    Info "full report: $report"
  }
}

Write-Host "`nDone. REBOOT for the dump and TDR settings to take effect." -ForegroundColor Magenta
Write-Host "KNOWN CAUSE: amdkmdag.sys (AMD display driver) overruns its ISR -> 0x133." -ForegroundColor Yellow
Write-Host "  The real fix is an AMD driver change: update via AMD Software (Adrenalin)," -ForegroundColor DarkGray
Write-Host "  or roll back if the current one is newer than the last stable build." -ForegroundColor DarkGray
Write-Host "  Current: $((Get-CimInstance Win32_VideoController | Select-Object -First 1).DriverVersion)" -ForegroundColor DarkGray
Write-Host "The dashboard already avoids sustained GPU compositing (CONFIG.ui.reduceGpu)." -ForegroundColor DarkGray
Write-Host "Also try:  Neptune.bat -SafeGraphics   (keeps the GPU video engine out of the path)`n" -ForegroundColor DarkGray
