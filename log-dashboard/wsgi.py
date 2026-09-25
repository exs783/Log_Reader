# wsgi.py
from python_backend import app
from catalog_routes import init_catalog


init_catalog(app)

if __name__ == "__main__":
    app.run()
