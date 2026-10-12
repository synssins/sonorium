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
        using (var run = Registry.CurrentUser.OpenSubKey(RunKey))
            StartAtSignIn.IsOn = run?.GetValue("Sonorium") is string;
        OpenBrowser.IsOn = s.OpenBrowserOnStart;
        Port.Text = s.Port.ToString();
        PortRow.Description = $"The address Sonorium's web page and speakers use: http://{Environment.MachineName.ToLowerInvariant()}:{s.Port}";
        Channels.Value = s.MaxChannels;
        StreamUrl.Text = state.Connection?["stream_url"]?.ToString() ?? "";
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
