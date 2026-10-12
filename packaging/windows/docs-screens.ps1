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
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, int dx, int dy, uint data, IntPtr extra);
  public static void ClickAt(int x, int y) { SetCursorPos(x, y); mouse_event(2, 0, 0, 0, IntPtr.Zero); mouse_event(4, 0, 0, 0, IntPtr.Zero); }
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
# Invoke when the control offers it; else a mouse click in its middle (Windows command links, and
# the installer's dark-mode controls, which UI Automation sees as plain panes)
function Click($hwnd, $name) {
    $e = Find $hwnd $name
    $pattern = $null
    if ($e.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) { $pattern.Invoke() }
    else {
        [void][W]::SetForegroundWindow($hwnd); Start-Sleep -Milliseconds 300
        $b = $e.Current.BoundingRectangle
        [W]::ClickAt([int]($b.X + $b.Width / 2), [int]($b.Y + $b.Height / 2))
    }
    Start-Sleep 2
}
function Pick($hwnd, $name) { (Find $hwnd $name).GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select(); Start-Sleep 1 }
function Tick($hwnd, $name) {
    $t = (Find $hwnd $name).GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
    if ($t.Current.ToggleState -ne 'On') { $t.Toggle() }
    Start-Sleep 1
}

# --- installer wizard: for me only (up to the tasks page, then Cancel) ----------------------
"Installer, for me only"
$setup = Start-Process $Installer -PassThru
$mode = Wait-Window { param($h) [W]::Title($h) -match 'Select Setup Install Mode' }
Click $mode 'Install for me only (recommended)'
$wizard = Wait-Window { param($h) [W]::Title($h) -match '^Setup - Sonorium' }
Click $wizard 'I accept the agreement'
Click $wizard 'Next'
Shot-Window $wizard 'install-me-03-location'
Click $wizard 'Next'
Shot-Window $wizard 'install-me-04-tasks'
Click $wizard 'Cancel'
$confirm = Wait-Window { param($h) [W]::Title($h) -match 'Exit Setup' }
Click $confirm 'Yes'
$setup.WaitForExit(30000) | Out-Null
Start-Sleep 2

# --- installer wizard: for all users, as a service --------------------------------------------
"Installer"
$setup = Start-Process $Installer -PassThru
$mode = Wait-Window { param($h) [W]::Title($h) -match 'Select Setup Install Mode' }
Shot-Window $mode 'install-01-mode'
Click $mode 'Install for all users'

$wizard = Wait-Window { param($h) [W]::Title($h) -match '^Setup - Sonorium' }
Shot-Window $wizard 'install-02-license'
Click $wizard 'I accept the agreement'
Shot-Window $wizard 'install-02b-license-accepted'
Click $wizard 'Next'
Shot-Window $wizard 'install-03-location'
Click $wizard 'Next'
Shot-Window $wizard 'install-04-tasks'
# The task list isn't exposed to UI Automation: its second row (the service task), by position in the window
$r = Rect $wizard; [void][W]::SetForegroundWindow($wizard); Start-Sleep -Milliseconds 300
[W]::ClickAt($r.L + 57, $r.T + 177); Start-Sleep 1
Shot-Window $wizard 'install-04b-tasks-ticked'
Click $wizard 'Next'
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
# The icon starts in the hidden-icons flyout (the ^ by the clock): open it and capture it
$taskbar = [W]::All() | Where-Object { [W]::Title($_) -eq '' -and $UIA::FromHandle($_).Current.ClassName -eq 'Shell_TrayWnd' } | Select-Object -First 1
try {
    $chevron = $UIA::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
        (New-Object System.Windows.Automation.PropertyCondition ($UIA::NameProperty, 'Show Hidden Icons')))
    $cb = $chevron.Current.BoundingRectangle
    "  chevron at $($cb.X),$($cb.Y) $($cb.Width)x$($cb.Height)" | Out-Host
    @{ x = [int]$cb.X - ($b.Width - 640); y = [int]$cb.Y - ($b.Height - 420); w = [int]$cb.Width; h = [int]$cb.Height } |
        ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $Out 'tray-04-overflow-chevron.json')
    [W]::ClickAt([int]($cb.X + $cb.Width / 2), [int]($cb.Y + $cb.Height / 2)); Start-Sleep 2
    Shot-Screen 'tray-04-overflow' ($b.Width - 640) ($b.Height - 420) 640 420
    $icon = $UIA::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
        (New-Object System.Windows.Automation.PropertyCondition ($UIA::NameProperty, 'Sonorium is running')))
    if ($icon) {
        $ib = $icon.Current.BoundingRectangle
        @{ x = [int]$ib.X - ($b.Width - 640); y = [int]$ib.Y - ($b.Height - 420); w = [int]$ib.Width; h = [int]$ib.Height } |
            ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $Out 'tray-04-overflow-icon.json')
    }
    [W]::ClickAt([int]($cb.X + $cb.Width / 2), [int]($cb.Y + $cb.Height / 2)); Start-Sleep 1
} catch { "  overflow: $_" | Out-Host }
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
    # The Updates page shows an available update: the console acts as an older pre-release (Preview)
    $env:SONORIUM_PRETEND_VERSION = if ($page -eq 'Updates') { '1.7.0-dev.1' } else { '' }
    $p = Start-Process $program -ArgumentList '--page', $page -PassThru
    $console = Wait-Window { param($h) [W]::Title($h) -eq 'Sonorium' -and (Rect $h).R - (Rect $h).L -gt 600 }
    Start-Sleep 6
    if ($page -eq 'Updates') { Click $console 'Check now'; Start-Sleep 6 }
    if ($page -eq 'Connections') {
        # Both switches on, so the address and login fields show (nothing is saved while they're empty)
        # The switch, not the row title of the same name
        foreach ($name in 'Use Home Assistant', 'Use MQTT') {
            $cond = New-Object System.Windows.Automation.AndCondition (
                (New-Object System.Windows.Automation.PropertyCondition ($UIA::NameProperty, $name)),
                (New-Object System.Windows.Automation.PropertyCondition ($UIA::ControlTypeProperty, $Types::Button)))
            $e = $UIA::FromHandle($console).FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
            $toggle = $null
            if ($e.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$toggle)) { $toggle.Toggle() }
            else { $b = $e.Current.BoundingRectangle; [W]::ClickAt([int]($b.X + $b.Width / 2), [int]($b.Y + $b.Height / 2)) }
            Start-Sleep 1
        }
        Start-Sleep 2
    }
    Shot-Window $console ('console-' + $page.ToLower())
}
Get-Process Sonorium -ErrorAction SilentlyContinue | Stop-Process -Force
"Done"
