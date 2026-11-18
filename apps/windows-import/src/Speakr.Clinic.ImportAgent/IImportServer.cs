namespace Speakr.Clinic.ImportAgent;

public interface IImportServer
{
    Task ReportInventoryAsync(InventoryReport report, CancellationToken cancellationToken);

    Task<IReadOnlyList<ImportIntent>> PollIntentsAsync(string agentId, CancellationToken cancellationToken);

    Task<IngestReceipt> BeginAsync(WireBeginIngest body, string idempotencyKey, CancellationToken cancellationToken);

    Task<IngestReceipt> PutChunkAsync(
        string ingestId,
        int sequence,
        long offset,
        ReadOnlyMemory<byte> body,
        string sha256,
        CancellationToken cancellationToken);

    Task<IngestReceipt> FinalizeAsync(
        string ingestId,
        WireFinalizeIngest body,
        string idempotencyKey,
        CancellationToken cancellationToken);

    Task<IngestReceipt> GetAsync(string ingestId, CancellationToken cancellationToken);

    // RECEIVING sessions only. Not used on the retry path: the same Idempotency-Key replays the aborted session.
    Task<IngestReceipt> AbortAsync(string ingestId, CancellationToken cancellationToken);

    Task ReportOutcomeAsync(AgentOutcome outcome, CancellationToken cancellationToken);
}
