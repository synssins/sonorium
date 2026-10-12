namespace Sonorium.Desktop;

/// <summary>
/// Where Sonorium's files are on Windows.
///
///   Program     the install folder: SonoriumHost.exe, Sonorium.exe, python\, app\
///   Per user    themes in Music\Sonorium, data in Documents\Sonorium,
///               settings and logs in %LOCALAPPDATA%\Sonorium
///   Service     everything in C:\ProgramData\Sonorium (the service account can't see a
///               user's profile)
/// </summary>
public static class Paths
{
    public const string ServiceName = "Sonorium";
    public const string ServiceAccount = @"NT SERVICE\Sonorium";

    /// <summary>The install folder. SONORIUM_HOST_ROOT overrides it (development: an unpacked build).</summary>
    public static string ProgramFolder =>
        Environment.GetEnvironmentVariable("SONORIUM_HOST_ROOT") is { Length: > 0 } root
            ? root
            : AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);

    public static string PythonExe => Path.Combine(ProgramFolder, "python", "python.exe");
    public static string AppFolder => Path.Combine(ProgramFolder, "app");
    public static string BundledThemes => Path.Combine(AppFolder, "themes");
    public static string HostExe => Path.Combine(ProgramFolder, "SonoriumHost.exe");
    public static string TrayExe => Path.Combine(ProgramFolder, "Sonorium.exe");
    public static string SecretsExe => Path.Combine(ProgramFolder, "SonoriumSecrets.exe");
    public static string UpdaterExe => Path.Combine(ProgramFolder, "SonoriumUpdater.exe");

    static string ProgramData => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Sonorium");
    static string LocalAppData => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Sonorium");

    /// <summary>Where desktop.json is.</summary>
    public static string SettingsDir(RunMode mode) =>
        Environment.GetEnvironmentVariable("SONORIUM_DESKTOP_DIR") is { Length: > 0 } dir ? dir  // development and tests
        : mode == RunMode.Service ? ProgramData : LocalAppData;

    public static string DefaultThemesFolder(RunMode mode) => mode == RunMode.Service
        ? Path.Combine(ProgramData, "Themes")
        : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyMusic), "Sonorium");

    public static string DefaultDataFolder(RunMode mode) => mode == RunMode.Service
        ? Path.Combine(ProgramData, "Data")
        : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "Sonorium");

    public static string DefaultLogsFolder(RunMode mode) => mode == RunMode.Service
        ? Path.Combine(ProgramData, "Logs")
        : Path.Combine(LocalAppData, "Logs");

    /// <summary>The service's own folder (ProgramData\Sonorium), whose ACL the service install sets.</summary>
    public static string ServiceRoot => ProgramData;

    /// <summary>
    /// All-users installs: the scheduled task that installs updates as SYSTEM (no administrator
    /// prompt per update), which the installing user may start; and its folder, where the user's
    /// updater leaves the request (the channel) and the task leaves its result and log.
    /// </summary>
    public const string UpdateTaskName = @"\Sonorium\Update";
    public static string UpdatesFolder => Path.Combine(ProgramData, "Updates");

    /// <summary>Installed for all users (Program Files) rather than for one user (%LOCALAPPDATA%\Programs).</summary>
    public static bool AllUsersInstall =>
        ProgramFolder.StartsWith(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), StringComparison.OrdinalIgnoreCase);
}
