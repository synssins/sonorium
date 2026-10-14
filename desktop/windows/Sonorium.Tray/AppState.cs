using System.Text.Json.Nodes;
using Microsoft.UI.Dispatching;
using Sonorium.Desktop;

namespace Sonorium.Tray;

/// <summary>
/// What the tray icon, its menu and the console window show: Sonorium's state (polled every
/// few seconds), the run mode, and an available update. Changes arrive on the UI thread.
/// </summary>
public sealed class AppState
{
    public static AppState Current { get; } = new();

    public HostControl Host { get; private set; } = HostControl.Detect();
    public DesktopSettings Settings { get; private set; }
    public SonoriumApi Api { get; private set; }

    public bool Running { get; private set; }
    public string? Version { get; private set; }
    public string? BootId { get; private set; }
    public DateTimeOffset? RunningSince { get; private set; }
    public int ChannelsPlaying { get; private set; }
    public IReadOnlyList<string> PlayingLines { get; private set; } = [];
    public JsonNode? Connection { get; private set; }
    public UpdateInfo? Update { get; private set; }
    /// <summary>Set while GitHub is too busy to answer the update check; it's tried again later.</summary>
    public string? UpdateBusy { get; private set; }

    public event Action? Changed;

    DispatcherQueue? ui;
    readonly SemaphoreSlim polling = new(1, 1);
    readonly Dictionary<string, string> themeNames = [];

    AppState()
    {
        Settings = DesktopSettings.Load(Host.Mode);
        Api = new SonoriumApi(Settings);
    }

    public void Attach(DispatcherQueue queue)
    {
        ui = queue;
        var timer = queue.CreateTimer();
        timer.Interval = TimeSpan.FromSeconds(5);
        timer.Tick += async (_, _) => await Poll();
        timer.Start();
        _ = Poll();
    }

    /// <summary>The run mode or desktop.json changed (General, Folders): reload them.</summary>
    public void Reload()
    {
        Host = HostControl.Detect();
        Settings = DesktopSettings.Load(Host.Mode);
        Api.Dispose();
        Api = new SonoriumApi(Settings);
        _ = Poll();
    }

    public async Task Poll()
    {
        if (!await polling.WaitAsync(0)) return;
        try
        {
            var info = await Api.Info();
            Running = info is not null;
            Version = info?["version"]?.ToString();
            var boot = info?["boot_id"]?.ToString();
            // When this Sonorium process started (older versions don't say: when we first saw it)
            if (DateTimeOffset.TryParse(info?["started_at"]?.ToString(), out var started)) RunningSince = started;
            else if (boot != BootId) RunningSince = boot is null ? null : DateTimeOffset.Now;
            BootId = boot;
            ChannelsPlaying = 0;
            PlayingLines = [];
            if (Running)
            {
                try
                {
                    var sessions = (await Api.Get("/api/sessions"))?.AsArray() ?? [];
                    var playing = sessions.Where(s => s?["is_playing"]?.GetValue<bool>() == true).ToList();
                    ChannelsPlaying = playing.Count;
                    var ids = playing.Select(s => s?["theme_id"]?.ToString()).OfType<string>().ToList();
                    if (ids.Any(id => !themeNames.ContainsKey(id)))
                    {
                        foreach (var t in (await Api.Get("/api/themes"))?.AsArray() ?? [])
                            if (t?["id"]?.ToString() is { } id) themeNames[id] = t["name"]?.ToString() ?? id;
                    }
                    PlayingLines = playing.Select(s =>
                    {
                        var id = s?["theme_id"]?.ToString();
                        var theme = id is null ? "" : themeNames.GetValueOrDefault(id, id);
                        return $"{s?["name"]}: {theme}";
                    }).ToList();
                    Connection = await Api.Get("/api/connection");
                }
                catch (Exception e) when (e is HttpRequestException or SonoriumApiException or TaskCanceledException or InvalidOperationException) { }
            }
        }
        finally
        {
            polling.Release();
        }
        Raise();
    }

    public void SetUpdate(UpdateInfo? update)
    {
        Update = update;
        UpdateBusy = null;
        Raise();
    }

    public void SetUpdateBusy(string? message)
    {
        UpdateBusy = message;
        Raise();
    }

    void Raise()
    {
        if (ui is null || ui.HasThreadAccess) Changed?.Invoke();
        else ui.TryEnqueue(() => Changed?.Invoke());
    }

    /// <summary>"localhost:8008", as the menu shows it.</summary>
    public string Address => $"localhost:{Settings.Port}";
}
