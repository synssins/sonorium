using System.Text.Json;
using System.Text.Json.Serialization;

namespace Sonorium.Desktop;

/// <summary>How Sonorium runs on this PC.</summary>
public enum RunMode
{
    /// <summary>Started at sign-in by the tray console, as the signed-in user. No admin rights.</summary>
    User,
    /// <summary>A Windows service that starts with Windows, as NT SERVICE\Sonorium.</summary>
    Service,
}

/// <summary>
/// The desktop app's own settings: what the Docker compose file or the add-on options set
/// elsewhere (port, folders, channels). Stored as desktop.json in <see cref="Paths.SettingsDir"/>
/// for the mode. Everything Sonorium itself stores (connection, channels, plugins) stays in its
/// data folder, through its own API.
/// </summary>
public sealed class DesktopSettings
{
    public const string FileName = "desktop.json";
    public const int DefaultPort = 8008;
    public const int DefaultHttpsPort = 8808;
    public const int DefaultMaxChannels = 6;

    public int Port { get; set; } = DefaultPort;
    public int HttpsPort { get; set; } = DefaultHttpsPort;
    public int MaxChannels { get; set; } = DefaultMaxChannels;
    public string? ThemesFolder { get; set; }
    public string? DataFolder { get; set; }
    public string? LogsFolder { get; set; }
    public bool OpenBrowserOnStart { get; set; }
    public bool CheckForUpdates { get; set; } = true;
    public bool InstallUpdates { get; set; }
    /// <summary>
    /// "stable" or "preview" when chosen on the Updates page; unset, a pre-release build follows
    /// Preview and a release Stable. Saved as "chosenUpdateChannel": the first builds saved
    /// "updateChannel": "stable" as a plain default, which must not count as a choice.
    /// </summary>
    [JsonPropertyName("chosenUpdateChannel")]
    public string? UpdateChannel { get; set; }

    [JsonIgnore]
    public string Channel => UpdateChannel is "stable" or "preview" ? UpdateChannel
        : Updates.CurrentVersion.Contains('-') ? "preview" : "stable";
    public DateTimeOffset? LastUpdateCheck { get; set; }

    [JsonIgnore] public RunMode Mode { get; private set; }

    public string Themes => ThemesFolder ?? Paths.DefaultThemesFolder(Mode);
    public string Data => DataFolder ?? Paths.DefaultDataFolder(Mode);
    public string Logs => LogsFolder ?? Paths.DefaultLogsFolder(Mode);
    public string ConsoleTokenFile => Path.Combine(Data, "console.token");
    public string LocalUrl => $"http://127.0.0.1:{Port}";

    static readonly JsonSerializerOptions Json = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    public static string FilePath(RunMode mode) => Path.Combine(Paths.SettingsDir(mode), FileName);

    /// <summary>The saved settings for a mode, or the defaults when there are none (or they can't be read).</summary>
    public static DesktopSettings Load(RunMode mode)
    {
        DesktopSettings settings;
        try
        {
            settings = JsonSerializer.Deserialize<DesktopSettings>(File.ReadAllText(FilePath(mode)), Json) ?? new();
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or JsonException)
        {
            settings = new();
        }
        settings.Mode = mode;
        return settings;
    }

    /// <summary>Writes the settings in place (not replaced), so the file keeps the ACL the installer gave it.</summary>
    public void Save()
    {
        var path = FilePath(Mode);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var text = JsonSerializer.Serialize(this, Json);
        using var stream = new FileStream(path, FileMode.OpenOrCreate, FileAccess.Write, FileShare.Read);
        using var writer = new StreamWriter(stream);
        writer.Write(text);
        writer.Flush();
        stream.SetLength(stream.Position);
    }
}
