using System.Globalization;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Speakr.Clinic.ImportAgent;

public static class ImportJson
{
    public static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = null,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    };
}

public sealed class HttpImportServer : IImportServer
{
    private readonly HttpClient _http;

    public HttpImportServer(HttpClient http)
    {
        _http = http;
    }

    public static HttpMessageHandler CreateHandler(MachineCredential credential)
    {
        if (credential.ClientCertificatePfx is not { Length: > 0 })
            throw new InvalidOperationException("A machine client certificate is required.");
        // Ephemeral: the private key is not persisted into the user's key store.
        var certificate = X509CertificateLoader.LoadPkcs12(
            credential.ClientCertificatePfx,
            credential.PfxPassword,
            X509KeyStorageFlags.EphemeralKeySet);
        var now = DateTime.UtcNow;
        if (now < certificate.NotBefore.ToUniversalTime() || now > certificate.NotAfter.ToUniversalTime())
            throw new InvalidOperationException("The machine client certificate is outside its validity period.");
        var handler = new HttpClientHandler
        {
            ClientCertificateOptions = ClientCertificateOption.Manual,
            // A redirect would carry chunk bodies and the client certificate handshake to another host.
            AllowAutoRedirect = false,
            SslProtocols = System.Security.Authentication.SslProtocols.Tls12 | System.Security.Authentication.SslProtocols.Tls13
        };
        handler.ClientCertificates.Add(certificate);
        return handler;
    }

    public async Task ReportInventoryAsync(InventoryReport report, CancellationToken cancellationToken)
    {
        using var response = await _http.PutAsJsonAsync("api/clinic/v1/agent/inventory", report, ImportJson.Options, cancellationToken);
        await EnsureSuccess(response, cancellationToken);
    }

    public async Task<IReadOnlyList<ImportIntent>> PollIntentsAsync(string agentId, CancellationToken cancellationToken)
    {
        // The agent identity is the mTLS client certificate; a self-asserted query parameter is never sent.
        var body = await _http.GetFromJsonAsync<IntentList>("api/clinic/v1/agent/intents", ImportJson.Options, cancellationToken);
        return body?.Intents ?? [];
    }

    public async Task<IngestReceipt> BeginAsync(WireBeginIngest body, string idempotencyKey, CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, "api/clinic/v1/agent/ingests")
        {
            Content = JsonContent.Create(body, options: ImportJson.Options)
        };
        request.Headers.TryAddWithoutValidation("Idempotency-Key", idempotencyKey);
        using var response = await _http.SendAsync(request, cancellationToken);
        return await ReadReceipt(response, cancellationToken);
    }

    public async Task<IngestReceipt> PutChunkAsync(
        string ingestId,
        int sequence,
        long offset,
        ReadOnlyMemory<byte> body,
        string sha256,
        CancellationToken cancellationToken)
    {
        using var content = new ByteArrayContent(body.ToArray());
        content.Headers.ContentType = new MediaTypeHeaderValue("application/octet-stream");
        using var request = new HttpRequestMessage(HttpMethod.Put, $"api/clinic/v1/agent/ingests/{IngestPath(ingestId)}/chunks/{sequence}")
        {
            Content = content
        };
        request.Headers.TryAddWithoutValidation("X-Chunk-SHA256", sha256);
        request.Headers.TryAddWithoutValidation("X-Chunk-Offset", offset.ToString(CultureInfo.InvariantCulture));
        using var response = await _http.SendAsync(request, cancellationToken);
        return await ReadReceipt(response, cancellationToken);
    }

    public async Task<IngestReceipt> FinalizeAsync(
        string ingestId,
        WireFinalizeIngest body,
        string idempotencyKey,
        CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, $"api/clinic/v1/agent/ingests/{IngestPath(ingestId)}/finalize")
        {
            Content = JsonContent.Create(body, options: ImportJson.Options)
        };
        request.Headers.TryAddWithoutValidation("Idempotency-Key", idempotencyKey);
        using var response = await _http.SendAsync(request, cancellationToken);
        return await ReadReceipt(response, cancellationToken);
    }

    public async Task<IngestReceipt> GetAsync(string ingestId, CancellationToken cancellationToken)
    {
        using var response = await _http.GetAsync($"api/clinic/v1/agent/ingests/{IngestPath(ingestId)}", cancellationToken);
        return await ReadReceipt(response, cancellationToken);
    }

    public async Task<IngestReceipt> AbortAsync(string ingestId, CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, $"api/clinic/v1/agent/ingests/{IngestPath(ingestId)}/abort");
        using var response = await _http.SendAsync(request, cancellationToken);
        return await ReadReceipt(response, cancellationToken);
    }

    public async Task ReportOutcomeAsync(AgentOutcome outcome, CancellationToken cancellationToken)
    {
        using var response = await _http.PostAsJsonAsync("api/clinic/v1/agent/outcomes", outcome, ImportJson.Options, cancellationToken);
        await EnsureSuccess(response, cancellationToken);
    }

    // The ingest id is server supplied. Only a canonical GUID may reach a URL path.
    private static string IngestPath(string ingestId)
    {
        if (!Guid.TryParseExact(ingestId, "D", out var id))
            throw new ImportServerException(0, "ingest id is not a canonical GUID");
        return id.ToString("D");
    }

    private static async Task<IngestReceipt> ReadReceipt(HttpResponseMessage response, CancellationToken cancellationToken)
    {
        await EnsureSuccess(response, cancellationToken);
        var receipt = await response.Content.ReadFromJsonAsync<IngestReceipt>(ImportJson.Options, cancellationToken);
        if (receipt is null || !Guid.TryParseExact(receipt.IngestId, "D", out _))
            throw new ImportServerException((int)response.StatusCode, "missing receipt or malformed ingest id");
        receipt.ReceivedChunks ??= [];
        receipt.CorrelationId ??= "";
        receipt.State ??= "";
        receipt.Durability ??= "";
        receipt.DecodeState ??= "";
        receipt.AgentId ??= "";
        receipt.IntentId ??= "";
        receipt.SourceId ??= "";
        receipt.SnapshotVersion ??= "";
        receipt.PermittedPostStep ??= "";
        receipt.AuthorState ??= "";
        receipt.Completion ??= "";
        return receipt;
    }

    private static async Task EnsureSuccess(HttpResponseMessage response, CancellationToken cancellationToken)
    {
        if ((int)response.StatusCode is >= 200 and <= 299)
            return;
        var raw = await response.Content.ReadAsStringAsync(cancellationToken);
        var code = ProblemCode(raw);
        var text = LogText.Clean(raw, 240);
        var status = (int)response.StatusCode;
        if (status == 409 || code == "conflict")
            throw new ImportConflictException(text);
        if (status == 410 || code == "expired")
            throw new ImportExpiredException(text);
        throw new ImportServerException(status, code is null ? text : code + ": " + text);
    }

    private static string? ProblemCode(string raw)
    {
        try
        {
            using var doc = JsonDocument.Parse(raw);
            if (doc.RootElement.TryGetProperty("code", out var code) && code.ValueKind == JsonValueKind.String)
                return code.GetString();
        }
        catch (JsonException)
        {
            return null;
        }
        return null;
    }
}
