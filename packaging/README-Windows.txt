================================================================
  VideoWorkflow Studio — Windows uchun desktop ilova
================================================================

BU NIMA?
--------
VideoWorkflow Studio — brauzerdagi ish jarayonini bir marta yozib
olib, keyin uni avtomatik qayta bajaradigan desktop dastur.
Yozilgan shablon (login, menyu bosishlari, forma to'ldirish, fayl
yuklash, sahifa o'tishlari) ilovaning o'zida saqlanadi va
«Ishga tushirish» tugmasi bilan aynan siz qilgan tartibda bajariladi.

Dastur alohida native oynada (WebView2) ochiladi — brauzer kerak
emas. Docker, Python yoki Node.js o'rnatish shart emas: Python
runtime, Chromium va barcha kutubxonalar paket ichida.

Talablar:
  • Windows 10 yoki Windows 11 (64-bit)
  • Kamida 4 GB bo'sh disk
  • Microsoft Edge WebView2 Runtime (Windows 11'da o'rnatilgan;
    Windows 10'da ko'p hollarda Edge bilan birga keladi)
  • Administrator huquqi SHART EMAS
  • Ishlash uchun internet — faqat siz avtomatlashtirayotgan sayt
    uchun kerak. Dasturning o'zi internetsiz ham ochiladi.

O'RNATISH
---------
1-usul (eng qulay):
  VideoWorkflow-Setup.exe faylini ikki marta bosing. U hamma narsani
  %LOCALAPPDATA%\VideoWorkflow ga o'rnatadi, ish stolida va Start
  menyuda yorliq yaratadi va dastur oynasini ochadi.

2-usul (ZIP):
  1) ZIP arxivni istalgan joyga oching (masalan, Desktop).
  2) Ichidagi "Install.cmd" ustiga ikki marta bosing.
  3) Dastur %LOCALAPPDATA%\VideoWorkflow ga o'rnatiladi va ochiladi.

O'rnatmasdan sinab ko'rish:
  ZIP ichidagi "VideoWorkflow.exe" ni ishga tushiring — barcha
  ma'lumotlar paket papkasida qoladi.

Birinchi ishga tushish 30–60 soniya olishi mumkin. Keyingi safar
tezroq ochiladi. Dasturni yopish uchun oynani yoping — backend ham
avtomatik to'xtaydi. Majburan to'xtatish: "VideoWorkflow.exe --stop".

BIRINCHI QADAMLAR
-----------------
1) Ilova ochilgach «Ro'yxatdan o'tish» orqali lokal hisob yarating.
   Bu hisob faqat shu kompyuterda saqlanadi.
2) «Yozib olish» bo'limini oching.
3) Boshlang'ich sayt URL'ini va brauzerni tanlang, «Boshlash»ni
   bosing — alohida brauzer oynasi ochiladi.
4) Saytda odatdagidek ishlang: bosing, yozing, tanlang, fayl
   yuklang, sahifalar bo'ylab o'ting.
5) «To'xtatib saqlash»ni bosing. Yozuv shablonga aylanadi.
6) «Ishga tushirish» — shablon aynan siz qilgan tartibda bajariladi.

DASTUR OYNASI
-------------
  Ilova:       native oyna (WebView2), brauzer ochilmaydi
  Backend API: http://127.0.0.1:8000
  API Docs:    http://127.0.0.1:8000/docs

Dastur faqat 127.0.0.1 manzilida tinglaydi: boshqa kompyuterdan
ulanib bo'lmaydi.

SHABLON QANDAY QAYTA O'YNALADI
------------------------------
• Har bir qadam (navigate, click, type, select, press, upload va
  boshqalar) yozilgandagi tartibda bajariladi.
• Login formasida parol kiritilib Enter bosilganda dastur OneID
  zanjiri (id.egov.uz → sayt → dashboard) tugashini kutadi va
  keyingi qadamga shundan keyingina o'tadi. Shu tufayli sessiya
  yo'qolmaydi va keyingi bosqichlar bo'sh login sahifasida
  bajarilmaydi.
• Agar login o'tmasa (parol xato va h.k.), dastur tushunarli xabar
  beradi: «Tizimga kirish yakunlanmadi…» — keyingi qadamlar
  ma'nosiz bajarilmaydi.
• Parol maydonlari shablonda {{PASSWORD}} / {{LOGIN}} ko'rinishida
  qoladi; haqiqiy qiymatlar diskka yozilmaydi. URL'lardagi
  token/sessiya parametrlari avtomatik tozalanadi.
• Ichki iframe elementlari ham qo'llanadi.

BATCH (ko'p kishilik ish)
-------------------------
Paketda 30 qadamli namunali ish jarayoni (job) bor — uni Job
sahifasida ko'rishingiz mumkin. O'z Excel ro'yxatingiz bilan
ishlash uchun:
  1) Job sahifasida «Batch» bo'limini oching.
  2) Excel faylni yuklang.
  3) Xonadonlar soni (`0` = cheksiz), har biriga nechta kishi va
     boshlanish qatorini kiriting.
  4) LOGIN/PASSWORD maydonlarini to'ldiring — OneID sessiyani
     yopsa, dastur shu bilan avtomatik qayta kiradi.
  5) «Saqlash»ni bosing.
Natijalar storage\batch_result_*.md / .csv / .json fayllariga
yoziladi.

AI KALITI (ixtiyoriy)
---------------------
Faqat video tahlili uchun OpenRouter API kaliti kerak. Shablondan
yozib olish va qayta o'ynash uchun shart emas.
  "Sozlamalar.cmd" ni oching yoki qo'lda:
  app\backend\.env  ->  OPENROUTER_API_KEY=...

BUYRUQLAR
---------
  VideoWorkflow.exe                  dastur oynasini ochish
  VideoWorkflow.exe --status         holatni ko'rish
  VideoWorkflow.exe --stop           to'xtatish
  VideoWorkflow.exe --restart        qayta ishga tushirish
  VideoWorkflow.exe --logs           loglarni ko'rish
  VideoWorkflow.exe --path DIR       boshqa paket papkasidan ishga tushirish
  VideoWorkflow.exe --browser        brauzerda ochish (zaxira rejim)
  VideoWorkflow.exe --no-pause       konsolni kutmaslik

MA'LUMOTLAR QAYERDA?
--------------------
O'rnatilgan nusxada: %LOCALAPPDATA%\VideoWorkflow
  data\app.db          — SQLite bazasi (hisoblar, shablonlar)
  data\logs\           — backend loglari
  data\webview2\       — oyna keshi
  storage\             — yuklangan fayllar, skrinshotlar, natijalar

ZAXIRA NUSXA: dasturni "VideoWorkflow.exe --stop" bilan to'xtatib,
%LOCALAPPDATA%\VideoWorkflow\storage va data\app.db ni ko'chirib
qo'ying. Yangilanish o'rnatilganda bu ma'lumotlar saqlanadi.

MUAMMOLAR
---------
• Oyna ochilmasa va brauzer rejimiga o'tsa: WebView2 Runtime'ni
  o'rnating (Microsoft saytidan Evergreen Runtime).
• "Port band" xatosi: avval "VideoWorkflow.exe --stop", keyin
  qayta ishga tushiring.
• Windows SmartScreen ogohlantirsa: "More info" -> "Run anyway".
  Bu imzosiz mahalliy ilova uchun odatiy hol.
• Loglar: "VideoWorkflow.exe --logs" yoki data\logs\.
• Antivirus Chrome/Python fayllarini bloklasa, papkani istisno
  qiling.
• «Tizimga kirish yakunlanmadi» xatosi: LOGIN/PASSWORD ni
  tekshiring. Hisob OneID'da bloklanmagan bo'lsin.
• «Element topilmadi» xatosi: sayt dizayni o'zgargan bo'lishi
  mumkin — shablonni qayta yozib oling.

MAXFIYLIK
---------
• OneID login/paroli faqat ilovaning Batch bo'limidagi maydonlarga
  kiritiladi; .env fayliga va shablonga yozilmaydi.
• LOGIN/PASSWORD backend jarayoni xotirasida (qayta login uchun)
  ushlab turiladi va dastur to'xtaganda o'chib ketadi.
• Batch sessiyasi (storage\batch_session_*.json) va natijalar
  (storage\batch_result_*), shuningdek data\app.db shaxsiy
  ma'lumotlarni o'z ichiga oladi — ularni begonalarga bermang.
• Yangi o'rnatish paketi bunday fayllarni o'z ichiga olmaydi.
