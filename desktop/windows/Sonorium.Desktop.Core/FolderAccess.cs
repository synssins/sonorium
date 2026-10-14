using System.Diagnostics;

namespace Sonorium.Desktop;

/// <summary>Sharing a folder with the service account, which can't see a user's folders otherwise.</summary>
public static class FolderAccess
{
    /// <summary>
    /// Lets NT SERVICE\Sonorium read and change a folder and everything in it. Needs the right to
    /// change the folder's permissions: its owner, or an administrator.
    /// </summary>
    public static void GrantService(string folder)
    {
        var info = new ProcessStartInfo("icacls.exe")
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            ArgumentList = { folder, "/grant", $"{Paths.ServiceAccount}:(OI)(CI)M" },
        };
        using var p = Process.Start(info)!;
        var output = p.StandardOutput.ReadToEnd() + p.StandardError.ReadToEnd();
        p.WaitForExit();
        if (p.ExitCode != 0)
            throw new UnauthorizedAccessException($"The Sonorium service couldn't be given access to {folder}: {output.Trim()}");
    }
}
