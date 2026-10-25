"""Entrypoint: python app.py serves on PORT (8000)."""

import logging
import os

from waitress import serve

from hosting import create_app

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    serve(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), threads=8)
