# NovaGet — خطوات تحويله لـ APK

## الملفات اللي لازم تكون في الريبو (الجذر)
- `main.py` `buildozer.spec` `extra_app_args.txt` `nova_theme.xml` `nova_theme_v31.xml`
- `icon.png` `icon_fg.png` `icon_bg.png` `presplash.png`
- `assets/logo.png`
- `.github/workflows/build.yml`
- `Cairo-Regular.ttf` و `Cairo-Bold.ttf` (نفس خطوط Pydroid)
- (اختياري) `ffmpeg_bin` = نفس ملف ffmpeg بتاع Pydroid، أو `libffmpeg.so`

## البناء
GitHub > Actions > Build NovaGet APK > Run workflow. بعد ما يخلص (أول مرة 25-40 دقيقة) نزّل Artifact باسم NovaGet-apk.
أول تشغيل: فعّل "السماح بالوصول لكل الملفات" عشان التحميل يتحفظ في مجلد NovaGet.
