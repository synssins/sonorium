namespace Sonorium.Tray;

/// <summary>How times and addresses read in the console (boards Win_Status, Win_Updates).</summary>
public static class Format
{
    /// <summary>"3 hours", "1 minute", "less than a minute".</summary>
    public static string Duration(TimeSpan span)
    {
        static string Plural(int n, string unit) => n == 1 ? $"1 {unit}" : $"{n} {unit}s";
        if (span.TotalDays >= 1) return Plural((int)span.TotalDays, "day");
        if (span.TotalHours >= 1) return Plural((int)span.TotalHours, "hour");
        if (span.TotalMinutes >= 1) return Plural((int)span.TotalMinutes, "minute");
        return "less than a minute";
    }

    /// <summary>"today at 9:12 AM", "yesterday at 9:12 AM", or the date.</summary>
    public static string When(DateTimeOffset at)
    {
        var local = at.ToLocalTime();
        var time = local.ToString("t");
        return (DateTime.Today - local.Date).Days switch
        {
            0 => $"today at {time}",
            1 => $"yesterday at {time}",
            _ => $"{local:d} at {time}",
        };
    }

    /// <summary>The host part of an address: http://homeassistant.local:8123 → homeassistant.local.</summary>
    public static string Host(string url) => Uri.TryCreate(url, UriKind.Absolute, out var u) ? u.Host : url;
}
