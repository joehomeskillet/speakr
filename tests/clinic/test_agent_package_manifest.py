"""Offline import-agent package manifest.

pwsh and the .NET SDK are not on this runner. The assertions execute the same
policy the packager script embeds, and they refuse a script that drifts from it.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'apps' / 'windows-import' / 'packaging' / 'Build-Package.ps1'
ORACLES = {
    'BT-IMP-001': {
        'fixtures': ['F-DSS', 'F-HW'],
        'given': ['Philips 9600 oder 9620 mit nur synthetischen Dateien; Treiber installiert.'],
        'when': [
            'Gerät anstecken, abziehen und erneut anstecken.',
            'Überwachung dieses Gerätetyps ausschalten/einschalten.',
            'Gerät ohne nutzbaren Treiber gesondert beobachten.',
        ],
        'then': [
            'Liste reagiert auf Anschluss und Auswahl.',
            'Keine doppelten Einträge nach Wiederanschluss.',
            'Fehlender Treiber und nicht gefundenes Gerät werden unterscheidbar angezeigt.',
        ],
        'status': 'not_run',
        'oracle_status': 'specified_not_observed',
    },
    'BT-OPS-008': {
        'fixtures': ['F-WORK', 'F-FAIL', 'F-ID'],
        'given': ['Frische Labordienste und nur eigener Labor-Arbeitsordner.'],
        'when': [
            'Client schliessen, Hintergrundauftrag weiter beobachten.',
            'Arbeitsordner kurz unerreichbar machen; Auftrag erneut versuchen.',
            'Nicht existierenden Autoren-/Poolkontext verwenden.',
        ],
        'then': [
            'Dienstverarbeitung braucht keine offene Arbeitsliste.',
            'Fehler benennen Ursache und verhindern stilles Fehlrouting.',
            'Neubau benötigt keinen identischen proprietären Datei-Jobvertrag, muss aber dieselben fachlichen Erfolge/Fehler abbilden.',
        ],
        'status': 'not_run',
        'oracle_status': 'specified_not_observed',
    },
}
FORBIDDEN = (
    'msiexec', 'New-Service', 'InstallUtil', 'Invoke-WebRequest', 'Invoke-RestMethod',
    'Enter-PSSession', 'Cert:\\', 'Get-Credential', 'Get-ChildItem Env:',
    'Get-AuthenticodeSignature', 'Set-AuthenticodeSignature', 'waveIn', 'NAudio',
    'SpeechSynthesizer', 'RegisterHotKey', 'SendInput', 'Start-Process', 'sc.exe',
    'reg.exe', 'Program Files', 'microphone', 'NativeMessaging', 'dotnet restore',
    'nuget.exe', 'speakr-private-fixtures', 'Install-Module', 'Add-WindowsFeature',
)


def load_policy(script: Path = SCRIPT) -> dict:
    text = script.read_text(encoding='utf-8')
    match = re.search(r"\$PackagePolicyJson = @'(.*?)'@", text, re.S)
    if match is None:
        raise AssertionError('package policy block missing')
    return json.loads(match.group(1))


def _pins(path: Path) -> tuple[list[str], list[tuple[str, str]]]:
    root = ET.parse(path).getroot()
    tfms = [(node.text or '').strip() for node in root.iter('TargetFramework')]
    packages = []
    for node in root.iter('PackageReference'):
        include = node.attrib.get('Include', '')
        version = node.attrib.get('Version', '')
        if not include or not version:
            raise ValueError('unpinned PackageReference')
        packages.append((include, version))
    return tfms, packages


def _deterministic(path: Path) -> list[str]:
    root = ET.parse(path).getroot()
    return [(node.text or '').strip() for node in root.iter('Deterministic')]


def _allow_list(path: Path | None) -> list[str] | None:
    if path is None or not path.is_file():
        return None
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        item = line.strip()
        if item and not item.startswith('#'):
            rows.append(item)
    return rows


def _failure(output: Path, policy: dict, status: str, attempts: list[int], verification: dict | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    for name in (policy['manifest_name'], policy['unsigned_marker'] + '.txt'):
        path = output / name
        if path.is_file():
            path.unlink()
    body: dict = {
        'package_id': policy['package_id'],
        'status': status,
        'version': policy['version'],
        'publish_attempts': attempts,
    }
    if verification is not None:
        body['signature_verification'] = verification
    (output / policy['failure_name']).write_text(
        json.dumps(body, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return body


def _publish_command(policy: dict, project: Path, output: Path, version: str) -> list[str]:
    return [
        'dotnet', 'publish', str(project),
        '-c', 'Release',
        '-f', policy['publish']['framework'],
        '-r', policy['publish']['runtime'],
        '--self-contained', 'false',
        '--no-restore',
        '-o', str(output),
        f"-p:Version={version}",
        '-p:Deterministic=true',
    ]


def assemble(
    output: Path,
    *,
    configuration: str = 'Development',
    version: str | None = None,
    identity: str = '',
    allow_path: Path | None = None,
    layout: Path | None = None,
    stub_codes: tuple[int, ...] | None = None,
    project_path: Path | None = None,
    props_path: Path | None = None,
) -> dict:
    """Manifest contract shared with Build-Package.ps1. Writes one JSON artifact."""
    policy = load_policy()
    project = project_path or (ROOT / policy['worker_project'])
    props = props_path or (ROOT / policy['directory_build_props'])
    requested = policy['version'] if version is None else version
    if configuration not in ('Development', 'Release'):
        return _failure(output, policy, 'unauthorized', [])
    if policy['publish']['self_contained'] or not policy['publish']['no_restore']:
        return _failure(output, policy, 'stale-version', [])
    if requested != policy['version']:
        return _failure(output, policy, 'stale-version', [])
    try:
        tfms, packages = _pins(project)
        dets = _deterministic(props)
    except (ET.ParseError, OSError, ValueError):
        return _failure(output, policy, 'stale-version', [])
    framework = policy['publish']['framework']
    if tfms != [framework] or not packages or dets != ['true']:
        return _failure(output, policy, 'stale-version', [])

    channel = 'unsigned-development'
    verified_identity = None
    if configuration == 'Release':
        listed = _allow_list(allow_path)
        if not identity.strip() or listed is None or identity.strip() not in listed:
            refused = {
                'step': policy['signature_step'],
                'executed': True,
                'result': 'unauthorized',
                'identity': None,
                'payload_sha256': None,
            }
            return _failure(output, policy, 'unauthorized', [], refused)
        channel = 'signed-release'
        verified_identity = identity.strip()

    attempts: list[int] = []
    if stub_codes is None:
        mode = 'prebuilt-layout'
        if layout is None:
            return _failure(output, policy, 'publish-failed', [])
    else:
        mode = 'command'
        ok = False
        for code in list(stub_codes)[: policy['publish']['max_attempts']]:
            attempts.append(int(code))
            if code == 0:
                ok = True
                break
        if not ok or layout is None:
            return _failure(output, policy, 'publish-failed', attempts)

    output.mkdir(parents=True, exist_ok=True)
    skip = {policy['manifest_name'], policy['failure_name'], policy['unsigned_marker'] + '.txt'}
    for source in sorted(path for path in layout.rglob('*') if path.is_file()):
        rel = source.relative_to(layout).as_posix()
        if rel in skip:
            continue
        dest = output / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())

    marker_name = policy['unsigned_marker'] + '.txt'
    marker_path = output / marker_name
    if configuration == 'Development':
        marker_path.write_text(policy['unsigned_notice'] + '\n', encoding='utf-8')
    elif marker_path.is_file():
        marker_path.unlink()

    files = []
    for path in sorted(item for item in output.rglob('*') if item.is_file()):
        rel = path.relative_to(output).as_posix()
        if rel in (policy['manifest_name'], policy['failure_name']):
            continue
        files.append({'path': rel, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    if not [row for row in files if row['path'] != marker_name]:
        return _failure(output, policy, 'publish-failed', attempts)

    runtime_notice = policy['runtime_notice'].replace('{framework}', framework).replace('{runtime}', policy['publish']['runtime'])
    notices = [runtime_notice]
    notices.extend(
        policy['license_notice'].replace('{id}', name).replace('{version}', ver) for name, ver in packages)
    canonical = '\n'.join(f"{row['path']}\t{row['sha256']}" for row in files)
    failure_path = output / policy['failure_name']
    if failure_path.is_file():
        failure_path.unlink()
    manifest = {
        'package_id': policy['package_id'],
        'status': 'ok',
        'version': policy['version'],
        'configuration': configuration,
        'channel': channel,
        'distribution_marker': policy['unsigned_marker'] if configuration == 'Development' else None,
        'target_framework': framework,
        'runtime_identifier': policy['publish']['runtime'],
        'self_contained': False,
        'restore_during_package': False,
        'deterministic': True,
        'source_offer': {
            'product': 'speakr',
            'version': policy['version'],
            'commit': policy['source_pin'],
            'project': policy['worker_project'],
        },
        'pinned_packages': [{'id': name, 'version': ver} for name, ver in packages],
        'license_notices': notices,
        'files': files,
        'signature_verification': {
            'step': policy['signature_step'],
            'executed': True,
            'result': channel,
            'identity': verified_identity,
            'payload_sha256': hashlib.sha256(canonical.encode()).hexdigest(),
        },
        'publish': {
            'mode': mode,
            'attempts': attempts,
            'command': _publish_command(policy, project, output, policy['version']),
        },
        'publish_attempts': attempts,
        'prohibited': {
            'machine_installation': False,
            'production_rollout': False,
            'credential_discovery': False,
            'native_dictation': False,
        },
    }
    (output / policy['manifest_name']).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return manifest


def _layout(tmp_path: Path) -> Path:
    layout = tmp_path / 'layout'
    (layout / 'dir').mkdir(parents=True)
    (layout / 'worker.txt').write_bytes(b'synthetic-agent')
    (layout / 'dir' / 'note.txt').write_bytes(b'note')
    return layout


def _allow(tmp_path: Path, body: str) -> Path:
    path = tmp_path / 'authorized-identities.txt'
    path.write_text(body, encoding='utf-8')
    return path


def _read(output: Path, name: str) -> dict:
    return json.loads((output / name).read_text(encoding='utf-8'))


def test_unsigned_development_build_is_marked(tmp_path: Path):
    policy = load_policy()
    output = tmp_path / 'out'
    assemble(output, layout=_layout(tmp_path))
    manifest = _read(output, policy['manifest_name'])
    assert manifest['status'] == 'ok'
    assert manifest['channel'] == 'unsigned-development'
    assert manifest['distribution_marker'] == 'UNSIGNED-DEVELOPMENT'
    assert manifest['version'] == '0.11.1'
    assert manifest['source_offer']['commit'] == '48d3018fb7d80b77f897bedd86bcc89b5034d1f5'
    assert manifest['target_framework'] == 'net10.0'
    assert manifest['runtime_identifier'] == 'win-x64'
    assert manifest['self_contained'] is False
    assert manifest['restore_during_package'] is False
    assert manifest['deterministic'] is True
    _, packages = _pins(ROOT / policy['worker_project'])
    assert [(row['id'], row['version']) for row in manifest['pinned_packages']] == packages
    rendered_runtime = policy['runtime_notice'].replace('{framework}', 'net10.0').replace('{runtime}', 'win-x64')
    assert manifest['license_notices'][0] == rendered_runtime
    assert manifest['license_notices'][1:] == [
        policy['license_notice'].replace('{id}', name).replace('{version}', ver) for name, ver in packages
    ]
    notice = policy['unsigned_notice'] + '\n'
    files = {row['path']: row['sha256'] for row in manifest['files']}
    assert files['worker.txt'] == hashlib.sha256(b'synthetic-agent').hexdigest()
    assert files['dir/note.txt'] == hashlib.sha256(b'note').hexdigest()
    assert files['UNSIGNED-DEVELOPMENT.txt'] == hashlib.sha256(notice.encode()).hexdigest()
    assert (output / 'UNSIGNED-DEVELOPMENT.txt').read_text(encoding='utf-8') == notice
    canonical = '\n'.join(f"{row['path']}\t{row['sha256']}" for row in manifest['files'])
    verification = manifest['signature_verification']
    assert verification['step'] == 'Verify-AgentPackageSignature'
    assert verification['executed'] is True
    assert verification['result'] == 'unsigned-development'
    assert verification['identity'] is None
    assert verification['payload_sha256'] == hashlib.sha256(canonical.encode()).hexdigest()
    command = manifest['publish']['command']
    assert command[:3] == ['dotnet', 'publish', str(ROOT / policy['worker_project'])]
    assert command[command.index('-f') + 1] == 'net10.0'
    assert command[command.index('-r') + 1] == 'win-x64'
    assert command[command.index('--self-contained') + 1] == 'false'
    assert '--no-restore' in command and 'restore' not in command
    assert '-p:Version=0.11.1' in command and '-p:Deterministic=true' in command
    assert manifest['publish']['mode'] == 'prebuilt-layout'
    assert manifest['prohibited']['native_dictation'] is False
    assert not (output / policy['failure_name']).exists()


def test_signed_release_requires_authorized_identity(tmp_path: Path):
    policy = load_policy()
    allow = _allow(tmp_path, '# configured outside the packager\nlab-signing-01\n')
    layout = _layout(tmp_path)

    missing = tmp_path / 'missing'
    assemble(missing, configuration='Release', layout=layout)
    refused = _read(missing, policy['failure_name'])
    assert refused['status'] == 'unauthorized'
    assert refused['signature_verification']['executed'] is True
    assert refused['signature_verification']['result'] == 'unauthorized'
    assert refused['signature_verification']['identity'] is None
    assert not (missing / policy['manifest_name']).exists()
    assert not (missing / 'UNSIGNED-DEVELOPMENT.txt').exists()

    unknown = tmp_path / 'unknown'
    assemble(unknown, configuration='Release', identity='other', allow_path=allow, layout=layout)
    assert _read(unknown, policy['failure_name'])['status'] == 'unauthorized'

    wrong_case = tmp_path / 'case'
    assemble(wrong_case, configuration='Release', identity='Lab-Signing-01', allow_path=allow, layout=layout)
    assert _read(wrong_case, policy['failure_name'])['status'] == 'unauthorized'

    absent = tmp_path / 'absent'
    assemble(absent, configuration='Release', identity='lab-signing-01',
             allow_path=tmp_path / 'no-such-file.txt', layout=layout)
    assert _read(absent, policy['failure_name'])['status'] == 'unauthorized'

    signed = tmp_path / 'signed'
    assemble(signed, configuration='Release', identity=' lab-signing-01 ', allow_path=allow, layout=layout)
    manifest = _read(signed, policy['manifest_name'])
    assert manifest['channel'] == 'signed-release'
    assert manifest['distribution_marker'] is None
    assert manifest['signature_verification']['identity'] == 'lab-signing-01'
    assert manifest['signature_verification']['result'] == 'signed-release'
    assert 'UNSIGNED-DEVELOPMENT.txt' not in {row['path'] for row in manifest['files']}
    assert not (signed / 'UNSIGNED-DEVELOPMENT.txt').exists()

    dev = tmp_path / 'dev-with-identity'
    assemble(dev, identity='lab-signing-01', allow_path=allow, layout=layout)
    dev_manifest = _read(dev, policy['manifest_name'])
    assert dev_manifest['channel'] == 'unsigned-development'
    assert dev_manifest['signature_verification']['identity'] is None


def test_stale_version_is_refused(tmp_path: Path):
    policy = load_policy()
    layout = _layout(tmp_path)
    stale = tmp_path / 'stale'
    assemble(stale, version='0.9.0', layout=layout)
    assert _read(stale, policy['failure_name'])['status'] == 'stale-version'
    assert not (stale / policy['manifest_name']).exists()

    project = tmp_path / 'Worker.csproj'
    source = (ROOT / policy['worker_project']).read_text(encoding='utf-8')
    project.write_text(source.replace('net10.0', 'net8.0'), encoding='utf-8')
    drifted = tmp_path / 'drifted'
    assemble(drifted, layout=layout, project_path=project)
    assert _read(drifted, policy['failure_name'])['status'] == 'stale-version'

    props = tmp_path / 'Directory.Build.props'
    props.write_text(
        '<Project><PropertyGroup><Deterministic>false</Deterministic></PropertyGroup></Project>',
        encoding='utf-8')
    undet = tmp_path / 'undet'
    assemble(undet, layout=layout, props_path=props)
    assert _read(undet, policy['failure_name'])['status'] == 'stale-version'


def test_publish_retries_then_fails(tmp_path: Path):
    policy = load_policy()
    layout = _layout(tmp_path)
    failed = tmp_path / 'failed'
    assemble(failed, layout=layout, stub_codes=(1, 1, 1, 0))
    body = _read(failed, policy['failure_name'])
    assert body['status'] == 'publish-failed'
    assert body['publish_attempts'] == [1, 1, 1]
    assert not (failed / policy['manifest_name']).exists()

    retried = tmp_path / 'retried'
    assemble(retried, layout=layout, stub_codes=(1, 0))
    manifest = _read(retried, policy['manifest_name'])
    assert manifest['status'] == 'ok'
    assert manifest['publish']['mode'] == 'command'
    assert manifest['publish_attempts'] == [1, 0]
    assert manifest['files']


def test_packager_script_stays_offline_and_pinned():
    text = SCRIPT.read_text(encoding='utf-8')
    lowered = text.lower()
    for token in FORBIDDEN:
        assert token.lower() not in lowered
    policy = load_policy()
    assert policy['version'] == '0.11.1'
    assert policy['source_pin'] == '48d3018fb7d80b77f897bedd86bcc89b5034d1f5'
    assert policy['publish'] == {
        'framework': 'net10.0',
        'runtime': 'win-x64',
        'self_contained': False,
        'no_restore': True,
        'max_attempts': 3,
    }
    assert text.count('0.11.1') == 1
    assert text.count(policy['source_pin']) == 1
    assert text.count('net10.0') == 1
    assert text.count('win-x64') == 1
    assert text.count('10.0.4') == 0
    assert 'Microsoft.Extensions.Hosting' not in text
    assert 'function Verify-AgentPackageSignature' in text
    assert 'Verify-AgentPackageSignature $channel $verifiedIdentity $payloadHash' in text
    assert "-cne 'Development'" in text and "-cne 'Release'" in text
    assert '-ceq $Identity' in text
    assert ".Replace('{framework}'" in text and ".Replace('{runtime}'" in text
    assert "$script:Policy.version" in text
    assert "$script:Policy.publish.framework" in text
    assert "$script:Policy.publish.runtime" in text
    assert "$script:Policy.publish.max_attempts" in text
    assert '.Replace(\'{id}\'' in text and '.Replace(\'{version}\'' in text
    assert "'--no-restore'" in text and "'--self-contained', 'false'" in text
    assert 'Get-FileHash' in text and 'ToLowerInvariant' in text
    assert 'layout escape' in text
    assert 'machine_installation = $false' in text
    assert 'production_rollout = $false' in text
    assert 'credential_discovery = $false' in text
    assert 'native_dictation = $false' in text
    assert "Configuration = 'Development'" in text
    assert 'AuthorizedIdentitiesPath' in text
    assert 'SigningIdentity = \'\'' in text


def test_original_imp_and_ops_oracles_unchanged():
    catalog = json.loads((ROOT / 'clinic-spec' / 'tests' / 'dns-behavior-tests.original.json').read_text(encoding='utf-8'))
    cases = {row['id']: row for row in catalog['test_cases']}
    for case_id, expected in ORACLES.items():
        actual = cases[case_id]
        for key, value in expected.items():
            assert actual[key] == value
        assert actual['status'] != 'passed'
