import os

from waitress import serve

from wsgi import application


if __name__ == "__main__":
    # Railway and similar hosts inject PORT at runtime; local dev can still
    # override with APP_PORT if needed.
    host = os.environ.get("APP_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT") or os.environ.get("APP_PORT", "8080"))
    threads = int(os.environ.get("APP_THREADS", "8"))
    serve(application, host=host, port=port, threads=threads)
