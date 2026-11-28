namespace Speakr.Clinic.ImportAgent;

public sealed class ResumableUploader
{
    private readonly IImportServer _server;
    private readonly EncryptedSpool _spool;
    private readonly AgentOptions _options;

    public ResumableUploader(IImportServer server, EncryptedSpool spool, AgentOptions options)
    {
        _server = server;
        _spool = spool;
        _options = options;
    }

    public static WireBeginIngest BuildBegin(InventoryItem item, ImportIntent intent)
    {
        return new WireBeginIngest
        {
            IntentId = intent.IntentId,
            DeclaredSize = item.Size,
            DeclaredSha256 = item.ContentSha256,
            SourceId = item.SourceId,
            SnapshotVersion = item.SnapshotVersion,
            AgentId = intent.AgentId,
            PostStep = intent.PostStep
        };
    }

    // Deterministic per item size so a resumed upload always uses the same chunk map. The finalize
    // contract allows at most 10000 chunks; the chunk size grows for large files instead of failing.
    public int EffectiveChunkSize(long size)
    {
        var needed = (size + AgentOptions.MaxChunks - 1) / AgentOptions.MaxChunks;
        return (int)Math.Min(AgentOptions.MaxChunkBytes, Math.Max(_options.ChunkSizeBytes, needed));
    }

    public Task<BoundReceipt> UploadAsync(InventoryItem item, ImportIntent intent, CancellationToken cancellationToken) =>
        UploadAsync(item, intent, cancellationToken, backupVerified: false);

    internal async Task<BoundReceipt> UploadAsync(InventoryItem item, ImportIntent intent, CancellationToken cancellationToken,
        bool backupVerified)
    {
        var bytes = _spool.Read(item.SnapshotVersion);
        if (bytes.LongLength != item.Size || !string.Equals(Hashing.Sha256Hex(bytes), item.ContentSha256, StringComparison.Ordinal))
            throw new InvalidDataException("Spool bytes do not match the snapshot.");
        var chunkSize = EffectiveChunkSize(bytes.LongLength);
        var chunkCount = (int)((bytes.LongLength + chunkSize - 1) / chunkSize);
        var beginKey = item.IdempotencyKey("begin");
        var begin = BuildBegin(item, intent);
        var receipt = await _server.BeginAsync(begin, beginKey, cancellationToken);
        var ingestId = receipt.IngestId;
        RequireProgressable(receipt);
        if (string.Equals(receipt.State, "STORED", StringComparison.Ordinal))
            return ReceiptBinder.Bind(item, intent, ingestId, receipt);
        // The server may only report state that fits the local snapshot.
        if (receipt.ReceivedBytes > item.Size || receipt.ReceivedChunks.Any(s => s < 0 || s >= chunkCount))
            throw new ImportConflictException("Server reports upload state beyond the local snapshot.");
        var received = new HashSet<int>(receipt.ReceivedChunks);
        var digests = new List<ChunkDigest>();
        var sequence = 0;
        for (var offset = 0; offset < bytes.Length; offset += chunkSize)
        {
            var length = Math.Min(chunkSize, bytes.Length - offset);
            var chunk = bytes.AsMemory(offset, length);
            var chunkHash = Hashing.Sha256Hex(chunk.Span);
            digests.Add(new ChunkDigest { Sequence = sequence, Bytes = length, Sha256 = chunkHash });
            if (!received.Contains(sequence))
            {
                receipt = await _server.PutChunkAsync(ingestId, sequence, offset, chunk, chunkHash, cancellationToken);
                ReceiptBinder.RequireSession(ingestId, receipt);
                received.Add(sequence);
            }
            sequence++;
        }
        var finalize = new WireFinalizeIngest
        {
            Chunks = digests,
            TotalBytes = bytes.LongLength,
            Sha256 = item.ContentSha256,
            SourceFinishedReading = true,
            BackupVerified = backupVerified
        };
        receipt = await _server.FinalizeAsync(ingestId, finalize, item.IdempotencyKey("finalize"), cancellationToken);
        ReceiptBinder.RequireSession(ingestId, receipt);
        // A non-terminal state is re-read a bounded number of times. UNKNOWN is terminal and is never STORED.
        for (var attempt = 0; attempt < _options.FinalizePollAttempts && !ReceiptBinder.IsFinal(receipt); attempt++)
        {
            if (_options.FinalizePollDelayMs > 0)
                await Task.Delay(_options.FinalizePollDelayMs, cancellationToken);
            receipt = await _server.GetAsync(ingestId, cancellationToken);
            ReceiptBinder.RequireSession(ingestId, receipt);
        }
        return ReceiptBinder.Bind(item, intent, ingestId, receipt);
    }

    private static void RequireProgressable(IngestReceipt receipt)
    {
        if (receipt.State is "RECEIVING" or "STORED")
            return;
        throw new ReceiptRejectedException("Server marked the ingest " + receipt.State + ".");
    }
}

public static class ReceiptBinder
{
    public static void RequireSession(string expectedIngestId, IngestReceipt receipt)
    {
        if (!string.Equals(receipt.IngestId, expectedIngestId, StringComparison.Ordinal))
            throw new ReceiptRejectedException("Receipt ingest does not match the upload session.");
    }

    public static bool IsFinal(IngestReceipt receipt) =>
        receipt.State is "STORED" or "INVALID" or "UNKNOWN" or "ABORTED";

    // expectedIngestId must come from the session (begin response), never from the receipt under test.
    public static BoundReceipt Bind(InventoryItem item, ImportIntent intent, string expectedIngestId, IngestReceipt receipt)
    {
        RequireSession(expectedIngestId, receipt);
        if (!string.Equals(receipt.State, "STORED", StringComparison.Ordinal)
            || !string.Equals(receipt.Durability, "STORED", StringComparison.Ordinal))
            throw new ReceiptRejectedException("Receipt is not a durable STORED commit.");
        if (!string.Equals(receipt.Sha256, item.ContentSha256, StringComparison.Ordinal)
            || receipt.ReceivedBytes != item.Size
            || receipt.Size != item.Size)
            throw new ReceiptRejectedException("Receipt is not bound to this snapshot hash and size.");
        if (!string.Equals(receipt.SourceId, item.SourceId, StringComparison.Ordinal)
            || !string.Equals(receipt.SnapshotVersion, item.SnapshotVersion, StringComparison.Ordinal))
            throw new ReceiptRejectedException("Receipt is not bound to this source snapshot.");
        if (!string.Equals(receipt.IntentId, intent.IntentId, StringComparison.Ordinal))
            throw new ReceiptRejectedException("Receipt intent does not match the upload.");
        if (receipt.CorrelationId.Length == 0)
            throw new ReceiptRejectedException("Receipt has no correlation id.");
        if (string.Equals(receipt.DecodeState, "INVALID", StringComparison.Ordinal))
            throw new ReceiptRejectedException("Receipt decode state is INVALID.");
        if (receipt.AuthorState is not ("UNRESOLVED" or "RESOLVED"))
            throw new ReceiptRejectedException("Receipt author state is not a known value.");
        if (receipt.PermittedPostStep is not ("none" or "move_backup" or "delete"))
            throw new ReceiptRejectedException("Receipt post step is not a known value.");
        if (receipt.DeletionPermitted && receipt.PermittedPostStep != "delete")
            throw new ReceiptRejectedException("Receipt permits deletion without a delete step.");
        return new BoundReceipt(
            item.SourceId,
            item.SnapshotVersion,
            receipt.Sha256 ?? "",
            receipt.ReceivedBytes,
            receipt.IngestId,
            receipt.CorrelationId,
            receipt.State,
            receipt.Durability,
            receipt.DecodeState,
            receipt.PermittedPostStep,
            receipt.DeletionPermitted);
    }
}
