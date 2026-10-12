<#
.SYNOPSIS
    Builds PyAV for Windows against Sonorium's LGPL FFmpeg (build-ffmpeg.sh).

.DESCRIPTION
    PyAV's own Windows wheel carries a full FFmpeg with GPL video encoders, so
    Sonorium builds PyAV from source with MSVC, linked to its own audio-only
    FFmpeg, and puts those FFmpeg DLLs inside the wheel (delvewheel, as PyAV's
    own wheels do). Run from an MSVC developer shell (CI: ilammy/msvc-dev-cmd),
    with the Python that the private Python will match.

.PARAMETER FFmpeg
    build-ffmpeg.sh's PREFIX (bin\, lib\, include\).
.PARAMETER Out
    Folder for the finished wheel.
#>
param(
    [Parameter(Mandatory)] [string] $FFmpeg,
    [Parameter(Mandatory)] [string] $Out
)
$ErrorActionPreference = 'Stop'
$AvVersion = '19.0.1'

$FFmpeg = (Resolve-Path $FFmpeg).Path
$bin = Join-Path $FFmpeg 'bin'
$lib = Join-Path $FFmpeg 'lib'
# FFmpeg's MinGW build installs the MSVC import libraries (avcodec.lib, ...) next to the DLLs
Get-ChildItem $bin -Filter '*.lib' | Copy-Item -Destination $lib -Force
foreach ($name in 'avformat', 'avcodec', 'avdevice', 'avutil', 'avfilter', 'swscale', 'swresample') {
    if (-not (Test-Path (Join-Path $lib "$name.lib"))) { throw "$name.lib missing in $lib" }
}

$work = Join-Path ([IO.Path]::GetTempPath()) 'pyav-build'
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
New-Item -ItemType Directory -Force $work, $Out | Out-Null

# PyAV's setup.py on Windows takes FFmpeg from MSVC's INCLUDE and LIB paths
$env:INCLUDE = "$(Join-Path $FFmpeg 'include');$env:INCLUDE"
$env:LIB = "$lib;$env:LIB"
& python -m pip install --disable-pip-version-check delvewheel
& python -m pip wheel --no-cache-dir --no-deps --no-binary av -w $work "av==$AvVersion"
if ($LASTEXITCODE) { throw "PyAV build failed ($LASTEXITCODE)" }

# Put the FFmpeg DLLs inside the wheel and have av\__init__ find them
$wheel = Get-ChildItem $work -Filter 'av-*.whl' | Select-Object -First 1
& python -m delvewheel repair --add-path $bin -w $Out $wheel.FullName
if ($LASTEXITCODE) { throw "delvewheel failed ($LASTEXITCODE)" }
Get-ChildItem $Out -Filter 'av-*.whl' | ForEach-Object { $_.FullName }
