#!/usr/bin/env python3
import base64
import ipaddress
import os
import random
import string
import shlex
import subprocess
import stat
import sys
import time
import yaml
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
import hashlib
import requests
from requests.auth import HTTPBasicAuth
import tarfile


MAXMIND_DATABASES = {
    'GeoLite2-City': 'GeoLite2-City.mmdb',
    'GeoLite2-Country': 'GeoLite2-Country.mmdb',
}

MAXMIND_DOWNLOAD_BASE_URL = 'https://download.maxmind.com/geoip/databases/{edition}/download'


def load_vars(vars_file):
    with open(vars_file, "r") as f:
        return yaml.safe_load(f)


def get_random_str(size=32):
    return ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(size))


def prepare_context(vars_file):
    # Load variables
    context = load_vars(vars_file)
    context['volgactf']['qualifier']['transient'] = {}

    ca_root_dir = run_cmd('mkcert -CAROOT', '.', capture_output=True).stdout.strip()
    ca_root = f"{ca_root_dir}/rootCA.pem"
    context['volgactf']['qualifier']['transient']['ca_root'] = ca_root

    net = ipaddress.ip_network(context['volgactf']['qualifier']['network']['cidr'])
    net_prefix = net.prefixlen
    subnets = list(net.subnets(new_prefix=net_prefix + 1))

    expose_ports_start = context['volgactf']['qualifier']['expose_ports']['start']
    expose_ports_end = context['volgactf']['qualifier']['expose_ports']['end']

    static_subnet = subnets[0]
    dynamic_subnet = subnets[1]
    context['volgactf']['qualifier']['transient']['dynamic_subnet'] = str(dynamic_subnet)

    static_ips = list(static_subnet.hosts())
    context['volgactf']['qualifier']['transient']['gateway'] = {'ip_address': static_ips[0]}
    context['volgactf']['qualifier']['transient']['nginx'] = {'ip_address': static_ips[1]}

    context['volgactf']['qualifier']['transient']['smtp'] = {
        'username': os.getenv('VOLGACTF_QUALIFIER_SMTP_USERNAME', 'volgactf_qualifier'),
        'password': os.getenv('VOLGACTF_QUALIFIER_SMTP_PASSWORD', 'V0lgactf_qu4lifi3r!')
    }

    context['volgactf']['qualifier']['transient']['telegram'] = {
        'bot_token': os.getenv('TELEGRAM_BOT_TOKEN', '')
    }

    context['volgactf']['qualifier']['transient']['session_secret'] = get_random_str()

    context['volgactf']['qualifier']['transient']['github_token'] = os.getenv('GITHUB_TOKEN', '')

    context['volgactf']['qualifier']['transient']['ctftime_oauth'] = {
        'client_id': os.getenv('CTFTIME_OAUTH_CLIENT_ID', ''),
        'client_secret': os.getenv('CTFTIME_OAUTH_CLIENT_SECRET', ''),
    }

    context['volgactf']['qualifier']['transient']['aws'] = {
        'region': os.getenv('AWS_REGION', 'eu-central-1'),
        'endpoint': os.getenv('AWS_ENDPOINT', 'http://seaweedfs:8333'),
        'access_key_id': os.getenv('AWS_ACCESS_KEY_ID', 'volgactf_qualifier'),
        'secret_access_key': os.getenv('AWS_SECRET_ACCESS_KEY', 'V0lgactf_qu4lifi3r!'),
        's3_force_path_style': os.getenv('AWS_S3_FORCE_PATH_STYLE', 'true')
    }

    context['volgactf']['qualifier']['transient']['maxmind_enabled'] = os.getenv('MAXMIND_ACCOUNT_ID', None) is not None and os.getenv('MAXMIND_LICENSE_KEY', None) is not None

    context['volgactf']['qualifier']['transient']['proxy-admin'] = {'ip_address': static_ips[2], 'port': expose_ports_start}
    context['volgactf']['qualifier']['transient']['pgclient'] = {'ip_address': static_ips[3], 'port': expose_ports_start + 1}
    context['volgactf']['qualifier']['transient']['mailpit'] = {'ip_address': static_ips[4], 'port': expose_ports_start + 2}
    context['volgactf']['qualifier']['transient']['seaweedfs'] = {'ip_address': static_ips[5], 'port': expose_ports_start + 3}

    return context


def render_templates(template_dir, output_dir, context):
    # Setup Jinja2 environment (Ansible-style delimiters are fine)
    env = Environment(
        loader=FileSystemLoader(template_dir),
        trim_blocks=True,
        lstrip_blocks=True
    )

    template_dir = Path(template_dir)
    output_dir = Path(output_dir)

    for root, _, files in os.walk(template_dir):
        rel_root = Path(root).relative_to(template_dir)
        for file in files:
            template_path = rel_root / file

            rendered = None
            if template_path.name.endswith(".j2"):
                template = env.get_template(str(template_path))
                rendered = template.render(context).encode()
            else:
                with open(Path(template_dir, template_path), "rb") as f:
                    rendered = f.read()

            if file.endswith(".j2"):
                output_filename = file[:-3]
            else:
                output_filename = file

            output_path = output_dir / rel_root / output_filename
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "wb") as f:
                f.write(rendered)

            print(f"Rendered {template_path} → {output_path}")
            src_stat = (template_dir / template_path).stat()
            mode = src_stat.st_mode
            os.chmod(output_path, stat.S_IMODE(mode))


def generate_cert(output_dir, context):
    domains = [
        context['volgactf']['qualifier']['hostname'],
        context['volgactf']['qualifier']['remote_filestore']['hostname']
    ]

    for domain in domains:
        output_path = Path(output_dir, 'nginx', 'certs')
        output_path.mkdir(parents=True, exist_ok=True)

        cert_file = output_path / f"{domain}.pem"
        key_file = output_path / f"{domain}-key.pem"

        cmd = [
            "mkcert",
            "-cert-file", str(cert_file),
            "-key-file", str(key_file),
            domain
        ]

        try:
            subprocess.run(cmd, check=True)
            print(f"✅ Certificate created: {cert_file}, {key_file}")
        except subprocess.CalledProcessError as e:
            print(f"❌ mkcert failed: {e}")
        except FileNotFoundError:
            print("❌ mkcert not found. Please install it: https://github.com/FiloSottile/mkcert")


def get_remote_checksum(edition: str, auth: HTTPBasicAuth) -> str:
    """Fetch the expected SHA256 checksum from MaxMind."""
    url = MAXMIND_DOWNLOAD_BASE_URL.format(edition=edition)
    response = requests.get(url, params={'suffix': 'tar.gz.sha256'}, auth=auth)
    response.raise_for_status()
    # Format: "<checksum>  <filename>"
    return response.text.strip().split()[0]


def compute_local_checksum(filepath: str) -> str:
    """Compute SHA256 of a local file."""
    sha256 = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


def download_and_extract(download_dir, edition: str, filename: str, auth: HTTPBasicAuth):
    """Download, extract, and save the .mmdb file."""
    import tarfile, io

    url = MAXMIND_DOWNLOAD_BASE_URL.format(edition=edition)
    print(f'  Downloading {edition}...')
    response = requests.get(url, params={'suffix': 'tar.gz'}, auth=auth, stream=True)
    response.raise_for_status()

    dest = os.path.join(download_dir, f'{filename}.tar.gz')
    with open(dest, 'wb') as f:
        f.write(response.content)
    print(f'  Saved to: {dest}')


def sync_maxmind_databases(download_dir):
    auth = HTTPBasicAuth(os.getenv('MAXMIND_ACCOUNT_ID'), os.getenv('MAXMIND_LICENSE_KEY'))

    for edition, filename in MAXMIND_DATABASES.items():
        print(f'\n[{edition}]')
        local_path = os.path.join(download_dir, f'{filename}.tar.gz')

        remote_checksum = get_remote_checksum(edition, auth)
        print(f'  Remote checksum: {remote_checksum}')

        if os.path.exists(local_path):
            local_checksum = compute_local_checksum(local_path)
            print(f'  Local checksum:  {local_checksum}')
            if local_checksum == remote_checksum:
                print('  Already up to date. Skipping.')
                continue
            else:
                print('  Checksum mismatch. Updating...')
        else:
            print('  File not found locally. Downloading...')

        download_and_extract(download_dir, edition, filename, auth)


def extract_maxmind_databases(download_dir: str, output_dir: str):
    """Extract .mmdb files from all cached tar.gz archives into dest_dir."""
    dest_dir = os.path.join(output_dir, 'nginx', 'maxmind')
    os.makedirs(dest_dir, exist_ok=True)

    for edition, filename in MAXMIND_DATABASES.items():
        archive_path = os.path.join(download_dir, f'{filename}.tar.gz')
        if not os.path.exists(archive_path):
            raise FileNotFoundError(f'Archive not found: {archive_path}')

        with tarfile.open(archive_path) as tar:
            for member in tar.getmembers():
                if member.name.endswith('.mmdb'):
                    extracted = tar.extractfile(member)
                    dest_path = os.path.join(dest_dir, filename)
                    with open(dest_path, 'wb') as f:
                        f.write(extracted.read())
                    print(f'  Extracted {member.name} -> {dest_path}')
                    break
            else:
                raise RuntimeError(f'No .mmdb found in archive: {archive_path}')

def run_cmd(cmd, cwd, check=True, capture_output=False):
    """Helper to run shell commands in project dir"""
    print(f"→ {cmd} (cwd={cwd})")
    return subprocess.run(
        shlex.split(cmd),
        check=check,
        capture_output=capture_output,
        text=True,
        cwd=str(cwd)
    )


def service_running(service, cwd):
    """Check if a service container is running in docker compose"""
    result = run_cmd(f"docker compose ps -q {service}", cwd, capture_output=True)
    print(f"ps -> {result}")
    cid = result.stdout.strip()
    if not cid:
        return False
    # Confirm it's actually running
    result = run_cmd(f"docker inspect -f '{{{{.State.Running}}}}' {cid}", cwd, capture_output=True)
    print(f"inspect -> {result}")
    return result.stdout.strip() == "true"


def first_init(work_dir):
    required_services = ["postgres"]
    were_running = {}

    for svc in required_services:
        already = service_running(svc, work_dir)
        were_running[svc] = already
        if not already:
            run_cmd(f"docker compose up -d {svc}", work_dir)

    time.sleep(1)
    run_cmd(f"./script/dist-frontend.sh", work_dir)
    time.sleep(1)
    run_cmd(f"./script/db-migrate.sh", work_dir)
    time.sleep(1)

    for svc, already in were_running.items():
        if not already:
            run_cmd(f"docker compose stop {svc}", work_dir)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: render.py <template_dir> <output_dir> <vars.yml>")
        sys.exit(1)

    template_dir, output_dir, vars_file = sys.argv[1:]
    context = prepare_context(vars_file)
    render_templates(template_dir, output_dir, context)
    generate_cert(output_dir, context)
    if context['volgactf']['qualifier']['transient']['maxmind_enabled']:
        sync_maxmind_databases(os.path.join(os.getcwd(), 'maxmind'))
        extract_maxmind_databases(os.path.join(os.getcwd(), 'maxmind'), output_dir)
    first_init(output_dir)
