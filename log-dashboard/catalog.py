from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import logging
import os
import re
import shutil
import sqlite3
import time
import unicodedata
import uuid
from pathlib import Path
from typing import BinaryIO, Callable, ContextManager, Iterator


logger = logging.getLogger(__name__)


class CatalogError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class CatalogStore:
    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root)
        self.files = self.root / 'files'
        self.database = self.root / 'catalog.sqlite3'
        self.clock = clock

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute('PRAGMA busy_timeout = 5000')
        return connection

    def initialize(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.files.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.root, 0o700)
        os.chmod(self.files, 0o700)
        self.database.touch(mode=0o600, exist_ok=True)
        os.chmod(self.database, 0o600)
        with self._connect() as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version > 1:
                raise CatalogError('Unsupported catalog schema version', 500)
            if version == 0:
                connection.executescript(
                    '''
                    BEGIN IMMEDIATE;
                    CREATE TABLE folders (
                      id INTEGER PRIMARY KEY,
                      parent_id INTEGER REFERENCES folders(id) ON DELETE RESTRICT,
                      name TEXT NOT NULL,
                      created_at REAL NOT NULL,
                      UNIQUE(parent_id, name),
                      CHECK ((id = 1 AND parent_id IS NULL) OR
                             (id <> 1 AND parent_id IS NOT NULL))
                    );
                    CREATE TABLE logs (
                      id TEXT PRIMARY KEY,
                      file_key TEXT NOT NULL UNIQUE,
                      original_filename TEXT NOT NULL,
                      size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                      created_at REAL NOT NULL,
                      status TEXT NOT NULL,
                      expires_at REAL,
                      saved_at REAL,
                      updated_at REAL NOT NULL,
                      folder_id INTEGER REFERENCES folders(id) ON DELETE RESTRICT,
                      display_name TEXT,
                      notes TEXT NOT NULL DEFAULT '',
                      CHECK (status IN ('processing', 'temporary', 'saved', 'deleting')),
                      CHECK (
                        (status = 'processing' AND expires_at IS NULL AND
                         saved_at IS NULL AND folder_id IS NULL AND display_name IS NULL) OR
                        (status = 'temporary' AND expires_at IS NOT NULL AND
                         saved_at IS NULL AND folder_id IS NULL AND display_name IS NULL) OR
                        (status = 'saved' AND expires_at IS NULL AND
                         saved_at IS NOT NULL AND folder_id IS NOT NULL AND
                         display_name IS NOT NULL AND
                         length(trim(display_name)) > 0) OR
                        status = 'deleting'
                      )
                    );
                    CREATE TABLE tags (
                      id INTEGER PRIMARY KEY,
                      name TEXT NOT NULL UNIQUE
                    );
                    CREATE TABLE log_tags (
                      log_id TEXT NOT NULL REFERENCES logs(id) ON DELETE CASCADE,
                      tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                      PRIMARY KEY(log_id, tag_id)
                    );
                    CREATE INDEX logs_expiry ON logs(status, expires_at);
                    CREATE INDEX logs_folder_date ON logs(folder_id, saved_at);
                    CREATE INDEX log_tags_tag ON log_tags(tag_id, log_id);
                    '''
                )
                connection.execute(
                    '''INSERT INTO folders(id, parent_id, name, created_at)
                       VALUES (1, NULL, 'Logs', ?)''',
                    (self.clock(),),
                )
                connection.execute('PRAGMA user_version = 1')
                connection.commit()

    @staticmethod
    def _extension(filename: str) -> str:
        suffix = Path(filename).suffix
        if re.fullmatch(r'\.[A-Za-z0-9]{1,16}', suffix):
            return suffix.lower()
        return '.bin'

    def ingest(
        self,
        source: BinaryIO,
        original_filename: str,
        analyze: Callable[[str, str], dict],
    ) -> tuple[dict, dict]:
        log_id = str(uuid.uuid4())
        file_key = f'{log_id}{self._extension(original_filename)}'
        path = self.files / file_key
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        handle = os.fdopen(descriptor, 'wb')
        published = False
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            now = self.clock()
            with self._connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute(
                    '''INSERT INTO logs(
                           id, file_key, original_filename, size_bytes, created_at,
                           status, expires_at, saved_at, updated_at, folder_id,
                           display_name, notes
                       ) VALUES (?, ?, ?, 0, ?, 'processing', NULL, NULL, ?, NULL, NULL, '')''',
                    (log_id, file_key, original_filename, now, now),
                )
                connection.commit()
            published = True
            shutil.copyfileobj(source, handle)
            handle.flush()
            size_bytes = handle.tell()
            analysis = analyze(str(path), original_filename)
            completed_at = self.clock()
            with self._connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute(
                    '''UPDATE logs
                       SET size_bytes = ?, status = 'temporary', expires_at = ?,
                           updated_at = ?
                       WHERE id = ? AND status = 'processing' ''',
                    (size_bytes, completed_at + 3600, completed_at, log_id),
                )
                connection.commit()
            return analysis, self._metadata_for_id(log_id)
        except BaseException:
            if published:
                self._mark_deleting(log_id)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception('Could not unlink failed catalog ingest %s', path)
            else:
                if published:
                    self._remove_row(log_id)
            raise
        finally:
            handle.close()

    def save(self, log_id: str, metadata: dict) -> dict:
        with self._connect() as connection:
            current = connection.execute(
                'SELECT status FROM logs WHERE id = ?', (log_id,)
            ).fetchone()
        if current is None:
            raise CatalogError('Log not found', 404)
        if not isinstance(metadata, dict):
            raise CatalogError('Invalid metadata', 400)
        allowed = {'display_name', 'folder_id', 'tags', 'notes'}
        if not set(metadata).issubset(allowed):
            raise CatalogError('Invalid metadata field', 400)
        display_name = self._name(metadata.get('display_name'), 200, 'display name')
        folder_id = metadata.get('folder_id', 1)
        if not isinstance(folder_id, int) or isinstance(folder_id, bool):
            raise CatalogError('Invalid folder', 400)
        tags = self._tags(metadata.get('tags', []))
        notes = metadata.get('notes', '')
        if not isinstance(notes, str) or len(notes) > 10_000:
            raise CatalogError('Invalid notes', 400)
        if current['status'] == 'saved':
            return self._metadata_for_id(log_id)

        now = self.clock()
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            folder = connection.execute(
                'SELECT id FROM folders WHERE id = ?', (folder_id,)
            ).fetchone()
            if folder is None:
                connection.rollback()
                raise CatalogError('Folder not found', 404)
            cursor = connection.execute(
                '''UPDATE logs SET status = 'saved', expires_at = NULL,
                       saved_at = ?, updated_at = ?, folder_id = ?,
                       display_name = ?, notes = ?
                   WHERE id = ? AND status = 'temporary' AND expires_at > ?''',
                (now, now, folder_id, display_name, notes, log_id, now),
            )
            if cursor.rowcount != 1:
                status = connection.execute(
                    'SELECT status, expires_at FROM logs WHERE id = ?', (log_id,)
                ).fetchone()
                connection.rollback()
                if status is None:
                    raise CatalogError('Log not found', 404)
                if status['status'] == 'saved':
                    return self._metadata_for_id(log_id)
                if status['status'] == 'temporary' and status['expires_at'] <= now:
                    raise CatalogError('Temporary log expired', 410)
                raise CatalogError('Log is not available to save', 409)
            for tag in tags:
                connection.execute('INSERT OR IGNORE INTO tags(name) VALUES (?)', (tag,))
                connection.execute(
                    '''INSERT INTO log_tags(log_id, tag_id)
                       SELECT ?, id FROM tags WHERE name = ?''',
                    (log_id, tag),
                )
            connection.commit()
        return self._metadata_for_id(log_id)

    def get(self, log_id: str) -> dict:
        metadata = self._metadata_for_id(log_id, saved_only=True)
        return metadata

    def list_logs(
        self,
        *,
        folder_id: int | None = None,
        name: str = '',
        tag: str = '',
    ) -> list[dict]:
        if folder_id is not None and (
            not isinstance(folder_id, int) or isinstance(folder_id, bool)
        ):
            raise CatalogError('Invalid folder', 400)
        if (
            not isinstance(name, str)
            or len(name) > 200
            or any(unicodedata.category(character).startswith('C') for character in name)
        ):
            raise CatalogError('Invalid name filter', 400)
        if not isinstance(tag, str):
            raise CatalogError('Invalid tag filter', 400)
        if folder_id is not None:
            with self._connect() as connection:
                folder = connection.execute(
                    'SELECT id FROM folders WHERE id = ?', (folder_id,)
                ).fetchone()
            if folder is None:
                raise CatalogError('Folder not found', 404)

        conditions = ["l.status = 'saved'"]
        parameters: list[object] = []
        if folder_id is not None:
            conditions.append('l.folder_id = ?')
            parameters.append(folder_id)
        if name:
            escaped = name.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            conditions.append("l.display_name LIKE ? ESCAPE '\\'")
            parameters.append(f'%{escaped}%')
        if tag:
            normalized_tag = self._name(tag, 40, 'tag').casefold()
            conditions.append(
                '''EXISTS (
                       SELECT 1 FROM log_tags selected_lt
                       JOIN tags selected_t ON selected_t.id = selected_lt.tag_id
                       WHERE selected_lt.log_id = l.id AND selected_t.name = ?
                   )'''
            )
            parameters.append(normalized_tag)
        where = ' AND '.join(conditions)
        with self._connect() as connection:
            rows = connection.execute(
                f'''SELECT l.*, GROUP_CONCAT(t.name, char(31)) AS tag_names
                    FROM logs l
                    LEFT JOIN log_tags lt ON lt.log_id = l.id
                    LEFT JOIN tags t ON t.id = lt.tag_id
                    WHERE {where}
                    GROUP BY l.id
                    ORDER BY l.saved_at DESC, l.id DESC''',
                parameters,
            ).fetchall()
        return [self._public_metadata(row) for row in rows]

    def edit(self, log_id: str, changes: dict) -> dict:
        if not isinstance(changes, dict):
            raise CatalogError('Invalid metadata', 400)
        allowed = {'display_name', 'folder_id', 'tags', 'notes'}
        if not set(changes).issubset(allowed):
            raise CatalogError('Invalid metadata field', 400)
        if not changes:
            return self.get(log_id)

        values: dict[str, object] = {}
        if 'display_name' in changes:
            values['display_name'] = self._name(
                changes['display_name'], 200, 'display name'
            )
        if 'folder_id' in changes:
            folder_id = changes['folder_id']
            if not isinstance(folder_id, int) or isinstance(folder_id, bool):
                raise CatalogError('Invalid folder', 400)
            values['folder_id'] = folder_id
        if 'notes' in changes:
            notes = changes['notes']
            if not isinstance(notes, str) or len(notes) > 10_000:
                raise CatalogError('Invalid notes', 400)
            values['notes'] = notes
        tags = self._tags(changes['tags']) if 'tags' in changes else None

        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current = connection.execute(
                "SELECT id FROM logs WHERE id = ? AND status = 'saved'", (log_id,)
            ).fetchone()
            if current is None:
                connection.rollback()
                raise CatalogError('Log not found', 404)
            if 'folder_id' in values:
                folder = connection.execute(
                    'SELECT id FROM folders WHERE id = ?', (values['folder_id'],)
                ).fetchone()
                if folder is None:
                    connection.rollback()
                    raise CatalogError('Folder not found', 404)
            assignments = [f'{field} = ?' for field in values]
            parameters = list(values.values())
            assignments.append('updated_at = ?')
            parameters.extend((self.clock(), log_id))
            connection.execute(
                f"UPDATE logs SET {', '.join(assignments)} WHERE id = ? AND status = 'saved'",
                parameters,
            )
            if tags is not None:
                connection.execute('DELETE FROM log_tags WHERE log_id = ?', (log_id,))
                for item in tags:
                    connection.execute('INSERT OR IGNORE INTO tags(name) VALUES (?)', (item,))
                    connection.execute(
                        '''INSERT INTO log_tags(log_id, tag_id)
                           SELECT ?, id FROM tags WHERE name = ?''',
                        (log_id, item),
                    )
            connection.commit()
        return self._metadata_for_id(log_id, saved_only=True)

    def list_tags(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                '''SELECT DISTINCT t.name
                   FROM tags t
                   JOIN log_tags lt ON lt.tag_id = t.id
                   JOIN logs l ON l.id = lt.log_id
                   WHERE l.status = 'saved'
                   ORDER BY t.name'''
            ).fetchall()
        return [row['name'] for row in rows]

    def use_file(self, log_id: str) -> ContextManager[tuple[Path, dict]]:
        return self._use_file(log_id)

    def delete(self, log_id: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT file_key FROM logs WHERE id = ? AND status = 'saved'",
                (log_id,),
            ).fetchone()
        if row is None:
            raise CatalogError('Log not found', 404)
        path = self.files / row['file_key']
        try:
            handle = path.open('rb')
        except FileNotFoundError:
            handle = None
        if handle is not None:
            try:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise CatalogError('Log is in use', 409) from error
                self._claim_saved_for_deletion(log_id, row['file_key'])
                try:
                    path.unlink()
                except OSError as error:
                    logger.exception('Could not unlink catalog file %s', path)
                    raise CatalogError('Could not delete log file', 500) from error
            finally:
                handle.close()
        else:
            self._claim_saved_for_deletion(log_id, row['file_key'])
        self._remove_row(log_id)

    @contextlib.contextmanager
    def _use_file(self, log_id: str) -> Iterator[tuple[Path, dict]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT file_key FROM logs WHERE id = ? AND status = 'saved'",
                (log_id,),
            ).fetchone()
        if row is None:
            raise CatalogError('Log not found', 404)
        path = self.files / row['file_key']
        try:
            handle = path.open('rb')
        except FileNotFoundError as error:
            raise CatalogError('Log file not found', 404) from error
        try:
            fcntl.flock(handle, fcntl.LOCK_SH)
            yield path, self._metadata_for_id(log_id, saved_only=True)
        finally:
            handle.close()

    def cleanup(self) -> dict:
        counts = {'removed': 0, 'skipped': 0, 'failed': 0}
        now = self.clock()
        with self._connect() as connection:
            rows = connection.execute(
                '''SELECT id, file_key FROM logs
                   WHERE status IN ('processing', 'deleting')
                      OR (status = 'temporary' AND expires_at <= ?)''',
                (now,),
            ).fetchall()
        for row in rows:
            path = self.files / row['file_key']
            try:
                handle = path.open('rb')
            except FileNotFoundError:
                if self._claim_cleanup(row['id'], now):
                    self._remove_row(row['id'])
                    counts['removed'] += 1
                continue
            except OSError:
                logger.exception('Could not open catalog cleanup candidate %s', path)
                counts['failed'] += 1
                continue
            try:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    counts['skipped'] += 1
                    continue
                if not self._claim_cleanup(row['id'], now):
                    continue
                try:
                    path.unlink()
                except OSError:
                    logger.exception('Could not unlink catalog cleanup candidate %s', path)
                    counts['failed'] += 1
                else:
                    self._remove_row(row['id'])
                    counts['removed'] += 1
            finally:
                handle.close()

        with self._connect() as connection:
            tracked = {
                row['file_key']
                for row in connection.execute('SELECT file_key FROM logs').fetchall()
            }
        staging_pattern = re.compile(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.[A-Za-z0-9]{1,16}$'
        )
        for path in self.files.iterdir():
            if path.name in tracked or not staging_pattern.fullmatch(path.name):
                continue
            try:
                handle = path.open('rb')
            except FileNotFoundError:
                continue
            except OSError:
                logger.exception('Could not open untracked catalog file %s', path)
                counts['failed'] += 1
                continue
            try:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    counts['skipped'] += 1
                    continue
                if os.fstat(handle.fileno()).st_mtime >= now - 3600:
                    continue
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    logger.exception('Could not unlink untracked catalog file %s', path)
                    counts['failed'] += 1
                else:
                    counts['removed'] += 1
            finally:
                handle.close()
        return counts

    def list_folders(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                'SELECT id, parent_id, name, created_at FROM folders ORDER BY id'
            ).fetchall()
        return [self._public_folder(row) for row in rows]

    def create_folder(self, name: str, parent_id: int = 1) -> dict:
        name = self._name(name, 120, 'folder name')
        if not isinstance(parent_id, int) or isinstance(parent_id, bool):
            raise CatalogError('Invalid parent folder', 400)
        now = self.clock()
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            parent = connection.execute(
                'SELECT id FROM folders WHERE id = ?', (parent_id,)
            ).fetchone()
            if parent is None:
                connection.rollback()
                raise CatalogError('Folder not found', 404)
            try:
                cursor = connection.execute(
                    'INSERT INTO folders(parent_id, name, created_at) VALUES (?, ?, ?)',
                    (parent_id, name, now),
                )
                folder_id = cursor.lastrowid
                if folder_id is None:
                    raise RuntimeError('SQLite did not return a folder ID')
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise CatalogError('A folder with that name already exists', 409) from error
        return self._folder_for_id(folder_id)

    def rename_folder(self, folder_id: int, name: str) -> dict:
        if not isinstance(folder_id, int) or isinstance(folder_id, bool):
            raise CatalogError('Invalid folder', 400)
        if folder_id == 1:
            raise CatalogError('The root folder cannot be renamed', 409)
        name = self._name(name, 120, 'folder name')
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                cursor = connection.execute(
                    'UPDATE folders SET name = ? WHERE id = ?', (name, folder_id)
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    raise CatalogError('Folder not found', 404)
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise CatalogError('A folder with that name already exists', 409) from error
        return self._folder_for_id(folder_id)

    def delete_folder(self, folder_id: int) -> None:
        if not isinstance(folder_id, int) or isinstance(folder_id, bool):
            raise CatalogError('Invalid folder', 400)
        if folder_id == 1:
            raise CatalogError('The root folder cannot be deleted', 409)
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                cursor = connection.execute('DELETE FROM folders WHERE id = ?', (folder_id,))
                if cursor.rowcount != 1:
                    connection.rollback()
                    raise CatalogError('Folder not found', 404)
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise CatalogError('Folder is not empty', 409) from error

    @staticmethod
    def _name(value: object, maximum: int, label: str) -> str:
        if not isinstance(value, str):
            raise CatalogError(f'Invalid {label}', 400)
        value = value.strip()
        if (
            not value
            or len(value) > maximum
            or '/' in value
            or '\\' in value
            or any(unicodedata.category(character).startswith('C') for character in value)
        ):
            raise CatalogError(f'Invalid {label}', 400)
        return value

    def _tags(self, value: object) -> list[str]:
        if not isinstance(value, list) or len(value) > 20:
            raise CatalogError('Invalid tags', 400)
        normalized: list[str] = []
        for item in value:
            tag = self._name(item, 40, 'tag').casefold()
            if tag not in normalized:
                normalized.append(tag)
        return normalized

    def _metadata_for_id(self, log_id: str, *, saved_only: bool = False) -> dict:
        with self._connect() as connection:
            clause = " AND l.status = 'saved'" if saved_only else ''
            row = connection.execute(
                f'''SELECT l.*, GROUP_CONCAT(t.name, char(31)) AS tag_names
                    FROM logs l
                    LEFT JOIN log_tags lt ON lt.log_id = l.id
                    LEFT JOIN tags t ON t.id = lt.tag_id
                    WHERE l.id = ?{clause}
                    GROUP BY l.id''',
                (log_id,),
            ).fetchone()
        if row is None:
            raise CatalogError('Log not found', 404)
        return self._public_metadata(row)

    def _folder_for_id(self, folder_id: int) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT id, parent_id, name, created_at FROM folders WHERE id = ?',
                (folder_id,),
            ).fetchone()
        if row is None:
            raise CatalogError('Folder not found', 404)
        return self._public_folder(row)

    @staticmethod
    def _timestamp(value: float | None) -> str | None:
        if value is None:
            return None
        return dt.datetime.fromtimestamp(value, dt.UTC).isoformat().replace('+00:00', 'Z')

    def _public_metadata(self, row: sqlite3.Row) -> dict:
        tag_names = row['tag_names']
        return {
            'id': row['id'],
            'status': row['status'],
            'original_filename': row['original_filename'],
            'size_bytes': row['size_bytes'],
            'created_at': self._timestamp(row['created_at']),
            'expires_at': self._timestamp(row['expires_at']),
            'saved_at': self._timestamp(row['saved_at']),
            'updated_at': self._timestamp(row['updated_at']),
            'display_name': row['display_name'],
            'folder_id': row['folder_id'],
            'tags': [] if tag_names is None else sorted(tag_names.split(chr(31))),
            'notes': row['notes'],
        }

    def _public_folder(self, row: sqlite3.Row) -> dict:
        return {
            'id': row['id'],
            'parent_id': row['parent_id'],
            'name': row['name'],
            'created_at': self._timestamp(row['created_at']),
        }

    def _mark_deleting(self, log_id: str) -> None:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute(
                "UPDATE logs SET status = 'deleting', updated_at = ? WHERE id = ?",
                (self.clock(), log_id),
            )
            connection.commit()

    def _claim_saved_for_deletion(self, log_id: str, file_key: str) -> None:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            cursor = connection.execute(
                '''UPDATE logs SET status = 'deleting', updated_at = ?
                   WHERE id = ? AND file_key = ? AND status = 'saved' ''',
                (self.clock(), log_id, file_key),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise CatalogError('Log is no longer available', 409)
            connection.commit()

    def _claim_cleanup(self, log_id: str, now: float) -> bool:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            cursor = connection.execute(
                '''UPDATE logs SET status = 'deleting', updated_at = ?
                   WHERE id = ? AND (
                       status IN ('processing', 'deleting') OR
                       (status = 'temporary' AND expires_at <= ?)
                   )''',
                (now, log_id, now),
            )
            connection.commit()
        return cursor.rowcount == 1

    def _remove_row(self, log_id: str) -> None:
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute("DELETE FROM logs WHERE id = ? AND status = 'deleting'", (log_id,))
            connection.commit()


def _cleanup_command(data_dir: Path) -> int:
    counts = {'removed': 0, 'skipped': 0, 'failed': 0}
    try:
        store = CatalogStore(data_dir)
        store.initialize()
        counts = store.cleanup()
    except Exception:
        counts['failed'] += 1
    print(
        f"removed={counts['removed']} "
        f"skipped={counts['skipped']} "
        f"failed={counts['failed']}"
    )
    return 1 if counts['failed'] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    cleanup = commands.add_parser('cleanup')
    cleanup.add_argument('--data-dir', type=Path, required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == 'cleanup':
        return _cleanup_command(arguments.data_dir)
    parser.error('unknown command')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
