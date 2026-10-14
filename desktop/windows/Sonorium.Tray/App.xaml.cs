using System.Runtime.InteropServices;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Sonorium.Desktop;

namespace Sonorium.Tray;

public partial class App : Application
{
    static App? current;
    TrayIcon? icon;
    TrayMenu? menu;
    DispatcherQueue? ui;

    public App()
    {
        current = this;
        InitializeComponent();
        // Lives in the notification area: no window needs to be open
        DispatcherShutdownMode = DispatcherShutdownMode.OnExplicitShutdown;
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        ui = DispatcherQueue.GetForCurrentThread();
        var state = AppState.Current;
        state.Attach(ui);
        Notifications.Register();

        // Starting the tray starts Sonorium: per user at sign-in, and the service when Quit stopped it
        if (!state.Host.IsRunning)
        {
            try { state.Host.Start(); }
            catch (Exception e) { Notifications.ShowError($"Sonorium couldn't start: {e.Message}"); }
        }

        menu = new TrayMenu();
        icon = new TrayIcon(TrayIconImage());
        icon.LeftClick += () => ui.TryEnqueue(() => Actions.ShowConsole("Status"));
        icon.RightClick += () => ui.TryEnqueue(() => menu.ShowAtCursor());
        icon.SettingsChanged += () => ui.TryEnqueue(() => icon.SetIcon(TrayIconImage()));
        state.Changed += () => icon.SetTip(state.Running ? "Sonorium is running" : "Sonorium is stopped");

        // General's "Open Sonorium in my browser when it starts": once, when it first answers
        if (state.Settings.OpenBrowserOnStart)
        {
            void OpenOnce()
            {
                if (!state.Running) return;
                state.Changed -= OpenOnce;
                Actions.OpenSonorium();
            }
            state.Changed += OpenOnce;
        }

        Updater.Start();
        var startArgs = Environment.GetCommandLineArgs();
        if (startArgs.Contains("--settings")) Actions.ShowConsole("Status");
        // Development (documentation screenshots): --page Updates opens the console on that page
        var page = Array.IndexOf(startArgs, "--page");
        if (page >= 0 && page + 1 < startArgs.Length) Actions.ShowConsole(startArgs[page + 1]);
        // Development: open the menu as a right click would (screenshots, tests)
        if (startArgs.Contains("--menu"))
        {
            menu.StayOpen = true;
            ui.TryEnqueue(DispatcherQueuePriority.Low, () => menu.ShowAtCursor());
        }
    }

    /// <summary>Sonorium started again from the Start menu, or a notification was clicked.</summary>
    public static void OnReactivated() => current?.ui?.TryEnqueue(() =>
    {
        if (!AppState.Current.Host.IsRunning) Actions.Start();
        Actions.ShowConsole("Status");
    });

    /// <summary>
    /// The Sonorium icon (the app's own, in colour: it reads on a light and a dark taskbar alike),
    /// at the notification area's size for the current display scale.
    /// </summary>
    static IntPtr TrayIconImage()
    {
        var file = Path.Combine(AppContext.BaseDirectory, "Assets", "Sonorium.ico");
        var size = GetSystemMetricsForDpi(49 /* SM_CXSMICON */, GetDpiForSystem());
        return LoadImage(IntPtr.Zero, file, 1 /* IMAGE_ICON */, size, size, 0x10 /* LR_LOADFROMFILE */);
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr LoadImage(IntPtr instance, string name, uint type, int cx, int cy, uint flags);
    [DllImport("user32.dll")] static extern int GetSystemMetricsForDpi(int index, uint dpi);
    [DllImport("user32.dll")] static extern uint GetDpiForSystem();
}
