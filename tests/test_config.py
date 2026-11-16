import time
from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.data import SyncError, synchronize
from app.main import create_app

ORIGINS = ['http://localhost:8000', 'https://kenuna.duckdns.org']


def record(code='FRA', name='France'):
    return {
        'codes': {'alpha_3': code},
        'names': {'common': name, 'official': name},
        'capitals': [{'name': 'Paris'}],
        'region': 'Europe',
        'population': 100,
        'area': {'kilometers': 50},
        'flag': {'url_svg': 'https://example.test/flag.svg'},
        'languages': [{'name': 'French'}],
        'currencies': [{'name': 'Euro', 'code': 'EUR'}],
        'timezones': ['UTC+01:00'],
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Клиент с явно заданным списком разрешённых Origin"""
    monkeypatch.setenv('CORS_ORIGINS', ','.join(ORIGINS))
    app = create_app('sqlite:///' + str(tmp_path / 'config.db'), initial_sync=False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def remote(monkeypatch):
    """Подмена HTTP-клиента ответом REST Countries v5"""
    monkeypatch.setenv('REST_COUNTRIES_API_KEY', 'test-key')
    response = MagicMock()
    response.json.return_value = {'data': {'objects': [record()]}}
    response.raise_for_status.return_value = None
    with patch('requests.Session.get', return_value=response) as mock:
        yield mock


@pytest.mark.parametrize('origin', ORIGINS)
def test_cors_allows_configured_origins(client, origin):
    """Preflight с разрешённым Origin возвращает 200 и точный Allow-Origin"""
    response = client.options('/api/countries', headers={'Origin': origin, 'Access-Control-Request-Method': 'GET'})
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == origin


def test_cors_rejects_unknown_origin(client):
    """Preflight с посторонним Origin возвращает 400 без Allow-Origin"""
    response = client.options('/api/countries', headers={'Origin': 'https://evil.site', 'Access-Control-Request-Method': 'GET'})
    assert response.status_code == 400
    assert 'access-control-allow-origin' not in response.headers


def test_cors_simple_request(client):
    """Простой запрос получает Allow-Origin только для разрешённого Origin"""
    allowed = client.get('/api/countries', headers={'Origin': ORIGINS[0]})
    assert allowed.headers['access-control-allow-origin'] == ORIGINS[0]
    denied = client.get('/api/countries', headers={'Origin': 'https://evil.example'})
    assert 'access-control-allow-origin' not in denied.headers

@pytest.mark.parametrize('path', ['/api/v1/status', '/api/v2/countries', '/api/v1/countries?search=France'])
def test_versioned_routes_not_implemented(client, path):
    """Версионированных маршрутов нет, возврат 404"""
    assert client.get(path).status_code == 404


def test_unversioned_status(client):
    """Базовый маршрут работает"""
    assert client.get('/api/status').status_code == 200
