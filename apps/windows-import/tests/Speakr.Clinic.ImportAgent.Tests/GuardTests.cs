using System.Net;
using System.Reflection;
using System.Runtime.Versioning;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;
using Speakr.Clinic.ImportAgent.Worker;

namespace Speakr.Clinic.ImportAgent.Tests;

public class GuardTests
{
    [Fact]
    public void Path_outside_an_allowed_root_is_rejected()
    {
        var root = Temp.Dir();
        var outside = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            var evil = Path.Combine(outside, "evil.txt");
            File.WriteAllText(evil, "secret");
            File.WriteAllText(Path.Combine(root, "ok.dss"), "ok");
            Directory.CreateDirectory(Path.Combine(root, "nested"));
            File.WriteAllText(Path.Combine(root, "nested", "hidden.dss"), "hidden");
            File.WriteAllText(Path.Combine(root, "note.txt"), "text");
            var link = Path.Combine(root, "escape.dss");
            File.CreateSymbolicLink(link, evil);

            Assert.False(PathGuard.TryAdmit(root, evil, out _));
            Assert.False(PathGuard.TryAdmit(root, Path.Combine(root, "..", "evil.txt"), out _));
            Assert.False(PathGuard.TryAdmit(root, link, out _));
            Assert.Throws<InvalidOperationException>(() => Temp.Options("relative-root", spool).Validate());

            var top = SourceWalker.List(new AllowedRoot { Id = "root-a", Path = root, Pattern = "*.dss" }).ToArray();
            Assert.Contains(top, path => Path.GetFileName(path) == "ok.dss");
            Assert.DoesNotContain(top, path => path == evil || path == link || path.Contains($"{Path.DirectorySeparatorChar}nested{Path.DirectorySeparatorChar}", StringComparison.Ordinal));

            var recursive = SourceWalker.List(new AllowedRoot { Id = "root-a", Path = root, Recursive = true, Pattern = "*.dss" }).ToArray();
            Assert.Contains(recursive, path => Path.GetFileName(path) == "hidden.dss");
            Assert.DoesNotContain(recursive, path => path == evil || path == link);
        }
        finally
        {
            Temp.Delete(root, outside, spool);
        }
    }

    [Fact]
    public void Spool_roundtrip_hides_plaintext_and_enforces_the_bound()
    {
        var spoolDir = Temp.Dir();
        try
        {
            var spool = new EncryptedSpool(spoolDir, Temp.Key(), 64);
            var plain = Encoding.UTF8.GetBytes("PLAINTEXT-MARKER-1234567890");
            var version = Hashing.Sha256Hex(plain);
            spool.Put(version, plain);
            var stored = File.ReadAllBytes(Path.Combine(spoolDir, version));
            Assert.DoesNotContain("PLAINTEXT-MARKER-1234567890", Encoding.Latin1.GetString(stored));
            Assert.Equal(plain, spool.Read(version));
            var other = Hashing.Sha256Hex(Encoding.UTF8.GetBytes("another-snapshot-version-value"));
            Assert.Throws<SpoolLimitException>(() => spool.Put(other, new byte[64]));
        }
        finally
        {
            Temp.Delete(spoolDir);
        }
    }

    [Fact]
    public async Task Http_client_follows_the_ingest_contract_and_resumes()
    {
        var bytes = Encoding.UTF8.GetBytes("abcdefghijklmnopqrst");
        var sha = Hashing.Sha256Hex(bytes);
        var handler = new RecordingHandler(sha, bytes.Length);
        var spoolDir = Temp.Dir();
        try
        {
            using var client = new HttpClient(handler) { BaseAddress = new Uri("https://clinic.example.invalid") };
            var server = new HttpImportServer(client);
            var item = new InventoryItem(
                "root-a/note.dss",
                "root-a",
                "/tmp/root-a",
                "note.dss",
                "/tmp/root-a/note.dss",
                bytes.Length,
                DateTimeOffset.Parse("2026-01-02T03:04:05Z"),
                sha,
                Hashing.Sha256Hex(Encoding.UTF8.GetBytes("version")),
                CompletionObservation.Unknown);
            var spool = new EncryptedSpool(spoolDir, Temp.Key(), 1024);
            spool.Put(item.SnapshotVersion, bytes);
            var options = new AgentOptions
            {
                AgentId = "agent-1",
                SpoolDirectory = spoolDir,
                ChunkSizeBytes = 8,
                AllowedRoots = [new AllowedRoot { Id = "root-a", Path = "/tmp/root-a" }]
            };
            var uploader = new ResumableUploader(server, spool, options);
            await server.ReportInventoryAsync(new InventoryReport
            {
                AgentId = "agent-1",
                Items =
                [
                    new InventoryItemWire
                    {
                        SourceId = item.SourceId,
                        RootId = item.RootId,
                        RelativePath = item.RelativePath,
                        Size = item.Size,
                        Sha256 = item.ContentSha256,
                        SnapshotVersion = item.SnapshotVersion
                    }
                ]
            }, CancellationToken.None);
            await server.PollIntentsAsync("agent-1", CancellationToken.None);
            var receipt = await uploader.UploadAsync(item, CancellationToken.None);
            Assert.Equal(sha, receipt.ContentSha256);
            Assert.True(receipt.IsDurable);

            Assert.Contains(handler.Calls, call => call.Method == "PUT" && call.Path == "/api/clinic/v1/agent/inventory");
            Assert.Contains(handler.Calls, call => call.Method == "GET" && call.Path == "/api/clinic/v1/agent/intents");
            Assert.Contains(handler.Calls, call => call.Method == "POST" && call.Path == "/api/clinic/v1/ingests");
            Assert.Contains(handler.Calls, call => call.Method == "PUT" && call.Path.EndsWith("/chunks/1", StringComparison.Ordinal));
            Assert.Contains(handler.Calls, call => call.Method == "PUT" && call.Path.EndsWith("/chunks/2", StringComparison.Ordinal));
            Assert.DoesNotContain(handler.Calls, call => call.Path.Contains("/chunks/0", StringComparison.Ordinal));
            Assert.Contains(handler.Calls, call => call.Method == "POST" && call.Path.EndsWith("/finalize", StringComparison.Ordinal));
            Assert.DoesNotContain(handler.Calls, call => call.Path.Contains("resolve-author", StringComparison.Ordinal) || call.Path.Contains("/commands", StringComparison.Ordinal));
            Assert.All(handler.Calls, call => Assert.Equal("https", call.Scheme));

            var chunk1 = handler.Calls.Single(call => call.Path.EndsWith("/chunks/1", StringComparison.Ordinal));
            Assert.Equal(bytes.AsSpan(8, 8).ToArray(), chunk1.Body);
            Assert.Equal(Hashing.Sha256Hex(chunk1.Body), Assert.Single(chunk1.Headers["X-Chunk-SHA256"]));
            var begin = handler.Calls.Single(call => call.Method == "POST" && call.Path == "/api/clinic/v1/ingests");
            var beginBody = JsonSerializer.Deserialize<WireBeginIngest>(begin.Body, ImportJson.Options);
            Assert.NotNull(beginBody);
            Assert.Equal(Assert.Single(begin.Headers["Idempotency-Key"]), beginBody.RequestId);
            Assert.Equal("device", beginBody.SourceKind);
            Assert.Equal(sha, beginBody.ExpectedSha256);
            Assert.All(handler.Calls, call =>
            {
                Assert.False(call.Headers.ContainsKey("X-CSRFToken"));
                Assert.False(call.Headers.ContainsKey("Cookie"));
            });
            var inventory = handler.Calls.Single(call => call.Path.EndsWith("/inventory", StringComparison.Ordinal));
            Assert.DoesNotContain("/tmp/root-a", Encoding.UTF8.GetString(inventory.Body));
        }
        finally
        {
            Temp.Delete(spoolDir);
        }
    }

    [Fact]
    public void Machine_credential_is_not_logged_or_printed()
    {
        const string password = "pw-secret-not-for-logs";
        using var rsa = RSA.Create(2048);
        var request = new CertificateRequest("CN=import-agent", rsa, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        using var issued = request.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddDays(2));
        var credential = new MachineCredential
        {
            AgentId = "agent-1",
            ClientCertificatePfx = issued.ExportPkcs12(Pkcs12ExportPbeParameters.Default, password),
            PfxPassword = password
        };
        Assert.DoesNotContain(password, credential.ToString());
        using var handler = (HttpClientHandler)HttpImportServer.CreateHandler(credential);
        var printed = handler.ClientCertificates.Cast<X509Certificate>().Single().ToString();
        Assert.DoesNotContain(password, printed);
        Assert.Single(handler.ClientCertificates);
        if (!OperatingSystem.IsWindows())
            Assert.Throws<PlatformNotSupportedException>(() => new DpapiFileSecretStore("/tmp/clinic-import-secret"));
    }

    [Fact]
    public async Task Secret_material_does_not_appear_in_import_logs()
    {
        const string password = "pw-secret-not-for-logs";
        var root = Temp.Dir();
        var spool = Temp.Dir();
        try
        {
            var path = Path.Combine(root, "note.dss");
            Temp.Write(path, "abc");
            var log = new ListLog();
            var server = new SimulatorImportServer();
            var agent = Temp.Agent(Temp.Options(root, spool), server, log);
            var store = new MemorySecretStore(new MachineCredential { AgentId = "agent-1", PfxPassword = password }, Temp.Key());
            _ = store.GetMachineCredential();
            var start = DateTimeOffset.Parse("2026-01-02T00:00:00Z");
            var item = Assert.Single((await Temp.Stabilize(agent, start)).Inventory);
            server.Enqueue(Temp.Intent(item, "none"));
            await agent.RunOnceAsync(start.AddSeconds(6));
            var text = string.Join("\n", log.Lines);
            Assert.Contains("upload ", text, StringComparison.Ordinal);
            Assert.DoesNotContain(password, text);
        }
        finally
        {
            Temp.Delete(root, spool);
        }
    }

    [Fact]
    public void Built_agent_has_no_prohibited_api_surface()
    {
        var root = SolutionRoot();
        var sourceRoot = Path.Combine(root, "src");
        var failures = new List<string>();
        foreach (var file in Directory.EnumerateFiles(sourceRoot, "*.cs", SearchOption.AllDirectories))
        {
            var text = File.ReadAllText(file);
            foreach (var token in ForbiddenFragments)
            {
                if (text.Contains(token, StringComparison.OrdinalIgnoreCase))
                    failures.Add(file + " contains " + token);
            }
        }

        foreach (var assembly in new[] { typeof(ImportOrchestrator).Assembly, typeof(ImportWorker).Assembly })
        {
            var framework = assembly.GetCustomAttribute<TargetFrameworkAttribute>();
            Assert.Equal(".NETCoreApp,Version=v10.0", framework?.FrameworkName);
            foreach (var reference in assembly.GetReferencedAssemblies())
                RejectReference(reference.Name, failures);
            var depsPath = DependencyFile(assembly, root);
            Assert.True(File.Exists(depsPath), depsPath);
            using var deps = JsonDocument.Parse(File.ReadAllText(depsPath));
            foreach (var library in deps.RootElement.GetProperty("libraries").EnumerateObject())
                RejectReference(library.Name.Split('/')[0], failures);
            foreach (var type in assembly.GetTypes())
            {
                if (type.Namespace is null || !type.Namespace.StartsWith("Speakr.Clinic.ImportAgent", StringComparison.Ordinal))
                    continue;
                foreach (var method in type.GetMethods(BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.DeclaredOnly))
                {
                    if (BannedMethods.Contains(method.Name))
                        failures.Add(type.FullName + "." + method.Name);
                }
            }
        }

        Assert.True(failures.Count == 0, string.Join("\n", failures));
    }

    private static void RejectReference(string? name, List<string> failures)
    {
        if (string.IsNullOrEmpty(name))
            return;
        foreach (var token in ForbiddenReferences)
        {
            if (name.Contains(token, StringComparison.OrdinalIgnoreCase))
                failures.Add("reference " + name);
        }
    }

    private static string DependencyFile(Assembly assembly, string solutionRoot)
    {
        var beside = Path.ChangeExtension(assembly.Location, ".deps.json");
        if (File.Exists(beside))
            return beside;
        var name = assembly.GetName().Name + ".deps.json";
        var binMarker = $"{Path.DirectorySeparatorChar}bin{Path.DirectorySeparatorChar}";
        return Directory.EnumerateFiles(Path.Combine(solutionRoot, "src"), name, SearchOption.AllDirectories)
            .FirstOrDefault(path => path.Contains(binMarker, StringComparison.Ordinal))
            ?? beside;
    }

    private static string SolutionRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null)
        {
            if (File.Exists(Path.Combine(dir.FullName, "Speakr.Clinic.ImportAgent.sln")))
                return dir.FullName;
            dir = dir.Parent;
        }
        throw new InvalidOperationException("Solution root was not found.");
    }

    private static readonly string[] ForbiddenFragments =
    [
        "HttpListener", "Kestrel", "WebSocket", "SendInput", "RegisterHotKey", "SetWindowsHookEx",
        "Clipboard", "ShellExecute", "ProcessStartInfo", "UseShellExecute", "waveIn", "waveOut",
        "mciSend", "HidD_", "HidP_", "keybd_event", "mouse_event", "FindWindow", "SendMessage",
        "UIAutomation", "GetUserMedia", "NAudio", "SpeechSynthesizer", "Wasapi", "DllImport",
        "LibraryImport", "SendKeys", "Firmware", "Microsoft.Office", "System.Speech",
        "NativeMessaging", "OpenFile", "ExecuteCommand", "SetCursorPos", "Environment.UserName",
        "WindowsIdentity", "Process.Start"
    ];

    private static readonly string[] ForbiddenReferences =
    [
        "NAudio", "CSCore", "HidSharp", "HidLibrary", "FlaUI", "InputSimulator", "WindowsInput",
        "System.Speech", "CognitiveServices", "Office.Interop", "NetOffice", "Kestrel",
        "HttpListener", "WebSocket", "LibUsb", "AspNetCore", "UIAutomation", "WinMM", "CoreAudio",
        "Wasapi", "Clipboard"
    ];

    private static readonly HashSet<string> BannedMethods = new(StringComparer.Ordinal)
    {
        "Execute", "OpenFile", "StartProcess", "SendInput", "RegisterHotKey", "SetClipboard"
    };

    private sealed class RecordingHandler(string sha, int total) : HttpMessageHandler
    {
        public List<Call> Calls { get; } = [];

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var body = request.Content is null
                ? []
                : request.Content.ReadAsByteArrayAsync(cancellationToken).GetAwaiter().GetResult();
            var headers = new Dictionary<string, string[]>(StringComparer.OrdinalIgnoreCase);
            foreach (var header in request.Headers)
                headers[header.Key] = header.Value.ToArray();
            if (request.Content is not null)
            {
                foreach (var header in request.Content.Headers)
                    headers[header.Key] = header.Value.ToArray();
            }
            Calls.Add(new Call(request.Method.Method, request.RequestUri!.AbsolutePath, request.RequestUri.Scheme, headers, body));
            var path = request.RequestUri.AbsolutePath;
            if (path.EndsWith("/inventory", StringComparison.Ordinal))
                return Task.FromResult(new HttpResponseMessage(HttpStatusCode.NoContent));
            if (path.EndsWith("/intents", StringComparison.Ordinal))
                return Task.FromResult(Json("{\"intents\":[]}"));
            if (request.Method == HttpMethod.Post && path.EndsWith("/ingests", StringComparison.Ordinal))
                return Task.FromResult(Json(Receipt("RECEIVING", "RECEIVED", null, 0, [0])));
            if (request.Method == HttpMethod.Put && path.Contains("/chunks/", StringComparison.Ordinal))
                return Task.FromResult(Json(Receipt("RECEIVING", "RECEIVED", null, body.Length, [0])));
            if (path.EndsWith("/finalize", StringComparison.Ordinal))
                return Task.FromResult(Json(Receipt("STORED", "STORED", sha, total, [0, 1, 2])));
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.NotFound));
        }

        private static HttpResponseMessage Json(string json)
        {
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(json, Encoding.UTF8, "application/json")
            };
        }

        private static string Receipt(string state, string durability, string? hash, long size, int[] chunks)
        {
            return JsonSerializer.Serialize(new IngestReceipt
            {
                IngestId = "11111111-1111-1111-1111-111111111111",
                State = state,
                ReceivedChunks = chunks,
                ReceivedBytes = size,
                Sha256 = hash,
                DictationId = null,
                Durability = durability,
                DecodeState = "NOT_STARTED",
                Completion = "UNKNOWN",
                AuthorState = "UNRESOLVED",
                CorrelationId = "22222222-2222-2222-2222-222222222222"
            }, ImportJson.Options);
        }
    }

    private sealed record Call(string Method, string Path, string Scheme, IReadOnlyDictionary<string, string[]> Headers, byte[] Body);
}
