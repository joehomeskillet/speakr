using System.Net;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Extensions.Options;
using Speakr.Clinic.ImportAgent.Worker;

namespace Speakr.Clinic.ImportAgent.Tests;

public class ContractAndFindingTests
{
    [Fact]
    public void Intent_signature_is_version_2_and_rejects_an_expired_stamp()
    {
        var intent = new ImportIntent
        {
            IntentId = "11111111-1111-1111-1111-111111111111",
            Kind = ImportIntentKind.ImportSnapshot,
            AgentId = "agent-1",
            SourceId = "root-a:note.dss",
            SnapshotVersion = "snap",
            PostStep = "none",
            ExpiresAt = "2099-01-01T00:00:00Z"
        };
        var lines = string.Join(
            '\n',
            "speakr-import-intent-v2",
            intent.IntentId,
            intent.Kind,
            "agent-1",
            intent.SourceId,
            intent.SnapshotVersion,
            intent.PostStep,
            intent.ExpiresAt);
        Assert.Equal(8, lines.Split('\n').Length);
        Assert.False(lines.EndsWith('\n'));
        var expected = Convert.ToHexString(HMACSHA256.HashData(Temp.IntentKey, Encoding.UTF8.GetBytes(lines))).ToLowerInvariant();
        intent.Signature = IntentBinding.Sign(intent, "agent-1", Temp.IntentKey);
        Assert.Equal(expected, intent.Signature);
        Assert.Equal(IntentBindingState.Valid, IntentBinding.Verify(intent, "agent-1", Temp.IntentKey));

        intent.ExpiresAt = "2000-01-01T00:00:00Z";
        intent.Signature = IntentBinding.Sign(intent, "agent-1", Temp.IntentKey);
        Assert.Equal(IntentBindingState.Invalid, IntentBinding.Verify(intent, "agent-1", Temp.IntentKey));
    }

    [Fact]
    public void Source_ids_use_a_colon_or_a_hash_and_paths_stop_at_1024()
    {
        Assert.Equal("root-a:note.dss", ContractIds.SourceId("root-a", "note.dss"));
        Assert.Equal("root-a:dir:note.dss", ContractIds.SourceId("root-a", "dir/note.dss"));
        Assert.Equal(64, ContractIds.SourceId("root-a", "my note.dss").Length);
        Assert.True(ContractIds.IsRelativePath(new string('n', 130) + ".dss"));
        Assert.False(ContractIds.IsRelativePath(new string('n', 1025)));
        Assert.False(ContractIds.IsRelativePath("a/../b"));
    }

    [Fact]
    public void Generated_short_names_are_only_the_all_caps_alias()
    {
        Assert.False(PathGuard.IsGeneratedShortName("report~1.dss"));
        Assert.False(PathGuard.IsGeneratedShortName("Report~1.dss"));
        Assert.True(PathGuard.IsGeneratedShortName("REPORT~1.DSS"));
        Assert.True(PathGuard.IsGeneratedShortName("PROGRA~1"));
        Assert.True(PathGuard.IsGeneratedShortName("FILE~1.TXT"));
        Assert.False(PathGuard.IsGeneratedShortName("LONGNAME~1.DSS"));
    }

    [Fact]
    public void Windows_admits_a_tilde_name_that_is_not_a_short_alias()
    {
        if (!OperatingSystem.IsWindows())
            return;
        var root = Temp.Dir();
        try
        {
            var kept = Path.Combine(root, "report~1.dss");
            File.WriteAllText(kept, "abc");
            Assert.True(PathGuard.TryAdmit(root, kept, out _));
            Assert.False(PathGuard.TryAdmit(root, Path.Combine(root, "PROGRA~1"), out _));
        }
        finally
        {
            Temp.Delete(root);
        }
    }

    [Fact]
    public async Task Problem_status_maps_to_conflict_expired_or_server_error()
    {
        var handler = new StatusHandler();
        using var client = new HttpClient(handler) { BaseAddress = new Uri("https://clinic.example.invalid") };
        var server = new HttpImportServer(client);
        var id = "11111111-1111-1111-1111-111111111111";
        handler.Status = 409;
        handler.Body = """{"code":"conflict","title":"busy"}""";
        await Assert.ThrowsAsync<ImportConflictException>(() => server.AbortAsync(id, CancellationToken.None));
        Assert.Equal("POST", handler.LastMethod);
        Assert.Equal("/api/clinic/v1/agent/ingests/" + id + "/abort", handler.LastPath);

        handler.Status = 410;
        handler.Body = """{"code":"expired"}""";
        var expired = await Assert.ThrowsAsync<ImportExpiredException>(() => server.GetAsync(id, CancellationToken.None));
        Assert.Contains("expired", expired.Message, StringComparison.Ordinal);

        handler.Status = 403;
        handler.Body = """{"code":"forbidden","detail":"nope"}""";
        var denied = await Assert.ThrowsAsync<ImportServerException>(() =>
            server.ReportOutcomeAsync(new AgentOutcome { IntentId = id, Outcome = "stored" }, CancellationToken.None));
        Assert.Equal(403, denied.Status);
        Assert.Contains("forbidden", denied.Message, StringComparison.Ordinal);
        Assert.IsNotType<ImportExpiredException>(denied);
    }

    [Fact]
    public async Task Skipped_delete_reports_stored_and_refused()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            Temp.Write(Path.Combine(root, "note.dss"), "abc");
            var server = new SimulatorImportServer();
            var agent = Temp.Agent(Temp.Options(root, spool), server);
            var start = DateTimeOffset.Parse("2026-01-02T00:00:00Z");
            var item = Assert.Single((await Temp.Stabilize(agent, start)).Inventory);
            server.Enqueue(Temp.Intent(item, "none"));
            await agent.RunOnceAsync(start.AddSeconds(6));
            Assert.Equal(["stored"], server.Outcomes.Select(o => o.Outcome).ToArray());

            var deleting = new SimulatorImportServer();
            var held = Temp.Agent(Temp.Options(root, spool), deleting);
            var again = Assert.Single((await Temp.Stabilize(held, start.AddSeconds(20))).Inventory);
            deleting.Enqueue(Temp.Intent(again, "delete"));
            var skipped = await held.RunOnceAsync(start.AddSeconds(26));
            Assert.Equal(PostImportOutcome.SkippedPolicyOff, Assert.Single(skipped.Post));
            Assert.Equal(["stored", "refused"], deleting.Outcomes.Select(o => o.Outcome).ToArray());
            Assert.Equal("abc", File.ReadAllText(Path.Combine(root, "note.dss")));
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Fact]
    public async Task Cycle_failure_is_logged_as_an_error()
    {
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            var options = Temp.Options(root, spool);
            options.PollIntervalSeconds = 1;
            options.StabilitySamples = 1;
            options.StabilityIntervalSeconds = 0;
            var log = new ListLog();
            var worker = new ImportWorker(Temp.Agent(options, new ThrowingInventory(), log), new FixedOptions(options), log);
            await worker.StartAsync(CancellationToken.None);
            var deadline = DateTime.UtcNow.AddSeconds(5);
            while (log.Errors.Count == 0 && DateTime.UtcNow < deadline)
                await Task.Delay(20);
            var error = Assert.Single(log.Errors);
            Assert.StartsWith("cycle failed IOException", error, StringComparison.Ordinal);
            await worker.StopAsync(CancellationToken.None);
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Fact]
    public void Cycle_error_is_offered_to_the_windows_event_log()
    {
        var marker = "cycle-marker-" + Guid.NewGuid().ToString("N");
        var wrote = WindowsEventLog.Write(marker);
        if (!OperatingSystem.IsWindows())
        {
            Assert.False(wrote);
            return;
        }

        if (!wrote)
            return;
        Assert.True(WindowsEventLog.RecentContains(marker));
    }

    [Fact]
    public void Broad_secret_acl_is_a_clean_error()
    {
        if (!OperatingSystem.IsWindows())
            return;
        var dir = Temp.Dir();
        try
        {
            var path = Path.Combine(dir, "secret.bin");
            File.WriteAllBytes(path, [1, 2, 3, 4]);
            DpapiFileSecretStore.GrantEveryoneRead(path);
            var ex = Assert.Throws<SecretStoreException>(() => new DpapiFileSecretStore(path).GetMachineCredential());
            Assert.Contains("too broad", ex.Message, StringComparison.OrdinalIgnoreCase);
            Assert.Contains("Everyone", ex.Message, StringComparison.Ordinal);
            Assert.DoesNotContain(path, ex.Message, StringComparison.Ordinal);
            Assert.DoesNotContain("secret.bin", ex.Message, StringComparison.Ordinal);
        }
        finally
        {
            Temp.Delete(dir);
        }
    }

    [Fact]
    public void Missing_or_unreadable_secret_is_a_clean_error()
    {
        if (!OperatingSystem.IsWindows())
            return;
        var dir = Temp.Dir();
        try
        {
            var missing = Path.Combine(dir, "missing.bin");
            var absent = Assert.Throws<SecretStoreException>(() => new DpapiFileSecretStore(missing).GetMachineCredential());
            Assert.Equal("Secret file was not found.", absent.Message);

            var path = Path.Combine(dir, "garbage.bin");
            File.WriteAllBytes(path, [9, 8, 7, 6]);
            var ex = Assert.Throws<SecretStoreException>(() => new DpapiFileSecretStore(path).GetSpoolKey());
            Assert.DoesNotContain(path, ex.Message, StringComparison.Ordinal);
            Assert.Contains(ex.Message, new[]
            {
                "Secret file could not be unprotected.",
                "Secret file could not be read.",
                "Secret file is incomplete.",
                "Secret file ACL is too broad. Remove read access for Everyone, Users and Authenticated Users.",
                "Secret file ACL could not be verified. Refusing to open it."
            });
        }
        finally
        {
            Temp.Delete(dir);
        }
    }

    private sealed class FixedOptions(AgentOptions value) : IOptions<AgentOptions>
    {
        public AgentOptions Value { get; } = value;
    }

    private sealed class ThrowingInventory : IImportServer
    {
        public Task ReportInventoryAsync(InventoryReport report, CancellationToken cancellationToken) =>
            throw new IOException("disk");

        public Task<IReadOnlyList<ImportIntent>> PollIntentsAsync(string agentId, CancellationToken cancellationToken) =>
            Task.FromResult<IReadOnlyList<ImportIntent>>([]);

        public Task<IngestReceipt> BeginAsync(WireBeginIngest body, string idempotencyKey, CancellationToken cancellationToken) =>
            throw new InvalidOperationException();

        public Task<IngestReceipt> PutChunkAsync(string ingestId, int sequence, long offset, ReadOnlyMemory<byte> body, string sha256, CancellationToken cancellationToken) =>
            throw new InvalidOperationException();

        public Task<IngestReceipt> FinalizeAsync(string ingestId, WireFinalizeIngest body, string idempotencyKey, CancellationToken cancellationToken) =>
            throw new InvalidOperationException();

        public Task<IngestReceipt> GetAsync(string ingestId, CancellationToken cancellationToken) =>
            throw new InvalidOperationException();

        public Task<IngestReceipt> AbortAsync(string ingestId, CancellationToken cancellationToken) =>
            throw new InvalidOperationException();

        public Task ReportOutcomeAsync(AgentOutcome outcome, CancellationToken cancellationToken) =>
            Task.CompletedTask;
    }

    private sealed class StatusHandler : HttpMessageHandler
    {
        public int Status { get; set; }
        public string Body { get; set; } = "";
        public string LastMethod { get; private set; } = "";
        public string LastPath { get; private set; } = "";

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            LastMethod = request.Method.Method;
            LastPath = request.RequestUri!.AbsolutePath;
            return Task.FromResult(new HttpResponseMessage((HttpStatusCode)Status)
            {
                Content = new StringContent(Body, Encoding.UTF8, "application/problem+json")
            });
        }
    }
}
