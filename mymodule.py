import os
import re
import sqlite3
import subprocess

from flask import Flask, abort, jsonify, request
from flask_wtf.csrf import CSRFProtect

import settings as settings_store

AWS_DEFAULT_REGION = os.environ.get("AWS_DEFAULT_REGION", "eu-central-1")

INVENTORY_DB = "inventory.db"
ARCHIVE_ROOT = "/var/lib/inventory/archive"

# Snapshot names end up in a filesystem path, so keep them to a character set
# that cannot express a directory traversal in the first place.
SNAPSHOT_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")

app = Flask(__name__)
app.secret_key = settings_store.secret_key()
csrf = CSRFProtect(app)


def connect():
    """Open a connection to the inventory database."""
    return sqlite3.connect(INVENTORY_DB)


def normalize_sku(sku):
    """Strip surrounding whitespace and upper-case a SKU."""
    return sku.strip().upper()


def total_value(items):
    """Return the summed value of the given inventory rows."""
    return sum(quantity * price for _, quantity, price in items)


@app.route("/items", methods=["GET"])
def list_items():
    """Return the inventory rows for a warehouse."""
    warehouse = request.args.get("warehouse", "")
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT sku, quantity, price FROM items WHERE warehouse = ?", (warehouse,)
    )
    rows = cursor.fetchall()
    connection.close()
    return jsonify(items=rows, total=total_value(rows))


@app.route("/items/<sku>", methods=["GET"])
def item_detail(sku):
    """Return a single inventory row by SKU."""
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT sku, quantity, price FROM items WHERE sku = ?", (normalize_sku(sku),)
    )
    row = cursor.fetchone()
    connection.close()
    return jsonify(item=row)


@app.route("/archive", methods=["POST"])
def archive_snapshot():
    """Compress a warehouse snapshot directory and return the tar output."""
    snapshot = request.form.get("snapshot", "latest")
    if not SNAPSHOT_NAME.fullmatch(snapshot):
        abort(400, "Invalid snapshot name.")

    # No shell: tar receives the archive and source paths as separate argv
    # entries, so nothing in them can be interpreted as a command.
    output = subprocess.check_output(
        [
            "tar",
            "-czf",
            os.path.join(ARCHIVE_ROOT, snapshot + ".tar.gz"),
            os.path.join(ARCHIVE_ROOT, snapshot),
        ],
        shell=False,
    )
    return jsonify(archived=snapshot, output=output.decode())


@app.route("/health", methods=["GET"])
def health():
    """Return a static health payload."""
    return jsonify(status="ok", region=AWS_DEFAULT_REGION)
