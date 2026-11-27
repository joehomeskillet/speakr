using System.Text;
using System.Text.Json;

namespace Speakr.Clinic.ImportAgent.Tests;

public class DeviceInventoryReplayTests
{
    [Theory]
    [InlineData("philips-lfh-9600")]
    [InlineData("philips-lfh-9620")]
    public async Task Synthetic_inventories_keep_independent_identity_and_unknown_eol_on_reconnect(string disconnectedId)
    {
        // Synthetic folders and DS2 headers only: this is not Philips hardware or firmware evidence.
        var source = Temp.Dir();
        var spool = Temp.Dir();
        var start = DateTimeOffset.Parse("2026-01-02T00:00:00Z");
        var mtime = new DateTime(2026, 1, 2, 3, 4, 5, DateTimeKind.Utc);
        var fixtures = new[] { (Id: "philips-lfh-9600", Author: "QA-A"), (Id: "philips-lfh-9620", Author: "QA-B") };
        try
        {
            var options = Temp.Options(source, spool);
            options.AllowedRoots.Clear();
            foreach (var fixture in fixtures)
            {
                var root = Path.Combine(source, fixture.Id);
                Directory.CreateDirectory(root);
                var path = Path.Combine(root, "note.ds2");
                File.WriteAllBytes(path, Header(fixture.Author));
                File.SetLastWriteTimeUtc(path, mtime);
                // A claimed EOL in an untrusted sidecar must not complete a production observation.
                Temp.Write(path + ".simulator.json", "{\"simulator\":true,\"finished\":true}");
                options.AllowedRoots.Add(new AllowedRoot { Id = fixture.Id, Path = root, Pattern = "*.ds2" });
            }
            Assert.False(options.PostImportEnabled);
            Assert.False(options.SimulatorMode);
            var server = new SimulatorImportServer();
            var agent = Temp.Agent(options, server);
            var before = (await Temp.Stabilize(agent, start)).Inventory;
            Assert.Equal(2, before.Count);
            Assert.Equal(2, before.Select(item => item.SourceId).Distinct().Count());
            Assert.Equal(2, before.Select(item => item.SnapshotVersion).Distinct().Count());

            var disconnected = Path.Combine(source, disconnectedId);
            var detached = Path.Combine(source, "detached");
            Directory.Move(disconnected, detached);
            var remaining = Assert.Single((await agent.RunOnceAsync(start.AddSeconds(6))).Inventory);
            Assert.NotEqual(disconnectedId, remaining.RootId);
            Assert.Equal(before.Single(item => item.RootId == remaining.RootId), remaining);
            Assert.Equal(remaining.SourceId, Assert.Single(server.LastInventory!.Items).SourceId);
            Directory.Move(detached, disconnected);

            var after = (await Temp.Stabilize(agent, start.AddSeconds(10))).Inventory;
            Assert.Equal(2, after.Count);
            // Exercise the serialized inventory envelope as well as the in-process observations.
            var envelope = JsonSerializer.Deserialize<InventoryReport>(
                JsonSerializer.Serialize(server.LastInventory, ImportJson.Options), ImportJson.Options)!;
            Assert.Equal(2, envelope.Items.Count);
            foreach (var fixture in fixtures)
            {
                var original = Assert.Single(before, item => item.RootId == fixture.Id);
                var replay = Assert.Single(after, item => item.RootId == fixture.Id);
                Assert.Equal(original, replay);
                Assert.Equal(fixture.Id + ":note.ds2", replay.SourceId);
                Assert.Equal(fixture.Author, replay.Completion.ClaimedAuthorCode);
                Assert.Equal("unknown", replay.Completion.ObservedCompletion);
                Assert.Equal("unverified", replay.Completion.EvidenceLevel);
                var wire = Assert.Single(envelope.Items, item => item.RootId == fixture.Id);
                Assert.Equal(original.SourceId, wire.SourceId);
                Assert.Equal(original.SnapshotVersion, wire.SnapshotVersion);
                Assert.Equal(original.ContentSha256, wire.Sha256);
                Assert.Equal("unknown", wire.ObservedCompletion);
                Assert.Equal("unverified", wire.EvidenceLevel);
                Assert.Equal(Header(fixture.Author), File.ReadAllBytes(replay.FullPath));
                Assert.Equal(mtime, File.GetLastWriteTimeUtc(replay.FullPath));
                Assert.Equal(2, Directory.GetFiles(Path.GetDirectoryName(replay.FullPath)!).Length);
            }
            Assert.Equal(0, server.IngestCount);
            Assert.Empty(server.PutSequences);
        }
        finally
        {
            Temp.Delete(source, spool);
        }
    }

    private static byte[] Header(string author)
    {
        var header = new byte[1536];
        "\u0003ds2"u8.CopyTo(header);
        Encoding.ASCII.GetBytes(author).CopyTo(header, 0x0c);
        "Author"u8.CopyTo(header.AsSpan(0x240));
        Encoding.ASCII.GetBytes(author).CopyTo(header, 0x25e);
        return header;
    }
}
