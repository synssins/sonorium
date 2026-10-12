using System.ServiceProcess;
using Sonorium.Desktop;
using Sonorium.Host;

// SonoriumHost.exe
//   (no arguments)              run Sonorium per user, in the background (the tray starts it at sign-in)
//   --service                   run as the Windows service (only the Service Control Manager uses this)
//   install-service <user SID>  register the service (admin)
//   uninstall-service           remove the service (admin); data is kept
//   env                         print the variables Sonorium would run with (diagnostics)

try
{
    switch (args.FirstOrDefault())
    {
        case "--service":
            ServiceBase.Run(new SonoriumService());
            return 0;
        case "install-service" when args.Length == 2:
            ServiceSetup.Install(args[1]);
            return 0;
        case "uninstall-service":
            ServiceSetup.Uninstall();
            return 0;
        case "env":
            var mode = args.Contains("--service-mode") ? RunMode.Service : RunMode.User;
            foreach (var (k, v) in Supervisor.Environment(DesktopSettings.Load(mode)))
                Console.WriteLine($"{k}={v}");
            return 0;
        case null:
            return RunPerUser();
        default:
            Console.Error.WriteLine("Usage: SonoriumHost [--service | install-service <user SID> | uninstall-service | env]");
            return 2;
    }
}
catch (Exception e) when (args.FirstOrDefault() is "install-service" or "uninstall-service")
{
    // The tray shows this text when the elevated step fails
    Console.Error.WriteLine(e.Message);
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
    using var supervisor = new Supervisor(settings, log);
    log.Host("Running per user");
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
        log.Host("Running as the Windows service");
        supervisor = new Supervisor(settings, log);
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
