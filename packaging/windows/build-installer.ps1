<#
.SYNOPSIS
    Builds Sonorium-Setup-<version>.exe from a private-Python bundle (build-python.ps1).

.DESCRIPTION
    Publishes the .NET programs (tray console, host, secret helper, updater) into the bundle
    folder, adds the licence files, and compiles packaging\windows\Sonorium.iss with Inno Setup.

.PARAMETER Bundle
    build-python.ps1's output folder (python\, app\). The programs are added to it.
.PARAMETER Version
    The version (the server package's, e.g. 1.6.3 or 1.6.3-dev.4).
.PARAMETER Out
    Folder for the installer.
#>
param(
    [Parameter(Mandatory)] [string] $Bundle,
    [Parameter(Mandatory)] [string] $Version,
    [Parameter(Mandatory)] [string] $Out
)
$ErrorActionPreference = 'Stop'
$repo = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$Bundle = (Resolve-Path $Bundle).Path
New-Item -ItemType Directory -Force $Out | Out-Null

# .NET wants a numeric version; the full one goes in the informational version (Updates.CurrentVersion)
$numeric = $Version -replace '-.*$', ''
$dotnet = if ($env:DOTNET_EXE) { $env:DOTNET_EXE } else { 'dotnet' }
foreach ($project in 'SonoriumHost', 'SonoriumSecrets', 'SonoriumUpdater', 'Sonorium.Tray') {
    & $dotnet publish (Join-Path $repo "desktop\windows\$project") -c Release -o $Bundle `
        "-p:Version=$numeric" "-p:InformationalVersion=$Version" -p:IncludeSourceRevisionInInformationalVersion=false
    if ($LASTEXITCODE) { throw "$project failed ($LASTEXITCODE)" }
}
Get-ChildItem $Bundle -Filter *.pdb | Remove-Item
# The tray console's pages (compiled XAML) are in Sonorium.pri; without it it can't start
if (-not (Test-Path (Join-Path $Bundle 'Sonorium.pri'))) { throw 'Sonorium.pri is missing from the build' }
Copy-Item (Join-Path $repo 'LICENSE.md'), (Join-Path $repo 'THIRD_PARTY_NOTICES.md') $Bundle

$iscc = @(
    (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source,
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 (ISCC.exe) not found' }
& $iscc "/DAppVersion=$Version" "/DSource=$Bundle" "/O$((Resolve-Path $Out).Path)" (Join-Path $PSScriptRoot 'Sonorium.iss')
if ($LASTEXITCODE) { throw "Inno Setup failed ($LASTEXITCODE)" }
Get-ChildItem $Out -Filter "Sonorium-Setup-$Version.exe" | ForEach-Object { "$($_.FullName)  $([math]::Round($_.Length / 1MB, 1)) MB" }
