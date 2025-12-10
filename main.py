# main.py
"""
GitHub-Actions-ready script using Playwright for JS-login:
- loggt sich in eine JS-seitige Seite ein
- lädt JSON-Seite mit Noten (grades_url)
- beim ersten Lauf: speichert sample als previous.json und beendet sich
- später: findet neue grade-IDs, sendet E-Mail (ohne numerischen Wert)
- updated previous.json (commit & push)
"""

import os
import sys
import json
import smtplib
from email.message import EmailMessage
from datetime import datetime
import subprocess
from playwright.sync_api import sync_playwright

# ---- Repository Secrets Wrapper ----
def load_secrets():
    raw = os.environ.get("BOT_SECRETS")
    if not raw:
        print("ERROR: BOT_SECRETS not provided. Add it as a GitHub repository secret.")
        sys.exit(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print("ERROR: BOT_SECRETS is not valid JSON")
        sys.exit(1)

SECRETS = load_secrets()

# ---- Konfiguration aus Repository Secrets ----
LOGIN_URL = SECRETS.get("LOGIN_URL")
GRADES_URL = SECRETS.get("GRADES_URL")
LOGIN_FORM_FIELD_USER = SECRETS.get("LOGIN_FORM_FIELD_USER", "username")
LOGIN_FORM_FIELD_PASS = SECRETS.get("LOGIN_FORM_FIELD_PASS", "password")
LOGIN_USERNAME = SECRETS.get("LOGIN_USERNAME")
LOGIN_PASSWORD = SECRETS.get("LOGIN_PASSWORD")

# SMTP / Mail
SMTP_HOST = SECRETS.get("SMTP_HOST")
SMTP_PORT = int(SECRETS.get("SMTP_PORT", "587"))
SMTP_USER = SECRETS.get("SMTP_USER")
SMTP_PASS = SECRETS.get("SMTP_PASS")
RECIPIENT = SECRETS.get("RECIPIENT_EMAIL")
SENDER = SECRETS.get("SENDER_EMAIL") or SMTP_USER

# Git settings
GIT_COMMIT_NAME = SECRETS.get("GIT_COMMIT_NAME", "grades-bot")
GIT_COMMIT_EMAIL = SECRETS.get("GIT_COMMIT_EMAIL", "action@users.noreply.github.com")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

# Paths
PREV_FILE = "previous.json"
CURRENT_FILE = "current.json"

# ---- Helpers ----
def fatal(msg):
    print("ERROR:", msg)
    sys.exit(1)

def login_and_fetch():
    """
    Login via Playwright and return JSON page content.
    """

    if not LOGIN_URL or not GRADES_URL or not LOGIN_USERNAME or not LOGIN_PASSWORD:
        fatal("Missing required secrets: LOGIN_URL, GRADES_URL, LOGIN_USERNAME, LOGIN_PASSWORD")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        print("-> Opening login page")
        page.goto(LOGIN_URL, timeout=60000)

        page.fill(f'input[name="{LOGIN_FORM_FIELD_USER}"]', LOGIN_USERNAME)
        page.fill(f'input[name="{LOGIN_FORM_FIELD_PASS}"]', LOGIN_PASSWORD)

        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state("networkidle", timeout=30000)

        print("-> Login successful, loading grades JSON")
        page.goto(GRADES_URL)
        page.wait_for_load_state("networkidle", timeout=30000)

        try:
            json_text = page.inner_text("body")
            data = json.loads(json_text)
        except Exception:
            fatal("Fehler: Grades URL liefert kein JSON. Falsche URL? Seite JS-gerendert?")

        browser.close()
        return data

def send_email(subject, body, to_addr):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SENDER
    msg["To"] = to_addr
    msg.set_content(body)

    print("-> Sending mail to", to_addr)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)
    print("-> Mail sent")

def git_commit_and_push(files, message):
    subprocess.check_call(["git", "config", "user.name", GIT_COMMIT_NAME])
    subprocess.check_call(["git", "config", "user.email", GIT_COMMIT_EMAIL])
    subprocess.check_call(["git", "add"] + files)
    subprocess.check_call(["git", "commit", "-m", message])

    if GITHUB_TOKEN:
        origin_url = subprocess.check_output(["git", "remote", "get-url", "origin"]).decode().strip()
        if origin_url.startswith("https://"):
            auth_url = origin_url.replace("https://", f"https://x-access-token:{GITHUB_TOKEN}@")
            subprocess.check_call(["git", "remote", "set-url", "origin", auth_url])
        subprocess.check_call(["git", "push"])
    else:
        print("WARNING: No GITHUB_TOKEN set — skipping push")

# ---- Main ----
def main():
    data = login_and_fetch()

    with open(CURRENT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    try:
        grades = data["data"]["grades"]
    except:
        fatal("Unexpected JSON structure — expected data.data.grades")

    if not os.path.exists(PREV_FILE):
        print("-> First run: saving previous.json and exiting")
        subprocess.check_call(["git", "add", CURRENT_FILE])
        subprocess.check_call(["git", "mv", CURRENT_FILE, PREV_FILE])
        git_commit_and_push([PREV_FILE], f"Initial sample {datetime.utcnow().isoformat()}Z")
        return

    with open(PREV_FILE, "r", encoding="utf-8") as f:
        prev = json.load(f)

    prev_ids = {g["id"] for g in prev.get("data", {}).get("grades", [])}
    curr_ids = {g["id"] for g in grades}
    new_ids = sorted(list(curr_ids - prev_ids))

    if not new_ids:
        print("-> No new grades found")
        if os.path.exists(CURRENT_FILE):
            os.remove(CURRENT_FILE)
        return

    print("-> New grade IDs:", new_ids)

    notifications = []
    for g in grades:
        if g["id"] in new_ids:
            subject = (
                g.get("collection", {}).get("subject", {}).get("name")
                or g.get("collection", {}).get("subject", {}).get("local_id")
                or "Unbekanntes Fach"
            )
            collection = g.get("collection", {}).get("name") or "Unbenannte Sammlung"
            notifications.append({
                "id": g["id"],
                "subject": subject,
                "collection": collection,
                "given_at": g.get("given_at")
            })

    subj = f"[Noten-Update] {len(notifications)} neue Einträge"
    body = "Neue Note(n):\n\n" + "\n\n".join(
        f"- Fach: {n['subject']}\n  Bezeichnung: {n['collection']}\n  Datum: {n['given_at']}"
        for n in notifications
    ) + "\n\n(Hinweis: Der numerische Wert wird aus Datenschutzgründen nicht angezeigt.)"

    send_email(subj, body, RECIPIENT)

    with open(PREV_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    git_commit_and_push([PREV_FILE], f"Updated sample {datetime.utcnow().isoformat()}Z")
    print("-> Done.")

if __name__ == "__main__":
    main()
