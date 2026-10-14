using System.Diagnostics;
using Sonorium.Desktop;

namespace Sonorium.Tray;

/// <summary>
/// Looks for updates (at start, then every 6 hours, when "Check for updates automatically" is
/// on) and hands the install to SonoriumUpdater.exe, which closes this app and Sonorium,
/// installs, and starts them again. When GitHub is too busy to answer (its rate limit), the
/// check, or the install, is tried again when GitHub says it will answer.
/// </summary>
public static class Updater
{
    static readonly TimeSpan Every = TimeSpan.FromHours(6);
    /// <summary>SonoriumUpdater.exe's exit code when GitHub was too busy to answer.</summary>
    const int UpdaterBusy = 3;
    static string? notified;
    static DateTimeOffset? retryAt;
    static readonly SemaphoreSlim wake = new(0);
    static AppState State => AppState.Current;

    public static void Start()
    {
        _ = Task.Run(async () =>
        {
            await Task.Delay(TimeSpan.FromSeconds(30));
            var nextAuto = DateTimeOffset.Now;
            while (true)
            {
                var due = retryAt is { } r && r < nextAuto ? r : nextAuto;
                var wait = due - DateTimeOffset.Now;
                if (wait > TimeSpan.Zero)
                {
                    // Woken early when a check or an install meets a busy GitHub
                    await wake.WaitAsync(wait);
                    continue;
                }
                var retrying = retryAt is { } at && at <= DateTimeOffset.Now;
                if (retrying) retryAt = null;
                if (retrying || State.Settings.CheckForUpdates)
                {
                    var update = await Check();
                    if (update is not null && update.Version != notified)
                    {
                        notified = update.Version;
                        if (State.Settings.CheckForUpdates && State.Settings.InstallUpdates) InstallNow();
                        else Notifications.ShowUpdate(update);
                    }
                }
                if (!retrying || DateTimeOffset.Now >= nextAuto) nextAuto = DateTimeOffset.Now + Every;
            }
        });
    }

    /// <summary>
    /// Checks now; null when up to date. Throws when GitHub can't be reached; a
    /// GitHubBusyException when it's too busy to answer (the check is tried again by itself).
    /// </summary>
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
        catch (GitHubBusyException busy)
        {
            RetryAt(busy.RetryAt);
            if (throwOnError) throw;
            return null;
        }
        catch (Exception)
        {
            // Not "busy" any more: no retry is waiting
            if (State.UpdateBusy is not null && retryAt is null) State.SetUpdateBusy(null);
            if (throwOnError) throw;
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
        var updater = Process.Start(info);
        if (updater is null) return;
        // The updater closes this app before it installs; if it's still here when the updater
        // ends and GitHub was busy, the update is tried again later
        _ = Task.Run(async () =>
        {
            using (updater)
            {
                await updater.WaitForExitAsync();
                if (updater.ExitCode != UpdaterBusy) return;
            }
            notified = null;
            RetryAt(DateTimeOffset.Now.AddMinutes(15));
        });
    }

    static void RetryAt(DateTimeOffset at)
    {
        retryAt = at;
        State.SetUpdateBusy(GitHubBusyException.UserMessage);
        wake.Release();
    }

    static void TrySave()
    {
        try { State.Settings.Save(); } catch (Exception e) when (e is IOException or UnauthorizedAccessException) { }
    }
}
