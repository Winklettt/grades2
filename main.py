# main.py
"""
Extended debug version:
- prints EXACT username & password entered
- prints what is filled in each field
- takes screenshots before and after login attempt
- tries multiple login approaches
"""

import os
import sys
import json
import smtplib
import base64
from email.message import EmailMessage
from datetime import datetime
import subprocess
from playwright.sync_api import sync_playwright


# ---- Repository Secrets Wrapper ----
def load_secrets():
    raw_b64 = os.environ.get("BOT_SECRETS_B64")
    if not raw_b64:
        print("ERROR: BOT_SECRETS_B64 not provided.")
        sys.exit(1)

    try:
        decoded = base64.b64decode(raw_b64).decode("utf-8")
        return json.loads(decoded)
    except Exception as e:
        print("ERROR: Failed to decode BOT_SECRETS_B64:", e)
        sys.exit(1)


SECRETS = load_secrets()

LOGIN_URL = SECRETS.get("LOGIN_URL")
GRADES_URL = SECRETS.get("GRADES_URL")
LOGIN_USERNAME = SECRETS.get("LOGIN_USERNAME")
LOGIN_PASSWORD = SECRETS.get("LOGIN_PASSWORD")
LOGIN_FORM_FIELD_USER = SECRETS.get("LOGIN_FORM_FIELD_USER", "username")
LOGIN_FORM_FIELD_PASS = SECRETS.get("LOGIN_FORM_FIELD_PASS", "password")

SMTP_HOST = SECRETS.get("SMTP_HOST")
SMTP_PORT = int(SECRETS.get("SMTP_PORT", "587"))
SMTP_USER = SECRETS.get("SMTP_USER")
SMTP_PASS = SECRETS.get("SMTP_PASS")
RECIPIENT = SECRETS.get("RECIPIENT_EMAIL")
SENDER = SECRETS.get("SENDER_EMAIL") or SMTP_USER

GIT_COMMIT_NAME = SECRETS.get("GIT_COMMIT_NAME", "grades-bot")
GIT_COMMIT_EMAIL = SECRETS.get("GIT_COMMIT_EMAIL", "action@users.noreply.github.com")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

PREV_FILE = "previous.json"
CURRENT_FILE = "current.json"


def fatal(msg):
    print("ERROR:", msg)
    sys.exit(1)


def login_and_fetch():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        print("-> Opening login page")
        page.goto(LOGIN_URL, timeout=60000)
        page.wait_for_load_state("networkidle")

        page.screenshot(path="debug_before_login.png")
        print("-> Screenshot saved: debug_before_login.png")

        print("-> Login page loaded. Scanning inputs...")
        inputs = page.locator("input").all()
        for i, inp in enumerate(inputs):
            name = inp.get_attribute("name")
            itype = inp.get_attribute("type")
            print(f"   {i}: name={name}, type={itype}")

        possible_user_fields = [
            LOGIN_FORM_FIELD_USER,
            "identifier",
            "username",
            "email",
            "user",
        ]

        possible_pass_fields = [
            LOGIN_FORM_FIELD_PASS,
            "password",
            "pass",
            "passwd",
        ]

        username_selector = None
        password_selector = None

        for field in possible_user_fields:
            sel = f'input[name="{field}"]'
            if page.locator(sel).count() > 0:
                username_selector = sel
                break

        for field in possible_pass_fields:
            sel = f'input[name="{field}"]'
            if page.locator(sel).count() > 0:
                password_selector = sel
                break

        if not username_selector:
            fatal("Could not find username field.")
        if not password_selector:
            fatal("Could not find password field.")

        print(f"-> Using username selector: {username_selector}")
        print(f"-> Using password selector: {password_selector}")

        print(f"-> ENTERING USERNAME: {LOGIN_USERNAME}")
        page.fill(username_selector, LOGIN_USERNAME)
        print("-> Username field now contains:", page.locator(username_selector).input_value())

        print(f"-> ENTERING PASSWORD: {LOGIN_PASSWORD}")
        page.fill(password_selector, LOGIN_PASSWORD)
        print("-> Password field now contains:", page.locator(password_selector).input_value())

        print("-> Trying ENTER key login")
        page.press(password_selector, "Enter")

        page.wait_for_timeout(2000)

        still_login = (page.locator(username_selector).count() > 0)

        if still_login:
            print("-> ENTER did not submit. Trying button clicks...")

            buttons = page.locator("button, input[type=submit]").all()
            print(f"-> Found {len(buttons)} clickable elements")

            for i, b in enumerate(buttons):
                try:
                    txt = b.inner_text()
                except:
                    txt = "(no text)"

                print(f"   Trying click #{i}: '{txt}'")
                try:
                    b.click(timeout=3000)
                    page.wait_for_timeout(2000)
                    if page.locator(username_selector).count() == 0:
                        print("-> Login successful after clicking button!")
                        break
                except:
                    pass

        if page.locator(username_selector).count() > 0:
            page.screenshot(path="debug_after_failed_login.png")
            print("-> Screenshot saved: debug_after_failed_login.png")
            fatal("Login did not succeed. Check credentials!")

        print("-> Login successful (page changed).")

        print("-> Loading grades JSON...")
        page.goto(GRADES_URL, timeout=60000)
        page.wait_for_load_state("networkidle")

        json_text = page.inner_text("body")
        data = json.loads(json_text)

        browser.close()
        return data


def send_email(subject, body, to_addr):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SENDER
    msg["To"] = to_addr
    msg.set_content(body)

    print("-> Sending mail…")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)
    print("-> Mail sent.")


def git_commit_and_push(files, message):
    subprocess.check_call(["git", "config", "user.name", GIT_COMMIT_NAME])
    subprocess.check_call(["git", "config", "user.email", GIT_COMMIT_EMAIL])
    subprocess.check_call(["git", "add"] + files)

    try:
        subprocess.check_call(["git", "commit", "-m", message])
    except subprocess.CalledProcessError:
        print("-> Nothing to commit")
        return

    if not GITHUB_TOKEN:
        print("-> No GITHUB_TOKEN. Skipping push.")
        return

    origin = subprocess.check_output(["git", "remote", "get-url", "origin"]).decode().strip()
    if origin.startswith("https://"):
        authenticated = origin.replace("https://", f"https://x-access-token:{GITHUB_TOKEN}@")
        subprocess.check_call(["git", "remote", "set-url", "origin", authenticated])

    subprocess.check_call(["git", "push"])
    print("-> Push OK.")


def main():
    data = login_and_fetch()

    with open(CURRENT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    try:
        grades = data["data"]["grades"]
    except:
        fatal("JSON format unexpected.")

    if not os.path.exists(PREV_FILE):
        print("-> First run, storing previous.json")
        subprocess.check_call(["git", "mv", CURRENT_FILE, PREV_FILE])
        git_commit_and_push([PREV_FILE], "Initial sample")
        return

    with open(PREV_FILE, "r", encoding="utf-8") as f:
        prev = json.load(f)

    prev_ids = {g["id"] for g in prev.get("data", {}).get("grades", [])}
    curr_ids = {g["id"] for g in grades}

    new_ids = sorted(curr_ids - prev_ids)

    if not new_ids:
        print("-> No new grades.")
        return

    notes = []
    for g in grades:
        if g["id"] in new_ids:
            notes.append({
                "id": g["id"],
                "subject": g.get("collection", {}).get("subject", {}).get("name"),
                "collection": g.get("collection", {}).get("name"),
                "given_at": g.get("given_at")
            })

    subject = f"[Noten-Update] {len(notes)} neue Einträge"
    body = "\n\n".join(
        f"Fach: {n['subject']}\nBezeichnung: {n['collection']}\nDatum: {n['given_at']}"
        for n in notes
    )

    send_email(subject, body, RECIPIENT)

    with open(PREV_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    git_commit_and_push([PREV_FILE], "Updated sample")


if __name__ == "__main__":
    main()
