using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Sonorium.Desktop;
using Windows.ApplicationModel.DataTransfer;

namespace Sonorium.Tray.Pages;

public sealed partial class AboutPage : Page
{
    public AboutPage()
    {
        InitializeComponent();
        var how = AppState.Current.Host.Mode == RunMode.Service ? "Running as a service" : "Running when you sign in";
        Version.Text = $"Version {Updates.CurrentVersion} · {how}";
    }

    void CopyVersion_Click(object sender, RoutedEventArgs e)
    {
        var package = new DataPackage();
        package.SetText(Updates.CurrentVersion);
        Clipboard.SetContent(package);
    }

    /// <summary>A web page, or a file in the install folder (opened with its usual app).</summary>
    void Open_Click(object sender, RoutedEventArgs e)
    {
        var target = (string)((Button)sender).Tag;
        Actions.OpenUrl(target.StartsWith("https://") ? target : Path.Combine(Paths.ProgramFolder, target));
    }
}
