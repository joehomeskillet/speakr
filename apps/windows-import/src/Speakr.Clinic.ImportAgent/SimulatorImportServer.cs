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

    public List<AgentOutcome> Outcomes { get; } = [];

    public Task<IngestReceipt> BeginAsync(WireBeginIngest body, string idempotencyKey, CancellationToken cancellationToken)
    {
        if (idempotencyKey.Length is < 8 or > 255)
            throw new ImportServerException(400, "idempotency key");
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
            var session = new Session(Guid.NewGuid().ToString(), Guid.NewGuid().ToString(), hash, body);
            _byKey[idempotencyKey] = session;
            _byId[session.Id] = session;
            return Task.FromResult(session.Receipt());
        }
    }

    public Task<IngestReceipt> PutChunkAsync(
        string ingestId,
        int sequence,
        long offset,
        ReadOnlyMemory<byte> body,
        string sha256,
        CancellationToken cancellationToken)
    {
        if (sequence < 0)
            throw new ImportServerException(400, "sequence");
        if (body.Length < 1 || body.Length > AgentOptions.MaxChunkBytes)
            throw new ImportServerException(400, "chunk size");
        var actual = Hashing.Sha256Hex(body.Span);
        if (!string.Equals(actual, sha256, StringComparison.Ordinal))
            throw new ImportServerException(400, "chunk hash");
        lock (_gate)
        {
            var session = _byId[ingestId];
            if (session.Aborted)
                throw new ImportConflictException("session is ABORTED");
            if (session.Chunks.TryGetValue(sequence, out var previous))
            {
                if (!previous.AsSpan().SequenceEqual(body.Span))
                    throw new ImportConflictException("chunk replay differs");
                return Task.FromResult(session.Receipt());
            }
            var priorBytes = session.Chunks.Where(pair => pair.Key < sequence).Sum(pair => (long)pair.Value.Length);
            var priorCount = session.Chunks.Count(pair => pair.Key < sequence);
            if (priorCount != sequence || offset != priorBytes)
                throw new ImportConflictException("chunk offset out of order");
            if (offset + body.Length > session.DeclaredSize)
                throw new ImportConflictException("chunk exceeds the declared size");
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
            if (!string.Equals(session.DeclaredSha256, body.Sha256, StringComparison.Ordinal)
                || session.DeclaredSize != body.TotalBytes)
                throw new ImportServerException(400, "finalize size or total hash differs from the declaration");
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

    public Task<IngestReceipt> AbortAsync(string ingestId, CancellationToken cancellationToken)
    {
        lock (_gate)
        {
            var session = _byId[ingestId];
            if (session.Finalized is not null || session.Committed)
                throw new ImportConflictException("session cannot be aborted");
            session.Aborted = true;
            session.Chunks.Clear();
            return Task.FromResult(session.Receipt());
        }
    }

    public Task ReportOutcomeAsync(AgentOutcome outcome, CancellationToken cancellationToken)
    {
        lock (_gate)
            Outcomes.Add(outcome);
        return Task.CompletedTask;
    }

    private static string PayloadHash(WireBeginIngest body)
    {
        var text = string.Join('\n', body.IntentId, body.DeclaredSize.ToString(System.Globalization.CultureInfo.InvariantCulture), body.DeclaredSha256);
        return Hashing.Sha256Hex(System.Text.Encoding.UTF8.GetBytes(text));
    }

    private sealed class Session(string id, string correlationId, string payloadHash, WireBeginIngest begin)
    {
        public string Id { get; } = id;
        public string CorrelationId { get; } = correlationId;
        public string PayloadHash { get; } = payloadHash;
        public string DeclaredSha256 { get; } = begin.DeclaredSha256;
        public long DeclaredSize { get; } = begin.DeclaredSize;
        public string IntentId { get; } = begin.IntentId;
        public string SourceId { get; } = begin.SourceId;
        public string SnapshotVersion { get; } = begin.SnapshotVersion;
        public string AgentId { get; } = begin.AgentId;
        public string PostStep { get; } = begin.PostStep.Length == 0 ? "none" : begin.PostStep;
        public Dictionary<int, byte[]> Chunks { get; } = [];
        public WireFinalizeIngest? Finalized { get; set; }
        public bool Committed { get; set; }
        public bool Aborted { get; set; }

        public IngestReceipt Receipt()
        {
            var stored = Finalized is not null && Committed && !Aborted;
            var state = Aborted ? "ABORTED" : stored ? "STORED" : Finalized is null ? "RECEIVING" : "FINALIZING";
            var permitted = stored ? PostStep : "none";
            return new IngestReceipt
            {
                IngestId = Id,
                State = state,
                ReceivedChunks = Chunks.Keys.Order().ToArray(),
                ReceivedBytes = stored ? DeclaredSize : Chunks.Values.Sum(c => (long)c.Length),
                Sha256 = stored ? DeclaredSha256 : null,
                DictationId = null,
                Durability = stored ? "STORED" : "RECEIVED",
                DecodeState = stored ? "PENDING" : "NOT_STARTED",
                Completion = "UNKNOWN",
                AuthorState = "UNRESOLVED",
                CorrelationId = CorrelationId,
                AgentId = AgentId,
                IntentId = IntentId,
                SourceId = SourceId,
                SnapshotVersion = SnapshotVersion,
                Size = DeclaredSize,
                OriginalId = stored ? Id : null,
                PermittedPostStep = permitted,
                DeletionPermitted = permitted == "delete"
            };
        }
    }
}
