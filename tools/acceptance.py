"""Rebuild Streambridge and test it exclusively against a private Docker receiver."""
import base64
import json
import os
from pathlib import Path
import runpy
import statistics
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
REPORT = PROJECT / 'test-results' / 'acceptance-results.json'
REPORT.parent.mkdir(parents=True, exist_ok=True)
BASE = ['docker', 'compose']
TEST = BASE + ['-f', 'compose.yaml', '-f', 'compose.test.yaml']
values = dict(line.split('=', 1) for line in (PROJECT / '.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
AUTH = 'Basic ' + base64.b64encode(('admin:' + values['ADMIN_PASSWORD']).encode()).decode()
results = []
started_output = False
initial = None
connection_id = None
last_bytes = None
cleanup_errors = []


def command(args, capture=False, timeout=600):
    return subprocess.run(args, cwd=PROJECT, check=True, stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None, timeout=timeout)


def api(path='/api/status', data=None):
    headers = {'Authorization': AUTH}
    if data is not None:
        headers.update({'Content-Type': 'application/json', 'X-Relay-Request': '1'})
    request = Request('http://127.0.0.1:8080' + path, data=None if data is None else json.dumps(data).encode(), headers=headers)
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def record(name, detail=None):
    results.append({'test': name, 'passed': True, 'detail': detail})
    print('PASS:', name, flush=True)


def wait_for(predicate, name, timeout=75, continuity=False):
    deadline = time.monotonic() + timeout
    status = {'error': 'dashboard unreachable'}
    while time.monotonic() < deadline:
        try:
            status = api()
        except (URLError, TimeoutError, ConnectionError):
            if continuity:
                raise RuntimeError('Dashboard disconnected during continuity test')
            time.sleep(2)
            continue
        if continuity:
            check_continuity(status)
        if predicate(status):
            record(name)
            return status
        time.sleep(2)
    raise RuntimeError(name + ' timed out; encoder diagnostic: ' + status.get('error', 'no response'))


def receiver():
    code = "import requests,json; r=requests.get('http://testreceiver:9997/v3/paths/list',timeout=5);r.raise_for_status(); print(json.dumps([{'name':p['name'],'ready':p['ready'],'source':p.get('source'),'tracks':p.get('tracks')} for p in (r.json().get('items') or []) if p['name']=='live/check']))"
    result = command(BASE + ['exec', '-T', 'relay', 'python', '-c', code], capture=True, timeout=15)
    items = json.loads(result.stdout)
    return items[0] if items else None


def wait_for_receiver_api():
    deadline = time.monotonic() + 30
    last_error = None
    while time.monotonic() < deadline:
        try:
            receiver()
            record('Local receiver API is ready')
            return
        except subprocess.CalledProcessError as error:
            last_error = error
        time.sleep(1)
    if last_error:
        raise last_error
    raise RuntimeError('Local receiver API did not start')



def verify_media_authentication():
    code = """
import os, requests
url = 'http://media:9997/v3/config/global/get'
r = requests.get(url, auth=('relay', os.environ['INGEST_PASSWORD']), timeout=5)
r.raise_for_status()
users = r.json()['authInternalUsers']
assert {u['user'] for u in users} == {'relay', 'broadcaster'}, 'Unexpected configured users'
assert all(u['pass'] == os.environ['INGEST_PASSWORD'] for u in users), 'Configured password mismatch'
assert requests.get(url, timeout=5).status_code == 401, 'Anonymous API access should be denied'
assert requests.get(url, auth=('relay', 'intentionally-wrong-test-password'), timeout=5).status_code == 401, 'Incorrect password should be denied'
print('Configured credentials accepted; anonymous and incorrect credentials rejected.')
"""
    command(BASE + ['exec', '-T', 'relay', 'python', '-c', code], capture=True, timeout=20)
    record('Feed server authentication accepts configured credentials and denies anonymous/wrong credentials')


def check_continuity(status=None):
    global last_bytes
    status = status or api()
    if not status['connected'] or not status['streaming'] or status['reconnecting']:
        raise RuntimeError('Output was not continuously active')
    if last_bytes is not None and status['output_bytes'] < last_bytes:
        raise RuntimeError('Output byte counter reset')
    last_bytes = status['output_bytes']
    observed = receiver()
    if not observed or not observed['ready'] or observed.get('source', {}).get('id') != connection_id:
        raise RuntimeError('Receiver publishing connection changed or disappeared')



def preview(source):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            path = '/preview/' + source + '/index.m3u8'
            for _ in range(3):
                with urlopen(Request('http://127.0.0.1:8080' + path, headers={'Authorization': AUTH}), timeout=10) as response:
                    playlist = response.read().decode()
                if '#EXTM3U' not in playlist:
                    raise ValueError('Not an HLS playlist')
                assets = [line.strip() for line in playlist.splitlines() if line.strip() and not line.startswith('#')]
                if not assets:
                    raise ValueError('No preview assets yet')
                asset = assets[0]
                if '/' in asset or '..' in asset or '?' in asset:
                    raise ValueError('Unexpected preview asset path')
                path = '/preview/' + source + '/' + asset
                if asset.endswith('.ts'):
                    with urlopen(Request('http://127.0.0.1:8080' + path, headers={'Authorization': AUTH}), timeout=10) as response:
                        data = response.read()
                    if not data or data[0] != 0x47:
                        raise ValueError('Invalid MPEG-TS preview segment')
                    record(source + ' preview playlist and video segment load')
                    return
        except (URLError, TimeoutError, ValueError):
            pass
        check_continuity()
        time.sleep(2)
    raise RuntimeError(source + ' HLS preview did not become playable')


def picture(expected):
    result = command(BASE + ['exec', '-T', 'relay', 'ffmpeg', '-v', 'error', '-rtsp_transport', 'tcp',
                            '-i', 'rtsp://testreceiver:8554/live/check', '-an', '-frames:v', '45', '-vf', 'scale=1:1',
                            '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'], capture=True, timeout=25)
    if len(result.stdout) != 45 * 3:
        raise RuntimeError('Could not decode 45 consecutive output video frames')
    samples = [list(result.stdout[i:i+3]) for i in range(0, len(result.stdout), 3)]
    # Ignore the initial transition/decoder startup; require the final half
    # second of the received sequence to show the expected source.
    steady = samples[-15:]
    r, g, b = [int(statistics.median(frame[channel] for frame in steady)) for channel in range(3)]
    red = r > 150 and r > 2 * max(g, b)
    blue = b > 150 and b > 2 * max(r, g)
    if expected == 'red' and not red or expected == 'blue' and not blue or expected == 'fallback' and (red or blue):
        status = api()
        raise RuntimeError('Unexpected output picture for ' + expected + '; final RGB samples=' + json.dumps(steady)
                           + '; selected=' + status['active'] + '; input health=' + json.dumps(status['inputs']))
    def matches(frame):
        red = frame[0] > 150 and frame[0] > 2 * max(frame[1], frame[2])
        blue = frame[2] > 150 and frame[2] > 2 * max(frame[0], frame[1])
        return red if expected == 'red' else blue if expected == 'blue' else not red and not blue
    if not all(matches(frame) for frame in steady):
        raise RuntimeError('Output did not settle to ' + expected + '; final RGB samples=' + json.dumps(steady))
    check_continuity()
    record('Receiver picture: ' + expected, {'median_rgb': [r, g, b], 'consecutive_matching_frames': len(steady)})


def route(source, automatic=True):
    api('/api/route', {'preferred': source, 'automatic': automatic})
    wait_for(lambda s: s['active'] == source, 'Switch to ' + source, continuity=True)


try:
    # Refuse to interrupt an existing output or take over connected real publishers.
    try:
        initial = api()
        if initial['streaming'] or initial['want_live'] or any(v['ready'] for v in initial['inputs'].values()):
            raise RuntimeError('Stop the existing output/publishers before using synthetic acceptance tests')
    except URLError:
        pass
    command([sys.executable, str(PROJECT / 'configure.py')])
    print('Building the updated encoder image...', flush=True)
    command(BASE + ['build', 'relay'])
    record('Docker image builds')
    command(BASE + ['run', '--rm', '--no-deps', '--entrypoint', 'python', '-v', str(PROJECT / 'tests') + ':/app/tests:ro',
                    'relay', '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
    record('Core and API unit tests')
    command(BASE + ['up', '-d', 'media', 'relay'])
    wait_for(lambda s: s['connected'], 'Encoder connects')
    verify_media_authentication()
    command(TEST + ['up', '-d', 'testreceiver'])
    wait_for_receiver_api()
    api('/api/route', {'preferred': 'desk', 'automatic': True})
    api('/api/output/start', {'server': 'rtmp://testreceiver:1935/live', 'key': 'check'})
    started_output = True
    wait_for(lambda s: s['streaming'], 'Local RTMP output starts')
    for _ in range(15):
        observed = receiver()
        if observed and observed['ready']:
            break
        time.sleep(1)
    else:
        raise RuntimeError('Local receiver did not receive the output')
    connection_id = observed.get('source', {}).get('id')
    if not connection_id or not any(any(codec in ''.join(c for c in str(t).upper() if c.isalnum()) for codec in ('AAC', 'MPEG4AUDIO')) for t in observed.get('tracks', [])):
        raise RuntimeError('Receiver is missing a publishing connection ID or AAC audio')
    record('Receiver has video and AAC audio', {'tracks': observed['tracks']})
    picture('fallback')
    print('Checking fallback stays live for 60 seconds...', flush=True)
    before = api()['output_bytes']
    until = time.monotonic() + 60
    while time.monotonic() < until:
        check_continuity()
        time.sleep(2)
    if api()['output_bytes'] <= before:
        raise RuntimeError('Fallback output did not advance')
    record('Fallback stays live for 60 seconds')
    command(TEST + ['up', '-d', 'test-desk', 'test-irl'])
    wait_for(lambda s: all(v['healthy'] for v in s['inputs'].values()), 'Both test inputs become healthy', continuity=True)
    wait_for(lambda s: s['active'] == 'desk', 'Desk replaces fallback', continuity=True)
    picture('red')
    preview('desk')
    preview('irl')
    route('irl')
    picture('blue')
    route('desk')
    command(TEST + ['stop', 'test-desk'])
    wait_for(lambda s: s['active'] == 'irl', 'Desk loss selects IRL', continuity=True)
    picture('blue')
    command(TEST + ['stop', 'test-irl'])
    wait_for(lambda s: s['active'] == 'fallback', 'Both feeds lost selects fallback', continuity=True)
    picture('fallback')
    command(TEST + ['up', '-d', 'test-desk', 'test-irl'])
    wait_for(lambda s: s['active'] == 'desk' and all(v['healthy'] for v in s['inputs'].values()), 'Preferred desk recovers', continuity=True)
    picture('red')
    api('/api/route', {'preferred': 'desk', 'automatic': False})
    command(TEST + ['stop', 'test-desk'])
    wait_for(lambda s: s['active'] == 'fallback', 'Manual mode uses fallback despite healthy IRL', continuity=True)
    picture('fallback')
    route('fallback')
    command(TEST + ['up', '-d', 'test-desk'])
    wait_for(lambda s: all(v['healthy'] for v in s['inputs'].values()), 'Inputs return while fallback is forced', continuity=True)
    if api()['active'] != 'fallback':
        raise RuntimeError('Forced fallback was not preserved')
    picture('fallback')
    record('One receiver RTMP connection survived all source changes', {'connection_id': connection_id})
except Exception as error:
    # Subprocess errors do not print argument lists containing publisher secrets.
    if isinstance(error, subprocess.CalledProcessError):
        stderr = error.stderr or b''
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors='replace')
        detail = 'Subprocess exited with code ' + str(error.returncode) + ': ' + stderr[-6000:]
    else:
        detail = str(error) if not isinstance(error, subprocess.SubprocessError) else type(error).__name__
    for value in values.values():
        if len(value) >= 16:
            detail = detail.replace(value, '[REDACTED]')
    results.append({'test': 'Acceptance run', 'passed': False, 'detail': detail})
    print('STOPPED:', detail, flush=True)
finally:
    if started_output:
        try:
            api('/api/output/stop', {})
            record('Test output stopped')
        except Exception:
            cleanup_errors.append('Could not stop test output; use Stop broadcast in the dashboard')
    # Only test services are stopped; do not remove the app or its persistent volume.
    try:
        command(TEST + ['stop', 'test-desk', 'test-irl', 'testreceiver'], capture=True, timeout=40)
    except Exception:
        cleanup_errors.append('Could not stop test services')
    if started_output:
        try:
            api('/api/route', {'preferred': initial['preferred'] if initial else 'desk', 'automatic': initial['automatic'] if initial else True})
        except Exception:
            cleanup_errors.append('Could not restore the original source selection')
    REPORT.write_text(json.dumps({'results': results, 'cleanup_errors': cleanup_errors}, indent=2) + '\n')
    REPORT.chmod(0o600)
    try:
        runpy.run_path(str(PROJECT / 'tools/diagnose.py'), run_name='__main__')
    except Exception:
        print('Additional diagnostics could not be collected.', flush=True)
    print('Results saved. Tell ChatGPT this command has finished.', flush=True)
    print(REPORT)
