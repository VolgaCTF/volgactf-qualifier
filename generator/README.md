# VolgaCTF Qualifier generator

The generator scaffolds a docker compose project with a fully functional VolgaCTF Qualifier checking system.

## Prerequisites

- Python 3
- Docker Compose
- [mkcert](https://github.com/FiloSottile/mkcert)
- any browser extension to connect to HTTP proxy e. g. [FoxyProxy](https://chromewebstore.google.com/detail/foxyproxy/gcknhkkoolaabfmlnjonogaaifnjlfnp)

## Setup

Install Python packages:

```shell
$ cd generator
$ python3 -m venv .venv
$ source .venv/bin/activate
$ pip install -r requirements.txt
$ deactivate
```

Copy sample settings:

```shell
$ cp generator/vars.example.yml generator/vars.yml
```

and adjust the copied file if necessary (network settings, teams, services et al.)

### Configuration

Depending on settings (in `vars.yml`), the generator will create a docker compose project for multiple containers. The most important setting here is the network CIDR (`volgactf.qualifier.network.cidr`):
- must be a local network (`/24` prefix is recommended)
- must not collide with any other networks in use (Docker, VirtualBox, VPN, Wi-Fi or Ethernet)

The next important group of options is the range of exposed ports (`volgactf.qualifier.expose_ports.start` and `volgactf.qualifier.expose_ports.end`). These ports are used by proxy containers:
- the first is used by a proxy container that serves as the traffic exchange node
- the second is used by a postgres client container, should one need to examine internal database structure
- the third is an mail server container, that is used instead of a real SMTP server that sends emails
- the fourth is an S3 server container, that is used instead os a real S3 server like AWS that stores task files

In essence all the traffic coming into the system is routed through the proxy container.

Additional settings can be provided via environment variables (see `generator/.env.example`). All of them are optional, and only make sense if the system is tested against real third-party systems (MaxMind GeoLite, AWS S3, Telegram, SMTP, GitHub, CTFTime).

## Generate

Choose an empty directory, in the example below `generated` will be used

```shell
$ cd generator
$ source .venv/bin/activate
# export env vars (see above) before running the next command 
$ python main.py templates ../generated vars.yml
$ deactivate
```

## Use

To start the system:
1. Navigate to the `generated` directory.
2. Read through post-generator steps and apply changes if necessary (add the system hostname to the local resolver, configure the proxy in a browser)
3. Launch the system with `docker compose up -d`. This will take some time.
4. Connect to the proxy.
5. Navigate to the system hostname e. g. `https://qualifier.volgactf.test` (specified in `volgactf.qualifier.hostname`).

To shut down: either `docker compose down` or `docker compose down -v` to do cleanup.

## Regenerate

For now, it is advised to destroy the project that was created previously, and generate it anew.

## Control tools

The commands described below must be launched with at least `postges` and `redis` containers up and running.

Competition tools:

```shell
$ cd generated
# create an internal user with admin permissions
$ script/cli.sh create_supervisor -e admin@example.com -u myawesomeusername -r admin
# for more CLI commands
$ script/cli.sh -h
```
