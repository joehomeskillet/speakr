namespace Speakr.Clinic.ImportAgent;

public sealed class PostImport
{
    private readonly AgentOptions _options;

    public PostImport(AgentOptions options)
    {
        _options = options;
    }

    public PostImportOutcome Apply(InventoryItem item, ImportIntent intent, BoundReceipt receipt)
    {
        if (!_options.PostImportEnabled)
            return PostImportOutcome.SkippedPolicyOff;
        if (string.Equals(intent.PostStep, "none", StringComparison.Ordinal) || intent.PostStep.Length == 0)
            return PostImportOutcome.SkippedNoStep;
        if (!receipt.Binds(item, receipt.IngestId))
            throw new ReceiptRejectedException("Post-import requires a receipt bound to this source version.");
        if (!string.Equals(intent.SourceId, item.SourceId, StringComparison.Ordinal)
            || !string.Equals(intent.SnapshotVersion, item.SnapshotVersion, StringComparison.Ordinal))
            return PostImportOutcome.Conflict;
        if (!PathGuard.TryAdmit(item.RootPath, item.FullPath, out var full))
            return PostImportOutcome.Conflict;
        if (!File.Exists(full) || !SameSnapshot(item, full))
            return PostImportOutcome.Conflict;
        if (string.Equals(intent.PostStep, "delete", StringComparison.Ordinal))
        {
            File.Delete(full);
            return PostImportOutcome.Deleted;
        }
        if (string.Equals(intent.PostStep, "move_backup", StringComparison.Ordinal))
        {
            if (_options.BackupFolder.Length == 0)
                throw new InvalidOperationException("BackupFolder is not configured.");
            var dest = Path.Combine(_options.BackupFolder, item.RootId, item.RelativePath.Replace('/', Path.DirectorySeparatorChar));
            if (!PathGuard.TryAdmit(_options.BackupFolder, dest, out var admittedDest))
                return PostImportOutcome.Conflict;
            var parent = Path.GetDirectoryName(admittedDest);
            if (!string.IsNullOrEmpty(parent))
                Directory.CreateDirectory(parent);
            if (File.Exists(admittedDest))
                return PostImportOutcome.Conflict;
            File.Move(full, admittedDest);
            return PostImportOutcome.Moved;
        }
        return PostImportOutcome.RejectedStep;
    }

    private static bool SameSnapshot(InventoryItem item, string fullPath)
    {
        // shortcut: identity is rechecked immediately before delete/move, but the file can still be replaced after the handle closes. Upgrade path is delete-by-open-handle on Windows.
        using var stream = new FileStream(fullPath, FileMode.Open, FileAccess.Read, FileShare.None);
        if (stream.Length != item.Size)
            return false;
        var info = new FileInfo(fullPath);
        if (new DateTimeOffset(info.LastWriteTimeUtc, TimeSpan.Zero) != item.MtimeUtc)
            return false;
        using var memory = new MemoryStream();
        stream.CopyTo(memory);
        return string.Equals(Hashing.Sha256Hex(memory.ToArray()), item.ContentSha256, StringComparison.Ordinal);
    }
}
