using System.Text;

namespace Sonorium.Host;

/// <summary>
/// Sonorium's log files: one per day (Sonorium-2026-10-12.log) in the logs folder, kept
/// for <see cref="KeepDays"/> days. Sonorium's own output is written as it comes; the
/// host's lines start with the time and "Host:".
/// </summary>
public sealed class LogWriter : IDisposable
{
    public const int KeepDays = 7;
    readonly string folder;
    readonly object gate = new();
    StreamWriter? writer;
    DateOnly day;

    public LogWriter(string folder)
    {
        this.folder = folder;
        Directory.CreateDirectory(folder);
        Prune();
    }

    public static string FileFor(string folder, DateOnly day) => Path.Combine(folder, $"Sonorium-{day:yyyy-MM-dd}.log");

    /// <summary>A line of Sonorium's output, as it came.</summary>
    public void Raw(string line)
    {
        lock (gate)
        {
            var today = DateOnly.FromDateTime(DateTime.Now);
            if (writer is null || today != day)
            {
                writer?.Dispose();
                day = today;
                var stream = new FileStream(FileFor(folder, day), FileMode.Append, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete);
                writer = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true };
                Prune();
            }
            writer.WriteLine(line);
        }
    }

    /// <summary>A line from the host itself.</summary>
    public void Host(string message) => Raw($"{DateTime.Now:HH:mm:ss.fff} Host: {message}");

    void Prune()
    {
        var cutoff = DateTime.Now.Date.AddDays(-KeepDays);
        foreach (var file in Directory.EnumerateFiles(folder, "Sonorium-*.log"))
        {
            try
            {
                if (File.GetLastWriteTime(file) < cutoff) File.Delete(file);
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    public void Dispose()
    {
        lock (gate) writer?.Dispose();
    }
}
