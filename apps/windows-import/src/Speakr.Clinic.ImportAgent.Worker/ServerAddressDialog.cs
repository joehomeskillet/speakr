#if IMPORT_WINDOWS_DIALOG
using System.Drawing;
using System.Reflection;
using System.Runtime.Versioning;
using System.Text.Json;
using System.Windows.Forms;

namespace Speakr.Clinic.ImportAgent.Worker;

[SupportedOSPlatform("windows")]
internal static class ServerAddressDialog
{
    private const string Title = "Speakr Import-Agent";

    public static int Show(ServerAddressSettings settings, Func<AgentOptions> readDeployment)
    {
        Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
        Application.EnableVisualStyles();
        AgentOptions deployment;
        ApprovedServer? previous;
        try
        {
            deployment = readDeployment();
            previous = settings.Read();
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException
            or InvalidDataException or InvalidOperationException or FormatException)
        {
            MessageBox.Show("Die lokale Konfiguration konnte nicht gelesen werden.", Title,
                MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        using var form = new Form
        {
            Text = Title, StartPosition = FormStartPosition.CenterScreen, FormBorderStyle = FormBorderStyle.FixedDialog,
            MaximizeBox = false, MinimizeBox = false, AutoScaleMode = AutoScaleMode.Font,
            AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink
        };
        var layout = new TableLayoutPanel
        {
            AutoSize = true, Dock = DockStyle.Fill, ColumnCount = 1, Padding = new Padding(16)
        };
        var version = typeof(Program).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion;
        if (version?.Contains("-preview", StringComparison.OrdinalIgnoreCase) == true)
            layout.Controls.Add(new Label { AutoSize = true, Text = "Unsignierte Entwicklungsversion" });
        layout.Controls.Add(new Label { AutoSize = true, Text = "&HTTPS-Serveradresse" });
        var address = new TextBox { Width = 510, AccessibleName = "HTTPS-Serveradresse" };
        try { address.Text = AgentOptions.ParseServerAddress(previous?.ServerBaseUrl ?? deployment.ServerBaseUrl).AbsoluteUri; }
        catch (InvalidOperationException) { /* Never display malformed deployment settings or embedded credentials. */ }
        layout.Controls.Add(address);
        layout.Controls.Add(new Label
        {
            AutoSize = true, MaximumSize = new Size(510, 0), Margin = new Padding(3, 8, 3, 10),
            Text = "Der Importdienst verwendet die gespeicherte Adresse ab seinem nächsten Start. " +
                   "Beim Speichern wird keine Verbindung aufgebaut."
        });
        var actions = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill, AutoSize = true, FlowDirection = FlowDirection.RightToLeft
        };
        var cancel = new Button { Text = "&Abbrechen", AutoSize = true, DialogResult = DialogResult.Cancel };
        var save = new Button { Text = "&Speichern", AutoSize = true };
        actions.Controls.Add(cancel);
        actions.Controls.Add(save);
        layout.Controls.Add(actions);
        form.Controls.Add(layout);
        form.AcceptButton = save;
        form.CancelButton = cancel;
        form.Shown += (_, _) => address.Focus();
        save.Click += (_, _) =>
        {
            try
            {
                var target = AgentOptions.ParseServerAddress(address.Text);
                var scope = string.IsNullOrEmpty(deployment.AgentId)
                    ? "Nach dem Einrichten der Importstation müssen Sie den Zielserver nochmals bestätigen."
                    : $"Importstation: {deployment.AgentId}";
                var decision = MessageBox.Show(form,
                    $"Der Importdienst darf seine Maschinenidentität (mTLS) beim nächsten Start an diesen Server senden:\n\n{target.AbsoluteUri}\n\n{scope}\n\nDiesen Zielserver speichern?",
                    "Zielserver bestätigen", MessageBoxButtons.YesNo, MessageBoxIcon.Warning,
                    MessageBoxDefaultButton.Button2);
                if (decision != DialogResult.Yes)
                    return;
                settings.Save(previous, deployment.AgentId, target, confirmed: true);
                form.DialogResult = DialogResult.OK;
            }
            catch (InvalidOperationException ex)
            {
                MessageBox.Show(form, ex.Message, Title, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                address.Focus();
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or InvalidDataException)
            {
                MessageBox.Show(form, "Speichern fehlgeschlagen. Prüfen Sie die Schreibberechtigung im Installationsordner " +
                    "und öffnen Sie die Einstellungen erneut.", Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        };
        form.ShowDialog();
        return 0;
    }
}
#endif
