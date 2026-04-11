import os
import threading

import psycopg2
import psycopg2.extras
import psycopg2.pool
from dotenv import load_dotenv
from utils.timezone import configure_process_timezone, get_app_timezone_name

load_dotenv()
configure_process_timezone()


class DbCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def close(self):
        return self._cursor.close()

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def __iter__(self):
        return iter(self._cursor)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class DbConnection:
    """
    Thin PostgreSQL connection wrapper.
    Keeps conn.execute(...) ergonomics used by the app without SQL translation.
    """

    def __init__(self, raw_conn, pool=None):
        self._conn = raw_conn
        self._pool = pool

    def execute(self, sql, params=None):
        cursor = self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if params is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql, tuple(params))
        return DbCursor(cursor)

    def executemany(self, sql, seq_of_params):
        cursor = self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.executemany(sql, seq_of_params)
        return DbCursor(cursor)

    def cursor(self, *args, **kwargs):
        if "cursor_factory" not in kwargs:
            kwargs["cursor_factory"] = psycopg2.extras.DictCursor
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def _reset_before_close(self):
        if getattr(self._conn, "closed", 1):
            return
        if getattr(self._conn, "autocommit", False):
            return
        try:
            self._conn.rollback()
        except Exception:
            # If rollback itself fails, the caller still needs the connection
            # removed from active use rather than returned dirty to the pool.
            raise

    def close(self):
        if self._pool is not None:
            try:
                self._reset_before_close()
            except Exception:
                return self._pool.putconn(self._conn, close=True)
            return self._pool.putconn(self._conn)

        try:
            self._reset_before_close()
        finally:
            return self._conn.close()

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._conn.__exit__(exc_type, exc, tb)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def get_db():
    pool = _get_pool()
    raw_conn = pool.getconn()
    raw_conn.autocommit = False
    with raw_conn.cursor() as cursor:
        cursor.execute("SET TIME ZONE %s", (get_app_timezone_name(),))
    return DbConnection(raw_conn, pool=pool)


def get_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.DictCursor)


_pool_lock = threading.Lock()
_db_pool = None


def _get_pool():
    global _db_pool
    if _db_pool is not None:
        return _db_pool

    with _pool_lock:
        if _db_pool is None:
            min_conn = int(os.environ.get("DB_POOL_MIN", 1))
            max_conn = int(os.environ.get("DB_POOL_MAX", 20))
            _db_pool = psycopg2.pool.ThreadedConnectionPool(
                min_conn,
                max_conn,
                host=os.environ["DB_HOST"],
                port=os.environ.get("DB_PORT", 5432),
                dbname=os.environ["DB_NAME"],
                user=os.environ["DB_USER"],
                password=os.environ["DB_PASSWORD"],
            )
    return _db_pool
