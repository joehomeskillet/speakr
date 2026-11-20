"""Build the unsigned win-x64 preview with the .NET 10 SDK and public NuGet packages."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

VERSION = "0.11.1-preview.1"
FILENAME = "Speakr-Import-win-x64-preview.exe"
MARKER = "UNSIGNED-DEVELOPMENT.txt"
MANIFEST = "agent-package-manifest.json"
NOTICES = "THIRD-PARTY-NOTICES.txt"
PROJECT = Path("src/Speakr.Clinic.ImportAgent.Worker/Speakr.Clinic.ImportAgent.Worker.csproj")


def checksum(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def dependency_notices(project):
    """Retain copyright and license texts from the actual resolved packages."""
    assets = json.loads((project.parent / "obj/project.assets.json").read_text(encoding="utf-8-sig"))
    packages = {key.lower() for key, item in assets["libraries"].items() if item["type"] == "package"}
    for framework in assets["project"]["frameworks"].values():
        for dep in framework.get("downloadDependencies", []):
            version = dep["version"].strip("[]").split(",")[0].strip()
            packages.add(f"{dep['name']}/{version}".lower())
    notices = []
    for package in sorted(packages):
        directory = next((Path(root) / package for root in assets["packageFolders"]
                          if (Path(root) / package).is_dir()), None)
        if directory is None:
            continue
        for file in sorted(directory.iterdir()):
            name = file.name.upper().replace("-", "").replace("_", "")
            if file.is_file() and (name.startswith(("LICENSE", "LICENCE", "THIRDPARTYNOTICE", "COPYRIGHT"))):
                notices.append(f"{package} / {file.name}\n\n" + file.read_text(encoding="utf-8-sig"))
    if not any("microsoft.windowsdesktop.app.runtime.win-x64/" in text for text in notices):
        raise RuntimeError("The Windows Desktop runtime license is missing from the resolved packages.")
    return "\n\n".join(notices) + "\n"


def publish(output):
    root = Path(__file__).resolve().parents[1]
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose an empty output directory.")
    edition = subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip()
    settings = ["-p:RuntimeIdentifier=win-x64", "-p:SelfContained=true", "-p:PublishSingleFile=true",
                "-p:IncludeNativeLibrariesForSelfExtract=true", "-p:EnableCompressionInSingleFile=true",
                "-p:DebugType=embedded", f"-p:InformationalVersion={VERSION}"]
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        # Compile the exact commit named in the public manifest.
        archive = stage / "source.zip"
        subprocess.run(["git", "-C", root, "archive", "--format=zip", "-o", archive,
                        f"{edition}:apps/windows-import", "src", "Directory.Build.props"], check=True)
        source = stage / "source"
        with zipfile.ZipFile(archive) as files:
            files.extractall(source)
        project = source / PROJECT
        settings += [f"-p:PathMap={source}=/_/src", f"-p:SourceRevisionId={edition}"]
        config = stage / "NuGet.Config"
        config.write_text('<configuration><packageSources><clear/>'
                          '<add key="nuget.org" value="https://api.nuget.org/v3/index.json"/>'
                          '</packageSources></configuration>', encoding="utf-8")
        env = {**os.environ, "DOTNET_CLI_TELEMETRY_OPTOUT": "1"}
        subprocess.run(["dotnet", "restore", project, "--configfile", config, *settings], check=True, env=env)
        subprocess.run(["dotnet", "publish", project, "-c", "Release", "--no-restore",
                        "-o", stage / "publish", *settings], check=True, env=env)
        output.mkdir(parents=True, exist_ok=True)
        notices = dependency_notices(project)
        shutil.copy2(stage / "publish" / (project.stem + ".exe"), output / FILENAME)
        (output / NOTICES).write_text(notices, encoding="utf-8")
    (output / MARKER).write_text("Unsignierte Entwicklungsversion. Kein signiertes Release.\n", encoding="utf-8")
    files = {name: checksum(output / name) for name in (FILENAME, MARKER, NOTICES)}
    manifest = {"package_id": "speakr-clinic-import-agent", "version": VERSION,
                "channel": "unsigned-development", "distribution_marker": "UNSIGNED-DEVELOPMENT",
                "source_commit": edition, "runtime_identifier": "win-x64", "self_contained": True,
                "signed": False, "files": [{"path": name, "sha256": sha} for name, sha in files.items()]}
    (output / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    files[MANIFEST] = checksum(output / MANIFEST)
    (output / "SHA256SUMS.txt").write_text("".join(f"{sha}  {name}\n" for name, sha in files.items()),
                                          encoding="utf-8", newline="\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new or empty distribution directory")
    publish(parser.parse_args().output)
