using System.Security.Cryptography;
using System.Text;

namespace Speakr.Clinic.ImportAgent;

public sealed class EncryptedSpool
{
    private const int HeaderBytes = 29;
    private const byte FormatVersion = 2;

    private readonly string _directory;
    private readonly byte[] _key;
    private readonly long _maxBytes;
    private readonly Dictionary<string, long> _sizes = new(StringComparer.Ordinal);

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
        // The directory is the only truth: no separate size index that can be corrupt or forged.
        foreach (var file in Directory.EnumerateFiles(directory))
        {
            var name = Path.GetFileName(file);
            if (name.EndsWith(".tmp", StringComparison.Ordinal))
                File.Delete(file);
            else if (IsEntryName(name))
                _sizes[name] = Math.Max(0, new FileInfo(file).Length - HeaderBytes);
        }
    }

    // Idempotent per snapshot version: the version is a content hash, an existing entry is the same bytes.
    // This also avoids re-encrypting under the same key every cycle.
    public void Put(string snapshotVersion, ReadOnlySpan<byte> plain)
    {
        var name = SafeName(snapshotVersion);
        if (_sizes.ContainsKey(name) && File.Exists(Path.Combine(_directory, name)))
            return;
        var next = _sizes.Values.Sum() + plain.Length;
        if (next > _maxBytes)
            throw new SpoolLimitException();
        var nonce = RandomNumberGenerator.GetBytes(12);
        var cipher = new byte[plain.Length];
        var tag = new byte[16];
        using (var aes = new AesGcm(_key, 16))
            aes.Encrypt(nonce, plain, cipher, tag, Encoding.ASCII.GetBytes(name));
        var blob = new byte[HeaderBytes + cipher.Length];
        blob[0] = FormatVersion;
        nonce.CopyTo(blob.AsSpan(1));
        tag.CopyTo(blob.AsSpan(13));
        cipher.CopyTo(blob.AsSpan(HeaderBytes));
        var path = Path.Combine(_directory, name);
        var temp = path + ".tmp";
        File.WriteAllBytes(temp, blob);
        File.Move(temp, path, overwrite: true);
        _sizes[name] = plain.Length;
    }

    public byte[] Read(string snapshotVersion)
    {
        var name = SafeName(snapshotVersion);
        var blob = File.ReadAllBytes(Path.Combine(_directory, name));
        if (blob.Length < HeaderBytes || blob[0] != FormatVersion)
            throw new InvalidDataException("Spool entry is not readable.");
        var plain = new byte[blob.Length - HeaderBytes];
        using var aes = new AesGcm(_key, 16);
        aes.Decrypt(blob.AsSpan(1, 12), blob.AsSpan(HeaderBytes), blob.AsSpan(13, 16), plain, Encoding.ASCII.GetBytes(name));
        return plain;
    }

    public bool Contains(string snapshotVersion) =>
        File.Exists(Path.Combine(_directory, SafeName(snapshotVersion)));

    public void Remove(string snapshotVersion)
    {
        var name = SafeName(snapshotVersion);
        File.Delete(Path.Combine(_directory, name));
        _sizes.Remove(name);
    }

    public int Used => _sizes.Count;

    public void Retain(IEnumerable<string> snapshotVersions)
    {
        var keep = snapshotVersions.Select(SafeName).ToHashSet(StringComparer.Ordinal);
        foreach (var name in _sizes.Keys.Where(n => !keep.Contains(n)).ToArray())
        {
            File.Delete(Path.Combine(_directory, name));
            _sizes.Remove(name);
        }
    }

    private static bool IsEntryName(string name) =>
        name.Length == 64 && name.All(c => c is >= '0' and <= '9' or >= 'a' and <= 'f');

    private static string SafeName(string snapshotVersion)
    {
        if (snapshotVersion.Length != 64 || snapshotVersion.Any(c => c is not (>= '0' and <= '9' or >= 'a' and <= 'f' or >= 'A' and <= 'F')))
            throw new ArgumentException("Snapshot version is not a sha256 hex string.", nameof(snapshotVersion));
        return snapshotVersion.ToLowerInvariant();
    }
}
