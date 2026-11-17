using System.Text.RegularExpressions;

namespace Speakr.Clinic.ImportAgent;

public static class SourceWalker
{
    public static IEnumerable<string> List(AllowedRoot root)
    {
        if (!Directory.Exists(root.Path))
            yield break;
        var rootFull = Path.GetFullPath(root.Path);
        foreach (var file in Walk(rootFull, root.Recursive))
        {
            var name = Path.GetFileName(file);
            if (name.EndsWith(".simulator.json", StringComparison.OrdinalIgnoreCase))
                continue;
            if (!FilePattern.IsMatch(root.Pattern, name))
                continue;
            if (!PathGuard.TryAdmit(rootFull, file, out var admitted))
                continue;
            yield return admitted;
        }
    }

    private static IEnumerable<string> Walk(string directory, bool recursive)
    {
        IEnumerable<string> files;
        try
        {
            files = Directory.EnumerateFiles(directory);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            yield break;
        }

        foreach (var file in files)
        {
            if (IsReparse(file))
                continue;
            yield return file;
        }

        if (!recursive)
            yield break;

        IEnumerable<string> directories;
        try
        {
            directories = Directory.EnumerateDirectories(directory);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            yield break;
        }

        foreach (var child in directories)
        {
            if (IsReparse(child))
                continue;
            foreach (var file in Walk(child, true))
                yield return file;
        }
    }

    private static bool IsReparse(string path)
    {
        try
        {
            return (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return true;
        }
    }
}

public static class FilePattern
{
    public static bool IsMatch(string pattern, string fileName)
    {
        if (string.IsNullOrEmpty(pattern) || pattern == "*")
            return true;
        return Matcher(pattern).IsMatch(fileName);
    }

    private static Regex Matcher(string pattern)
    {
        var body = Regex.Escape(pattern).Replace("\\*", ".*", StringComparison.Ordinal).Replace("\\?", ".", StringComparison.Ordinal);
        return new Regex("^" + body + "$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
    }
}
