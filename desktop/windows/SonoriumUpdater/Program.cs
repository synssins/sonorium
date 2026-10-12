using System.Diagnostics;
using System.Security.Cryptography;
using Sonorium.Desktop;

// SonoriumUpdater.exe --install [--channel stable|preview]
//
// Finds the newest Windows installer in the GitHub releases (Updates.CheckAsync), downloads it,
// checks its SHA-256 against the digest GitHub keeps for the asset, closes the tray console and
// Sonorium, and runs the installer silently. The installer replaces the files, starts the
// service again (service mode) and relaunches the tray console, which starts Sonorium per user.
// Exit codes: 0 installed or nothing to do, 1 failed (logged to the logs folder).

var channel = args.SkipWhile(a => a != "--channel").Skip(1).FirstOrDefault() ?? "stable";
if (!args.Contains("--install"))
{
    Console.Error.WriteLine("Usage: SonoriumUpdater --install [--channel stable|preview]");
    return 2;
}

var mode = HostControl.ServiceInstalled() ? RunMode.Service : RunMode.User;
var logs = DesktopSettings.Load(mode).Logs;
void Log(string message)
{
    try
    {
        Directory.CreateDirectory(logs);
        File.AppendAllText(Path.Combine(logs, $"Updater-{DateTime.Now:yyyy-MM-dd}.log"), $"{DateTime.Now:HH:mm:ss} {message}{Environment.NewLine}");
    }
    catch (Exception) { }
}

try
{
    var update = await Updates.CheckAsync(channel == "preview");
    if (update is null)
    {
        Log($"Up to date ({Updates.CurrentVersion}, {channel})");
        return 0;
    }
    Log($"Updating {Updates.CurrentVersion} to {update.Version} ({channel})");

    var folder = Path.Combine(Path.GetTempPath(), "SonoriumUpdate");
    Directory.CreateDirectory(folder);
    var installer = Path.Combine(folder, update.InstallerName);
    using (var http = new HttpClient { Timeout = TimeSpan.FromMinutes(15) })
    {
        http.DefaultRequestHeaders.UserAgent.ParseAdd("Sonorium-Updater");
        await using var source = await http.GetStreamAsync(update.InstallerUrl);
        await using var target = File.Create(installer);
        await source.CopyToAsync(target);
    }

    if (update.Sha256 is null) throw new InvalidOperationException("GitHub gave no checksum for the installer; not installing it.");
    await using (var file = File.OpenRead(installer))
    {
        var hash = Convert.ToHexStringLower(await SHA256.HashDataAsync(file));
        if (!hash.Equals(update.Sha256, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException($"The download is damaged (SHA-256 {hash}, expected {update.Sha256}).");
    }

    // The tray console first, so it doesn't start Sonorium again; per user, then the host
    foreach (var tray in Process.GetProcessesByName("Sonorium"))
    {
        using (tray)
        {
            try { tray.Kill(); tray.WaitForExit(5000); } catch (Exception) { }
        }
    }
    if (mode == RunMode.User)
    {
        try { HostControl.Detect().Stop(); } catch (Exception) { }
    }

    // All-users installs live in Program Files (and ask for administrator permission);
    // per-user installs in %LOCALAPPDATA%\Programs
    var programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
    var allUsers = Paths.ProgramFolder.StartsWith(programFiles, StringComparison.OrdinalIgnoreCase);
    var info = new ProcessStartInfo(installer)
    {
        UseShellExecute = true,
        Arguments = $"/VERYSILENT /SUPPRESSMSGBOXES /NORESTART {(allUsers ? "/ALLUSERS" : "/CURRENTUSER")} /LOG=\"{Path.Combine(logs, $"Install-{update.Version}.log")}\"",
        Verb = allUsers ? "runas" : "",
    };
    using var setup = Process.Start(info)!;
    await setup.WaitForExitAsync();
    Log(setup.ExitCode == 0 ? $"Installed {update.Version}" : $"The installer ended with code {setup.ExitCode}");
    try { File.Delete(installer); } catch (Exception) { }
    return setup.ExitCode == 0 ? 0 : 1;
}
catch (Exception e)
{
    Log($"Update failed: {e.Message}");
    // Whatever happened, leave the tray console running
    try { Process.Start(new ProcessStartInfo(Paths.TrayExe) { UseShellExecute = true })?.Dispose(); } catch (Exception) { }
    return 1;
}
