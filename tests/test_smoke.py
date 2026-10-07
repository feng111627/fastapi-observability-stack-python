"""既有对外行为保持不变。"""

from fastapi.testclient import TestClient

from app.main import create_app


def test_根路由行为不变():
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"Hello": "World"}


def test_应用可正常构建():
    assert create_app() is not None
