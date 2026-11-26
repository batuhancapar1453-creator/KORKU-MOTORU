# KORKU MOTORU – Ücretsiz başlangıç paketi

## Önemli kapasite notu
Bu ücretsiz yapı, Cloudflare Workers AI ile sahne görselleri üretir; her görseli FFmpeg ile 4 saniyelik sinematik 2D kamera hareketine dönüştürür. Bu, tam anlamıyla bir text-to-video/image-to-video modelinin her sahne için yeni hareket üretmesi değildir. Ücretsiz ve sürdürülebilir bir AI video API'si garanti edilemediği için ücretli API gerektiren sahte bir entegrasyon eklenmemiştir. Gerçek AI video hareketi için ayrıca ücretsiz kota veren bir sağlayıcı bulunması ve API erişiminin doğrulanması gerekir.

## İlk video
- Başlık: Neden Hâlâ Uyanıksın?
- Ana karakter: Batu
- Süre: 180 saniye
- Sahne: 45 × 4 saniye
- Çıktı: 1080 × 1920 MP4
- Anlatıcı: tr-TR-AhmetNeural
- Müzik: Her hikâye kimliğinden farklı deterministik sentezlenen özgün ambient müzik
- Ses efektleri: Telefona özel sentetik bildirim efektleri

## GitHub kurulumu
1. Bu ZIP'i yeni, boş GitHub deposuna çıkarıp tüm dosyaları yükleyin.
2. `.github/workflows/korku_motoru.yml` dosyası Actions iş akışıdır.
3. Repository Settings → Secrets and variables → Actions bölümüne şu secrets değerlerini ekleyin:
   - `CLOUDFLARE_ACCOUNT_ID`
   - `CLOUDFLARE_API_TOKEN` (Workers AI çalıştırma izni)
   - `YOUTUBE_CLIENT_ID`
   - `YOUTUBE_CLIENT_SECRET`
   - `YOUTUBE_REFRESH_TOKEN`
4. Actions sekmesinden `KORKU MOTORU - Günlük 3 Dakikalık Shorts` iş akışını çalıştırın.
5. İlk başarılı çalışmadan sonra `output/` altındaki MP4 dosyasını Actions artifact bölümünden alın.

## Ücretsiz kullanım ve sınırlar
- Edge TTS ücretsiz bir çevrimiçi TTS servisi olarak çağrılır; hizmet şartları ve erişilebilirlik değişebilir.
- Cloudflare Workers AI kullanım kotası hesaba/planlara göre sınırlıdır. 45 görsel üretimi günlük ücretsiz kotaya sığmayabilir. Kota aşılırsa iş akışı hata verir; ücretli kullanıma kendiliğinden geçmez.
- GitHub Actions süresi, servis yanıtları ve ücretsiz kotalara bağlıdır. 30 dakikalık tamamlanma garantisi yoktur.
- YouTube API ve Cloudflare erişimi için hesap/anahtar gerekir. Bu paket gizli anahtar içermez.
- İlk paket yalnızca Video 001 senaryosunu içerir. Sonraki 9 konu için ayrı hikâye JSON dosyaları eklenmelidir.

## Yerel kontrol
`pip install -r requirements.txt` ardından `python run.py --self-test --no-upload` komutu ile sahte test görselleri kullanılarak montaj ve ses hattı sınanabilir. Self-test YouTube'a yükleme yapmaz.
