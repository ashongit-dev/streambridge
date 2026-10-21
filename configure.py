"""Render MediaMTX credentials to a private local file; requires only Python 3."""
import json
import os
from pathlib import Path


def configure(root=None):
    root = Path(root or Path(__file__).resolve().parent)
    values = dict(line.split('=', 1) for line in (root / '.env').read_text().splitlines()
                  if '=' in line and not line.lstrip().startswith('#'))
    password = values.get('INGEST_PASSWORD', '')
    if len(password) < 16:
        raise ValueError('Set INGEST_PASSWORD to a random value of at least 16 characters in .env')
    users = [
        {'user': 'broadcaster', 'pass': password, 'ips': [], 'permissions': [
            {'action': 'publish', 'path': 'desk'}, {'action': 'publish', 'path': 'irl'}]},
        {'user': 'relay', 'pass': password, 'ips': [], 'permissions': [
            {'action': 'read'}, {'action': 'api'}]},
    ]
    runtime = root / '.runtime'
    runtime.mkdir(mode=0o700, exist_ok=True)
    config = (root / 'mediamtx.yml').read_text().rstrip() + '\n\nauthMethod: internal\nauthInternalUsers: ' + json.dumps(users) + '\n'
    target = runtime / 'mediamtx.yml'
    # Keep the inode so an existing Docker bind mount sees a credentials update.
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(config)
    target.chmod(0o600)
    return target


if __name__ == '__main__':
    configure()
    print('Private MediaMTX configuration is ready.')
