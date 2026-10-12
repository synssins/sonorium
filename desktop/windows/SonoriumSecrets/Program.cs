using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

// Sonorium's secret helper on Windows (SONORIUM_SECRET_HELPER). Sonorium runs:
//   SonoriumSecrets store <name>   the value on stdin (UTF-8)
//   SonoriumSecrets get <name>     prints the value; exit 1 when there is none
//   SonoriumSecrets erase <name>
//
// Each value is a file <SONORIUM_CONFIG_DIR>\secrets\<name>.dpapi, encrypted with DPAPI for
// the account Sonorium runs as: the signed-in user per user, NT SERVICE\Sonorium as a service.
// Only that account (or an administrator acting as it) can decrypt it; a copy of the file on
// another PC or account is useless. Values never go to stdout except for "get", nor to logs.

const int Ok = 0, NotFound = 1, Usage = 2, Failed = 3;

if (args.Length != 2 || !Regex.IsMatch(args[1], "^[a-z0-9_]{1,64}$"))
    return Fail(Usage, "usage: SonoriumSecrets store|get|erase <name>");
var name = args[1];
var configDir = Environment.GetEnvironmentVariable("SONORIUM_CONFIG_DIR");
if (string.IsNullOrEmpty(configDir)) return Fail(Usage, "SONORIUM_CONFIG_DIR is not set");
var folder = Path.Combine(configDir, "secrets");
var file = Path.Combine(folder, name + ".dpapi");
var entropy = Encoding.UTF8.GetBytes("Sonorium secret: " + name);

try
{
    switch (args[0])
    {
        case "store":
            using (var stdin = Console.OpenStandardInput())
            using (var buffer = new MemoryStream())
            {
                stdin.CopyTo(buffer);
                var blob = ProtectedData.Protect(buffer.ToArray(), entropy, DataProtectionScope.CurrentUser);
                Directory.CreateDirectory(folder);
                var tmp = file + ".tmp";
                File.WriteAllBytes(tmp, blob);
                File.Move(tmp, file, overwrite: true);
            }
            return Ok;
        case "get":
            if (!File.Exists(file)) return NotFound;
            var value = ProtectedData.Unprotect(File.ReadAllBytes(file), entropy, DataProtectionScope.CurrentUser);
            using (var stdout = Console.OpenStandardOutput())
                stdout.Write(value);
            return Ok;
        case "erase":
            File.Delete(file);
            return Ok;
        default:
            return Fail(Usage, "usage: SonoriumSecrets store|get|erase <name>");
    }
}
catch (CryptographicException)
{
    // Made by another account or on another PC
    return Fail(Failed, $"{name} can't be decrypted by this account; enter it again");
}
catch (Exception e) when (e is IOException or UnauthorizedAccessException)
{
    return Fail(Failed, $"{name}: {e.Message}");
}

static int Fail(int code, string message)
{
    Console.Error.WriteLine(message);
    return code;
}
