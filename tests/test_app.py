from unittest.mock import patch

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.data import SyncError, synchronize
from app.main import create_app


def sync(client):
    """Запустить синхронизацию напрямую"""
    with Session(client.app.state.engine) as session:
        synchronize(session)


def record(code='FRA', name='France', region='Europe', population=100):
    return {
        'codes': {'alpha_3': code},
        'names': {'common': name, 'official': name},
        'capitals': [{'name': 'Paris'}],
        'region': region,
        'population': population,
        'area': {'kilometers': 50},
        'flag': {'url_svg': 'https://example.test/flag.svg'},
        'languages': [{'name': 'French'}],
        'currencies': [{'name': 'Euro', 'code': 'EUR'}],
        'timezones': ['UTC+01:00'],
    }


@pytest.fixture
def client(tmp_path):
    """Приложение на временной SQLite без синхронизации"""
    app = create_app('sqlite:///' + str(tmp_path / 'test.db'), initial_sync=False)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def remote(monkeypatch):
    """Подмена HTTP-клиента ответом REST Countries v5"""
    monkeypatch.setenv('REST_COUNTRIES_API_KEY', 'test-key')
    with patch('requests.Session.get') as mock:
        mock.return_value.json.return_value = {'data': {'objects': [record(), record('JPN', 'Japan', 'Asia', 200)]}}
        yield mock


@pytest.fixture
def seeded(client, remote):
    """Клиент с уже загруженными странами"""
    sync(client)
    return client


def test_list(seeded):
    """Каталог отдаёт все загруженные страны"""
    assert len(seeded.get('/api/countries').json()) == 2


def test_search(seeded):
    """Поиск по части названия без учёта регистра находит страну"""
    assert seeded.get('/api/countries?search=FRan').json()[0]['code'] == 'FRA'


def test_region(seeded):
    """Фильтр по региону оставляет только страны этого региона"""
    assert [x['code'] for x in seeded.get('/api/countries?region=Asia').json()] == ['JPN']


def test_sort(seeded):
    """Сортировка по населению по убыванию"""
    assert [x['population'] for x in seeded.get('/api/countries?sort=population&order=desc').json()] == [200, 100]


def test_detail(seeded):
    """Подробности страны по коду и расчёт плотности населения"""
    assert seeded.get('/api/countries/fra').json()['density'] == 2


def test_missing(client):
    """Неизвестный код страны возвращает 404 с сообщением"""
    r = client.get('/api/countries/XXX')
    assert r.status_code == 404
    assert r.json()['error']['message']


def test_initial(tmp_path, remote):
    """Пустая база синхронизируется при старте приложения"""
    with TestClient(create_app('sqlite:///' + str(tmp_path / 'initial.db'))) as c:
        assert c.get('/api/status').json()['countries'] == 2


def test_repeat(seeded, remote):
    """Повторная синхронизация обновляет данные без дубликатов"""
    remote.return_value.json.return_value['data']['objects'][0]['population'] = 999
    sync(seeded)
    assert seeded.get('/api/status').json()['countries'] == 2
    assert seeded.get('/api/countries/FRA').json()['population'] == 999


@pytest.mark.parametrize('failure,status', [(requests.Timeout(),504), (requests.ConnectionError(),502), (requests.HTTPError(),502), (ValueError(),502)])
def test_failures_preserve(seeded, remote, failure, status):
    """При таймауте, отказе сети, HTTP-ошибке и битом JSON данные и last_sync не меняются"""
    before = seeded.get('/api/status').json()
    if isinstance(failure, ValueError):
        remote.return_value.json.side_effect = failure
    elif isinstance(failure, requests.HTTPError):
        remote.return_value.status_code = 500
        remote.return_value.raise_for_status.side_effect = failure
    else:
        remote.side_effect = failure
    with pytest.raises(SyncError) as exc:
        sync(seeded)
    assert exc.value.status == status
    assert seeded.get('/api/status').json() == before


@pytest.mark.parametrize('payload', [[], {}, [{'cca3':'BAD'}]])
def test_invalid_payload(seeded, remote, payload):
    """Пустой или неполный ответ API отклоняется, старые данные сохраняются"""
    remote.return_value.json.return_value = payload
    with pytest.raises(SyncError):
        sync(seeded)
    assert seeded.get('/api/status').json()['countries'] == 2


@pytest.mark.parametrize('origin,allowed', [('http://localhost:8000',True), ('https://evil.site',False)])
def test_cors(client, origin, allowed):
    """Preflight разрешает только origin из CORS_ORIGINS"""
    r = client.options('/api/countries', headers={'Origin':origin,'Access-Control-Request-Method':'GET'})
    assert r.status_code == (200 if allowed else 400)
    assert (r.headers.get('access-control-allow-origin') == origin) == allowed


def test_validation(client):
    """Недопустимый параметр сортировки возвращает 422"""
    assert client.get('/api/countries?sort=invalid').status_code == 422


def test_frontend(client):
    """Главная страница и статика отдаются"""
    assert 'WorldAtlas' in client.get('/').text
    assert client.get('/static/app.js').status_code == 200


def test_requires_key(client, monkeypatch):
    """Без REST_COUNTRIES_API_KEY синхронизация завершается ошибкой 500"""
    monkeypatch.delenv('REST_COUNTRIES_API_KEY', raising=False)
    with pytest.raises(SyncError) as exc:
        sync(client)
    assert exc.value.status == 500
    assert 'REST_COUNTRIES_API_KEY' in exc.value.message


def test_modern_missing_alpha3(client, remote, monkeypatch):
    """Страна без alpha_3 получает код EXT- и обновляется без дублей"""
    monkeypatch.setenv('REST_COUNTRIES_API_KEY', 'test-key')
    remote.return_value.json.return_value = {'data': {'objects': [{
        'codes': {'alpha_3': '', 'alpha_2': 'XK'},
        'names': {'common': 'Kosovo'}, 'population': 100,
        'area': {'kilometers': 50}, 'languages': [], 'currencies': []
    }]}}
    sync(client)
    assert client.get('/api/countries/EXT-XK').json()['name'] == 'Kosovo'
    sync(client)
    assert client.get('/api/status').json()['countries'] == 1


def test_missing_all_identifiers():
    """Запись без ISO-кодов и uuid отклоняется валидацией"""
    from app.data import normalize
    with pytest.raises(ValueError):
        normalize({'codes': {}, 'names': {'common': 'Unknown'},
                   'population': 1, 'area': {'kilometers': 1},
                   'languages': [], 'currencies': []})
