"""Encrypted cloud checkpoints in GitHub Releases; no plaintext data is uploaded."""
import hashlib
import hmac
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

REPO = 'haroldbergner-lang/SGP-Lineup-Tracker'
TAG = 'research-data-2026'
ASSET = 'season-2026-latest.enc'
MAGIC = b'SGPARCHIVE1\n'


def password():
    value = os.environ.get('SGP_ARCHIVE_PASSWORD', '')
    if len(value) < 32:
        raise ValueError('Archive password must be configured with at least 32 characters')
    return value


def openssl(data, decrypt=False):
    password()
    command = ['openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-iter', '200000',
               '-pass', 'env:SGP_ARCHIVE_PASSWORD']
    if decrypt:
        command.append('-d')
    result = subprocess.run(command, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError('Archive encryption/decryption failed')
    return result.stdout


def encrypt(root):
    root = Path(root)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for path in sorted(root.rglob('*')):
            if not path.is_file() or path.is_symlink() or path.name.endswith('.tmp') or path.name == 'collector.lock':
                continue
            content = path.read_bytes()
            if path.suffix == '.csv' and content and not content.endswith(b'\n'):
                content = content.rsplit(b'\n', 1)[0] + b'\n'
            member = tarfile.TarInfo('season-2026/' + str(path.relative_to(root)))
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    encrypted = openssl(buffer.getvalue())
    key = hashlib.sha256(b'SGP archive authentication\0' + password().encode()).digest()
    signature = hmac.new(key, MAGIC + encrypted, hashlib.sha256).digest()
    return MAGIC + signature + encrypted


def restore(data, parent):
    if not data.startswith(MAGIC):
        raise ValueError('Unrecognized archive')
    signature = data[len(MAGIC):len(MAGIC)+32]
    encrypted = data[len(MAGIC)+32:]
    key = hashlib.sha256(b'SGP archive authentication\0' + password().encode()).digest()
    expected = hmac.new(key, MAGIC + encrypted, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError('Archive authentication failed')
    with tarfile.open(fileobj=io.BytesIO(openssl(encrypted, decrypt=True)), mode='r:gz') as archive:
        for member in archive.getmembers():
            if (not member.name.startswith('season-2026/') and member.name != 'season-2026'
                    or Path(member.name).is_absolute() or '..' in Path(member.name).parts
                    or member.issym() or member.islnk()):
                raise ValueError('Unsafe archive member')
        archive.extractall(parent, filter='data')


def gh(args, **kwargs):
    return subprocess.run(['gh', *args, '--repo', REPO], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)


def download(parent):
    with tempfile.TemporaryDirectory() as temporary:
        gh(['release', 'download', TAG, '--pattern', ASSET, '--dir', temporary])
        restore((Path(temporary)/ASSET).read_bytes(), parent)
    return Path(parent)/'season-2026'


def publish(root):
    payload = encrypt(root)
    # Releases preserve the encrypted latest checkpoint beyond Actions artifact expiry.
    # Never upload plaintext CSVs or raw quote responses to the public repository.
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary)/ASSET
        archive.write_bytes(payload)
        gh(['release', 'upload', TAG, str(archive), '--clobber'])
    print('Encrypted cloud checkpoint saved.', flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_bytes(encrypt(args.root))
    print('Encrypted downloadable archive prepared.')
