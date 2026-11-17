namespace Speakr.Clinic.ImportAgent;

public sealed class StabilityTracker
{
    private readonly TimeSpan _gap;
    private readonly int _required;
    private readonly Dictionary<string, Sample> _samples = new(StringComparer.Ordinal);

    public StabilityTracker(TimeSpan gap, int requiredSamples)
    {
        if (requiredSamples < 1)
            throw new ArgumentOutOfRangeException(nameof(requiredSamples));
        _gap = gap;
        _required = requiredSamples;
    }

    public bool Observe(string path, long size, DateTimeOffset mtimeUtc, DateTimeOffset now)
    {
        if (!_samples.TryGetValue(path, out var sample) || sample.Size != size || sample.MtimeUtc != mtimeUtc)
        {
            _samples[path] = new Sample(size, mtimeUtc, 1, now);
            return _required <= 1;
        }

        if (now - sample.Last < _gap)
            return sample.Count >= _required;
        sample.Count++;
        sample.Last = now;
        return sample.Count >= _required;
    }

    public void Reset(string path) => _samples.Remove(path);

    private sealed class Sample(long size, DateTimeOffset mtimeUtc, int count, DateTimeOffset last)
    {
        public long Size { get; } = size;
        public DateTimeOffset MtimeUtc { get; } = mtimeUtc;
        public int Count { get; set; } = count;
        public DateTimeOffset Last { get; set; } = last;
    }
}
