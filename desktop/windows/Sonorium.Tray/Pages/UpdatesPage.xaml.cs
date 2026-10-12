using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace Sonorium.Tray.Pages;

/// <summary>Board Win_Updates.</summary>
public sealed partial class UpdatesPage : Page
{
    readonly AppState state = AppState.Current;
    bool loading;

    public UpdatesPage()
    {
        InitializeComponent();
        AutoCheck.Toggled += on => Save(() => state.Settings.CheckForUpdates = on);
        AutoInstall.Toggled += on => Save(() => state.Settings.InstallUpdates = on);
        Channel.SelectionChanged += index =>
        {
            if (loading) return;
            Save(() => state.Settings.UpdateChannel = index == 1 ? "preview" : "stable");
            _ = CheckNow();
        };
    }

    protected override void OnNavigatedTo(NavigationEventArgs e)
    {
        loading = true;
        AutoCheck.IsOn = state.Settings.CheckForUpdates;
        AutoInstall.IsOn = state.Settings.InstallUpdates;
        Channel.SelectedIndex = state.Settings.Channel == "preview" ? 1 : 0;
        loading = false;
        state.Changed += Refresh;
        Refresh();
        if (e.Parameter is true) _ = CheckNow();
    }

    protected override void OnNavigatedFrom(NavigationEventArgs e) => state.Changed -= Refresh;

    void Refresh()
    {
        // "Later" hides this version's notice until a newer version turns up
        var update = state.Update is { } u && u.Version != laterVersion ? u : null;
        Available.Visibility = update is null ? Visibility.Collapsed : Visibility.Visible;
        if (update is not null)
        {
            AvailableTitle.Text = $"Sonorium {update.Version} is available";
            // The release notes' list items on one line, as on the board
            var items = update.Notes.Split('\n').Select(l => l.Trim())
                .Where(l => l.StartsWith("- ") || l.StartsWith("* "))
                .Select(l => l[2..].Trim()).Where(l => l.Length > 0).Take(6).ToList();
            // Otherwise the first line that says something: not a heading, not just "Sonorium <version>"
            var first = update.Notes.Split('\n').Select(l => l.Trim())
                .FirstOrDefault(l => l.Length > 0 && !l.StartsWith('#') && l != $"Sonorium {update.Version}") ?? "";
            var notes = items.Count > 0 ? string.Join(" · ", items) : first;
            Notes.Text = notes.Length == 0 ? "" : $"What's new: {notes}";
            Notes.Visibility = notes.Length == 0 ? Visibility.Collapsed : Visibility.Visible;
        }
        else
        {
            Notes.Visibility = Visibility.Collapsed;
        }
        LastChecked.Description = state.Settings.LastUpdateCheck is { } at
            ? char.ToUpper(Format.When(at)[0]) + Format.When(at)[1..]
            : "Not yet";
    }

    public async Task CheckNow()
    {
        CheckButton.IsEnabled = false;
        Problem.IsOpen = false;
        try
        {
            await Updater.Check(throwOnError: true);
        }
        catch (Exception ex)
        {
            Problem.Message = $"Couldn't check for updates: {ex.Message}";
            Problem.IsOpen = true;
        }
        finally
        {
            CheckButton.IsEnabled = true;
            Refresh();
        }
    }

    void Save(Action change)
    {
        if (loading) return;
        change();
        try { state.Settings.Save(); } catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
    }

    void CheckNow_Click(object sender, RoutedEventArgs e) => _ = CheckNow();
    void UpdateNow_Click(object sender, RoutedEventArgs e) => Updater.InstallNow();
    void Later_Click(object sender, RoutedEventArgs e)
    {
        laterVersion = state.Update?.Version;
        Refresh();
    }

    static string? laterVersion;
}
