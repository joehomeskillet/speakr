namespace Speakr.Clinic.ImportAgent;

public sealed class SimulatorImportServer : IImportServer
{
    private readonly object _gate = new();
    private readonly List<ImportIntent> _intents = [];
    private readonly Dictionary<string, Session> _byKey = new(StringComparer.Ordinal);
    private readonly Dictionary<string, Session> _byId = new(StringComparer.Ordinal);

    public InventoryReport? LastInventory { get; private set; }
    public WireBeginIngest? LastBegin { get; private set; }
    public List<int> PutSequences { get; } = [];
    public int IngestCount => _byId.Count;
    public int FailPutsRemaining { get; set; }
    public bool DurableCommit { get; set; } = true;

    public void Enqueue(ImportIntent intent)
    {
        lock (_gate)
            _intents.Add(intent);
    }

    public byte[] ReadStoredBytes(string ingestId)
    {
        lock (_gate)
        {
            var session = _byId[ingestId];
            var length = session.Chunks.Values.Sum(c => c.Length);
            var all = new byte[length];
            var offset = 0;
            foreach (var sequence in session.Chunks.Keys.Order())
            {
                session.Chunks[sequence].CopyTo(all, offset);
                offset += session.Chunks[sequence].Length;
            }
            return all;
        }
    }

    public Task ReportInventoryAsync(InventoryReport report, CancellationToken cancellationToken)
    {
        lock (_gate)
            LastInventory = report;
        return Task.CompletedTask;
    }

    public Task<IReadOnlyList<ImportIntent>> PollIntentsAsync(string agentId, CancellationToken cancellationToken)
    {
        lock (_gate)
            return Task.FromResult<IReadOnlyList<ImportIntent>>(_intents.ToArray());
    }

    public Task<IngestReceipt> BeginAsync(WireBeginIngest body, string idempotencyKey, CancellationToken cancellationToken)
    {
        if (!string.Equals(body.RequestId, idempotencyKey, StringComparison.Ordinal))
            throw new ImportConflictException("request_id and Idempotency-Key differ.");
        var hash = PayloadHash(body);
        lock (_gate)
        {
            LastBegin = body;
            if (_byKey.TryGetValue(idempotencyKey, out var existing))
            {
                if (!string.Equals(existing.PayloadHash, hash, StringComparison.Ordinal))
                    throw new ImportConflictException("Idempotency key reused with a different source version.");
                return Task.FromResult(existing.Receipt());
            }
            var session = new Session(Guid.NewGuid().ToString(), Guid.NewGuid().ToString(), hash, body.ExpectedSha256, body.ExpectedBytes);
            _byKey[idempotencyKey] = session;
            _byId[session.Id] = session;
            return Task.FromResult(session.Receipt());
        }
    }

    public Task<IngestReceipt> PutChunkAsync(
        string ingestId,
        int sequence,
        ReadOnlyMemory<byte> body,
        string sha256,
        CancellationToken cancellationToken)
    {
        if (sequence < 0)
            throw new ImportServerException(400, "sequence");
        if (body.Length < 1 || body.Length > AgentOptions.MaxChunkBytes)
            throw new ImportServerException(413, "chunk size");
        var actual = Hashing.Sha256Hex(body.Span);
        if (!string.Equals(actual, sha256, StringComparison.Ordinal))
            throw new ImportConflictException("chunk hash");
        lock (_gate)
        {
            var session = _byId[ingestId];
            if (session.Chunks.TryGetValue(sequence, out var previous))
            {
                if (!previous.AsSpan().SequenceEqual(body.Span))
                    throw new ImportConflictException("chunk replay differs");
                return Task.FromResult(session.Receipt());
            }
            session.Chunks[sequence] = body.ToArray();
            PutSequences.Add(sequence);
            var receipt = session.Receipt();
            if (FailPutsRemaining > 0)
            {
                FailPutsRemaining--;
                throw new IOException("simulated interrupt");
            }
            return Task.FromResult(receipt);
        }
    }

    public Task<IngestReceipt> FinalizeAsync(
        string ingestId,
        WireFinalizeIngest body,
        string idempotencyKey,
        CancellationToken cancellationToken)
    {
        lock (_gate)
        {
            var session = _byId[ingestId];
            if (session.Finalized is not null)
            {
                if (!string.Equals(session.Finalized.Sha256, body.Sha256, StringComparison.Ordinal))
                    throw new ImportConflictException("finalize replay differs");
                return Task.FromResult(session.Receipt());
            }
            if (body.Chunks.Count == 0 || body.Chunks.Count != session.Chunks.Count)
                throw new ImportConflictException("chunk map");
            for (var i = 0; i < body.Chunks.Count; i++)
            {
                var digest = body.Chunks[i];
                if (digest.Sequence != i || !session.Chunks.TryGetValue(i, out var chunk))
                    throw new ImportConflictException("chunk sequence");
                if (chunk.Length != digest.Bytes || !string.Equals(Hashing.Sha256Hex(chunk), digest.Sha256, StringComparison.Ordinal))
                    throw new ImportConflictException("chunk digest");
            }
            var stored = ReadStoredBytes(ingestId);
            if (stored.LongLength != body.TotalBytes || !string.Equals(Hashing.Sha256Hex(stored), body.Sha256, StringComparison.Ordinal))
                throw new ImportConflictException("content hash");
            if (session.ExpectedSha256 is not null && !string.Equals(session.ExpectedSha256, body.Sha256, StringComparison.Ordinal))
                throw new ImportConflictException("expected hash");
            session.Finalized = body;
            session.Committed = DurableCommit;
            return Task.FromResult(session.Receipt());
        }
    }

    public Task<IngestReceipt> GetAsync(string ingestId, CancellationToken cancellationToken)
    {
        lock (_gate)
            return Task.FromResult(_byId[ingestId].Receipt());
    }

    private static string PayloadHash(WireBeginIngest body)
    {
        var text = string.Join(
            '\n',
            body.SourceKind,
            body.Source.SourceNamespace,
            body.Source.SourceRecordingId,
            body.ExpectedSha256,
            body.Source.ObservedCompletion,
            body.Source.EvidenceLevel);
        return Hashing.Sha256Hex(System.Text.Encoding.UTF8.GetBytes(text));
    }

    private sealed class Session(string id, string correlationId, string payloadHash, string? expectedSha256, long? expectedBytes)
    {
        public string Id { get; } = id;
        public string CorrelationId { get; } = correlationId;
        public string PayloadHash { get; } = payloadHash;
        public string? ExpectedSha256 { get; } = expectedSha256;
        public long? ExpectedBytes { get; } = expectedBytes;
        public Dictionary<int, byte[]> Chunks { get; } = [];
        public WireFinalizeIngest? Finalized { get; set; }
        public bool Committed { get; set; }

        public IngestReceipt Receipt()
        {
            var stored = Finalized is not null && Committed;
            return new IngestReceipt
            {
                IngestId = Id,
                State = stored ? "STORED" : Finalized is null ? "RECEIVING" : "FINALIZING",
                ReceivedChunks = Chunks.Keys.Order().ToArray(),
                ReceivedBytes = Chunks.Values.Sum(c => (long)c.Length),
                Sha256 = Finalized?.Sha256,
                DictationId = null,
                Durability = stored ? "STORED" : "RECEIVED",
                DecodeState = "NOT_STARTED",
                Completion = "UNKNOWN",
                AuthorState = "UNRESOLVED",
                CorrelationId = CorrelationId
            };
        }
    }
}
