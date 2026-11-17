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

    public async Task<BoundReceipt> UploadAsync(InventoryItem item, CancellationToken cancellationToken)
    {
        var bytes = _spool.Read(item.SnapshotVersion);
        if (bytes.LongLength != item.Size || !string.Equals(Hashing.Sha256Hex(bytes), item.ContentSha256, StringComparison.Ordinal))
            throw new InvalidDataException("Spool bytes do not match the snapshot.");
        var beginKey = item.IdempotencyKey("begin");
        var begin = BuildBegin(_options, item);
        var receipt = await _server.BeginAsync(begin, beginKey, cancellationToken);
        var received = new HashSet<int>(receipt.ReceivedChunks);
        var digests = new List<ChunkDigest>();
        var sequence = 0;
        for (var offset = 0; offset < bytes.Length; offset += _options.ChunkSizeBytes)
        {
            var length = Math.Min(_options.ChunkSizeBytes, bytes.Length - offset);
            var chunk = bytes.AsMemory(offset, length);
            var chunkHash = Hashing.Sha256Hex(chunk.Span);
            digests.Add(new ChunkDigest { Sequence = sequence, Bytes = length, Sha256 = chunkHash });
            if (!received.Contains(sequence))
            {
                receipt = await _server.PutChunkAsync(receipt.IngestId, sequence, chunk, chunkHash, cancellationToken);
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
        receipt = await _server.FinalizeAsync(receipt.IngestId, finalize, item.IdempotencyKey("finalize"), cancellationToken);
        return ReceiptBinder.Bind(item, receipt.IngestId, receipt);
    }
}

public static class ReceiptBinder
{
    public static BoundReceipt Bind(InventoryItem item, string expectedIngestId, IngestReceipt receipt)
    {
        if (!string.Equals(receipt.IngestId, expectedIngestId, StringComparison.Ordinal))
            throw new ReceiptRejectedException("Receipt ingest does not match the upload session.");
        if (!string.Equals(receipt.State, "STORED", StringComparison.Ordinal)
            || !string.Equals(receipt.Durability, "STORED", StringComparison.Ordinal))
            throw new ReceiptRejectedException("Receipt is not a durable STORED commit.");
        if (!string.Equals(receipt.Sha256, item.ContentSha256, StringComparison.Ordinal) || receipt.ReceivedBytes != item.Size)
            throw new ReceiptRejectedException("Receipt is not bound to this snapshot hash and size.");
        if (receipt.CorrelationId.Length == 0)
            throw new ReceiptRejectedException("Receipt has no correlation id.");
        if (!string.Equals(receipt.AuthorState, "UNRESOLVED", StringComparison.Ordinal)
            && !string.Equals(receipt.AuthorState, "RESOLVED", StringComparison.Ordinal))
            throw new ReceiptRejectedException("Receipt author state is not a known value.");
        return new BoundReceipt(
            item.SourceId,
            item.SnapshotVersion,
            item.ContentSha256,
            item.Size,
            receipt.IngestId,
            receipt.CorrelationId,
            receipt.State,
            receipt.Durability);
    }
}
