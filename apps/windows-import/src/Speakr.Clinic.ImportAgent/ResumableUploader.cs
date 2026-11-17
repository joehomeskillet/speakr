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

    public static WireBeginIngest BuildBegin(AgentOptions options, InventoryItem item)
    {
        var key = item.IdempotencyKey("begin");
        return new WireBeginIngest
        {
            RequestId = key,
            SourceKind = options.SimulatorMode ? "simulator" : "device",
            MimeType = "application/octet-stream",
            ExpectedBytes = item.Size,
            ExpectedSha256 = item.ContentSha256,
            Source = new SourceObservation
            {
                DeviceRegistryId = null,
                SourceNamespace = item.SourceNamespace,
                SourceRecordingId = item.SourceRecordingId,
                ClaimedAuthorCode = item.Completion.ClaimedAuthorCode,
                ObservedCompletion = item.Completion.ObservedCompletion,
                ParserVersion = item.Completion.ParserVersion,
                EvidenceLevel = item.Completion.EvidenceLevel
            }
        };
    }

    // Deterministic per item size so a resumed upload always uses the same chunk map. The finalize
    // contract allows at most 10000 chunks; the chunk size grows for large files instead of failing.
    public int EffectiveChunkSize(long size)
    {
        var needed = (size + AgentOptions.MaxChunks - 1) / AgentOptions.MaxChunks;
        return (int)Math.Min(AgentOptions.MaxChunkBytes, Math.Max(_options.ChunkSizeBytes, needed));
    }

    public async Task<BoundReceipt> UploadAsync(InventoryItem item, CancellationToken cancellationToken)
    {
        var bytes = _spool.Read(item.SnapshotVersion);
        if (bytes.LongLength != item.Size || !string.Equals(Hashing.Sha256Hex(bytes), item.ContentSha256, StringComparison.Ordinal))
            throw new InvalidDataException("Spool bytes do not match the snapshot.");
        var chunkSize = EffectiveChunkSize(bytes.LongLength);
        var chunkCount = (int)((bytes.LongLength + chunkSize - 1) / chunkSize);
        var beginKey = item.IdempotencyKey("begin");
        var begin = BuildBegin(_options, item);
        var receipt = await _server.BeginAsync(begin, beginKey, cancellationToken);
        var ingestId = receipt.IngestId;
        RequireProgressable(receipt);
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
                receipt = await _server.PutChunkAsync(ingestId, sequence, chunk, chunkHash, cancellationToken);
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
            SourceFinishedReading = true
        };
        receipt = await _server.FinalizeAsync(ingestId, finalize, item.IdempotencyKey("finalize"), cancellationToken);
        ReceiptBinder.RequireSession(ingestId, receipt);
        // Finalize is accepted asynchronously (202). Anything but a durable STORED stays unknown here;
        // it is re-read a bounded number of times and is never treated as stored.
        for (var attempt = 0; attempt < _options.FinalizePollAttempts && !ReceiptBinder.IsFinal(receipt); attempt++)
        {
            if (_options.FinalizePollDelayMs > 0)
                await Task.Delay(_options.FinalizePollDelayMs, cancellationToken);
            receipt = await _server.GetAsync(ingestId, cancellationToken);
            ReceiptBinder.RequireSession(ingestId, receipt);
        }
        return ReceiptBinder.Bind(item, ingestId, receipt);
    }

    private static void RequireProgressable(IngestReceipt receipt)
    {
        if (string.Equals(receipt.State, "INVALID", StringComparison.Ordinal)
            || string.Equals(receipt.State, "QUARANTINED", StringComparison.Ordinal))
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
        string.Equals(receipt.State, "STORED", StringComparison.Ordinal)
        || string.Equals(receipt.State, "INVALID", StringComparison.Ordinal)
        || string.Equals(receipt.State, "QUARANTINED", StringComparison.Ordinal);

    // expectedIngestId must come from the session (begin response), never from the receipt under test.
    public static BoundReceipt Bind(InventoryItem item, string expectedIngestId, IngestReceipt receipt)
    {
        RequireSession(expectedIngestId, receipt);
        if (!string.Equals(receipt.State, "STORED", StringComparison.Ordinal)
            || !string.Equals(receipt.Durability, "STORED", StringComparison.Ordinal))
            throw new ReceiptRejectedException("Receipt is not a durable STORED commit.");
        if (!string.Equals(receipt.Sha256, item.ContentSha256, StringComparison.Ordinal) || receipt.ReceivedBytes != item.Size)
            throw new ReceiptRejectedException("Receipt is not bound to this snapshot hash and size.");
        if (receipt.CorrelationId.Length == 0)
            throw new ReceiptRejectedException("Receipt has no correlation id.");
        if (string.Equals(receipt.DecodeState, "INVALID", StringComparison.Ordinal))
            throw new ReceiptRejectedException("Receipt decode state is INVALID.");
        if (!string.Equals(receipt.AuthorState, "UNRESOLVED", StringComparison.Ordinal)
            && !string.Equals(receipt.AuthorState, "RESOLVED", StringComparison.Ordinal))
            throw new ReceiptRejectedException("Receipt author state is not a known value.");
        // Hash and size come from the server receipt; the source id and version are the local binding.
        return new BoundReceipt(
            item.SourceId,
            item.SnapshotVersion,
            receipt.Sha256 ?? "",
            receipt.ReceivedBytes,
            receipt.IngestId,
            receipt.CorrelationId,
            receipt.State,
            receipt.Durability,
            receipt.DecodeState);
    }
}
