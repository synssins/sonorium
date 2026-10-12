using System.Diagnostics;
using System.Security.Principal;
using System.ServiceProcess;
using Sonorium.Desktop;

namespace Sonorium.Host;

/// <summary>
/// Registers and removes the Windows service (needs admin: the installer, or the console's
/// "Run Sonorium as" through a UAC prompt).
///
///   account     NT SERVICE\Sonorium, a virtual account: no password, low privilege, its own
///               profile (its own DPAPI key for Sonorium's secrets), and folders can be shared
///               with it by name
///   start       automatic (delayed), so it runs before anyone signs in
///   recovery    the SCM restarts the host if the host itself fails
///   control     the installing user may start, stop and restart it without elevation
///   folders     C:\ProgramData\Sonorium: SYSTEM and Administrators full control, the service
///               account and the installing user modify
///   firewall    the private Python may accept connections on private and domain networks
/// </summary>
public static class ServiceSetup
{
    const string FirewallRule = "Sonorium";

    public static void Install(string userSid)
    {
        RequireAdmin();
        _ = new SecurityIdentifier(userSid); // refuses anything that isn't a SID

        if (Exists()) Uninstall(keepFirewall: true);

        var bin = $"\"{Paths.HostExe}\" --service";
        Sc("create", Paths.ServiceName, "binPath=", bin, "start=", "delayed-auto",
           "obj=", Paths.ServiceAccount, "DisplayName=", "Sonorium");
        Sc("description", Paths.ServiceName, "Plays Sonorium's soundscapes to your speakers.");
        Sc("sidtype", Paths.ServiceName, "unrestricted");
        Sc("failure", Paths.ServiceName, "reset=", "86400", "actions=", "restart/5000/restart/10000/restart/30000");

        // The installing user: query, start, stop, pause, interrogate, user-defined controls (restart), read
        var sddl = Capture("sc.exe", "sdshow", Paths.ServiceName).Trim();
        var ace = $"(A;;CCLCSWRPWPDTLOCRRC;;;{userSid})";
        if (!sddl.Contains(ace))
        {
            var at = sddl.IndexOf("S:", StringComparison.Ordinal);
            sddl = at < 0 ? sddl + ace : sddl.Insert(at, ace);
            Sc("sdset", Paths.ServiceName, sddl);
        }

        var settings = DesktopSettings.Load(RunMode.Service);
        PrepareFolders(settings, userSid);
        AddFirewallRules();
    }

    public static void Uninstall(bool keepFirewall = false)
    {
        RequireAdmin();
        if (Exists())
        {
            using var sc = new ServiceController(Paths.ServiceName);
            if (sc.Status != ServiceControllerStatus.Stopped)
            {
                try
                {
                    sc.Stop();
                    sc.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(30));
                }
                catch (System.ServiceProcess.TimeoutException) { }
                catch (InvalidOperationException) { }
            }
            Sc("delete", Paths.ServiceName);
        }
        if (!keepFirewall) RemoveFirewallRules();
        // Data stays: themes, channels and settings survive a reinstall
    }

    /// <summary>
    /// General's "Run Sonorium as: A service": registers the service and copies the user's
    /// settings, data and themes into its folders (when those are still empty), then starts it.
    /// Secrets don't move: they're encrypted for the user's account, so the Home Assistant token
    /// and MQTT password are entered again.
    /// </summary>
    public static void SwitchToService(string userSid, string userData, string userThemes)
    {
        Install(userSid);
        var service = DesktopSettings.Load(RunMode.Service);
        CopyIfEmpty(userData, service.Data);
        CopyIfEmpty(userThemes, service.Themes);
        using var sc = new ServiceController(Paths.ServiceName);
        sc.Start();
        sc.WaitForStatus(ServiceControllerStatus.Running, TimeSpan.FromSeconds(30));
    }

    /// <summary>"When I sign in": the service's data and themes back to the user's folders (when empty), service removed.</summary>
    public static void SwitchToUser(string userData, string userThemes)
    {
        RequireAdmin();
        var service = DesktopSettings.Load(RunMode.Service);
        Uninstall(keepFirewall: true);
        CopyIfEmpty(service.Data, userData);
        CopyIfEmpty(service.Themes, userThemes);
    }

    // Not carried across: secrets (encrypted for the other account) and the console token (made anew)
    static readonly string[] NotCopied = ["secrets", "console.token"];

    static void CopyIfEmpty(string from, string to)
    {
        if (!Directory.Exists(from)) return;
        Directory.CreateDirectory(to);
        if (Directory.EnumerateFileSystemEntries(to).Any()) return;
        Copy(from, to);
    }

    static void Copy(string from, string to)
    {
        Directory.CreateDirectory(to);
        foreach (var file in Directory.EnumerateFiles(from))
            if (!NotCopied.Contains(Path.GetFileName(file), StringComparer.OrdinalIgnoreCase))
                File.Copy(file, Path.Combine(to, Path.GetFileName(file)), overwrite: false);
        foreach (var dir in Directory.EnumerateDirectories(from))
            if (!NotCopied.Contains(Path.GetFileName(dir), StringComparer.OrdinalIgnoreCase))
                Copy(dir, Path.Combine(to, Path.GetFileName(dir)));
    }

    public static bool Exists() =>
        ServiceController.GetServices().Any(s => s.ServiceName.Equals(Paths.ServiceName, StringComparison.OrdinalIgnoreCase));

    static void PrepareFolders(DesktopSettings settings, string userSid)
    {
        var root = Paths.ServiceRoot;
        Directory.CreateDirectory(root);
        // Fresh ACL on ProgramData\Sonorium, inherited by everything in it
        Run("icacls.exe", root, "/inheritance:r", "/grant:r",
            "*S-1-5-18:(OI)(CI)F",           // SYSTEM
            "*S-1-5-32-544:(OI)(CI)F",       // Administrators
            $"{Paths.ServiceAccount}:(OI)(CI)M",
            $"*{userSid}:(OI)(CI)M");
        foreach (var dir in new[] { settings.Data, settings.Themes, settings.Logs })
        {
            Directory.CreateDirectory(dir);
            if (!IsUnder(dir, root)) FolderAccess.GrantService(dir);
        }
        if (!File.Exists(DesktopSettings.FilePath(RunMode.Service))) settings.Save();
    }


    static bool IsUnder(string path, string root) =>
        Path.GetFullPath(path).TrimEnd('\\').StartsWith(Path.GetFullPath(root).TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase);

    /// <summary>
    /// Lets Sonorium's Python accept connections on private and domain networks (web page, streams,
    /// the app's HTTPS port, discovery replies): by program, not port, so a port changed on General
    /// stays covered.
    /// </summary>
    public static void AddFirewallRules()
    {
        RequireAdmin();
        RemoveFirewallRules();
        var program = $"program={Paths.PythonExe}";
        foreach (var protocol in new[] { "TCP", "UDP" })
            Run("netsh.exe", "advfirewall", "firewall", "add", "rule", $"name={FirewallRule}", "dir=in", "action=allow",
                program, $"protocol={protocol}", "profile=private,domain");
    }

    public static void RemoveFirewallRules()
    {
        // Fails harmlessly when there's no such rule
        Capture("netsh.exe", "advfirewall", "firewall", "delete", "rule", $"name={FirewallRule}");
    }

    static void RequireAdmin()
    {
        using var id = WindowsIdentity.GetCurrent();
        if (!new WindowsPrincipal(id).IsInRole(WindowsBuiltInRole.Administrator))
            throw new UnauthorizedAccessException("This needs administrator rights.");
    }

    static void Sc(params string[] args) => Run("sc.exe", args);

    static void Run(string exe, params string[] args)
    {
        var (code, output) = Exec(exe, args);
        if (code != 0) throw new InvalidOperationException($"{exe} {args.FirstOrDefault()} failed ({code}): {output.Trim()}");
    }

    static string Capture(string exe, params string[] args) => Exec(exe, args).Output;

    static (int Code, string Output) Exec(string exe, string[] args)
    {
        var info = new ProcessStartInfo(exe)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (var a in args) info.ArgumentList.Add(a);
        using var p = Process.Start(info)!;
        var output = p.StandardOutput.ReadToEnd() + p.StandardError.ReadToEnd();
        p.WaitForExit();
        return (p.ExitCode, output);
    }
}
