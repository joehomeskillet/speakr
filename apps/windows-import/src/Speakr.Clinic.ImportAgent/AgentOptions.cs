namespace Speakr.Clinic.ImportAgent;

public sealed class AgentOptions
{
    public const int MaxChunkBytes = 4 * 1024 * 1024;
    public const int MaxFileBytes = 134217728;

    public string AgentId { get; set; } = "";
    public string ServerBaseUrl { get; set; } = "";
    public string SecretFilePath { get; set; } = "";
    public string BackupFolder { get; set; } = "";
    public string SpoolDirectory { get; set; } = "";
    public long SpoolMaxBytes { get; set; } = 268435456;
    public bool PostImportEnabled { get; set; }
    public int ChunkSizeBytes { get; set; } = MaxChunkBytes;
    public int StabilitySamples { get; set; } = 3;
    public int StabilityIntervalSeconds { get; set; } = 2;
    public int PollIntervalSeconds { get; set; } = 15;
    public string Mode { get; set; } = "production";
    public List<AllowedRoot> AllowedRoots { get; set; } = [];

    public bool SimulatorMode => string.Equals(Mode, "simulator", StringComparison.Ordinal);

    public void Validate()
    {
        if (string.IsNullOrWhiteSpace(AgentId))
            throw new InvalidOperationException("AgentId is required.");
        if (ChunkSizeBytes < 1 || ChunkSizeBytes > MaxChunkBytes)
            throw new InvalidOperationException("ChunkSizeBytes is outside the ingest contract.");
        if (StabilitySamples < 1)
            throw new InvalidOperationException("StabilitySamples must be at least 1.");
        if (StabilityIntervalSeconds < 0)
            throw new InvalidOperationException("StabilityIntervalSeconds cannot be negative.");
        if (SpoolMaxBytes < 1)
            throw new InvalidOperationException("SpoolMaxBytes must be positive.");
        if (string.IsNullOrWhiteSpace(SpoolDirectory) || !Path.IsPathRooted(SpoolDirectory))
            throw new InvalidOperationException("SpoolDirectory must be an absolute path.");
        var ids = new HashSet<string>(StringComparer.Ordinal);
        foreach (var root in AllowedRoots)
        {
            if (string.IsNullOrWhiteSpace(root.Id) || string.IsNullOrWhiteSpace(root.Path))
                throw new InvalidOperationException("Each allowed root needs an id and a path.");
            if (!Path.IsPathRooted(root.Path))
                throw new InvalidOperationException("Allowed roots must be absolute paths.");
            if (!ids.Add(root.Id))
                throw new InvalidOperationException("Allowed root ids must be unique.");
            if (string.IsNullOrWhiteSpace(root.Pattern))
                root.Pattern = "*";
        }
        if (BackupFolder.Length > 0)
        {
            if (!Path.IsPathRooted(BackupFolder))
                throw new InvalidOperationException("BackupFolder must be an absolute path.");
            var backup = PathGuard.Normalize(BackupFolder);
            foreach (var root in AllowedRoots)
            {
                var rootFull = PathGuard.Normalize(root.Path);
                if (PathGuard.IsUnder(backup, rootFull) || PathGuard.IsUnder(rootFull, backup))
                    throw new InvalidOperationException("BackupFolder must not overlap an allowed root.");
            }
        }
    }
}

public sealed class AllowedRoot
{
    public string Id { get; set; } = "";
    public string Path { get; set; } = "";
    public bool Recursive { get; set; }
    public string Pattern { get; set; } = "*";
}
