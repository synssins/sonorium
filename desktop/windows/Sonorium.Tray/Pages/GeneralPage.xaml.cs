using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Navigation;
using Microsoft.Win32;
using Sonorium.Desktop;
using Windows.System;

namespace Sonorium.Tray.Pages;

/// <summary>Board Win_General. Port and Channels restart Sonorium; the stream address is Sonorium's own setting.</summary>
public sealed partial class GeneralPage : Page
{
    const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    readonly AppState state = AppState.Current;

    public GeneralPage()
    {
        InitializeComponent();
        StartAtSignIn.Toggled += SetStartAtSignIn;
        RunAs.SelectionChanged += index => _ = SwitchMode(index == 0 ? RunMode.Service : RunMode.User);
        OpenBrowser.Toggled += on => Save(s => s.OpenBrowserOnStart = on, restart: false);
        Port.LostFocus += (_, _) => ApplyPort();
        Port.KeyDown += (_, e) => { if (e.Key == VirtualKey.Enter) ApplyPort(); };
        Channels.ValueChanged += (_, _) => ApplyChannels();
        StreamUrl.LostFocus += (_, _) => _ = ApplyStreamUrl();
        StreamUrl.KeyDown += (_, e) => { if (e.Key == VirtualKey.Enter) _ = ApplyStreamUrl(); };
    }

    protected override void OnNavigatedTo(NavigationEventArgs e)
    {
        var s = state.Settings;
        ShowMode();
        using (var run = Registry.CurrentUser.OpenSubKey(RunKey))
            StartAtSignIn.IsOn = run?.GetValue("Sonorium") is string;
        OpenBrowser.IsOn = s.OpenBrowserOnStart;
        Port.Text = s.Port.ToString();
        PortRow.Description = $"The address Sonorium's web page and speakers use: http://{Environment.MachineName.ToLowerInvariant()}:{s.Port}";
        Channels.Value = s.MaxChannels;
        StreamUrl.Text = state.Connection?["stream_url"]?.ToString() ?? "";
    }

    /// <summary>The first two rows follow the run mode (boards Win_General_RunAs, Win_General_Service).</summary>
    void ShowMode()
    {
        var service = state.Host.Mode == RunMode.Service;
        RunAs.SelectedIndex = service ? 0 : 1;
        SignInRow.Title = service ? "Show this tray icon when I sign in to Windows" : "Start Sonorium when I sign in to Windows";
        SignInRow.Description = service ? null : "Sonorium and this tray icon start in the background.";
        StartAtSignIn.AccessibleName = SignInRow.Title;
    }

    /// <summary>
    /// Registers or removes the service through SonoriumHost (Windows asks for administrator
    /// permission), copying data and themes across; Sonorium then runs the new way.
    /// </summary>
    async Task SwitchMode(RunMode mode)
    {
        if (mode == state.Host.Mode) return;
        Problem.IsOpen = false;
        RunAs.IsEnabled = false;
        try
        {
            var user = DesktopSettings.Load(RunMode.User);
            string arguments;
            if (mode == RunMode.Service)
            {
                var programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
                if (!Paths.ProgramFolder.StartsWith(programFiles, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("To run Sonorium as a service, install it for all users.");
                var sid = System.Security.Principal.WindowsIdentity.GetCurrent().User!.Value;
                arguments = $"switch-mode service {sid} \"{user.Data}\" \"{user.Themes}\"";
                // Sonorium per user stops first: the service takes over its port
                state.Host.Stop();
                await Task.Delay(2000);
            }
            else
            {
                arguments = $"switch-mode user \"{user.Data}\" \"{user.Themes}\"";
            }

            var errorFile = Path.Combine(Path.GetTempPath(), HostProtocol.ElevatedErrorFile);
            File.Delete(errorFile);
            var info = new System.Diagnostics.ProcessStartInfo(Paths.HostExe, arguments) { UseShellExecute = true, Verb = "runas" };
            int code;
            try
            {
                using var p = System.Diagnostics.Process.Start(info)!;
                await p.WaitForExitAsync();
                code = p.ExitCode;
            }
            catch (System.ComponentModel.Win32Exception)
            {
                code = -1; // the administrator prompt was cancelled
            }
            if (code != 0)
            {
                var message = File.Exists(errorFile) ? File.ReadAllText(errorFile) : null;
                state.Reload();
                if (state.Host.Mode == RunMode.User && !state.Host.IsRunning) state.Host.Start();
                if (message is not null) throw new InvalidOperationException(message);
                return;
            }
            state.Reload();
            if (state.Host.Mode == RunMode.User) state.Host.Start();
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException or UnauthorizedAccessException)
        {
            Problem.Message = ex.Message;
            Problem.IsOpen = true;
        }
        finally
        {
            RunAs.IsEnabled = true;
            ShowMode();
        }
    }

    void SetStartAtSignIn(bool on)
    {
        using var run = Registry.CurrentUser.CreateSubKey(RunKey);
        if (on) run.SetValue("Sonorium", $"\"{Paths.TrayExe}\"");
        else run.DeleteValue("Sonorium", throwOnMissingValue: false);
    }

    void ApplyPort()
    {
        if (!int.TryParse(Port.Text.Trim(), out var port) || port is < 1 or > 65535)
        {
            Port.Text = state.Settings.Port.ToString();
            return;
        }
        if (port == state.Settings.Port) return;
        Save(s => s.Port = port, restart: true);
        PortRow.Description = $"The address Sonorium's web page and speakers use: http://{Environment.MachineName.ToLowerInvariant()}:{port}";
    }

    void ApplyChannels()
    {
        if (double.IsNaN(Channels.Value)) { Channels.Value = state.Settings.MaxChannels; return; }
        var n = (int)Math.Clamp(Math.Round(Channels.Value), 1, 10);
        if (n == state.Settings.MaxChannels) return;
        Save(s => s.MaxChannels = n, restart: true);
    }

    async Task ApplyStreamUrl()
    {
        var url = StreamUrl.Text.Trim();
        if (url == (state.Connection?["stream_url"]?.ToString() ?? "")) return;
        try
        {
            // Sonorium saves it and restarts itself
            await state.Api.Put("/api/connection", new Dictionary<string, object?> { ["stream_url"] = url });
        }
        catch (Exception ex) when (ex is SonoriumApiException or HttpRequestException or TaskCanceledException)
        {
            Notifications.ShowError(ex.Message);
            StreamUrl.Text = state.Connection?["stream_url"]?.ToString() ?? "";
        }
    }

    void Save(Action<DesktopSettings> change, bool restart)
    {
        try
        {
            change(state.Settings);
            state.Settings.Save();
            state.Reload();
            if (restart) Actions.Restart();
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            Notifications.ShowError($"Couldn't save: {ex.Message}");
        }
    }
}
