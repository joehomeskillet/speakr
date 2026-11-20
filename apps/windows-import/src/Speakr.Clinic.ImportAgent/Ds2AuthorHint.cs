using System.Text;

namespace Speakr.Clinic.ImportAgent;

public static class Ds2AuthorHint
{
    // The pinned dss-codec DS2 specification calls 0x0c the 16-byte author ID.
    // Recognize only the observed header layout where the named AUTHOR keyword agrees.
    public static string? Read(ReadOnlySpan<byte> original)
    {
        if (original.Length < 1536 || !original.StartsWith("\u0003ds2"u8))
            return null;
        var label = Field(original.Slice(0x240, 30));
        var id = Field(original.Slice(0x0c, 16));
        if (!Ascii.EqualsIgnoreCase(label, "AUTHOR"u8) || id.IsEmpty || !id.SequenceEqual(Field(original.Slice(0x25e, 30))))
            return null;
        foreach (byte ch in id)
            if (!char.IsAsciiLetterOrDigit((char)ch) && ch is not ((byte)'-' or (byte)'_' or (byte)'.'))
                return null;
        return Encoding.ASCII.GetString(id);
    }

    private static ReadOnlySpan<byte> Field(ReadOnlySpan<byte> field)
    {
        int end = field.IndexOf((byte)0);
        return (end < 0 ? field : field[..end]).TrimEnd((byte)' ');
    }
}
