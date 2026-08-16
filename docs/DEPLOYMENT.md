# Deployment

## Local daemon

Create and review `config.yaml`, then use the bundled local launcher. Its
default log directory is `./logs`, so it works without writing to `/var/log`.

```bash
./krm start --port 8000 --config config.yaml
./krm status --port 8000
./krm logs --port 8000
./krm stop --port 8000
```

Use `--log-path /srv/keeprollming/logs` when an operator-managed path is
required. Set `KRM_PYTHON` for a managed interpreter; otherwise `krm` prefers
the project `.venv`. Use `./krm serve` when the server should remain in the
foreground.

## Container

Install from package metadata rather than a second dependency list:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install .
CMD ["keeprollming", "--port", "8000"]
```

Mount or provide `config.yaml` and set `CONFIG_FILE` when it is outside the
working directory.

## Reverse proxy

Streaming requires buffering to remain disabled and an adequate read timeout.

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_read_timeout 300s;
    proxy_buffering off;
}
```

Do not expose the proxy publicly without an authentication and network policy
appropriate for the upstream credentials and transcript logging you use.
