# Neptune — topside launcher (ROG Ally / Windows)

**One thing to run.** Double‑click **`Neptune.bat`** the first time and it walks through every
step itself, in order, skipping anything already done:

1. **Pi address** — asks once for the Pi's IP and remembers it (in `neptune-host.txt`).
2. **Desktop shortcut** — creates a **Neptune** icon on the desktop (trident) so future launches
   are one click.
3. **Local server** — starts a tiny static server on `127.0.0.1` (no admin, no Python, no deps).
4. **Dashboard** — opens **Brave → Chrome → Edge** (first one found) **fullscreen**, pointed at
   the Pi. **Alt+F4** closes the app and stops the server.

The Pi is plain HTTP (sealed tether, no TLS), so there's **no certificate to deal with**.

After the first run, just use the **Neptune** icon on the desktop. Everything re‑checks itself
each launch, so it's safe to just click and trust it.

This keeps the Pi **backend‑only** — the Ally serves its own dashboard from `localhost` (a secure
origin, so geolocation/PWA work) and talks to the Pi's API/video over the tether.

## Set the Pi IP

Any one of these (first run will just ask you):
- put the IP in **`neptune-host.txt`** (one line), or
- `Neptune.bat -PiHost 192.168.1.88`, or
- edit `$DefaultHost` at the top of `neptune.ps1`.

## Options

- `Neptune.bat -NoKiosk` — fullscreen **app window** instead of locked kiosk (easier to Alt‑Tab
  out of while setting up).
- `Neptune.bat -Port 8090` — use a different local port if 8080 is taken.
- `Neptune.bat -Setup` — do steps 1–3 only (shortcut + cert), don't launch.

## Files

| File | What |
|---|---|
| `Neptune.bat` | double‑click launcher (runs the script) |
| `neptune.ps1` | the whole thing: shortcut + cert + server + fullscreen browser |
| `neptune-host.txt` | your Pi's IP |

No Edge required — it prefers **Brave**, then Chrome, then Edge.
