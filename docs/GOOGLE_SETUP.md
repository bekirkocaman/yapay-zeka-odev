# Google Cloud & Classroom Kurulumu

Bu rehber `credentials.json` ve `token.json` dosyalarını oluşturmanız için gereken adımları özetler.

## 1. Google Cloud projesi

1. [Google Cloud Console](https://console.cloud.google.com/) → yeni proje oluşturun.
2. **APIs & Services** → **Library** bölümünden şunları etkinleştirin:
   - Google Classroom API
   - Google Drive API

## 2. OAuth consent screen

1. **APIs & Services** → **OAuth consent screen**
2. User Type: **External** (test kullanıcısı olarak kendi Gmail/okul hesabınızı ekleyin)
3. Gerekli alanları doldurup kaydedin.

## 3. OAuth Client ID

1. **Credentials** → **Create Credentials** → **OAuth client ID**
2. Application type: **Desktop app**
3. İndirilen JSON dosyasını proje köküne `credentials.json` adıyla kaydedin.

## 4. İlk yetkilendirme

```powershell
python auth.py
```

Tarayıcı açılır; Classroom ve Drive izinlerini onaylayın. `token.json` oluşur.

## 5. Gerekli OAuth kapsamları

`auth.py` şu scope’ları kullanır:

- `classroom.coursework.me`
- `classroom.courses.readonly`
- `classroom.student-submissions.me.readonly`
- `drive`

Token süresi dolduğunda `main.py` otomatik yenilemeyi dener; sorun olursa `auth.py`’yi tekrar çalıştırın.

## Sorun giderme

| Hata | Çözüm |
|------|--------|
| `access_denied` | OAuth ekranında test kullanıcısı olarak hesabınızı ekleyin |
| `invalid_grant` | `token.json` silin, `auth.py` tekrar çalıştırın |
| `403` API | Classroom/Drive API’lerinin etkin olduğunu doğrulayın |
