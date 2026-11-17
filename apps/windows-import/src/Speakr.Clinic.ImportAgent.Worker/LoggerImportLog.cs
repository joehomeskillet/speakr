using System.Diagnostics;
using Microsoft.Extensions.Logging;
using Speakr.Clinic.ImportAgent;

namespace Speakr.Clinic.ImportAgent.Worker;

public sealed class LoggerImportLog(ILogger<ImportWorker> logger) : IImportLog
{
    public void Info(string message) => logger.LogInformation("{Message}", message);

    public void Error(string message)
    {
        logger.LogError("{Message}", message);
        WindowsEventLog.Write(message);
    }
}

static class WindowsEventLog
{
    // Source "Application" is registered by Windows. A missing custom source must not crash the cycle.
    public static bool Write(string message)
    {
        if (!OperatingSystem.IsWindows())
            return false;
        try
        {
            var text = "SpeakrClinicImportAgent " + message;
            if (text.Length > 30000)
                text = text[..30000];
            EventLog.WriteEntry("Application", text, EventLogEntryType.Error);
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    public static bool RecentContains(string marker)
    {
        if (!OperatingSystem.IsWindows() || marker.Length == 0)
            return false;
        try
        {
            using var log = new EventLog("Application");
            var count = log.Entries.Count;
            var start = Math.Max(0, count - 30);
            for (var i = count - 1; i >= start; i--)
            {
                if (log.Entries[i].Message.Contains(marker, StringComparison.Ordinal))
                    return true;
            }
            return false;
        }
        catch (Exception)
        {
            return false;
        }
    }
}
