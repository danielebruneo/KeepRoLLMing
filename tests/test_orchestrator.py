import pytest
from fastapi.testclient import TestClient
from keeprollming.app import app  # Assumendo che l'app FastAPI sia definita in questo file

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c

def test_completion_status_200(client):
    response = client.post("/completion", json={"messages": [{"role": "user", "content": "ciao, test"}]})
    assert response.status_code == 200
