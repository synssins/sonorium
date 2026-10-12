using System.Diagnostics;
using System.ServiceProcess;

namespace Sonorium.Desktop;

/// <summary>
/// Starts, stops and restarts Sonorium for the tray console, whichever way it runs:
/// as the Windows service (through the Service Control Manager; the installing user was
/// allowed to without elevation), or per user (SonoriumHost.exe in this sign-in session,
/// through its named events).
/// </summary>
public abstract class HostControl
{
    public abstract RunMode Mode { get; }
    /// <summary>Whether the host (not necessarily Sonorium inside it) is running.</summary>
    public abstract bool IsRunning { get; }
    public abstract void Start();
    public abstract void Stop();
    public abstract void Restart();

    /// <summary>The service when it's installed, else per user.</summary>
    public static HostControl Detect() => ServiceInstalled() ? new ServiceHost() : new UserHost();

    public static bool ServiceInstalled()
    {
        try
        {
            using var sc = new ServiceController(Paths.ServiceName);
            _ = sc.Status;
            return true;
        }
        catch (InvalidOperationException)
        {
            return false;
        }
    }

    sealed class ServiceHost : HostControl
    {
        public override RunMode Mode => RunMode.Service;

        public override bool IsRunning
        {
            get
            {
                using var sc = new ServiceController(Paths.ServiceName);
                return sc.Status is ServiceControllerStatus.Running or ServiceControllerStatus.StartPending;
            }
        }

        public override void Start()
        {
            using var sc = new ServiceController(Paths.ServiceName);
            if (sc.Status is ServiceControllerStatus.Stopped or ServiceControllerStatus.StopPending)
            {
                if (sc.Status == ServiceControllerStatus.StopPending)
                    sc.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(30));
                sc.Start();
            }
        }

        public override void Stop()
        {
            using var sc = new ServiceController(Paths.ServiceName);
            if (sc.Status is ServiceControllerStatus.Running or ServiceControllerStatus.StartPending)
                sc.Stop();
        }

        public override void Restart()
        {
            using var sc = new ServiceController(Paths.ServiceName);
            if (sc.Status == ServiceControllerStatus.Running)
                sc.ExecuteCommand(HostProtocol.ServiceRestartControl);
            else
                Start();
        }
    }

    sealed class UserHost : HostControl
    {
        public override RunMode Mode => RunMode.User;

        public override bool IsRunning => Mutex.TryOpenExisting(HostProtocol.UserInstanceMutex, out var m) && Dispose(m);

        static bool Dispose(Mutex m)
        {
            m.Dispose();
            return true;
        }

        public override void Start()
        {
            if (IsRunning) return;
            Process.Start(new ProcessStartInfo(Paths.HostExe) { UseShellExecute = false, CreateNoWindow = true })?.Dispose();
        }

        public override void Stop() => Signal(HostProtocol.UserStopEvent);

        public override void Restart()
        {
            if (!Signal(HostProtocol.UserRestartEvent)) Start();
        }

        static bool Signal(string name)
        {
            if (!EventWaitHandle.TryOpenExisting(name, out var e)) return false;
            using (e) e.Set();
            return true;
        }
    }
}
