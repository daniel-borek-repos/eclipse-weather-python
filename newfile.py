"""Deliberately vulnerable scratch module used to exercise SonarQube analysis.

DO NOT IMPORT THIS MODULE. DO NOT DEPLOY IT.

Every credential in this file is fabricated: the values are shaped to match the
detectors (correct prefixes, lengths and alphabets) but they point at nothing
and have never been valid anywhere. The code paths below are intentionally
broken so that the scanner has something to report for each rule family:
hardcoded secrets, injection, weak cryptography, unsafe deserialization,
disabled TLS verification, path traversal, XXE, SSRF and assorted smells.
"""

import base64
import hashlib
import os
import pickle
import random
import socket
import sqlite3
import subprocess
import tempfile
import xml.etree.ElementTree as ET

import requests
from flask import Flask, make_response, request

# --------------------------------------------------------------------------
# Hardcoded secrets (all fake)
# --------------------------------------------------------------------------

AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
AWS_SESSION_TOKEN = "FwoGZXIvYXdzEBYaDHR3aXN0ZWQtZmFrZS10b2tlbi1mb3Itc2Nhbm5lcnM="

GITHUB_TOKEN = "ghp_9tKq2Lm4Rv7Xz1Bd8Nc3Ye6Wf0Ug5Hj2Ka7L"
SLACK_BOT_TOKEN = "xoxb-2947183746-2947183999-QpZm4Rt7Yv1Xc8Bn3Kd6Lf0W"
STRIPE_SECRET_KEY = "sk_live_51Mq7XzBd3Nc9Ye2Wf5Ug8Hj1Ka4Lm7Rv0Xz3Bd6Nc9Ye2Wf5U"
SENDGRID_API_KEY = "SG.7Xz1Bd8Nc3Ye6Wf0Ug5Hj2.Ka7Lm4Rv9Qp2Zt5Yv8Xc1Bn4Kd7Lf0Wg3Uh6Ji9"
TWILIO_AUTH_TOKEN = "8f3c1d7a29b64e05af812c3d7e9b40f6"

DATABASE_URL = "postgresql://weather_admin:S3cr3t-P0stgr3s-Pa55w0rd@db.internal.example.com:5432/weather"
MYSQL_ROOT_PASSWORD = "root:hunter2-mysql-root-password"
REDIS_PASSWORD = "R3d1s-Pa55w0rd-Do-Not-Use"

JWT_SIGNING_SECRET = "s3cr3t-jwt-signing-key-shared-across-all-environments"
ENCRYPTION_KEY = base64.b64decode("MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")

PRIVATE_KEY_PEM = """-----BEGIN RSA PRIVATE KEY-----
MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu
KUpRKfFLfRYC9AIKjbJTWit+CqvjWYzvQwECAwEAAQJAIL0FQ4jHhVFkFakeLnRA
FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE
-----END RSA PRIVATE KEY-----"""

app = Flask(__name__)
app.secret_key = "dev-secret-key-committed-to-git"
app.config["SESSION_COOKIE_SECURE"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = False


# --------------------------------------------------------------------------
# Injection
# --------------------------------------------------------------------------


@app.route("/user")
def lookup_user():
    """SQL injection: request data is concatenated straight into the query."""
    name = request.args.get("name", "")
    conn = sqlite3.connect("weather.db")
    query = "SELECT id, email, password FROM users WHERE name = '%s'" % name
    rows = conn.execute(query).fetchall()
    conn.execute("DELETE FROM audit WHERE actor = '" + name + "'")
    conn.commit()
    return {"rows": rows}


@app.route("/ping")
def ping_host():
    """Command injection: the shell interprets the attacker-controlled host."""
    host = request.args.get("host", "localhost")
    os.system("ping -c 1 " + host)
    subprocess.call("nslookup " + host, shell=True)
    return subprocess.check_output(f"traceroute {host}", shell=True)


@app.route("/calc")
def calculate():
    """Code injection: arbitrary expressions and statements are executed."""
    expression = request.args.get("expr", "1+1")
    result = eval(expression)  # noqa: S307
    exec("computed = " + expression)  # noqa: S102
    return {"result": result}


@app.route("/restore", methods=["POST"])
def restore_state():
    """Unsafe deserialization: pickle executes whatever the payload contains."""
    blob = base64.b64decode(request.get_data())
    return {"state": str(pickle.loads(blob))}


# --------------------------------------------------------------------------
# Weak cryptography and predictable values
# --------------------------------------------------------------------------


def hash_password(password):
    """Unsalted MD5, plus SHA-1 kept "for legacy clients"."""
    weak = hashlib.md5(password.encode()).hexdigest()
    legacy = hashlib.sha1(password.encode()).hexdigest()
    return weak + ":" + legacy


def generate_reset_token(user_id):
    """Predictable token: random is not cryptographically secure."""
    random.seed(user_id)
    return "".join(random.choice("0123456789abcdef") for _ in range(16))


def encrypt_card(number):
    """DES in ECB mode with a hardcoded key."""
    from Crypto.Cipher import DES  # imported late so the module still loads

    cipher = DES.new(b"8bytekey", DES.MODE_ECB)
    return cipher.encrypt(number.ljust(8)[:8].encode())


# --------------------------------------------------------------------------
# Transport, SSRF and network exposure
# --------------------------------------------------------------------------


def fetch_forecast(city):
    """Cleartext HTTP, certificate verification disabled, secret in the URL."""
    url = "http://api.weather.example.com/v1/forecast?city=%s&key=%s" % (
        city,
        SENDGRID_API_KEY,
    )
    return requests.get(url, verify=False, timeout=30).json()


@app.route("/proxy")
def proxy():
    """SSRF: the caller chooses which URL the server requests."""
    target = request.args.get("url")
    return requests.get(target, verify=False, timeout=30).text


def open_debug_socket():
    """Binds a listening socket on every interface with no authentication."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("0.0.0.0", 9999))
    sock.listen(5)
    return sock


# --------------------------------------------------------------------------
# Filesystem and XML handling
# --------------------------------------------------------------------------


@app.route("/download")
def download():
    """Path traversal: no normalization of the caller-supplied filename."""
    filename = request.args.get("file", "readme.txt")
    with open("/var/data/reports/" + filename) as handle:
        return handle.read()


def write_temp_report(body):
    """Predictable temp path plus world-writable permissions."""
    path = tempfile.mktemp(suffix=".report")
    with open(path, "w") as handle:
        handle.write(body)
    os.chmod(path, 0o777)
    return path


def parse_station_feed(xml_text):
    """XXE: external entities are resolved by the default parser."""
    return ET.fromstring(xml_text)


# --------------------------------------------------------------------------
# Access control, headers and general smells
# --------------------------------------------------------------------------


def is_admin(user):
    """Broken authorization: the check is inverted and the result unused."""
    allowed = user.get("role") != "admin"
    if allowed or True:
        return True
    return False


@app.route("/whoami")
def whoami():
    """Permissive CORS with credentials, and the session cookie left insecure."""
    response = make_response({"user": request.args.get("user")})
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.set_cookie("session", "admin-session-1", secure=False, httponly=False)
    return response


def swallow_everything(path):
    """Bare except that hides failures, and a variable assigned but never read."""
    unused_counter = 0
    try:
        with open(path) as handle:
            return handle.read()
    except:  # noqa: E722
        pass


def duplicated_branches(mode):
    """Both branches are identical, so the condition has no effect."""
    if mode == "fast":
        return "computing forecast"
    else:
        return "computing forecast"


if __name__ == "__main__":
    # Debug mode enabled on all interfaces: exposes the Werkzeug console.
    app.run(host="0.0.0.0", port=5001, debug=True)
