# Neptune — topside launcher (ROG Ally / Windows)

**One thing to run.** Double‑click **`Neptune.bat`**. It walks through every step itself,
skipping anything already done:

1. **Find the Pi** — probes `192.168.42.1` (the fixed tether address), then `neptune.local`,
   then whatever was saved last, and uses the **first one that actually answers `/api/status`**.
2. **Desktop shortcut** — creates a **Neptune** icon (trident) on the desktop.
3. **Local server** — a small *concurrent* static server on `127.0.0.1` (no admin, no deps).
4. **Dashboard** — opens **Chrome → Edge** (first found) **fullscreen**, pointed at the Pi.

The Pi is plain HTTP (sealed tether, no TLS), so there's **no certificate to deal with**.

The dashboard is **offline‑first**: if no Pi answers, it still opens and runs, and connects by
itself the moment the Pi appears. A missing Pi is never a launch failure.

---

## ⚡ First time only: set up the tether

A direct Ally↔Pi Ethernet cable has **no DHCP server**. Left on automatic, Windows falls back to
a `169.254.x.x` link‑local address and the Pi does the same — so neither can find the other, and
the dashboard shows "no connection" with the cable plugged in.

Run this **once**, as Administrator (it will prompt):

```powershell
client\launch\tether-setup.ps1
```

It does five things:

| | What | Why |
|---|---|---|
| 1 | Ethernet adapter → **`192.168.42.2/24`** | matches the Pi's fixed `192.168.42.1`; no DHCP, no mDNS needed |
| 2 | **USB selective suspend → off** | Windows was power‑suspending the USB tether NIC mid‑session, which drops the link and looks exactly like the Pi dying |
| 3 | **Adapter power‑down → disabled** | same reason, via the driver's own power management |
| 4 | **Location → allowed for desktop apps** | Windows keeps this *second* switch off by default. Chrome is a desktop app, so with it off `navigator.geolocation` is denied no matter what the page asks, and the map can never take an origin |
| 5 | **Chrome auto‑granted location for `http://localhost`** | otherwise Chrome asks per‑origin, and that prompt is easy to miss on a fullscreen handheld. Scoped to loopback only — the page we serve ourselves |

> Step 5 lives here rather than in the launcher because `HKCU\Software\Policies` is
> ACL‑protected: Chrome policy writes need elevation even in the user hive.
> Verified on a fresh Chrome profile: no prompt, fix returned at ±58 m.

Undo with `tether-setup.ps1 -Revert`. No gateway is set on the tether, so **Wi‑Fi stays your
internet path** — the cable only reaches the Pi.

> The Pi side is handled by `install.sh`, which pins `192.168.42.1/24` on `eth0` *in addition to*
> DHCP. Plugged into a router it still takes a lease; on the tether it always holds that address.

---

## Options

- `Neptune.bat -Stop` — **close a stuck dashboard and free the port.** Use this if anything
  looks wedged; it is always safe.
- `Neptune.bat -PiHost 192.168.42.1` — skip discovery, use this address.
- `Neptune.bat -Port 8090` — different local port (it auto‑advances if one is busy anyway).
- `Neptune.bat -Setup` — steps 1–2 only, don't launch.
- `Neptune.bat -Kiosk` — locked kiosk window. **Not recommended on the handheld** (see below).

## Why it is not a kiosk any more

`--kiosk` removes every window control. On a device with **no physical keyboard**, "press Alt+F4
to exit" is not an exit — once it was up, the only way out was a hard reboot. Two changes:

- the default is now a **fullscreen app window**, which can be closed and Alt‑Tabbed away from;
- the dashboard has an **EXIT button** (top right) that stops the local server and closes the window.

## Getting unstuck

The launcher is single‑instance and cleans up after itself, so this should not happen. If it ever
does, `Neptune.bat -Stop` fixes it without a reboot:

- It refuses to start a **second** server (a global mutex), instead of the old behaviour where the
  second instance threw, hit a blocking `Read-Host`, and waited forever *behind* a fullscreen window.
- It **kills leftover Neptune browser windows** before launching. A window orphaned by a previous
  run owns Chromium's process‑singleton for this profile, so a new launch would hand its command
  line to the orphan and exit instantly — which the old script read as "the browser closed" and
  used to shut its own web server down ~180 ms after starting.
- Liveness is decided by **scanning for browser processes using our own profile directory**, never
  by the PID `Start-Process` returned (that PID legitimately exits immediately in the case above).
- It only ever touches browsers launched with **our** `--user-data-dir`, so your own browser
  windows are never closed.
- On exit it stops the browser **and** the listener, so the port is always free for the next launch.

## Files

| File | What |
|---|---|
| `Neptune.bat` | double‑click launcher (runs the script) |
| `neptune.ps1` | discovery + shortcut + concurrent static server + fullscreen browser |
| `tether-setup.ps1` | **run once as admin** — fixed tether IP, USB power fixes, location auto-grant |
| `crash-diagnostics.ps1` | **run as admin** — kernel crash dumps + GPU timeout headroom |
| `neptune-host.txt` | last known Pi address (discovery overwrites it when it finds a live one) |

**Chrome, then Edge.** Brave is no longer used. If Chrome isn't installed the launcher falls back
to Edge, which is the same Chromium engine and works identically.
