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
    public static string Sign(ImportIntent intent, string agentId, byte[] key)
    {
        var text = string.Join(
            '\n',
            "speakr-import-intent-v1",
            intent.IntentId,
            intent.Kind,
            agentId,
            intent.SourceId,
            intent.SnapshotVersion,
            intent.PostStep);
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
        return CryptographicOperations.FixedTimeEquals(expected, actual)
            ? IntentBindingState.Valid
            : IntentBindingState.Invalid;
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
