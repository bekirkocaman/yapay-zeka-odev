"""Gemini API üzerinden erişilebilir modelleri listeler (test aracı)."""

import os
import sys

from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()


def main() -> None:
    api_key = os.getenv("API_KEY")
    if not api_key:
        print("Hata: .env dosyasında API_KEY bulunamadı.")
        sys.exit(1)

    genai.configure(api_key=api_key)
    print("Kullanılabilir Gemini modelleri:\n")

    try:
        modeller = [
            m.name
            for m in genai.list_models()
            if "generateContent" in m.supported_generation_methods
        ]
        for ad in modeller:
            print(f"  [+] {ad}")
        if not modeller:
            print("  [-] Hiçbir modele erişim yok. API anahtarını kontrol edin.")
    except Exception as e:
        print(f"Bağlantı hatası: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()