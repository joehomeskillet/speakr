namespace Speakr.Clinic.ImportAgent.Tests;

static class Temp
{
    public static string Dir() => Directory.CreateTempSubdirectory("clinic-import-").FullName;

    public static byte[] Key()
    {
        var key = new byte[32];
        key[0] = 4;
        return key;
    }

    public static byte[] IntentKey { get; } = Enumerable.Range(1, 32).Select(i => (byte)i).ToArray();

    public static AgentOptions Options(
        string root,
        string spool,
        bool post = false,
        string mode = "production",
        string? backup = null,
        bool recursive = false,
        string pattern = "*",
        long spoolMax = 268435456,
        string rootId = "root-a")
    {
        return new AgentOptions
        {
            AgentId = "agent-1",
            SpoolDirectory = spool,
            PostImportEnabled = post,
            ChunkSizeBytes = 8,
            StabilitySamples = 3,
            StabilityIntervalSeconds = 2,
            PollIntervalSeconds = 15,
            FinalizePollDelayMs = 0,
            Mode = mode,
            BackupFolder = backup ?? "",
            SpoolMaxBytes = spoolMax,
            AllowedRoots =
            [
                new AllowedRoot { Id = rootId, Path = root, Recursive = recursive, Pattern = pattern }
            ]
        };
    }

    public static void Write(string path, string text)
    {
        var parent = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(parent))
            Directory.CreateDirectory(parent);
        File.WriteAllText(path, text);
        File.SetLastWriteTimeUtc(path, new DateTime(2026, 1, 2, 3, 4, 5, DateTimeKind.Utc));
    }

    public static ImportOrchestrator Agent(AgentOptions options, IImportServer server, IImportLog? log = null) =>
        ImportOrchestrator.Create(options, server, Key(), log, IntentKey);

    public static async Task<RunResult> Stabilize(ImportOrchestrator agent, DateTimeOffset start)
    {
        await agent.RunOnceAsync(start);
        await agent.RunOnceAsync(start.AddSeconds(2));
        return await agent.RunOnceAsync(start.AddSeconds(4));
    }

    public static ImportIntent Intent(InventoryItem item, string step)
    {
        var intent = new ImportIntent
        {
            IntentId = Guid.NewGuid().ToString(),
            Kind = ImportIntentKind.ImportSnapshot,
            SourceId = item.SourceId,
            SnapshotVersion = item.SnapshotVersion,
            PostStep = step,
            AgentId = "agent-1"
        };
        intent.Signature = IntentBinding.Sign(intent, "agent-1", IntentKey);
        return intent;
    }

    public static void Delete(params string[] paths)
    {
        foreach (var path in paths)
            Directory.Delete(path, true);
    }
}

sealed class ListLog : IImportLog
{
    public List<string> Lines { get; } = [];

    public void Info(string message) => Lines.Add(message);
}
