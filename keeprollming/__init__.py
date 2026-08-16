"Keeprollming orchestrator package."

__version__ = "0.9.2"


def main():
    """CLI entry point: runs uvicorn on keeprollming.app.
    Parses --port from sys.argv (or defaults to 8000).
    """
    import sys
    import uvicorn
    port = 8000
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
            break
    uvicorn.run("keeprollming.app:app", host="0.0.0.0", port=port, log_level="info")
