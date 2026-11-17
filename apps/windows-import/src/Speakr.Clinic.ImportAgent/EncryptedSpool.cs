using System.Security.Cryptography;
using System.Text.Json;

namespace Speakr.Clinic.ImportAgent;

public sealed class EncryptedSpool
{
    private readonly string _directory;
    private readonly byte[] _key;
    private readonly long _maxBytes;
    private readonly Dictionary<string, long> _sizes;
    private readonly string _indexPath;

    public EncryptedSpool(string directory, byte[] key, long maxBytes)
    {
        if (key.Length != 32)
            throw new ArgumentException("Spool key must be 32 bytes.", nameof(key));
        if (maxBytes < 1)
            throw new ArgumentOutOfRangeException(nameof(maxBytes));
        _directory = directory;
        _key = key.ToArray();
        _maxBytes = maxBytes;
        Directory.CreateDirectory(directory);
        _indexPath = Path.Combine(directory, "sizes.json");
        _sizes = Load();
    }

    public void Put(string snapshotVersion, ReadOnlySpan<byte> plain)
    {
        var name = SafeName(snapshotVersion);
        var previous = _sizes.GetValueOrDefault(name);
        var next = _sizes.Values.Sum() - previous + plain.Length;
        if (next > _maxBytes)
            throw new SpoolLimitException();
        var nonce = RandomNumberGenerator.GetBytes(12);
        var cipher = new byte[plain.Length];
        var tag = new byte[16];
        using (var aes = new AesGcm(_key, 16))
            aes.Encrypt(nonce, plain, cipher, tag);
        var blob = new byte[1 + nonce.Length + tag.Length + cipher.Length];
        blob[0] = 1;
        nonce.CopyTo(blob.AsSpan(1));
        tag.CopyTo(blob.AsSpan(13));
        cipher.CopyTo(blob.AsSpan(29));
        var path = Path.Combine(_directory, name);
        var temp = path + ".tmp";
        File.WriteAllBytes(temp, blob);
        File.Move(temp, path, overwrite: true);
        _sizes[name] = plain.Length;
        Save();
    }

    public byte[] Read(string snapshotVersion)
    {
        var path = Path.Combine(_directory, SafeName(snapshotVersion));
        var blob = File.ReadAllBytes(path);
        if (blob.Length < 29 || blob[0] != 1)
            throw new InvalidDataException("Spool entry is not readable.");
        var plain = new byte[blob.Length - 29];
        using var aes = new AesGcm(_key, 16);
        aes.Decrypt(blob.AsSpan(1, 12), blob.AsSpan(29), blob.AsSpan(13, 16), plain);
        return plain;
    }

    public bool Contains(string snapshotVersion) =>
        File.Exists(Path.Combine(_directory, SafeName(snapshotVersion)));

    private static string SafeName(string snapshotVersion)
    {
        if (snapshotVersion.Length != 64 || snapshotVersion.Any(c => c is not (>= '0' and <= '9' or >= 'a' and <= 'f' or >= 'A' and <= 'F')))
            throw new ArgumentException("Snapshot version is not a sha256 hex string.", nameof(snapshotVersion));
        return snapshotVersion.ToLowerInvariant();
    }

    private Dictionary<string, long> Load()
    {
        if (!File.Exists(_indexPath))
            return new Dictionary<string, long>(StringComparer.Ordinal);
        var parsed = JsonSerializer.Deserialize<Dictionary<string, long>>(File.ReadAllText(_indexPath));
        return parsed ?? new Dictionary<string, long>(StringComparer.Ordinal);
    }

    private void Save()
    {
        var json = JsonSerializer.Serialize(_sizes);
        File.WriteAllText(_indexPath, json);
    }
}
