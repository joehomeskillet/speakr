using System.Text;
using System.Text.Json;

namespace Speakr.Clinic.ImportAgent.Tests;

public class InventoryHintTests
{
    [Fact]
    public async Task Only_matching_ds2_author_fields_produce_a_claim()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            var server = new SimulatorImportServer();
            var agent = Temp.Agent(Temp.Options(root, spool), server);
            var start = DateTimeOffset.UtcNow;
            File.WriteAllBytes(Path.Combine(root, "known.ds2"), Header("QA-A"));
            await Temp.Stabilize(agent, start);
            using var knownReport = SerializeReport(server);
            var known = Assert.Single(knownReport.RootElement.GetProperty("items").EnumerateArray());
            Assert.Equal("QA-A", known.GetProperty("claimed_author_code").GetString());
            Assert.Equal("unknown", known.GetProperty("observed_completion").GetString());
            Assert.Equal("unverified", known.GetProperty("evidence_level").GetString());
            Assert.False(known.TryGetProperty("completion_source", out _));
            Assert.False(known.TryGetProperty("author_user_id", out _));

            File.WriteAllBytes(Path.Combine(root, "invalid.ds2"), Header("QA-B", "OTHER"));
            File.WriteAllBytes(Path.Combine(root, "unsupported.ds2"), "QA-B EOL"u8.ToArray());
            await Temp.Stabilize(agent, start.AddSeconds(6));
            using var nextReport = SerializeReport(server);
            var items = nextReport.RootElement.GetProperty("items");
            Assert.Equal(3, items.GetArrayLength());
            foreach (var path in new[] { "invalid.ds2", "unsupported.ds2" })
            {
                var item = Assert.Single(items.EnumerateArray(), entry => entry.GetProperty("relative_path").GetString() == path);
                Assert.False(item.TryGetProperty("claimed_author_code", out _));
                Assert.False(item.TryGetProperty("completion_source", out _));
                Assert.Equal("unknown", item.GetProperty("observed_completion").GetString());
            }
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Theory]
    [InlineData("simulator", """{"simulator":true,"finished":true,"author_code":"QA-A"}""", true, "QA-A")]
    [InlineData("simulator", """{"simulator":true,"finished":true,"author_code":"bad author"}""", true, null)]
    [InlineData("simulator", """{"simulator":true,"finished":false}""", false, null)]
    [InlineData("simulator", """{"simulator":true,"finished":"true","author_code":"bad author"}""", false, null)]
    [InlineData("simulator", """{"simulator":true}""", false, null)]
    [InlineData("production", """{"simulator":true,"finished":true,"author_code":"FIXTURE"}""", false, "QA-A")]
    public async Task Only_explicit_simulator_completion_supplies_eol_fixture_provenance(
        string mode, string sidecar, bool hasEol, string? expectedAuthor)
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            var path = Path.Combine(root, "sample.ds2");
            File.WriteAllBytes(path, Header("QA-A"));
            File.WriteAllText(path + ".simulator.json", sidecar);
            var server = new SimulatorImportServer();
            var agent = Temp.Agent(Temp.Options(root, spool, mode: mode, pattern: "*.ds2"), server);
            await Temp.Stabilize(agent, DateTimeOffset.UtcNow);

            using var report = SerializeReport(server);
            var item = Assert.Single(report.RootElement.GetProperty("items").EnumerateArray());
            Assert.Equal(expectedAuthor is not null, item.TryGetProperty("claimed_author_code", out var author));
            if (expectedAuthor is not null)
                Assert.Equal(expectedAuthor, author.GetString());
            Assert.Equal(hasEol, item.TryGetProperty("completion_source", out var source));
            if (hasEol)
            {
                Assert.Equal("philips_eol", source.GetString());
                Assert.Equal("completed", item.GetProperty("observed_completion").GetString());
                Assert.Equal("fixture", item.GetProperty("evidence_level").GetString());
            }
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Fact]
    public void Inventory_from_older_agents_can_omit_hints()
    {
        var report = JsonSerializer.Deserialize<InventoryReport>(
            """{"items":[{"source_id":"root-a:note.ds2","observed_completion":"unknown","evidence_level":"unverified"}]}""",
            ImportJson.Options);
        var item = Assert.Single(Assert.IsType<InventoryReport>(report).Items);
        Assert.Equal("root-a:note.ds2", item.SourceId);
        using var json = JsonDocument.Parse(JsonSerializer.Serialize(item, ImportJson.Options));
        Assert.False(json.RootElement.TryGetProperty("claimed_author_code", out _));
        Assert.False(json.RootElement.TryGetProperty("completion_source", out _));
    }

    private static byte[] Header(string author, string? keyword = null)
    {
        var header = new byte[1536];
        "\u0003ds2"u8.CopyTo(header);
        Encoding.ASCII.GetBytes(author).CopyTo(header, 0x0c);
        "AUTHOR"u8.CopyTo(header.AsSpan(0x240));
        Encoding.ASCII.GetBytes(keyword ?? author).CopyTo(header, 0x25e);
        return header;
    }

    private static JsonDocument SerializeReport(SimulatorImportServer server) =>
        JsonDocument.Parse(JsonSerializer.Serialize(Assert.IsType<InventoryReport>(server.LastInventory), ImportJson.Options));
}
