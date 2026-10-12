using Microsoft.UI.Xaml.Controls;
using Sonorium.Desktop;

namespace Sonorium.Tray.Pages;

public sealed partial class AboutPage : Page
{
    public AboutPage()
    {
        InitializeComponent();
        Version.Text = $"Version {Updates.CurrentVersion}";
    }
}
