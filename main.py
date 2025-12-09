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

# ---- Konfiguration über ENV ----
LOGIN_URL = os.environ.get("LOGIN_URL")
GRADES_URL = os.environ.get("GRADES_URL")
LOGIN_FORM_FIELD_USER = os.environ.get("LOGIN_FORM_FIELD_USER", "username")
LOGIN_FORM_FIELD_PASS = os.environ.get("LOGIN_FORM_FIELD_PASS", "password")
LOGIN_USERNAME = os.environ.get("LOGIN_USERNAME")
LOGIN_PASSWORD = os.environ.get("LOGIN_PASSWORD")

# SMTP / Mail
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
RECIPIENT = os.environ.get("RECIPIENT_EMAIL")
SENDER = os.environ.get("SENDER_EMAIL") or SMTP_USER

# Repo push
GIT_COMMIT_NAME = os.environ.get("GIT_COMMIT_NAME", "grades-bot")
GIT_COMMIT_EMAIL = os.environ.get("GIT_COMMIT_EMAIL", "action@users.noreply.github.com")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

# Pfade
PREV_FILE = "previous.json"
CURRENT_FILE = "current.json"

# ---- Hilfsfunktionen ----
def fatal(msg):
    print("ERROR:", msg)
    sys.exit(1)

def login_and_fetch():
    """
    Login mit Playwright (JS-fähig) und return JSON-Daten der Noten.
    """
    if not LOGIN_URL or not GRADES_URL or not LOGIN_USERNAME or not LOGIN_PASSWORD:
        fatal("Bitte setze LOGIN_URL, GRADES_URL, LOGIN_USERNAME, LOGIN_PASSWORD als Umgebungsvariablen.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        print("-> Navigating to login page")
        page.goto(LOGIN_URL, timeout=60000)
        
        # fill username/password fields
        page.fill(f'input[name="{LOGIN_FORM_FIELD_USER}"]', LOGIN_USERNAME)
        page.fill(f'input[name="{LOGIN_FORM_FIELD_PASS}"]', LOGIN_PASSWORD)
        # click login button (assume it's a button or input[type=submit])
        page.click('button[type="submit"], input[type="submit"]')
        
        # wait until navigation or page has loaded JSON link
        page.wait_for_load_state("networkidle", timeout=30000)
        
        print("-> Logged in, fetching grades JSON")
        page.goto(GRADES_URL)
        page.wait_for_load_state("networkidle", timeout=30000)
        
        # extract JSON content
        content = page.content()
        # assume the page returns raw JSON
        try:
            json_text = page.inner_text("body")
            data = json.loads(json_text)
        except Exception as e:
            fatal("Fehler beim Parsen der JSON-Seite nach Login. Eventuell JS-rendered, oder falsche URL.")
        
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
    print("Mail sent.")

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
            subprocess.check_call(["git", "push"])
    else:
        print("Kein GITHUB_TOKEN gesetzt — push skipped.")

# ---- Main Ablauf ----
def main():
    data = login_and_fetch()

    with open(CURRENT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    try:
        grades = data["data"]["grades"]
    except Exception:
        fatal("JSON-Struktur unerwartet. Erwartet: data.data.grades")

    if not os.path.exists(PREV_FILE):
        print("Kein previous.json gefunden — Erstlauf: Speichern als sample und beenden.")
        subprocess.check_call(["git", "add", CURRENT_FILE])
        subprocess.check_call(["git", "mv", CURRENT_FILE, PREV_FILE])
        git_commit_and_push([PREV_FILE], f"chore: add initial sample {datetime.utcnow().isoformat()}Z")
        print("Sample saved. Exit.")
        return

    with open(PREV_FILE, "r", encoding="utf-8") as f:
        prev = json.load(f)
    prev_grades = prev.get("data", {}).get("grades", [])

    prev_ids = {g["id"] for g in prev_grades}
    curr_ids = {g["id"] for g in grades}

    new_ids = sorted(list(curr_ids - prev_ids))
    if not new_ids:
        print("Keine neuen Noten gefunden.")
        if os.path.exists(CURRENT_FILE):
            os.remove(CURRENT_FILE)
        return

    print(f"Gefundene neue grade IDs: {new_ids}")

    notifications = []
    for g in grades:
        if g["id"] in new_ids:
            subject = g.get("collection", {}).get("subject", {}).get("name") or g.get("collection", {}).get("subject", {}).get("local_id") or "Unbekanntes Fach"
            collection_name = g.get("collection", {}).get("name") or "Unbenannte Sammlung"
            notifications.append({"id": g["id"], "subject": subject, "collection": collection_name, "given_at": g.get("given_at")})

    subj = f"[Noten-Update] {len(notifications)} neue(n) Eintrag(e)"
    body_lines = []
    for n in notifications:
        body_lines.append(f"- Fach: {n['subject']}\n  Bezeichnung: {n['collection']}\n  Datum: {n.get('given_at')}")
    body = "Neue Note(n) entdeckt:\n\n" + "\n\n".join(body_lines) + "\n\n(Hinweis: Numerischer Wert der Note wird aus Datenschutzgründen nicht angezeigt.)"

    send_email(subj, body, RECIPIENT)

    with open(PREV_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    git_commit_and_push([PREV_FILE], f"chore: update sample after detected new grades {datetime.utcnow().isoformat()}Z")
    print("Updated sample committed & pushed. Done.")

if __name__ == "__main__":
    main()
