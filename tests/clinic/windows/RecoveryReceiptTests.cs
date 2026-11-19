using System.Text;

namespace Speakr.Clinic.ImportAgent.ClinicTests;

public class RecoveryReceiptTests
{
    [Fact]
    public async Task Restart_after_spool_write_preserves_hash_and_single_logical_import()
    {
        using var folders = new ClinicFolders();
        var path = Path.Combine(folders.Source, "spooled.dss");
        const string content = "payload-spooled-prior-to-restart";
        FixtureSupport.WriteSynthetic(path, content);
        var server = new SimulatorImportServer();
        var options = FixtureSupport.Options(folders);

        // Agent 1 stabilizes and spools the file
        var agent1 = FixtureSupport.CreateAgent(options, server);
        var items1 = (await FixtureSupport.Stabilize(agent1, FixtureSupport.Start)).Inventory;
        var item1 = Assert.Single(items1);

        // Verify spool directory has the encrypted entry
        var spoolFiles = Directory.GetFiles(folders.Spool);
        Assert.Single(spoolFiles);

        // Simulate agent restart: instantiate Agent 2 with the same options and spool folder
        var agent2 = FixtureSupport.CreateAgent(options, server);
        var intent = FixtureSupport.SignIntent(item1, "none");

        // Agent 2 reads from the encrypted spool written by Agent 1 and completes upload
        var receipt = await agent2.UploadAsync(item1, intent);

        Assert.Equal(1, server.IngestCount);
        Assert.True(receipt.Binds(item1, receipt.IngestId));
        Assert.Equal(item1.ContentSha256, receipt.ContentSha256);
        Assert.Equal(Encoding.UTF8.GetBytes(content), server.ReadStoredBytes(receipt.IngestId));
    }

    [Fact]
    public async Task Restart_after_partial_upload_resumes_without_duplicate_import_and_preserves_hash()
    {
        using var folders = new ClinicFolders();
        var path = Path.Combine(folders.Source, "partial.dss");
        // Chunk size in FixtureSupport.Options is 8 bytes. Exactly 24 bytes => 3 chunks (sequences 0, 1, 2)
        const string content = "123456781234567812345678";
        FixtureSupport.WriteSynthetic(path, content);
        var server = new SimulatorImportServer { FailPutsRemaining = 1 };
        var options = FixtureSupport.Options(folders);

        var agent1 = FixtureSupport.CreateAgent(options, server);
        var items = (await FixtureSupport.Stabilize(agent1, FixtureSupport.Start)).Inventory;
        var item = Assert.Single(items);
        var intent = FixtureSupport.SignIntent(item, "none");

        // Upload with agent 1 fails after sequence 0
        await Assert.ThrowsAsync<IOException>(() => agent1.UploadAsync(item, intent));
        Assert.Equal([0], server.PutSequences);

        // Simulate restart with agent 2
        var agent2 = FixtureSupport.CreateAgent(options, server);
        var receipt = await agent2.UploadAsync(item, intent);

        // Resumed upload puts only remaining chunks (1, 2)
        Assert.Equal([0, 1, 2], server.PutSequences);
        Assert.Equal(1, server.IngestCount);
        Assert.True(receipt.Binds(item, receipt.IngestId));
        Assert.Equal(item.ContentSha256, receipt.ContentSha256);
        Assert.Equal(Encoding.UTF8.GetBytes(content), server.ReadStoredBytes(receipt.IngestId));
    }

    [Fact]
    public async Task Restart_after_durable_server_receipt_before_post_processing_asserts_single_import_and_preserves_hash()
    {
        using var folders = new ClinicFolders();
        var path = Path.Combine(folders.Source, "durable-ack.dss");
        const string content = "durable-ack-content-for-recovery";
        FixtureSupport.WriteSynthetic(path, content);
        var server = new SimulatorImportServer();
        var options = FixtureSupport.Options(folders, postImport: true);

        // Agent 1 stabilizes and uploads; durable receipt is returned
        var agent1 = FixtureSupport.CreateAgent(options, server);
        var item = Assert.Single((await FixtureSupport.Stabilize(agent1, FixtureSupport.Start)).Inventory);
        var intent = FixtureSupport.SignIntent(item, "delete");

        var receipt1 = await agent1.UploadAsync(item, intent);
        Assert.True(receipt1.IsDurable);
        Assert.Equal(1, server.IngestCount);
        // Crash before agent1.ApplyPost is called; original file still exists in source
        Assert.True(File.Exists(path));

        // Agent 2 restarts: reconnects to server and queries session using same idempotency key
        var agent2 = FixtureSupport.CreateAgent(options, server);
        var receipt2 = await agent2.UploadAsync(item, intent);

        // Verify session was not duplicated, same ingestId and preserved hash
        Assert.Equal(receipt1.IngestId, receipt2.IngestId);
        Assert.Equal(1, server.IngestCount);
        Assert.Equal(item.ContentSha256, receipt2.ContentSha256);
        Assert.Equal(Encoding.UTF8.GetBytes(content), server.ReadStoredBytes(receipt2.IngestId));

        // Agent 2 safely applies post-import deletion now that durable receipt is re-verified
        var outcome = agent2.ApplyPost(item, intent, receipt2);
        Assert.Equal(PostImportOutcome.Deleted, outcome);
        Assert.False(File.Exists(path));
    }

    [Fact]
    public async Task Post_import_removal_is_disabled_by_default_and_refused_for_unknown()
    {
        using var folders = new ClinicFolders();
        var path = Path.Combine(folders.Source, "default-safety.dss");
        const string content = "stay-when-disabled-or-unknown";
        FixtureSupport.WriteSynthetic(path, content);
        var server = new SimulatorImportServer();

        // Default options has PostImportEnabled = false
        var defaultOptions = FixtureSupport.Options(folders, postImport: false);
        Assert.False(defaultOptions.PostImportEnabled);

        var agent = FixtureSupport.CreateAgent(defaultOptions, server);
        var item = Assert.Single((await FixtureSupport.Stabilize(agent, FixtureSupport.Start)).Inventory);
        var intent = FixtureSupport.SignIntent(item, "delete");
        var receipt = await agent.UploadAsync(item, intent);

        // With post-import disabled by default, ApplyPost skips and preserves source
        var disabledOutcome = agent.ApplyPost(item, intent, receipt);
        Assert.Equal(PostImportOutcome.SkippedPolicyOff, disabledOutcome);
        Assert.True(File.Exists(path));

        // Now test with PostImportEnabled = true but receipt is UNKNOWN
        var enabledOptions = FixtureSupport.Options(folders, postImport: true);
        var post = new PostImport(enabledOptions, FixtureSupport.IntentKey);

        var unknownReceipt = receipt with { State = "UNKNOWN", Durability = "UNKNOWN" };
        Assert.False(unknownReceipt.IsDurable);
        Assert.False(unknownReceipt.Binds(item, unknownReceipt.IngestId));
        Assert.Throws<ReceiptRejectedException>(() => post.Apply(item, intent, unknownReceipt));
        Assert.True(File.Exists(path));

        // Also assert ReceiptBinder.Bind rejects UNKNOWN state
        var rawUnknownReceipt = new IngestReceipt
        {
            IngestId = receipt.IngestId,
            State = "UNKNOWN",
            Durability = "UNKNOWN",
            Size = item.Size,
            ReceivedBytes = item.Size,
            Sha256 = item.ContentSha256,
            SourceId = item.SourceId,
            SnapshotVersion = item.SnapshotVersion,
            IntentId = intent.IntentId,
            CorrelationId = Guid.NewGuid().ToString()
        };
        Assert.Throws<ReceiptRejectedException>(() => ReceiptBinder.Bind(item, intent, receipt.IngestId, rawUnknownReceipt));
        Assert.True(File.Exists(path));
    }

    [Fact]
    public async Task Post_import_removal_refused_for_mismatched_source_hash_or_intent()
    {
        using var folders = new ClinicFolders();
        var path = Path.Combine(folders.Source, "mismatch.dss");
        const string content = "safe-from-mismatches";
        FixtureSupport.WriteSynthetic(path, content);
        var server = new SimulatorImportServer();
        var options = FixtureSupport.Options(folders, postImport: true);
        var agent = FixtureSupport.CreateAgent(options, server);

        var item = Assert.Single((await FixtureSupport.Stabilize(agent, FixtureSupport.Start)).Inventory);
        var intent = FixtureSupport.SignIntent(item, "delete");
        var receipt = await agent.UploadAsync(item, intent);
        var post = new PostImport(options, FixtureSupport.IntentKey);

        // Mismatched source id
        var foreignSourceIntent = new ImportIntent
        {
            IntentId = Guid.NewGuid().ToString(),
            Kind = ImportIntentKind.ImportSnapshot,
            SourceId = "foreign-source-id",
            SnapshotVersion = item.SnapshotVersion,
            PostStep = "delete",
            AgentId = FixtureSupport.AgentId,
            ExpiresAt = "2099-01-01T00:00:00Z"
        };
        foreignSourceIntent.Signature = IntentBinding.Sign(foreignSourceIntent, FixtureSupport.AgentId, FixtureSupport.IntentKey);
        Assert.Equal(PostImportOutcome.Conflict, post.Apply(item, foreignSourceIntent, receipt));
        Assert.True(File.Exists(path));

        // Mismatched hash in receipt
        var badHashReceipt = receipt with { ContentSha256 = new string('0', 64) };
        Assert.Throws<ReceiptRejectedException>(() => post.Apply(item, intent, badHashReceipt));
        Assert.True(File.Exists(path));

        // Mismatched intent
        var differentIntent = FixtureSupport.SignIntent(item, "delete");
        var rawReceipt = new IngestReceipt
        {
            IngestId = receipt.IngestId,
            State = "STORED",
            Durability = "STORED",
            Size = item.Size,
            ReceivedBytes = item.Size,
            Sha256 = item.ContentSha256,
            SourceId = item.SourceId,
            SnapshotVersion = item.SnapshotVersion,
            IntentId = differentIntent.IntentId,
            CorrelationId = receipt.CorrelationId,
            PermittedPostStep = "delete",
            DeletionPermitted = true
        };
        Assert.Throws<ReceiptRejectedException>(() => ReceiptBinder.Bind(item, intent, receipt.IngestId, rawReceipt));
        Assert.True(File.Exists(path));
    }

    [Fact]
    public async Task Post_import_removal_refused_for_expired_policy_or_changed_source_version()
    {
        using var folders = new ClinicFolders();
        var path = Path.Combine(folders.Source, "expired-and-stale.dss");
        const string originalText = "initial-source-bytes";
        FixtureSupport.WriteSynthetic(path, originalText);
        var server = new SimulatorImportServer();
        var options = FixtureSupport.Options(folders, postImport: true);
        var agent = FixtureSupport.CreateAgent(options, server);

        var item = Assert.Single((await FixtureSupport.Stabilize(agent, FixtureSupport.Start)).Inventory);
        var intent = FixtureSupport.SignIntent(item, "delete");
        var receipt = await agent.UploadAsync(item, intent);
        var post = new PostImport(options, FixtureSupport.IntentKey);

        // 1. Expired intent policy
        var expiredIntent = new ImportIntent
        {
            IntentId = Guid.NewGuid().ToString(),
            Kind = ImportIntentKind.ImportSnapshot,
            SourceId = item.SourceId,
            SnapshotVersion = item.SnapshotVersion,
            PostStep = "delete",
            AgentId = FixtureSupport.AgentId,
            ExpiresAt = "2020-01-01T00:00:00Z"
        };
        expiredIntent.Signature = IntentBinding.Sign(expiredIntent, FixtureSupport.AgentId, FixtureSupport.IntentKey);
        Assert.False(IntentBinding.ExpiresInFuture(expiredIntent.ExpiresAt));
        Assert.Equal(IntentBindingState.Invalid, IntentBinding.Verify(expiredIntent, FixtureSupport.AgentId, FixtureSupport.IntentKey));
        Assert.Equal(PostImportOutcome.RefusedUnbound, post.Apply(item, expiredIntent, receipt));
        Assert.True(File.Exists(path));
        Assert.Equal(originalText, File.ReadAllText(path));

        // 2. Changed source version (X4-004: file or SD card content swapped before deletion)
        const string changedText = "swapped-source-bytes-do-not-delete";
        FixtureSupport.WriteSynthetic(path, changedText);
        var changedOutcome = post.Apply(item, intent, receipt);
        Assert.Equal(PostImportOutcome.Conflict, changedOutcome);
        Assert.True(File.Exists(path));
        Assert.Equal(changedText, File.ReadAllText(path));
    }

    [Fact]
    public async Task Post_import_move_backup_preserves_hash_and_removes_source()
    {
        using var folders = new ClinicFolders();
        var path = Path.Combine(folders.Source, "move-backup.dss");
        const string content = "backup-preserved-content";
        FixtureSupport.WriteSynthetic(path, content);
        var server = new SimulatorImportServer();
        var options = FixtureSupport.Options(folders, postImport: true);
        var agent = FixtureSupport.CreateAgent(options, server);

        var item = Assert.Single((await FixtureSupport.Stabilize(agent, FixtureSupport.Start)).Inventory);
        var intent = FixtureSupport.SignIntent(item, "move_backup");
        var receipt = await agent.UploadAsync(item, intent);
        var post = new PostImport(options, FixtureSupport.IntentKey);

        var outcome = post.Apply(item, intent, receipt);
        Assert.Equal(PostImportOutcome.Moved, outcome);
        Assert.False(File.Exists(path));

        var expectedBackupPath = Path.Combine(folders.Backup, item.RootId, item.RelativePath);
        Assert.True(File.Exists(expectedBackupPath));
        Assert.Equal(content, File.ReadAllText(expectedBackupPath));
    }

    [Fact]
    public void Disposable_synthetic_folders_distinguish_simulator_execution_from_real_philips_evidence()
    {
        string sourcePath, spoolPath, backupPath;
        using (var folders = new ClinicFolders())
        {
            sourcePath = folders.Source;
            spoolPath = folders.Spool;
            backupPath = folders.Backup;

            var audioPath = Path.Combine(folders.Source, "dictation.dss");
            FixtureSupport.WriteSynthetic(audioPath, "synthetic-audio-data");
            var sidecarPath = audioPath + ".simulator.json";
            File.WriteAllText(sidecarPath, "{\"simulator\": true, \"finished\": true, \"author_code\": \"AUTH99\"}");

            // Simulator mode: reads synthetic sidecar, flags evidence as fixture
            var simObservation = SidecarReader.Read(audioPath, simulatorMode: true);
            Assert.Equal("completed", simObservation.ObservedCompletion);
            Assert.Equal("fixture", simObservation.EvidenceLevel);
            Assert.Equal("simulator-sidecar", simObservation.ParserVersion);
            Assert.Equal("AUTH99", simObservation.ClaimedAuthorCode);

            // Production mode (default): ignores simulator sidecar, marks unverified/unknown
            var prodObservation = SidecarReader.Read(audioPath, simulatorMode: false);
            Assert.Equal("unknown", prodObservation.ObservedCompletion);
            Assert.Equal("unverified", prodObservation.EvidenceLevel);
            Assert.Null(prodObservation.ClaimedAuthorCode);
            Assert.Null(prodObservation.ParserVersion);
        }

        // Assert temporary folders were fully cleaned up on dispose
        Assert.False(Directory.Exists(sourcePath));
        Assert.False(Directory.Exists(spoolPath));
        Assert.False(Directory.Exists(backupPath));
    }
}
