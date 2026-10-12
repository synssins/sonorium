using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Sonorium.Desktop;

/// <summary>
/// The console's calls to Sonorium on http://127.0.0.1:&lt;port&gt;. It signs in with the
/// console token Sonorium writes to SONORIUM_CONSOLE_TOKEN_FILE (accepted from this PC
/// only). Secrets are only ever sent to Sonorium, which stores them itself.
/// </summary>
public sealed class SonoriumApi : IDisposable
{
    readonly HttpClient http;
    readonly string tokenFile;

    public SonoriumApi(DesktopSettings settings)
    {
        http = new HttpClient { BaseAddress = new Uri(settings.LocalUrl + "/"), Timeout = TimeSpan.FromSeconds(10) };
        tokenFile = settings.ConsoleTokenFile;
    }

    /// <summary>Read each time: Sonorium makes a new one if the file goes missing.</summary>
    string? Token()
    {
        try
        {
            var token = File.ReadAllText(tokenFile).Trim();
            return token.StartsWith("sc_", StringComparison.Ordinal) ? token : null;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return null;
        }
    }

    HttpRequestMessage Request(HttpMethod method, string path, object? body = null)
    {
        var request = new HttpRequestMessage(method, path.TrimStart('/'));
        if (Token() is { } token) request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        if (body is not null) request.Content = JsonContent.Create(body);
        return request;
    }

    async Task<JsonNode?> Send(HttpMethod method, string path, object? body, CancellationToken cancel)
    {
        using var request = Request(method, path, body);
        using var response = await http.SendAsync(request, cancel);
        var text = await response.Content.ReadAsStringAsync(cancel);
        if (!response.IsSuccessStatusCode)
        {
            string? detail = null;
            try { detail = JsonNode.Parse(text)?["detail"]?.ToString(); } catch (JsonException) { }
            throw new SonoriumApiException((int)response.StatusCode, detail ?? response.ReasonPhrase ?? "Error");
        }
        return string.IsNullOrWhiteSpace(text) ? null : JsonNode.Parse(text);
    }

    public Task<JsonNode?> Get(string path, CancellationToken cancel = default) => Send(HttpMethod.Get, path, null, cancel);
    public Task<JsonNode?> Put(string path, object body, CancellationToken cancel = default) => Send(HttpMethod.Put, path, body, cancel);
    public Task<JsonNode?> Post(string path, object body, CancellationToken cancel = default) => Send(HttpMethod.Post, path, body, cancel);
    public Task<JsonNode?> Delete(string path, CancellationToken cancel = default) => Send(HttpMethod.Delete, path, null, cancel);

    /// <summary>GET /api/info (public): version, boot id, features; null when Sonorium doesn't answer.</summary>
    public async Task<JsonNode?> Info(CancellationToken cancel = default)
    {
        try
        {
            return await Get("/api/info", cancel);
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException or SonoriumApiException or JsonException)
        {
            return null;
        }
    }

    public void Dispose() => http.Dispose();
}

public sealed class SonoriumApiException(int status, string detail) : Exception(detail)
{
    public int Status { get; } = status;
}
