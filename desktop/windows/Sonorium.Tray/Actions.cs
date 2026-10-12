using System.Diagnostics;
using Microsoft.UI.Xaml;
using Sonorium.Desktop;

namespace Sonorium.Tray;

/// <summary>What the menu, the console and the notifications do.</summary>
public static class Actions
{
    static ConsoleWindow? console;

    /// <summary>The console window, for pickers that need an owner.</summary>
    public static ConsoleWindow? ConsoleOwner => console;
    static AppState State => AppState.Current;

    public static void OpenSonorium() => OpenUrl($"http://localhost:{State.Settings.Port}");

    public static void OpenUrl(string url) => Process.Start(new ProcessStartInfo(url) { UseShellExecute = true })?.Dispose();

    public static void OpenFolder(string path)
    {
        Directory.CreateDirectory(path);
        Process.Start(new ProcessStartInfo("explorer.exe", $"\"{path}\"") { UseShellExecute = true })?.Dispose();
    }

    public static void ShowConsole(string page, bool checkNow = false)
    {
        if (console is null)
        {
            console = new ConsoleWindow();
            console.Closed += (_, _) => console = null;
        }
        console.Navigate(page, checkNow);
        console.Activate();
    }

    public static void Start() => Run(() => State.Host.Start(), "start");
    public static void Stop() => Run(() => State.Host.Stop(), "stop");
    public static void Restart() => Run(() => State.Host.Restart(), "restart");

    static void Run(Action action, string what)
    {
        _ = Task.Run(async () =>
        {
            try
            {
                action();
            }
            catch (Exception e)
            {
                Notifications.ShowError($"Sonorium couldn't {what}: {e.Message}");
            }
            await Task.Delay(1500);
            await State.Poll();
        });
    }

    /// <summary>Quit stops Sonorium (the service too) and closes the tray icon.</summary>
    public static void Quit()
    {
        try { State.Host.Stop(); } catch (Exception) { }
        Application.Current.Exit();
    }
}
