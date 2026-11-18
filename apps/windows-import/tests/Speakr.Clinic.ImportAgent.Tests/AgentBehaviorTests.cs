using System.Text;
using System.Text.Json;

namespace Speakr.Clinic.ImportAgent.Tests;

public class AgentBehaviorTests
{
    [Fact]
    public void Three_unchanged_samples_are_required()
    {
        var tracker = new StabilityTracker(TimeSpan.FromSeconds(2), 3);
        var mtime = DateTimeOffset.Parse("2026-01-02T03:04:05Z");
        var start = DateTimeOffset.Parse("2026-01-02T00:00:00Z");
        Assert.False(tracker.Observe("a", 10, mtime, start));
        Assert.False(tracker.Observe("a", 10, mtime, start.AddSeconds(1)));
        Assert.False(tracker.Observe("a", 10, mtime, start.AddSeconds(2)));
        Assert.True(tracker.Observe("a", 10, mtime, start.AddSeconds(4)));
    }

    [Fact]
    public void Size_change_resets_stability()
    {
        var tracker = new StabilityTracker(TimeSpan.FromSeconds(2), 3);
        var mtime = DateTimeOffset.Parse("2026-01-02T03:04:05Z");
        var start = DateTimeOffset.Parse("2026-01-02T00:00:00Z");
        tracker.Observe("a", 10, mtime, start);
        tracker.Observe("a", 10, mtime, start.AddSeconds(2));
        Assert.False(tracker.Observe("a", 11, mtime, start.AddSeconds(4)));
        Assert.False(tracker.Observe("a", 11, mtime, start.AddSeconds(6)));
        Assert.True(tracker.Observe("a", 11, mtime, start.AddSeconds(8)));
    }

    [Fact]
    public async Task Changing_file_is_not_inventoried()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            var path = Path.Combine(root, "note.dss");
            Temp.Write(path, "aaaa");
            var agent = Temp.Agent(Temp.Options(root, spool), new SimulatorImportServer());
            var start = DateTimeOffset.Parse("2026-01-02T00:00:00Z");
            Assert.Empty((await agent.RunOnceAsync(start)).Inventory);
            File.WriteAllText(path, "aaaa-more");
            Assert.Empty((await agent.RunOnceAsync(start.AddSeconds(2))).Inventory);
            Assert.Empty((await agent.RunOnceAsync(start.AddSeconds(4))).Inventory);
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Fact]
    public async Task Stable_file_inventory_records_path_size_mtime_and_sha256()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            var path = Path.Combine(root, "note.dss");
            Temp.Write(path, "abc");
            var server = new SimulatorImportServer();
            var agent = Temp.Agent(Temp.Options(root, spool), server);
            var result = await Temp.Stabilize(agent, DateTimeOffset.Parse("2026-01-02T00:00:00Z"));
            var item = Assert.Single(result.Inventory);
            Assert.Equal("root-a:note.dss", item.SourceId);
            Assert.Equal(3, item.Size);
            Assert.Equal("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", item.ContentSha256);
            Assert.Equal("unknown", item.Completion.ObservedCompletion);
            Assert.Equal(new DateTimeOffset(File.GetLastWriteTimeUtc(path), TimeSpan.Zero), item.MtimeUtc);
            var json = JsonSerializer.Serialize(server.LastInventory, ImportJson.Options);
            Assert.DoesNotContain(root, json);
            Assert.DoesNotContain("full_path", json);
            Assert.False(new AgentOptions().PostImportEnabled);
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Fact]
    public async Task Interrupted_upload_resumes_with_identical_original_bytes()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            var path = Path.Combine(root, "note.dss");
            var text = "abcdefghijklmnopqrst";
            Temp.Write(path, text);
            var server = new SimulatorImportServer { FailPutsRemaining = 1 };
            var agent = Temp.Agent(Temp.Options(root, spool), server);
            var item = Assert.Single((await Temp.Stabilize(agent, DateTimeOffset.Parse("2026-01-02T00:00:00Z"))).Inventory);
            await Assert.ThrowsAsync<IOException>(() => agent.UploadAsync(item));
            Assert.Equal([0], server.PutSequences);
            File.WriteAllText(path, "ZZZZZZZZZZZZZZZZZZZZ");
            var receipt = await agent.UploadAsync(item);
            Assert.Equal([0, 1, 2], server.PutSequences);
            Assert.Equal(Encoding.UTF8.GetBytes(text), server.ReadStoredBytes(receipt.IngestId));
            Assert.Equal(text.Length, receipt.Size);
            Assert.True(receipt.Binds(item, receipt.IngestId));
            var again = await agent.UploadAsync(item);
            Assert.Equal(receipt.IngestId, again.IngestId);
            Assert.Equal(1, server.IngestCount);
            var stored = await server.GetAsync(receipt.IngestId, CancellationToken.None);
            Assert.Equal("UNKNOWN", stored.Completion);
            Assert.Equal("UNRESOLVED", stored.AuthorState);
            Assert.Null(stored.DictationId);
            Assert.Equal("STORED", stored.Durability);
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Fact]
    public async Task Source_deletion_stays_off_and_changed_source_conflicts()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        var backup = Temp.Dir();
        try
        {
            var path = Path.Combine(root, "note.dss");
            Temp.Write(path, "original-bytes");
            var server = new SimulatorImportServer();
            var start = DateTimeOffset.Parse("2026-01-02T00:00:00Z");
            var agent = Temp.Agent(Temp.Options(root, spool), server);
            var item = Assert.Single((await Temp.Stabilize(agent, start)).Inventory);
            server.Enqueue(Temp.Intent(item, "delete"));
            var skipped = await agent.RunOnceAsync(start.AddSeconds(6));
            Assert.Equal(PostImportOutcome.SkippedPolicyOff, Assert.Single(skipped.Post));
            Assert.Equal("original-bytes", File.ReadAllText(path));

            var enabled = Temp.Agent(Temp.Options(root, spool, post: true, backup: backup), new SimulatorImportServer());
            var stable = Assert.Single((await Temp.Stabilize(enabled, start.AddSeconds(10))).Inventory);
            var deleteIntent = Temp.Intent(stable, "delete");
            var receipt = await enabled.UploadAsync(stable, deleteIntent);
            File.WriteAllText(path, "changed-bytes");
            Assert.Equal(PostImportOutcome.Conflict, enabled.ApplyPost(stable, deleteIntent, receipt));
            Assert.Equal("changed-bytes", File.ReadAllText(path));
            var foreign = receipt with { SourceId = "other-root/note.dss" };
            Assert.Throws<ReceiptRejectedException>(() => enabled.ApplyPost(stable, Temp.Intent(stable, "delete"), foreign));
            Assert.Equal("changed-bytes", File.ReadAllText(path));
        }
        finally
        {
            Temp.Delete(root, spool, backup);
        }
    }

    [Fact]
    public async Task Enabled_policy_deletes_only_the_bound_snapshot_or_moves_it()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        var backup = Temp.Dir();
        try
        {
            var path = Path.Combine(root, "note.dss");
            Temp.Write(path, "original-bytes");
            var server = new SimulatorImportServer();
            var start = DateTimeOffset.Parse("2026-01-02T00:00:00Z");
            var mover = Temp.Agent(Temp.Options(root, spool, post: true, backup: backup), server);
            var item = Assert.Single((await Temp.Stabilize(mover, start)).Inventory);
            server.Enqueue(Temp.Intent(item, "move_backup"));
            var moved = await mover.RunOnceAsync(start.AddSeconds(6));
            Assert.Equal(PostImportOutcome.Moved, Assert.Single(moved.Post));
            var dest = Path.Combine(backup, "root-a", "note.dss");
            Assert.False(File.Exists(path));
            Assert.Equal("original-bytes", File.ReadAllText(dest));

            Temp.Write(path, "second-bytes");
            var deleteServer = new SimulatorImportServer();
            var deleter = Temp.Agent(Temp.Options(root, spool, post: true, backup: backup), deleteServer);
            var second = Assert.Single((await Temp.Stabilize(deleter, start.AddSeconds(20))).Inventory);
            deleteServer.Enqueue(Temp.Intent(second, "delete"));
            var deleted = await deleter.RunOnceAsync(start.AddSeconds(26));
            Assert.Equal(PostImportOutcome.Deleted, Assert.Single(deleted.Post));
            Assert.False(File.Exists(path));
            Assert.Equal(Encoding.UTF8.GetBytes("second-bytes"), deleteServer.ReadStoredBytes(Assert.Single(deleted.Receipts).IngestId));
        }
        finally
        {
            Temp.Delete(root, spool, backup);
        }
    }

    [Fact]
    public async Task Non_durable_receipt_does_not_delete()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            var path = Path.Combine(root, "note.dss");
            Temp.Write(path, "keep-me");
            var server = new SimulatorImportServer { DurableCommit = false };
            var start = DateTimeOffset.Parse("2026-01-02T00:00:00Z");
            var agent = Temp.Agent(Temp.Options(root, spool, post: true), server);
            var item = Assert.Single((await Temp.Stabilize(agent, start)).Inventory);
            server.Enqueue(Temp.Intent(item, "delete"));
            await Assert.ThrowsAsync<ReceiptRejectedException>(() => agent.RunOnceAsync(start.AddSeconds(6)));
            Assert.Equal("keep-me", File.ReadAllText(path));
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Fact]
    public async Task Unknown_intent_and_post_step_do_not_touch_the_source()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            var path = Path.Combine(root, "note.dss");
            Temp.Write(path, "stay");
            var server = new SimulatorImportServer();
            var start = DateTimeOffset.Parse("2026-01-02T00:00:00Z");
            var agent = Temp.Agent(Temp.Options(root, spool, post: true), server);
            server.Enqueue(new ImportIntent
            {
                IntentId = "x",
                Kind = "firmware_flash",
                SourceId = "root-a/note.dss",
                SnapshotVersion = "nope",
                PostStep = "delete"
            });
            var rejected = await agent.RunOnceAsync(start);
            Assert.Equal(PostImportOutcome.RejectedStep, Assert.Single(rejected.Post));
            var item = Assert.Single((await Temp.Stabilize(agent, start)).Inventory);
            var receipt = await agent.UploadAsync(item);
            Assert.Equal(PostImportOutcome.RejectedStep, agent.ApplyPost(item, Temp.Intent(item, "execute"), receipt));
            Assert.Equal("stay", File.ReadAllText(path));
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Fact]
    public async Task Same_bytes_from_two_sources_stay_distinct_and_retries_are_idempotent()
    {
        var rootA = Temp.Dir();
        var rootB = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            Temp.Write(Path.Combine(rootA, "note.dss"), "abc");
            Temp.Write(Path.Combine(rootB, "note.dss"), "abc");
            var options = Temp.Options(rootA, spool);
            options.AllowedRoots.Add(new AllowedRoot { Id = "root-b", Path = rootB, Pattern = "*" });
            var server = new SimulatorImportServer();
            var agent = Temp.Agent(options, server);
            var start = DateTimeOffset.Parse("2026-01-02T00:00:00Z");
            var items = (await Temp.Stabilize(agent, start)).Inventory;
            Assert.Equal(2, items.Count);
            Assert.Equal(items[0].ContentSha256, items[1].ContentSha256);
            Assert.NotEqual(items[0].SourceId, items[1].SourceId);
            Assert.NotEqual(items[0].SnapshotVersion, items[1].SnapshotVersion);
            server.Enqueue(Temp.Intent(items[0], "none"));
            server.Enqueue(Temp.Intent(items[1], "none"));
            var uploaded = await agent.RunOnceAsync(start.AddSeconds(6));
            Assert.Equal(2, server.IngestCount);
            Assert.Equal(2, uploaded.Receipts.Select(r => r.IngestId).Distinct().Count());
            await agent.RunOnceAsync(start.AddSeconds(8));
            Assert.Equal(2, server.IngestCount);
        }
        finally
        {
            Temp.Delete(rootA, rootB, spool);
        }
    }

    [Fact]
    public async Task Production_ignores_simulator_sidecar_and_simulator_mode_marks_fixture()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            var path = Path.Combine(root, "note.dss");
            Temp.Write(path, "abc");
            File.WriteAllText(path + ".simulator.json", """{"simulator":true,"finished":true,"author_code":"hdr-author"}""");
            var start = DateTimeOffset.Parse("2026-01-02T00:00:00Z");
            var production = new SimulatorImportServer();
            var agent = Temp.Agent(Temp.Options(root, spool), production);
            var item = Assert.Single((await Temp.Stabilize(agent, start)).Inventory);
            Assert.Equal("unknown", item.Completion.ObservedCompletion);
            Assert.Equal("unverified", item.Completion.EvidenceLevel);
            Assert.Null(item.Completion.ClaimedAuthorCode);
            production.Enqueue(Temp.Intent(item, "none"));
            await agent.RunOnceAsync(start.AddSeconds(6));
            var json = JsonSerializer.Serialize(production.LastBegin, ImportJson.Options);
            Assert.DoesNotContain("source_kind", json);
            Assert.DoesNotContain("request_id", json);
            Assert.DoesNotContain("author_user_id", json);
            Assert.DoesNotContain("hdr-author", json);

            var simulated = new SimulatorImportServer();
            var simulatorAgent = Temp.Agent(Temp.Options(root, spool, mode: "simulator"), simulated);
            var fixture = Assert.Single((await Temp.Stabilize(simulatorAgent, start.AddSeconds(30))).Inventory);
            Assert.Equal("completed", fixture.Completion.ObservedCompletion);
            Assert.Equal("fixture", fixture.Completion.EvidenceLevel);
            Assert.Equal("hdr-author", fixture.Completion.ClaimedAuthorCode);
            simulated.Enqueue(Temp.Intent(fixture, "none"));
            await simulatorAgent.RunOnceAsync(start.AddSeconds(36));
            var simulatedJson = JsonSerializer.Serialize(simulated.LastBegin, ImportJson.Options);
            Assert.DoesNotContain("source_kind", simulatedJson);
            Assert.DoesNotContain("request_id", simulatedJson);
            Assert.DoesNotContain("author_user_id", simulatedJson);
            Assert.DoesNotContain("hdr-author", simulatedJson);
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }
}
