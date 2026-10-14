using System.Diagnostics;
using Sonorium.Desktop;

namespace Sonorium.Host;

/// <summary>
/// Keeps Sonorium running: starts the private Python with Sonorium's settings, writes its
/// output to the log, starts it again at once when it asks to (exit code 75, its Restart),
/// and after a crash with a growing pause (2 s doubling to 60 s; back to 2 s once it has run
/// for 5 minutes). Stop() ends it and everything it started.
/// </summary>
public sealed class Supervisor : IDisposable
{
    static readonly TimeSpan MaxBackoff = TimeSpan.FromSeconds(60);
    static readonly TimeSpan StableRun = TimeSpan.FromMinutes(5);

    readonly RunMode mode;
    DesktopSettings settings;
    readonly LogWriter log;
    readonly JobObject job = new();
    readonly CancellationTokenSource stop = new();
    readonly object gate = new();
    Process? process;
    bool restartRequested;
    Thread? thread;

    public Supervisor(RunMode mode, LogWriter log)
    {
        this.mode = mode;
        settings = DesktopSettings.Load(mode);
        this.log = log;
    }

    public void Start()
    {
        thread = new Thread(Run) { IsBackground = true, Name = "Sonorium supervisor" };
        thread.Start();
    }

    /// <summary>Ends Sonorium and waits for the supervisor to finish.</summary>
    public void Stop()
    {
        stop.Cancel();
        Kill();
        thread?.Join(TimeSpan.FromSeconds(15));
    }

    /// <summary>Restarts Sonorium (the Python process); the host keeps running.</summary>
    public void Restart()
    {
        lock (gate) restartRequested = true;
        log.Host("Restart requested");
        Kill();
    }

    void Kill()
    {
        lock (gate)
        {
            try
            {
                if (process is { HasExited: false }) process.Kill(entireProcessTree: true);
            }
            catch (InvalidOperationException) { }
            catch (System.ComponentModel.Win32Exception e) { log.Host($"Could not end Sonorium: {e.Message}"); }
        }
    }

    void Run()
    {
        var backoff = TimeSpan.FromSeconds(2);
        while (!stop.IsCancellationRequested)
        {
            var started = DateTime.UtcNow;
            int code;
            try
            {
                code = RunOnce();
            }
            catch (Exception e)
            {
                log.Host($"Could not start Sonorium: {e.Message}");
                code = -1;
            }
            if (stop.IsCancellationRequested) break;

            bool asked;
            lock (gate)
            {
                asked = restartRequested;
                restartRequested = false;
            }
            if (asked || code == HostProtocol.RestartExitCode)
            {
                log.Host("Starting Sonorium again");
                backoff = TimeSpan.FromSeconds(2);
                continue;
            }

            if (DateTime.UtcNow - started > StableRun) backoff = TimeSpan.FromSeconds(2);
            log.Host($"Sonorium stopped unexpectedly (exit code {code}); starting it again in {backoff.TotalSeconds:0} s");
            if (stop.Token.WaitHandle.WaitOne(backoff)) break;
            backoff = TimeSpan.FromTicks(Math.Min(backoff.Ticks * 2, MaxBackoff.Ticks));
        }
        log.Host("Stopped");
    }

    int RunOnce()
    {
        // The console may have changed the port, folders or channels since the last start
        settings = DesktopSettings.Load(mode);
        foreach (var dir in new[] { settings.Data, settings.Themes, settings.Logs })
            Directory.CreateDirectory(dir);

        var info = new ProcessStartInfo(Paths.PythonExe)
        {
            // -I: ignore PYTHON* variables and the user's site-packages; -u: unbuffered output
            ArgumentList = { "-I", "-u", "-m", "sonorium.entrypoint" },
            WorkingDirectory = settings.Data,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = System.Text.Encoding.UTF8,
            StandardErrorEncoding = System.Text.Encoding.UTF8,
        };
        foreach (var (key, value) in Environment(settings))
            info.Environment[key] = value;
        // Another Python's settings must never leak in
        foreach (var key in new[] { "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "VIRTUAL_ENV" })
            info.Environment.Remove(key);

        log.Host($"Starting Sonorium on port {settings.Port} (themes: {settings.Themes}, data: {settings.Data})");
        var p = new Process { StartInfo = info };
        p.OutputDataReceived += (_, e) => { if (e.Data is not null) log.Raw(e.Data); };
        p.ErrorDataReceived += (_, e) => { if (e.Data is not null) log.Raw(e.Data); };
        lock (gate)
        {
            if (stop.IsCancellationRequested) return 0;
            p.Start();
            job.Add(p);
            process = p;
        }
        p.BeginOutputReadLine();
        p.BeginErrorReadLine();
        p.WaitForExit();
        lock (gate) process = null;
        var code = p.ExitCode;
        p.Dispose();
        return code;
    }

    /// <summary>The variables Sonorium runs with: the same ones the Docker image and its entrypoint set.</summary>
    public static Dictionary<string, string> Environment(DesktopSettings s) => new()
    {
        ["SONORIUM_STANDALONE"] = "1",
        // Restart = exit with 75; this host starts it again
        ["SONORIUM_SUPERVISED"] = "1",
        // ha_token and mqtt_password go to Windows' DPAPI, not connection.json
        ["SONORIUM_SECRET_HELPER"] = Paths.SecretsExe,
        ["SONORIUM_CONFIG_DIR"] = s.Data,
        ["SONORIUM_CONNECTION_FILE"] = Path.Combine(s.Data, "connection.json"),
        ["SONORIUM__PATH_AUDIO"] = s.Themes,
        ["SONORIUM_BUNDLED_THEMES"] = Paths.BundledThemes,
        ["SONORIUM_HTTP_PORT"] = s.Port.ToString(),
        ["SONORIUM_HTTPS_PORT"] = s.HttpsPort.ToString(),
        ["SONORIUM__MAX_CHANNELS"] = s.MaxChannels.ToString(),
        ["SONORIUM_CONSOLE_TOKEN_FILE"] = s.ConsoleTokenFile,
        ["PYTHONUTF8"] = "1",
        ["PYTHONUNBUFFERED"] = "1",
    };

    public void Dispose()
    {
        stop.Dispose();
        job.Dispose();
    }
}
