using System.Runtime.Versioning;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.Json;
using Speakr.Clinic.ImportAgent;

namespace Speakr.Clinic.ImportAgent.Worker;

public sealed class DpapiFileSecretStore : ISecretStore
{
    // Application-specific entropy: another DPAPI blob of the same user is not accepted here.
    private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("Speakr.Clinic.ImportAgent.secret.v1");

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

    public byte[]? GetIntentKey()
    {
        var encoded = Read().IntentKeyBase64;
        return string.IsNullOrEmpty(encoded) ? null : Convert.FromBase64String(encoded);
    }

    private SecretFile Read()
    {
        if (!OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException("DPAPI secret store requires Windows.");

        RequireRestrictedAcl(_path);
        var raw = ProtectedData.Unprotect(File.ReadAllBytes(_path), Entropy, DataProtectionScope.CurrentUser);
        try
        {
            var parsed = JsonSerializer.Deserialize<SecretFile>(raw);
            if (parsed is null || parsed.AgentId.Length == 0 || parsed.SpoolKey.Length != 32)
                throw new InvalidDataException("Secret file is incomplete.");
            return parsed;
        }
        finally
        {
            CryptographicOperations.ZeroMemory(raw);
        }
    }

    // The blob is only as private as its file: refuse a secret file readable by broad groups.
    [SupportedOSPlatform("windows")]
    private static void RequireRestrictedAcl(string path)
    {
        var broad = new[]
        {
            new SecurityIdentifier(WellKnownSidType.WorldSid, null),
            new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null),
            new SecurityIdentifier(WellKnownSidType.AuthenticatedUserSid, null)
        };
        var rules = new FileInfo(path).GetAccessControl().GetAccessRules(true, true, typeof(SecurityIdentifier));
        foreach (FileSystemAccessRule rule in rules)
        {
            if (rule.AccessControlType == AccessControlType.Allow
                && broad.Contains(rule.IdentityReference)
                && (rule.FileSystemRights & (FileSystemRights.ReadData | FileSystemRights.FullControl)) != 0)
                throw new InvalidOperationException("Secret file is readable by a broad group.");
        }
    }

    private sealed class SecretFile
    {
        public string AgentId { get; set; } = "";
        public string? PfxBase64 { get; set; }
        public string? PfxPassword { get; set; }
        public byte[] SpoolKey { get; set; } = [];
        public string? IntentKeyBase64 { get; set; }

        public MachineCredential Credential => new()
        {
            AgentId = AgentId,
            ClientCertificatePfx = string.IsNullOrEmpty(PfxBase64) ? null : Convert.FromBase64String(PfxBase64),
            PfxPassword = PfxPassword
        };
    }
}
