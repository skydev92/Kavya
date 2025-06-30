from fastapi.security import HTTPBearer
from fastapi.testclient import TestClient

import kavya.auth


class AuthStub(HTTPBearer):
    def __init__(self, auto_error: bool = True):
        super().__init__(auto_error=auto_error)

    async def __call__(self, _):
        return random.randint(40000, 50000)


kavya.auth.JWTBearer = AuthStub

import json
import random

import pytest

import kavya.openai_server

client = TestClient(kavya.openai_server.app)


def test_functioning():
    health_response = client.get("/health")
    assert health_response.status_code == 200

    hello_response = client.post(
        "/v1/chat/completions",
        content=json.dumps(
            {
                "model": "kavya-m1",
                "messages": [{"role": "user", "content": "Hello!"}],
            }
        ),
        headers={"Authorization": "Bearer 0"},
    )
    assert hello_response.status_code == 200


def test_streaming_search():
    test_response = client.post(
        "/v1/chat/completions",
        content=json.dumps(
            {
                "model": "kavya-m1",
                "messages": [{"role": "user", "content": "Weather today in New York"}],
                "stream": True,
            }
        ),
        headers={"Authorization": "Bearer 0"},
    )
    assert test_response.status_code == 200
    assert "agent/web_source_disclosure" in test_response.text

    test_response = client.post(
        "/v1/chat/completions",
        content=json.dumps(
            {
                "model": "kavya-m1",
                "messages": [
                    {
                        "role": "user",
                        "content": "Who was the first president of USA? Answer only with first name and last name",
                    }
                ],
                "stream": True,
            }
        ),
        headers={"Authorization": "Bearer 0"},
    )
    assert test_response.status_code == 200
    assert "agent/web_source_disclosure" not in test_response.text


def test_non_streaming_search():
    test_response = client.post(
        "/v1/chat/completions",
        content=json.dumps(
            {
                "model": "kavya-m1",
                "messages": [{"role": "user", "content": "Weather today in New York"}],
                "stream": False,
            }
        ),
        headers={"Authorization": "Bearer 0"},
    )
    assert test_response.status_code == 200
    assert "search_results" in test_response.text

    test_response = client.post(
        "/v1/chat/completions",
        content=json.dumps(
            {
                "model": "kavya-m1",
                "messages": [
                    {
                        "role": "user",
                        "content": "Who was the first president of USA? Answer only with first name and last name",
                    }
                ],
                "stream": False,
            }
        ),
        headers={"Authorization": "Bearer 0"},
    )
    assert test_response.status_code == 200
    assert "search_results" not in test_response.text


# Calls itself
def default_tests():
    retcode = pytest.main(["--verbose", "./kavya/tests/test_app.py"])
    return f"""code {retcode} - {[
        "Tests passed.",
        "Tests failed.",
        "pytest was interrupted.",
        "An internal error got in the way.",
        "pytest was misused.",
        "pytest couldn't find tests.",
    ][retcode]}"""  # https://docs.pytest.org/en/stable/_modules/_pytest/config.html#ExitCode
