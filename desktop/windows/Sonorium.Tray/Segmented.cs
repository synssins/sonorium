using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;

namespace Sonorium.Tray;

/// <summary>
/// The boards' segmented choice (.seg): joined buttons, the chosen one in the accent colour.
/// Items are strings; an item starting with "!" gets Windows' administrator shield.
/// </summary>
public sealed partial class Segmented : UserControl
{
    readonly StackPanel panel = new() { Orientation = Orientation.Horizontal };
    readonly List<ToggleButton> buttons = [];
    int selected = -1;
    bool quiet;

    /// <summary>The user picked another item (not raised when SelectedIndex is set in code).</summary>
    public event Action<int>? SelectionChanged;

    public Segmented()
    {
        Content = panel;
        IsTabStop = false;
    }

    public string Items
    {
        set
        {
            panel.Children.Clear();
            buttons.Clear();
            var items = value.Split('|');
            for (var i = 0; i < items.Length; i++)
            {
                var index = i;
                var text = items[i];
                var shield = text.StartsWith('!');
                if (shield) text = text[1..];
                var content = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
                // Segoe Fluent Icons' shield, as Windows marks steps that ask for administrator permission
                if (shield) content.Children.Add(new FontIcon { Glyph = "", FontSize = 14 });
                content.Children.Add(new TextBlock { Text = text });
                var button = new ToggleButton
                {
                    Content = content,
                    Padding = new Thickness(14, 5, 14, 6),
                    CornerRadius = new CornerRadius(i == 0 ? 4 : 0, i == items.Length - 1 ? 4 : 0, i == items.Length - 1 ? 4 : 0, i == 0 ? 4 : 0),
                    Margin = new Thickness(i == 0 ? 0 : -1, 0, 0, 0),
                };
                Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(button, text);
                button.Click += (_, _) =>
                {
                    Select(index);
                    if (!quiet) SelectionChanged?.Invoke(index);
                };
                buttons.Add(button);
                panel.Children.Add(button);
            }
            Select(selected);
        }
    }

    public int SelectedIndex
    {
        get => selected;
        set
        {
            quiet = true;
            Select(value);
            quiet = false;
        }
    }

    void Select(int index)
    {
        selected = index;
        for (var i = 0; i < buttons.Count; i++) buttons[i].IsChecked = i == index;
    }
}
