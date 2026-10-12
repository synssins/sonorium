using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Navigation;
using Sonorium.Desktop;

namespace Sonorium.Tray.Pages;

/// <summary>
/// Board Win_Connections: Sonorium's own Settings › Connection, through its API. A field is
/// saved when it loses focus with a new value (Sonorium then restarts to use it). A saved token
/// or password never comes back: its box stays empty, and empty keeps the saved one.
/// </summary>
public sealed partial class ConnectionsPage : Page
{
    readonly AppState state = AppState.Current;
    JsonNode? saved;
    bool loading;

    public ConnectionsPage()
    {
        InitializeComponent();
        UseHa.Toggled += on => _ = SetUseHa(on);
        UseMqtt.Toggled += on => _ = SetUseMqtt(on);
        FindSpeakers.Toggled += on => _ = SetFindSpeakers(on);
        HaUrl.LostFocus += (_, _) => _ = SaveIfChanged("ha_url", HaUrl.Text.Trim());
        HaToken.LostFocus += (_, _) => _ = SaveSecret("ha_token", HaToken);
        MqttHost.LostFocus += (_, _) => _ = SaveIfChanged("mqtt_host", MqttHost.Text.Trim());
        MqttPort.LostFocus += (_, _) => _ = SaveIfChanged("mqtt_port", MqttPort.Text.Trim());
        MqttUser.LostFocus += (_, _) => _ = SaveIfChanged("mqtt_username", MqttUser.Text.Trim());
        MqttPassword.LostFocus += (_, _) => _ = SaveSecret("mqtt_password", MqttPassword);
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e) => await Load();

    async Task Load()
    {
        loading = true;
        try
        {
            saved = await state.Api.Get("/api/connection");
            var install = await state.Api.Get("/api/install");
            HaUrl.Text = saved?["ha_url"]?.ToString() ?? "";
            HaToken.PlaceholderText = saved?["ha_token_set"]?.GetValue<bool>() == true ? "••••••••••••••••" : "";
            MqttHost.Text = saved?["mqtt_host"]?.ToString() ?? "";
            MqttPort.Text = saved?["mqtt_port"]?.ToString() ?? "";
            MqttUser.Text = saved?["mqtt_username"]?.ToString() ?? "";
            MqttPassword.PlaceholderText = saved?["mqtt_password_set"]?.GetValue<bool>() == true ? "••••••••" : "";
            UseHa.IsOn = !string.IsNullOrEmpty(HaUrl.Text);
            UseMqtt.IsOn = !string.IsNullOrEmpty(MqttHost.Text);
            FindSpeakers.IsOn = install?["features"]?["network_speakers"]?["enabled"]?.GetValue<bool>() == true;
            Problem.IsOpen = false;
        }
        catch (Exception ex) when (ex is SonoriumApiException or HttpRequestException or TaskCanceledException)
        {
            Show(state.Running ? ex.Message : "Sonorium isn't running.");
        }
        finally
        {
            loading = false;
            ShowRows();
        }
    }

    void ShowRows()
    {
        HaUrlRow.Visibility = HaTokenRow.Visibility = UseHa.IsOn ? Visibility.Visible : Visibility.Collapsed;
        BrokerRow.Visibility = LoginRow.Visibility = UseMqtt.IsOn ? Visibility.Visible : Visibility.Collapsed;
    }

    async Task SetUseHa(bool on)
    {
        ShowRows();
        if (!on && !string.IsNullOrEmpty(saved?["ha_url"]?.ToString()))
            await Call(() => state.Api.Delete("/api/connection/ha"));
    }

    async Task SetUseMqtt(bool on)
    {
        ShowRows();
        if (!on && !string.IsNullOrEmpty(saved?["mqtt_host"]?.ToString()))
            await Call(() => state.Api.Delete("/api/connection/mqtt"));
    }

    // Takes effect when Sonorium restarts, which this asks for
    Task SetFindSpeakers(bool on) =>
        Call(() => state.Api.Put("/api/install/features", new Dictionary<string, object> { ["network_speakers"] = on, ["restart"] = true }));

    async Task SaveIfChanged(string field, string value)
    {
        if (loading || value == (saved?[field]?.ToString() ?? "")) return;
        if (field is "ha_url" && value.Length > 0 && !value.StartsWith("http://") && !value.StartsWith("https://"))
            value = "http://" + value;
        await Call(() => state.Api.Put("/api/connection", new Dictionary<string, object?> { [field] = value }));
    }

    async Task SaveSecret(string field, PasswordBox box)
    {
        if (loading || box.Password.Length == 0) return;
        var value = box.Password;
        box.Password = "";
        await Call(() => state.Api.Put("/api/connection", new Dictionary<string, object?> { [field] = value }));
    }

    async Task Call(Func<Task<JsonNode?>> call)
    {
        try
        {
            await call();
            Problem.IsOpen = false;
            await Task.Delay(500);
            await Load();
        }
        catch (Exception ex) when (ex is SonoriumApiException or HttpRequestException or TaskCanceledException)
        {
            Show(ex.Message);
        }
    }

    void TestHa_Click(object sender, RoutedEventArgs e) => _ = Test((Button)sender, "ha", new()
    {
        ["ha_url"] = HaUrl.Text.Trim(),
        ["ha_token"] = HaToken.Password,
    });

    void TestMqtt_Click(object sender, RoutedEventArgs e) => _ = Test((Button)sender, "mqtt", new()
    {
        ["mqtt_host"] = MqttHost.Text.Trim(),
        ["mqtt_port"] = MqttPort.Text.Trim(),
        ["mqtt_username"] = MqttUser.Text.Trim(),
        ["mqtt_password"] = MqttPassword.Password,
    });

    /// <summary>Tries the values as typed (unsaved) on top of the saved ones; the answer shows by the button.</summary>
    async Task Test(Button button, string kind, Dictionary<string, string> values)
    {
        button.IsEnabled = false;
        string message;
        try
        {
            var result = await state.Api.Post("/api/connection/test", new { kind, values });
            message = result?["message"]?.ToString() ?? (result?["success"]?.GetValue<bool>() == true ? "Connected" : "Couldn't connect");
        }
        catch (Exception ex) when (ex is SonoriumApiException or HttpRequestException or TaskCanceledException)
        {
            message = ex.Message;
        }
        finally
        {
            button.IsEnabled = true;
        }
        var flyout = new Flyout { Content = new TextBlock { Text = message, TextWrapping = TextWrapping.Wrap, MaxWidth = 320 } };
        flyout.ShowAt(button, new FlyoutShowOptions { Placement = FlyoutPlacementMode.Bottom });
    }

    void Show(string message)
    {
        Problem.Message = message;
        Problem.IsOpen = true;
    }
}
