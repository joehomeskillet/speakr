using System.Text.Json;

namespace Speakr.Clinic.ImportAgent;

public static class SidecarReader
{
    public static CompletionObservation Read(string filePath, bool simulatorMode)
    {
        if (!simulatorMode)
            return CompletionObservation.Unknown;
        var sidePath = filePath + ".simulator.json";
        if (!File.Exists(sidePath))
            return CompletionObservation.Unknown;
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllBytes(sidePath));
            var root = doc.RootElement;
            if (!root.TryGetProperty("simulator", out var marked) || marked.ValueKind != JsonValueKind.True)
                return CompletionObservation.Unknown;
            var finished = root.TryGetProperty("finished", out var flag) && flag.ValueKind == JsonValueKind.True;
            string? author = null;
            if (root.TryGetProperty("author_code", out var authorNode) && authorNode.ValueKind == JsonValueKind.String)
                author = authorNode.GetString();
            return new CompletionObservation(
                finished ? "completed" : "draft",
                "fixture",
                author,
                "simulator-sidecar");
        }
        catch (JsonException)
        {
            return CompletionObservation.Unknown;
        }
    }
}
