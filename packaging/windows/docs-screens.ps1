<#
.SYNOPSIS
    Documentation screenshots of the Windows app, taken on a CI runner (never on a desktop in use).

.DESCRIPTION
    Installs Sonorium with the real installer wizard (for all users, as a service), then opens the
    tray menu and each console page, and saves for every screen:
      <name>.png   the window (PrintWindow) or the screen
      <name>.json  every named control on it: name, type, and rectangle in the PNG's pixels
    The numbered callouts are drawn afterwards from the JSON.

    Windows PowerShell 5.1 (UI Automation is a .NET Framework library). Dark mode, 100 % scale.

.PARAMETER Installer
    Sonorium-Setup-<version>.exe to install.
.PARAMETER Out
    Folder for the screenshots.
#>
param(
    [Parameter(Mandatory)] [string] $Installer,
    [Parameter(Mandatory)] [string] $Out
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force $Out | Out-Null
Add-Type -AssemblyName System.Drawing, UIAutomationClient, UIAutomationTypes
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Collections.Generic; using System.Text;
public static class W {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr v);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  public struct RECT { public int L, T, R, B; }
  public static List<IntPtr> Of(uint pid) { var list = new List<IntPtr>(); EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p); if (p == pid && IsWindowVisible(h)) list.Add(h); return true; }, IntPtr.Zero); return list; }
  public static List<IntPtr> All() { var list = new List<IntPtr>(); EnumWindows((h, l) => { if (IsWindowVisible(h)) list.Add(h); return true; }, IntPtr.Zero); return list; }
  public static string Title(IntPtr h) { var s = new StringBuilder(256); GetWindowText(h, s, 256); return s.ToString(); }
}
"@
[void][W]::SetProcessDpiAwarenessContext([IntPtr](-4))
$UIA = [System.Windows.Automation.AutomationElement]
$Types = [System.Windows.Automation.ControlType]

function Rect($hwnd) { $r = New-Object W+RECT; [void][W]::GetWindowRect($hwnd, [ref]$r); $r }

# Every named control in a window, with its rectangle relative to the window (= the PNG)
function Controls($hwnd) {
    $win = Rect $hwnd
    $root = $UIA::FromHandle($hwnd)
    $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    $items = foreach ($e in $all) {
        try {
            $c = $e.Current
            if ($c.IsOffscreen -or $c.BoundingRectangle.IsEmpty) { continue }
            $b = $c.BoundingRectangle
            [pscustomobject]@{ name = $c.Name; type = $c.ControlType.ProgrammaticName -replace 'ControlType\.', ''; id = $c.AutomationId
                x = [int]($b.X - $win.L); y = [int]($b.Y - $win.T); w = [int]$b.Width; h = [int]$b.Height }
        } catch { }
    }
    @($items | Where-Object { $_.name -or $_.id })
}

function Shot-Window($hwnd, $name) {
    $r = Rect $hwnd
    $bmp = New-Object System.Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
    $g = [System.Drawing.Graphics]::FromImage($bmp); $dc = $g.GetHdc()
    [void][W]::PrintWindow($hwnd, $dc, 2); $g.ReleaseHdc($dc); $g.Dispose()
    $bmp.Save((Join-Path $Out "$name.png")); $bmp.Dispose()
    Controls $hwnd | ConvertTo-Json -Depth 3 | Set-Content -Encoding utf8 (Join-Path $Out "$name.json")
    "  $name.png  $($r.R - $r.L)x$($r.B - $r.T)"
}

function Shot-Screen($name, $x, $y, $w, $h) {
    $bmp = New-Object System.Drawing.Bitmap $w, $h
    $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($x, $y, 0, 0, $bmp.Size); $g.Dispose()
    $bmp.Save((Join-Path $Out "$name.png")); $bmp.Dispose()
    "  $name.png (screen)"
}

function Wait-Window([scriptblock] $match, $seconds = 60) {
    for ($i = 0; $i -lt $seconds * 2; $i++) {
        $h = [W]::All() | Where-Object { & $match $_ } | Select-Object -First 1
        if ($h) { Start-Sleep -Milliseconds 800; return $h }
        Start-Sleep -Milliseconds 500
    }
    throw "Window not found"
}

function Find($hwnd, $name) {
    $cond = New-Object System.Windows.Automation.PropertyCondition ($UIA::NameProperty, $name)
    $e = $UIA::FromHandle($hwnd).FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
    if (-not $e) { throw "No '$name' in $([W]::Title($hwnd))" }
    $e
}
function Click($hwnd, $name) { (Find $hwnd $name).GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); Start-Sleep 2 }
function Pick($hwnd, $name) { (Find $hwnd $name).GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select(); Start-Sleep 1 }
function Tick($hwnd, $name) {
    $t = (Find $hwnd $name).GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
    if ($t.Current.ToggleState -ne 'On') { $t.Toggle() }
    Start-Sleep 1
}

# --- installer wizard -------------------------------------------------------------------------
"Installer"
$setup = Start-Process $Installer -PassThru
$mode = Wait-Window { param($h) [W]::Title($h) -match 'Select Setup Install Mode' }
Shot-Window $mode 'install-01-mode'
Click $mode 'Install for all users'

$wizard = Wait-Window { param($h) [W]::Title($h) -match '^Setup - Sonorium' }
Shot-Window $wizard 'install-02-license'
Pick $wizard 'I accept the agreement'
Shot-Window $wizard 'install-02b-license-accepted'
Click $wizard 'Next >'
Shot-Window $wizard 'install-03-location'
Click $wizard 'Next >'
Shot-Window $wizard 'install-04-tasks'
Tick $wizard 'Run Sonorium as a Windows service (starts with Windows, before anyone signs in)'
Shot-Window $wizard 'install-04b-tasks-ticked'
Click $wizard 'Next >'
Shot-Window $wizard 'install-05-ready'
Click $wizard 'Install'
for ($i = 0; $i -lt 240; $i++) { try { [void](Find $wizard 'Finish'); break } catch { Start-Sleep 1 } }
Shot-Window $wizard 'install-06-finish'
Click $wizard 'Finish'
Start-Sleep 25

# --- tray -------------------------------------------------------------------------------------
"Tray"
Add-Type -AssemblyName System.Windows.Forms
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
Shot-Screen 'tray-01-taskbar' ($b.Width - 640) ($b.Height - 120) 640 120
$program = 'C:\Program Files\Sonorium\Sonorium.exe'
Get-Process Sonorium -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep 2
[void][W]::SetCursorPos($b.Width - 200, $b.Height - 30)
$tray = Start-Process $program -ArgumentList '--menu' -PassThru
Start-Sleep 10
foreach ($h in [W]::Of([uint32]$tray.Id)) { if ([W]::Title($h) -ne 'Sonorium') { Shot-Window $h 'tray-02-menu' } }
Shot-Screen 'tray-03-menu-on-screen' ($b.Width - 640) ($b.Height - 700) 640 700

# --- console pages ----------------------------------------------------------------------------
"Console"
foreach ($page in 'Status', 'General', 'Folders', 'Connections', 'Updates', 'Logs', 'About') {
    Get-Process Sonorium -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep 2
    # The Updates page shows an available update: the console acts as an older version
    $env:SONORIUM_PRETEND_VERSION = if ($page -eq 'Updates') { '1.7.0' } else { '' }
    $p = Start-Process $program -ArgumentList '--page', $page -PassThru
    $console = Wait-Window { param($h) [W]::Title($h) -eq 'Sonorium' -and (Rect $h).R - (Rect $h).L -gt 600 }
    Start-Sleep 6
    Shot-Window $console ('console-' + $page.ToLower())
}
Get-Process Sonorium -ErrorAction SilentlyContinue | Stop-Process -Force
"Done"
