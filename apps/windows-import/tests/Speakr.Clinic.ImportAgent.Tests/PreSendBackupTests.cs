using System.Security.Cryptography;

namespace Speakr.Clinic.ImportAgent.Tests;

public class PreSendBackupTests
{
    [Fact]
    public async Task Same_named_recordings_have_separate_verified_backups()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        var backup = Temp.Dir();
        try
        {
            var first = Path.Combine(root, "first", "note.dss");
            var second = Path.Combine(root, "second", "note.dss");
            Temp.Write(first, "first recording");
            Temp.Write(second, "second recording");
            var options = Temp.Options(root, spool, backup: backup, recursive: true);
            options.PreSendBackupEnabled = true;
            options.BackupFileNameTemplate = "{rootId}-{date}-{name}{ext}";
            var server = new SimulatorImportServer();
            var agent = Temp.Agent(options, server);
            var now = DateTimeOffset.Parse("2026-01-02T00:00:00Z");
            var inventory = (await Temp.Stabilize(agent, now)).Inventory;
            server.Enqueue(Temp.Intent(Assert.Single(inventory, item => item.FullPath == first), "none"));
            server.Enqueue(Temp.Intent(Assert.Single(inventory, item => item.FullPath == second), "none"));

            var result = await agent.RunOnceAsync(now.AddSeconds(6));

            Assert.Equal(2, result.Receipts.Count);
            Assert.Equal(2, Directory.GetFiles(backup).Length);
            var firstCopy = Path.Combine(backup, "root-a-20260102-note.dss");
            var secondCopy = Path.Combine(backup, "root-a-20260102-note-1.dss");
            Assert.Equal(SHA256.HashData("first recording"u8), SHA256.HashData(File.ReadAllBytes(firstCopy)));
            Assert.Equal(SHA256.HashData("second recording"u8), SHA256.HashData(File.ReadAllBytes(secondCopy)));
            Assert.Equal("first recording", File.ReadAllText(first));
            Assert.Equal("second recording", File.ReadAllText(second));
        }
        finally
        {
            Temp.Delete(root, spool, backup);
        }
    }

    [Fact]
    public async Task Backup_write_failure_prevents_begin_and_leaves_the_source_intact()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        var backupParent = Temp.Dir();
        try
        {
            var source = Path.Combine(root, "note.dss");
            var blockedFolder = Path.Combine(backupParent, "already-a-file");
            Temp.Write(source, "original recording");
            Temp.Write(blockedFolder, "existing content");
            var options = Temp.Options(root, spool, backup: blockedFolder);
            options.PreSendBackupEnabled = true;
            var server = new SimulatorImportServer();
            var agent = Temp.Agent(options, server);
            var now = DateTimeOffset.Parse("2026-01-02T00:00:00Z");
            var item = Assert.Single((await Temp.Stabilize(agent, now)).Inventory);
            server.Enqueue(Temp.Intent(item, "none"));

            await Assert.ThrowsAsync<IOException>(() => agent.RunOnceAsync(now.AddSeconds(6)));

            Assert.Null(server.LastBegin);
            Assert.Equal("original recording", File.ReadAllText(source));
            Assert.Equal("existing content", File.ReadAllText(blockedFolder));
        }
        finally
        {
            Temp.Delete(root, spool, backupParent);
        }
    }

    [Fact]
    public async Task Hash_mismatch_publishes_no_copy_and_never_starts_the_upload()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        var backup = Temp.Dir();
        try
        {
            Temp.Write(Path.Combine(root, "note.dss"), "original recording");
            var options = Temp.Options(root, spool, backup: backup);
            options.PreSendBackupEnabled = true;
            var server = new SimulatorImportServer();
            var agent = Temp.Agent(options, server);
            var now = DateTimeOffset.Parse("2026-01-02T00:00:00Z");
            var item = Assert.Single((await Temp.Stabilize(agent, now)).Inventory);
            var mismatched = item with { ContentSha256 = new string('0', 64) };

            await Assert.ThrowsAsync<InvalidDataException>(() => agent.UploadAsync(mismatched));

            Assert.Empty(Directory.EnumerateFileSystemEntries(backup));
            Assert.Null(server.LastBegin);
        }
        finally
        {
            Temp.Delete(root, spool, backup);
        }
    }

    [Theory]
    [InlineData("../{name}{ext}")]
    [InlineData("{name}\\copy{ext}")]
    [InlineData("{unknown}{ext}")]
    [InlineData("NUL{ext}")]
    public void Unsafe_filename_templates_are_rejected(string template)
    {
        var options = Temp.Options(Path.GetFullPath("incoming"), Path.GetFullPath("spool"), backup: Path.GetFullPath("backup"));
        options.PreSendBackupEnabled = true;
        options.BackupFileNameTemplate = template;

        Assert.Throws<InvalidOperationException>(options.Validate);
    }
}
