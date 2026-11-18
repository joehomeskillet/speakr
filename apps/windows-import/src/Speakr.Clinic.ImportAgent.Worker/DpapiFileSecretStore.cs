using System.Runtime.Versioning;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.Json;
using Speakr.Clinic.ImportAgent;

namespace Speakr.Clinic.ImportAgent.Worker;

public sealed class SecretStoreException : Exception
{
    public SecretStoreException(string message) : base(message) { }

    public SecretStoreException(string message, Exception inner) : base(message, inner) { }
}

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
        if (string.IsNullOrEmpty(encoded))
            return null;
        try
        {
            return Convert.FromBase64String(encoded);
        }
        catch (FormatException ex)
        {
            throw new SecretStoreException("Secret file could not be read.", ex);
        }
    }

    private SecretFile Read()
    {
        if (!OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException("DPAPI secret store requires Windows.");
        if (!File.Exists(_path))
            throw new SecretStoreException("Secret file was not found.");

        RequireRestrictedAcl(_path);
        byte[]? raw = null;
        try
        {
            try
            {
                raw = ProtectedData.Unprotect(File.ReadAllBytes(_path), Entropy, DataProtectionScope.CurrentUser);
            }
            catch (Exception ex) when (ex is CryptographicException or IOException or UnauthorizedAccessException)
            {
                throw new SecretStoreException("Secret file could not be unprotected.", ex);
            }
            try
            {
                var parsed = JsonSerializer.Deserialize<SecretFile>(raw);
                if (parsed is null || parsed.AgentId.Length == 0 || parsed.SpoolKey.Length != 32)
                    throw new SecretStoreException("Secret file is incomplete.");
                return parsed;
            }
            catch (SecretStoreException)
            {
                throw;
            }
            catch (Exception ex) when (ex is JsonException)
            {
                throw new SecretStoreException("Secret file could not be read.", ex);
            }
        }
        finally
        {
            if (raw is not null)
                CryptographicOperations.ZeroMemory(raw);
        }
    }

    // Test seam: a real Everyone read ACE, so the refusal is observable without a valid DPAPI blob.
    [SupportedOSPlatform("windows")]
    internal static void GrantEveryoneRead(string path)
    {
        var info = new FileInfo(path);
        var acl = info.GetAccessControl();
        acl.AddAccessRule(new FileSystemAccessRule(
            new SecurityIdentifier(WellKnownSidType.WorldSid, null),
            FileSystemRights.ReadData,
            AccessControlType.Allow));
        info.SetAccessControl(acl);
    }

    // The blob is only as private as its file. A failure to read the ACL is a refusal, not a crash.
    [SupportedOSPlatform("windows")]
    private static void RequireRestrictedAcl(string path)
    {
        var broad = new[]
        {
            new SecurityIdentifier(WellKnownSidType.WorldSid, null),
            new SecurityIdentifier(WellKnownSidType.BuiltinUsersSid, null),
            new SecurityIdentifier(WellKnownSidType.AuthenticatedUserSid, null)
        };
        AuthorizationRuleCollection rules;
        try
        {
            rules = new FileInfo(path).GetAccessControl().GetAccessRules(true, true, typeof(SecurityIdentifier));
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or System.Security.SecurityException
            or InvalidOperationException or PlatformNotSupportedException or PrivilegeNotHeldException
            or IdentityNotMappedException)
        {
            throw new SecretStoreException("Secret file ACL could not be verified. Refusing to open it.", ex);
        }
        foreach (FileSystemAccessRule rule in rules)
        {
            if (rule.AccessControlType == AccessControlType.Allow
                && broad.Contains(rule.IdentityReference)
                && (rule.FileSystemRights & (FileSystemRights.ReadData | FileSystemRights.FullControl)) != 0)
                throw new SecretStoreException(
                    "Secret file ACL is too broad. Remove read access for Everyone, Users and Authenticated Users.");
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
