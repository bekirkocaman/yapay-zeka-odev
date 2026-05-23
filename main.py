"""
Google Classroom ödev otomasyonu — okuma, AI cevap, PDF ve teslim.
"""

import io, datetime, re, time, os, docx, base64
import requests
import anthropic
from dotenv import load_dotenv

load_dotenv()

import undetected_chromedriver as uc
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from PyPDF2 import PdfReader

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_LEFT

# ===================== AYARLAR =====================
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GOOGLE_EMAIL      = os.getenv("GOOGLE_EMAIL", "")
GOOGLE_PASSWORD   = os.getenv("GOOGLE_PASSWORD", "")
# Bos liste = Classroom API'den aktif dersler otomatik alinir
DERSLER           = []
ATLANACAK_DERS_KELIMELERI = ["grammar"]
SON_KAC_GUN       = 14
MISSING_ODEVLERI_DAHIL_ET = True
CHROMEDRIVER_YOLU = r"chromedriver.exe"
TESLIM_KAYIT_DOSYASI = "teslim_edilenler.txt"
CHROME_ACIK_KALSIN = True
# ===================================================

claude_client = anthropic.Anthropic(
    api_key=ANTHROPIC_API_KEY,
    timeout=45,
    max_retries=0,
)

chrome_driver = None


# ───────────────────────────────────────────────
# API ILE TESLIM (Selenium yok)
# ───────────────────────────────────────────────

def api_ile_teslim_et(cr, drive, course_id: str, task_id: str,
                      submission_id: str, pdf_yolu: str, baslik: str,
                      sub_state: str = "") -> bool:
    try:
        if sub_state == "RETURNED":
            print("    Odev zaten graded/returned; atlaniyor.")
            return True

        if sub_state == "TURNED_IN":
            print("    Onceki teslim geri aliniyor...")
            cr.courses().courseWork().studentSubmissions().reclaim(
                courseId=course_id,
                courseWorkId=task_id,
                id=submission_id,
                body={}
            ).execute()
            print("    Teslim geri alindi.")

        print(f"    Drive'a yukleniyor: {pdf_yolu}")

        # 1. PDF'i Drive'a yukle
        file_metadata = {
            "name": os.path.basename(pdf_yolu),
            "mimeType": "application/pdf"
        }
        media = MediaFileUpload(pdf_yolu, mimetype="application/pdf", resumable=True)
        uploaded = drive.files().create(
            body=file_metadata,
            media_body=media,
            fields="id,name"
        ).execute()
        drive_file_id = uploaded.get("id")
        print(f"    Drive'a yuklendi. ID: {drive_file_id}")

        # 2. API ile dosyayi ekle ve teslim et (eğer mümkünse)
        try:
            # Dosyayi submission'a ekle
            cr.courses().courseWork().studentSubmissions().modifyAttachments(
                courseId=course_id,
                courseWorkId=task_id,
                id=submission_id,
                body={"addAttachments": [{"driveFile": {"id": drive_file_id}}]}
            ).execute()
            # Teslim et
            cr.courses().courseWork().studentSubmissions().turnIn(
                courseId=course_id,
                courseWorkId=task_id,
                id=submission_id,
                body={}
            ).execute()
            print("    BASARILI: PDF API ile eklenip teslim edildi.")
            return True
        except Exception as e_api:
            print(f"    API eklenemedi, Chrome UI deneniyor: {e_api}")
    except HttpError as e:
        print(f"    API teslim hatasi ({e.resp.status}): {e}")
        return False
    except Exception as e:
        print(f"    Teslim hatasi: {e}")
        return False


# ───────────────────────────────────────────────
# METIN OKUMA
# ───────────────────────────────────────────────

def xpath_yazisi(metin: str) -> str:
    if "'" not in metin:
        return f"'{metin}'"
    if '"' not in metin:
        return f'"{metin}"'
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in metin.split("'")) + ")"


def tikla_yazi(driver, yazilar: list, timeout: int = 20) -> bool:
    bitis = time.time() + timeout
    while time.time() < bitis:
        for yazi in yazilar:
            xp = (
                f"//*[contains(normalize-space(.), {xpath_yazisi(yazi)})]"
                "/ancestor-or-self::*[@role='button' or @role='menuitem' or @role='option' or @role='tab' or @role='listitem' or self::button or self::a][1]"
            )
            try:
                el = driver.find_element(By.XPATH, xp)
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", el)
                return True
            except Exception:
                pass
        time.sleep(0.5)
    return False


def sayfayi_odakla(driver):
    try:
        driver.execute_script("window.focus();")
        body = driver.find_element(By.TAG_NAME, "body")
        body.send_keys(Keys.ESCAPE)
        driver.execute_script("document.body.click();")
    except Exception:
        pass


def sayfada_metin_var(driver, metinler: list) -> bool:
    try:
        body = driver.find_element(By.TAG_NAME, "body").text.lower()
        return any(metin.lower() in body for metin in metinler)
    except Exception:
        return False


def teslim_kaydi_okundu(task_id: str) -> bool:
    if not os.path.exists(TESLIM_KAYIT_DOSYASI):
        return False
    with open(TESLIM_KAYIT_DOSYASI, "r", encoding="utf-8") as f:
        return task_id in {satir.strip() for satir in f if satir.strip()}


def teslim_kaydi_yaz(task_id: str):
    with open(TESLIM_KAYIT_DOSYASI, "a", encoding="utf-8") as f:
        f.write(task_id + "\n")


def google_giris_yap(driver) -> bool:
    try:
        sayfayi_odakla(driver)
        if tikla_yazi(driver, [GOOGLE_EMAIL, "Continue as", "Devam et", "Devam"], timeout=5):
            print("    Google hesap secimi/continue gecildi.")
            time.sleep(3)
            if "accounts.google.com" not in driver.current_url:
                print("    Google girisi tamamlandi.")
                return True

        wait = WebDriverWait(driver, 30)
        email_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email']")))
        sayfayi_odakla(driver)
        email_input.clear()
        email_input.send_keys(GOOGLE_EMAIL)
        driver.find_element(By.ID, "identifierNext").click()
        print("    Google e-posta girildi.")

        time.sleep(3)
        tikla_yazi(driver, [GOOGLE_EMAIL, "Continue as", "Devam et", "Devam"], timeout=5)

        password_input = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='password']"))
        )
        sayfayi_odakla(driver)
        time.sleep(1)
        password_input.clear()
        password_input.send_keys(GOOGLE_PASSWORD)
        driver.find_element(By.ID, "passwordNext").click()
        print("    Google sifre girildi.")

        time.sleep(3)
        tikla_yazi(driver, ["Continue as", "Devam et", "Devam"], timeout=10)

        WebDriverWait(driver, 120).until(lambda d: "accounts.google.com" not in d.current_url)
        print("    Google girisi tamamlandi.")
        return True
    except Exception as e:
        print(f"    Otomatik Google girisi tamamlanamadi: {e}")
        print("    Google ek dogrulama istiyorsa ekrandan bir kere manuel onayla.")
        return False


def chrome_driver_al():
    global chrome_driver
    try:
        if chrome_driver:
            _ = chrome_driver.current_url
            return chrome_driver
    except Exception:
        chrome_driver = None

    opts = uc.ChromeOptions()
    opts.add_argument("--start-maximized")
    opts.add_argument(f"--user-data-dir={os.path.abspath('chrome_profile')}")
    chrome_driver = uc.Chrome(driver_executable_path=CHROMEDRIVER_YOLU, options=opts)
    return chrome_driver


def linkten_ders_id(link: str) -> str:
    """Classroom alternateLink icindeki base64 ders ID'sini cozer."""
    m = re.search(r"/c/([^/]+)/", link or "")
    if not m:
        return ""
    raw = m.group(1)
    pad = "=" * ((4 - len(raw) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(raw + pad).decode("utf-8")
    except Exception:
        return ""


def link_aktif_mi(link: str, aktif_dersler: set) -> bool:
    cid = linkten_ders_id(link)
    return bool(cid and cid in aktif_dersler)


def tarayici_ile_teslim_et(task: dict, pdf_yolu: str, drive, sub_state: str = "",
                           aktif_dersler: set | None = None) -> bool:
    try:
        link = task.get("alternateLink")
        if not link:
            print("    Classroom linki yok, tarayici ile teslim edilemiyor.")
            return False
        if aktif_dersler is not None and not link_aktif_mi(link, aktif_dersler):
            print("    Sinif linki gecersiz (silinmis/eski ders). Chrome acilmadi.")
            print("    classroom.google.com uzerinden guncel derslere girin.")
            return False

        pdf_abs = os.path.abspath(pdf_yolu)
        pdf_adi = os.path.basename(pdf_yolu)
        arama_terimi = pdf_adi.replace('.pdf', '')
        print(f"    API reddetti, Chrome ile teslim deneniyor (Drive arama yontemi)...")

        # PDF'i Drive'a yukle ki Recent/Search'te gorunsun
        print(f"    PDF Drive'a yukleniyor: {pdf_adi}")
        try:
            file_meta = {"name": pdf_adi, "mimeType": "application/pdf"}
            media = MediaFileUpload(pdf_abs, mimetype="application/pdf", resumable=True)
            uploaded = drive.files().create(
                body=file_meta, media_body=media, fields="id,name"
            ).execute()
            print(f"    Drive'a yuklendi. ID: {uploaded.get('id')}")
        except Exception as e:
            print(f"    Drive yukleme hatasi (belki zaten yuklu): {e}")

        time.sleep(2)

        driver = chrome_driver_al()
        wait = WebDriverWait(driver, 60)
        driver.get(link)

        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(4)
        sayfayi_odakla(driver)

        if "accounts.google.com" in driver.current_url:
            print("    Chrome Google girisi istiyor, otomatik giris deneniyor...")
            if not google_giris_yap(driver):
                WebDriverWait(driver, 180).until(lambda d: "accounts.google.com" not in d.current_url)
            time.sleep(4)
            sayfayi_odakla(driver)

        if sub_state == "TURNED_IN":
            tikla_yazi(driver, ["Unsubmit", "Teslimi geri al", "Teslimden vazgec"], timeout=8)
            tikla_yazi(driver, ["Unsubmit", "Teslimi geri al", "Tamam"], timeout=8)
            time.sleep(3)

        if not tikla_yazi(driver, ["Add or create", "Ekle veya oluştur", "Ekle veya olustur"], timeout=25):
            if sayfada_metin_var(driver, ["graded"]):
                print("    Odev zaten graded veya teslim suresi gecmis; kapatiliyor ve devam ediliyor.")
                return True
            driver.save_screenshot(f"hata_addbutton_{task.get('id', 'unknown')}.png")
            print("    Add/Ekle butonu bulunamadi, ekran goruntusu kaydedildi.")
            return False

        time.sleep(1)
        if not tikla_yazi(driver, ["File", "Dosya"], timeout=15):
            sayfayi_odakla(driver)
            tikla_yazi(driver, ["Add or create", "Ekle veya oluştur", "Ekle veya olustur"], timeout=5)
            time.sleep(1)
            tiklandi = tikla_yazi(driver, ["File", "Dosya"], timeout=10)
            if tiklandi:
                time.sleep(1)
            else:
                driver.save_screenshot(f"hata_filebutton_{task.get('id', 'unknown')}.png")
                print("    File/Dosya secenegi bulunamadi, ekran goruntusu kaydedildi.")
                return False
        time.sleep(3)

        # Recent tab'ina tikla (dialog Upload tab'inda acilabiliyor)
        recent_tiklandi = False
        try:
            recent_xpaths = [
                "//*[text()='Recent']",
                "//*[text()='Son kullanılanlar']",
                "//*[text()='Son']",
                "//*[contains(@aria-label, 'Recent')]",
            ]
            for rxp in recent_xpaths:
                try:
                    el = driver.find_element(By.XPATH, rxp)
                    if el.is_displayed():
                        driver.execute_script("arguments[0].click();", el)
                        recent_tiklandi = True
                        print("    Recent tab'ina tiklandi.")
                        time.sleep(3)
                        break
                except Exception:
                    pass
            if not recent_tiklandi:
                print("    Recent tab bulunamadi, mevcut tab'da devam ediliyor...")
        except Exception:
            pass

        # Drive dialog acildi - Arama kutusuna PDF adini yaz
        arama_basarili = False
        try:
            search_selectors = [
                "input[aria-label*='earch']",
                "input[placeholder*='earch']",
                "input[placeholder*='Drive']",
                "input[type='text']",
                "input[type='search']",
            ]
            search_input = None
            for sel in search_selectors:
                try:
                    els = driver.find_elements(By.CSS_SELECTOR, sel)
                    for el in els:
                        if el.is_displayed():
                            search_input = el
                            break
                    if search_input:
                        break
                except Exception:
                    continue

            if search_input:
                search_input.click()
                time.sleep(0.5)
                search_input.clear()
                search_input.send_keys(arama_terimi)
                search_input.send_keys(Keys.RETURN)
                print(f"    Drive'da araniyor: {arama_terimi}")
                time.sleep(5)
                arama_basarili = True
            else:
                print("    Arama kutusu bulunamadi, Recent listesinden aranacak...")
        except Exception as e:
            print(f"    Arama kutusu hatasi: {e}")

        # Dosyayi bul ve sec
        dosya_secildi = False
        bitis = time.time() + 15
        while time.time() < bitis and not dosya_secildi:
            for xp in [
                f"//*[contains(text(), '{arama_terimi}')]",
                f"//*[contains(@aria-label, '{arama_terimi}')]",
                f"//*[contains(@data-tooltip, '{arama_terimi}')]",
            ]:
                try:
                    els = driver.find_elements(By.XPATH, xp)
                    for el in els:
                        if el.is_displayed():
                            driver.execute_script(
                                "arguments[0].scrollIntoView({block:'center'});", el)
                            time.sleep(0.5)
                            el.click()
                            dosya_secildi = True
                            print(f"    Dosya bulundu ve secildi: {pdf_adi}")
                            break
                except Exception:
                    pass
                if dosya_secildi:
                    break
            time.sleep(1)

        if not dosya_secildi:
            driver.save_screenshot(f"hata_recent_{task.get('id', 'unknown')}.png")
            print("    PDF dosyasi Drive dialogunda bulunamadi.")
            return False

        time.sleep(2)

        # Insert / Add / Ekle tikla
        tikla_yazi(driver, ["Insert", "Add", "Ekle", "Sec"], timeout=15)
        time.sleep(5)

        if not tikla_yazi(driver, ["Turn in", "Hand in", "Teslim et"], timeout=15):
            if sayfada_metin_var(driver, ["work cannot be turned in after the due date"]):
                print("    PDF eklendi ama son tarih gectigi icin teslim butonu kapali; devam ediliyor.")
                return True
            if sayfada_metin_var(driver, ["graded", "work cannot be turned in after the due date"]):
                print("    Odev zaten graded veya teslim suresi gecmis; kapatiliyor ve devam ediliyor.")
                return True
            driver.save_screenshot(f"hata_turnin_{task.get('id', 'unknown')}.png")
            print("    Teslim et butonu bulunamadi, ekran goruntusu kaydedildi.")
            return False

        tikla_yazi(driver, ["Turn in", "Hand in", "Teslim et", "Tamam"], timeout=20)
        time.sleep(5)
        print(f"    BASARILI: {task.get('title', 'Odev')} Chrome ile teslim edildi.")
        return True

    except Exception as e:
        print(f"    Chrome teslim hatasi: {e}")
        return False


def submission_dosyalarinda_var_mi(submission: dict, dosya_adi: str) -> bool:
    for ek in submission.get("assignmentSubmission", {}).get("attachments", []):
        drive_file = ek.get("driveFile", {})
        ad = drive_file.get("title") or drive_file.get("name") or ""
        if ad == dosya_adi:
            return True
    return False


def linkteki_metni_oku(url: str) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        metin = soup.get_text(separator="\n")
        metin = "\n".join(line.strip() for line in metin.splitlines() if line.strip())
        return metin[:3000]
    except Exception as e:
        print(f"    Link okuma hatasi ({url}): {e}")
        return ""


def metindeki_linkleri_bul(metin: str) -> list:
    return re.findall(r'https?://[^\s\)\]\>\"\']+', metin)


def dosyayi_oku(fh, mime_type: str) -> str:
    try:
        if "pdf" in mime_type:
            reader = PdfReader(fh)
            return "".join([p.extract_text() or "" for p in reader.pages])
        elif "document" in mime_type or "word" in mime_type:
            doc = docx.Document(fh)
            return "\n".join([p.text for p in doc.paragraphs])
        return ""
    except Exception as e:
        print(f"    Dosya okuma hatasi: {e}")
        return ""


def drive_dosyasi_indir(drive, f_id: str):
    try:
        meta = drive.files().get(fileId=f_id, fields="mimeType,name").execute()
        mime = meta.get("mimeType", "")
        fh = io.BytesIO()
        if "google-apps" in mime:
            req = drive.files().export_media(fileId=f_id, mimeType="application/pdf")
            mime = "application/pdf"
        else:
            req = drive.files().get_media(fileId=f_id)
        dl = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = dl.next_chunk()
        fh.seek(0)
        return fh, mime
    except HttpError as e:
        print(f"    Drive hatasi ({e.resp.status}): {e}")
        return None, None
    except Exception as e:
        print(f"    Drive beklenmeyen hata: {e}")
        return None, None


def odev_metnini_topla(task: dict, drive) -> str:
    parcalar = []
    for mat in task.get("materials", []):
        if "driveFile" in mat:
            f_id = mat["driveFile"]["driveFile"]["id"]
            fh, mime = drive_dosyasi_indir(drive, f_id)
            if fh:
                okunan = dosyayi_oku(fh, mime)
                if len(okunan) >= 50:
                    print("    Drive dosyasi okundu.")
                    parcalar.append(okunan)
        elif "link" in mat:
            url = mat["link"].get("url", "")
            if url:
                print(f"    Link okunuyor: {url}")
                lt = linkteki_metni_oku(url)
                if lt:
                    parcalar.append(f"[Link: {url}]\n{lt}")
        elif "youtubeVideo" in mat:
            v = mat["youtubeVideo"]
            parcalar.append(f"[YouTube] {v.get('title','')}\n{v.get('alternateLink','')}")

    desc = task.get("description", "")
    if desc:
        for url in metindeki_linkleri_bul(desc):
            lt = linkteki_metni_oku(url)
            if lt:
                parcalar.append(f"[Link: {url}]\n{lt}")
        if len(desc) >= 30:
            parcalar.append(f"[Aciklama]\n{desc}")

    return "\n\n".join(parcalar)


# ───────────────────────────────────────────────
# CLAUDE
# ───────────────────────────────────────────────

def basit_b1_cevap_olustur(baslik: str, metin: str = "") -> str:
    konu = baslik.strip() or "The assignment"
    if "shopping" in konu.lower() and "business" in konu.lower():
        return (
            "1. My New Shopping Business\n\n"
            "My business idea is a small online shopping store. The name of my store is Easy Market. "
            "It sells clothes, school items, phone accessories, and small gifts. I want the prices to be fair, "
            "so students and families can buy things easily. The website will be simple to use on a phone.\n\n"
            "2. Customers and Products\n\n"
            "My main customers are young people, students, and busy parents. They need useful products with good prices. "
            "The store will show clear photos and short information about every product. Customers can also read reviews before buying. "
            "This helps them feel safe and comfortable.\n\n"
            "3. Marketing and Delivery\n\n"
            "I will advertise the business on Instagram, TikTok, and Facebook. I will also make small discounts for first-time customers. "
            "Delivery should be fast and not expensive. If a customer has a problem, the store will answer quickly. "
            "Good service is important because happy customers come back again."
        )

    return (
        f"1. {konu}\n\n"
        f"{konu} is an important topic for students. In my opinion, we should think about it carefully and use simple, clear ideas. "
        "People can learn more when they read, discuss, and share examples. This also helps students improve their English. "
        "I think the best answer should be practical and easy to understand.\n\n"
        "2. My Opinion\n\n"
        "I believe this topic is connected to daily life. Students can use their own experience and explain their ideas with examples. "
        "It is important to be organized and respectful when we write an answer. A good answer has an introduction, details, and a conclusion. "
        "For this reason, I try to explain my opinion in a simple way."
    )


def claude_ile_coz(metin: str, baslik: str = ""):
    filtre = claude_client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        messages=[{"role": "user", "content": (
            "You are a homework classifier.\n"
            f"Homework title: {baslik}\n\n"
            "Rules:\n"
            "- If ONLY textbook exercises (fill-in-the-blank, matching, circle answer, "
            "page tasks, copy sentences) reply exactly: KITAP\n"
            "- Otherwise extract open-ended/essay/analytical questions as a numbered "
            "list IN ENGLISH. Nothing else.\n\n"
            f"Content:\n---\n{metin[:4000]}\n---"
        )}],
    )
    sonuc = filtre.content[0].text.strip()
    if sonuc.upper() == "KITAP":
        print("    Kitap odevi, atlaniyor.")
        return None

    print("    Sorular bulundu, cevaplanıyor...")
    cevap = claude_client.messages.create(
        model="claude-sonnet-4-5",
            max_tokens=1200,
        messages=[{"role": "user", "content": (
            "You are a student. Answer in English ONLY.\n"
            "Use clear B1-level English. Use simple grammar and common vocabulary.\n"
            "Numbered heading per question. 4-6 sentences minimum per answer.\n\n"
            f"Questions:\n{sonuc}\n\nContext:\n{metin[:2000]}"
        )}],
    )
    return cevap.content[0].text


# ───────────────────────────────────────────────
# PDF
# ───────────────────────────────────────────────

def pdf_olustur(metin: str, isim: str):
    doc = SimpleDocTemplate(isim, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("B", parent=styles["Normal"],
        fontSize=11, leading=16, alignment=TA_LEFT, spaceAfter=8)
    head = ParagraphStyle("H", parent=styles["Heading2"],
        fontSize=13, leading=18, spaceAfter=6, spaceBefore=12)
    story = []
    for block in metin.split("\n\n"):
        block = block.strip()
        if not block:
            story.append(Spacer(1, 6))
            continue
        for line in block.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("## "):
                story.append(Paragraph(line[3:], head))
            elif line.startswith("# "):
                story.append(Paragraph(line[2:], head))
            elif line.startswith("**") and line.endswith("**"):
                story.append(Paragraph(f"<b>{line[2:-2]}</b>", body))
            else:
                safe = line.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
                story.append(Paragraph(safe, body))
        story.append(Spacer(1, 4))
    doc.build(story)


def aktif_dersleri_al(cr) -> list:
    """Manuel DERSLER doluysa onu kullanir; degilse API'den aktif dersleri ceker."""
    if DERSLER:
        return DERSLER

    dersler = []
    page_token = None
    while True:
        resp = cr.courses().list(
            courseStates=["ACTIVE"],
            pageSize=100,
            pageToken=page_token,
        ).execute()
        for course in resp.get("courses", []):
            ad = course.get("name", "")
            if any(k in ad.lower() for k in ATLANACAK_DERS_KELIMELERI):
                print(f"\n>> {ad} dersi atlandi.")
                continue
            dersler.append(course["id"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    if not dersler:
        print("  Uyari: Aktif ders bulunamadi. token.json dogru hesapla mi?")
    else:
        print(f"  API'den {len(dersler)} aktif ders bulundu.")
    return dersler


def benzersiz_pdf_adi(baslik: str, task_id: str) -> str:
    """Odev basligi ve task ID'den benzersiz PDF ismi olusturur."""
    slug = re.sub(r'[^a-zA-Z0-9]+', '_', baslik).strip('_')[:30]
    kisa_id = task_id[-6:]
    return f"HW_{slug}_{kisa_id}.pdf"


# ───────────────────────────────────────────────
# ANA PROGRAM
# ───────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  OTONOM ODEV SISTEMI BASLIYOR")
    print("=" * 50)

    if not ANTHROPIC_API_KEY:
        print("Hata: .env dosyasinda ANTHROPIC_API_KEY eksik.")
        return
    if not GOOGLE_EMAIL or not GOOGLE_PASSWORD:
        print("Hata: .env dosyasinda GOOGLE_EMAIL ve GOOGLE_PASSWORD eksik.")
        return

    # Token yenile
    creds = Credentials.from_authorized_user_file("token.json")
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open("token.json", "w") as f:
            f.write(creds.to_json())

    cr    = build("classroom", "v1", credentials=creds)
    drive = build("drive",     "v3", credentials=creds)

    sinir    = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=SON_KAC_GUN)
    toplam   = 0
    basarili = 0
    aktif_dersler = aktif_dersleri_al(cr)
    aktif_set = set(aktif_dersler)

    for d_id in aktif_dersler:
        try:
            ders_adi = cr.courses().get(id=d_id).execute().get("name", "")
            print(f"\n===== DERS: {ders_adi} =====")

            tasks = cr.courses().courseWork().list(courseId=d_id).execute().get("courseWork", [])
            for task in tasks:
                ts = task.get("updateTime", "2000-01-01T00:00:00.000Z")
                try:
                    tarih = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ")\
                                    .replace(tzinfo=datetime.timezone.utc)
                except ValueError:
                    tarih = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))

                baslik = task.get("title", "Isimsiz")
                print(f"\n>> {baslik}")

                # Submission bul
                sub_resp = cr.courses().courseWork().studentSubmissions()\
                        .list(courseId=d_id, courseWorkId=task["id"]).execute()
                subs = sub_resp.get("studentSubmissions", [])
                if not subs:
                    print("    Submission yok, atlaniyor.")
                    continue

                submission = subs[0]
                submission_id = submission["id"]
                sub_state = submission.get("state", "")
                print(f"    Submission durumu: {sub_state}")
                missing_veya_teslimsiz = sub_state not in ("TURNED_IN", "RETURNED")
                if submission.get("late"):
                    print("    Durum: MISSING/LATE gorunuyor, islenecek.")

                if tarih < sinir and not (MISSING_ODEVLERI_DAHIL_ET and missing_veya_teslimsiz):
                    continue

                toplam += 1

                pdf_adi = benzersiz_pdf_adi(baslik, task['id'])

                if teslim_kaydi_okundu(task["id"]):
                    print("    Bu odev daha once otomatik teslim edilmis, atlaniyor.")
                    toplam -= 1
                    continue

                if sub_state in ("TURNED_IN", "RETURNED"):
                    print("    Odev zaten teslim edilmis veya hocadan donmus, sayfa acilmadan atlaniyor.")
                    teslim_kaydi_yaz(task["id"])
                    toplam -= 1
                    continue

                if os.path.exists(pdf_adi):
                    print(f"    Mevcut PDF bulundu, yeniden cevap uretilmeyecek: {pdf_adi}")
                else:
                    metin = odev_metnini_topla(task, drive)
                    if not metin or len(metin) < 30:
                        print("    Yeterli icerik yok, atlaniyor.")
                        continue

                    try:
                        cevap = claude_ile_coz(metin, baslik)
                    except Exception as e:
                        print(f"    Claude yavas/hata verdi, basit B1 cevap kullaniliyor: {e}")
                        cevap = basit_b1_cevap_olustur(baslik, metin)
                    if not cevap:
                        continue

                    pdf_olustur(cevap, pdf_adi)
                    print(f"    PDF olusturuldu: {pdf_adi}")

                teslim_edildi = api_ile_teslim_et(
                    cr, drive, d_id, task["id"], submission_id, pdf_adi, baslik, sub_state
                )
                if not teslim_edildi:
                    teslim_edildi = tarayici_ile_teslim_et(
                        task, pdf_adi, drive, sub_state, aktif_set
                    )

                if teslim_edildi:
                    teslim_kaydi_yaz(task["id"])
                    basarili += 1
                else:
                    print("    Otomatik teslim basarisiz oldu.")
                    print(f"    PDF yolu: {os.path.abspath(pdf_adi)}")
                    print(f"    Classroom linki: {task.get('alternateLink', 'Link yok')}")

        except HttpError as e:
            if e.resp.status == 404:
                print(f"  Ders bulunamadi (ID:{d_id}). DERSLER listesini bos birakip otomatik modu kullanin.")
            else:
                print(f"  Ders hatasi (ID:{d_id}): {e}")
        except Exception as e:
            print(f"  Ders hatasi (ID:{d_id}): {e}")

    print("\n" + "=" * 50)
    print(f"  TAMAMLANDI: {basarili}/{toplam} odev islendi.")
    print("=" * 50)

    if chrome_driver and not CHROME_ACIK_KALSIN:
        chrome_driver.quit()


if __name__ == "__main__":
    main()
