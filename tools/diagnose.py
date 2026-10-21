"""Collect local, read-only Streambridge diagnostics without exposing credentials."""
import json
import re
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
REPORT = PROJECT / 'test-results' / 'streambridge-diagnostics.txt'
REPORT.parent.mkdir(parents=True, exist_ok=True)
secrets = []
for line in (PROJECT / '.env').read_text().splitlines():
    if line.startswith(('ADMIN_PASSWORD=', 'INGEST_PASSWORD=')):
        secrets.append(line.split('=', 1)[1])

def redact(value):
    for secret in secrets:
        if secret:
            value = value.replace(secret, '[REDACTED]')
    value = re.sub(r'(rtmps?://|rtsps?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', value)
    value = re.sub(r'(?i)([?&](?:pass|password|key|token)=)[^\s&\"]+', r'\1[REDACTED]', value)
    return value

sections = []
def run(title, args, stdin=None, timeout=30):
    try:
        result = subprocess.run(args, input=stdin, text=True, capture_output=True, cwd=PROJECT, timeout=timeout)
        output = result.stdout + result.stderr
        sections.append(title + '\n' + redact(output) + '\nExit code: ' + str(result.returncode))
        return result.returncode
    except (subprocess.SubprocessError, OSError) as error:
        sections.append(title + '\n' + redact(str(error)))
        return 1

compose = ['docker', 'compose']
run('Container status', compose + ['ps', '--all'])
run('Service startup logs', compose + ['logs', '--no-color', '--tail', '70', 'relay', 'media'])
test_compose = compose + ['-f', 'compose.yaml', '-f', 'compose.test.yaml']
run('Private test receiver logs', test_compose + ['logs', '--no-color', '--tail', '160', 'testreceiver'])
run('Synthetic publisher logs', test_compose + ['logs', '--no-color', '--tail', '40', 'test-desk', 'test-irl'])
probe = r'''
import json, pathlib
try:
    from app.obs import OBS
    client = OBS()
    client.connect()
    for kind, data in [
        ('GetVersion', {}), ('GetStreamStatus', {}), ('GetVideoSettings', {}),
        ('GetSceneList', {}), ('GetInputList', {}), ('GetInputKindList', {}),
    ]:
        try:
            result = client.call(kind, **data)
            if kind == 'GetSceneList':
                result = {'scenes': [s['sceneName'] for s in result['scenes']]}
            print(kind, json.dumps(result))
        except Exception as e:
            print(kind, type(e).__name__, str(e))
    client.close()
except Exception as e:
    print('OBS connection:', type(e).__name__, str(e))
p = pathlib.Path('/data/obs.log')
if p.exists():
    print('OBS log (last 120 lines)')
    print('\n'.join(p.read_text(errors='replace').splitlines()[-120:]))
'''
run('Encoder diagnostics', compose + ['exec', '-T', 'relay', 'python', '-'], probe)
REPORT.write_text('\n\n'.join(sections) + '\n')
REPORT.chmod(0o600)
print('Diagnostics saved. Tell ChatGPT the command has finished.')
print(REPORT)
