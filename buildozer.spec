[app]

title = NovaGet
package.name = novaget
package.domain = org.novaget
source.dir = .
# ttf,otf = خطوط Cairo لازم تتضمن في الـ APK | png = اللوجو (assets/logo.png) وصورة شاشة البداية
source.include_exts = py,png,jpg,jpeg,kv,atlas,ttf,otf,json,txt
source.exclude_dirs = .github,.buildozer,bin,libs
version = 4.1

# اللوجو (أيقونة التطبيق) وشاشة البداية الأولى (قبل ما بايثون يشتغل)
icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/presplash.png
android.presplash_color = #020C2C

# أيقونة adaptive (اللوجو بيبان صح في كل أشكال الأيقونات) + ثيم بخلفية كحلي لشاشة البداية
icon.adaptive_foreground.filename = %(source.dir)s/icon_fg.png
icon.adaptive_background.filename = %(source.dir)s/icon_bg.png
android.apptheme = @style/NovaTheme
android.add_resources = nova_theme.xml:values/nova_theme.xml,nova_theme_v31.xml:values-v31/nova_theme.xml

# السماح بـ HTTP على 127.0.0.1 (المشغّل الداخلي بيستخدم سيرفر محلي)
android.extra_manifest_application_arguments = extra_app_args.txt

requirements = python3==3.11.5,hostpython3==3.11.5,kivy==2.3.0,pillow,yt-dlp,certifi,arabic-reshaper,python-bidi==0.4.2,fonttools,pyjnius,android

orientation = portrait
fullscreen = 0

# كل الملفات (للحفظ في مجلد NovaGet) + إشعارات التحميل + منع النوم أثناء التحميل
android.permissions = INTERNET,ACCESS_NETWORK_STATE,POST_NOTIFICATIONS,WAKE_LOCK,MANAGE_EXTERNAL_STORAGE,(name=android.permission.READ_EXTERNAL_STORAGE;maxSdkVersion=32),(name=android.permission.WRITE_EXTERNAL_STORAGE;maxSdkVersion=29)

android.api = 35
android.minapi = 26
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True
android.allow_backup = False

# ffmpeg (اختياري): ملف ffmpeg لأندرويد arm64 بيتحزّم كـ libffmpeg.so جوه التطبيق
# الـ workflow بيجهّزه تلقائيًا من ffmpeg_bin أو ffmpeg أو libffmpeg.so لو موجودين في الريبو
android.add_libs_arm64_v8a = libs/arm64-v8a/*.so

[buildozer]
log_level = 2
warn_on_root = 1
