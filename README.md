# Otonom Odev Sistemi

Google Classroom odevlerini okuyup cevap ureten ve teslim etmeyi deneyen Python projesi.

## Kurulum

1. Python 3.10+ kurun.
2. Sanal ortam ve bagimliliklar:

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

3. `.env.example` dosyasini `.env` olarak kopyalayin ve anahtarlari doldurun.
4. Google Cloud Console'dan OAuth `credentials.json` indirin (Classroom + Drive API acik olmali).
5. Ilk giris:

```powershell
python auth.py
```

6. ChromeDriver'i [Chrome for Testing](https://googlechromelabs.github.io/chrome-for-testing/) uzerinden indirip proje klasorune `chromedriver.exe` olarak koyun.

## Calistirma

```powershell
python main.py
```

`DERSLER = []` iken aktif dersler Classroom API'den otomatik alinir.

## Guvenlik

Asagidakiler **asla** repoya eklenmemelidir:

- `.env`
- `credentials.json`
- `token.json`
- `chrome_profile/`

Anahtar veya sifre sizdiysa hemen yenileyin (Google + Anthropic).
