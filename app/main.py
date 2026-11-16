import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .data import Country, SyncError, SyncState, make_engine, serialize, synchronize


def create_app(database_url=None, initial_sync=True):
    engine = make_engine(database_url or os.getenv('DATABASE_URL', 'sqlite:///./worldatlas.db'))

    @asynccontextmanager
    async def lifespan(app):
        def initialize():
            with Session(engine) as session:
                if not session.scalar(select(func.count()).select_from(Country)):
                    try:
                        synchronize(session)
                    except SyncError as exc:
                        logging.warning('Initial sync: %s', exc.message)
        if initial_sync:
            await run_in_threadpool(initialize)
        yield
        engine.dispose()

    app = FastAPI(title='WorldAtlas', lifespan=lifespan)
    app.state.engine = engine
    origins = [x.strip() for x in os.getenv('CORS_ORIGINS', 'http://localhost:8000,http://127.0.0.1:8000').split(',') if x.strip()]
    if '*' in origins:
        raise ValueError('Explicit CORS origins required')
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({'error': {'message': str(exc.detail)}}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({'error': {'message': 'Некорректные параметры запроса'}}, status_code=422)

    @app.exception_handler(SyncError)
    async def sync_error(request, exc):
        return JSONResponse({'error': {'message': exc.message}}, status_code=exc.status)

    @app.exception_handler(Exception)
    async def server_error(request, exc):
        logging.exception('Server error')
        return JSONResponse({'error': {'message': 'Внутренняя ошибка сервера'}}, status_code=500)

    @app.get('/api/countries')
    def countries(search: str = '', region: str = '', sort: Literal['name', 'population', 'area'] = 'name', order: Literal['asc', 'desc'] = 'asc'):
        with Session(engine) as session:
            rows = session.scalars(select(Country)).all()
            rows = [x for x in rows if search.casefold() in x.name.casefold() and (not region or x.region == region)]
            rows.sort(key=lambda x: (getattr(x, sort), x.code), reverse=order == 'desc')
            return [serialize(x) for x in rows]

    @app.get('/api/countries/{code}')
    def country(code: str):
        with Session(engine) as session:
            row = session.get(Country, code.upper())
            if row is None:
                raise HTTPException(404, 'Страна не найдена')
            return serialize(row)

    @app.get('/api/status')
    def status():
        with Session(engine) as session:
            state = session.get(SyncState, 1)
            return {'countries': session.scalar(select(func.count()).select_from(Country)), 'population': session.scalar(select(func.sum(Country.population))) or 0, 'last_sync': state.timestamp if state else None}

    static = Path(__file__).parent / 'static'
    app.mount('/static', StaticFiles(directory=static), name='static')

    @app.get('/')
    def index():
        return FileResponse(static / 'index.html')

    return app


app = create_app()
