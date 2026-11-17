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

    // Not defined in clinic-api.openapi.json (the agent routes are not there at all). Client-side
    // binding assumption, see README "Open contract gaps".
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

    [JsonPropertyName("observed_completion")]
    public string ObservedCompletion { get; set; } = "unknown";

    [JsonPropertyName("evidence_level")]
    public string EvidenceLevel { get; set; } = "unverified";
}

public sealed class InventoryReport
{
    [JsonPropertyName("agent_id")]
    public string AgentId { get; set; } = "";

    [JsonPropertyName("items")]
    public List<InventoryItemWire> Items { get; set; } = [];
}

public sealed class SourceObservation
{
    [JsonPropertyName("device_registry_id")]
    public string? DeviceRegistryId { get; set; }

    [JsonPropertyName("source_namespace")]
    public string SourceNamespace { get; set; } = "";

    [JsonPropertyName("source_recording_id")]
    public string? SourceRecordingId { get; set; }

    [JsonPropertyName("claimed_author_code")]
    public string? ClaimedAuthorCode { get; set; }

    [JsonPropertyName("observed_completion")]
    public string ObservedCompletion { get; set; } = "unknown";

    [JsonPropertyName("parser_version")]
    public string? ParserVersion { get; set; }

    [JsonPropertyName("evidence_level")]
    public string EvidenceLevel { get; set; } = "unverified";
}

public sealed class WireBeginIngest
{
    [JsonPropertyName("request_id")]
    public string RequestId { get; set; } = "";

    [JsonPropertyName("source_kind")]
    public string SourceKind { get; set; } = "";

    [JsonPropertyName("source")]
    public SourceObservation Source { get; set; } = new();

    [JsonPropertyName("mime_type")]
    public string MimeType { get; set; } = "application/octet-stream";

    [JsonPropertyName("expected_bytes")]
    public long? ExpectedBytes { get; set; }

    [JsonPropertyName("expected_sha256")]
    public string? ExpectedSha256 { get; set; }
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
}
