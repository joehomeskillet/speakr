using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Options;
using Speakr.Clinic.ImportAgent;

namespace Speakr.Clinic.ImportAgent.Worker;

public sealed class ImportWorker : BackgroundService
{
    private readonly ImportOrchestrator _orchestrator;
    private readonly AgentOptions _options;
    private readonly IImportLog _log;

    public ImportWorker(ImportOrchestrator orchestrator, IOptions<AgentOptions> options, IImportLog log)
    {
        _orchestrator = orchestrator;
        _options = options.Value;
        _log = log;
    }

    public ImportWorker(ImportOrchestrator orchestrator, AgentOptions options, IImportLog log)
        : this(orchestrator, Options.Create(options), log)
    {
    }

    public async Task<RunResult?> RunPassAsync(DateTimeOffset now, CancellationToken cancellationToken = default)
    {
        var gap = TimeSpan.FromSeconds(Math.Max(0, _options.StabilityIntervalSeconds));
        var samples = Math.Max(1, _options.StabilitySamples);
        RunResult? lastResult = null;
        for (var sample = 0; sample < samples; sample++)
        {
            if (sample > 0 && gap > TimeSpan.Zero)
                await Task.Delay(gap, cancellationToken);
            lastResult = await _orchestrator.RunOnceAsync(now, cancellationToken);
        }

        try
        {
            MaintainBackup(now);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception ex)
        {
            _log.Error("retention failed " + ex.GetType().Name + " " + LogText.Clean(ex.Message));
        }

        return lastResult;
    }

    public int MaintainBackup(DateTimeOffset now)
    {
        if (!_options.IsBackupRetentionActive)
            return 0;
        if (string.IsNullOrWhiteSpace(_options.BackupFolder) || !Directory.Exists(_options.BackupFolder))
            return 0;

        var days = _options.EffectiveBackupRetentionDays;
        if (days <= 0)
            return 0;

        var cutoff = now - TimeSpan.FromDays(days);
        string rootFull;
        try
        {
            rootFull = PathGuard.Normalize(_options.BackupFolder);
        }
        catch (Exception ex)
        {
            _log.Error("retention failed " + ex.GetType().Name + " " + LogText.Clean(ex.Message));
            return 0;
        }

        IEnumerable<string> files;
        try
        {
            files = Directory.EnumerateFiles(rootFull, "*", SearchOption.AllDirectories);
        }
        catch (Exception ex)
        {
            _log.Error("retention failed " + ex.GetType().Name + " " + LogText.Clean(ex.Message));
            return 0;
        }

        var candidates = new List<(string Path, DateTimeOffset Mtime)>();
        foreach (var file in files)
        {
            if (file.EndsWith(".part", StringComparison.Ordinal) || file.Contains(".import-hold-", StringComparison.Ordinal))
                continue;

            if (!PathGuard.TryAdmit(rootFull, file, out var safePath))
            {
                _log.Error("retention failed SecurityException unsafe backup path " + LogText.Clean(file));
                continue;
            }

            DateTimeOffset mtime;
            try
            {
                var info = new FileInfo(safePath);
                mtime = new DateTimeOffset(info.LastWriteTimeUtc, TimeSpan.Zero);
            }
            catch (Exception ex)
            {
                _log.Error("retention failed " + ex.GetType().Name + " " + LogText.Clean(ex.Message));
                continue;
            }

            if (mtime > now)
                continue;

            if (mtime <= cutoff)
                candidates.Add((safePath, mtime));
        }

        candidates.Sort((a, b) =>
        {
            var cmp = a.Mtime.CompareTo(b.Mtime);
            return cmp != 0 ? cmp : string.Compare(a.Path, b.Path, StringComparison.Ordinal);
        });

        var maxBatch = _options.BackupRetentionMaxBatch > 0
            ? _options.BackupRetentionMaxBatch
            : AgentOptions.DefaultBackupRetentionMaxBatch;

        var toDelete = candidates.Take(maxBatch).ToList();
        var deleted = 0;

        foreach (var item in toDelete)
        {
            try
            {
                File.Delete(item.Path);
                deleted++;
                _log.Info("retention deleted " + LogText.Clean(Path.GetRelativePath(rootFull, item.Path)));
            }
            catch (Exception ex)
            {
                _log.Error("retention failed " + ex.GetType().Name + " " + LogText.Clean(ex.Message));
            }
        }

        return deleted;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var poll = TimeSpan.FromSeconds(Math.Max(1, _options.PollIntervalSeconds));
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await RunPassAsync(DateTimeOffset.UtcNow, stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                _log.Error("cycle failed " + ex.GetType().Name + " " + LogText.Clean(ex.Message));
            }

            try
            {
                await Task.Delay(poll, stoppingToken);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
    }
}
