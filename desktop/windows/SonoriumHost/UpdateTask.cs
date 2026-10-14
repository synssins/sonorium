using System.Diagnostics;
using System.Security;
using System.Security.Principal;
using Sonorium.Desktop;

namespace Sonorium.Host;

/// <summary>
/// All-users installs: the "Sonorium Update" scheduled task, so updates install without an
/// administrator prompt each time.
///
///   runs as     SYSTEM, only "SonoriumUpdater.exe --system" from the install folder (Program
///               Files, which users can't change); it installs only Sonorium-Setup-*.exe from
///               the synssins/sonorium GitHub releases, checked against GitHub's SHA-256
///   started by  the installing user (the task's own permissions allow them to run it), through
///               their tray console's updater
///   folder      C:\ProgramData\Sonorium\Updates: SYSTEM and Administrators full control, the
///               installing user modify (the request with the channel; the task's result and log)
/// </summary>
public static class UpdateTask
{
    public static void Install(string userSid)
    {
        var sid = new SecurityIdentifier(userSid).Value;
        Directory.CreateDirectory(Paths.UpdatesFolder);
        Run("icacls.exe", Paths.UpdatesFolder, "/inheritance:r", "/grant:r",
            "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", $"*{sid}:(OI)(CI)M");

        var updater = SecurityElement.Escape(Paths.UpdaterExe);
        // FA: full control for SYSTEM and Administrators; FRFX: the installing user may read and run it
        var sddl = $"D:(A;;FA;;;SY)(A;;FA;;;BA)(A;;FRFX;;;{sid})";
        var xml = $"""
            <?xml version="1.0" encoding="UTF-16"?>
            <Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
              <RegistrationInfo>
                <Description>Installs Sonorium updates when Sonorium asks for them.</Description>
                <SecurityDescriptor>{sddl}</SecurityDescriptor>
              </RegistrationInfo>
              <Principals>
                <Principal id="System">
                  <UserId>S-1-5-18</UserId>
                  <RunLevel>HighestAvailable</RunLevel>
                </Principal>
              </Principals>
              <Settings>
                <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
                <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
                <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
                <AllowStartOnDemand>true</AllowStartOnDemand>
                <AllowHardTerminate>true</AllowHardTerminate>
                <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
                <Enabled>true</Enabled>
              </Settings>
              <Actions Context="System">
                <Exec>
                  <Command>{updater}</Command>
                  <Arguments>--system</Arguments>
                </Exec>
              </Actions>
            </Task>
            """;
        var file = Path.Combine(Path.GetTempPath(), "SonoriumUpdateTask.xml");
        File.WriteAllText(file, xml, System.Text.Encoding.Unicode);
        try
        {
            Run("schtasks.exe", "/create", "/tn", Paths.UpdateTaskName, "/xml", file, "/f");
        }
        finally
        {
            File.Delete(file);
        }
    }

    public static void Uninstall()
    {
        Exec("schtasks.exe", "/delete", "/tn", Paths.UpdateTaskName, "/f");
        try { Directory.Delete(Paths.UpdatesFolder, recursive: true); } catch (IOException) { } catch (UnauthorizedAccessException) { }
    }

    public static string CurrentUserSid() => WindowsIdentity.GetCurrent().User!.Value;

    static void Run(string exe, params string[] args)
    {
        var (code, output) = Exec(exe, args);
        if (code != 0) throw new InvalidOperationException($"{exe} {args.FirstOrDefault()} failed ({code}): {output.Trim()}");
    }

    static (int Code, string Output) Exec(string exe, params string[] args)
    {
        var info = new ProcessStartInfo(exe) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var a in args) info.ArgumentList.Add(a);
        using var p = Process.Start(info)!;
        var output = p.StandardOutput.ReadToEnd() + p.StandardError.ReadToEnd();
        p.WaitForExit();
        return (p.ExitCode, output);
    }
}
