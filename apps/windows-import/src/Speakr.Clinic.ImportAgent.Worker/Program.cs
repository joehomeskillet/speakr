using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Options;
using Speakr.Clinic.ImportAgent;

namespace Speakr.Clinic.ImportAgent.Worker;

public static class Program
{
    public static void Main(string[] args)
    {
        var builder = Host.CreateApplicationBuilder(args);
        builder.Services.AddWindowsService(options => options.ServiceName = "SpeakrClinicImportAgent");
        builder.Services.Configure<AgentOptions>(builder.Configuration.GetSection("ImportAgent"));
        builder.Services.AddSingleton<ISecretStore>(services =>
        {
            var options = services.GetRequiredService<IOptions<AgentOptions>>().Value;
            if (!OperatingSystem.IsWindows())
                return new RefusingSecretStore();
            return new DpapiFileSecretStore(options.SecretFilePath);
        });
        builder.Services.AddSingleton(services =>
        {
            var options = services.GetRequiredService<IOptions<AgentOptions>>().Value;
            var credential = services.GetRequiredService<ISecretStore>().GetMachineCredential();
            var handler = HttpImportServer.CreateHandler(credential);
            return new HttpClient(handler, disposeHandler: true)
            {
                BaseAddress = new Uri(options.ServerBaseUrl)
            };
        });
        builder.Services.AddSingleton<IImportServer>(services => new HttpImportServer(services.GetRequiredService<HttpClient>()));
        builder.Services.AddSingleton<IImportLog, LoggerImportLog>();
        builder.Services.AddSingleton(services =>
        {
            var options = services.GetRequiredService<IOptions<AgentOptions>>().Value;
            return ImportOrchestrator.Create(
                options,
                services.GetRequiredService<IImportServer>(),
                services.GetRequiredService<ISecretStore>().GetSpoolKey(),
                services.GetRequiredService<IImportLog>());
        });
        builder.Services.AddHostedService<ImportWorker>();
        builder.Build().Run();
    }
}
