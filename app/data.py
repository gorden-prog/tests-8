import os
import logging
from pathlib import Path
from datetime import datetime, timezone
from threading import Lock

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from sqlalchemy import JSON, Float, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

load_dotenv(Path(__file__).resolve().parent.parent / '.env')


class Base(DeclarativeBase):
    pass


class Country(Base):
    __tablename__ = 'countries'
    code: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    official: Mapped[str]
    capital: Mapped[str]
    region: Mapped[str]
    subregion: Mapped[str]
    population: Mapped[int] = mapped_column(Integer)
    area: Mapped[float] = mapped_column(Float)
    flag: Mapped[str]
    languages: Mapped[list] = mapped_column(JSON)
    currencies: Mapped[list] = mapped_column(JSON)
    timezones: Mapped[list] = mapped_column(JSON)
    updated_at: Mapped[str]


class SyncState(Base):
    __tablename__ = 'sync_state'
    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[str]


class Record(BaseModel):
    code: str = Field(pattern=r'^(?:[A-Z]{3}|EXT-[A-Z]{2}|EXT-[0-9A-Fa-f-]{36})$')
    name: str = Field(min_length=1)
    official: str
    capital: str
    region: str
    subregion: str
    population: int = Field(ge=0)
    area: float = Field(ge=0, allow_inf_nan=False)
    flag: str
    languages: list[str]
    currencies: list[str]
    timezones: list[str]


class SyncError(Exception):
    def __init__(self, message, status=502):
        self.message, self.status = message, status


def country_array(payload, key=None):
    if isinstance(payload, dict):
        errors = payload.get('errors')
        if errors:
            raise SyncError('REST Countries сообщил об ошибке. Проверьте доступность REST Countries v5 и REST_COUNTRIES_API_KEY.')
        payload = payload.get('data', payload)
        if isinstance(payload, dict):
            payload = payload.get('objects')
    if not isinstance(payload, list):
        raise SyncError('REST Countries v5 вернул объект вместо списка стран.')
    return payload


def fetch_records():
    key = os.getenv('REST_COUNTRIES_API_KEY', '').strip()
    if not key:
        raise SyncError('Не задан REST_COUNTRIES_API_KEY.', 500)
    url = 'https://api.restcountries.com/countries/v5'
    params = {'limit': 100, 'offset': 0}
    headers = {'Authorization': f'Bearer {key}'}
    try:
        with requests.Session() as client:
            response = client.get(url, params=params, headers=headers, timeout=(5, 30))
            response.raise_for_status()
            payload = country_array(response.json())
            pages = 0
            batch = payload
            while len(batch) == 100:
                pages += 1
                if pages > 20:
                    raise ValueError('Pagination did not terminate')
                params['offset'] = pages * 100
                page = client.get(url, params=params, headers=headers, timeout=(5, 30))
                page.raise_for_status()
                batch = country_array(page.json())
                payload.extend(batch)
        if not payload:
            raise ValueError('Expected nonempty country array')
        records = [normalize(item) for item in payload]
        if len({r.code for r in records}) != len(records):
            raise ValueError('Duplicate codes')
        return records
    except requests.Timeout as exc:
        raise SyncError('Внешний API не ответил вовремя', 504) from exc
    except requests.ConnectionError as exc:
        raise SyncError('Нет соединения с REST Countries') from exc
    except requests.HTTPError as exc:
        raise SyncError('REST Countries вернул ошибку HTTP') from exc
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        logging.warning('REST Countries response validation failed: %s', exc)
        raise SyncError('Некорректный ответ REST Countries') from exc


def normalize(x):
    code = x['codes'].get('alpha_3')
    if not code:
        identity = x['codes'].get('alpha_2') or x.get('uuid')
        if identity:
            code = f'EXT-{identity}'
    names = x['names']
    currencies = x.get('currencies', [])
    languages = x.get('languages', [])
    return Record(
        code=code or '', name=names['common'],
        official=names.get('official', names['common']),
        capital=', '.join(c['name'] for c in x.get('capitals', [])),
        region=x.get('region', ''), subregion=x.get('subregion', ''),
        population=x['population'], area=x['area']['kilometers'],
        flag=x.get('flag', {}).get('url_svg', ''),
        languages=[v['name'] for v in languages],
        currencies=[f"{v['name']} ({v['code']})" for v in currencies],
        timezones=x.get('timezones', []))


sync_lock = Lock()


def synchronize(session):
    with sync_lock:
        records = fetch_records()
        now = datetime.now(timezone.utc).isoformat()
        try:
            for record in records:
                session.merge(Country(**record.model_dump(), updated_at=now))
            session.merge(SyncState(id=1, timestamp=now))
            session.commit()
        except Exception:
            session.rollback()
            raise
        return {'updated': len(records), 'last_sync': now}


def make_engine(url):
    engine = create_engine(url, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    return engine


def serialize(country):
    result = {column.name: getattr(country, column.name) for column in Country.__table__.columns}
    result['density'] = country.population / country.area if country.area else None
    return result
