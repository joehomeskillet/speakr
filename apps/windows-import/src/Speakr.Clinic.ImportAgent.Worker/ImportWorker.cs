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

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var gap = TimeSpan.FromSeconds(Math.Max(0, _options.StabilityIntervalSeconds));
        var poll = TimeSpan.FromSeconds(Math.Max(1, _options.PollIntervalSeconds));
        var samples = Math.Max(1, _options.StabilitySamples);
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                for (var sample = 0; sample < samples; sample++)
                {
                    if (sample > 0 && gap > TimeSpan.Zero)
                        await Task.Delay(gap, stoppingToken);
                    await _orchestrator.RunOnceAsync(DateTimeOffset.UtcNow, stoppingToken);
                }
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
