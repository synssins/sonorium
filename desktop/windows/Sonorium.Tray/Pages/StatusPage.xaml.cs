using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Navigation;

namespace Sonorium.Tray.Pages;

public sealed partial class StatusPage : Page
{
    readonly AppState state = AppState.Current;

    public StatusPage() => InitializeComponent();

    protected override void OnNavigatedTo(NavigationEventArgs e)
    {
        state.Changed += Refresh;
        Refresh();
    }

    protected override void OnNavigatedFrom(NavigationEventArgs e) => state.Changed -= Refresh;

    void Refresh()
    {
        var starting = !state.Running && state.Host.IsRunning;
        State.Text = state.Running ? "Sonorium is running" : starting ? "Sonorium is starting" : "Sonorium is stopped";
        Dot.Fill = new SolidColorBrush(state.Running ? Windows.UI.Color.FromArgb(255, 15, 123, 15)
            : starting ? Windows.UI.Color.FromArgb(255, 157, 93, 0) : Windows.UI.Color.FromArgb(255, 138, 138, 138));
        var parts = new List<string>();
        if (state.Version is { } v) parts.Add($"Version {v}");
        parts.Add($"http://localhost:{state.Settings.Port}");
        if (state.Running && state.RunningSince is { } since) parts.Add($"Running for {Format.Duration(DateTimeOffset.Now - since)}");
        Detail.Text = string.Join(" · ", parts);
        var stopped = !state.Running && !starting;
        StopStartText.Text = stopped ? "Start" : "Stop";
        StopStartIcon.Glyph = stopped ? "" : "";

        Channels.Title = state.ChannelsPlaying switch
        {
            0 => "No channels playing",
            1 => "1 channel playing",
            var n => $"{n} channels playing",
        };
        Channels.Description = string.Join(" · ", state.PlayingLines);

        var conn = state.Connection;
        var haUrl = conn?["ha_url"]?.ToString();
        var haUp = conn?["ha_connected"]?.GetValue<bool>() == true;
        HomeAssistant.Description = string.IsNullOrEmpty(haUrl) ? "Not set up"
            : haUp ? $"Connected to {Format.Host(haUrl)}" : $"Not connected to {Format.Host(haUrl)}";
        HaState.Text = string.IsNullOrEmpty(haUrl) ? "" : haUp ? "Connected" : "Not connected";
        var mqttHost = conn?["mqtt_host"]?.ToString();
        var mqttPort = conn?["mqtt_port"]?.ToString() ?? "1883";
        var mqttUp = conn?["mqtt_connected"]?.GetValue<bool>() == true;
        Mqtt.Description = string.IsNullOrEmpty(mqttHost) ? "Not set up"
            : mqttUp ? $"Connected to {mqttHost}:{mqttPort}" : $"Not connected to {mqttHost}:{mqttPort}";
        MqttState.Text = string.IsNullOrEmpty(mqttHost) ? "" : mqttUp ? "Connected" : "Not connected";

        UpdatesRow.Description = state.Update is { } u ? $"Sonorium {u.Version} is available"
            : state.Settings.LastUpdateCheck is { } at ? $"You're up to date. Last checked {Format.When(at)}" : null;
    }

    void Open_Click(object sender, RoutedEventArgs e) => Actions.OpenSonorium();
    void Restart_Click(object sender, RoutedEventArgs e) => Actions.Restart();

    void StopStart_Click(object sender, RoutedEventArgs e)
    {
        if (StopStartText.Text == "Start") Actions.Start(); else Actions.Stop();
    }

    void StatusPage_Click(object sender, RoutedEventArgs e) => Actions.OpenSonorium();

    async void CheckNow_Click(object sender, RoutedEventArgs e)
    {
        await Updater.Check();
        Refresh();
    }
}
