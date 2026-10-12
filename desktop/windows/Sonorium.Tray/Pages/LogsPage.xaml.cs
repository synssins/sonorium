using System.Collections.ObjectModel;
using System.Text;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage.Pickers;

namespace Sonorium.Tray.Pages;

/// <summary>
/// Board Win_Logs: today's log file (Sonorium-yyyy-MM-dd.log in the logs folder), following it
/// as it grows; the last 5000 lines. Warnings and errors are Sonorium's "WARNING:" and "ERROR:" lines.
/// </summary>
public sealed partial class LogsPage : Page
{
    const int MaxLines = 5000;
    readonly ObservableCollection<LogLine> shown = [];
    readonly List<string> all = [];
    DispatcherQueueTimer? timer;
    string? file;
    long position;

    public LogsPage()
    {
        InitializeComponent();
        Lines.ItemsSource = shown;
        Search.TextChanged += (_, _) => Filter();
        Level.SelectionChanged += (_, _) => Filter();
    }

    protected override void OnNavigatedTo(NavigationEventArgs e)
    {
        timer = DispatcherQueue.CreateTimer();
        timer.Interval = TimeSpan.FromSeconds(2);
        timer.Tick += (_, _) => ReadNew();
        timer.Start();
        ReadNew();
    }

    protected override void OnNavigatedFrom(NavigationEventArgs e) => timer?.Stop();

    string Folder => AppState.Current.Settings.Logs;

    void ReadNew()
    {
        var today = Path.Combine(Folder, $"Sonorium-{DateTime.Now:yyyy-MM-dd}.log");
        if (today != file)
        {
            file = today;
            position = 0;
            all.Clear();
        }
        if (!File.Exists(file)) { Filter(); return; }
        try
        {
            using var stream = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            if (stream.Length < position) { position = 0; all.Clear(); }
            stream.Position = position;
            using var reader = new StreamReader(stream, Encoding.UTF8);
            var added = false;
            while (reader.ReadLine() is { } line)
            {
                all.Add(line);
                added = true;
            }
            position = stream.Position;
            if (all.Count > MaxLines) all.RemoveRange(0, all.Count - MaxLines);
            if (added) Filter();
        }
        catch (IOException) { }
    }

    bool Matches(string line)
    {
        var level = Level.SelectedIndex;
        if (level == 1 && !(line.Contains("WARNING") || line.Contains("ERROR"))) return false;
        if (level == 2 && !line.Contains("ERROR")) return false;
        var q = Search.Text.Trim();
        return q.Length == 0 || line.Contains(q, StringComparison.OrdinalIgnoreCase);
    }

    void Filter()
    {
        shown.Clear();
        foreach (var line in all.Where(Matches)) shown.Add(new LogLine(line));
        if (shown.Count > 0) Lines.ScrollIntoView(shown[^1]);
    }

    string Text(bool selectedOnly)
    {
        var lines = selectedOnly && Lines.SelectedItems.Count > 0 ? Lines.SelectedItems.Cast<LogLine>() : shown;
        return string.Join(Environment.NewLine, lines.Select(l => l.Text));
    }

    void Copy_Click(object sender, RoutedEventArgs e)
    {
        var package = new DataPackage();
        package.SetText(Text(selectedOnly: true));
        Clipboard.SetContent(package);
    }

    async void Save_Click(object sender, RoutedEventArgs e)
    {
        var picker = new FileSavePicker { SuggestedFileName = $"Sonorium-{DateTime.Now:yyyy-MM-dd}" };
        picker.FileTypeChoices.Add("Log file", [".log"]);
        picker.FileTypeChoices.Add("Text file", [".txt"]);
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(Actions.ConsoleOwner!));
        var target = await picker.PickSaveFileAsync();
        if (target is not null) await File.WriteAllTextAsync(target.Path, Text(selectedOnly: false), new UTF8Encoding(false));
    }

    void OpenFolder_Click(object sender, RoutedEventArgs e) => Actions.OpenFolder(Folder);
}

/// <summary>A log line, in the board's colours: warnings amber, errors red (theme brushes, so contrast themes work).</summary>
public sealed class LogLine(string text)
{
    public string Text { get; } = text;

    public Microsoft.UI.Xaml.Media.Brush Foreground => (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources[
        Text.Contains("ERROR") ? "SystemFillColorCriticalBrush"
        : Text.Contains("WARNING") ? "SystemFillColorCautionBrush"
        : "TextFillColorPrimaryBrush"];
}
