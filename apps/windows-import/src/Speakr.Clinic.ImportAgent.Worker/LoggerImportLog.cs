using Microsoft.Extensions.Logging;
using Speakr.Clinic.ImportAgent;

namespace Speakr.Clinic.ImportAgent.Worker;

public sealed class LoggerImportLog(ILogger<ImportWorker> logger) : IImportLog
{
    public void Info(string message) => logger.LogInformation("{Message}", message);
}
