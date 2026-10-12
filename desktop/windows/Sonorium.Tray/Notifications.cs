using Microsoft.Windows.AppNotifications;
using Microsoft.Windows.AppNotifications.Builder;
using Sonorium.Desktop;

namespace Sonorium.Tray;

/// <summary>Windows notifications (board Win_Toast): an available update, and errors.</summary>
public static class Notifications
{
    static bool registered;

    public static void Register()
    {
        try
        {
            AppNotificationManager.Default.NotificationInvoked += (_, e) =>
            {
                if (e.Arguments.TryGetValue("action", out var action) && action == "update")
                    Updater.InstallNow();
            };
            AppNotificationManager.Default.Register();
            registered = true;
        }
        catch (Exception)
        {
            // Notifications switched off or unavailable: the blue dot on the menu still shows updates
        }
    }

    public static void Unregister()
    {
        if (registered) AppNotificationManager.Default.Unregister();
    }

    public static void ShowUpdate(UpdateInfo update)
    {
        if (!registered) return;
        var toast = new AppNotificationBuilder()
            .AddText($"Sonorium {update.Version} is available")
            .AddText("The application will close during the update process, and will relaunch when complete.")
            .AddButton(new AppNotificationButton("Update now").AddArgument("action", "update"))
            .AddButton(new AppNotificationButton("Later").AddArgument("action", "later"))
            .BuildNotification();
        toast.Tag = "update";
        AppNotificationManager.Default.Show(toast);
    }

    public static void ShowError(string message)
    {
        if (!registered) return;
        AppNotificationManager.Default.Show(new AppNotificationBuilder().AddText(message).BuildNotification());
    }
}
