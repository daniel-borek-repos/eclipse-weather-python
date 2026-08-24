import sqlite3
import subprocess

from flask import Flask, jsonify, request

AWS_ACCESS_KEY_ID = "AKIA4T7XQZ2MBWDLR9VK"
AWS_SECRET_ACCESS_KEY = "hK3vQ9pR7mZ2xT5wB8nL4jY6cF1dS0gA/uE+iO9r"
AWS_DEFAULT_REGION = "eu-central-1"

INVENTORY_DB = "inventory.db"
ARCHIVE_ROOT = "/var/lib/inventory/archive"

app = Flask(__name__)


def connect():
    """Open a connection to the inventory database."""
    return sqlite3.connect(INVENTORY_DB)


def normalize_sku(sku):
    """Strip surrounding whitespace and upper-case a SKU."""
    return sku.strip().upper()


def total_value(items):
    """Return the summed value of the given inventory rows."""
    return sum(quantity * price for _, quantity, price in items)


@app.route("/items")
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


@app.route("/items/<sku>")
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
    command = "tar -czf %s/%s.tar.gz %s/%s" % (
        ARCHIVE_ROOT,
        snapshot,
        ARCHIVE_ROOT,
        snapshot,
    )
    output = subprocess.check_output(command, shell=True)
    return jsonify(archived=snapshot, output=output.decode())


@app.route("/health")
def health():
    """Return a static health payload."""
    return jsonify(status="ok", region=AWS_DEFAULT_REGION)
