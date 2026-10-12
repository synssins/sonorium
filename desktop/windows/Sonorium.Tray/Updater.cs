using System.Diagnostics;
using Sonorium.Desktop;

namespace Sonorium.Tray;

/// <summary>
/// Looks for updates (at start, then every 6 hours, when "Check for updates automatically" is
/// on) and hands the install to SonoriumUpdater.exe, which closes this app and Sonorium,
/// installs, and starts them again.
/// </summary>
public static class Updater
{
    static readonly TimeSpan Every = TimeSpan.FromHours(6);
    static string? notified;
    static AppState State => AppState.Current;

    public static void Start()
    {
        _ = Task.Run(async () =>
        {
            await Task.Delay(TimeSpan.FromSeconds(30));
            while (true)
            {
                if (State.Settings.CheckForUpdates)
                {
                    var update = await Check();
                    if (update is not null && update.Version != notified)
                    {
                        notified = update.Version;
                        if (State.Settings.InstallUpdates) InstallNow();
                        else Notifications.ShowUpdate(update);
                    }
                }
                await Task.Delay(Every);
            }
        });
    }

    /// <summary>Checks now; null when up to date. Throws when GitHub can't be reached.</summary>
    public static async Task<UpdateInfo?> Check(bool throwOnError = false)
    {
        try
        {
            var update = await Updates.CheckAsync(State.Settings.Channel == "preview");
            State.Settings.LastUpdateCheck = DateTimeOffset.Now;
            TrySave();
            State.SetUpdate(update);
            return update;
        }
        catch (Exception) when (!throwOnError)
        {
            return null;
        }
    }

    public static void InstallNow()
    {
        var channel = State.Settings.Channel;
        // Run from a copy: the installer replaces the one in the install folder
        var folder = Path.Combine(Path.GetTempPath(), "SonoriumUpdate");
        Directory.CreateDirectory(folder);
        var copy = Path.Combine(folder, "SonoriumUpdater.exe");
        File.Copy(Paths.UpdaterExe, copy, overwrite: true);
        var info = new ProcessStartInfo(copy, $"--install --channel {channel}") { UseShellExecute = false, CreateNoWindow = true };
        info.Environment["SONORIUM_HOST_ROOT"] = Paths.ProgramFolder;
        Process.Start(info)?.Dispose();
    }

    static void TrySave()
    {
        try { State.Settings.Save(); } catch (Exception e) when (e is IOException or UnauthorizedAccessException) { }
    }
}
