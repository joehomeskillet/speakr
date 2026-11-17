namespace Speakr.Clinic.ImportAgent;

public sealed class MachineCredential
{
    public required string AgentId { get; init; }
    public byte[]? ClientCertificatePfx { get; init; }
    public string? PfxPassword { get; init; }

    public override string ToString() => "MachineCredential(" + AgentId + ")";
}

public interface ISecretStore
{
    MachineCredential GetMachineCredential();
    byte[] GetSpoolKey();
}

public sealed class MemorySecretStore : ISecretStore
{
    private readonly MachineCredential _credential;
    private readonly byte[] _spoolKey;

    public MemorySecretStore(MachineCredential credential, byte[] spoolKey)
    {
        if (spoolKey.Length != 32)
            throw new ArgumentException("Spool key must be 32 bytes.", nameof(spoolKey));
        _credential = credential;
        _spoolKey = spoolKey.ToArray();
    }

    public MachineCredential GetMachineCredential() => _credential;

    public byte[] GetSpoolKey() => _spoolKey.ToArray();
}

public sealed class RefusingSecretStore : ISecretStore
{
    public MachineCredential GetMachineCredential() =>
        throw new InvalidOperationException("OS secret store is not available.");

    public byte[] GetSpoolKey() =>
        throw new InvalidOperationException("OS secret store is not available.");
}
