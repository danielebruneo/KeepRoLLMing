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

`stop` and `restart` send SIGTERM and wait up to **60 seconds**, without
automatically sending SIGKILL. Change the wait with `--stop-timeout SECONDS`
or `KRM_STOP_TIMEOUT` (`0` waits indefinitely). If the wait expires, the command
returns non-zero and retains the PID file; `restart` does **not** start a second
server. The old process may still be draining in-flight requests. Check status
and wait for actual exit before starting again. `--force` is an explicit opt-in
to SIGKILL after a short SIGTERM grace period, and signals only a verified KRM
PID. A stale PID pointing at another program is rejected, not killed.

```bash
./krm restart --stop-timeout 300  # allow long requests to drain
./krm stop --stop-timeout 0     # wait indefinitely
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
