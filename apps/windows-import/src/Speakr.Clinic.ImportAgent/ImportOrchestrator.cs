using System.Text;

namespace Speakr.Clinic.ImportAgent;

public sealed record RunResult(
    IReadOnlyList<InventoryItem> Inventory,
    IReadOnlyList<ImportIntent> Intents,
    IReadOnlyList<BoundReceipt> Receipts,
    IReadOnlyList<PostImportOutcome> Post);

public sealed class ImportOrchestrator
{
    private readonly AgentOptions _options;
    private readonly IImportServer _server;
    private readonly EncryptedSpool _spool;
    private readonly StabilityTracker _stability;
    private readonly ResumableUploader _uploader;
    private readonly PostImport _post;
    private readonly IImportLog _log;

    public ImportOrchestrator(
        AgentOptions options,
        IImportServer server,
        EncryptedSpool spool,
        StabilityTracker stability,
        IImportLog log,
        byte[]? intentKey = null)
    {
        options.Validate();
        _options = options;
        _server = server;
        _spool = spool;
        _stability = stability;
        _uploader = new ResumableUploader(server, spool, options);
        _intentKey = intentKey;
        _post = new PostImport(options, intentKey);
        _log = log;
    }

    private readonly byte[]? _intentKey;
    // Versions already committed (STORED) in this process: not re-spooled every cycle.
    private readonly HashSet<string> _stored = new(StringComparer.Ordinal);

    public static ImportOrchestrator Create(
        AgentOptions options,
        IImportServer server,
        byte[] spoolKey,
        IImportLog? log = null,
        byte[]? intentKey = null)
    {
        options.Validate();
        var spool = new EncryptedSpool(options.SpoolDirectory, spoolKey, options.SpoolMaxBytes);
        var stability = new StabilityTracker(TimeSpan.FromSeconds(options.StabilityIntervalSeconds), options.StabilitySamples);
        return new ImportOrchestrator(options, server, spool, stability, log ?? NullImportLog.Instance, intentKey);
    }

    public async Task<RunResult> RunOnceAsync(DateTimeOffset now, CancellationToken cancellationToken = default)
    {
        var inventory = Collect(now);
        var report = new InventoryReport
        {
            Items = inventory.Select(ToWire).ToList()
        };
        await _server.ReportInventoryAsync(report, cancellationToken);
        var intents = await _server.PollIntentsAsync(_options.AgentId, cancellationToken);
        var receipts = new List<BoundReceipt>();
        var outcomes = new List<PostImportOutcome>();
        // One failing intent must not starve the others; the first failure is rethrown after the pass.
        Exception? firstFailure = null;
        foreach (var intent in intents)
        {
            if (!string.Equals(intent.Kind, ImportIntentKind.ImportSnapshot, StringComparison.Ordinal))
            {
                _log.Info("rejected intent");
                outcomes.Add(PostImportOutcome.RejectedStep);
                await TryReport(intent, PostImportOutcome.RejectedStep, null, cancellationToken);
                continue;
            }
            if (IntentBinding.Verify(intent, _options.AgentId, _intentKey) == IntentBindingState.Invalid)
            {
                _log.Info("rejected intent: foreign agent or bad signature");
                outcomes.Add(PostImportOutcome.RejectedStep);
                await TryReport(intent, PostImportOutcome.RejectedStep, null, cancellationToken);
                continue;
            }
            var match = inventory.FirstOrDefault(item =>
                string.Equals(item.SourceId, intent.SourceId, StringComparison.Ordinal)
                && string.Equals(item.SnapshotVersion, intent.SnapshotVersion, StringComparison.Ordinal));
            if (match is null)
            {
                _log.Info("intent conflict " + LogText.Clean(intent.SourceId));
                outcomes.Add(PostImportOutcome.Conflict);
                await TryReport(intent, PostImportOutcome.Conflict, null, cancellationToken);
                continue;
            }
            try
            {
                if (!EnsureSpooled(match))
                {
                    outcomes.Add(PostImportOutcome.Conflict);
                    await TryReport(intent, PostImportOutcome.Conflict, null, cancellationToken);
                    continue;
                }
                _log.Info("upload " + LogText.Clean(match.SourceId) + " bytes " + match.Size);
                var receipt = await _uploader.UploadAsync(match, intent, cancellationToken);
                receipts.Add(receipt);
                // The spool copy is dropped at the start of the next collection pass.
                _stored.Add(match.SnapshotVersion);
                var post = _post.Apply(match, intent, receipt);
                outcomes.Add(post);
                await TryReport(intent, post, receipt.IngestId, cancellationToken);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                _log.Info("intent failed " + ex.GetType().Name);
                firstFailure ??= ex;
                outcomes.Add(PostImportOutcome.Conflict);
            }
        }
        if (firstFailure is not null)
            System.Runtime.ExceptionServices.ExceptionDispatchInfo.Capture(firstFailure).Throw();
        return new RunResult(inventory, intents, receipts, outcomes);
    }

    // A committed version leaves the spool. If a later intent needs it again, it is re-snapshotted
    // from the source and only accepted when it still has exactly the inventoried hash.
    private bool EnsureSpooled(InventoryItem item)
    {
        if (_spool.Contains(item.SnapshotVersion))
            return true;
        if (!PathGuard.TryAdmit(item.RootPath, item.FullPath, out var full) || !File.Exists(full))
            return false;
        var bytes = File.ReadAllBytes(full);
        if (bytes.LongLength != item.Size || !string.Equals(Hashing.Sha256Hex(bytes), item.ContentSha256, StringComparison.Ordinal))
            return false;
        try
        {
            _spool.Put(item.SnapshotVersion, bytes);
        }
        catch (SpoolLimitException)
        {
            return false;
        }
        return true;
    }

    public IReadOnlyList<InventoryItem> Collect(DateTimeOffset now)
    {
        var items = new List<InventoryItem>();
        foreach (var version in _stored)
        {
            if (_spool.Contains(version))
                _spool.Remove(version);
        }
        foreach (var root in _options.AllowedRoots)
        {
            if (!Directory.Exists(root.Path))
            {
                _log.Info("root missing " + root.Id);
                continue;
            }
            foreach (var file in SourceWalker.List(root))
            {
                var info = new FileInfo(file);
                var mtime = new DateTimeOffset(info.LastWriteTimeUtc, TimeSpan.Zero);
                if (info.Length < 1 || info.Length > AgentOptions.MaxFileBytes)
                {
                    _stability.Reset(file);
                    continue;
                }
                if (!_stability.Observe(file, info.Length, mtime, now))
                    continue;
                byte[] bytes;
                try
                {
                    bytes = File.ReadAllBytes(file);
                }
                catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
                {
                    _stability.Reset(file);
                    _log.Info("read failed " + ex.GetType().Name);
                    continue;
                }
                // Re-check after the read: a directory swapped for a link between listing and read is dropped.
                if (!PathGuard.TryAdmit(root.Path, file, out _))
                {
                    _stability.Reset(file);
                    _log.Info("path changed during read");
                    continue;
                }
                info.Refresh();
                var mtimeAfter = new DateTimeOffset(info.LastWriteTimeUtc, TimeSpan.Zero);
                if (bytes.LongLength != info.Length || mtimeAfter != mtime)
                {
                    _stability.Observe(file, info.Length, mtimeAfter, now);
                    continue;
                }
                var sha = Hashing.Sha256Hex(bytes);
                var relative = Path.GetRelativePath(Path.GetFullPath(root.Path), file).Replace('\\', '/');
                if (!ContractIds.IsRelativePath(relative))
                {
                    _log.Info("path outside the ingest contract");
                    continue;
                }
                var sourceId = ContractIds.SourceId(root.Id, relative);
                var version = Hashing.Sha256Hex(Encoding.UTF8.GetBytes(sourceId + "\n" + bytes.Length + "\n" + mtime.UtcTicks + "\n" + sha));
                if (!_stored.Contains(version))
                {
                    try
                    {
                        _spool.Put(version, bytes);
                    }
                    catch (SpoolLimitException)
                    {
                        _log.Info("spool full");
                        continue;
                    }
                }
                var completion = SidecarReader.Read(file, _options.SimulatorMode);
                items.Add(new InventoryItem(sourceId, root.Id, Path.GetFullPath(root.Path), relative, file, bytes.LongLength, mtime, sha, version, completion));
            }
        }
        // Spool entries of vanished or changed versions never accumulate.
        _spool.Retain(items.Select(i => i.SnapshotVersion));
        return items;
    }

    public Task<BoundReceipt> UploadAsync(InventoryItem item, CancellationToken cancellationToken = default) =>
        UploadAsync(item, SyntheticIntent(item), cancellationToken);

    public Task<BoundReceipt> UploadAsync(InventoryItem item, ImportIntent intent, CancellationToken cancellationToken = default) =>
        _uploader.UploadAsync(item, intent, cancellationToken);

    private ImportIntent SyntheticIntent(InventoryItem item) => new()
    {
        IntentId = item.IdempotencyKey("intent"),
        Kind = ImportIntentKind.ImportSnapshot,
        AgentId = _options.AgentId,
        SourceId = item.SourceId,
        SnapshotVersion = item.SnapshotVersion,
        PostStep = "none",
        ExpiresAt = "2099-01-01T00:00:00Z"
    };

    private async Task TryReport(ImportIntent intent, PostImportOutcome post, string? ingestId, CancellationToken cancellationToken)
    {
        if (!Guid.TryParseExact(intent.IntentId, "D", out _))
            return;
        foreach (var name in OutcomeNames(intent, post, ingestId is not null))
        {
            try
            {
                await _server.ReportOutcomeAsync(new AgentOutcome
                {
                    IntentId = intent.IntentId,
                    IngestId = ingestId,
                    Outcome = name
                }, cancellationToken);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                _log.Info("outcome failed " + ex.GetType().Name);
            }
        }
    }

    private static IEnumerable<string> OutcomeNames(ImportIntent intent, PostImportOutcome post, bool stored)
    {
        if (stored)
            yield return "stored";
        switch (post)
        {
            case PostImportOutcome.Moved:
                yield return "moved";
                break;
            case PostImportOutcome.Deleted:
                yield return "deleted";
                break;
            case PostImportOutcome.Conflict:
                yield return "conflict";
                break;
            case PostImportOutcome.RejectedStep:
            case PostImportOutcome.RefusedUnbound:
                yield return "refused";
                break;
            case PostImportOutcome.SkippedPolicyOff when intent.PostStep is not ("none" or ""):
                yield return "refused";
                break;
        }
    }

    public PostImportOutcome ApplyPost(InventoryItem item, ImportIntent intent, BoundReceipt receipt) =>
        _post.Apply(item, intent, receipt);

    private static InventoryItemWire ToWire(InventoryItem item) => new()
    {
        SourceId = item.SourceId,
        RootId = item.RootId,
        RelativePath = item.RelativePath,
        Size = item.Size,
        MtimeUtc = item.MtimeUtc,
        Sha256 = item.ContentSha256,
        SnapshotVersion = item.SnapshotVersion,
        ObservedCompletion = item.Completion.ObservedCompletion,
        EvidenceLevel = item.Completion.EvidenceLevel
    };
}
