using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;
using Sonorium.Desktop;

// SonoriumUpdater.exe
//
//   --install [--channel stable|preview]
//       Run by the tray console, as the signed-in user, from a temporary copy.
//       For me only installs: downloads the newest installer for the channel from the GitHub
//       release, checks GitHub's SHA-256 of it, closes the tray console and Sonorium, runs the
//       installer silently (it relaunches the tray console).
//       All-users installs: asks the "Sonorium Update" scheduled task (SYSTEM) to do the same,
//       so there's no administrator prompt, waits for it, then starts the tray console again.
//
//   --system          The scheduled task's action (SYSTEM, from Program Files): copies itself to
//                     SYSTEM's own temp folder and starts that copy, so the installer can replace
//                     the one in Program Files.
//   --system-run      That copy: installs the update named in the request.
//
// Exit codes: 0 installed or nothing to do, 1 failed. Logged in the logs folder (per user) or in
// C:\ProgramData\Sonorium\Updates (the task).

const int Ok = 0, Failed = 1;
var mode = args.FirstOrDefault();
var channel = args.SkipWhile(a => a != "--channel").Skip(1).FirstOrDefault() is "preview" ? "preview" : "stable";
var requestFile = Path.Combine(Paths.UpdatesFolder, "request.json");
var statusFile = Path.Combine(Paths.UpdatesFolder, "status.json");

switch (mode)
{
    case "--install":
        return Paths.AllUsersInstall && TaskExists() ? await ViaTask() : await InstallAsUser();
    case "--system":
        return StartSystemCopy();
    case "--system-run":
        return await RunAsSystem();
    default:
        Console.Error.WriteLine("Usage: SonoriumUpdater --install [--channel stable|preview]");
        return 2;
}

// --- per user -------------------------------------------------------------------------------

async Task<int> InstallAsUser()
{
    var logs = DesktopSettings.Load(HostControl.ServiceInstalled() ? RunMode.Service : RunMode.User).Logs;
    void Log(string m) => Append(Path.Combine(logs, $"Updater-{DateTime.Now:yyyy-MM-dd}.log"), m);
    try
    {
        var update = await Updates.CheckAsync(channel == "preview");
        if (update is null)
        {
            Log($"Up to date ({Updates.CurrentVersion}, {channel})");
            return Ok;
        }
        Log($"Updating {Updates.CurrentVersion} to {update.Version} ({channel})");
        var installer = await Download(update, Path.Combine(Path.GetTempPath(), "SonoriumUpdate"));
        CloseTray();
        try { HostControl.Detect().Stop(); } catch (Exception) { }

        // An all-users install without the update task (older installs): Windows asks for permission
        var info = new ProcessStartInfo(installer)
        {
            UseShellExecute = true,
            Arguments = $"/VERYSILENT /SUPPRESSMSGBOXES /NORESTART {(Paths.AllUsersInstall ? "/ALLUSERS" : "/CURRENTUSER")} /LOG=\"{Path.Combine(logs, $"Install-{update.Version}.log")}\"",
            Verb = Paths.AllUsersInstall ? "runas" : "",
        };
        using var setup = Process.Start(info)!;
        await setup.WaitForExitAsync();
        Log(setup.ExitCode == 0 ? $"Installed {update.Version}" : $"The installer ended with code {setup.ExitCode}");
        TryDelete(installer);
        if (setup.ExitCode != 0) StartTray();
        return setup.ExitCode == 0 ? Ok : Failed;
    }
    catch (Exception e)
    {
        Log($"Update failed: {e.Message}");
        StartTray();
        return Failed;
    }
}

async Task<int> ViaTask()
{
    var logs = DesktopSettings.Load(HostControl.ServiceInstalled() ? RunMode.Service : RunMode.User).Logs;
    void Log(string m) => Append(Path.Combine(logs, $"Updater-{DateTime.Now:yyyy-MM-dd}.log"), m);
    try
    {
        var requested = DateTimeOffset.UtcNow;
        File.WriteAllText(requestFile, JsonSerializer.Serialize(new Request(channel, requested)));
        var (code, output) = Exec("schtasks.exe", "/run", "/tn", Paths.UpdateTaskName);
        if (code != 0) throw new InvalidOperationException($"The update task didn't start: {output.Trim()}");
        Log($"Asked the update task for the newest {channel} version");

        // The task answers in status.json: "running", then "done" with the installer's exit code
        var deadline = DateTime.UtcNow.AddMinutes(30);
        Status? status = null;
        while (DateTime.UtcNow < deadline)
        {
            await Task.Delay(2000);
            status = ReadStatus();
            if (status is { State: "done" } && status.Requested >= requested) break;
        }
        Log(status is { State: "done" } ? $"Update task: {status.Message}" : "The update task didn't finish in 30 minutes");
        StartTray();
        return status is { State: "done", Code: 0 } ? Ok : Failed;
    }
    catch (Exception e)
    {
        Log($"Update failed: {e.Message}");
        StartTray();
        return Failed;
    }
}

// --- the scheduled task (SYSTEM) --------------------------------------------------------------

int StartSystemCopy()
{
    // SYSTEM's temp folder (C:\Windows\SystemTemp) is closed to other users: nobody can swap the copy
    var folder = Path.Combine(Path.GetTempPath(), "SonoriumUpdate");
    Directory.CreateDirectory(folder);
    var copy = Path.Combine(folder, "SonoriumUpdater.exe");
    File.Copy(Environment.ProcessPath!, copy, overwrite: true);
    var info = new ProcessStartInfo(copy, "--system-run") { UseShellExecute = false, CreateNoWindow = true };
    info.Environment["SONORIUM_HOST_ROOT"] = Paths.ProgramFolder;
    Process.Start(info)?.Dispose();
    return Ok;
}

async Task<int> RunAsSystem()
{
    var log = Path.Combine(Paths.UpdatesFolder, "updater.log");
    Request? request = null;
    try
    {
        request = JsonSerializer.Deserialize<Request>(File.ReadAllText(requestFile));
    }
    catch (Exception e) when (e is IOException or JsonException) { }
    // The request is written by a user: only the channel is taken from it
    var preview = request?.Channel == "preview";
    var requested = request?.Requested ?? DateTimeOffset.UtcNow;
    void Done(int code, string message)
    {
        Append(log, message);
        WriteStatus(new Status("done", code, message, requested));
    }

    WriteStatus(new Status("running", null, "Checking for updates", requested));
    try
    {
        var update = await Updates.CheckAsync(preview);
        if (update is null)
        {
            Done(Ok, $"Up to date ({Updates.CurrentVersion})");
            return Ok;
        }
        Append(log, $"Updating {Updates.CurrentVersion} to {update.Version}");
        var installer = await Download(update, Path.Combine(Path.GetTempPath(), "SonoriumUpdate"));
        // /NOTRAY=1: there's no signed-in user to start the tray console for; the user's updater does
        var info = new ProcessStartInfo(installer)
        {
            UseShellExecute = false,
            Arguments = $"/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /ALLUSERS /NOTRAY=1 /LOG=\"{Path.Combine(Paths.UpdatesFolder, $"Install-{update.Version}.log")}\"",
        };
        using var setup = Process.Start(info)!;
        await setup.WaitForExitAsync();
        TryDelete(installer);
        Done(setup.ExitCode, setup.ExitCode == 0 ? $"Installed {update.Version}" : $"The installer ended with code {setup.ExitCode}");
        return setup.ExitCode == 0 ? Ok : Failed;
    }
    catch (Exception e)
    {
        Done(Failed, $"Update failed: {e.Message}");
        return Failed;
    }
}

// --- shared ---------------------------------------------------------------------------------

static async Task<string> Download(UpdateInfo update, string folder)
{
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
    await using var file = File.OpenRead(installer);
    var hash = Convert.ToHexStringLower(await SHA256.HashDataAsync(file));
    if (!hash.Equals(update.Sha256, StringComparison.OrdinalIgnoreCase))
        throw new InvalidOperationException($"The download is damaged (SHA-256 {hash}, expected {update.Sha256}).");
    return installer;
}

static void CloseTray()
{
    foreach (var tray in Process.GetProcessesByName("Sonorium"))
    {
        using (tray)
        {
            try { tray.Kill(); tray.WaitForExit(5000); } catch (Exception) { }
        }
    }
}

static void StartTray()
{
    if (Process.GetProcessesByName("Sonorium").Length > 0) return;
    try { Process.Start(new ProcessStartInfo(Paths.TrayExe) { UseShellExecute = true })?.Dispose(); } catch (Exception) { }
}

static bool TaskExists() => Exec("schtasks.exe", "/query", "/tn", Paths.UpdateTaskName).Code == 0;

Status? ReadStatus()
{
    try { return JsonSerializer.Deserialize<Status>(File.ReadAllText(statusFile)); }
    catch (Exception e) when (e is IOException or JsonException or UnauthorizedAccessException) { return null; }
}

void WriteStatus(Status status)
{
    try
    {
        var tmp = statusFile + ".tmp";
        File.WriteAllText(tmp, JsonSerializer.Serialize(status));
        File.Move(tmp, statusFile, overwrite: true);
    }
    catch (Exception e) when (e is IOException or UnauthorizedAccessException) { }
}

static void Append(string file, string message)
{
    try
    {
        Directory.CreateDirectory(Path.GetDirectoryName(file)!);
        File.AppendAllText(file, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {message}{Environment.NewLine}");
    }
    catch (Exception) { }
}

static void TryDelete(string file)
{
    try { File.Delete(file); } catch (Exception) { }
}

static (int Code, string Output) Exec(string exe, params string[] arguments)
{
    var info = new ProcessStartInfo(exe) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
    foreach (var a in arguments) info.ArgumentList.Add(a);
    using var p = Process.Start(info)!;
    var output = p.StandardOutput.ReadToEnd() + p.StandardError.ReadToEnd();
    p.WaitForExit();
    return (p.ExitCode, output);
}

record Request(string Channel, DateTimeOffset Requested);
record Status(string State, int? Code, string Message, DateTimeOffset Requested);
