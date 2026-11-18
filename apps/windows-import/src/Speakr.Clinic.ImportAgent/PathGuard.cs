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
            // shortcut: all-caps names that are exactly a generated 8.3 alias are refused; mixed-case names such as report~1.dss are kept. Upgrade when a managed API can tell a short alias from the long name without P/Invoke.
            if (windows && IsGeneratedShortName(segment))
                return true;
        }
        return false;
    }

    // NTFS stores a generated short alias in uppercase (PROGRA~1, REPORT~1.DSS). A long name keeps its case.
    internal static bool IsGeneratedShortName(string segment)
    {
        if (segment.Length is < 2 or > 12)
            return false;
        foreach (var ch in segment)
        {
            if (char.IsLetter(ch) && char.IsLower(ch))
                return false;
        }
        var dot = segment.LastIndexOf('.');
        var baseName = dot < 0 ? segment : segment[..dot];
        var ext = dot < 0 ? "" : segment[(dot + 1)..];
        if (dot >= 0 && (ext.Length is < 1 or > 3 || ext.AsSpan().Contains('.')))
            return false;
        if (baseName.Length is < 2 or > 8)
            return false;
        var tilde = baseName.IndexOf('~');
        if (tilde <= 0 || tilde != baseName.LastIndexOf('~') || tilde > 6)
            return false;
        var digits = baseName[(tilde + 1)..];
        if (digits.Length == 0)
            return false;
        foreach (var ch in digits)
        {
            if (!char.IsAsciiDigit(ch))
                return false;
        }
        if (baseName[..tilde].Length + 1 + digits.Length > 8)
            return false;
        foreach (var ch in baseName[..tilde])
        {
            if (!char.IsAsciiLetterOrDigit(ch) && ch != '_')
                return false;
        }
        foreach (var ch in ext)
        {
            if (!char.IsAsciiLetterOrDigit(ch) && ch != '_')
                return false;
        }
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
