namespace Sonorium.Desktop;

/// <summary>
/// How the tray console, the host and Sonorium talk to each other.
///
/// Per user, the host runs in the user's session and listens for two named events
/// (Local\, so per sign-in session): the tray sets one to stop or restart Sonorium.
/// As a service, the Service Control Manager does that instead (stop, start), and a
/// restart is the custom control code <see cref="ServiceRestartControl"/>.
/// </summary>
public static class HostProtocol
{
    /// <summary>Sonorium's exit code for "start me again now" (its web UI's Restart).</summary>
    public const int RestartExitCode = 75;

    /// <summary>The service's custom control code: restart Sonorium (the Python process), keep the service running.</summary>
    public const int ServiceRestartControl = 128;

    public const string UserStopEvent = @"Local\Sonorium.Host.Stop";
    public const string UserRestartEvent = @"Local\Sonorium.Host.Restart";
    public const string UserInstanceMutex = @"Local\Sonorium.Host";
}
