using System.Text.Json;

namespace Speakr.Clinic.ImportAgent.Worker;

internal sealed record ApprovedServer(string ServerBaseUrl, string AgentId);

internal sealed class ServerAddressSettings(string installDirectory)
{
    private readonly string _path = Path.Combine(installDirectory, "import-server.json");

    public ApprovedServer? Read()
    {
        try
        {
            using var input = File.OpenRead(_path);
            var approval = JsonSerializer.Deserialize<ApprovedServer>(input)
                ?? throw new InvalidDataException("Die gespeicherte Serveradresse ist leer.");
            if (approval.ServerBaseUrl is null || approval.AgentId is null)
                throw new InvalidDataException("Die lokale Zielbestätigung ist unvollständig.");
            return approval with { ServerBaseUrl = AgentOptions.ParseServerAddress(approval.ServerBaseUrl).AbsoluteUri };
        }
        catch (FileNotFoundException)
        {
            return null;
        }
    }

    public string ForWorker(AgentOptions options)
    {
        var approval = Read();
        if (approval == null)
            return options.ServerBaseUrl;
        if (approval.AgentId.Length == 0 || approval.AgentId != options.AgentId)
            throw new InvalidOperationException("Bitte den Zielserver für die eingerichtete Importstation erneut bestätigen.");
        return approval.ServerBaseUrl;
    }

    public void Save(ApprovedServer? displayed, string agentId, Uri target, bool confirmed)
    {
        if (!confirmed)
            throw new InvalidOperationException("Bitte bestätigen Sie den Zielserver für die Maschinenidentität.");

        // Serialize settings dialogs, including dialogs in other Windows sessions.
        // The lock is acquired before comparing with the value originally shown.
        using var gate = new FileStream(_path + ".lock", FileMode.OpenOrCreate, FileAccess.Write,
            FileShare.None, 1, FileOptions.DeleteOnClose);
        if (Read() != displayed)
            throw new IOException("Die Serveradresse wurde inzwischen geändert. Öffnen Sie die Einstellungen erneut.");
        var address = AgentOptions.ParseServerAddress(target.OriginalString).AbsoluteUri;
        var temp = _path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var output = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                JsonSerializer.Serialize(output, new ApprovedServer(address, agentId));
                output.Flush(flushToDisk: true);
            }
            File.Move(temp, _path, overwrite: true);
        }
        finally
        {
            File.Delete(temp);
        }
    }
}
