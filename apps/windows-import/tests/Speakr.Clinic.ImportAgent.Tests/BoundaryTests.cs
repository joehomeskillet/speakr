using System.Reflection;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using System.Text.Json;
using Speakr.Clinic.ImportAgent.Worker;

namespace Speakr.Clinic.ImportAgent.Tests;

// Binary-level boundary check. Source text scans (GuardTests) can be evaded by string concatenation or
// aliasing; the metadata of the built assemblies cannot.
public class BoundaryTests
{
    private static readonly string[] BannedTypes =
    [
        "System.Diagnostics.Process", "System.Diagnostics.ProcessStartInfo",
        "System.Net.Sockets.Socket", "System.Net.Sockets.TcpListener", "System.Net.Sockets.TcpClient", "System.Net.Sockets.UdpClient",
        "System.IO.Pipes.NamedPipeServerStream", "System.IO.Pipes.NamedPipeClientStream", "System.IO.Pipes.AnonymousPipeServerStream",
        "System.Net.HttpListener", "System.Net.WebSockets.WebSocket", "System.Net.WebSockets.ClientWebSocket",
        "System.Runtime.InteropServices.DllImportAttribute", "System.Runtime.InteropServices.LibraryImportAttribute",
        "System.Runtime.InteropServices.NativeLibrary", "Microsoft.Win32.Registry", "System.Activator"
    ];

    private static readonly string[] BannedMembers =
    [
        "System.Reflection.Assembly::Load", "System.Reflection.Assembly::LoadFrom", "System.Reflection.Assembly::LoadFile",
        "System.Runtime.InteropServices.Marshal::GetDelegateForFunctionPointer"
    ];

    internal static object Canary() => typeof(System.Diagnostics.Process);

    internal static IReadOnlyList<string> Violations(Assembly assembly)
    {
        var found = new List<string>();
        using var stream = File.OpenRead(assembly.Location);
        using var pe = new PEReader(stream);
        var md = pe.GetMetadataReader();
        foreach (var handle in md.TypeReferences)
        {
            var type = md.GetTypeReference(handle);
            var name = md.GetString(type.Namespace) + "." + md.GetString(type.Name);
            if (BannedTypes.Contains(name))
                found.Add(name);
        }
        foreach (var handle in md.MemberReferences)
        {
            var member = md.GetMemberReference(handle);
            if (member.Parent.Kind != HandleKind.TypeReference)
                continue;
            var type = md.GetTypeReference((TypeReferenceHandle)member.Parent);
            var name = md.GetString(type.Namespace) + "." + md.GetString(type.Name) + "::" + md.GetString(member.Name);
            if (BannedMembers.Contains(name))
                found.Add(name);
        }
        return found;
    }

    [Fact]
    public void Metadata_scan_detects_a_prohibited_reference()
    {
        Assert.Contains("System.Diagnostics.Process", Violations(typeof(BoundaryTests).Assembly));
    }

    [Fact]
    public void Built_agent_assemblies_reference_no_process_socket_pipe_native_or_loader_api()
    {
        Assert.Empty(Violations(typeof(ImportOrchestrator).Assembly));
        Assert.Empty(Violations(typeof(ImportWorker).Assembly));
    }

    [Fact]
    public void Boundary_file_denials_and_defaults_hold()
    {
        var boundary = Path.Combine(AppContext.BaseDirectory, "Fixtures", "import-agent-boundary.json");
        Assert.True(File.Exists(boundary));
        using var doc = JsonDocument.Parse(File.ReadAllText(boundary));
        var denied = doc.RootElement.GetProperty("denied_operations").EnumerateArray().Select(e => e.GetString()).ToArray();
        Assert.Contains("arbitrary_file_proxy", denied);
        Assert.Contains("generic_shell", denied);
        Assert.Contains("credential_passthrough", denied);
        Assert.False(doc.RootElement.GetProperty("source_deletion_default").GetBoolean());
        Assert.False(new AgentOptions().PostImportEnabled);
    }
}
