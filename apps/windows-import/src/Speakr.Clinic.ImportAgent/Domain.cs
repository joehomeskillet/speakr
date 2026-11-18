using System.Text;

namespace Speakr.Clinic.ImportAgent;

public sealed record CompletionObservation(
    string ObservedCompletion,
    string EvidenceLevel,
    string? ClaimedAuthorCode,
    string? ParserVersion)
{
    public static CompletionObservation Unknown { get; } = new("unknown", "unverified", null, null);
}

public sealed record InventoryItem(
    string SourceId,
    string RootId,
    string RootPath,
    string RelativePath,
    string FullPath,
    long Size,
    DateTimeOffset MtimeUtc,
    string ContentSha256,
    string SnapshotVersion,
    CompletionObservation Completion)
{
    public string SourceNamespace => RootId;
    public string SourceRecordingId => RelativePath;

    public string IdempotencyKey(string operation) =>
        Hashing.DeterministicGuid($"{SourceNamespace}\n{SourceRecordingId}\n{SnapshotVersion}\n{operation}")
            .ToString();
}

public sealed record BoundReceipt(
    string SourceId,
    string SnapshotVersion,
    string ContentSha256,
    long Size,
    string IngestId,
    string CorrelationId,
    string State,
    string Durability,
    string DecodeState = "",
    string PermittedPostStep = "none",
    bool DeletionPermitted = false)
{
    public bool IsDurable =>
        string.Equals(State, "STORED", StringComparison.Ordinal)
        && string.Equals(Durability, "STORED", StringComparison.Ordinal);

    public bool Binds(InventoryItem item, string ingestId) =>
        IsDurable
        && string.Equals(SourceId, item.SourceId, StringComparison.Ordinal)
        && string.Equals(SnapshotVersion, item.SnapshotVersion, StringComparison.Ordinal)
        && string.Equals(ContentSha256, item.ContentSha256, StringComparison.Ordinal)
        && Size == item.Size
        && string.Equals(IngestId, ingestId, StringComparison.Ordinal)
        && !string.Equals(DecodeState, "INVALID", StringComparison.Ordinal)
        && CorrelationId.Length > 0;
}

public enum PostImportOutcome
{
    SkippedPolicyOff,
    SkippedNoStep,
    Moved,
    Deleted,
    Conflict,
    RejectedStep,
    RefusedUnbound
}

public sealed class ImportConflictException : Exception
{
    public ImportConflictException(string message) : base(message) { }
}

public sealed class ReceiptRejectedException : Exception
{
    public ReceiptRejectedException(string message) : base(message) { }
}

public class ImportServerException : Exception
{
    public ImportServerException(int status, string message) : base(message)
    {
        Status = status;
    }

    public int Status { get; }
}

public sealed class ImportExpiredException : ImportServerException
{
    public ImportExpiredException(string message) : base(410, message) { }
}

public sealed class SpoolLimitException : Exception
{
    public SpoolLimitException() : base("Encrypted spool limit reached.") { }
}

public interface IImportLog
{
    void Info(string message);

    // Cycle and secret failures. The Windows host writes these to the Application event log.
    void Error(string message);
}

public sealed class NullImportLog : IImportLog
{
    public static readonly NullImportLog Instance = new();

    public void Info(string message)
    {
    }

    public void Error(string message)
    {
    }
}

public static class ContractIds
{
    public const int MaxRelativePathChars = 1024;
    public const int MaxIdChars = 128;

    public static bool IsId(string value)
    {
        if (value.Length is < 1 or > MaxIdChars)
            return false;
        foreach (var ch in value)
        {
            if (ch is not ((>= 'A' and <= 'Z') or (>= 'a' and <= 'z') or (>= '0' and <= '9') or '.' or '_' or ':' or '@' or '-'))
                return false;
        }
        return true;
    }

    // Server relative_path: 1..1024, no leading slash, no drive prefix, no empty / "." / ".." segment.
    public static bool IsRelativePath(string relative)
    {
        if (relative.Length is < 1 or > MaxRelativePathChars)
            return false;
        if (relative[0] is '/' or '\\')
            return false;
        if (relative.Length >= 2 && char.IsAsciiLetter(relative[0]) && relative[1] == ':')
            return false;
        foreach (var ch in relative)
        {
            if (char.IsControl(ch))
                return false;
        }
        foreach (var segment in relative.Split('/', '\\'))
        {
            if (segment is "" or "." or "..")
                return false;
        }
        return true;
    }

    // source_id has no slash. A readable root:path is used when it fits; otherwise a stable hash.
    public static string SourceId(string rootId, string relativePath)
    {
        var readable = rootId + ":" + relativePath.Replace('\\', '/').Replace('/', ':');
        if (IsId(readable))
            return readable;
        return Hashing.Sha256Hex(Encoding.UTF8.GetBytes(rootId + "\n" + relativePath));
    }
}
