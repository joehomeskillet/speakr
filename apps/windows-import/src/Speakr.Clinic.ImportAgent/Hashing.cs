using System.Security.Cryptography;
using System.Text;

namespace Speakr.Clinic.ImportAgent;

public static class Hashing
{
    public static string Sha256Hex(ReadOnlySpan<byte> data)
    {
        var hash = SHA256.HashData(data);
        return Convert.ToHexString(hash).ToLowerInvariant();
    }

    public static Guid DeterministicGuid(string material)
    {
        var hash = SHA256.HashData(Encoding.UTF8.GetBytes(material));
        hash[6] = (byte)((hash[6] & 0x0F) | 0x50);
        hash[8] = (byte)((hash[8] & 0x3F) | 0x80);
        // Big-endian so the version and variant nibbles appear at their RFC 4122 text positions.
        return new Guid(hash.AsSpan(0, 16), bigEndian: true);
    }
}
