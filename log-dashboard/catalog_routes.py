from __future__ import annotations

import os
from contextlib import ExitStack
from functools import wraps
from pathlib import Path
from typing import Callable, Iterator, ParamSpec, cast

from flask import Flask, Response, current_app, jsonify, request, send_file
from flask.typing import ResponseReturnValue
from werkzeug.http import dump_options_header
from werkzeug.utils import secure_filename

from catalog import CatalogError, CatalogStore
from log_text import TextDump, open_text_dump


P = ParamSpec('P')


def init_catalog(app: Flask, *, store: CatalogStore | None = None) -> CatalogStore:
    if store is None:
        configured = app.config.get('CATALOG_DIR') or os.environ.get('CATALOG_DIR')
        root = Path(configured or Path(app.root_path) / 'catalog-data')
        store = CatalogStore(root)
    store.initialize()
    store.cleanup()
    app.extensions['catalog'] = store
    return store


def get_catalog() -> CatalogStore:
    store = current_app.extensions.get('catalog')
    if store is None:
        store = init_catalog(cast(Flask, current_app))
    return store


def _catalog_errors(
    handler: Callable[P, ResponseReturnValue],
) -> Callable[P, ResponseReturnValue]:
    @wraps(handler)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> ResponseReturnValue:
        try:
            return handler(*args, **kwargs)
        except CatalogError as error:
            return jsonify({'error': str(error)}), error.status_code
        except Exception:
            current_app.logger.exception('Catalog request failed')
            return jsonify({'error': 'Catalog request failed'}), 500

    return wrapped


def _json_object() -> dict:
    value = request.get_json(silent=True)
    if not isinstance(value, dict) or not value:
        raise CatalogError('Invalid JSON body', 400)
    return value


def _only_fields(value: dict, allowed: set[str]) -> dict:
    if not set(value).issubset(allowed):
        raise CatalogError('Invalid request field', 400)
    return value


def _stream_dump(dump: TextDump, stack: ExitStack) -> Iterator[bytes]:
    try:
        yield from dump
    finally:
        stack.close()


def register_catalog_routes(
    app: Flask,
    analyze: Callable[[str, str], dict],
) -> None:
    @app.before_request
    def require_same_origin() -> ResponseReturnValue | None:
        if not request.path.startswith('/api/') or request.method not in {
            'POST', 'PATCH', 'DELETE', 'PUT'
        }:
            return None
        expected = (
            current_app.config.get('PUBLIC_ORIGIN')
            or os.environ.get('PUBLIC_ORIGIN')
            or request.host_url.rstrip('/')
        )
        origin = request.headers.get('Origin')
        if origin is not None:
            if origin == expected and origin != 'null':
                return None
        elif request.headers.get('Sec-Fetch-Site') == 'same-origin':
            return None
        return jsonify({'error': 'Forbidden origin'}), 403

    @app.post('/api/logs/<log_id>/save')
    @_catalog_errors
    def save_log(log_id: str) -> ResponseReturnValue:
        metadata = get_catalog().save(log_id, _json_object())
        return jsonify({'success': True, 'log': metadata}), 200

    @app.get('/api/logs')
    @_catalog_errors
    def list_logs() -> ResponseReturnValue:
        allowed = {'folder_id', 'name', 'tag'}
        if not set(request.args).issubset(allowed) or any(
            len(request.args.getlist(key)) != 1 for key in request.args
        ):
            raise CatalogError('Invalid log filter', 400)
        folder_text = request.args.get('folder_id')
        if folder_text is None:
            folder_id = None
        elif not folder_text.isdigit():
            raise CatalogError('Invalid folder', 400)
        else:
            folder_id = int(folder_text)
        logs = get_catalog().list_logs(
            folder_id=folder_id,
            name=request.args.get('name', ''),
            tag=request.args.get('tag', ''),
        )
        return jsonify({'success': True, 'logs': logs}), 200

    @app.get('/api/logs/<log_id>')
    @_catalog_errors
    def get_log(log_id: str) -> ResponseReturnValue:
        return jsonify({'success': True, 'log': get_catalog().get(log_id)}), 200

    @app.patch('/api/logs/<log_id>')
    @_catalog_errors
    def edit_log(log_id: str) -> ResponseReturnValue:
        metadata = get_catalog().edit(log_id, _json_object())
        return jsonify({'success': True, 'log': metadata}), 200

    @app.delete('/api/logs/<log_id>')
    @_catalog_errors
    def delete_log(log_id: str) -> ResponseReturnValue:
        get_catalog().delete(log_id)
        return jsonify({'success': True}), 200

    @app.post('/api/logs/<log_id>/analyze')
    @_catalog_errors
    def analyze_log(log_id: str) -> ResponseReturnValue:
        with get_catalog().use_file(log_id) as (path, metadata):
            analysis = analyze(str(path), metadata['original_filename'])
        return jsonify({**analysis, 'log_id': log_id}), 200

    @app.get('/api/logs/<log_id>/download')
    @_catalog_errors
    def download_log(log_id: str) -> ResponseReturnValue:
        stack = ExitStack()
        try:
            path, metadata = stack.enter_context(get_catalog().use_file(log_id))
            stem = secure_filename(metadata['display_name']) or f'log-{log_id}'
            response = send_file(
                path,
                as_attachment=True,
                download_name=stem + path.suffix,
                mimetype='application/octet-stream',
            )
            response.direct_passthrough = False
            response.call_on_close(stack.close)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            return response
        except BaseException:
            stack.close()
            raise

    @app.get('/api/logs/<log_id>/text')
    @_catalog_errors
    def text_log(log_id: str) -> ResponseReturnValue:
        stack = ExitStack()
        try:
            path, metadata = stack.enter_context(get_catalog().use_file(log_id))
            dump = open_text_dump(path)
            stack.callback(dump.close)
            stem = secure_filename(metadata['display_name']) or f'log-{log_id}'
            disposition = 'attachment' if request.args.get('download') == '1' else 'inline'
            response = Response(
                _stream_dump(dump, stack), content_type='text/plain; charset=utf-8'
            )
            response.headers['Content-Disposition'] = dump_options_header(
                disposition, {'filename': stem + '.txt'}
            )
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Accel-Buffering'] = 'no'
            response.call_on_close(stack.close)
            return response
        except BaseException:
            stack.close()
            raise

    @app.get('/api/folders')
    @_catalog_errors
    def list_folders() -> ResponseReturnValue:
        return jsonify({
            'success': True,
            'folders': get_catalog().list_folders(),
        }), 200

    @app.post('/api/folders')
    @_catalog_errors
    def create_folder() -> ResponseReturnValue:
        body = _only_fields(_json_object(), {'name', 'parent_id'})
        folder = get_catalog().create_folder(
            cast(str, body.get('name')), cast(int, body.get('parent_id', 1))
        )
        return jsonify({'success': True, 'folder': folder}), 201

    @app.patch('/api/folders/<int:folder_id>')
    @_catalog_errors
    def rename_folder(folder_id: int) -> ResponseReturnValue:
        body = _only_fields(_json_object(), {'name'})
        folder = get_catalog().rename_folder(folder_id, cast(str, body.get('name')))
        return jsonify({'success': True, 'folder': folder}), 200

    @app.delete('/api/folders/<int:folder_id>')
    @_catalog_errors
    def delete_folder(folder_id: int) -> ResponseReturnValue:
        get_catalog().delete_folder(folder_id)
        return jsonify({'success': True}), 200

    @app.get('/api/tags')
    @_catalog_errors
    def list_tags() -> ResponseReturnValue:
        return jsonify({'success': True, 'tags': get_catalog().list_tags()}), 200
