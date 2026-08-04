<#
  NEPTUNE - one-time tether setup for the ROG Ally. RUN AS ADMINISTRATOR, once.

  Three things, all of which need elevation:

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

  Undo with:  tether-setup.ps1 -Revert   (back to DHCP + default power settings)
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

$nic = Get-NetAdapter -Name $Adapter -ErrorAction SilentlyContinue
if (-not $nic) {
  Nope "no adapter named '$Adapter'. Available:"
  Get-NetAdapter | ForEach-Object { Write-Host "        $($_.Name)  [$($_.Status)]  $($_.InterfaceDescription)" }
  Write-Host "`nRe-run with:  tether-setup.ps1 -Adapter `"<name>`"" -ForegroundColor DarkGray
  return
}
Info "adapter: $($nic.Name) - $($nic.InterfaceDescription) [$($nic.Status)]"

# ---------------------------------------------------------------------------
if ($Revert) {
  Write-Host "`n[1/3] Address -> DHCP" -ForegroundColor Cyan
  netsh interface ip set address name="$Adapter" source=dhcp | Out-Null
  netsh interface ip set dns     name="$Adapter" source=dhcp | Out-Null
  OK "back to DHCP"

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
Write-Host "`n[1/3] Fixed tether address" -ForegroundColor Cyan
try {
  netsh interface ip set address name="$Adapter" static $Address $Mask | Out-Null
  OK "$Adapter = $Address/$Mask  (no gateway - the tether is point-to-point)"
  Info "Wi-Fi stays the internet path; the tether only reaches the Pi"
} catch {
  Nope "could not set the address: $($_.Exception.Message)"
}

Write-Host "`n[2/3] USB selective suspend" -ForegroundColor Cyan
# Windows suspends the USB NIC and the tether silently drops. On a vehicle control
# link that is not acceptable, so turn it off on both AC and battery.
powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
powercfg /setdcvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
powercfg /setactive SCHEME_CURRENT
OK "disabled on AC and battery"

Write-Host "`n[3/3] Adapter power management" -ForegroundColor Cyan
# 0x18 = disable "allow the computer to turn off this device to save power"
# and "allow this device to wake the computer".
$guid = '{4d36e972-e325-11ce-bfc1-08002be10318}'
$done = $false
Get-ChildItem "HKLM:\SYSTEM\CurrentControlSet\Control\Class\$guid" -ErrorAction SilentlyContinue | ForEach-Object {
  $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
  if ($p.DriverDesc -eq $nic.InterfaceDescription) {
    Set-ItemProperty -Path $_.PSPath -Name PnPCapabilities -Value 24 -Type DWord
    OK "power-down disabled for '$($p.DriverDesc)' (key $($_.PSChildName))"
    $done = $true
  }
}
if (-not $done) { Nope "could not find the driver key - set it by hand in Device Manager > Power Management" }

try {
  Disable-NetAdapterPowerManagement -Name $Adapter -ErrorAction Stop
  OK "Disable-NetAdapterPowerManagement applied"
} catch { Info "adapter exposes no WMI power settings (registry change above still applies)" }

Write-Host "`n---- result ----" -ForegroundColor Magenta
Get-NetIPAddress -InterfaceAlias $Adapter -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Select-Object InterfaceAlias, IPAddress, PrefixLength, PrefixOrigin | Format-Table -AutoSize

Write-Host "Done. Unplug/replug the tether once so the power change takes effect." -ForegroundColor Magenta
Write-Host "The Pi should now answer at 192.168.42.1 - check with:  ping 192.168.42.1`n" -ForegroundColor DarkGray
