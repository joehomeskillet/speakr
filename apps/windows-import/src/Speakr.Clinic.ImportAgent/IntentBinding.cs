using System.Globalization;
using System.Security.Cryptography;
using System.Text;

namespace Speakr.Clinic.ImportAgent;

public enum IntentBindingState
{
    // No agent_id/signature supplied, or no key configured: nothing is verifiable.
    Unbound,
    Valid,
    // Supplied but foreign agent or wrong signature.
    Invalid
}

public static class IntentBinding
{
    // Eight lines, LF, no trailing LF. expires_at is the wire string, not a reformatted clock.
    public static string Sign(ImportIntent intent, string agentId, byte[] key)
    {
        var text = string.Join(
            '\n',
            "speakr-import-intent-v2",
            intent.IntentId,
            intent.Kind,
            agentId,
            intent.SourceId,
            intent.SnapshotVersion,
            intent.PostStep,
            intent.ExpiresAt);
        return Convert.ToHexString(HMACSHA256.HashData(key, Encoding.UTF8.GetBytes(text))).ToLowerInvariant();
    }

    public static IntentBindingState Verify(ImportIntent intent, string ownAgentId, byte[]? key)
    {
        var supplied = intent.AgentId.Length > 0 || intent.Signature.Length > 0;
        if (supplied && !string.Equals(intent.AgentId, ownAgentId, StringComparison.Ordinal))
            return IntentBindingState.Invalid;
        if (!supplied || key is null || key.Length == 0)
            return supplied ? IntentBindingState.Invalid : IntentBindingState.Unbound;
        var expected = Encoding.ASCII.GetBytes(Sign(intent, ownAgentId, key));
        var actual = Encoding.ASCII.GetBytes(intent.Signature.ToLowerInvariant());
        if (expected.Length != actual.Length || !CryptographicOperations.FixedTimeEquals(expected, actual))
            return IntentBindingState.Invalid;
        return ExpiresInFuture(intent.ExpiresAt) ? IntentBindingState.Valid : IntentBindingState.Invalid;
    }

    public static bool ExpiresInFuture(string expiresAt)
    {
        if (!DateTimeOffset.TryParseExact(
                expiresAt,
                "yyyy-MM-dd'T'HH:mm:ss'Z'",
                CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
                out var expires))
            return false;
        return expires > DateTimeOffset.UtcNow;
    }
}

public static class LogText
{
    // Server-controlled strings must not forge log lines.
    public static string Clean(string? value, int max = 128)
    {
        if (string.IsNullOrEmpty(value))
            return "";
        var chars = value.Select(c => char.IsControl(c) ? '?' : c).ToArray();
        var text = new string(chars);
        return text.Length > max ? text[..max] : text;
    }
}
