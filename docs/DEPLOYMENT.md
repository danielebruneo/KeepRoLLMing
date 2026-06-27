# Deployment

## Production Daemon

```bash
bash scripts/daemon.sh start   --port 8000              # start
bash scripts/daemon.sh status  --port 8000              # check
bash scripts/daemon.sh stop    --port 8000              # stop
bash scripts/daemon.sh logs    --port 8000              # tail logs
```

## Uvicorn Direct

```bash
uvicorn keeprollming.app:app --host 0.0.0.0 --port 8000 --workers 4
```

## Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "keeprollming.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

## Reverse Proxy (nginx)

```nginx
server {
    listen 443 ssl;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
        proxy_buffering off;
    }
}
```

## Configuration File

Set via `CONFIG_FILE` environment variable (default: `config.yaml`).
