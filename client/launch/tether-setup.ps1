<#
  NEPTUNE - one-time tether setup for the ROG Ally. RUN AS ADMINISTRATOR, once.

  Five things, all of which need elevation:

   1. Give the Ethernet adapter the fixed tether address 192.168.42.2/24.
      A direct Ally<->Pi cable has NO DHCP SERVER, so Windows falls back to an
      APIPA 169.254.x.x address and the Pi (which does the same) becomes
      unreachable. Both ends fixed = deterministic, no DHCP, no mDNS needed.

   2. Stop Windows power-suspending the USB Ethernet adapter. The Realtek USB GbE
      dongle used for the tether reports DeviceSelectiveSuspended=1 out of the box,
      which drops the link mid-dive and looks exactly like the Pi going away.

   3. Leave the adapter's DNS alone and keep the metric high enough that Wi-Fi
      stays the internet path - the tether is a point-to-point link, not a route
      to the world.

   4. Allow DESKTOP APPS to use location. Windows keeps this off by default and it
      is separate from the main location switch. Chrome is a desktop app, so with
      it off navigator.geolocation always fails and the map can never take an
      origin fix - and nothing in the dashboard can fix that from inside the page.

  Undo with:  tether-setup.ps1 -Revert   (back to DHCP + default power settings;
              the location switches are left alone, they are not tether-specific)
#>
param(
  [string]$Adapter = "Ethernet",
  [string]$Address = "192.168.42.2",
  [string]$Mask    = "255.255.255.0",
  [switch]$Revert
)

$ErrorActionPreference = "Stop"

function OK([string]$m)   { Write-Host "  OK  $m" -ForegroundColor Green }
function Info([string]$m) { Write-Host "  --  $m" -ForegroundColor DarkGray }
function Nope([string]$m) { Write-Host "  !!  $m" -ForegroundColor Yellow }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  Write-Host "`nThis needs Administrator." -ForegroundColor Yellow
  Write-Host "Relaunching with an elevation prompt..." -ForegroundColor DarkGray
  $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
               "-Adapter", "`"$Adapter`"", "-Address", $Address, "-Mask", $Mask)
  if ($Revert) { $argList += "-Revert" }
  Start-Process -FilePath (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") `
                -ArgumentList $argList -Verb RunAs
  return
}

Write-Host "`n========  NEPTUNE tether setup  ========" -ForegroundColor Magenta

# The tether NIC is a USB dongle and it can be absent - unplugged, or dropped off the
# bus by the fault we are chasing. That must NOT abort the whole script: steps 4 and 5
# (Windows location + the Chrome policy) have nothing to do with the adapter, and
# bailing here is why they silently never ran.
$nic = Get-NetAdapter -Name $Adapter -ErrorAction SilentlyContinue
if ($nic) {
  Info "adapter: $($nic.Name) - $($nic.InterfaceDescription) [$($nic.Status)]"
} else {
  Nope "no adapter named '$Adapter' - skipping the tether steps (1-3), continuing with the rest"
  Get-NetAdapter | ForEach-Object { Write-Host "        $($_.Name)  [$($_.Status)]  $($_.InterfaceDescription)" }
  Write-Host "        (re-run with -Adapter `"<name>`" once it is plugged in)" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
if ($Revert) {
  Write-Host "`n[1/3] Address -> DHCP" -ForegroundColor Cyan
  if ($nic) {
    netsh interface ip set address name="$Adapter" source=dhcp | Out-Null
    netsh interface ip set dns     name="$Adapter" source=dhcp | Out-Null
    OK "back to DHCP"
  } else { Info "skipped - adapter not present" }

  Write-Host "`n[2/3] USB selective suspend -> enabled (Windows default)" -ForegroundColor Cyan
  powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 1
  powercfg /setdcvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 1
  powercfg /setactive SCHEME_CURRENT
  OK "restored"

  Write-Host "`n[3/3] Adapter power management -> default" -ForegroundColor Cyan
  $guid = '{4d36e972-e325-11ce-bfc1-08002be10318}'
  Get-ChildItem "HKLM:\SYSTEM\CurrentControlSet\Control\Class\$guid" -ErrorAction SilentlyContinue | ForEach-Object {
    $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
    if ($p.DriverDesc -eq $nic.InterfaceDescription) {
      Remove-ItemProperty -Path $_.PSPath -Name PnPCapabilities -ErrorAction SilentlyContinue
      OK "cleared PnPCapabilities on $($_.PSChildName)"
    }
  }
  Write-Host "`nReverted. Unplug/replug the adapter (or reboot) to apply.`n" -ForegroundColor Magenta
  return
}

# ---------------------------------------------------------------------------
Write-Host "`n[1/5] Fixed tether address" -ForegroundColor Cyan
if (-not $nic) { Info "skipped - adapter not present" } else {
  try {
    netsh interface ip set address name="$Adapter" static $Address $Mask | Out-Null
    OK "$Adapter = $Address/$Mask  (no gateway - the tether is point-to-point)"
    Info "Wi-Fi stays the internet path; the tether only reaches the Pi"
  } catch {
    Nope "could not set the address: $($_.Exception.Message)"
  }
}

Write-Host "`n[2/5] USB selective suspend" -ForegroundColor Cyan
# Windows suspends the USB NIC and the tether silently drops. On a vehicle control
# link that is not acceptable, so turn it off on both AC and battery.
powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
powercfg /setdcvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
powercfg /setactive SCHEME_CURRENT
OK "disabled on AC and battery"

Write-Host "`n[3/5] Adapter power management" -ForegroundColor Cyan
# 0x18 = disable "allow the computer to turn off this device to save power"
# and "allow this device to wake the computer".
$guid = '{4d36e972-e325-11ce-bfc1-08002be10318}'
$done = $false
if (-not $nic) { Info "skipped - adapter not present"; $done = $true }
else { Get-ChildItem "HKLM:\SYSTEM\CurrentControlSet\Control\Class\$guid" -ErrorAction SilentlyContinue | ForEach-Object {
  $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
  if ($p.DriverDesc -eq $nic.InterfaceDescription) {
    Set-ItemProperty -Path $_.PSPath -Name PnPCapabilities -Value 24 -Type DWord
    OK "power-down disabled for '$($p.DriverDesc)' (key $($_.PSChildName))"
    $done = $true
  }
} }
if (-not $done) { Nope "could not find the driver key - set it by hand in Device Manager > Power Management" }

if ($nic) {
  try {
    Disable-NetAdapterPowerManagement -Name $Adapter -ErrorAction Stop
    OK "Disable-NetAdapterPowerManagement applied"
  } catch { Info "adapter exposes no WMI power settings (registry change above still applies)" }
}

Write-Host "`n[4/5] Location for desktop apps" -ForegroundColor Cyan
# The map origin comes from the handheld's own position via navigator.geolocation.
# Windows gates that behind TWO switches, and the second is OFF by default:
#   Settings > Privacy & security > Location > Location services          (usually on)
#   Settings > Privacy & security > Location > Let desktop apps access... (usually OFF)
# Chrome is a desktop (non-packaged) app, so with the second one off it reports a
# permission denial no matter what the page or the site permission says - and there
# is nothing the dashboard can do about it from inside the browser.
$consent = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\location"
try {
  Set-ItemProperty -Path $consent -Name Value -Value "Allow" -ErrorAction Stop
  OK "location services: Allow"
} catch { Nope "could not set location services: $($_.Exception.Message)" }
try {
  if (-not (Test-Path "$consent\NonPackaged")) { New-Item -Path "$consent\NonPackaged" -Force | Out-Null }
  Set-ItemProperty -Path "$consent\NonPackaged" -Name Value -Value "Allow" -ErrorAction Stop
  OK "desktop apps (Chrome) may use location"
} catch { Nope "could not allow desktop apps: $($_.Exception.Message)" }
try {
  $svc = Get-Service lfsvc -ErrorAction Stop
  if ($svc.StartType -eq 'Disabled') { Set-Service lfsvc -StartupType Manual }
  if ($svc.Status -ne 'Running') { Start-Service lfsvc -ErrorAction SilentlyContinue }
  Info "geolocation service (lfsvc): $((Get-Service lfsvc).Status)"
} catch { Info "geolocation service not available" }
Info "Chrome will still ask once per profile - tap Allow when the map requests it"

Write-Host "`n[5/5] Auto-grant location to the dashboard (no prompt)" -ForegroundColor Cyan
# Even with Windows allowing location, Chrome still asks PER ORIGIN. On a fullscreen
# handheld that prompt is easy to miss and awkward to dismiss, so the map silently
# never got a fix. Chrome's own policy settles it before the first page load.
#
# GeolocationAllowedForUrls whitelists specific origins - deliberately NOT
# DefaultGeolocationSetting, which would allow every site on the machine. The pattern
# omits the port, which in Chrome's URL-pattern syntax matches ANY port, so it keeps
# working if the launcher has to move off 8080.
#
# This lives here rather than in neptune.ps1 because HKCU\Software\Policies is
# ACL-protected: policy writes require elevation even in the user hive.
$chromePol = "HKLM:\SOFTWARE\Policies\Google\Chrome"
try {
  $geoKey = "$chromePol\GeolocationAllowedForUrls"
  if (-not (Test-Path $geoKey)) { New-Item -Path $geoKey -Force | Out-Null }
  foreach ($v in (Get-Item $geoKey).Property) { Remove-ItemProperty -Path $geoKey -Name $v -ErrorAction SilentlyContinue }
  # The PORT IS REQUIRED. A Chrome content-settings pattern with no port matches the
  # scheme's DEFAULT port (80) - not "any port", which is what it looks like. Written
  # as bare "http://localhost" the policy silently never matched http://localhost:8080
  # and the map prompted on every launch. Enumerate the launcher's whole port range
  # (it advances from 8080 when a port is busy), for both loopback spellings.
  $i = 0
  foreach ($p in 8080..8091) {
    foreach ($h in @("http://localhost:$p", "http://127.0.0.1:$p")) {
      $i++
      New-ItemProperty -Path $geoKey -Name "$i" -Value $h -PropertyType String -Force | Out-Null
    }
  }
  OK "Chrome will auto-allow location for localhost:8080-8091 ($i patterns)"
  Info "scope is loopback only - the page we serve ourselves, nothing else"
} catch { Nope "could not set the Chrome geolocation policy: $($_.Exception.Message)" }
# Edge shares the Chromium policy schema, in case the launcher falls back to it.
try {
  $edgeKey = "HKLM:\SOFTWARE\Policies\Microsoft\Edge\GeolocationAllowedForUrls"
  if (-not (Test-Path $edgeKey)) { New-Item -Path $edgeKey -Force | Out-Null }
  $j = 0
  foreach ($p in 8080..8091) {
    foreach ($h in @("http://localhost:$p", "http://127.0.0.1:$p")) {
      $j++
      New-ItemProperty -Path $edgeKey -Name "$j" -Value $h -PropertyType String -Force | Out-Null
    }
  }
  Info "same policy applied to Edge (fallback browser)"
} catch { }

Write-Host "`n---- result ----" -ForegroundColor Magenta
if ($nic) {
  Get-NetIPAddress -InterfaceAlias $Adapter -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Select-Object InterfaceAlias, IPAddress, PrefixLength, PrefixOrigin | Format-Table -AutoSize
} else { Write-Host "  (tether adapter not present - plug it in and re-run for steps 1-3)" -ForegroundColor Yellow }

Write-Host "Done. Unplug/replug the tether once so the power change takes effect." -ForegroundColor Magenta
Write-Host "The Pi should now answer at 192.168.42.1 - check with:  ping 192.168.42.1`n" -ForegroundColor DarkGray
