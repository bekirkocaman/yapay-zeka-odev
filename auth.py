"""
Google OAuth 2.0 ile Classroom ve Drive erişim token'ı oluşturur.

Kullanım:
    python auth.py

Çıktı:
    token.json (git'e eklenmemeli)
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/classroom.coursework.me",
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    "https://www.googleapis.com/auth/drive",
]


def main() -> None:
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    creds = flow.run_local_server(port=0)
    with open("token.json", "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    print("token.json oluşturuldu.")


if __name__ == "__main__":
    main()
