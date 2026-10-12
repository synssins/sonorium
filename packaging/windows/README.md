# Sonorium for Windows: packaging

How the Windows desktop app is built. The programs themselves are in
[`desktop/windows`](../../desktop/windows).

## What's in the installer

| File | What it is |
|---|---|
| `Sonorium.exe` | The tray console: the icon, its menu and the settings window |
| `SonoriumHost.exe` | Runs Sonorium in the background: as the Windows service, or when you sign in |
| `SonoriumSecrets.exe` | Keeps the Home Assistant token and the MQTT password encrypted with Windows (DPAPI) |
| `SonoriumUpdater.exe` | Downloads and installs a new version |
| `python\` | Sonorium's own Python, with its packages; nothing is installed system-wide |
| `app\` | Sonorium itself and the bundled themes |

## Building

On Windows, in this order (CI does the same: [`.github/workflows/windows-app.yml`](../../.github/workflows/windows-app.yml)):

1. `build-ffmpeg.sh`, in an MSYS2 UCRT64 shell: Sonorium's audio-only LGPL FFmpeg, with the same
   versions and options as the Docker images.
2. `build-pyav.ps1`, in a Visual Studio developer shell: PyAV built against that FFmpeg.
3. `build-python.ps1`: Python 3.13 (embeddable), the packages and Sonorium in one folder. It checks
   that PyAV uses the LGPL build.
4. `build-installer.ps1`: the .NET programs (.NET 10 SDK) and `Sonorium-Setup-<version>.exe`
   (Inno Setup 6).

## Installing

- **Install for me only**: no administrator rights. Sonorium runs while you're signed in.
- **Install for all users**: asks for administrator rights. Sonorium can run as a Windows service,
  which starts with Windows before anyone signs in. The service runs as its own low-privilege
  account, `NT SERVICE\Sonorium`, and keeps its files in `C:\ProgramData\Sonorium`.

The installer isn't signed yet, so Windows SmartScreen shows **"Windows protected your PC"** with
**Unknown publisher** the first time. Select **More info**, then **Run anyway**.

## Updates

The tray console checks the GitHub releases of `synssins/sonorium` for a newer
`Sonorium-Setup-<version>.exe` (at start, every 6 hours, or when you ask). Stable sees releases;
Preview also sees pre-releases.

- **Install for me only:** the updater downloads the installer, checks it against the SHA-256 that
  GitHub reports for it, and runs it as you.
- **Install for all users:** Setup adds a scheduled task, **Sonorium Update**, that runs as SYSTEM
  and that only the user who installed Sonorium may start. The updater asks it to install, so
  there's no administrator prompt for each update. The task takes only the release asset named
  `Sonorium-Setup-<version>.exe` from `synssins/sonorium`, checks it against GitHub's SHA-256 before
  running anything, and runs nothing but that installer.

**Security note:** with the update task, whoever can publish releases to `synssins/sonorium` can
run an installer as SYSTEM on every all-users install. Protect write access to the repository the
same way you would administrator access to those PCs.

Silent install (what the updater runs): `Sonorium-Setup-<version>.exe /VERYSILENT /SUPPRESSMSGBOXES
/NORESTART /CURRENTUSER` (or `/ALLUSERS`, and `/TASKS=service` for the service).
