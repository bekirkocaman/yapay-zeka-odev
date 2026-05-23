import os
from dotenv import load_dotenv
import google.generativeai as genai

# .env dosyasından API anahtarını alıyoruz
load_dotenv()
api_key = os.getenv("API_KEY")

if not api_key:
    print("Hata: .env dosyasında API_KEY bulunamadı!")
    exit()

genai.configure(api_key=api_key)

print("Kullanılabilir yapay zeka modelleri taranıyor...\n")

try:
    modeller = []
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f"  [+] Erişim İzni Var: {m.name}")
            modeller.append(m.name)
            
    if not modeller:
        print("  [-] Uyarı: API anahtarın hiçbir modele erişemiyor! (Yeni bir key alman lazım)")
        
except Exception as e:
    print(f"\nBağlantı Hatası: {e}\n(Muhtemelen API key yanlış yerden alındı veya engelli.)")