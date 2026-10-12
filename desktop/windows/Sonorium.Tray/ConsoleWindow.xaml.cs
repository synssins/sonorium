using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;

namespace Sonorium.Tray;

public sealed partial class ConsoleWindow : Window
{
    static readonly Dictionary<string, Type> PageTypes = new()
    {
        ["Status"] = typeof(Pages.StatusPage),
        ["General"] = typeof(Pages.GeneralPage),
        ["Folders"] = typeof(Pages.FoldersPage),
        ["Connections"] = typeof(Pages.ConnectionsPage),
        ["Updates"] = typeof(Pages.UpdatesPage),
        ["Logs"] = typeof(Pages.LogsPage),
        ["About"] = typeof(Pages.AboutPage),
    };

    public ConsoleWindow()
    {
        InitializeComponent();
        // Mica, or a solid colour when transparency effects are off (the backdrop handles that)
        SystemBackdrop = new MicaBackdrop();
        AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "Sonorium.ico"));
        // The system title bar follows the apps' light/dark mode (and the accent colour, when Windows shows it)
        AppWindow.TitleBar.PreferredTheme = Microsoft.UI.Windowing.TitleBarTheme.UseDefaultAppMode;
        // The board's 1080 × 720, at the scale of the monitor it opens on (known once it's shown)
        Nav.Loaded += (_, _) =>
        {
            var scale = Nav.XamlRoot.RasterizationScale;
            AppWindow.Resize(new Windows.Graphics.SizeInt32((int)(1080 * scale), (int)(720 * scale)));
        };
    }

    /// <summary>Shows a page; Updates can check at once (the menu's "Check for updates…").</summary>
    public void Navigate(string page, bool checkNow = false)
    {
        var item = Nav.MenuItems.OfType<NavigationViewItem>().First(i => (string)i.Tag == page);
        if (ReferenceEquals(Nav.SelectedItem, item))
        {
            if (checkNow && Pages.Content is Pages.UpdatesPage updates) _ = updates.CheckNow();
            return;
        }
        pendingCheck = checkNow;
        Nav.SelectedItem = item;
    }

    bool pendingCheck;

    void Nav_SelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (args.SelectedItem is not NavigationViewItem item) return;
        Pages.Navigate(PageTypes[(string)item.Tag], pendingCheck, new SuppressNavigationTransitionInfo());
        pendingCheck = false;
    }
}
