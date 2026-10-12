using System.Runtime.InteropServices;
using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using Sonorium.Desktop;
using Windows.Foundation;
using Windows.Graphics;

namespace Sonorium.Tray;

public sealed partial class TrayMenu : Window
{
    readonly AppState state = AppState.Current;

    /// <summary>Development (--menu): stays open when another window takes the focus.</summary>
    public bool StayOpen { get; set; }

    public TrayMenu()
    {
        InitializeComponent();
        SystemBackdrop = new DesktopAcrylicBackdrop();
        var presenter = OverlappedPresenter.CreateForContextMenu();
        AppWindow.SetPresenter(presenter);
        AppWindow.IsShownInSwitchers = false;
        Activated += (_, e) =>
        {
            if (e.WindowActivationState == WindowActivationState.Deactivated && !StayOpen) AppWindow.Hide();
        };
        state.Changed += Refresh;
        Refresh();
    }

    void Refresh()
    {
        var starting = !state.Running && state.Host.IsRunning;
        StatusText.Text = state.Running ? "Sonorium is running" : starting ? "Sonorium is starting" : "Sonorium is stopped";
        StatusDot.Fill = new SolidColorBrush(state.Running ? Windows.UI.Color.FromArgb(255, 15, 123, 15)
            : starting ? Windows.UI.Color.FromArgb(255, 157, 93, 0) : Windows.UI.Color.FromArgb(255, 138, 138, 138));
        var channels = state.ChannelsPlaying switch
        {
            0 => null,
            1 => "1 channel playing",
            var n => $"{n} channels playing",
        };
        StatusDetail.Text = channels is null ? state.Address : $"{channels} · {state.Address}";
        var stopped = !state.Running && !starting;
        StopStartText.Text = stopped ? "Start Sonorium" : "Stop Sonorium";
        StopStartIcon.Glyph = stopped ? "" : "";
        UpdateDot.Visibility = state.Update is null ? Visibility.Collapsed : Visibility.Visible;
    }

    /// <summary>Shows the menu with its bottom right corner at the cursor, kept on the screen.</summary>
    public void ShowAtCursor()
    {
        Refresh();
        GetCursorPos(out var cursor);
        var area = DisplayArea.GetFromPoint(new PointInt32(cursor.X, cursor.Y), DisplayAreaFallback.Nearest).WorkArea;
        GetDpiForMonitor(MonitorFromPoint(cursor, 2 /* nearest */), 0 /* effective */, out var dpi, out _);
        var scale = dpi / 96.0;
        Root.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
        // The content area gets the menu's full size; the window's border comes on top of it
        AppWindow.ResizeClient(new SizeInt32(
            (int)Math.Ceiling(Root.DesiredSize.Width * scale),
            (int)Math.Ceiling(Root.DesiredSize.Height * scale)));
        var outer = AppWindow.Size;
        var x = Math.Clamp(cursor.X - outer.Width, area.X, area.X + area.Width - outer.Width);
        var y = Math.Clamp(cursor.Y - outer.Height, area.Y, area.Y + area.Height - outer.Height);
        AppWindow.Move(new PointInt32(x, y));
        AppWindow.Show();
        Activate();
        SetForegroundWindow(WinRT.Interop.WindowNative.GetWindowHandle(this));
    }

    void Done() => AppWindow.Hide();

    void Open_Click(object sender, RoutedEventArgs e) { Done(); Actions.OpenSonorium(); }
    void Settings_Click(object sender, RoutedEventArgs e) { Done(); Actions.ShowConsole("Status"); }
    void Restart_Click(object sender, RoutedEventArgs e) { Done(); Actions.Restart(); }
    void StopStart_Click(object sender, RoutedEventArgs e)
    {
        Done();
        if (StopStartText.Text == "Start Sonorium") Actions.Start(); else Actions.Stop();
    }
    void Updates_Click(object sender, RoutedEventArgs e) { Done(); Actions.ShowConsole("Updates", checkNow: true); }
    void Logs_Click(object sender, RoutedEventArgs e) { Done(); Actions.ShowConsole("Logs"); }
    void Help_Click(object sender, RoutedEventArgs e) { Done(); Actions.OpenUrl("https://sonorium.app"); }
    void Quit_Click(object sender, RoutedEventArgs e) { Done(); Actions.Quit(); }

    [StructLayout(LayoutKind.Sequential)]
    struct POINT { public int X, Y; }

    [DllImport("user32.dll")] static extern bool GetCursorPos(out POINT point);
    [DllImport("user32.dll")] static extern IntPtr MonitorFromPoint(POINT point, uint flags);
    [DllImport("shcore.dll")] static extern int GetDpiForMonitor(IntPtr monitor, int type, out uint x, out uint y);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr hwnd);
}
