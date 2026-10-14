using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Sonorium.Tray;

/// <summary>A switch with "On"/"Off" to its left, as Windows Settings and the boards show it.</summary>
public sealed partial class LabeledToggle : UserControl
{
    readonly TextBlock label = new()
    {
        VerticalAlignment = VerticalAlignment.Center,
        Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["TextFillColorSecondaryBrush"],
    };
    readonly ToggleSwitch toggle = new() { OnContent = "", OffContent = "", MinWidth = 0, Margin = new Thickness(8, 0, -8, 0) };
    bool quiet;

    public event Action<bool>? Toggled;

    public LabeledToggle()
    {
        Content = new StackPanel { Orientation = Orientation.Horizontal, Children = { label, toggle } };
        toggle.Toggled += (_, _) =>
        {
            label.Text = toggle.IsOn ? "On" : "Off";
            if (!quiet) Toggled?.Invoke(toggle.IsOn);
        };
        label.Text = "Off";
    }

    /// <summary>Set without raising Toggled.</summary>
    public bool IsOn
    {
        get => toggle.IsOn;
        set
        {
            quiet = true;
            toggle.IsOn = value;
            label.Text = value ? "On" : "Off";
            quiet = false;
        }
    }

    /// <summary>The name screen readers announce (the row's title).</summary>
    public string AccessibleName
    {
        get => Microsoft.UI.Xaml.Automation.AutomationProperties.GetName(toggle);
        set => Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(toggle, value);
    }
}
