namespace Speakr.Clinic.ImportAgent;

public sealed class AgentOptions
{
    public const int MaxChunkBytes = 4 * 1024 * 1024;
    public const int MaxFileBytes = 134217728;
    public const int DefaultBackupRetentionDays = 30;
    public const int DefaultBackupRetentionMaxBatch = 100;
    public const int MaxRetentionBatch = 10000;

    public string AgentId { get; set; } = "";
    public string ServerBaseUrl { get; set; } = "";
    public string SecretFilePath { get; set; } = "";
    public string BackupFolder { get; set; } = "";
    public string SpoolDirectory { get; set; } = "";
    public long SpoolMaxBytes { get; set; } = 268435456;
    public bool PostImportEnabled { get; set; }
    public bool BackupRetentionEnabled { get; set; }
    public int BackupRetentionDays { get; set; }
    public int BackupRetentionMaxBatch { get; set; } = DefaultBackupRetentionMaxBatch;
    public int ChunkSizeBytes { get; set; } = MaxChunkBytes;
    public int StabilitySamples { get; set; } = 3;
    public int StabilityIntervalSeconds { get; set; } = 2;
    public int PollIntervalSeconds { get; set; } = 15;
    public int FinalizePollAttempts { get; set; } = 3;
    public int FinalizePollDelayMs { get; set; } = 500;
    public int HttpTimeoutSeconds { get; set; } = 300;
    public string Mode { get; set; } = "production";
    public List<AllowedRoot> AllowedRoots { get; set; } = [];

    public const int MaxSourceFieldChars = 128;
    public const int MaxChunks = 10000;

    public bool IsBackupRetentionActive =>
        (BackupRetentionEnabled || BackupRetentionDays > 0)
        && EffectiveBackupRetentionDays > 0
        && BackupFolder.Length > 0;

    public int EffectiveBackupRetentionDays =>
        BackupRetentionDays > 0 ? BackupRetentionDays : (BackupRetentionEnabled ? DefaultBackupRetentionDays : 0);

    // Server base URL must be absolute https: no cleartext transfer of dictation originals.
    public Uri RequireHttpsServer()
    {
        if (!Uri.TryCreate(ServerBaseUrl, UriKind.Absolute, out var uri) || uri.Scheme != Uri.UriSchemeHttps)
            throw new InvalidOperationException("ServerBaseUrl must be an absolute https URL.");
        return uri;
    }

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
        if (FinalizePollAttempts < 0 || FinalizePollDelayMs < 0 || HttpTimeoutSeconds < 1)
            throw new InvalidOperationException("Finalize polling and HTTP timeout values are out of range.");
        if (BackupRetentionDays < 0)
            throw new InvalidOperationException("BackupRetentionDays cannot be negative.");
        if (BackupRetentionMaxBatch < 0 || BackupRetentionMaxBatch > MaxRetentionBatch)
            throw new InvalidOperationException("BackupRetentionMaxBatch is outside the allowed range.");
        if ((BackupRetentionEnabled || BackupRetentionDays > 0) && BackupFolder.Length == 0)
            throw new InvalidOperationException("BackupFolder must be configured when backup retention is enabled.");
        if (ServerBaseUrl.Length > 0)
            RequireHttpsServer();
        var ids = new HashSet<string>(StringComparer.Ordinal);
        foreach (var root in AllowedRoots)
        {
            if (string.IsNullOrWhiteSpace(root.Id) || string.IsNullOrWhiteSpace(root.Path))
                throw new InvalidOperationException("Each allowed root needs an id and a path.");
            if (!ContractIds.IsId(root.Id))
                throw new InvalidOperationException("Allowed root id does not match the agent contract.");
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
