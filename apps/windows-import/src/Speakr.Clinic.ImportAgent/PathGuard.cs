namespace Speakr.Clinic.ImportAgent;

public static class PathGuard
{
    public static bool TryAdmit(string rootPath, string candidatePath, out string fullPath)
    {
        fullPath = "";
        string root;
        string full;
        if (HasForbiddenForm(candidatePath))
            return false;
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

    // Raw-form checks before GetFullPath, which on Windows would silently strip trailing dots and
    // spaces and leaves device prefixes alone.
    private static bool HasForbiddenForm(string raw)
    {
        if (raw.StartsWith(@"\\?\", StringComparison.Ordinal) || raw.StartsWith(@"\\.\", StringComparison.Ordinal)
            || raw.StartsWith("//?/", StringComparison.Ordinal) || raw.StartsWith("//./", StringComparison.Ordinal))
            return true;
        var windows = OperatingSystem.IsWindows();
        var tail = windows && raw.Length >= 2 && raw[1] == ':' ? raw[2..] : raw;
        if (windows && tail.Contains(':', StringComparison.Ordinal))
            return true;
        foreach (var segment in raw.Split('/', '\\'))
        {
            if (segment.Length == 0 || segment == "." || segment == "..")
                continue;
            if (segment.EndsWith('.') || segment.EndsWith(' '))
                return true;
            if (windows && System.Text.RegularExpressions.Regex.IsMatch(segment, "~[0-9]"))
                return true;
        }
        return false;
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
            // Attributes, not Path.Exists: Exists follows links and reports a dangling link as absent.
            FileAttributes attributes;
            try
            {
                attributes = File.GetAttributes(cursor);
            }
            catch (Exception ex) when (ex is FileNotFoundException or DirectoryNotFoundException)
            {
                return false;
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                return true;
            }
            if ((attributes & FileAttributes.ReparsePoint) == 0)
                continue;
            FileSystemInfo? target;
            try
            {
                target = File.ResolveLinkTarget(cursor, returnFinalTarget: true);
            }
            catch (IOException)
            {
                return true;
            }
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
}
