using System.Net;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;

namespace Speakr.Clinic.ImportAgent.ClinicTests;

// Synthetic local TLS endpoint: disposable self-signed certificates only, no real credential or server.
internal sealed class TlsEndpoint : IDisposable
{
    private readonly TcpListener _listener = new(IPAddress.Loopback, 0);
    private readonly X509Certificate2 _server;
    private readonly CancellationTokenSource _cts = new();
    private readonly Task _loop;
    private int _served;

    public HashSet<string> Allowed { get; } = new(StringComparer.OrdinalIgnoreCase);
    public int Served => Volatile.Read(ref _served);
    public int Port => ((IPEndPoint)_listener.LocalEndpoint).Port;

    public TlsEndpoint(X509Certificate2 server)
    {
        _server = server;
        _listener.Start();
        _loop = Task.Run(Loop);
    }

    private async Task Loop()
    {
        while (!_cts.IsCancellationRequested)
        {
            TcpClient client;
            try
            {
                client = await _listener.AcceptTcpClientAsync(_cts.Token);
            }
            catch (Exception e) when (e is OperationCanceledException or ObjectDisposedException or SocketException)
            {
                return;
            }
            _ = Handle(client);
        }
    }

    private async Task Handle(TcpClient client)
    {
        using (client)
        {
            using var ssl = new SslStream(client.GetStream(), false,
                (_, cert, _, _) => cert is not null && Allowed.Contains(CertificateLifecycleTests.Hex(cert.GetCertHash(HashAlgorithmName.SHA256))));
            try
            {
                await ssl.AuthenticateAsServerAsync(new SslServerAuthenticationOptions
                {
                    ServerCertificate = _server,
                    ClientCertificateRequired = true,
                    EnabledSslProtocols = SslProtocols.Tls12 | SslProtocols.Tls13
                }, _cts.Token);
                var seen = new List<byte>();
                var one = new byte[1];
                while (!(seen.Count >= 4 && seen[^4] == '\r' && seen[^3] == '\n' && seen[^2] == '\r' && seen[^1] == '\n'))
                {
                    if (await ssl.ReadAsync(one, _cts.Token) == 0)
                        return;
                    seen.Add(one[0]);
                }
                var body = Encoding.UTF8.GetBytes("{\"intents\":[]}");
                var head = Encoding.ASCII.GetBytes(
                    $"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {body.Length}\r\nConnection: close\r\n\r\n");
                await ssl.WriteAsync(head, _cts.Token);
                await ssl.WriteAsync(body, _cts.Token);
                await ssl.FlushAsync(_cts.Token);
                Interlocked.Increment(ref _served);
            }
            catch (Exception e) when (e is AuthenticationException or IOException or OperationCanceledException or ObjectDisposedException)
            {
                // A refused handshake is the expected outcome for the negative cases.
            }
        }
    }

    public void Dispose()
    {
        _cts.Cancel();
        _listener.Stop();
        try
        {
            _loop.Wait(TimeSpan.FromSeconds(2));
        }
        catch (AggregateException)
        {
        }
        _cts.Dispose();
        _server.Dispose();
    }
}

public class CertificateLifecycleTests
{
    private const string Password = "synthetic-pfx-secret";
    private static readonly DateTimeOffset Now = DateTimeOffset.UtcNow;

    internal static string Hex(byte[] bytes) => Convert.ToHexString(bytes);

    private static X509Certificate2 MakeCert(string name, DateTimeOffset notBefore, DateTimeOffset notAfter, bool server, string dns = "localhost")
    {
        using var rsa = RSA.Create(2048);
        var request = new CertificateRequest(new X500DistinguishedName("CN=" + name), rsa, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        if (server)
        {
            var san = new SubjectAlternativeNameBuilder();
            san.AddDnsName(dns);
            if (dns == "localhost")
                san.AddIpAddress(IPAddress.Loopback);
            request.CertificateExtensions.Add(san.Build());
        }
        request.CertificateExtensions.Add(new X509EnhancedKeyUsageExtension(
            [new Oid(server ? "1.3.6.1.5.5.7.3.1" : "1.3.6.1.5.5.7.3.2")], false));
        using var self = request.CreateSelfSigned(notBefore, notAfter);
        return X509CertificateLoader.LoadPkcs12(self.Export(X509ContentType.Pfx, Password), Password, X509KeyStorageFlags.Exportable);
    }

    private static MachineCredential Credential(X509Certificate2 client) =>
        new() { AgentId = FixtureSupport.AgentId, ClientCertificatePfx = client.Export(X509ContentType.Pfx, Password), PfxPassword = Password };

    private static string ThumbOf(X509Certificate2 cert) => Hex(cert.GetCertHash(HashAlgorithmName.SHA256));

    private static string KeyPin(X509Certificate2 cert) => Hex(SHA256.HashData(cert.GetPublicKey()));

    private static async Task<IReadOnlyList<ImportIntent>> Poll(TlsEndpoint endpoint, MachineCredential credential, CredentialPolicy policy)
    {
        using var http = new HttpClient(HttpImportServer.CreateHandler(credential, policy))
        {
            BaseAddress = new Uri($"https://127.0.0.1:{endpoint.Port}/"),
            Timeout = TimeSpan.FromSeconds(10)
        };
        return await new HttpImportServer(http).PollIntentsAsync(FixtureSupport.AgentId, CancellationToken.None);
    }

    [Fact]
    public async Task Provisioned_credential_with_pinned_server_identity_is_accepted()
    {
        using var client = MakeCert("agent-client", Now.AddDays(-1), Now.AddDays(30), server: false);
        var serverCert = MakeCert("synthetic-server", Now.AddDays(-1), Now.AddDays(30), server: true);
        var pin = KeyPin(serverCert);
        using var endpoint = new TlsEndpoint(serverCert);
        endpoint.Allowed.Add(ThumbOf(client));
        var intents = await Poll(endpoint, Credential(client), new CredentialPolicy { PinnedServerKeySha256 = pin });
        Assert.Empty(intents);
        Assert.Equal(1, endpoint.Served);
    }

    [Fact]
    public async Task Unpinned_untrusted_server_and_wrong_pin_are_refused_without_sending_anything()
    {
        using var client = MakeCert("agent-client", Now.AddDays(-1), Now.AddDays(30), server: false);
        using var endpoint = new TlsEndpoint(MakeCert("synthetic-server", Now.AddDays(-1), Now.AddDays(30), server: true));
        endpoint.Allowed.Add(ThumbOf(client));
        await Assert.ThrowsAsync<HttpRequestException>(() => Poll(endpoint, Credential(client), new CredentialPolicy()));
        await Assert.ThrowsAsync<HttpRequestException>(() =>
            Poll(endpoint, Credential(client), new CredentialPolicy { PinnedServerKeySha256 = new string('0', 64) }));
        Assert.Equal(0, endpoint.Served);
        Assert.Throws<InvalidOperationException>(() =>
            HttpImportServer.CreateHandler(Credential(client), new CredentialPolicy { PinnedServerKeySha256 = "not-a-digest" }));
    }

    [Fact]
    public async Task Pinned_key_does_not_excuse_a_host_name_mismatch()
    {
        using var client = MakeCert("agent-client", Now.AddDays(-1), Now.AddDays(30), server: false);
        var serverCert = MakeCert("synthetic-server", Now.AddDays(-1), Now.AddDays(30), server: true, dns: "other.invalid");
        var pin = KeyPin(serverCert);
        using var endpoint = new TlsEndpoint(serverCert);
        endpoint.Allowed.Add(ThumbOf(client));
        await Assert.ThrowsAsync<HttpRequestException>(() =>
            Poll(endpoint, Credential(client), new CredentialPolicy { PinnedServerKeySha256 = pin }));
        Assert.Equal(0, endpoint.Served);
    }

    [Fact]
    public void Expired_or_not_yet_valid_credential_is_refused_locally()
    {
        using var expired = MakeCert("agent-old", Now.AddDays(-20), Now.AddDays(-1), server: false);
        using var future = MakeCert("agent-future", Now.AddDays(1), Now.AddDays(30), server: false);
        using var valid = MakeCert("agent-valid", Now.AddDays(-1), Now.AddDays(30), server: false);
        Assert.Throws<InvalidOperationException>(() => HttpImportServer.CreateHandler(Credential(expired)));
        Assert.Throws<InvalidOperationException>(() => HttpImportServer.CreateHandler(Credential(future)));
        // The same valid certificate is refused once the clock passes its end.
        Assert.Throws<InvalidOperationException>(() =>
            HttpImportServer.CreateHandler(Credential(valid), new CredentialPolicy { UtcNow = DateTime.UtcNow.AddDays(60) }));
        using var ok = HttpImportServer.CreateHandler(Credential(valid)) as HttpClientHandler;
        Assert.NotNull(ok);
    }

    [Fact]
    public async Task Rotation_with_restart_replaces_the_credential_and_revokes_the_old_one()
    {
        using var oldClient = MakeCert("agent-gen1", Now.AddDays(-5), Now.AddDays(30), server: false);
        using var newClient = MakeCert("agent-gen2", Now.AddDays(-1), Now.AddDays(60), server: false);
        var serverCert = MakeCert("synthetic-server", Now.AddDays(-1), Now.AddDays(30), server: true);
        var pin = KeyPin(serverCert);

        using (var endpoint = new TlsEndpoint(X509CertificateLoader.LoadPkcs12(serverCert.Export(X509ContentType.Pfx, Password), Password)))
        {
            endpoint.Allowed.Add(ThumbOf(oldClient));
            Assert.Empty(await Poll(endpoint, Credential(oldClient), new CredentialPolicy { PinnedServerKeySha256 = pin }));
        }

        // Restart mid-rotation: the server now trusts only the new generation, the agent restarts with a revocation list.
        using var restarted = new TlsEndpoint(serverCert);
        restarted.Allowed.Add(ThumbOf(newClient));
        var policy = new CredentialPolicy { PinnedServerKeySha256 = pin, RevokedClientThumbprints = [ThumbOf(oldClient)] };

        var local = Assert.Throws<InvalidOperationException>(() => HttpImportServer.CreateHandler(Credential(oldClient), policy));
        Assert.Contains("revoked", local.Message);
        // Without the local list the server itself still rejects the replaced certificate.
        await Assert.ThrowsAsync<HttpRequestException>(() =>
            Poll(restarted, Credential(oldClient), new CredentialPolicy { PinnedServerKeySha256 = pin }));
        Assert.Equal(0, restarted.Served);

        Assert.Empty(await Poll(restarted, Credential(newClient), policy));
        Assert.Equal(1, restarted.Served);
    }

    [Fact]
    public async Task Failures_do_not_fall_back_and_leak_no_credential_material()
    {
        using var client = MakeCert("agent-client", Now.AddDays(-1), Now.AddDays(30), server: false);
        var credential = Credential(client);
        using var endpoint = new TlsEndpoint(MakeCert("synthetic-server", Now.AddDays(-1), Now.AddDays(30), server: true));
        var messages = new List<string> { credential.ToString() };

        // Server does not know the client certificate: the request fails, it is not retried anonymously.
        var refused = await Assert.ThrowsAsync<HttpRequestException>(() => Poll(endpoint, credential, new CredentialPolicy()));
        messages.Add(refused.ToString());
        var revoked = Assert.Throws<InvalidOperationException>(() =>
            HttpImportServer.CreateHandler(credential, new CredentialPolicy { RevokedClientThumbprints = [ThumbOf(client)] }));
        messages.Add(revoked.ToString());
        var noCert = Assert.Throws<InvalidOperationException>(() =>
            HttpImportServer.CreateHandler(new MachineCredential { AgentId = FixtureSupport.AgentId }));
        messages.Add(noCert.ToString());

        Assert.Equal(0, endpoint.Served);
        var pfxText = Convert.ToBase64String(credential.ClientCertificatePfx!);
        foreach (var text in messages)
        {
            Assert.DoesNotContain(Password, text);
            Assert.DoesNotContain(pfxText[..40], text);
        }
        // Cleartext server URLs stay refused, so there is no insecure channel to fall back to.
        var options = new AgentOptions { ServerBaseUrl = "http://127.0.0.1:1/" };
        Assert.Throws<InvalidOperationException>(() => options.RequireHttpsServer());
    }
}
