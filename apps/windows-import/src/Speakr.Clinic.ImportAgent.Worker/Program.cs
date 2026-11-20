using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Hosting.WindowsServices;
using Microsoft.Extensions.Options;
using Speakr.Clinic.ImportAgent;

namespace Speakr.Clinic.ImportAgent.Worker;

public static class Program
{
    [STAThread]
    public static int Main(string[] args)
    {
        var settings = new ServerAddressSettings(AppContext.BaseDirectory);
        if (args is ["--configure"] || (args.Length == 0 && OperatingSystem.IsWindows()
            && Environment.UserInteractive && !WindowsServiceHelpers.IsWindowsService()))
        {
#if IMPORT_WINDOWS_DIALOG
            if (OperatingSystem.IsWindows())
                return ServerAddressDialog.Show(settings, ReadDeployment);
#endif
            Console.Error.WriteLine("Der Einstellungsdialog benötigt Windows.");
            return 1;
        }
        if (args.Contains("--configure"))
            return 1;
        if (args.Length > 0 && args[0] == "--run")
            args = args[1..];
        using var host = BuildHost(args, settings);
        try
        {
            if (OperatingSystem.IsWindows())
            {
                var store = host.Services.GetRequiredService<ISecretStore>();
                _ = store.GetMachineCredential();
                _ = store.GetSpoolKey();
                _ = store.GetIntentKey();
            }
            host.Run();
            return 0;
        }
        catch (SecretStoreException ex)
        {
            var text = "Speakr clinic import agent stopped. " + ex.Message;
            Console.Error.WriteLine(text);
            WindowsEventLog.Write(text);
            return 1;
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException or UnauthorizedAccessException
            or System.Text.Json.JsonException or InvalidDataException)
        {
            const string text = "Der Import-Agent konnte seine lokale Konfiguration nicht verwenden. " +
                "Prüfen Sie die Konfiguration und bestätigen Sie den Zielserver für diese Station mit --configure.";
            Console.Error.WriteLine(text);
            WindowsEventLog.Write(text);
            return 1;
        }
    }

    private static AgentOptions ReadDeployment()
    {
        var config = Host.CreateApplicationBuilder(new HostApplicationBuilderSettings { ContentRootPath = AppContext.BaseDirectory })
            .Configuration;
        return new AgentOptions
        {
            AgentId = config["ImportAgent:AgentId"] ?? "",
            ServerBaseUrl = config["ImportAgent:ServerBaseUrl"] ?? ""
        };
    }

    private static IHost BuildHost(string[] args, ServerAddressSettings settings)
    {
        var builder = Host.CreateApplicationBuilder(args);
        builder.Services.AddWindowsService(options => options.ServiceName = "SpeakrClinicImportAgent");
        builder.Services.Configure<AgentOptions>(builder.Configuration.GetSection("ImportAgent"));
        builder.Services.PostConfigure<AgentOptions>(options =>
            options.ServerBaseUrl = settings.ForWorker(options));
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
            if (credential.AgentId != options.AgentId)
                throw new SecretStoreException("Die Maschinenidentität gehört zu einer anderen Importstation.");
            var handler = HttpImportServer.CreateHandler(credential);
            return new HttpClient(handler, disposeHandler: true)
            {
                BaseAddress = options.RequireHttpsServer(),
                Timeout = TimeSpan.FromSeconds(options.HttpTimeoutSeconds)
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
                services.GetRequiredService<IImportLog>(),
                services.GetRequiredService<ISecretStore>().GetIntentKey());
        });
        builder.Services.AddHostedService<ImportWorker>();
        return builder.Build();
    }
}
