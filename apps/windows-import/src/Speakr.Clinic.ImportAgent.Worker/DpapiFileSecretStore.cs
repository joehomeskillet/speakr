using System.Security.Cryptography;
using System.Text.Json;
using Speakr.Clinic.ImportAgent;

namespace Speakr.Clinic.ImportAgent.Worker;

public sealed class DpapiFileSecretStore : ISecretStore
{
    private readonly string _path;

    public DpapiFileSecretStore(string path)
    {
        if (!OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException("DPAPI secret store requires Windows.");
        if (string.IsNullOrWhiteSpace(path))
            throw new InvalidOperationException("SecretFilePath is required.");
        _path = path;
    }

    public MachineCredential GetMachineCredential() => Read().Credential;

    public byte[] GetSpoolKey() => Read().SpoolKey.ToArray();

    private SecretFile Read()
    {
        if (!OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException("DPAPI secret store requires Windows.");

        var raw = ProtectedData.Unprotect(File.ReadAllBytes(_path), null, DataProtectionScope.CurrentUser);
        var parsed = JsonSerializer.Deserialize<SecretFile>(raw);
        if (parsed is null || parsed.AgentId.Length == 0 || parsed.SpoolKey.Length != 32)
            throw new InvalidDataException("Secret file is incomplete.");
        return parsed;
    }

    private sealed class SecretFile
    {
        public string AgentId { get; set; } = "";
        public string? PfxBase64 { get; set; }
        public string? PfxPassword { get; set; }
        public byte[] SpoolKey { get; set; } = [];

        public MachineCredential Credential => new()
        {
            AgentId = AgentId,
            ClientCertificatePfx = string.IsNullOrEmpty(PfxBase64) ? null : Convert.FromBase64String(PfxBase64),
            PfxPassword = PfxPassword
        };
    }
}
