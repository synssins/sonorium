<#
.SYNOPSIS
    Builds Sonorium's private Python for Windows: the embeddable CPython with
    Sonorium's packages and Sonorium itself, in one folder that needs nothing
    installed on the PC (no Python, no PATH, no registry).

.DESCRIPTION
    Output layout (copied as-is into C:\Program Files\Sonorium by the installer):

        <Out>\python\          embeddable CPython + Lib\site-packages
        <Out>\app\sonorium\    the sonorium package (the image's /app/sonorium)
        <Out>\app\themes\      bundled themes, copied to the themes folder on first start
        <Out>\app\*.png        logo and icons, as in the image's /app

    Packages are the Docker image's list (docker/Dockerfile). PyAV is the wheel
    built against Sonorium's LGPL FFmpeg (build-pyav.ps1), never PyAV's own wheel.

    Runs on a Windows machine with the same Python minor version installed
    (CI: actions/setup-python), whose pip installs the wheels into the folder.

.PARAMETER AvWheel
    The PyAV wheel from build-pyav.ps1.
.PARAMETER Out
    The output folder (emptied first).
#>
param(
    [Parameter(Mandatory)] [string] $AvWheel,
    [Parameter(Mandatory)] [string] $Out
)
$ErrorActionPreference = 'Stop'

# Python 3.13: the last 3.12 with Windows downloads was 3.12.10 (3.12 is
# source-only since), and PyAV 19 needs 3.12 or later.
$PyVersion = '3.13.16'
$PySha256 = '97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297'

$repo = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$hostVersion = (& python -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
if ($hostVersion -ne ($PyVersion -replace '\.\d+$', '')) {
    throw "The Python running this script is $hostVersion; it must be $($PyVersion -replace '\.\d+$', '') to install matching wheels"
}

if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
$py = Join-Path $Out 'python'
$app = Join-Path $Out 'app'
New-Item -ItemType Directory -Force $py, $app | Out-Null

# 1. The embeddable distribution
$zip = Join-Path ([IO.Path]::GetTempPath()) "python-$PyVersion-embed-amd64.zip"
Invoke-WebRequest "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip" -OutFile $zip -UseBasicParsing
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($hash -ne $PySha256) { throw "python-$PyVersion-embed-amd64.zip: sha256 $hash, expected $PySha256" }
Expand-Archive $zip -DestinationPath $py
Remove-Item $zip

# 2. Search path: the standard library, site-packages and Sonorium. A ._pth
# file also makes Python ignore PYTHONPATH, PYTHONHOME and the user's
# site-packages, so another Python on the PC can't leak in.
$ver = ($PyVersion -split '\.')[0..1] -join ''
@(
    "python$ver.zip"
    '.'
    'Lib\site-packages'
    '..\app'
    'import site'
) | Set-Content -Encoding ascii (Join-Path $py "python$ver._pth")

# 3. Packages: the Docker image's list and pins, wheels only. pip is included
# because plugins may install their own packages (sonorium/plugins/packages.py).
$site = Join-Path $py 'Lib\site-packages'
$packages = @(
    'pyyaml', 'appdirs', 'cachetools', 'fastapi', 'uvicorn[standard]', 'pydantic', 'httpx', 'httpx-retries',
    'websockets', 'python-multipart', 'soco',
    'numpy<2.4',
    'paho-mqtt', 'pydantic-settings', 'pychromecast',
    'aiohttp', 'zeroconf', 'async-upnp-client', 'pyatv',
    'cryptography', 'segno', 'argon2-cffi',
    'pip'
)
& python -m pip install --no-cache-dir --disable-pip-version-check --only-binary=:all: `
    --target $site (Resolve-Path $AvWheel) @packages
if ($LASTEXITCODE) { throw "pip install failed ($LASTEXITCODE)" }
# pip --target leaves console-script launchers that point at the build machine's Python
Remove-Item -Recurse -Force (Join-Path $site 'bin') -ErrorAction SilentlyContinue

# 4. Sonorium, laid out as in the image's /app
$addon = Join-Path $repo 'sonorium_addon'
Copy-Item -Recurse (Join-Path $addon 'sonorium') (Join-Path $app 'sonorium')
Copy-Item -Recurse (Join-Path $addon 'themes') (Join-Path $app 'themes')
foreach ($f in 'logo.png', 'icon.png', 'favicon.png', 'display.png') {
    Copy-Item (Join-Path $addon $f) $app
}
Get-ChildItem -Recurse -Directory -Filter '__pycache__' $Out | Remove-Item -Recurse -Force
# Tests are not part of the program
Remove-Item -Recurse -Force (Join-Path $app 'sonorium\tests') -ErrorAction SilentlyContinue

# 5. Check it runs on its own: the private Python, with no environment from
# the build machine, imports Sonorium and the LGPL PyAV.
$check = @'
import sys, av, av._core as c
lic = c.library_meta['libavcodec']['license']
print('Python', sys.version.split()[0], 'PyAV', av.__version__, 'FFmpeg', av.ffmpeg_version_info, lic)
assert lic.startswith('LGPL'), lic
# The licence string alone isn't proof: PyAV's own Windows wheel reports LGPL too while
# configured with x264/x265. Require Sonorium's own configuration (build-ffmpeg.sh).
conf = c.library_meta['libavcodec']['configuration']
assert '--disable-gpl' in conf and '--disable-everything' in conf, conf
assert 'x264' not in conf and 'x265' not in conf, conf
assert av.codec.Codec('mp3', 'w').name == 'libmp3lame'
assert sys.prefix.lower().startswith(sys.argv[1].lower()), sys.prefix
import sonorium.mp3_out
from sonorium.version import __version__
print('Sonorium', __version__)
'@
$env:PYTHONPATH = $null
& (Join-Path $py 'python.exe') -I -c $check (Resolve-Path $py)
if ($LASTEXITCODE) { throw "The private Python failed its check ($LASTEXITCODE)" }
