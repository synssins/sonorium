using System.Net.Http.Headers;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Sonorium.Desktop;

/// <summary>A newer Windows build of Sonorium on GitHub.</summary>
public sealed record UpdateInfo(string Version, string Notes, string InstallerUrl, string InstallerName, string? Sha256, bool Preview);

/// <summary>
/// Finds updates in the GitHub releases of synssins/sonorium. A release counts when it has
/// a Windows installer asset, Sonorium-Setup-&lt;version&gt;.exe; other releases (the add-on, the old
/// Windows app) are ignored. Stable sees full releases only; Preview also sees pre-releases.
/// </summary>
public static class Updates
{
    public const string Repository = "synssins/sonorium";
    public const string InstallerPrefix = "Sonorium-Setup-";

    /// <summary>The version this build was made from (the server package's version, set by CI).</summary>
    public static string CurrentVersion
    {
        get
        {
            // Documentation screenshots only: act as an older version so an update shows
            if (Environment.GetEnvironmentVariable("SONORIUM_PRETEND_VERSION") is { Length: > 0 } pretend) return pretend;
            var info = Assembly.GetEntryAssembly()?.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion
                       ?? "0.0.0";
            var plus = info.IndexOf('+');
            return plus < 0 ? info : info[..plus];
        }
    }

    public static async Task<UpdateInfo?> CheckAsync(bool preview, string? current = null, HttpClient? http = null, CancellationToken cancel = default)
    {
        current ??= CurrentVersion;
        var client = http ?? Http.Value;
        // 100, the most GitHub gives in one page: preview builds come often, and a stable release
        // must not drop off the first page behind them
        using var response = await client.GetAsync($"https://api.github.com/repos/{Repository}/releases?per_page=100", cancel);
        if (GitHubBusyException.From(response) is { } busy) throw busy;
        response.EnsureSuccessStatusCode();
        var releases = await JsonSerializer.DeserializeAsync<List<Release>>(await response.Content.ReadAsStreamAsync(cancel), cancellationToken: cancel) ?? [];
        return Newest(releases, preview, current);
    }

    public static UpdateInfo? Newest(IEnumerable<Release> releases, bool preview, string current)
    {
        UpdateInfo? best = null;
        foreach (var release in releases)
        {
            if (release.Draft || (release.Prerelease && !preview)) continue;
            var asset = release.Assets.FirstOrDefault(a =>
                a.Name.StartsWith(InstallerPrefix, StringComparison.OrdinalIgnoreCase) &&
                a.Name.EndsWith(".exe", StringComparison.OrdinalIgnoreCase));
            if (asset is null) continue;
            var version = asset.Name[InstallerPrefix.Length..^4];
            if (!SemVer.TryParse(version, out _)) continue;
            if (SemVer.Compare(version, current) <= 0) continue;
            if (best is not null && SemVer.Compare(version, best.Version) <= 0) continue;
            var sha = asset.Digest is { } d && d.StartsWith("sha256:", StringComparison.Ordinal) ? d[7..] : null;
            best = new UpdateInfo(version, release.Body ?? "", asset.Url, asset.Name, sha, release.Prerelease);
        }
        return best;
    }

    static readonly Lazy<HttpClient> Http = new(() =>
    {
        var client = new HttpClient { Timeout = TimeSpan.FromSeconds(30) };
        client.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("Sonorium-Windows", CurrentVersion.Replace('+', '-')));
        client.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/vnd.github+json"));
        return client;
    });

    public sealed class Release
    {
        [JsonPropertyName("tag_name")] public string Tag { get; set; } = "";
        [JsonPropertyName("body")] public string? Body { get; set; }
        [JsonPropertyName("draft")] public bool Draft { get; set; }
        [JsonPropertyName("prerelease")] public bool Prerelease { get; set; }
        [JsonPropertyName("assets")] public List<Asset> Assets { get; set; } = [];
    }

    public sealed class Asset
    {
        [JsonPropertyName("name")] public string Name { get; set; } = "";
        [JsonPropertyName("browser_download_url")] public string Url { get; set; } = "";
        /// <summary>"sha256:…", which GitHub computes for every uploaded asset.</summary>
        [JsonPropertyName("digest")] public string? Digest { get; set; }
    }
}

/// <summary>
/// GitHub answered "rate limit exceeded" (403 or 429): it limits how often a computer may ask
/// without signing in. Not a failure; the check is tried again later (RetryAt).
/// </summary>
public sealed class GitHubBusyException(DateTimeOffset retryAt, string detail)
    : Exception(UserMessage)
{
    public const string UserMessage = "GitHub is busy right now. Sonorium will check again later.";

    /// <summary>When GitHub says it will answer again (5 minutes to 1 hour from now).</summary>
    public DateTimeOffset RetryAt { get; } = retryAt;

    /// <summary>The HTTP answer, for the logs.</summary>
    public string Detail { get; } = detail;

    public static GitHubBusyException? From(HttpResponseMessage response)
    {
        var code = (int)response.StatusCode;
        if (code != 403 && code != 429) return null;
        string? Header(string name) => response.Headers.TryGetValues(name, out var v) ? v.FirstOrDefault() : null;
        // A 403 is a rate limit only when GitHub says so (no requests left, or a Retry-After);
        // any other 403 stays an error
        var remaining = Header("x-ratelimit-remaining");
        var retryAfter = response.Headers.RetryAfter;
        if (code == 403 && remaining != "0" && retryAfter is null) return null;

        var now = DateTimeOffset.Now;
        var at = now.AddMinutes(15);
        if (retryAfter?.Delta is { } delta) at = now + delta;
        else if (retryAfter?.Date is { } date) at = date.ToLocalTime();
        else if (long.TryParse(Header("x-ratelimit-reset"), out var reset)) at = DateTimeOffset.FromUnixTimeSeconds(reset).ToLocalTime().AddMinutes(1);
        if (at < now.AddMinutes(5)) at = now.AddMinutes(5);
        if (at > now.AddHours(1)) at = now.AddHours(1);
        return new GitHubBusyException(at, $"{code} {response.ReasonPhrase} (rate limit), retry after {at:HH:mm}");
    }
}

/// <summary>Semantic versions as the add-on uses them: 1.6.2, 1.6.2-dev.67 (a pre-release sorts before 1.6.2).</summary>
public static class SemVer
{
    public static bool TryParse(string text, out (int[] Core, string[] Pre) value)
    {
        value = default;
        var dash = text.IndexOf('-');
        var core = (dash < 0 ? text : text[..dash]).Split('.');
        if (core.Length != 3) return false;
        var numbers = new int[3];
        for (var i = 0; i < 3; i++)
            if (!int.TryParse(core[i], out numbers[i]) || numbers[i] < 0) return false;
        var pre = dash < 0 ? [] : text[(dash + 1)..].Split('.');
        if (pre.Any(string.IsNullOrEmpty)) return false;
        value = (numbers, pre);
        return true;
    }

    /// <summary>&lt;0, 0 or &gt;0 like CompareTo. Something that isn't a version sorts first.</summary>
    public static int Compare(string a, string b)
    {
        var okA = TryParse(a, out var x);
        var okB = TryParse(b, out var y);
        if (!okA || !okB) return okA.CompareTo(okB);
        for (var i = 0; i < 3; i++)
            if (x.Core[i] != y.Core[i]) return x.Core[i].CompareTo(y.Core[i]);
        if (x.Pre.Length == 0 || y.Pre.Length == 0) return y.Pre.Length.CompareTo(x.Pre.Length);
        for (var i = 0; i < Math.Min(x.Pre.Length, y.Pre.Length); i++)
        {
            var numA = int.TryParse(x.Pre[i], out var na);
            var numB = int.TryParse(y.Pre[i], out var nb);
            var c = numA && numB ? na.CompareTo(nb)
                : numA ? -1 : numB ? 1
                : string.CompareOrdinal(x.Pre[i], y.Pre[i]);
            if (c != 0) return c;
        }
        return x.Pre.Length.CompareTo(y.Pre.Length);
    }
}
