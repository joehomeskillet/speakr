using System.Globalization;
using System.Security.Cryptography;

namespace Speakr.Clinic.ImportAgent;

public sealed class PostImport
{
    private readonly AgentOptions _options;
    private readonly byte[]? _intentKey;

    public PostImport(AgentOptions options, byte[]? intentKey = null)
    {
        _options = options;
        _intentKey = intentKey;
    }

    // Test seam: runs after the captured file is verified and before delete/move completes.
    internal Action<string>? AfterVerify { get; set; }

    internal void BackupBeforeSend(InventoryItem item, ReadOnlySpan<byte> bytes)
    {
        var name = _options.FormatBackupName(Path.GetFileNameWithoutExtension(item.RelativePath),
            Path.GetExtension(item.RelativePath), item.RootId, item.MtimeUtc);
        var folder = new DirectoryInfo(_options.BackupFolder);
        var temporary = Path.Combine(_options.BackupFolder, Guid.NewGuid().ToString("N") + ".part");
        if (!PathGuard.TryAdmit(folder.FullName, temporary, out var part))
            throw new IOException("Pre-send backup path is unsafe.");
        for (DirectoryInfo? parent = folder; parent is not null; parent = parent.Parent)
        {
            var attributes = parent.Attributes;
            if (attributes != (FileAttributes)(-1) && (attributes & FileAttributes.ReparsePoint) != 0)
                throw new IOException("BackupFolder must not pass through a reparse point.");
        }
        folder.Create();

        var copy = new FileStream(part, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None);
        var published = false;
        try
        {
            using (copy)
            {
                copy.Write(bytes);
                copy.Flush(flushToDisk: true);
                copy.Position = 0;
                if (copy.Length != item.Size
                    || !Convert.ToHexString(SHA256.HashData(copy)).Equals(item.ContentSha256, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("Pre-send backup does not match the snapshot.");
            }

            var stem = Path.GetFileNameWithoutExtension(name);
            var ext = Path.GetExtension(name);
            for (var suffix = 0; ; suffix++)
            {
                var candidate = suffix == 0 ? name : stem + "-" + suffix.ToString(CultureInfo.InvariantCulture) + ext;
                var destination = Path.Combine(folder.FullName, candidate);
                if (Path.Exists(destination))
                    continue;
                if (!PathGuard.TryAdmit(folder.FullName, destination, out var admitted))
                    throw new IOException("Pre-send backup path is unsafe.");
                try
                {
                    File.Move(part, admitted, overwrite: false);
                    published = true;
                    return;
                }
                catch (Exception ex) when ((ex is IOException or UnauthorizedAccessException) && Path.Exists(destination))
                {
                    // A concurrent writer claimed this filename. Keep its file and try the next suffix.
                }
            }
        }
        finally
        {
            if (!published)
                File.Delete(part);
        }
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
        var delete = string.Equals(intent.PostStep, "delete", StringComparison.Ordinal);
        var move = string.Equals(intent.PostStep, "move_backup", StringComparison.Ordinal);
        if (!delete && !move)
            return PostImportOutcome.RejectedStep;
        if (IntentBinding.Verify(intent, _options.AgentId, _intentKey) != IntentBindingState.Valid)
            return PostImportOutcome.RefusedUnbound;
        // The receipt is the server's grant. A local intent cannot widen it.
        if (delete && (receipt.PermittedPostStep != "delete" || !receipt.DeletionPermitted))
            return PostImportOutcome.RejectedStep;
        if (move && receipt.PermittedPostStep != "move_backup")
            return PostImportOutcome.RejectedStep;
        if (!PathGuard.TryAdmit(item.RootPath, item.FullPath, out var full))
            return PostImportOutcome.Conflict;
        if (!File.Exists(full))
            return PostImportOutcome.Conflict;
        string dest = "";
        if (move)
        {
            if (_options.BackupFolder.Length == 0)
                throw new InvalidOperationException("BackupFolder is not configured.");
            var target = Path.Combine(_options.BackupFolder, item.RootId, item.RelativePath.Replace('/', Path.DirectorySeparatorChar));
            if (!PathGuard.TryAdmit(_options.BackupFolder, target, out dest))
                return PostImportOutcome.Conflict;
            if (File.Exists(dest) || Path.Exists(dest))
                return PostImportOutcome.Conflict;
        }

        // Capture the directory entry first. What is verified below is exactly what gets deleted or
        // copied; a file that replaces the original name afterwards is never touched.
        var hold = full + ".import-hold-" + Guid.NewGuid().ToString("N");
        File.Move(full, hold);
        var restore = true;
        try
        {
            if (!SameSnapshot(item, hold))
                return PostImportOutcome.Conflict;
            AfterVerify?.Invoke(full);
            if (delete)
            {
                File.Delete(hold);
                restore = false;
                return PostImportOutcome.Deleted;
            }
            var parent = Path.GetDirectoryName(dest);
            if (!string.IsNullOrEmpty(parent))
                Directory.CreateDirectory(parent);
            var part = dest + ".part";
            try
            {
                File.Copy(hold, part, overwrite: false);
                if (!string.Equals(Hashing.Sha256Hex(File.ReadAllBytes(part)), item.ContentSha256, StringComparison.Ordinal))
                    return PostImportOutcome.Conflict;
                File.Move(part, dest, overwrite: false);
            }
            finally
            {
                if (File.Exists(part))
                    File.Delete(part);
            }
            File.Delete(hold);
            restore = false;
            return PostImportOutcome.Moved;
        }
        finally
        {
            // Mismatch or failure: put the captured original back. If the name is taken meanwhile the
            // original stays in place under the hold name; nothing is deleted.
            if (restore && File.Exists(hold) && !File.Exists(full))
                File.Move(hold, full);
        }
    }

    private static bool SameSnapshot(InventoryItem item, string path)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.None);
        if (stream.Length != item.Size)
            return false;
        var info = new FileInfo(path);
        if (new DateTimeOffset(info.LastWriteTimeUtc, TimeSpan.Zero) != item.MtimeUtc)
            return false;
        using var memory = new MemoryStream();
        stream.CopyTo(memory);
        return string.Equals(Hashing.Sha256Hex(memory.ToArray()), item.ContentSha256, StringComparison.Ordinal);
    }
}
