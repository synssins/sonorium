using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Markup;

namespace Sonorium.Tray;

/// <summary>
/// One row of a settings card (the boards' .row): icon, title, optional subtext, and its
/// control on the right. Stacked: the control goes on a line of its own under the text
/// (the Folders page's paths).
/// </summary>
[ContentProperty(Name = nameof(Action))]
public sealed partial class SettingRow : UserControl
{
    readonly FontIcon icon = new() { FontSize = 20, VerticalAlignment = VerticalAlignment.Center };
    readonly TextBlock title = new() { TextWrapping = TextWrapping.Wrap };
    readonly TextBlock description = new()
    {
        TextWrapping = TextWrapping.Wrap,
        Style = (Style)Application.Current.Resources["CaptionTextBlockStyle"],
        Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["TextFillColorSecondaryBrush"],
        Margin = new Thickness(0, 2, 0, 0),
        Visibility = Visibility.Collapsed,
    };
    readonly ContentPresenter action = new() { VerticalAlignment = VerticalAlignment.Center };
    readonly Grid grid = new() { ColumnSpacing = 16, MinHeight = 64, Padding = new Thickness(18, 10, 18, 10) };

    public SettingRow()
    {
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var text = new StackPanel { VerticalAlignment = VerticalAlignment.Center, Children = { title, description } };
        Grid.SetColumn(text, 1);
        Grid.SetColumn(action, 2);
        grid.Children.Add(icon);
        grid.Children.Add(text);
        grid.Children.Add(action);
        frame.Child = grid;
        Content = frame;
        IsTabStop = false;
    }

    readonly Border frame = new();

    /// <summary>The line above every row but a card's first.</summary>
    internal void TopLine(bool on)
    {
        var thickness = on ? new Thickness(0, 1, 0, 0) : new Thickness(0);
        if (frame.BorderThickness == thickness && frame.BorderBrush is not null) return; // called on every layout pass
        frame.BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"];
        frame.BorderThickness = thickness;
    }

    public string Glyph { get => icon.Glyph; set => icon.Glyph = value; }
    public string Title { get => title.Text; set => title.Text = value; }

    public string? Description
    {
        get => description.Text;
        set
        {
            description.Text = value ?? "";
            description.Visibility = string.IsNullOrEmpty(value) ? Visibility.Collapsed : Visibility.Visible;
        }
    }

    public object? Action { get => action.Content; set => action.Content = value; }

    /// <summary>The control on its own line under the text, indented to the text.</summary>
    public bool Stacked
    {
        get => Grid.GetRow(action) == 1;
        set
        {
            Grid.SetRow(action, value ? 1 : 0);
            Grid.SetColumn(action, value ? 1 : 2);
            Grid.SetColumnSpan(action, value ? 2 : 1);
            action.Margin = value ? new Thickness(0, 10, 0, 4) : new Thickness(0);
            action.HorizontalAlignment = value ? HorizontalAlignment.Stretch : HorizontalAlignment.Right;
            action.HorizontalContentAlignment = HorizontalAlignment.Stretch;
        }
    }
}

/// <summary>A card of rows (the boards' .card), with a line between rows.</summary>
[ContentProperty(Name = nameof(Rows))]
public sealed partial class SettingCard : UserControl
{
    readonly StackPanel panel = new();

    public SettingCard()
    {
        Content = new Border
        {
            Background = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardBackgroundFillColorDefaultBrush"],
            BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(6),
            Margin = new Thickness(0, 0, 0, 4),
            Child = panel,
        };
        Rows = panel.Children;
        IsTabStop = false;
        panel.LayoutUpdated += (_, _) => Divide();
    }

    public UIElementCollection Rows { get; }

    void Divide()
    {
        var first = true;
        foreach (var child in panel.Children.OfType<FrameworkElement>())
        {
            if (child.Visibility == Visibility.Collapsed) continue;
            if (child is SettingRow row) row.TopLine(!first);
            first = false;
        }
    }
}
