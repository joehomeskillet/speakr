using System.Text;

namespace Speakr.Clinic.ImportAgent.ClinicTests;

public sealed class ClinicFolders : IDisposable
{
    public string Source { get; } = Directory.CreateTempSubdirectory("clinic-src-").FullName;
    public string Spool { get; } = Directory.CreateTempSubdirectory("clinic-spool-").FullName;
    public string Backup { get; } = Directory.CreateTempSubdirectory("clinic-bak-").FullName;

    public void Dispose()
    {
        foreach (var path in new[] { Source, Spool, Backup })
        {
            if (Directory.Exists(path))
                Directory.Delete(path, true);
        }
    }
}

public static class FixtureSupport
{
    public const string AgentId = "agent-1";
    public static readonly DateTimeOffset Start = DateTimeOffset.Parse("2026-01-02T00:00:00Z");
    public static byte[] IntentKey { get; } = Enumerable.Range(1, 32).Select(i => (byte)i).ToArray();

    public static byte[] SpoolKey()
    {
        var key = new byte[32];
        key[0] = 4;
        return key;
    }

    public static AgentOptions Options(ClinicFolders folders, bool postImport = false, string mode = "production") =>
        new()
        {
            AgentId = AgentId,
            SpoolDirectory = folders.Spool,
            BackupFolder = folders.Backup,
            PostImportEnabled = postImport,
            ChunkSizeBytes = 8,
            StabilitySamples = 3,
            StabilityIntervalSeconds = 2,
            PollIntervalSeconds = 15,
            FinalizePollDelayMs = 0,
            Mode = mode,
            SpoolMaxBytes = 268435456,
            AllowedRoots = [new AllowedRoot { Id = "root-a", Path = folders.Source, Pattern = "*" }]
        };

    public static ImportOrchestrator CreateAgent(AgentOptions options, IImportServer server, IImportLog? log = null) =>
        ImportOrchestrator.Create(options, server, SpoolKey(), log, IntentKey);

    public static void WriteSynthetic(string path, string text)
    {
        var parent = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(parent))
            Directory.CreateDirectory(parent);
        File.WriteAllText(path, text);
        File.SetLastWriteTimeUtc(path, new DateTime(2026, 1, 2, 3, 4, 5, DateTimeKind.Utc));
    }

    public static ImportIntent SignIntent(InventoryItem item, string postStep)
    {
        var intent = new ImportIntent
        {
            IntentId = Guid.NewGuid().ToString(),
            Kind = ImportIntentKind.ImportSnapshot,
            SourceId = item.SourceId,
            SnapshotVersion = item.SnapshotVersion,
            PostStep = postStep,
            AgentId = AgentId,
            ExpiresAt = "2099-01-01T00:00:00Z"
        };
        intent.Signature = IntentBinding.Sign(intent, AgentId, IntentKey);
        return intent;
    }

    public static async Task<RunResult> Stabilize(ImportOrchestrator agent, DateTimeOffset start)
    {
        await agent.RunOnceAsync(start);
        await agent.RunOnceAsync(start.AddSeconds(2));
        return await agent.RunOnceAsync(start.AddSeconds(4));
    }
}

public class ImportFollowUpTests
{
    [Fact]
    public void Synthetic_source_spool_and_backup_are_temporary()
    {
        string source, spool, backup;
        using (var folders = new ClinicFolders())
        {
            source = folders.Source;
            spool = folders.Spool;
            backup = folders.Backup;
            Assert.Equal(3, new[] { source, spool, backup }.Distinct(StringComparer.Ordinal).Count());
            var temp = Path.GetFullPath(Path.GetTempPath());
            Assert.StartsWith(temp, Path.GetFullPath(source));
            Assert.StartsWith(temp, Path.GetFullPath(spool));
            Assert.StartsWith(temp, Path.GetFullPath(backup));
            var options = FixtureSupport.Options(folders, postImport: true);
            options.Validate();
            Assert.Equal(source, Assert.Single(options.AllowedRoots).Path);
            Assert.Equal(spool, options.SpoolDirectory);
            Assert.Equal(backup, options.BackupFolder);
            Assert.False(new AgentOptions().PostImportEnabled);
        }

        Assert.False(Directory.Exists(source));
        Assert.False(Directory.Exists(spool));
        Assert.False(Directory.Exists(backup));
    }

    [Fact]
    public async Task Marked_file_is_sent_and_an_unselected_file_stays()
    {
        using var folders = new ClinicFolders();
        var marked = Path.Combine(folders.Source, "marked.dss");
        var other = Path.Combine(folders.Source, "other.dss");
        FixtureSupport.WriteSynthetic(marked, "marked-title");
        FixtureSupport.WriteSynthetic(other, "other-title");
        var server = new SimulatorImportServer();
        var agent = FixtureSupport.CreateAgent(FixtureSupport.Options(folders), server);
        var items = (await FixtureSupport.Stabilize(agent, FixtureSupport.Start)).Inventory;
        var selected = Assert.Single(items, item => item.RelativePath == "marked.dss");
        Assert.Single(items, item => item.RelativePath == "other.dss");
        server.Enqueue(FixtureSupport.SignIntent(selected, "none"));
        var sent = await agent.RunOnceAsync(FixtureSupport.Start.AddSeconds(6));
        var receipt = Assert.Single(sent.Receipts);
        Assert.Equal(1, server.IngestCount);
        Assert.True(receipt.Binds(selected, receipt.IngestId));
        Assert.Equal(PostImportOutcome.SkippedPolicyOff, Assert.Single(sent.Post));
        Assert.Equal(Encoding.UTF8.GetBytes("marked-title"), server.ReadStoredBytes(receipt.IngestId));
        Assert.Equal("marked-title", File.ReadAllText(marked));
        Assert.Equal("other-title", File.ReadAllText(other));
    }

    [Fact]
    public async Task Foreign_or_forged_intent_does_not_upload_or_delete()
    {
        using var folders = new ClinicFolders();
        var path = Path.Combine(folders.Source, "note.dss");
        FixtureSupport.WriteSynthetic(path, "stay");
        var server = new SimulatorImportServer();
        var agent = FixtureSupport.CreateAgent(FixtureSupport.Options(folders, postImport: true), server);
        var item = Assert.Single((await FixtureSupport.Stabilize(agent, FixtureSupport.Start)).Inventory);
        var foreign = FixtureSupport.SignIntent(item, "delete");
        foreign.AgentId = "agent-2";
        var forged = FixtureSupport.SignIntent(item, "delete");
        forged.Signature = IntentBinding.Sign(forged, FixtureSupport.AgentId, new byte[32]);
        server.Enqueue(foreign);
        server.Enqueue(forged);
        var result = await agent.RunOnceAsync(FixtureSupport.Start.AddSeconds(6));
        Assert.Equal(0, server.IngestCount);
        Assert.Equal(2, result.Post.Count);
        Assert.All(result.Post, outcome => Assert.Equal(PostImportOutcome.RejectedStep, outcome));
        Assert.Equal("stay", File.ReadAllText(path));
    }

    [Fact]
    public async Task Stale_snapshot_conflicts_and_leaves_the_changed_source()
    {
        using var folders = new ClinicFolders();
        var path = Path.Combine(folders.Source, "note.dss");
        FixtureSupport.WriteSynthetic(path, "original-bytes");
        var server = new SimulatorImportServer();
        var agent = FixtureSupport.CreateAgent(FixtureSupport.Options(folders, postImport: true), server);
        var item = Assert.Single((await FixtureSupport.Stabilize(agent, FixtureSupport.Start)).Inventory);
        var stale = FixtureSupport.SignIntent(item, "delete");
        FixtureSupport.WriteSynthetic(path, "changed-bytes");
        server.Enqueue(stale);
        var result = await agent.RunOnceAsync(FixtureSupport.Start.AddSeconds(6));
        Assert.Equal(PostImportOutcome.Conflict, Assert.Single(result.Post));
        Assert.Equal(0, server.IngestCount);
        Assert.Equal("changed-bytes", File.ReadAllText(path));
    }

    [Fact]
    public async Task Interrupted_upload_retries_without_deleting_the_source()
    {
        using var folders = new ClinicFolders();
        var path = Path.Combine(folders.Source, "note.dss");
        const string text = "abcdefghijklmnopqrst";
        FixtureSupport.WriteSynthetic(path, text);
        var server = new SimulatorImportServer { FailPutsRemaining = 1 };
        var agent = FixtureSupport.CreateAgent(FixtureSupport.Options(folders), server);
        var item = Assert.Single((await FixtureSupport.Stabilize(agent, FixtureSupport.Start)).Inventory);
        var intent = FixtureSupport.SignIntent(item, "delete");
        await Assert.ThrowsAsync<IOException>(() => agent.UploadAsync(item, intent));
        Assert.Equal(text, File.ReadAllText(path));
        var receipt = await agent.UploadAsync(item, intent);
        Assert.Equal([0, 1, 2], server.PutSequences);
        Assert.Equal(Encoding.UTF8.GetBytes(text), server.ReadStoredBytes(receipt.IngestId));
        Assert.Equal(PostImportOutcome.SkippedPolicyOff, agent.ApplyPost(item, intent, receipt));
        Assert.Equal(text, File.ReadAllText(path));
    }
}
