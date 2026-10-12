using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using Sonorium.Desktop;
using Windows.Storage.Pickers;

namespace Sonorium.Tray.Pages;

/// <summary>
/// Board Win_Folders. Browse… picks a new place: Sonorium stops, the folder's contents move
/// there, desktop.json points at it, and Sonorium starts again.
/// </summary>
public sealed partial class FoldersPage : Page
{
    readonly AppState state = AppState.Current;

    public FoldersPage() => InitializeComponent();

    protected override void OnNavigatedTo(NavigationEventArgs e) => Show();

    void Show()
    {
        var s = state.Settings;
        Themes.Text = s.Themes;
        Data.Text = s.Data;
        Logs.Text = s.Logs;
        Program.Text = Paths.ProgramFolder;
    }

    string PathOf(string tag) => tag switch
    {
        "themes" => state.Settings.Themes,
        "data" => state.Settings.Data,
        "logs" => state.Settings.Logs,
        _ => Paths.ProgramFolder,
    };

    void Open_Click(object sender, RoutedEventArgs e) => Actions.OpenFolder(PathOf((string)((Button)sender).Tag));

    async void Browse_Click(object sender, RoutedEventArgs e)
    {
        var tag = (string)((Button)sender).Tag;
        var picker = new FolderPicker { SuggestedStartLocation = PickerLocationId.ComputerFolder };
        picker.FileTypeFilter.Add("*");
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(Actions.ConsoleOwner!));
        var folder = await picker.PickSingleFolderAsync();
        if (folder is null) return;
        var from = PathOf(tag);
        var to = folder.Path;
        if (string.Equals(Path.GetFullPath(from).TrimEnd('\\'), Path.GetFullPath(to).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase)) return;

        Problem.IsOpen = false;
        ((Button)sender).IsEnabled = false;
        try
        {
            await Task.Run(() => Move(tag, from, to));
            Show();
        }
        catch (Exception ex)
        {
            Problem.Message = ex.Message;
            Problem.IsOpen = true;
        }
        finally
        {
            ((Button)sender).IsEnabled = true;
        }
    }

    void Move(string tag, string from, string to)
    {
        if (IsInside(to, from)) throw new InvalidOperationException("The new folder can't be inside the old one.");
        var host = state.Host;
        var wasRunning = host.IsRunning;
        if (wasRunning)
        {
            host.Stop();
            WaitStopped(host);
        }
        try
        {
            Directory.CreateDirectory(to);
            if (Directory.Exists(from)) MoveContents(from, to);
            if (host.Mode == RunMode.Service) FolderAccess.GrantService(to);
            if (tag == "themes") state.Settings.ThemesFolder = to; else state.Settings.DataFolder = to;
            state.Settings.Save();
            state.Reload();
        }
        finally
        {
            if (wasRunning) host.Start();
        }
    }

    static void WaitStopped(HostControl host)
    {
        for (var i = 0; i < 60 && host.IsRunning; i++) Thread.Sleep(500);
    }

    static bool IsInside(string path, string folder) =>
        Path.GetFullPath(path).TrimEnd('\\').StartsWith(Path.GetFullPath(folder).TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase);

    /// <summary>Moves every file and folder; across drives, copies and then deletes.</summary>
    static void MoveContents(string from, string to)
    {
        foreach (var dir in Directory.EnumerateDirectories(from))
        {
            var target = Path.Combine(to, Path.GetFileName(dir));
            if (Directory.Exists(target)) { MoveContents(dir, target); Directory.Delete(dir, recursive: false); }
            else if (Path.GetPathRoot(dir) == Path.GetPathRoot(target)) Directory.Move(dir, target);
            else { CopyTree(dir, target); Directory.Delete(dir, recursive: true); }
        }
        foreach (var file in Directory.EnumerateFiles(from))
            File.Move(file, Path.Combine(to, Path.GetFileName(file)), overwrite: true);
    }

    static void CopyTree(string from, string to)
    {
        Directory.CreateDirectory(to);
        foreach (var file in Directory.EnumerateFiles(from)) File.Copy(file, Path.Combine(to, Path.GetFileName(file)), overwrite: true);
        foreach (var dir in Directory.EnumerateDirectories(from)) CopyTree(dir, Path.Combine(to, Path.GetFileName(dir)));
    }
}
