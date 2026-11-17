using System.Net;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;

namespace Speakr.Clinic.ImportAgent.Tests;

// Regression tests for the wave review of the import agent. Each test fails on the pre-fix code.
public class ReviewFixTests
{
    private static readonly DateTimeOffset Start = DateTimeOffset.Parse("2026-01-02T00:00:00Z");

    // Wraps the simulator so single calls can be tampered with.
    private class Forwarding(SimulatorImportServer inner) : IImportServer
    {
        public SimulatorImportServer Inner { get; } = inner;

        public virtual Task ReportInventoryAsync(InventoryReport report, CancellationToken ct) => Inner.ReportInventoryAsync(report, ct);

        public virtual Task<IReadOnlyList<ImportIntent>> PollIntentsAsync(string agentId, CancellationToken ct) => Inner.PollIntentsAsync(agentId, ct);

        public virtual Task<IngestReceipt> BeginAsync(WireBeginIngest body, string key, CancellationToken ct) => Inner.BeginAsync(body, key, ct);

        public virtual Task<IngestReceipt> PutChunkAsync(string id, int seq, long offset, ReadOnlyMemory<byte> body, string sha, CancellationToken ct) =>
            Inner.PutChunkAsync(id, seq, offset, body, sha, ct);

        public virtual Task<IngestReceipt> FinalizeAsync(string id, WireFinalizeIngest body, string key, CancellationToken ct) =>
            Inner.FinalizeAsync(id, body, key, ct);

        public virtual Task<IngestReceipt> GetAsync(string id, CancellationToken ct) => Inner.GetAsync(id, ct);

        public virtual Task<IngestReceipt> AbortAsync(string ingestId, CancellationToken ct) => Inner.AbortAsync(ingestId, ct);

        public virtual Task ReportOutcomeAsync(AgentOutcome outcome, CancellationToken ct) => Inner.ReportOutcomeAsync(outcome, ct);
    }

    private sealed class Recording : HttpMessageHandler
    {
        public List<Uri> Uris { get; } = [];
        public string Body { get; set; } = "{\"intents\":[]}";

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Uris.Add(request.RequestUri!);
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(Body, Encoding.UTF8, "application/json")
            });
        }
    }

    private static string ReceiptJson(string ingestId) => JsonSerializer.Serialize(new IngestReceipt
    {
        IngestId = ingestId,
        State = "RECEIVING",
        ReceivedChunks = [],
        Durability = "RECEIVED",
        DecodeState = "NOT_STARTED",
        Completion = "UNKNOWN",
        AuthorState = "UNRESOLVED",
        CorrelationId = "22222222-2222-2222-2222-222222222222"
    }, ImportJson.Options);

    private static async Task<(InventoryItem Item, BoundReceipt Receipt, AgentOptions Options, ImportOrchestrator Agent, string Path)> Stored(
        string root, string spool, string? backup, string text, string step = "delete")
    {
        var path = Path.Combine(root, "note.dss");
        Temp.Write(path, text);
        var options = Temp.Options(root, spool, post: true, backup: backup);
        var agent = Temp.Agent(options, new SimulatorImportServer());
        var item = Assert.Single((await Temp.Stabilize(agent, Start)).Inventory);
        var receipt = await agent.UploadAsync(item, Temp.Intent(item, step));
        return (item, receipt, options, agent, path);
    }

    [Fact]
    public async Task Replacement_written_after_verification_is_never_deleted()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            var (item, receipt, options, _, path) = await Stored(root, spool, null, "original-bytes");
            var post = new PostImport(options, Temp.IntentKey)
            {
                AfterVerify = original => File.WriteAllText(original, "replacement-after-check")
            };
            Assert.Equal(PostImportOutcome.Deleted, post.Apply(item, Temp.Intent(item, "delete"), receipt));
            Assert.Equal("replacement-after-check", File.ReadAllText(path));
            Assert.Single(Directory.GetFiles(root));
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Fact]
    public async Task Replacement_written_after_verification_is_not_taken_by_a_move_either()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        var backup = Temp.Dir();
        try
        {
            var (item, receipt, options, _, path) = await Stored(root, spool, backup, "original-bytes", "move_backup");
            var post = new PostImport(options, Temp.IntentKey)
            {
                AfterVerify = original => File.WriteAllText(original, "replacement-after-check")
            };
            Assert.Equal(PostImportOutcome.Moved, post.Apply(item, Temp.Intent(item, "move_backup"), receipt));
            Assert.Equal("replacement-after-check", File.ReadAllText(path));
            Assert.Equal("original-bytes", File.ReadAllText(Path.Combine(backup, "root-a", "note.dss")));
        }
        finally
        {
            Temp.Delete(root, spool, backup);
        }
    }

    [Fact]
    public async Task Source_replaced_before_capture_is_restored_untouched()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            var (item, receipt, options, _, path) = await Stored(root, spool, null, "original-bytes");
            File.WriteAllText(path, "other-bytes!!");
            Assert.Equal(PostImportOutcome.Conflict, new PostImport(options, Temp.IntentKey).Apply(item, Temp.Intent(item, "delete"), receipt));
            Assert.Equal("other-bytes!!", File.ReadAllText(path));
            Assert.Single(Directory.GetFiles(root));
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Fact]
    public async Task Destructive_step_needs_a_verifiable_intent()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            var (item, receipt, options, _, path) = await Stored(root, spool, null, "original-bytes");
            var post = new PostImport(options, Temp.IntentKey);
            var unsigned = Temp.Intent(item, "delete");
            unsigned.AgentId = "";
            unsigned.Signature = "";
            Assert.Equal(PostImportOutcome.RefusedUnbound, post.Apply(item, unsigned, receipt));
            Assert.Equal(PostImportOutcome.RefusedUnbound, new PostImport(options).Apply(item, Temp.Intent(item, "delete"), receipt));
            var tampered = Temp.Intent(item, "delete");
            tampered.PostStep = "move_backup";
            Assert.Equal(PostImportOutcome.RefusedUnbound, post.Apply(item, tampered, receipt));
            Assert.Equal("original-bytes", File.ReadAllText(path));
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Fact]
    public async Task Foreign_or_badly_signed_intent_starts_no_upload()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            Temp.Write(Path.Combine(root, "note.dss"), "abc");
            var server = new SimulatorImportServer();
            var agent = Temp.Agent(Temp.Options(root, spool, post: true), server);
            var item = Assert.Single((await Temp.Stabilize(agent, Start)).Inventory);
            var foreign = Temp.Intent(item, "none");
            foreign.AgentId = "agent-2";
            var forged = Temp.Intent(item, "none");
            forged.Signature = IntentBinding.Sign(forged, "agent-1", new byte[32]);
            server.Enqueue(foreign);
            server.Enqueue(forged);
            var result = await agent.RunOnceAsync(Start.AddSeconds(6));
            Assert.Equal(0, server.IngestCount);
            Assert.All(result.Post, outcome => Assert.Equal(PostImportOutcome.RejectedStep, outcome));
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Fact]
    public async Task Receipt_for_another_ingest_than_the_session_is_rejected()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            Temp.Write(Path.Combine(root, "note.dss"), "abcdefgh12345678");
            var inner = new SimulatorImportServer();
            var swapping = new SwapOnFinalize(inner);
            var agent = Temp.Agent(Temp.Options(root, spool), swapping);
            var item = Assert.Single((await Temp.Stabilize(agent, Start)).Inventory);
            await Assert.ThrowsAsync<ReceiptRejectedException>(() => agent.UploadAsync(item));
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    private sealed class SwapOnFinalize(SimulatorImportServer inner) : Forwarding(inner)
    {
        public override async Task<IngestReceipt> FinalizeAsync(string id, WireFinalizeIngest body, string key, CancellationToken ct)
        {
            var receipt = await base.FinalizeAsync(id, body, key, ct);
            receipt.IngestId = Guid.NewGuid().ToString();
            return receipt;
        }
    }

    [Fact]
    public async Task Server_supplied_ingest_id_with_path_characters_never_reaches_a_url()
    {
        var handler = new Recording { Body = ReceiptJson("../../agent/inventory") };
        using var client = new HttpClient(handler) { BaseAddress = new Uri("https://clinic.example.invalid") };
        var server = new HttpImportServer(client);
        await Assert.ThrowsAsync<ImportServerException>(() =>
            server.BeginAsync(new WireBeginIngest(), Guid.NewGuid().ToString(), CancellationToken.None));
        await Assert.ThrowsAsync<ImportServerException>(() =>
            server.PutChunkAsync("../../agent/inventory", 0, 0, new byte[] { 1 }, "00", CancellationToken.None));
        await Assert.ThrowsAsync<ImportServerException>(() => server.GetAsync("x/../y", CancellationToken.None));
        Assert.Single(handler.Uris);
    }

    [Fact]
    public async Task Intent_poll_sends_no_self_asserted_agent_id()
    {
        var handler = new Recording();
        using var client = new HttpClient(handler) { BaseAddress = new Uri("https://clinic.example.invalid") };
        await new HttpImportServer(client).PollIntentsAsync("agent-1", CancellationToken.None);
        var uri = Assert.Single(handler.Uris);
        Assert.Equal("", uri.Query);
        Assert.DoesNotContain("agent-1", uri.ToString(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task Committed_files_leave_the_spool_so_it_never_stalls_new_files()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            Temp.Write(Path.Combine(root, "a.dss"), "aaaaaaaaaaaaaaaaaaaa");
            var server = new SimulatorImportServer();
            var agent = Temp.Agent(Temp.Options(root, spool, spoolMax: 30), server);
            var first = Assert.Single((await Temp.Stabilize(agent, Start)).Inventory);
            server.Enqueue(Temp.Intent(first, "none"));
            await agent.RunOnceAsync(Start.AddSeconds(6));
            Temp.Write(Path.Combine(root, "b.dss"), "bbbbbbbbbbbbbbbbbbbb");
            await agent.RunOnceAsync(Start.AddSeconds(8));
            await agent.RunOnceAsync(Start.AddSeconds(10));
            var third = await agent.RunOnceAsync(Start.AddSeconds(12));
            Assert.Equal(2, third.Inventory.Count);
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Fact]
    public async Task Changed_files_do_not_leave_orphans_in_the_spool()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            var path = Path.Combine(root, "a.dss");
            Temp.Write(path, "version-one-bytes");
            var agent = Temp.Agent(Temp.Options(root, spool), new SimulatorImportServer());
            await Temp.Stabilize(agent, Start);
            Assert.Single(Directory.GetFiles(spool));
            File.Delete(path);
            await agent.RunOnceAsync(Start.AddSeconds(6));
            Assert.Empty(Directory.GetFiles(spool));
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Fact]
    public void Spool_entry_is_bound_to_its_name_by_aad()
    {
        var dir = Temp.Dir();
        try
        {
            var spool = new EncryptedSpool(dir, Temp.Key(), 1024);
            var a = Hashing.Sha256Hex(Encoding.UTF8.GetBytes("a"));
            var b = Hashing.Sha256Hex(Encoding.UTF8.GetBytes("b"));
            spool.Put(a, Encoding.UTF8.GetBytes("payload-a"));
            spool.Put(b, Encoding.UTF8.GetBytes("payload-b"));
            File.Copy(Path.Combine(dir, a), Path.Combine(dir, b), overwrite: true);
            Assert.ThrowsAny<CryptographicException>(() => spool.Read(b));
        }
        finally
        {
            Temp.Delete(dir);
        }
    }

    [Fact]
    public void Corrupt_or_forged_size_index_cannot_break_or_bypass_the_spool()
    {
        var dir = Temp.Dir();
        try
        {
            File.WriteAllText(Path.Combine(dir, "sizes.json"), "{ this is not json");
            var spool = new EncryptedSpool(dir, Temp.Key(), 60);
            spool.Put(Hashing.Sha256Hex(Encoding.UTF8.GetBytes("a")), new byte[40]);
            var again = new EncryptedSpool(dir, Temp.Key(), 60);
            Assert.Throws<SpoolLimitException>(() => again.Put(Hashing.Sha256Hex(Encoding.UTF8.GetBytes("b")), new byte[40]));
        }
        finally
        {
            Temp.Delete(dir);
        }
    }

    [Fact]
    public void Spool_put_is_idempotent_and_does_not_reencrypt()
    {
        var dir = Temp.Dir();
        try
        {
            var spool = new EncryptedSpool(dir, Temp.Key(), 1024);
            var version = Hashing.Sha256Hex(Encoding.UTF8.GetBytes("a"));
            spool.Put(version, new byte[10]);
            var before = File.ReadAllBytes(Path.Combine(dir, version));
            spool.Put(version, new byte[10]);
            Assert.Equal(before, File.ReadAllBytes(Path.Combine(dir, version)));
        }
        finally
        {
            Temp.Delete(dir);
        }
    }

    [Fact]
    public async Task Server_state_beyond_the_local_snapshot_is_a_conflict()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            Temp.Write(Path.Combine(root, "note.dss"), "abcdefgh12345678");
            var agent = Temp.Agent(Temp.Options(root, spool), new TooMuch(new SimulatorImportServer(), chunks: [99], bytes: 0));
            var item = Assert.Single((await Temp.Stabilize(agent, Start)).Inventory);
            await Assert.ThrowsAsync<ImportConflictException>(() => agent.UploadAsync(item));
            var agent2 = Temp.Agent(Temp.Options(root, spool), new TooMuch(new SimulatorImportServer(), chunks: [], bytes: 9999));
            await Assert.ThrowsAsync<ImportConflictException>(() => agent2.UploadAsync(item));
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    private sealed class TooMuch(SimulatorImportServer inner, int[] chunks, long bytes) : Forwarding(inner)
    {
        public override async Task<IngestReceipt> BeginAsync(WireBeginIngest body, string key, CancellationToken ct)
        {
            var receipt = await base.BeginAsync(body, key, ct);
            receipt.ReceivedChunks = chunks;
            receipt.ReceivedBytes = bytes;
            return receipt;
        }
    }

    [Fact]
    public async Task Accepted_finalize_is_polled_until_stored_and_never_treated_as_stored_before()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            Temp.Write(Path.Combine(root, "note.dss"), "abcdefgh12345678");
            var agent = Temp.Agent(Temp.Options(root, spool), new AcceptedThenStored(new SimulatorImportServer()));
            var item = Assert.Single((await Temp.Stabilize(agent, Start)).Inventory);
            Assert.True((await agent.UploadAsync(item)).IsDurable);
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    private sealed class AcceptedThenStored(SimulatorImportServer inner) : Forwarding(inner)
    {
        public override async Task<IngestReceipt> FinalizeAsync(string id, WireFinalizeIngest body, string key, CancellationToken ct)
        {
            var receipt = await base.FinalizeAsync(id, body, key, ct);
            receipt.State = "FINALIZING";
            receipt.Durability = "RECEIVED";
            return receipt;
        }
    }

    [Fact]
    public async Task One_failing_intent_does_not_starve_the_next()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            Temp.Write(Path.Combine(root, "a.dss"), "aaaaaaaaaaaaaaaa");
            Temp.Write(Path.Combine(root, "b.dss"), "bbbbbbbbbbbbbbbb");
            var inner = new SimulatorImportServer();
            var failing = new FailFirstBegin(inner);
            var agent = Temp.Agent(Temp.Options(root, spool), failing);
            var items = (await Temp.Stabilize(agent, Start)).Inventory;
            Assert.Equal(2, items.Count);
            inner.Enqueue(Temp.Intent(items[0], "none"));
            inner.Enqueue(Temp.Intent(items[1], "none"));
            await Assert.ThrowsAsync<ImportServerException>(() => agent.RunOnceAsync(Start.AddSeconds(6)));
            Assert.Equal(1, inner.IngestCount);
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    private sealed class FailFirstBegin(SimulatorImportServer inner) : Forwarding(inner)
    {
        private bool _failed;

        public override Task<IngestReceipt> BeginAsync(WireBeginIngest body, string key, CancellationToken ct)
        {
            if (_failed)
                return base.BeginAsync(body, key, ct);
            _failed = true;
            throw new ImportServerException(503, "unavailable");
        }
    }

    [Fact]
    public async Task Chunk_count_stays_within_the_finalize_contract()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            Temp.Write(Path.Combine(root, "big.dss"), new string('x', 20001));
            var options = Temp.Options(root, spool);
            options.ChunkSizeBytes = 1;
            var server = new SimulatorImportServer();
            var agent = Temp.Agent(options, server);
            var item = Assert.Single((await Temp.Stabilize(agent, Start)).Inventory);
            await agent.UploadAsync(item);
            Assert.InRange(server.PutSequences.Count, 1, AgentOptions.MaxChunks);
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Fact]
    public async Task Long_names_within_1024_are_inventoried_and_log_lines_are_clean()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            Temp.Write(Path.Combine(root, new string('n', 130) + ".dss"), "abc");
            var log = new ListLog();
            var server = new SimulatorImportServer();
            var agent = Temp.Agent(Temp.Options(root, spool), server, log);
            var inventoried = await Temp.Stabilize(agent, Start);
            Assert.Single(inventoried.Inventory);
            Assert.True(ContractIds.IsId(inventoried.Inventory[0].SourceId));
            Assert.False(ContractIds.IsRelativePath(new string('n', 1025)));
            server.Enqueue(new ImportIntent { Kind = ImportIntentKind.ImportSnapshot, SourceId = "x\nFORGED line", SnapshotVersion = "v" });
            await agent.RunOnceAsync(Start.AddSeconds(6));
            Assert.DoesNotContain(log.Lines, line => line.Contains('\n', StringComparison.Ordinal));
            Assert.Contains(log.Lines, line => line.StartsWith("intent conflict x?FORGED", StringComparison.Ordinal));
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Fact]
    public void Idempotency_keys_are_rfc4122_version_5_text()
    {
        var text = Hashing.DeterministicGuid("a\nb\nc\nbegin").ToString();
        Assert.Equal('5', text[14]);
        Assert.Contains(text[19], "89ab");
    }

    [Fact]
    public void Dangling_links_trailing_dots_and_device_prefixes_are_not_admitted()
    {
        var root = Temp.Dir();
        var outside = Temp.Dir();
        try
        {
            File.CreateSymbolicLink(Path.Combine(root, "dangling.dss"), Path.Combine(outside, "not-yet.dss"));
            Assert.False(PathGuard.TryAdmit(root, Path.Combine(root, "dangling.dss"), out _));
            var dirLink = Path.Combine(root, "dir");
            Directory.CreateSymbolicLink(dirLink, outside);
            Assert.False(PathGuard.TryAdmit(root, Path.Combine(dirLink, "new.dss"), out _));
            Assert.False(PathGuard.TryAdmit(root, Path.Combine(root, "name."), out _));
            Assert.False(PathGuard.TryAdmit(root, Path.Combine(root, "name "), out _));
            Assert.False(PathGuard.TryAdmit(root, @"\\?\" + Path.Combine(root, "x.dss"), out _));
            Assert.True(PathGuard.TryAdmit(root, Path.Combine(root, "plain.dss"), out _));
        }
        finally
        {
            Temp.Delete(root, outside);
        }
    }

    [Fact]
    public void Transport_is_https_only_without_redirects_and_with_a_valid_certificate()
    {
        var options = Temp.Options("/tmp/x", Temp.Dir());
        options.ServerBaseUrl = "http://clinic.example.invalid";
        Assert.Throws<InvalidOperationException>(() => options.Validate());
        options.ServerBaseUrl = "https://clinic.example.invalid";
        options.Validate();

        using var rsa = RSA.Create(2048);
        var request = new CertificateRequest("CN=import-agent", rsa, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        using var expired = request.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-10), DateTimeOffset.UtcNow.AddDays(-1));
        var bad = new MachineCredential { AgentId = "agent-1", ClientCertificatePfx = expired.ExportPkcs12(Pkcs12ExportPbeParameters.Default, "pw"), PfxPassword = "pw" };
        Assert.Throws<InvalidOperationException>(() => HttpImportServer.CreateHandler(bad));

        using var valid = request.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddDays(2));
        var good = new MachineCredential { AgentId = "agent-1", ClientCertificatePfx = valid.ExportPkcs12(Pkcs12ExportPbeParameters.Default, "pw"), PfxPassword = "pw" };
        using var handler = (HttpClientHandler)HttpImportServer.CreateHandler(good);
        Assert.False(handler.AllowAutoRedirect);
        Assert.Equal(System.Security.Authentication.SslProtocols.Tls12 | System.Security.Authentication.SslProtocols.Tls13, handler.SslProtocols);
        Assert.Null(handler.ServerCertificateCustomValidationCallback);
    }
}
