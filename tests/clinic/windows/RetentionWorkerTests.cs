using System.Text;
using Microsoft.Extensions.Options;
using Speakr.Clinic.ImportAgent.Worker;

namespace Speakr.Clinic.ImportAgent.ClinicTests;

public sealed class TestImportLog : IImportLog
{
    public List<string> Infos { get; } = [];
    public List<string> Errors { get; } = [];

    public void Info(string message) => Infos.Add(message);

    public void Error(string message) => Errors.Add(message);
}

public class RetentionWorkerTests
{
    [Fact]
    public void Default_configuration_preserves_backup_copies_and_post_import_stays_off()
    {
        var options = new AgentOptions();
        Assert.False(options.PostImportEnabled);
        Assert.False(options.BackupRetentionEnabled);
        Assert.Equal(0, options.BackupRetentionDays);
        Assert.Equal(AgentOptions.DefaultBackupRetentionMaxBatch, options.BackupRetentionMaxBatch);
        Assert.False(options.IsBackupRetentionActive);

        using var folders = new ClinicFolders();
        var backupFile = Path.Combine(folders.Backup, "preserve-me.dss");
        FixtureSupport.WriteSynthetic(backupFile, "backup-payload");
        File.SetLastWriteTimeUtc(backupFile, DateTime.UtcNow.AddDays(-100));

        var testOptions = FixtureSupport.Options(folders);
        var log = new TestImportLog();
        var server = new SimulatorImportServer();
        var orchestrator = FixtureSupport.CreateAgent(testOptions, server, log);
        var worker = new ImportWorker(orchestrator, testOptions, log);

        var deleted = worker.MaintainBackup(DateTimeOffset.UtcNow);
        Assert.Equal(0, deleted);
        Assert.True(File.Exists(backupFile));
        Assert.Equal("backup-payload", File.ReadAllText(backupFile));
    }

    [Fact]
    public void Positive_case_expired_backup_copies_are_cleaned_up_while_younger_stay()
    {
        using var folders = new ClinicFolders();
        var now = FixtureSupport.Start;
        var expired1 = Path.Combine(folders.Backup, "root-a", "expired1.dss");
        var expired2 = Path.Combine(folders.Backup, "root-a", "boundary.dss");
        var fresh = Path.Combine(folders.Backup, "root-a", "fresh.dss");

        FixtureSupport.WriteSynthetic(expired1, "expired-1");
        File.SetLastWriteTimeUtc(expired1, now.AddDays(-35).UtcDateTime);

        FixtureSupport.WriteSynthetic(expired2, "expired-2");
        File.SetLastWriteTimeUtc(expired2, now.AddDays(-30).UtcDateTime);

        FixtureSupport.WriteSynthetic(fresh, "fresh-data");
        File.SetLastWriteTimeUtc(fresh, now.AddDays(-5).UtcDateTime);

        var testOptions = FixtureSupport.Options(folders, postImport: true);
        testOptions.BackupRetentionEnabled = true;
        testOptions.BackupRetentionDays = 30;
        testOptions.Validate();

        var log = new TestImportLog();
        var server = new SimulatorImportServer();
        var orchestrator = FixtureSupport.CreateAgent(testOptions, server, log);
        var worker = new ImportWorker(orchestrator, Options.Create(testOptions), log);

        var deleted = worker.MaintainBackup(now);
        Assert.Equal(2, deleted);
        Assert.False(File.Exists(expired1));
        Assert.False(File.Exists(expired2));
        Assert.True(File.Exists(fresh));
        Assert.Equal("fresh-data", File.ReadAllText(fresh));
        Assert.Equal(2, log.Infos.Count(msg => msg.StartsWith("retention deleted", StringComparison.Ordinal)));
    }

    [Fact]
    public void Bounded_maintenance_respects_max_batch_and_deletes_oldest_first()
    {
        using var folders = new ClinicFolders();
        var now = FixtureSupport.Start;
        var file1 = Path.Combine(folders.Backup, "batch-1.dss");
        var file2 = Path.Combine(folders.Backup, "batch-2.dss");
        var file3 = Path.Combine(folders.Backup, "batch-3.dss");
        var file4 = Path.Combine(folders.Backup, "batch-4.dss");

        FixtureSupport.WriteSynthetic(file1, "file-1");
        File.SetLastWriteTimeUtc(file1, now.AddDays(-50).UtcDateTime);

        FixtureSupport.WriteSynthetic(file2, "file-2");
        File.SetLastWriteTimeUtc(file2, now.AddDays(-40).UtcDateTime);

        FixtureSupport.WriteSynthetic(file3, "file-3");
        File.SetLastWriteTimeUtc(file3, now.AddDays(-30).UtcDateTime);

        FixtureSupport.WriteSynthetic(file4, "file-4");
        File.SetLastWriteTimeUtc(file4, now.AddDays(-20).UtcDateTime);

        var testOptions = FixtureSupport.Options(folders, postImport: true);
        testOptions.BackupRetentionDays = 14;
        testOptions.BackupRetentionMaxBatch = 2;
        testOptions.Validate();

        var log = new TestImportLog();
        var server = new SimulatorImportServer();
        var orchestrator = FixtureSupport.CreateAgent(testOptions, server, log);
        var worker = new ImportWorker(orchestrator, testOptions, log);

        // Pass 1: deletes the 2 oldest files (file1: -50d, file2: -40d)
        var pass1 = worker.MaintainBackup(now);
        Assert.Equal(2, pass1);
        Assert.False(File.Exists(file1));
        Assert.False(File.Exists(file2));
        Assert.True(File.Exists(file3));
        Assert.True(File.Exists(file4));

        // Pass 2: deletes next 2 oldest (file3: -30d, file4: -20d)
        var pass2 = worker.MaintainBackup(now);
        Assert.Equal(2, pass2);
        Assert.False(File.Exists(file3));
        Assert.False(File.Exists(file4));
    }

    [Fact]
    public void Future_timestamp_and_in_progress_files_are_preserved()
    {
        using var folders = new ClinicFolders();
        var now = FixtureSupport.Start;
        var futureFile = Path.Combine(folders.Backup, "future.dss");
        var partFile = Path.Combine(folders.Backup, "temp.dss.part");
        var holdFile = Path.Combine(folders.Backup, "hold.dss.import-hold-1234");

        FixtureSupport.WriteSynthetic(futureFile, "future-clock");
        File.SetLastWriteTimeUtc(futureFile, now.AddDays(5).UtcDateTime);

        FixtureSupport.WriteSynthetic(partFile, "in-flight-part");
        File.SetLastWriteTimeUtc(partFile, now.AddDays(-40).UtcDateTime);

        FixtureSupport.WriteSynthetic(holdFile, "hold-marker");
        File.SetLastWriteTimeUtc(holdFile, now.AddDays(-40).UtcDateTime);

        var testOptions = FixtureSupport.Options(folders, postImport: true);
        testOptions.BackupRetentionDays = 14;
        testOptions.Validate();

        var log = new TestImportLog();
        var server = new SimulatorImportServer();
        var orchestrator = FixtureSupport.CreateAgent(testOptions, server, log);
        var worker = new ImportWorker(orchestrator, testOptions, log);

        var deleted = worker.MaintainBackup(now);
        Assert.Equal(0, deleted);
        Assert.True(File.Exists(futureFile));
        Assert.True(File.Exists(partFile));
        Assert.True(File.Exists(holdFile));
    }

    [Fact]
    public async Task Retention_error_is_logged_separately_and_does_not_acknowledge_uncommitted_import_or_block_uploads()
    {
        using var folders = new ClinicFolders();
        var now = FixtureSupport.Start;

        // Source has note.dss
        var sourcePath = Path.Combine(folders.Source, "note.dss");
        FixtureSupport.WriteSynthetic(sourcePath, "clinical-content");

        // Backup has an expired file inside an unreadable subdirectory to force a retention error
        var lockedSubdir = Path.Combine(folders.Backup, "locked-sub");
        Directory.CreateDirectory(lockedSubdir);
        var lockedFile = Path.Combine(lockedSubdir, "expired.dss");
        FixtureSupport.WriteSynthetic(lockedFile, "payload");
        File.SetLastWriteTimeUtc(lockedFile, now.AddDays(-40).UtcDateTime);

        if (OperatingSystem.IsLinux() || OperatingSystem.IsMacOS())
        {
            File.SetUnixFileMode(lockedSubdir, UnixFileMode.None);
        }

        try
        {
            var testOptions = FixtureSupport.Options(folders, postImport: true);
            testOptions.BackupRetentionDays = 14;
            testOptions.Validate();

            var log = new TestImportLog();
            var server = new SimulatorImportServer();
            var orchestrator = FixtureSupport.CreateAgent(testOptions, server, log);
            var worker = new ImportWorker(orchestrator, testOptions, log);

            // Stabilize inventory
            await FixtureSupport.Stabilize(orchestrator, now);

            // Pass 1: RunPassAsync runs without intent -> uncommitted import remains uncommitted!
            var result = await worker.RunPassAsync(now.AddSeconds(6));
            Assert.NotNull(result);
            Assert.Empty(result.Receipts);

            // Assert retention error logged separately, NOT cycle failed
            if (OperatingSystem.IsLinux() || OperatingSystem.IsMacOS())
            {
                Assert.Contains(log.Errors, err => err.StartsWith("retention failed", StringComparison.Ordinal));
                Assert.DoesNotContain(log.Errors, err => err.StartsWith("cycle failed", StringComparison.Ordinal));
            }

            // Server has 0 ingests; uncommitted import was not acknowledged
            Assert.Equal(0, server.IngestCount);
            Assert.True(File.Exists(sourcePath));

            // Restore directory permissions for pass 2
            if (OperatingSystem.IsLinux() || OperatingSystem.IsMacOS())
            {
                File.SetUnixFileMode(lockedSubdir, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
            }

            // Pass 2: Enqueue valid intent for the source file -> upload succeeds independently!
            var item = Assert.Single(result.Inventory);
            server.Enqueue(FixtureSupport.SignIntent(item, "none"));

            var pass2 = await worker.RunPassAsync(now.AddSeconds(12));
            Assert.NotNull(pass2);
            var receipt = Assert.Single(pass2.Receipts);
            Assert.Equal(1, server.IngestCount);
            Assert.True(receipt.Binds(item, receipt.IngestId));
            Assert.Equal(Encoding.UTF8.GetBytes("clinical-content"), server.ReadStoredBytes(receipt.IngestId));
        }
        finally
        {
            if (OperatingSystem.IsLinux() || OperatingSystem.IsMacOS())
            {
                try
                {
                    File.SetUnixFileMode(lockedSubdir, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
                }
                catch
                {
                }
            }
        }
    }

    [Fact]
    public void Unauthorized_or_traversal_paths_cannot_escape_backup_folder()
    {
        using var folders = new ClinicFolders();
        var outside = Path.Combine(Path.GetTempPath(), "clinic-outside-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(outside);
        try
        {
            var outsideFile = Path.Combine(outside, "secret.txt");
            File.WriteAllText(outsideFile, "secret");

            var testOptions = FixtureSupport.Options(folders, postImport: true);
            testOptions.BackupRetentionDays = 14;

            // Validates that BackupFolder overlapping an allowed root is rejected
            var invalidOptions = FixtureSupport.Options(folders);
            invalidOptions.BackupFolder = folders.Source;
            Assert.Throws<InvalidOperationException>(() => invalidOptions.Validate());

            // PathGuard rejects paths escaping BackupFolder
            Assert.False(PathGuard.TryAdmit(folders.Backup, outsideFile, out _));
            Assert.False(PathGuard.TryAdmit(folders.Backup, Path.Combine(folders.Backup, "..", "escape.txt"), out _));
        }
        finally
        {
            if (Directory.Exists(outside))
                Directory.Delete(outside, true);
        }
    }

    [Fact]
    public void Options_validation_enforces_bounds()
    {
        using var folders = new ClinicFolders();

        var opt1 = FixtureSupport.Options(folders);
        opt1.BackupRetentionDays = -1;
        Assert.Throws<InvalidOperationException>(() => opt1.Validate());

        var opt2 = FixtureSupport.Options(folders);
        opt2.BackupRetentionMaxBatch = -1;
        Assert.Throws<InvalidOperationException>(() => opt2.Validate());

        var opt3 = FixtureSupport.Options(folders);
        opt3.BackupRetentionMaxBatch = 10001;
        Assert.Throws<InvalidOperationException>(() => opt3.Validate());

        var opt4 = FixtureSupport.Options(folders);
        opt4.BackupFolder = "";
        opt4.BackupRetentionEnabled = true;
        Assert.Throws<InvalidOperationException>(() => opt4.Validate());

        var opt5 = FixtureSupport.Options(folders);
        opt5.BackupFolder = "";
        opt5.BackupRetentionDays = 10;
        Assert.Throws<InvalidOperationException>(() => opt5.Validate());
    }
}
