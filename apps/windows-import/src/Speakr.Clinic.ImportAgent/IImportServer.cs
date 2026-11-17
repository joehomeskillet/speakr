namespace Speakr.Clinic.ImportAgent;

public interface IImportServer
{
    Task ReportInventoryAsync(InventoryReport report, CancellationToken cancellationToken);

    Task<IReadOnlyList<ImportIntent>> PollIntentsAsync(string agentId, CancellationToken cancellationToken);

    Task<IngestReceipt> BeginAsync(WireBeginIngest body, string idempotencyKey, CancellationToken cancellationToken);

    Task<IngestReceipt> PutChunkAsync(
        string ingestId,
        int sequence,
        ReadOnlyMemory<byte> body,
        string sha256,
        CancellationToken cancellationToken);

    Task<IngestReceipt> FinalizeAsync(
        string ingestId,
        WireFinalizeIngest body,
        string idempotencyKey,
        CancellationToken cancellationToken);

    Task<IngestReceipt> GetAsync(string ingestId, CancellationToken cancellationToken);
}
