using System.Security.Cryptography;
using System.Text;

namespace Speakr.Clinic.ImportAgent.Tests;

public class ApprovedDrivePickupTests
{
    [Fact]
    public async Task New_dictation_observes_changed_author_hint_without_reassigning_the_original()
    {
        var drive = Environment.GetEnvironmentVariable("SPEAKR_QA_MAPPED_DRIVE");
        var source = drive is null ? Temp.Dir()
            : Directory.CreateDirectory(Path.Combine(drive, Guid.NewGuid().ToString("N"))).FullName;
        var spool = Temp.Dir();
        try
        {
            var simulator = new SimulatorImportServer();
            var agent = Temp.Agent(Temp.Options(source, spool, pattern: "*.ds2"), simulator);
            var start = DateTimeOffset.UtcNow;
            byte[] Header(string code)
            {
                var header = new byte[1536];
                "\u0003ds2"u8.CopyTo(header);
                Encoding.ASCII.GetBytes(code).CopyTo(header, 0x0c);
                "Author"u8.CopyTo(header.AsSpan(0x240));
                Encoding.ASCII.GetBytes(code).CopyTo(header, 0x25e);
                return header;
            }
            var firstPath = Path.Combine(source, "first.ds2");
            var firstBytes = Header("QA-A");
            await File.WriteAllBytesAsync(firstPath, firstBytes);
            var first = Assert.Single((await Temp.Stabilize(agent, start)).Inventory);
            Assert.Equal("QA-A", first.Completion.ClaimedAuthorCode);
            await File.WriteAllBytesAsync(Path.Combine(source, "next.ds2"), Header("QA-B"));
            var entries = (await Temp.Stabilize(agent, start.AddSeconds(10))).Inventory;
            var next = Assert.Single(entries, i => i.RelativePath == "next.ds2");
            Assert.Equal("QA-B", next.Completion.ClaimedAuthorCode);
            Assert.Equal("unknown", next.Completion.ObservedCompletion);
            Assert.Equal("unverified", next.Completion.EvidenceLevel);
            Assert.Equal(firstBytes, await File.ReadAllBytesAsync(firstPath));
            var mismatch = Header("QA-A");
            mismatch[0x25e] = (byte)'X';
            Assert.Null(Ds2AuthorHint.Read(mismatch));
            Assert.Null(Ds2AuthorHint.Read(Header("QA<A")));
            Assert.Null(Ds2AuthorHint.Read(firstBytes.AsSpan(0, 27)));
        }
        finally
        {
            Temp.Delete(source, spool);
        }
    }

    [Fact]
    public async Task Approved_folder_collects_and_replays_one_unchanged_recording()
    {
        // The Windows operator script supplies its fresh SUBST drive. Regular CI uses a private folder.
        var drive = Environment.GetEnvironmentVariable("SPEAKR_QA_MAPPED_DRIVE");
        var source = drive is null ? Temp.Dir()
            : Directory.CreateDirectory(Path.Combine(drive, Guid.NewGuid().ToString("N"))).FullName;
        var spool = Temp.Dir();
        try
        {
            var name = "simulation.wav";
            var file = Path.Combine(source, name);
            using (var writer = new BinaryWriter(File.Create(file)))
            {
                // 0.25 seconds of silent, mono PCM at 8 kHz, with a standard RIFF/WAVE header.
                writer.Write("RIFF"u8); writer.Write(4036); writer.Write("WAVEfmt "u8);
                writer.Write(16); writer.Write((short)1); writer.Write((short)1);
                writer.Write(8000); writer.Write(16000); writer.Write((short)2); writer.Write((short)16);
                writer.Write("data"u8); writer.Write(4000); writer.Write(new byte[4000]);
            }
            var original = await File.ReadAllBytesAsync(file);
            var hash = Convert.ToHexStringLower(SHA256.HashData(original));
            var mtime = File.GetLastWriteTimeUtc(file);
            var server = new SimulatorImportServer();
            var options = Temp.Options(source, spool, pattern: "*.wav");
            options.ChunkSizeBytes = 1024;
            var agent = Temp.Agent(options, server);
            var start = DateTimeOffset.UtcNow;
            var item = Assert.Single((await Temp.Stabilize(agent, start)).Inventory);
            Assert.Equal(name, item.RelativePath);
            Assert.Equal(hash, item.ContentSha256);
            Assert.Null(item.Completion.ClaimedAuthorCode);
            server.Enqueue(Temp.Intent(item, "none"));

            var first = Assert.Single((await agent.RunOnceAsync(start.AddSeconds(6))).Receipts);
            Assert.True(first.IsDurable);
            Assert.Equal(original, server.ReadStoredBytes(first.IngestId));
            var transferred = server.PutSequences.Count;
            Assert.True(transferred > 0);
            var replay = Assert.Single((await agent.RunOnceAsync(start.AddSeconds(8))).Receipts);
            Assert.True(replay.IsDurable);
            Assert.Equal(first.IngestId, replay.IngestId);
            Assert.Equal(1, server.IngestCount);
            Assert.Equal(transferred, server.PutSequences.Count);
            Assert.Equal(original, await File.ReadAllBytesAsync(file));
            Assert.Equal(mtime, File.GetLastWriteTimeUtc(file));
        }
        finally
        {
            Temp.Delete(source, spool);
        }
    }
}
