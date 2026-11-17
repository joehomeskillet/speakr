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
    string Durability)
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
        && CorrelationId.Length > 0;
}

public enum PostImportOutcome
{
    SkippedPolicyOff,
    SkippedNoStep,
    Moved,
    Deleted,
    Conflict,
    RejectedStep
}

public sealed class ImportConflictException : Exception
{
    public ImportConflictException(string message) : base(message) { }
}

public sealed class ReceiptRejectedException : Exception
{
    public ReceiptRejectedException(string message) : base(message) { }
}

public sealed class ImportServerException : Exception
{
    public ImportServerException(int status, string message) : base(message)
    {
        Status = status;
    }

    public int Status { get; }
}

public sealed class SpoolLimitException : Exception
{
    public SpoolLimitException() : base("Encrypted spool limit reached.") { }
}

public interface IImportLog
{
    void Info(string message);
}

public sealed class NullImportLog : IImportLog
{
    public static readonly NullImportLog Instance = new();

    public void Info(string message)
    {
    }
}
