namespace Sonorium.Desktop;

/// <summary>What this install is: the desktop version and the server commit it carries (app\BUILD.txt, from CI).</summary>
public static class BuildInfo
{
    public static string Describe()
    {
        string? server = null;
        try { server = File.ReadAllText(Path.Combine(Paths.AppFolder, "BUILD.txt")).Trim(); }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { }
        return server is null ? $"version {Updates.CurrentVersion}" : $"version {Updates.CurrentVersion}, {server}";
    }
}
