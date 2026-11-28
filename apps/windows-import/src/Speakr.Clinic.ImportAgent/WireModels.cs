using System.Text.Json.Serialization;

namespace Speakr.Clinic.ImportAgent;

public static class ImportIntentKind
{
    public const string ImportSnapshot = "import_snapshot";
}

public sealed class ImportIntent
{
    [JsonPropertyName("intent_id")]
    public string IntentId { get; set; } = "";

    [JsonPropertyName("kind")]
    public string Kind { get; set; } = "";

    [JsonPropertyName("source_id")]
    public string SourceId { get; set; } = "";

    [JsonPropertyName("snapshot_version")]
    public string SnapshotVersion { get; set; } = "";

    [JsonPropertyName("post_step")]
    public string PostStep { get; set; } = "none";

    // UTC, whole seconds, no fraction: 2026-01-02T03:04:05Z. Part of the v2 signature.
    [JsonPropertyName("expires_at")]
    public string ExpiresAt { get; set; } = "";

    [JsonPropertyName("agent_id")]
    public string AgentId { get; set; } = "";

    [JsonPropertyName("signature")]
    public string Signature { get; set; } = "";
}

public sealed class IntentList
{
    [JsonPropertyName("intents")]
    public List<ImportIntent> Intents { get; set; } = [];
}

public sealed class InventoryItemWire
{
    [JsonPropertyName("source_id")]
    public string SourceId { get; set; } = "";

    [JsonPropertyName("root_id")]
    public string RootId { get; set; } = "";

    [JsonPropertyName("relative_path")]
    public string RelativePath { get; set; } = "";

    [JsonPropertyName("size")]
    public long Size { get; set; }

    [JsonPropertyName("mtime_utc")]
    public DateTimeOffset MtimeUtc { get; set; }

    [JsonPropertyName("sha256")]
    public string Sha256 { get; set; } = "";

    [JsonPropertyName("snapshot_version")]
    public string SnapshotVersion { get; set; } = "";

    [JsonPropertyName("claimed_author_code")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? ClaimedAuthorCode { get; set; }

    [JsonPropertyName("completion_source")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? CompletionSource { get; set; }

    [JsonPropertyName("observed_completion")]
    public string ObservedCompletion { get; set; } = "unknown";

    [JsonPropertyName("evidence_level")]
    public string EvidenceLevel { get; set; } = "unverified";
}

public sealed class InventoryReport
{
    // Deprecated by the contract. Omitted when null: identity is the transport credential.
    [JsonPropertyName("agent_id")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? AgentId { get; set; }

    [JsonPropertyName("items")]
    public List<InventoryItemWire> Items { get; set; } = [];
}

public sealed class WireBeginIngest
{
    [JsonPropertyName("intent_id")]
    public string IntentId { get; set; } = "";

    [JsonPropertyName("declared_size")]
    public long DeclaredSize { get; set; }

    [JsonPropertyName("declared_sha256")]
    public string DeclaredSha256 { get; set; } = "";

    // Not serialized. The in-process simulator has no inventory lookup of its own.
    [JsonIgnore]
    public string SourceId { get; set; } = "";

    [JsonIgnore]
    public string SnapshotVersion { get; set; } = "";

    [JsonIgnore]
    public string AgentId { get; set; } = "";

    [JsonIgnore]
    public string PostStep { get; set; } = "none";
}

public sealed class ChunkDigest
{
    [JsonPropertyName("sequence")]
    public int Sequence { get; set; }

    [JsonPropertyName("bytes")]
    public int Bytes { get; set; }

    [JsonPropertyName("sha256")]
    public string Sha256 { get; set; } = "";
}

public sealed class WireFinalizeIngest
{
    [JsonPropertyName("chunks")]
    public List<ChunkDigest> Chunks { get; set; } = [];

    [JsonPropertyName("total_bytes")]
    public long TotalBytes { get; set; }

    [JsonPropertyName("sha256")]
    public string Sha256 { get; set; } = "";

    // Reading the snapshot finished. This is not a clinical end-of-letter mark.
    [JsonPropertyName("source_finished_reading")]
    public bool SourceFinishedReading { get; set; }

    // The authenticated agent verified its local backup of this exact snapshot.
    [JsonPropertyName("backup_verified")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingDefault)]
    public bool BackupVerified { get; set; }
}

public sealed class IngestReceipt
{
    [JsonPropertyName("ingest_id")]
    public string IngestId { get; set; } = "";

    [JsonPropertyName("state")]
    public string State { get; set; } = "";

    [JsonPropertyName("received_chunks")]
    public int[] ReceivedChunks { get; set; } = [];

    [JsonPropertyName("received_bytes")]
    public long ReceivedBytes { get; set; }

    [JsonPropertyName("sha256")]
    public string? Sha256 { get; set; }

    [JsonPropertyName("dictation_id")]
    public string? DictationId { get; set; }

    [JsonPropertyName("durability")]
    public string Durability { get; set; } = "";

    [JsonPropertyName("decode_state")]
    public string DecodeState { get; set; } = "";

    [JsonPropertyName("completion")]
    public string Completion { get; set; } = "";

    [JsonPropertyName("author_state")]
    public string AuthorState { get; set; } = "";

    [JsonPropertyName("correlation_id")]
    public string CorrelationId { get; set; } = "";

    [JsonPropertyName("agent_id")]
    public string AgentId { get; set; } = "";

    [JsonPropertyName("intent_id")]
    public string IntentId { get; set; } = "";

    [JsonPropertyName("source_id")]
    public string SourceId { get; set; } = "";

    [JsonPropertyName("snapshot_version")]
    public string SnapshotVersion { get; set; } = "";

    [JsonPropertyName("size")]
    public long Size { get; set; }

    [JsonPropertyName("original_id")]
    public string? OriginalId { get; set; }

    [JsonPropertyName("permitted_post_step")]
    public string PermittedPostStep { get; set; } = "";

    [JsonPropertyName("deletion_permitted")]
    public bool DeletionPermitted { get; set; }
}

public sealed class AgentOutcome
{
    [JsonPropertyName("intent_id")]
    public string IntentId { get; set; } = "";

    [JsonPropertyName("ingest_id")]
    public string? IngestId { get; set; }

    [JsonPropertyName("outcome")]
    public string Outcome { get; set; } = "";

    [JsonPropertyName("detail")]
    public string? Detail { get; set; }
}
