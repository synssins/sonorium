using System.ServiceProcess;
using Sonorium.Desktop;
using Sonorium.Host;

// SonoriumHost.exe
//   (no arguments)              run Sonorium per user, in the background (the tray starts it at sign-in)
//   --service                   run as the Windows service (only the Service Control Manager uses this)
//   install-service [user SID]  register the service (admin); the SID is who may start and stop it
//                               without elevation (default: the user running this, as in a UAC prompt)
//   uninstall-service           remove the service (admin); data is kept
//   switch-mode service <SID> <user data folder> <user themes folder>
//   switch-mode user <user data folder> <user themes folder>
//                               the console's "Run Sonorium as" (admin): the service registered
//                               or removed, data and themes copied across
//   install-firewall            all-users installs (admin): Windows Firewall rules for Sonorium's Python
//   uninstall-firewall          remove them (admin)
//   install-update-task [SID]   all-users installs (admin): the SYSTEM task that installs updates
//                               without a prompt, which that user may start (default: the user running this)
//   uninstall-update-task       remove it (admin)
//   env                         print the variables Sonorium would run with (diagnostics)

try
{
    switch (args.FirstOrDefault())
    {
        case "--service":
            ServiceBase.Run(new SonoriumService());
            return 0;
        case "install-service" when args.Length <= 2:
            ServiceSetup.Install(args.Length == 2 ? args[1] : System.Security.Principal.WindowsIdentity.GetCurrent().User!.Value);
            return 0;
        case "uninstall-service":
            ServiceSetup.Uninstall();
            return 0;
        case "install-firewall":
            ServiceSetup.AddFirewallRules();
            return 0;
        case "uninstall-firewall":
            ServiceSetup.RemoveFirewallRules();
            return 0;
        case "install-update-task" when args.Length <= 2:
            UpdateTask.Install(args.Length == 2 ? args[1] : UpdateTask.CurrentUserSid());
            return 0;
        case "uninstall-update-task":
            UpdateTask.Uninstall();
            return 0;
        case "switch-mode" when args.Length == 5 && args[1] == "service":
            ServiceSetup.SwitchToService(args[2], args[3], args[4]);
            return 0;
        case "switch-mode" when args.Length == 4 && args[1] == "user":
            ServiceSetup.SwitchToUser(args[2], args[3]);
            return 0;
        case "env":
            var mode = args.Contains("--service-mode") ? RunMode.Service : RunMode.User;
            foreach (var (k, v) in Supervisor.Environment(DesktopSettings.Load(mode)))
                Console.WriteLine($"{k}={v}");
            return 0;
        case null:
            return RunPerUser();
        default:
            Console.Error.WriteLine("Usage: SonoriumHost [--service | install-service [user SID] | uninstall-service | env]");
            return 2;
    }
}
catch (Exception e) when (args.FirstOrDefault() is "install-service" or "uninstall-service" or "switch-mode" or "install-update-task" or "uninstall-update-task" or "install-firewall" or "uninstall-firewall")
{
    // The tray shows this text when the elevated step fails; an elevated process has no
    // stderr it can read, so it's also left in %TEMP% (the same user's, elevated)
    Console.Error.WriteLine(e.Message);
    try { File.WriteAllText(Path.Combine(Path.GetTempPath(), HostProtocol.ElevatedErrorFile), e.Message); } catch (IOException) { }
    return 1;
}

static int RunPerUser()
{
    // One per sign-in session
    using var mutex = new Mutex(true, HostProtocol.UserInstanceMutex, out var first);
    if (!first) return 0;

    var settings = DesktopSettings.Load(RunMode.User);
    using var log = new LogWriter(settings.Logs);
    using var stopEvent = new EventWaitHandle(false, EventResetMode.AutoReset, HostProtocol.UserStopEvent);
    using var restartEvent = new EventWaitHandle(false, EventResetMode.AutoReset, HostProtocol.UserRestartEvent);
    using var supervisor = new Supervisor(RunMode.User, log);
    log.Host($"Running per user ({BuildInfo.Describe()})");
    supervisor.Start();
    while (true)
    {
        if (WaitHandle.WaitAny([stopEvent, restartEvent]) == 0) break;
        supervisor.Restart();
    }
    supervisor.Stop();
    return 0;
}

sealed class SonoriumService : ServiceBase
{
    Supervisor? supervisor;
    LogWriter? log;

    public SonoriumService()
    {
        ServiceName = Paths.ServiceName;
        CanStop = true;
        CanShutdown = true;
    }

    protected override void OnStart(string[] args)
    {
        var settings = DesktopSettings.Load(RunMode.Service);
        log = new LogWriter(settings.Logs);
        log.Host($"Running as the Windows service ({BuildInfo.Describe()})");
        supervisor = new Supervisor(RunMode.Service, log);
        supervisor.Start();
    }

    protected override void OnCustomCommand(int command)
    {
        if (command == HostProtocol.ServiceRestartControl) supervisor?.Restart();
    }

    protected override void OnStop() => Shutdown();
    protected override void OnShutdown() => Shutdown();

    void Shutdown()
    {
        supervisor?.Stop();
        supervisor?.Dispose();
        supervisor = null;
        log?.Dispose();
    }
}
