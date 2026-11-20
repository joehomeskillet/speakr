using Speakr.Clinic.ImportAgent.Worker;

namespace Speakr.Clinic.ImportAgent.Tests;

public class ServerAddressTests
{
    [Theory]
    [InlineData("http://example.invalid")]
    [InlineData("/relative/path")]
    [InlineData("https://user:pass@example.invalid")]
    [InlineData("https://@example.invalid")]
    [InlineData("https://example.invalid?q=token")]
    [InlineData("https://example.invalid#fragment")]
    [InlineData("https://example.invalid/\\elsewhere")]
    public void Dialog_and_worker_refuse_non_https_or_embedded_credentials(string value)
    {
        Assert.Throws<InvalidOperationException>(() => AgentOptions.ParseServerAddress(value));
        Assert.Throws<InvalidOperationException>(() => new AgentOptions { ServerBaseUrl = value }.RequireHttpsServer());
    }

    [Fact]
    public void Reopening_reads_exactly_the_approved_target_and_station()
    {
        var folder = Temp.Dir();
        try
        {
            var settings = new ServerAddressSettings(folder);
            var station = new AgentOptions { ServerBaseUrl = "https://original.invalid", AgentId = "station-1" };
            var target = AgentOptions.ParseServerAddress("https://new.invalid:8443/clinic");
            Assert.Equal(station.ServerBaseUrl, settings.ForWorker(station));
            Assert.Empty(Directory.EnumerateFileSystemEntries(folder));
            Assert.Throws<InvalidOperationException>(() => settings.Save(null, station.AgentId, target, confirmed: false));
            Assert.Null(settings.Read());
            settings.Save(null, station.AgentId, target, confirmed: true);
            var reopened = new ServerAddressSettings(folder);
            Assert.Equal("https://new.invalid:8443/clinic/", reopened.ForWorker(station));
            Assert.Equal("https://new.invalid:8443/clinic/api/clinic/v1/agent/inventory",
                new Uri(new Uri(reopened.ForWorker(station)), "api/clinic/v1/agent/inventory").AbsoluteUri);
            Assert.Throws<IOException>(() => settings.Save(null, station.AgentId, target, confirmed: true));
            station.AgentId = "station-2";
            Assert.Throws<InvalidOperationException>(() => reopened.ForWorker(station));
            reopened.Save(reopened.Read(), station.AgentId, target, confirmed: true);
            Assert.Equal(target.AbsoluteUri, reopened.ForWorker(station));
            Assert.Single(Directory.EnumerateFiles(folder));
        }
        finally { Temp.Delete(folder); }
    }

    [Fact]
    public void An_unprovisioned_preview_needs_approval_after_machine_setup()
    {
        var folder = Temp.Dir();
        try
        {
            var settings = new ServerAddressSettings(folder);
            var target = AgentOptions.ParseServerAddress("https://preview.invalid");
            settings.Save(null, "", target, confirmed: true);
            Assert.Equal(target.AbsoluteUri, settings.Read()?.ServerBaseUrl);
            Assert.Throws<InvalidOperationException>(() => settings.ForWorker(new AgentOptions { AgentId = "station-1" }));
            Assert.Throws<InvalidOperationException>(() => settings.ForWorker(new AgentOptions()));
        }
        finally { Temp.Delete(folder); }
    }
}
