"""Vendor HLS.js at image build time; no public CDN needed during operation."""
from pathlib import Path
from urllib.request import urlopen

root = Path('/app/app/static')
for remote, local in [('dist/hls.min.js', 'hls.min.js'), ('LICENSE', 'HLS-LICENSE.txt')]:
    with urlopen('https://cdn.jsdelivr.net/npm/hls.js@1.6.13/' + remote, timeout=60) as response:
        content = response.read()
    if not content:
        raise RuntimeError('Empty frontend dependency')
    (root / local).write_bytes(content)
