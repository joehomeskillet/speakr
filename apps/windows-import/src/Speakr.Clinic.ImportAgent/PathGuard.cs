namespace Speakr.Clinic.ImportAgent;

public static class PathGuard
{
    public static bool TryAdmit(string rootPath, string candidatePath, out string fullPath)
    {
        fullPath = "";
        string root;
        string full;
        try
        {
            root = Normalize(rootPath);
            full = Path.GetFullPath(candidatePath);
        }
        catch (Exception ex) when (ex is ArgumentException or NotSupportedException or PathTooLongException)
        {
            return false;
        }

        if (!IsUnder(root, full) || Escapes(root, full))
            return false;
        fullPath = full;
        return true;
    }

    public static string Normalize(string path)
    {
        var full = Path.GetFullPath(path);
        var sep = Path.DirectorySeparatorChar;
        if (full.EndsWith(sep) || full.EndsWith(Path.AltDirectorySeparatorChar))
            return full;
        return full + sep;
    }

    public static bool IsUnder(string rootWithSeparator, string fullPath)
    {
        return fullPath.StartsWith(rootWithSeparator, Comparison)
            && fullPath.Length > rootWithSeparator.Length;
    }

    private static StringComparison Comparison =>
        OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;

    private static bool Escapes(string rootWithSeparator, string fullPath)
    {
        var rootTrim = rootWithSeparator.TrimEnd(Path.DirectorySeparatorChar);
        var relative = fullPath[rootWithSeparator.Length..];
        var cursor = rootTrim;
        foreach (var part in relative.Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar))
        {
            if (part.Length == 0 || part == ".")
                continue;
            if (part == "..")
                return true;
            cursor = Path.Combine(cursor, part);
            if (!Path.Exists(cursor))
                return false;
            if (!IsReparse(cursor))
                continue;
            var target = File.ResolveLinkTarget(cursor, returnFinalTarget: true);
            if (target is null)
                return true;
            var resolved = target.FullName;
            if (!IsUnder(rootWithSeparator, resolved)
                && !string.Equals(resolved.TrimEnd(Path.DirectorySeparatorChar), rootTrim, Comparison))
                return true;
            cursor = resolved;
        }
        return false;
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
