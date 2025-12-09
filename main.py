# main.py
"""
GitHub-Actions-ready script:
- loggt sich in eine Seite ein (einfacher Login ohne CSRF)
- lädt eine JSON-Seite mit Noten (grades_url)
- beim ersten Lauf: speichert sample als previous.json und beendet sich
- später: findet neue grade-IDs, sendet E-Mail (ohne numerischen Wert),
  und updated previous.json (commit & push zurück in das Repo)
Konfiguration über Umgebungsvariablen (s.u.).
"""

import os
import sys
import json
import requests
import smtplib
from email.message import EmailMessage
from datetime import datetime
import subprocess

# ---- Konfiguration über ENV (setze als GitHub Secrets oder Action env) ----
LOGIN_URL = os.environ.get("LOGIN_URL")  # z.B. "https://example.com/login"
GRADES_URL = os.environ.get("GRADES_URL")  # URL, die das JSON mit "data" & "grades" zurückgibt
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

# Repo push (für commit back)
GIT_COMMIT_NAME = os.environ.get("GIT_COMMIT_NAME", "grades-bot")
GIT_COMMIT_EMAIL = os.environ.get("GIT_COMMIT_EMAIL", "action@users.noreply.github.com")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")  # GitHub Actions provides this automatically

# Pfade
PREV_FILE = "previous.json"
CURRENT_FILE = "current.json"

# ---- Hilfsfunktionen ----
def fatal(msg):
    print("ERROR:", msg)
    sys.exit(1)

def login_and_fetch(session):
    """
    Login ohne CSRF:
      - zuerst GET auf LOGIN_URL, um Session-Cookies zu erhalten
      - dann POST mit username/password
    """
    if not LOGIN_URL or not GRADES_URL or not LOGIN_USERNAME or not LOGIN_PASSWORD:
        fatal("Bitte setze LOGIN_URL, GRADES_URL, LOGIN_USERNAME, LOGIN_PASSWORD als Umgebungsvariablen.")
    
    headers = {"User-Agent": "grades-bot/1.0"}

    print("-> GET Login-Seite für Session-Cookies:", LOGIN_URL)
    r_get = session.get(LOGIN_URL, headers=headers, timeout=30)
    if r_get.status_code != 200:
        fatal(f"Fehler beim Laden der Login-Seite (HTTP {r_get.status_code})")

    print("-> POST Login-Daten")
    form = {LOGIN_FORM_FIELD_USER: LOGIN_USERNAME, LOGIN_FORM_FIELD_PASS: LOGIN_PASSWORD}
    r_post = session.post(LOGIN_URL, data=form, headers=headers, timeout=30)
    print("Login response:", r_post.status_code)
    if r_post.status_code not in (200, 302):
        fatal(f"Login scheint fehlgeschlagen (HTTP {r_post.status_code}). Antwort: {r_post.text[:400]}")

    print("-> Laden der Noten/geschützten Seite:", GRADES_URL)
    r2 = session.get(GRADES_URL, headers=headers, timeout=30)
    if r2.status_code != 200:
        fatal(f"Fehler beim Laden der Noten (HTTP {r2.status_code})")
    try:
        data = r2.json()
    except ValueError:
        fatal("Die geschützte Seite lieferte kein JSON. Wenn sie JS-rendered ist, musst du Selenium/Playwright verwenden.")
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
    session = requests.Session()
    data = login_and_fetch(session)

    with open(CURRENT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    try:
        grades = data["data"]["grades"]
    except Exception as e:
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
