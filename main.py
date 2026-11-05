# -*- coding: utf-8 -*-
# NovaGet - Video / Audio Downloader  (Kivy + yt-dlp)  -- Pydroid 3
#
# Pip:  yt-dlp  arabic-reshaper  python-bidi
# الخطوط: حط Cairo-Regular.ttf و Cairo-Bold.ttf في نفس مجلد الملف ده
#         (أو في مجلد Download في الهاتف)

import itertools
import copy
import json
import math
import os
import re
import threading
import time
import tempfile
from concurrent.futures import ThreadPoolExecutor

APP_VERSION = "4.1"

DEBUG_LOG = True     # تفعيل رسائل التشخيص مؤقتًا لمعرفة سبب مشكلة الرجوع
# عدد محدود من طلبات الصور حتى لا يعلّق الهاتف مع البلاي ليست الكبيرة
_THUMB_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="NovaThumb")
# فحص الفيديوهات المباشرة (حجم/دقة/لقطة) في بول مستقل عشان ما يعطّلش صور البلاي ليست
_PROBE_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="NovaProbe")
# لا نعطّل سجل Kivy هنا؛ نحتاج ظهور الأخطاء لتشخيص مشكلة زر الرجوع.

# ---- تشخيص: لو الواجهة وقفت أكتر من 8 ثواني بيتكتب كل الـ threads في novaget_hang.txt
# ولو حصل أي خطأ مخفي بيتكتب في novaget_log.txt (ابعتهم لو المشكلة اتكررت)
import sys
import traceback
try:
    import faulthandler
except Exception:
    faulthandler = None
_HANG_FH = None
HANG_DUMP = False   # كتابة novaget_hang.txt متوقفة (غيّرها True للتشخيص)


def _apk_private_dir():
    """داخل الـ APK: مجلد البيانات الخاص بالتطبيق (بيفضل موجود بعد التحديث، عكس مجلد الكود)."""
    p = os.environ.get("ANDROID_PRIVATE", "")
    low = p.lower()
    if p and os.path.isdir(p) and "pyramide" not in low and "pydroid" not in low:
        return p
    return ""


def _diag_dir():
    """كل ملفات البرنامج الجانبية (إعدادات/سجل/كاش/أصوات) في مجلد واحد مخفي."""
    base = _apk_private_dir()
    if not base:
        try:
            base = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            base = os.getcwd()
    d = os.path.join(base, ".novaget_data")
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        return base


def nlog(msg):
    """سجل أخطاء بسيط جنب الملف (بيتقصّ لو كبر)."""
    try:
        p = os.path.join(_diag_dir(), "novaget_log.txt")
        if os.path.isfile(p) and os.path.getsize(p) > 300000:
            os.remove(p)
        with open(p, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _hang_arm(*_):
    """بتتنادى كل ثانية من الـ UI thread؛ لو الـ UI وقف مش هتتنادى والـ watchdog هيكتب الـ stack."""
    global _HANG_FH
    if faulthandler is None or not HANG_DUMP:
        return
    now = time.time()
    if now - _hang_arm.__dict__.get("t", 0) < 2.0:
        return
    _hang_arm.t = now
    try:
        if _HANG_FH is None:
            _HANG_FH = open(os.path.join(_diag_dir(), "novaget_hang.txt"), "a")
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(8, repeat=False, file=_HANG_FH)
    except Exception:
        pass


def _excepthook(tp, val, tb):
    nlog("UNCAUGHT: " + "".join(traceback.format_exception(tp, val, tb))[-1500:])


try:
    sys.excepthook = _excepthook
    threading.excepthook = lambda a: nlog("THREAD %s: %s" % (
        getattr(a.thread, "name", "?"), "".join(traceback.format_exception(
            a.exc_type, a.exc_value, a.exc_traceback))[-1500:]))
except Exception:
    pass

# ---- APK: شهادات HTTPS (أندرويد مفيهوش مكان شهادات يفهمه OpenSSL بتاع بايثون)
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except Exception:
    pass

try:
    import yt_dlp
    YTDLP_IMPORT_ERROR = ""
except Exception as _ytdlp_import_exc:  # keep the UI available so the user gets a useful message
    yt_dlp = None
    YTDLP_IMPORT_ERROR = "%s: %s" % (type(_ytdlp_import_exc).__name__, _ytdlp_import_exc)

from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.text import LabelBase
from kivy.core.text import Label as CoreLabel
from kivy.core.window import Window
from kivy.animation import Animation
from kivy.graphics import (Color, Ellipse, Line, PopMatrix, PushMatrix, Quad, Rectangle,
                           Rotate, RoundedRectangle, Triangle)
from kivy.graphics.texture import Texture
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import BooleanProperty, ListProperty, NumericProperty, ObjectProperty, StringProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image as KImage
from kivy.uix.image import AsyncImage
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.logger import Logger
Logger.setLevel("INFO")

try:
    import arabic_reshaper
    try:
        from bidi.algorithm import get_display
    except Exception:
        from bidi import get_display
    AR_OK = True
except Exception:
    AR_OK = False

# ------------------------------------------------------------------ fonts
def find_file(name):
    """يدوّر على الملف جنب السكربت ثم في الهاتف (لحد 5 مستويات)."""
    roots = []
    try:
        roots.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    roots += [os.getcwd(), "/storage/emulated/0/Download", "/storage/emulated/0",
              "/sdcard/Download", "/sdcard"]
    for r in roots:
        p = os.path.join(r, name)
        if os.path.isfile(p):
            return p
    target = name.lower()
    done = set()
    for r in roots:
        real = os.path.realpath(r)
        if real in done or not os.path.isdir(real):
            continue
        done.add(real)
        base = real.rstrip("/").count("/")
        try:
            for d, dirs, files in os.walk(real):
                if d.count("/") - base >= 5:
                    dirs[:] = []
                else:
                    dirs[:] = [x for x in dirs if not x.startswith(".") and x != "Android"]
                for fn in files:
                    if fn.lower() == target:
                        return os.path.join(d, fn)
        except Exception:
            pass
    return None


FFMPEG_WHY = []      # سبب عدم عمل ffmpeg (للتشخيص)
FFMPEG_NAME = "ffmpeg_bin"


def _try_run(p):
    import subprocess
    r = subprocess.run([p, "-version"], capture_output=True, timeout=30)
    return r.returncode == 0


def _ffmpeg_works(p):
    """بيتأكد إن ffmpeg بيشتغل فعلًا، ولو المكان مش بيسمح بالتنفيذ بينسخه جوه مجلد البرنامج."""
    try:
        os.chmod(p, 0o755)
    except Exception:
        pass
    try:
        if _try_run(p):
            return p
        FFMPEG_WHY.append("%s: exit code" % os.path.basename(p))
    except OSError as e:
        FFMPEG_WHY.append("%s: %s" % (os.path.basename(p), str(e)[:60]))
        if e.errno == 8:      # Exec format error: الملف لمعالج تاني، النسخ مش هيفيد
            return None
    except Exception as e:
        FFMPEG_WHY.append("%s: %s" % (type(e).__name__, str(e)[:60]))
    try:
        import shutil
        dst = os.path.join(data_dir(), FFMPEG_NAME)
        if (not os.path.isfile(dst)) or os.path.getsize(dst) != os.path.getsize(p):
            shutil.copyfile(p, dst)
        os.chmod(dst, 0o755)
        if _try_run(dst):
            return dst
    except Exception as e:
        FFMPEG_WHY.append("copy %s: %s" % (type(e).__name__, str(e)[:60]))
    return None


def find_ffmpeg():
    """اكتشاف FFmpeg حتى لو imageio-ffmpeg لا يتعرف على معمارية أندرويد."""
    import glob
    import shutil
    cands = []

    # مسار صريح يحدده المستخدم (لو عنده ملف FFmpeg متوافق مع أندرويد).
    env_path = os.environ.get("IMAGEIO_FFMPEG_EXE")
    if env_path:
        cands.append(env_path)

    # APK: ملف libffmpeg.so المتحزّم جوه التطبيق (أندرويد 10+ بيسمح بالتشغيل من هنا بس)
    try:
        from jnius import autoclass
        _act = autoclass("org.kivy.android.PythonActivity").mActivity
        _nld = str(_act.getApplicationInfo().nativeLibraryDir)
        cands.append(os.path.join(_nld, "libffmpeg.so"))
        cands.extend(sorted(glob.glob(os.path.join(_nld, "lib*ffmpeg*.so"))))
    except Exception:
        pass

    # FFmpeg ARM64 Lite الذي ثبّته المستخدم داخل مجلد Pydroid الخاص.
    cands.append("/data/user/0/iiec.pyramide.python/files/ffmpeg_bin")

    # نسخة اتنسخت قبل كده جوه مجلد البرنامج، أو أمر في PATH.
    try:
        cands.append(os.path.join(data_dir(), FFMPEG_NAME))
    except Exception:
        pass
    p = shutil.which("ffmpeg")
    if p:
        cands.append(p)

    # الطريقة المعتادة للمكتبة؛ على Android قد لا تدعم get_ffmpeg_exe المعمارية.
    try:
        import imageio_ffmpeg
        try:
            cands.append(imageio_ffmpeg.get_ffmpeg_exe())
        except Exception:
            pass
        # جرّب الملفات المضمّنة فعليًا داخل الحزمة بدل الاعتماد على اسم منصة مدعومة.
        pkg_dir = os.path.dirname(os.path.abspath(imageio_ffmpeg.__file__))
        for pattern in ("binaries/ffmpeg*", "binaries/*/ffmpeg*", "**/ffmpeg*", "**/*ffmpeg*"):
            cands.extend(glob.glob(os.path.join(pkg_dir, pattern), recursive=True))
    except Exception:
        pass

    # أماكن شائعة على أندرويد/Pydroid، بالإضافة إلى مجلد Download.
    for d in (
        "/storage/emulated/0/Download", "/sdcard/Download",
        "/data/data/ru.iiec.pydroid3/files/usr/bin",
        "/data/user/0/ru.iiec.pydroid3/files/usr/bin",
        "/data/data/iiec.pyramide.python/files/usr/bin",
        "/data/user/0/iiec.pyramide.python/files/usr/bin",
        "/data/data/com.termux/files/usr/bin",
        "/data/user/0/com.termux/files/usr/bin",
    ):
        cands.append(os.path.join(d, "ffmpeg"))

    # لو المستخدم فك ضغط الملف في مجلد فرعي داخل Downloads، دور فيه كمان.
    # البحث محدود بمجلد التنزيلات فقط لتجنب فحص ملفات التطبيق كلها.
    for root in ("/storage/emulated/0/Download", "/sdcard/Download"):
        try:
            cands.extend(glob.glob(os.path.join(root, "**", "ffmpeg"), recursive=True))
        except Exception:
            pass

    seen = set()
    for c in cands:
        if not c or c in seen or not os.path.isfile(c):
            continue
        seen.add(c)
        # تجاهل الملفات غير التنفيذية/غير المتوافقة؛ جرّب كل مرشح مستقلًا.
        r = _ffmpeg_works(c)
        if r:
            return r
    return None


FFMPEG = find_ffmpeg()
HAS_FFMPEG = bool(FFMPEG)

try:                         # PyAV (مكتبة av): بتحوّل الصوت لـ MP3 جوه بايثون من غير ملف ffmpeg
    import av as pyav
    HAS_AV = True
except Exception:
    pyav = None
    HAS_AV = False


import glob
import struct


def font_covers(path, need=(0xFE8D, 0xFE91, 0xFEE3, 0xFEFB)):
    """بيتأكد إن الخط فيه الحروف العربية المشكّلة (Presentation Forms)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        off = 0
        if data[:4] == b"ttcf":
            off = struct.unpack(">I", data[12:16])[0]
        n_tab = struct.unpack(">H", data[off + 4:off + 6])[0]
        cmap = None
        for i in range(n_tab):
            rec = data[off + 12 + 16 * i: off + 28 + 16 * i]
            if rec[:4] == b"cmap":
                cmap = struct.unpack(">I", rec[8:12])[0]
                break
        if cmap is None:
            return False
        ranges = []
        n = struct.unpack(">H", data[cmap + 2:cmap + 4])[0]
        for i in range(n):
            pid, eid, so = struct.unpack(">HHI", data[cmap + 4 + 8 * i: cmap + 12 + 8 * i])
            if pid not in (0, 3):
                continue
            st = cmap + so
            fmt = struct.unpack(">H", data[st:st + 2])[0]
            if fmt == 4:
                segx2 = struct.unpack(">H", data[st + 6:st + 8])[0]
                seg = segx2 // 2
                ends = struct.unpack(">%dH" % seg, data[st + 14: st + 14 + segx2])
                starts = struct.unpack(">%dH" % seg, data[st + 16 + segx2: st + 16 + 2 * segx2])
                ranges += list(zip(starts, ends))
            elif fmt == 12:
                ng = struct.unpack(">I", data[st + 12:st + 16])[0]
                for g in range(ng):
                    s, e, _ = struct.unpack(">III", data[st + 16 + 12 * g: st + 28 + 12 * g])
                    ranges.append((s, e))
        return all(any(s <= cp <= e for s, e in ranges) for cp in need)
    except Exception:
        return False


_reg = find_file("Cairo-Regular.ttf")
_bold = find_file("Cairo-Bold.ttf")
AR_FONT = "Roboto"   # اسم الخط اللي بيتستخدم للنص العربي
DIAG = []
if HAS_FFMPEG:
    DIAG.append("FFmpeg: %s" % FFMPEG)
else:
    DIAG.append("FFmpeg: NOT found (%s)" % ("; ".join(FFMPEG_WHY[:3]) if FFMPEG_WHY else "no candidates"))
DIAG.append("PyAV: %s" % ("OK" if HAS_AV else "MISSING"))
_cands = []
if _reg:
    _cands.append((_reg, _bold or _reg, "Cairo"))
else:
    DIAG.append("Cairo fonts: NOT found")
_sys = [p for p in sorted(set(glob.glob("/system/fonts/*Arab*") + glob.glob("/system/fonts/*Naskh*")))
        if p.lower().endswith((".ttf", ".otf"))]
for _p in _sys:
    if "bold" in _p.lower():
        continue
    _b = _p.replace("Regular", "Bold")
    _cands.append((_p, _b if _b in _sys else _p, os.path.basename(_p)))

_chosen = None
for _r, _b, _label in _cands:
    _ok = font_covers(_r)
    DIAG.append("%s: %s" % (_label if _label != "Cairo" else _r, "Arabic OK" if _ok else "no Arabic glyphs"))
    if _ok and _chosen is None:
        _chosen = (_r, _b, _label)

FONT_OK = _chosen is not None
if _chosen:
    try:
        if _chosen[2] == "Cairo":
            # Cairo يشمل الحروف كلها: نخليه الخط الافتراضي للبرنامج
            LabelBase.register(name="Roboto", fn_regular=_chosen[0], fn_bold=_chosen[1])
        else:
            # خط النظام للعربي بس، والإنجليزي يفضل بخطه
            LabelBase.register(name="ArFont", fn_regular=_chosen[0], fn_bold=_chosen[1])
            AR_FONT = "ArFont"
        DIAG.append("Active Arabic font: " + _chosen[2])
    except Exception as _e:
        FONT_OK = False
        DIAG.append("Font register error: %s" % _e)
else:
    DIAG.append("No font with Arabic glyphs found")
# خط مخصوص لحقل البحث: Cairo فيه إنجليزي وعربي، فبنسجّله باسم مستقل (من غير ما نعتمد على استبدال Roboto)
SEARCH_FONT = None
if _reg:
    try:
        LabelBase.register(name="NovaCairo", fn_regular=_reg, fn_bold=_bold or _reg)
        SEARCH_FONT = "NovaCairo"
    except Exception as _e:
        DIAG.append("NovaCairo register error: %s" % _e)
DIAG.append("arabic-reshaper/bidi: " + ("OK" if AR_OK else "MISSING"))

# ------------------------------------------------------------------ palette
THEMES = {
    "dark": dict(BG=(0.031, 0.035, 0.078, 1), CARD=(0.067, 0.075, 0.137, 1),
                 CARD2=(0.106, 0.118, 0.200, 1), DIM=(0.58, 0.62, 0.78, 1),
                 TXT=(0.96, 0.97, 1.0, 1), TRACK=(.15, .17, .29, 1),
                 BORDER=(1, 1, 1, .075), SHADOW=.40, GLOW=.55, ON=(0.72, 0.68, 1.0, 1)),
    "light": dict(BG=(0.945, 0.953, 0.980, 1), CARD=(1, 1, 1, 1),
                  CARD2=(0.925, 0.935, 0.972, 1), DIM=(0.36, 0.40, 0.54, 1),
                  TXT=(0.07, 0.09, 0.18, 1), TRACK=(.84, .86, .93, 1),
                  BORDER=(0.10, 0.12, 0.35, .10), SHADOW=.10, GLOW=.30, ON=(0.42, 0.36, 0.91, 1)),
}
ACC1 = (0.424, 0.361, 0.906)
ACC2 = (0.000, 0.722, 1.000)
WHITE = (1, 1, 1, 1)          # أبيض ثابت (فوق الألوان الملونة)
GREEN = (0.18, 0.80, 0.55, 1)
RED = (1.0, 0.40, 0.45, 1)
ACC_MID = (0.20, 0.47, 1.0)
GRAD = (ACC1, ACC_MID, ACC2)           # تدرّج الهوية: بنفسجي > أزرق > سماوي
TRANSP = (0, 0, 0, 0)
BG = CARD = CARD2 = DIM = TXT = TRACK = None
BORDER = ON = (1, 1, 1, .08)
SHADOW_A = .3
GLOW_A = .5


def apply_theme(name):
    """بيغيّر ألوان البرنامج (night / light)."""
    global BG, CARD, CARD2, DIM, TXT, TRACK, BORDER, ON, SHADOW_A, GLOW_A
    th = THEMES.get(name) or THEMES["dark"]
    BG, CARD, CARD2, DIM, TXT, TRACK = (th["BG"], th["CARD"], th["CARD2"],
                                        th["DIM"], th["TXT"], th["TRACK"])
    BORDER, ON = th["BORDER"], th["ON"]
    SHADOW_A, GLOW_A = th["SHADOW"], th["GLOW"]
    try:
        Window.clearcolor = BG
    except Exception:
        pass


apply_theme("dark")
Window.softinput_mode = "resize"

# ------------------------------------------------------------------ language
S = {
    "en": {
        "tagline": "Download video, audio & images",
        "hint": "Paste video, playlist or photo link here",
        "paste": "Paste", "clear": "Clear",
        "analyze": "Analyze Link", "analyzing": "Analyzing...",
        "tab_video": "Video", "tab_audio": "Audio", "tab_images": "Images",
        "empty": "Paste a video, playlist or photo link above\nthen tap Analyze",
        "get": "Get",
        "k_img": "Image",
        "img_meta": "%d photos",
        "img_none": "No images found in this link",
        "k_dv": "Video",
        "img_added": "%d images added to the queue",
        "img_added_skip": "%d added · %d already in the list",
        "open_file": "Open",
        "open_folder": "Folder",
        "ready": "Ready",
        "saves": "Saves to: %s", "saved": "Saved to: %s",
        "paste_first": "Please paste a link first",
        "fetching": "Fetching video info...", "wait": "Please wait",
        "read_fail": "Couldn't read this link",
        "choose": "Choose a quality",
        "busy": "A download is already running",
        "starting": "Starting download...", "connecting": "Connecting",
        "downloading": "Downloading", "left": "%ds left",
        "done": "Download complete", "failed": "Download failed",
        "k_av": "Video", "k_m": "Video", "k_mx": "Video", "k_v": "Video only", "k_a": "Audio only",
        "size_unknown": "Size unknown",
        "no_formats": "No formats in this tab",
        "mp3_hint": "MP3 conversion is unavailable: FFmpeg/PyAV was not detected.\nInstall imageio-ffmpeg (or PyAV with MP3 encoder support), then fully restart NovaGet. Until then audio downloads as M4A.",
        "settings": "Settings", "language": "Language", "close": "Close",
        "downloads": "Downloads", "nav_download": "Download",
        "notifications": "Notifications",
        "pause": "Pause", "resume": "Resume", "paused": "Paused", "pausing": "Pausing...",
        "left_t": "%s left",
        "tab_active": "In progress", "tab_history": "Completed",
        "no_active": "No active downloads\nPick a quality and tap Get",
        "no_history": "Nothing downloaded yet",
        "cancel": "Cancel", "retry": "Retry", "remove": "Remove",
        "cancel_all": "Cancel all", "clear_all": "Clear all",
        "waiting": "Waiting in queue", "processing": "Processing...",
        "cancelling": "Cancelling...",
        "added": "Download started", "queued": "Added to queue",
        "dup": "Already in the list", "cancelled": "Download cancelled",
        "active_n": "Downloading %d · Queued %d",
        "sound": "Sound", "on": "On", "off": "Off",
        "speed": "Download speed", "unlimited": "Unlimited",
        "parallel": "Simultaneous downloads",
        "pl_meta": "Playlist \u00b7 %d videos",
        "pl_best": "Auto",
        "sel_all": "Select all", "sel_none": "Deselect all",
        "dl_sel": "Download (%d)",
        "pl_none": "Select at least one video",
        "pl_added": "%d videos added to the queue",
        "pl_added_skip": "%d added \u00b7 %d already in the list",
        "pl_empty": "No videos found in this playlist",
        "pl_size": "~%s total",
        "err_title": "Full error details",
        "err_copy": "Copy error",
        "err_unknown": "Unknown error",
        "net_wait": "Waiting for Wi-Fi (mobile data is disabled)",
    },
    "ar": {
        "tagline": "حمّل الفيديو والصوت والصور",
        "hint": "الصق رابط فيديو أو بلاي ليست أو صور هنا",
        "paste": "لصق", "clear": "مسح",
        "analyze": "تحليل الرابط", "analyzing": "جارٍ التحليل...",
        "tab_video": "فيديو", "tab_audio": "صوت", "tab_images": "صور",
        "empty": "الصق رابط فيديو أو بلاي ليست أو صور بالأعلى\nثم اضغط على تحليل الرابط",
        "get": "تحميل",
        "k_img": "صورة",
        "img_meta": "%d صورة",
        "img_none": "مفيش صور في الرابط ده",
        "k_dv": "فيديو",
        "img_added": "تمت إضافة %d صورة للقائمة",
        "img_added_skip": "تمت إضافة %d · %d موجود بالفعل",
        "open_file": "فتح",
        "open_folder": "المجلد",
        "ready": "جاهز",
        "saves": u"يُحفظ في: ‪%s‬", "saved": u"تم الحفظ في: ‪%s‬",
        "paste_first": "من فضلك الصق رابطًا أولًا",
        "fetching": "جارٍ جلب معلومات الفيديو...", "wait": "برجاء الانتظار",
        "read_fail": "تعذّرت قراءة هذا الرابط",
        "choose": "اختر الجودة",
        "busy": "يوجد تحميل قيد التنفيذ",
        "starting": "جارٍ بدء التحميل...", "connecting": "جارٍ الاتصال",
        "downloading": "جارٍ التحميل", "left": "متبقي %d ث",
        "done": "اكتمل التحميل", "failed": "فشل التحميل",
        "k_av": "فيديو", "k_m": "فيديو", "k_mx": "فيديو", "k_v": "فيديو فقط", "k_a": "صوت فقط",
        "size_unknown": "الحجم غير معروف",
        "no_formats": "لا توجد صيغ في هذا التبويب",
        "mp3_hint": "تحويل MP3 مش متاح: البرنامج مش لاقي FFmpeg أو PyAV.\nثبّت imageio-ffmpeg (أو PyAV بدعم MP3) وبعدها اقفل NovaGet وافتحه تاني. لحد وقتها الصوت هيتحمّل M4A.",
        "settings": "الإعدادات", "language": "اللغة", "close": "إغلاق",
        "downloads": "التحميلات", "nav_download": "تنزيل",
        "notifications": "الإشعارات",
        "pause": "إيقاف مؤقت", "resume": "استئناف", "paused": "متوقف مؤقتًا",
        "pausing": "جارٍ الإيقاف المؤقت...",
        "left_t": "متبقي %s",
        "tab_active": "جاري التحميل", "tab_history": "تم التحميل",
        "no_active": "لا توجد تحميلات جارية\nاختر الجودة ثم اضغط تحميل",
        "no_history": "لم يتم تحميل أي شيء بعد",
        "cancel": "إلغاء", "retry": "إعادة", "remove": "إزالة",
        "cancel_all": "إلغاء الكل", "clear_all": "مسح الكل",
        "waiting": "في قائمة الانتظار", "processing": "جارٍ المعالجة...",
        "cancelling": "جارٍ الإلغاء...",
        "added": "بدأ التحميل", "queued": "أُضيف إلى قائمة الانتظار",
        "dup": "موجود بالفعل في القائمة", "cancelled": "تم إلغاء التحميل",
        "active_n": "جارٍ تحميل %d · في الانتظار %d",
        "sound": "الصوت", "on": "يعمل", "off": "متوقف",
        "speed": "سرعة التحميل", "unlimited": "بدون حد",
        "parallel": "عدد التحميلات في نفس الوقت",
        "pl_meta": "بلاي ليست \u00b7 %d فيديو",
        "pl_best": "تلقائي",
        "sel_all": "تحديد الكل", "sel_none": "إلغاء التحديد",
        "dl_sel": "تحميل (%d)",
        "pl_none": "اختر فيديو واحد على الأقل",
        "pl_added": "تمت إضافة %d فيديو لقائمة التحميل",
        "pl_added_skip": "تمت إضافة %d \u00b7 %d موجود بالفعل",
        "pl_empty": "مفيش فيديوهات في البلاي ليست دي",
        "pl_size": "حوالي %s إجمالي",
        "err_title": "تفاصيل الخطأ كاملة",
        "err_copy": "نسخ الخطأ",
        "err_unknown": "خطأ غير معروف",
        "net_wait": "في انتظار الواي فاي (بيانات الهاتف ممنوعة)",
    },
}
STATE = {"lang": "en"}

BIDI_CTRL_RE = re.compile(u"[\u200e\u200f\u202a-\u202e\u2066-\u2069\u061c]")
AR_RE = re.compile(u"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
JUNK_RE = re.compile(u"[\U00010000-\U0010FFFF\u200b-\u200d\u2060-\u2064\u00ad\u180e"
                     u"\ufe00-\ufe0f\ufeff\u3164\u115f\u1160\u2600-\u27BF"
                     u"\u2190-\u21ff\u2300-\u23ff\u25a0-\u25ff\u2b00-\u2bff\u3000-\u303f\uff00-\uffef\ufff0-\uffff]")


AR_RUN = re.compile(
    u"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]+"
    u"(?:[ ]+[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]+)*")


def fixp(s):
    """نص عادي (بدون markup): يشكّل الحروف العربية ويرتّب الاتجاه."""
    s = JUNK_RE.sub("", str(s))
    # رموز الاتجاه المخفية (LRE/RLE/PDF/LRO/RLO/LRI/RLI/FSI/PDI...) بتكسّر مكتبة bidi لو جت ناقصة
    s = BIDI_CTRL_RE.sub("", s)
    if AR_OK and AR_RE.search(s):
        def _disp(l):
            r = arabic_reshaper.reshape(l)
            try:
                return get_display(r)
            except Exception:
                return r[::-1] if AR_RE.search(r) else r
        out = "\n".join(_disp(l) for l in s.split("\n"))
        # نشيل علامات الاتجاه الخفية عشان ما تظهرش كمربعات
        return re.sub(u"[‪-‮⁦-⁩‎‏]", "", out)
    return s


def fix(s):
    """نفس fixp لكن بصيغة markup: الأجزاء العربية بالخط العربي والباقي بالخط العادي."""
    from kivy.utils import escape_markup
    s = fixp(s)
    if AR_FONT == "Roboto":
        return escape_markup(s)
    out, pos = [], 0
    for m in AR_RUN.finditer(s):
        out.append(escape_markup(s[pos:m.start()]))
        out.append("[font=%s]%s[/font]" % (AR_FONT, escape_markup(m.group())))
        pos = m.end()
    out.append(escape_markup(s[pos:]))
    return "".join(out)


def ts(key, *a):
    s = S[STATE["lang"]][key]
    return s % a if a else s


def t(key, *a):
    return fix(ts(key, *a))


def L(en, ar):
    """نص بلغتين (جاهز للعرض)."""
    return fix(ar if STATE["lang"] == "ar" else en)


def data_dir():
    return _diag_dir()


def settings_path():
    return os.path.join(data_dir(), "novaget_settings.json")


def history_path():
    return os.path.join(data_dir(), "novaget_history.json")


MAX_PAR = 5          # أقصى عدد تحميلات في نفس الوقت
HIST_MAX = 100       # أقصى عدد عناصر في سجل التحميلات
KB, MB = 1024, 1024 * 1024
# درجات السرعة (بايت/ثانية) من الأبطأ للأسرع - آخر درجة = بدون حد
SPEED_LEVELS = [100 * KB, 200 * KB, 300 * KB, 500 * KB, 750 * KB,
                1 * MB, int(1.5 * MB), 2 * MB, 3 * MB, 5 * MB, 8 * MB, 0]
SPEED_MAX = len(SPEED_LEVELS) - 1
CFG = {"lang": "en", "sound": True, "notify": True, "speed_i": SPEED_MAX, "max_dl": 1, "ig_session": "", "ig_cookies": "",
       "fb_cookies": "", "theme": "dark", "mobile_ok": True, "max_dl_wifi": 1, "max_dl_mobile": 1,
       "frags": 8}
FRAGS_MIN, FRAGS_MAX = 1, 16


def frags_n():
    """عدد الأجزاء/الاتصالات لكل تحميل (من الإعدادات)."""
    try:
        return max(FRAGS_MIN, min(FRAGS_MAX, int(CFG.get("frags", 8))))
    except Exception:
        return 8


def rate_limit():
    """حد السرعة بالبايت/ثانية (None = بدون حد)."""
    try:
        return SPEED_LEVELS[int(CFG.get("speed_i", SPEED_MAX))] or None
    except Exception:
        return None


def speed_text(i):
    v = SPEED_LEVELS[max(0, min(SPEED_MAX, int(i)))]
    if not v:
        return ts("unlimited")
    return ("%d KB/s" % (v // KB)) if v < MB else ("%g MB/s" % (v / float(MB)))


def load_cfg():
    try:
        with open(settings_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        d = {}
    except Exception as exc:
        _storage_warning("load settings", exc)
        d = {}
    if not isinstance(d, dict):
        d = {}
    if d.get("lang") in S:
        CFG["lang"] = d["lang"]
    CFG["sound"] = bool(d.get("sound", True))
    CFG["notify"] = bool(d.get("notify", True))
    CFG["ig_session"] = str(d.get("ig_session", "") or "").strip()
    CFG["ig_cookies"] = str(d.get("ig_cookies", "") or "").strip()
    CFG["fb_cookies"] = str(d.get("fb_cookies", "") or "").strip()
    old = {"fast": SPEED_MAX, "medium": 6, "slow": 2}   # إعدادات النسخة القديمة
    if "speed_i" in d:
        try:
            CFG["speed_i"] = max(0, min(SPEED_MAX, int(d["speed_i"])))
        except Exception:
            CFG["speed_i"] = SPEED_MAX
    elif d.get("speed") in old:
        CFG["speed_i"] = old[d["speed"]]
    def _n(key, default):
        try:
            return max(1, min(MAX_PAR, int(d.get(key, default))))
        except Exception:
            return default
    try:
        CFG["frags"] = max(FRAGS_MIN, min(FRAGS_MAX, int(d.get("frags", 8))))
    except Exception:
        CFG["frags"] = 8
    CFG["max_dl"] = _n("max_dl", 1)
    CFG["max_dl_wifi"] = _n("max_dl_wifi", CFG["max_dl"])
    CFG["max_dl_mobile"] = _n("max_dl_mobile", 1)
    CFG["mobile_ok"] = bool(d.get("mobile_ok", True))
    CFG["theme"] = d.get("theme") if d.get("theme") in ("dark", "light") else "dark"


_JSON_WRITE_LOCK = threading.RLock()
_STORAGE_WARNINGS = []


def _storage_warning(label, exc):
    message = "%s: %s: %s" % (label, type(exc).__name__, str(exc)[:240])
    try:
        _STORAGE_WARNINGS.append(message)
        del _STORAGE_WARNINGS[:-10]
    except Exception:
        pass
    try:
        Logger.warning("NovaGet storage: " + message)
    except Exception:
        try:
            print("NovaGet storage warning: " + message)
        except Exception:
            pass


def _atomic_json_write(path, payload, label):
    """Write JSON beside the destination, then atomically replace the old file."""
    tmp_path = None
    with _JSON_WRITE_LOCK:
        try:
            folder = os.path.dirname(os.path.abspath(path)) or os.getcwd()
            os.makedirs(folder, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(prefix=".novaget_write_", suffix=".tmp", dir=folder)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            os.replace(tmp_path, path)
            tmp_path = None
            return True
        except Exception as exc:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            _storage_warning("save " + label, exc)
            return False


def save_cfg():
    try:
        # Serialize snapshot and replacement together so an older concurrent save
        # cannot overwrite a newer settings snapshot.
        with _JSON_WRITE_LOCK:
            snapshot = dict(CFG)
            return _atomic_json_write(settings_path(), snapshot, "settings")
    except Exception as exc:
        _storage_warning("save settings", exc)
        return False


_NET = {"t": 0.0, "kind": "unknown"}


def net_kind():
    """نوع الشبكة الحالية: wifi / mobile / none / unknown (بيتخزّن 3 ثواني)."""
    now = time.time()
    if now - _NET["t"] < 3.0:
        return _NET["kind"]
    _NET["t"] = now
    kind = "unknown"
    try:
        from jnius import autoclass, cast
        pa = None
        for name in ("org.kivy.android.PythonActivity", "org.renpy.android.PythonActivity"):
            try:
                pa = autoclass(name)
                break
            except Exception:
                continue
        if pa is not None and pa.mActivity is not None:
            act = pa.mActivity
            ctx = autoclass("android.content.Context")
            cm = cast("android.net.ConnectivityManager",
                      act.getSystemService(ctx.CONNECTIVITY_SERVICE))
            ni = cm.getActiveNetworkInfo()
            if ni is None or not ni.isConnected():
                kind = "none"
            else:
                tp = int(ni.getType())
                kind = "mobile" if tp in (0, 2, 3, 4, 5) else "wifi"
    except Exception:
        kind = "unknown"
    _NET["kind"] = kind
    return kind


def net_allowed():
    """هل مسموح التحميل دلوقتي؟ (ممنوع على بيانات الهاتف لو المستخدم قفلها)."""
    return not (net_kind() == "mobile" and not CFG.get("mobile_ok", True))


def cur_limit():
    """أقصى عدد تحميلات في نفس الوقت حسب نوع الشبكة."""
    key = "max_dl_mobile" if net_kind() == "mobile" else "max_dl_wifi"
    try:
        return max(1, min(MAX_PAR, int(CFG.get(key, 1))))
    except Exception:
        return 1


def load_history():
    try:
        with open(history_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, list):
            raise ValueError("history JSON must contain a list")
        return [e for e in d if isinstance(e, dict) and isinstance(e.get("it"), dict)
                and e.get("title") is not None][:HIST_MAX]
    except FileNotFoundError:
        return []
    except Exception as exc:
        _storage_warning("load history", exc)
        return []


def save_history(h):
    try:
        # Deep-copy and replace under one re-entrant lock so an older concurrent
        # snapshot cannot overwrite a more recent history update.
        with _JSON_WRITE_LOCK:
            snapshot = copy.deepcopy(list(h[:HIST_MAX]))
            return _atomic_json_write(history_path(), snapshot, "history")
    except Exception as exc:
        _storage_warning("save history", exc)
        return False


# ------------------------------------------------------------------ sounds
SR = 22050
# كل صوت = قائمة نغمات (تردد Hz, مدة ms, ارتفاع, سرعة الخفوت)
SOUND_DEFS = {
    "tap":   [(1200, 35, 0.35, 6.0)],
    "queue": [(660, 70, 0.45, 4.0), (880, 110, 0.45, 4.0)],
    "start": [(523.25, 80, 0.5, 3.5), (659.25, 80, 0.5, 3.5), (783.99, 150, 0.5, 4.0)],
    "done":  [(523.25, 90, 0.5, 3.0), (659.25, 90, 0.5, 3.0), (783.99, 90, 0.5, 3.0),
              (1046.5, 380, 0.55, 3.5)],
    "error": [(392.0, 150, 0.5, 3.0), (311.13, 320, 0.55, 3.0)],
}


def _tone(freq, ms, vol, decay):
    n = max(1, int(SR * ms / 1000.0))
    atk = max(1, int(SR * 0.004))
    out = []
    for i in range(n):
        env = min(1.0, i / float(atk)) * math.exp(-decay * i / float(n))
        v = math.sin(2 * math.pi * freq * i / SR) + 0.25 * math.sin(4 * math.pi * freq * i / SR)
        out.append(v * env * vol * 0.8)
    return out


def _write_wav(path, samples):
    import wave
    vals = [max(-32767, min(32767, int(x * 32767))) for x in samples]
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(struct.pack("<%dh" % len(vals), *vals))


def sound_file(d, name):
    return os.path.join(d, "%s_v1.wav" % name)


def gen_sounds(d):
    """بيولّد ملفات الأصوات (WAV) مرة واحدة لو مش موجودة."""
    for name, parts in SOUND_DEFS.items():
        path = sound_file(d, name)
        if os.path.isfile(path) and os.path.getsize(path) > 100:
            continue
        samples = []
        for f, ms, vol, dec in parts:
            samples += _tone(f, ms, vol, dec)
        _write_wav(path, samples)


def sounds_dir():
    for base in (data_dir(), tempfile.gettempdir()):
        d = os.path.join(base, "novaget_sounds")
        try:
            os.makedirs(d, exist_ok=True)
            test = os.path.join(d, ".w")
            with open(test, "w") as f:
                f.write("1")
            os.remove(test)
            return d
        except Exception:
            continue
    return tempfile.gettempdir()


class SoundFX(object):
    def __init__(self):
        self.snd = {}

    def load(self):
        try:
            from kivy.core.audio import SoundLoader
            d = sounds_dir()
            gen_sounds(d)
            for name in SOUND_DEFS:
                snd = SoundLoader.load(sound_file(d, name))
                if snd:
                    self.snd[name] = snd
            DIAG.append("Sound: %s (%d/%d)" % ("OK" if self.snd else "unavailable",
                                               len(self.snd), len(SOUND_DEFS)))
        except Exception as e:
            DIAG.append("Sound error: %s" % e)

    def play(self, name):
        if not CFG.get("sound", True):
            return
        snd = self.snd.get(name)
        if snd is None:
            return
        try:
            snd.stop()
            snd.play()
        except Exception:
            pass


SOUND = SoundFX()


# ------------------------------------------------------------------ notifications (Android)
A_PAUSE = "com.novaget.action.PAUSE"
A_RESUME = "com.novaget.action.RESUME"
A_CANCEL = "com.novaget.action.CANCEL"
PROBLEMS = []     # مشاكل تظهر في الإعدادات (لو فيه)


class Notifier(object):
    """إشعارات أندرويد عن طريق pyjnius (بدون مكتبات إضافية)."""
    CH_PROG = "novaget_progress"
    CH_DONE = "novaget_done"
    ID_PROG = 2000   # base for progress notifications
    ID_DONE = 3000   # base for completion notifications

    def __init__(self):
        self.ok = False
        self.actions_ok = False
        self.act = None
        self.nm = None
        self.sdk = 0
        self.callback = None
        self.br = None
        self._err_logged = False

    def cs(self, text):
        return self._cast("java.lang.CharSequence", self._String(str(text)))

    def _fail(self, e):
        if not self._err_logged:
            self._err_logged = True
            PROBLEMS.append("Notification error: %s" % str(e)[:90])

    def setup(self, callback):
        self.callback = callback
        try:
            from jnius import autoclass, cast
            self._auto, self._cast = autoclass, cast
            self._String = autoclass("java.lang.String")
            pa = None
            for name in ("org.kivy.android.PythonActivity", "org.renpy.android.PythonActivity"):
                try:
                    pa = autoclass(name)
                    break
                except Exception:
                    continue
            if pa is None or pa.mActivity is None:
                raise RuntimeError("no Android activity")
            self.act = pa.mActivity
            self.sdk = int(autoclass("android.os.Build$VERSION").SDK_INT)
            ctx = autoclass("android.content.Context")
            self.nm = cast("android.app.NotificationManager",
                           self.act.getSystemService(ctx.NOTIFICATION_SERVICE))
            self.R = autoclass("android.R$drawable")
            if self.sdk >= 26:
                nmc = autoclass("android.app.NotificationManager")
                nch = autoclass("android.app.NotificationChannel")
                self.nm.createNotificationChannel(
                    nch(self.CH_PROG, self.cs("NovaGet downloads"), nmc.IMPORTANCE_LOW))
                self.nm.createNotificationChannel(
                    nch(self.CH_DONE, self.cs("NovaGet completed"), nmc.IMPORTANCE_DEFAULT))
            self.ok = True
            try:   # أزرار (إيقاف/استئناف/إلغاء) جوه الإشعار محتاجة مستقبِل بث
                from android.broadcast import BroadcastReceiver
                self.br = BroadcastReceiver(self._on_broadcast,
                                            actions=[A_PAUSE, A_RESUME, A_CANCEL])
                self.br.start()
                self.actions_ok = True
            except Exception:
                self.actions_ok = False
            DIAG.append("Notifications: OK%s" % (" (+buttons)" if self.actions_ok else " (no buttons)"))
        except Exception as e:
            self.ok = False
            DIAG.append("Notifications unavailable: %s" % str(e)[:80])
            PROBLEMS.append("Notifications unavailable: %s" % str(e)[:80])

    def request_permission(self):
        """أندرويد 13+ بيطلب إذن الإشعارات."""
        if not self.ok or self.sdk < 33:
            return
        try:
            perm = "android.permission.POST_NOTIFICATIONS"
            pm = self._auto("android.content.pm.PackageManager")
            if self.act.checkSelfPermission(perm) != pm.PERMISSION_GRANTED:
                self.act.requestPermissions([perm], 7)
        except Exception as e:
            self._fail(e)

    def _on_broadcast(self, context, intent):
        try:
            action = str(intent.getAction())
            jid = str(intent.getStringExtra("job"))
            if self.callback:
                self.callback(action, jid)
        except Exception as e:
            self._fail(e)

    def _builder(self, channel):
        b = self._auto("android.app.Notification$Builder")
        return b(self.act, channel) if self.sdk >= 26 else b(self.act)

    def _flags(self):
        pi = self._auto("android.app.PendingIntent")
        return pi.FLAG_UPDATE_CURRENT | (pi.FLAG_IMMUTABLE if self.sdk >= 23 else 0)

    def _content_intent(self):
        intent = self._auto("android.content.Intent")
        pi = self._auto("android.app.PendingIntent")
        it = intent(self.act, self.act.getClass())
        it.setFlags(0x20000000 | 0x04000000)   # SINGLE_TOP | CLEAR_TOP
        return pi.getActivity(self.act, 0, it, self._flags())

    def _action_intent(self, action, jid, k):
        intent = self._auto("android.content.Intent")
        pi = self._auto("android.app.PendingIntent")
        it = intent(action)
        it.setPackage(self.act.getPackageName())
        it.putExtra("job", str(jid))
        return pi.getBroadcast(self.act, int(jid) * 4 + k, it, self._flags())

    def show_progress(self, jid, title, short, big, pct, indet, paused, lab_toggle, lab_cancel):
        if not self.ok:
            return
        try:
            b = self._builder(self.CH_PROG)
            b.setSmallIcon(self.R.ic_media_pause if paused else self.R.stat_sys_download)
            b.setContentTitle(self.cs((title or "NovaGet")[:80]))
            b.setContentText(self.cs(short))
            b.setOnlyAlertOnce(True)
            b.setOngoing(not paused)
            b.setProgress(100, int(pct), bool(indet))
            try:
                style = self._auto("android.app.Notification$BigTextStyle")()
                style.bigText(self.cs(big))
                b.setStyle(style)
            except Exception:
                pass
            b.setContentIntent(self._content_intent())
            if self.actions_ok:
                if paused:
                    b.addAction(self.R.ic_media_play, self.cs(lab_toggle),
                                self._action_intent(A_RESUME, jid, 2))
                else:
                    b.addAction(self.R.ic_media_pause, self.cs(lab_toggle),
                                self._action_intent(A_PAUSE, jid, 1))
                b.addAction(self.R.ic_menu_close_clear_cancel, self.cs(lab_cancel),
                            self._action_intent(A_CANCEL, jid, 3))
            self.nm.notify(self.ID_PROG + int(jid), b.build())
        except Exception as e:
            self._fail(e)

    def post_done(self, jid, title, text, ok):
        if not self.ok:
            return
        try:
            b = self._builder(self.CH_DONE)
            b.setSmallIcon(self.R.stat_sys_download_done if ok else self.R.stat_notify_error)
            b.setContentTitle(self.cs(title))
            b.setContentText(self.cs((text or "")[:120]))
            b.setAutoCancel(True)
            b.setContentIntent(self._content_intent())
            self.nm.notify(self.ID_DONE + int(jid), b.build())
        except Exception as e:
            self._fail(e)

    def cancel_job(self, jid):
        if not self.ok:
            return
        try:
            self.nm.cancel(self.ID_PROG + int(jid))
        except Exception as e:
            self._fail(e)


NOTIF = Notifier()


class KeepAlive(object):
    """WakeLock + WifiLock بيتفعّلوا وفيه تحميل شغال عشان أندرويد ما يوقفش الشبكة/المعالج والتطبيق في الخلفية."""

    def __init__(self):
        self.wl = None
        self.wf = None
        self.on = False

    def set(self, active):
        if active == self.on:
            return
        try:
            from jnius import autoclass
            pa = autoclass("org.kivy.android.PythonActivity")
            act = pa.mActivity
            ctx = autoclass("android.content.Context")
            if active:
                if self.wl is None:
                    pm = act.getSystemService(ctx.POWER_SERVICE)
                    self.wl = pm.newWakeLock(1, "NovaGet:dl")      # PARTIAL_WAKE_LOCK
                    self.wl.setReferenceCounted(False)
                if not self.wl.isHeld():
                    self.wl.acquire()
                try:
                    if self.wf is None:
                        wm = act.getApplicationContext().getSystemService(ctx.WIFI_SERVICE)
                        self.wf = wm.createWifiLock(3, "NovaGet:wifi")   # WIFI_MODE_FULL_HIGH_PERF
                        self.wf.setReferenceCounted(False)
                    if not self.wf.isHeld():
                        self.wf.acquire()
                except Exception:
                    pass
            else:
                for l in (self.wl, self.wf):
                    try:
                        if l is not None and l.isHeld():
                            l.release()
                    except Exception:
                        pass
            self.on = active
        except Exception as ex:
            nlog("KeepAlive: %s" % ex)
            self.on = active


KEEP = KeepAlive()
ACTIVE_JOBS_FILE = "novaget_active.json"


KV = """
#:import dp kivy.metrics.dp
#:import sp kivy.metrics.sp

<Card>:
    canvas.before:
        Color:
            rgba: 0, 0, 0, self.shadow_a * .45
        RoundedRectangle:
            pos: self.x - dp(2), self.y - dp(7)
            size: self.width + dp(4), self.height + dp(2)
            radius: [self.radius + dp(4)]
        Color:
            rgba: 0, 0, 0, self.shadow_a
        RoundedRectangle:
            pos: self.x, self.y - dp(3)
            size: self.width, self.height
            radius: [self.radius]
        Color:
            rgba: self.bg
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [self.radius]
        Color:
            rgba: self.border
        Line:
            rounded_rectangle: self.x, self.y, self.width, self.height, self.rr
            width: dp(1)

<TLabel>:
    text_size: self.size
    halign: 'left'
    valign: 'middle'
    shorten: True
    shorten_from: 'right'

<RoundButton>:
    font_size: sp(14)
    bold: True
    opacity: .5 if self.disabled else 1
    canvas.before:
        Color:
            rgba: ((1, 1, 1, 1) if self.state == 'normal' else (.8, .8, .8, 1)) if self.accent else (0, 0, 0, 0)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [self.radius]
            texture: self.tex
        Color:
            rgba: (0, 0, 0, 0) if self.accent else (self.bg if self.state == 'normal' else self.bg_down)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [self.radius]
        Color:
            rgba: self.bd
        Line:
            rounded_rectangle: self.x, self.y, self.width, self.height, self.rr
            width: dp(1)

<GradButton>:
    font_size: sp(16)
    bold: True
    opacity: .6 if self.disabled else 1
    canvas.before:
        Color:
            rgba: .30, .38, 1, (0 if self.disabled else .30)
        RoundedRectangle:
            pos: self.x + dp(8), self.y - dp(6)
            size: self.width - dp(16), self.height
            radius: [dp(20)]
        Color:
            rgba: .0, .72, 1, (0 if self.disabled else .10)
        RoundedRectangle:
            pos: self.x + dp(2), self.y - dp(3)
            size: self.width - dp(4), self.height
            radius: [dp(18)]
        Color:
            rgba: (1, 1, 1, 1) if self.state == 'normal' else (.78, .78, .78, 1)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(18)]
            texture: self.tex
        Color:
            rgba: 1, 1, 1, .15
        RoundedRectangle:
            pos: self.x + dp(1), self.y + self.height * .52
            size: self.width - dp(2), self.height * .47
            radius: [dp(17), dp(17), dp(8), dp(8)]

<SlimProgress>:
    canvas:
        Color:
            rgba: self.track
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [self.height / 2.]
        Color:
            rgba: 1, 1, 1, 1
        RoundedRectangle:
            pos: self.pos
            size: self.fill_w, self.height
            radius: [self.height / 2.]
            texture: self.tex
        Color:
            rgba: 0, .72, 1, (.30 if self.value > 0 else 0)
        Ellipse:
            pos: self.x + self.fill_w - self.height * 1.9, self.y - self.height * 0.9
            size: self.height * 2.8, self.height * 2.8
        Color:
            rgba: 1, 1, 1, (.95 if self.value > 0 else 0)
        Ellipse:
            pos: self.x + self.fill_w - self.height * 1.0, self.y
            size: self.height, self.height
"""


# ------------------------------------------------------------------ widgets
_GRAD_CACHE = {}
_GLOW_CACHE = {}


def make_grad(*cols):
    """تدرّج لوني أفقي من أي عدد ألوان (2 أو 3 أو أكتر)."""
    if len(cols) == 1 and cols[0] and isinstance(cols[0][0], (list, tuple)):
        cols = tuple(cols[0])
    key = tuple(tuple(c[:3]) for c in cols)
    tx = _GRAD_CACHE.get(key)
    if tx is not None:
        return tx
    tx = Texture.create(size=(len(cols), 1), colorfmt="rgba")
    data = []
    for c in cols:
        data += [int(c[0] * 255), int(c[1] * 255), int(c[2] * 255), 255]
    tx.blit_buffer(bytes(data), colorfmt="rgba", bufferfmt="ubyte")
    tx.mag_filter = "linear"
    tx.min_filter = "linear"
    try:
        tx.wrap = "clamp_to_edge"
    except Exception:
        pass
    _GRAD_CACHE[key] = tx
    return tx


def make_glow(col, n=40):
    """هالة دائرية ناعمة (شفافية بتقل ناحية الأطراف) لعمل إضاءة في الخلفية."""
    key = tuple(col[:3])
    tx = _GLOW_CACHE.get(key)
    if tx is not None:
        return tx
    r8, g8, b8 = int(col[0] * 255), int(col[1] * 255), int(col[2] * 255)
    buf = bytearray()
    c = (n - 1) / 2.0
    for j in range(n):
        for i in range(n):
            d = math.hypot(i - c, j - c) / c
            a = max(0.0, 1.0 - d)
            a = a * a * (3 - 2 * a)
            buf += bytes((r8, g8, b8, int(a * 255)))
    tx = Texture.create(size=(n, n), colorfmt="rgba")
    tx.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    tx.mag_filter = "linear"
    tx.min_filter = "linear"
    _GLOW_CACHE[key] = tx
    return tx


def add_aurora(w):
    """إضاءة خفيفة (بنفسجي + سماوي) في خلفية الشاشة كلها."""
    def draw(*a):
        w.canvas.before.clear()
        W = float(w.width)
        if W < 10:
            return
        with w.canvas.before:
            Color(1, 1, 1, GLOW_A)
            s = W * 1.5
            Rectangle(texture=make_glow(ACC1), size=(s, s),
                      pos=(w.x + W * .10 - s / 2.0, w.top - dp(70) - s / 2.0))
            Color(1, 1, 1, GLOW_A * .85)
            s = W * 1.2
            Rectangle(texture=make_glow(ACC2), size=(s, s),
                      pos=(w.x + W * .98 - s / 2.0, w.top + dp(10) - s / 2.0))
            Color(1, 1, 1, GLOW_A * .45)
            s = W * 1.1
            Rectangle(texture=make_glow(ACC1), size=(s, s),
                      pos=(w.right - s / 2.0, w.y + dp(90) - s / 2.0))
    w.bind(pos=draw, size=draw)
    draw()


def add_top_line(w):
    """خط رفيع في أعلى العنصر (فاصل شريط التنقل)."""
    with w.canvas.after:
        Color(*BORDER)
        ln = Rectangle(pos=(w.x, w.top - 1), size=(w.width, 1))

    def upd(*a):
        ln.pos = (w.x, w.top - 1)
        ln.size = (w.width, 1)
    w.bind(pos=upd, size=upd)


class Card(BoxLayout):
    bg = ListProperty(CARD)
    radius = NumericProperty(dp(18))
    border = ListProperty([0, 0, 0, 0])
    elev = NumericProperty(0)
    shadow_a = NumericProperty(0)
    rr = NumericProperty(dp(1))

    def __init__(self, **kw):
        kw.setdefault("bg", CARD)
        kw.setdefault("border", list(BORDER))
        super().__init__(**kw)
        self.shadow_a = SHADOW_A if self.elev else 0
        self.bind(size=self._calc_rr, radius=self._calc_rr)
        self._calc_rr()

    def _calc_rr(self, *a):
        self.rr = max(dp(1), min(self.radius, self.height / 2.0, self.width / 2.0))


class AutoFont(object):
    """بيفعّل الـ markup عشان الأجزاء العربية تتعرض بخط عربي والباقي بالخط العادي."""

    def _init_auto(self):
        self.markup = True


class AInput(TextInput):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(hint_text=self._auto, text=self._auto)
        self._auto()

    def _auto(self, *a):
        s = self.text if self.text else self.hint_text
        self.font_name = AR_FONT if AR_RE.search(s or "") else "Roboto"


class TLabel(AutoFont, Label):
    def __init__(self, **kw):
        kw.setdefault("color", TXT)
        super().__init__(**kw)
        self._init_auto()


class RoundButton(AutoFont, ButtonBehavior, Label):
    """زرار: لو لونه = لون البرنامج الأساسي بيترسم تلقائيًا بتدرّج (Premium)."""
    bg = ListProperty(CARD2)
    bg_down = ListProperty([0, 0, 0, 1])
    radius = NumericProperty(dp(14))
    accent = BooleanProperty(False)
    tex = ObjectProperty(None)
    bd = ListProperty([0, 0, 0, 0])
    rr = NumericProperty(dp(1))

    def __init__(self, **kw):
        kw.setdefault("bg", CARD2)
        super().__init__(**kw)
        self.tex = make_grad(*GRAD)
        self.bind(bg=self._upd)
        self.bind(size=self._calc_rr, radius=self._calc_rr)
        self._calc_rr()
        self._upd()
        self._init_auto()
        self.bind(on_press=lambda *_: SOUND.play("tap"))

    def _calc_rr(self, *a):
        self.rr = max(dp(1), min(self.radius, self.height / 2.0, self.width / 2.0))

    def _upd(self, *a):
        b = self.bg
        self.accent = bool(abs(b[0] - ACC1[0]) < .004 and abs(b[1] - ACC1[1]) < .004
                           and abs(b[2] - ACC1[2]) < .004 and b[3] > .9)
        if b[3] < .3:                       # زرار شفاف (تبويب غير مفعّل)
            self.bg_down = [.55, .58, .75, .16]
            self.color = TXT
            self.bd = [0, 0, 0, 0]
            return
        self.bg_down = [b[0] * .7, b[1] * .7, b[2] * .7, b[3]]
        lum = .299 * b[0] + .587 * b[1] + .114 * b[2]
        self.color = (1, 1, 1, 1) if (lum < .62 or self.accent) else TXT
        self.bd = [0, 0, 0, 0] if self.accent else list(BORDER)


class GradButton(AutoFont, ButtonBehavior, Label):
    tex = ObjectProperty(None)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.tex = make_grad(*GRAD)
        self._init_auto()
        self.bind(on_release=lambda *_: SOUND.play("tap"))


class SlimProgress(Widget):
    value = NumericProperty(0)
    fill_w = NumericProperty(0)
    tex = ObjectProperty(None)
    track = ListProperty([.17, .20, .32, 1])

    def __init__(self, **kw):
        super().__init__(**kw)
        self.track = list(TRACK)
        self.tex = make_grad(*GRAD)
        self.bind(value=self._calc, size=self._calc)

    def _calc(self, *a):
        if self.value > 0:
            self.fill_w = max(self.height, self.width * self.value / 100.0)
        else:
            self.fill_w = 0


class IconButton(ButtonBehavior, Widget):
    """زرار أيقونة مرسوم بالكود (مش محتاج صورة)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(pos=self._draw, size=self._draw, state=self._draw)
        self.bind(on_release=lambda *_: SOUND.play("tap"))
        self._draw()

    def _draw(self, *a):
        self.canvas.clear()
        x, y = self.pos
        w, h = self.size
        with self.canvas:
            Color(*(CARD2 if self.state == "normal" else (0.08, 0.10, 0.17, 1)))
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(14)])
            Color(*BORDER)
            Line(rounded_rectangle=(x, y, w, h, max(dp(1), min(dp(14), h / 2.0, w / 2.0))),
                 width=dp(1))
            Color(*TXT)
            self.icon(x + w / 2.0, y + h / 2.0, min(w, h))

    def icon(self, cx, cy, size):
        pass


BRAND_ORANGE = (1.0, 0.35, 0.10)        # برتقالي الشعار
SPLASH_BG = (2 / 255.0, 12 / 255.0, 44 / 255.0, 1)   # نفس لون presplash.png
_LOGO_TEX = {}


def asset_path(*parts):
    try:
        base = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        base = os.getcwd()
    return os.path.join(base, *parts)


def _load_tex(key, *parts):
    """بيحمّل صورة من مجلد البرنامج مرة واحدة (None لو مش موجودة، والتطبيق بيكمل عادي)."""
    if key in _LOGO_TEX:
        return _LOGO_TEX[key]
    tex = None
    try:
        p = asset_path(*parts)
        if os.path.isfile(p):
            from kivy.core.image import Image as CoreImage
            tex = CoreImage(p).texture
            tex.mag_filter = "linear"
            tex.min_filter = "linear"
    except Exception as e:
        nlog("logo load %s: %s" % (key, e))
    _LOGO_TEX[key] = tex
    return tex


def logo_texture():
    return _load_tex("logo", "assets", "logo.png")


def brand_markup():
    """اسم البرنامج بألوان الشعار: Nova برتقالي + Get بلون النص (أبيض في الداكن / كحلي في الفاتح)."""
    g = "#%02X%02X%02X" % tuple(int(max(0, min(1, v)) * 255) for v in TXT[:3])
    return "[color=#FF5A1F]Nova[/color][color=%s]Get[/color]" % g


class LogoMark(Widget):
    """شعار NovaGet: صورة الشعار (assets/logo.png)؛ ولو مش موجودة بيرسم الشعار القديم."""

    def __init__(self, glow=True, **kw):
        super().__init__(**kw)
        self.glow = glow
        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    @staticmethod
    def _star(cx, cy, r):
        k = r * .26
        Quad(points=[cx, cy + r, cx + k, cy, cx, cy - r, cx - k, cy])
        Quad(points=[cx - r, cy, cx, cy + k, cx + r, cy, cx, cy - k])

    def _draw(self, *a):
        c = self.canvas
        c.clear()
        s = min(self.width, self.height)
        if s < 4:
            return
        cx, cy = self.center_x, self.center_y
        tex = logo_texture()
        if tex is not None:          # شعار NovaGet الحقيقي (assets/logo.png) كأيقونة بحواف مدوّرة
            with c:
                if self.glow:
                    Color(1, 1, 1, min(1.0, GLOW_A * 1.1))
                    g = s * 2.3
                    Rectangle(texture=make_glow(BRAND_ORANGE), size=(g, g),
                              pos=(cx - g / 2.0, cy - g / 2.0))
                Color(0, 0, 0, .35)
                RoundedRectangle(pos=(cx - s / 2.0, cy - s / 2.0 - dp(3)), size=(s, s),
                                 radius=[s * .24])
                Color(1, 1, 1, 1)
                RoundedRectangle(pos=(cx - s / 2.0, cy - s / 2.0), size=(s, s), radius=[s * .24],
                                 texture=tex)
            return
        with c:
            if self.glow:
                Color(1, 1, 1, min(1.0, GLOW_A * 1.5))
                g = s * 2.5
                Rectangle(texture=make_glow(ACC1), size=(g, g), pos=(cx - g / 2.0, cy - g / 2.0))
                Color(1, 1, 1, min(1.0, GLOW_A * 1.0))
                g = s * 2.0
                Rectangle(texture=make_glow(ACC2), size=(g, g),
                          pos=(cx - g / 2.0 + s * .22, cy - g / 2.0 - s * .10))
            Color(0, 0, 0, .35)
            RoundedRectangle(pos=(cx - s / 2.0, cy - s / 2.0 - dp(3)), size=(s, s), radius=[s * .30])
            Color(1, 1, 1, 1)
            RoundedRectangle(pos=(cx - s / 2.0, cy - s / 2.0), size=(s, s), radius=[s * .30],
                             texture=make_grad(*GRAD))
            Color(1, 1, 1, .16)
            RoundedRectangle(pos=(cx - s / 2.0 + dp(1), cy), size=(s - dp(2), s * .49),
                             radius=[s * .29, s * .29, s * .10, s * .10])
            Color(1, 1, 1, 1)
            wd = max(dp(2.2), s * .06)
            Line(points=[cx, cy + s * .20, cx, cy - s * .15], width=wd, cap="round")
            Line(points=[cx - s * .17, cy - s * .02, cx, cy - s * .19, cx + s * .17, cy - s * .02],
                 width=wd, cap="round", joint="round")
            Line(points=[cx - s * .22, cy - s * .29, cx + s * .22, cy - s * .29],
                 width=wd, cap="round")
            Color(1, 1, 1, .96)
            self._star(cx + s * .27, cy + s * .27, s * .15)
            Color(1, 1, 1, .7)
            self._star(cx - s * .30, cy + s * .24, s * .06)


class LinkIcon(Widget):
    """أيقونة رابط (حلقتين متداخلتين) لحقل الإدخال."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    def _draw(self, *a):
        self.canvas.clear()
        s = min(self.width, self.height)
        if s < 4:
            return
        cx, cy = self.center_x, self.center_y
        with self.canvas:
            PushMatrix()
            Rotate(angle=-45, origin=(cx, cy))
            Color(*ON)
            wd = dp(2.0)
            Line(rounded_rectangle=(cx - s * .46, cy - s * .17, s * .52, s * .34, s * .17), width=wd)
            Line(rounded_rectangle=(cx - s * .06, cy - s * .17, s * .52, s * .34, s * .17), width=wd)
            PopMatrix()


class PlatformRow(Widget):
    """صف مربعات صغيرة بأسماء المنصات المدعومة."""
    TILES = (("YT", (.90, .12, .12)), ("TT", (.08, .80, .82)), ("IG", (.80, .22, .50)),
             ("f", (.09, .47, .95)), ("X", (.60, .60, .68)), ("+1000", (.42, .36, .91)))

    def __init__(self, **kw):
        super().__init__(**kw)
        self._tex = {}
        self.bind(pos=self._draw, size=self._draw)

    def _tx(self, txt):
        if txt not in self._tex:
            try:
                cl = CoreLabel(text=txt, font_size=dp(12), bold=True)
                cl.refresh()
                self._tex[txt] = cl.texture
            except Exception:
                self._tex[txt] = None
        return self._tex[txt]

    def _draw(self, *a):
        self.canvas.clear()
        if self.width < 10:
            return
        n = len(self.TILES)
        t_h = min(dp(32), self.height)
        gap = dp(8)
        widths = [dp(52) if tx == "+1000" else t_h for tx, _ in self.TILES]
        total = sum(widths) + gap * (n - 1)
        x = self.center_x - total / 2.0
        y = self.center_y - t_h / 2.0
        with self.canvas:
            for (txt, col), wd in zip(self.TILES, widths):
                Color(col[0], col[1], col[2], .22)
                RoundedRectangle(pos=(x, y), size=(wd, t_h), radius=[dp(10)])
                Color(col[0], col[1], col[2], .45)
                Line(rounded_rectangle=(x, y, wd, t_h, dp(10)), width=dp(1))
                tex = self._tx(txt)
                if tex is not None:
                    Color(*TXT[:3], .85)
                    Rectangle(texture=tex, size=tex.size,
                              pos=(x + (wd - tex.width) / 2.0, y + (t_h - tex.height) / 2.0))
                x += wd + gap


class EmptyState(FloatLayout):
    """حالة فاضية شيك: كرة متدرّجة بسهم + نص + (اختياري) المنصات المدعومة."""

    def __init__(self, text="", platforms=False, **kw):
        super().__init__(**kw)
        self.platforms = platforms
        self._lbl = TLabel(text=text, halign="center", valign="top", shorten=False,
                           color=DIM, font_size=dp(14), size_hint=(None, None))
        self.add_widget(self._lbl)
        self._pr = None
        if platforms:
            self._pr = PlatformRow(size_hint=(None, None))
            self.add_widget(self._pr)
        self.bind(pos=self._layout, size=self._layout)
        self._layout()

    def _layout(self, *a):
        x, y = self.pos
        w, h = self.size
        if w < 10 or h < 10:
            return
        orb = dp(78)
        top = y + h - dp(14)
        ocx, ocy = x + w / 2.0, top - orb / 2.0
        self.canvas.before.clear()
        with self.canvas.before:
            Color(1, 1, 1, min(1.0, GLOW_A * 1.4))
            g = orb * 2.6
            Rectangle(texture=make_glow(ACC1), size=(g, g), pos=(ocx - g / 2.0, ocy - g / 2.0))
            Color(*ACC1, .30)
            Line(circle=(ocx, ocy, orb * .62), width=dp(1.2))
            Color(*ACC2, .16)
            Line(circle=(ocx, ocy, orb * .80), width=dp(1.0))
            Color(1, 1, 1, 1)
            d = orb * .82
            Ellipse(texture=make_grad(*GRAD), size=(d, d), pos=(ocx - d / 2.0, ocy - d / 2.0))
            Color(1, 1, 1, 1)
            wd = dp(3)
            s = d
            Line(points=[ocx, ocy + s * .20, ocx, ocy - s * .13], width=wd, cap="round")
            Line(points=[ocx - s * .16, ocy - s * .01, ocx, ocy - s * .17, ocx + s * .16, ocy - s * .01],
                 width=wd, cap="round", joint="round")
            Line(points=[ocx - s * .20, ocy - s * .27, ocx + s * .20, ocy - s * .27],
                 width=wd, cap="round")
        lw = min(w - dp(24), dp(320))
        self._lbl.size = (lw, dp(48))
        self._lbl.pos = (ocx - lw / 2.0, top - orb - dp(14) - dp(48))
        if self._pr is not None:
            self._pr.size = (w, dp(34))
            self._pr.pos = (x, self._lbl.y - dp(16) - dp(34))


class Splash(FloatLayout):
    """شاشة افتتاحية قصيرة بالشعار، بتختفي لوحدها."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(pos=self._sync, size=self._sync)
        # نفس صورة presplash.png (اللي أندرويد بيعرضها قبل ما بايثون يشتغل) عشان الانتقال يبقى سلس
        self._pic = _load_tex("splash", "presplash.png")
        self.logo = LogoMark(size_hint=(None, None), size=(dp(70), dp(70)),
                             pos_hint={"center_x": .5, "center_y": .58}, opacity=0)
        self.brand = TLabel(text=("[color=#FF5A1F]Nova[/color][color=#F4F6FF]Get[/color]"
                                  if self._pic is not None else
                                  "[color=#6C5CE7]Nova[/color][color=#00B8FF]Get[/color]"),
                            markup=True, font_size=dp(38), bold=True, shorten=False,
                            halign="center", size_hint=(1, None), height=dp(56),
                            pos_hint={"center_x": .5, "center_y": .43}, opacity=0)
        self.sub = TLabel(text=t("tagline"), font_size=dp(13), color=DIM, halign="center",
                          size_hint=(1, None), height=dp(22),
                          pos_hint={"center_x": .5, "center_y": .37}, opacity=0)
        self.bar = SlimProgress(size_hint=(None, None), size=(dp(110), dp(4)),
                                pos_hint={"center_x": .5, "center_y": .27}, opacity=0)
        if self._pic is not None:
            self.sub.color = (0.66, 0.72, 0.88, 1)
            self.bar.track = [1, 1, 1, .10]
            for w in (self.brand, self.sub, self.bar):
                self.add_widget(w)
        else:
            for w in (self.logo, self.brand, self.sub, self.bar):
                self.add_widget(w)
        self._sync()

    def _sync(self, *a):
        W = float(self.width)
        if W < 10:
            return
        self.canvas.before.clear()
        if self._pic is not None:
            H = float(self.height)
            side = min(W, H)
            with self.canvas.before:
                Color(*SPLASH_BG)
                Rectangle(pos=self.pos, size=self.size)
                Color(1, 1, 1, 1)
                Rectangle(texture=self._pic, size=(side, side),
                          pos=(self.x + (W - side) / 2.0, self.y + (H - side) / 2.0))
            base = H / 2.0 - side * .25         # أسفل الشعار
            for wd, off in ((self.brand, 46), (self.sub, 82), (self.bar, 112)):
                wd.pos_hint = {"center_x": .5, "center_y": (base - dp(off)) / H}
            return
        with self.canvas.before:
            Color(*BG)
            Rectangle(pos=self.pos, size=self.size)
            Color(1, 1, 1, GLOW_A * 1.2)
            s = W * 1.6
            Rectangle(texture=make_glow(ACC1), size=(s, s),
                      pos=(self.x + W * .15 - s / 2.0, self.top - self.height * .30 - s / 2.0))
            Color(1, 1, 1, GLOW_A)
            s = W * 1.3
            Rectangle(texture=make_glow(ACC2), size=(s, s),
                      pos=(self.x + W * .90 - s / 2.0, self.y + self.height * .32 - s / 2.0))

    def play(self, on_done):
        Animation(opacity=1, d=.45, t="out_quad").start(self.logo)
        Animation(size=(dp(96), dp(96)), d=.7, t="out_back").start(self.logo)
        Animation(opacity=1, d=.5, t="out_quad").start(self.brand)
        Animation(opacity=1, d=.5, t="out_quad").start(self.sub)
        Animation(opacity=1, d=.3).start(self.bar)
        Animation(value=100, d=1.15, t="in_out_quad").start(self.bar)
        fade = Animation(opacity=1, d=1.25) + Animation(opacity=0, d=.40, t="in_quad")
        fade.bind(on_complete=lambda *_: on_done())
        fade.start(self)


class GearButton(IconButton):
    def icon(self, cx, cy, size):
        r = size * 0.20
        Line(circle=(cx, cy, r * 1.25), width=dp(2.4))
        Line(circle=(cx, cy, r * 0.55), width=dp(2.0))
        for i in range(8):
            a = i * math.pi / 4
            Line(points=[cx + math.cos(a) * r * 1.25, cy + math.sin(a) * r * 1.25,
                         cx + math.cos(a) * r * 1.75, cy + math.sin(a) * r * 1.75],
                 width=dp(3))


class DownloadsButton(IconButton):
    """أيقونة سهم تحميل + نقطة ملونة لو فيه تحميلات شغالة."""
    active = NumericProperty(0)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(active=self._draw)

    def icon(self, cx, cy, size):
        u = size * 0.22
        wd = dp(2.4)
        Line(points=[cx, cy + u * 1.5, cx, cy - u * 0.7], width=wd)
        Line(points=[cx - u * 0.9, cy + u * 0.2, cx, cy - u * 0.7, cx + u * 0.9, cy + u * 0.2], width=wd)
        Line(points=[cx - u * 1.4, cy - u * 0.5, cx - u * 1.4, cy - u * 1.4,
                     cx + u * 1.4, cy - u * 1.4, cx + u * 1.4, cy - u * 0.5], width=wd)
        if self.active > 0:
            Color(*ACC2)
            Ellipse(pos=(cx + size * 0.17, cy + size * 0.17), size=(size * 0.28, size * 0.28))

def _plog(msg):
    """سجل خطوات المشغّل (بيتكتب ويتفلّش فورًا، حتى لو البرنامج قفل بعدها)."""
    try:
        d = os.path.dirname(settings_path())
        with open(os.path.join(d, "novaget_player_log.txt"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass


def _remove_android_view(layout):
    """بيشيل الـ View من الشاشة (لازم cast لـ ViewGroup، لأن getParent بيرجّع ViewParent وملوش removeView)."""
    try:
        layout.setVisibility(8)
    except Exception as ex:
        _plog("hide error: %s" % ex)
    try:
        from jnius import cast
        parent = layout.getParent()
        if parent is not None:
            cast("android.view.ViewGroup", parent).removeView(layout)
    except Exception as ex:
        _plog("remove error: %s" % ex)


class NSlider(Slider):
    """Slider بيمسك اللمسة لنفسه: ScrollView اللي حواليه مابيسرقهاش، فتقدر تسحب يمين وشمال براحتك."""

    def _sv_parent(self):
        w = self.parent
        while w is not None:
            if isinstance(w, ScrollView):
                return w
            w = w.parent
        return None

    def on_touch_down(self, touch):
        if self.disabled or not self.collide_point(*touch.pos):
            return False
        sv = self._sv_parent()
        if sv is not None:
            touch.ud["ns_sv"] = (sv, sv.do_scroll_x, sv.do_scroll_y)
            sv.do_scroll_x = False
            sv.do_scroll_y = False
        touch.grab(self)
        touch.ud["ns_drag"] = True
        self.value_pos = touch.pos
        return True

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            self.value_pos = touch.pos
            return True
        return False

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            st = touch.ud.pop("ns_sv", None)
            if st:
                st[0].do_scroll_x, st[0].do_scroll_y = st[1], st[2]
            return True
        return False


class NScroll(ScrollView):
    """ScrollView بيدّي اللمسة للسلايدر فورًا (من غير ما يستنى ويقرر هل ده سحب للقايمة)،
    فالسلايدر يتسحب يمين وشمال براحته."""

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            for w in self.walk(restrict=True):
                if isinstance(w, NSlider) and w.collide_point(*touch.pos):
                    return w.on_touch_down(touch)
        return super().on_touch_down(touch)


class PlayerPanel(BoxLayout):
    """لوحة المشغّل: بتمتص كل اللمسات عشان ما توصلش للقايمة اللي تحتها."""

    def on_touch_down(self, touch):
        super().on_touch_down(touch)
        return True

    def on_touch_move(self, touch):
        super().on_touch_move(touch)
        return True

    def on_touch_up(self, touch):
        super().on_touch_up(touch)
        return True


class _ClientGone(Exception):
    """المشغّل قفل الاتصال (سحب على نقطة تانية في الفيديو مثلًا)."""


class _RangeProxy:
    """سيرفر محلي (127.0.0.1) بيجيب الفيديو من يوتيوب على أجزاء (Range) ويقدّمه للمشغّل.
    ده بيتفادى بطء/فشل تشغيل لينكات googlevideo مباشرةً من MediaPlayer."""
    CHUNK = 2 * 1024 * 1024

    def __init__(self):
        self.routes = {}
        self.httpd = None
        self.port = 0

    def start(self):
        if self.httpd is not None:
            return
        import http.server
        import socketserver
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_HEAD(self):
                self._serve(True)

            def do_GET(self):
                self._serve(False)

            def _serve(self, head):
                r = outer.routes.get(self.path.split("?")[0].strip("/"))
                if not r:
                    self.send_error(404)
                    return
                try:
                    size, ctype = outer._probe(r)
                    start, end, code = 0, size - 1, 200
                    rng = self.headers.get("Range")
                    if rng and rng.startswith("bytes="):
                        a, b = rng[6:].split(",")[0].split("-", 1)
                        if a == "":
                            start = max(0, size - int(b))
                        else:
                            start = int(a)
                            if b:
                                end = min(int(b), size - 1)
                        code = 206
                    if start >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", "bytes */%d" % size)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send_response(code)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Length", str(end - start + 1))
                    if code == 206:
                        self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
                    self.end_headers()
                    if head:
                        return
                    def _w(data):
                        try:
                            self.wfile.write(data)
                        except Exception as e:
                            raise _ClientGone() from e
                    outer._stream(r, start, end, _w)
                except (_ClientGone, BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
                except Exception as ex:
                    _plog("proxy error: %s" % ex)
                    try:
                        self.close_connection = True
                    except Exception:
                        pass

        class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.httpd = S(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def add(self, url, headers):
        import uuid
        rid = uuid.uuid4().hex[:12]
        hd = {}
        for k, v in (headers or {}).items():
            if isinstance(k, str) and isinstance(v, str) and k.lower() not in (
                    "range", "accept-encoding", "host", "connection"):
                hd[k] = v
        hd["Accept-Encoding"] = "identity"
        self.routes[rid] = {"url": url, "headers": hd, "size": None, "ctype": None}
        return "http://127.0.0.1:%d/%s" % (self.port, rid)

    def clear(self):
        self.routes.clear()

    def _open(self, r, a, b):
        import urllib.request
        hd = dict(r["headers"])
        hd["Range"] = "bytes=%d-%d" % (a, b)
        return urllib.request.urlopen(urllib.request.Request(r["url"], headers=hd), timeout=30)

    def _probe(self, r):
        if r["size"]:
            return r["size"], r["ctype"]
        last = None
        for _ in range(3):
            try:
                resp = self._open(r, 0, 0)
                cr = resp.headers.get("Content-Range") or ""
                ctype = resp.headers.get("Content-Type") or "video/mp4"
                resp.read()
                resp.close()
                r["size"] = int(cr.split("/")[-1])
                r["ctype"] = ctype
                return r["size"], r["ctype"]
            except Exception as ex:
                last = ex
                time.sleep(0.4)
        raise RuntimeError("probe failed: %s" % last)

    def _stream(self, r, a, b, write):
        """بيبعت البايتات للمشغّل أول بأول (من غير ما يستنى الجزء كله يتحمّل).
        أول جزء صغير عشان التشغيل يبدأ بسرعة بعد السحب، وبعدها الأجزاء بتكبر لحد CHUNK."""
        pos = a
        size = 256 * 1024
        while pos <= b:
            ce = min(b, pos + size - 1)
            tries = 0
            while pos <= ce:
                try:
                    resp = self._open(r, pos, ce)
                    try:
                        while pos <= ce:
                            data = resp.read(min(64 * 1024, ce - pos + 1))
                            if not data:
                                raise RuntimeError("short read")
                            write(data)
                            pos += len(data)
                    finally:
                        resp.close()
                except _ClientGone:
                    raise
                except Exception:
                    tries += 1
                    if tries > 3:
                        raise
                    time.sleep(0.3)
            size = min(self.CHUNK, size * 2)

    def _fetch(self, r, a, b):
        last = None
        for _ in range(3):
            try:
                resp = self._open(r, a, b)
                data = resp.read()
                resp.close()
                if len(data) == b - a + 1:
                    return data
                last = RuntimeError("short read %d/%d" % (len(data), b - a + 1))
            except Exception as ex:
                last = ex
            time.sleep(0.3)
        raise last


_STREAM_PROXY = None


def _stream_proxy():
    """بيرجّع السيرفر المحلي لو أندرويد بيسمح بـ HTTP على 127.0.0.1، وإلا None."""
    global _STREAM_PROXY
    try:
        from jnius import autoclass
        pol = autoclass("android.security.NetworkSecurityPolicy").getInstance()
        try:
            ok = bool(pol.isCleartextTrafficPermitted("127.0.0.1"))
        except Exception:
            ok = bool(pol.isCleartextTrafficPermitted())
        _plog("cleartext permitted: %s" % ok)
        if not ok:
            return None
    except Exception as ex:
        _plog("cleartext check: %s" % ex)
    try:
        if _STREAM_PROXY is None:
            px = _RangeProxy()
            px.start()
            _STREAM_PROXY = px
        return _STREAM_PROXY
    except Exception as ex:
        _plog("proxy start failed: %s" % ex)
        return None



_PL_ISOLATE_JS = r"""(function(){
try{
var v=document.querySelector('video'); if(!v) return 'nov';
try{ if(v.muted) v.muted=false; if(v.volume<1) v.volume=1;
  var um=document.querySelector('.ytp-unmute'); if(um) um.click(); }catch(x){}
var pl=v.closest('.html5-video-player')||v.parentElement; if(!pl) return 'nopl';
var el=pl;
while(el && el!==document.body){
  var par=el.parentElement; if(!par) break;
  for(var c=par.firstElementChild;c;c=c.nextElementSibling){
    if(c!==el && c.tagName!=='SCRIPT' && c.tagName!=='STYLE' && c.tagName!=='LINK')
      c.style.setProperty('display','none','important');
  }
  el=par;
}
document.documentElement.style.setProperty('overflow','hidden','important');
document.documentElement.style.setProperty('background','#000','important');
document.body.style.setProperty('overflow','hidden','important');
document.body.style.setProperty('background','#000','important');
el=pl;
while(el && el!==document.documentElement){
  var S=el.style;
  S.setProperty('width','100vw','important'); S.setProperty('max-width','100vw','important');
  S.setProperty('height','100vh','important'); S.setProperty('max-height','100vh','important');
  S.setProperty('margin','0','important'); S.setProperty('padding','0','important');
  S.setProperty('top','0','important'); S.setProperty('left','0','important');
  S.setProperty('transform','none','important');
  el=el.parentElement;
}
if(!document.getElementById('ng_css')){var st=document.createElement('style');st.id='ng_css';
st.textContent='.ytp-chrome-top,.ytp-chrome-bottom,.ytp-gradient-top,.ytp-gradient-bottom,.ytp-large-play-button,.ytp-pause-overlay,.ytp-ce-element,.ytp-cards-teaser,.ytp-endscreen-content,.ytp-watermark,.ytp-spinner,.ytp-unmute{display:none!important}'+'.ytp-caption-window-container{left:0!important;right:0!important;width:100%!important;overflow:visible!important}'+'.caption-window{left:5%!important;right:5%!important;width:90%!important;max-width:90%!important;margin-left:0!important;margin-right:0!important;bottom:10%!important;text-align:center!important}'+'.captions-text{white-space:normal!important;text-align:center!important;width:100%!important}'+'.ytp-caption-segment{white-space:normal!important;font-size:clamp(13px,4vh,34px)!important;line-height:1.35!important}';
document.head.appendChild(st);}
window.scrollTo(0,0);
return 'ok';
}catch(e){return 'err '+e;}
})();"""

_PL_SKIP_SEL = ("['.ytp-skip-ad-button','.ytp-ad-skip-button','.ytp-ad-skip-button-modern',"
                "'.ytp-ad-skip-button-container button','.ytp-ad-skip-button-slot button',"
                "'.ytp-ad-skip-button-container']")
_PL_STATE_JS = ("(function(){var v=document.querySelector('video');if(!v)return '';"
                "var pl=v.closest('.html5-video-player');"
                "var ad=(pl&&pl.classList.contains('ad-showing'))?1:0;var sk=0;"
                "if(ad){var sel=" + _PL_SKIP_SEL + ";for(var i=0;i<sel.length;i++){"
                "var b=document.querySelector(sel[i]);if(b&&b.offsetWidth>0&&b.offsetHeight>0){sk=1;break;}}}"
                "return v.currentTime+'|'+(isFinite(v.duration)?v.duration:0)+'|'+(v.paused?0:1)+'|'+ad+'|'+sk;})()")
_PL_SKIP_RECT_JS = ("(function(){var sel=" + _PL_SKIP_SEL + ";var d=window.devicePixelRatio||1;"
                    "for(var i=0;i<sel.length;i++){var b=document.querySelector(sel[i]);"
                    "if(b&&b.offsetWidth>0&&b.offsetHeight>0){var r=b.getBoundingClientRect();"
                    "return ((r.left+r.width/2)*d)+'|'+((r.top+r.height/2)*d);}}return 'none';})()")
_PL_SKIP_JS = ("(function(){var sel=" + _PL_SKIP_SEL + ";"
               "for(var i=0;i<sel.length;i++){var b=document.querySelector(sel[i]);"
               "if(b&&b.offsetWidth>0){['pointerdown','mousedown','touchstart','pointerup','mouseup','touchend','click']"
               ".forEach(function(t){try{b.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));}catch(e){}});"
               "try{b.click();}catch(e){}return 'ok';}}return 'none';})()")

_PL_Q_NAMES = {"highres": "4320p", "hd2880": "2880p", "hd2160": "2160p", "hd1440": "1440p",
               "hd1080": "1080p", "hd720": "720p", "large": "480p", "medium": "360p",
               "small": "240p", "tiny": "144p"}
_PL_LANGS = [("ar", "العربية"), ("en", "English"), ("fr", "French"), ("es", "Spanish"),
             ("de", "German"), ("tr", "Turkish"), ("ru", "Russian"), ("hi", "Hindi"),
             ("id", "Indonesian"), ("pt", "Portuguese"), ("it", "Italian"), ("ja", "Japanese"),
             ("ko", "Korean"), ("zh-Hans", "Chinese")]
_PL_HEAD = ("var v=document.querySelector('video');if(!v)return '{}';"
            "var pl=v.closest('.html5-video-player');if(!pl)return '{}';")
_PL_Q_LIST_JS = ("(function(){" + _PL_HEAD + "var o={q:[],cur:''};"
                 "try{o.q=pl.getAvailableQualityLevels()||[];o.cur=pl.getPlaybackQuality()||'';}catch(e){}"
                 "return JSON.stringify(o);})()")
_PL_CAP_LOAD_JS = ("(function(){" + _PL_HEAD + "try{pl.loadModule('captions');}catch(e){}return '{}';})()")
_PL_TL_FN = ("function getTL(){var tl=[];try{tl=pl.getOption('captions','tracklist')||[];}catch(e){}"
             "if(tl.length)return {tl:tl,tr:null};"
             "try{var pr=(pl.getPlayerResponse?pl.getPlayerResponse():null)||window.ytInitialPlayerResponse;"
             "var r=pr&&pr.captions&&pr.captions.playerCaptionsTracklistRenderer;"
             "if(r&&r.captionTracks){return {tl:r.captionTracks.map(function(c){"
             "var n=c.name&&(c.name.simpleText||(c.name.runs&&c.name.runs[0]&&c.name.runs[0].text));"
             "return {languageCode:c.languageCode,languageName:n||c.languageCode,kind:c.kind||''};}),"
             "tr:(r.translationLanguages||[]).map(function(t){return {languageCode:t.languageCode};})};}}"
             "catch(e){}return {tl:[],tr:null};}")
_PL_CAP_LIST_JS = ("(function(){" + _PL_HEAD + _PL_TL_FN + "var o={tracks:[],tr:[],cur:''};"
                   "try{pl.loadModule('captions');}catch(e){}"
                   "var g=getTL();"
                   "o.tracks=g.tl.map(function(t){var n=t.languageName||t.displayName||t.languageCode;"
                   "if(n&&typeof n==='object')n=n.simpleText||(n.runs&&n.runs[0]&&n.runs[0].text)||t.languageCode;"
                   "return {lc:t.languageCode,name:String(n),kind:t.kind||''};});"
                   "try{var tr=pl.getOption('captions','translationLanguages')||[];"
                   "if(!tr.length&&g.tr)tr=g.tr;o.tr=tr.map(function(t){return {lc:t.languageCode};});}catch(e){}"
                   "try{var c=pl.getOption('captions','track');if(c&&c.languageCode)o.cur=c.languageCode;}catch(e){}"
                   "return JSON.stringify(o);})()")
_VCB_KEEP = []


def _make_value_callback(f):
    from jnius import PythonJavaClass, java_method

    class _VCB(PythonJavaClass):
        __javainterfaces__ = ["android/webkit/ValueCallback"]
        __javacontext__ = "app"

        def __init__(self, fn):
            super().__init__()
            self.fn = fn

        @java_method("(Ljava/lang/Object;)V")
        def onReceiveValue(self, v):
            try:
                self.fn(str(v))
            except Exception as exc:
                _plog("vcb: %s" % str(exc)[:100])
    o = _VCB(f)
    _VCB_KEEP.append(o)
    del _VCB_KEEP[:-60]
    return o


def _js_json(sv):
    try:
        x = json.loads(sv)
        if isinstance(x, str):
            x = json.loads(x)
        return x
    except Exception:
        return None


_MAIN_KEEP = []
_WV_PREPARED = [False]


def _webview_prepare():
    """Pydroid بيشغّل Kivy في process تانية، والـ WebView ممنوع يشارك نفس مجلد البيانات
    مع process Pydroid الأساسية. بنديه مجلد بيانات خاص بينا (لازم قبل أول استخدام)."""
    if _WV_PREPARED[0]:
        return
    _WV_PREPARED[0] = True
    try:
        from jnius import autoclass
        WV = autoclass("android.webkit.WebView")
        _main_ui_decorator()(lambda: WV.setDataDirectorySuffix("novaget"))()
        _plog("webview data dir suffix set")
    except Exception as ex:
        _plog("webview suffix: %s" % str(ex)[:150])


def _main_ui_decorator():
    """بديل run_on_ui_thread: بيبعت الدالة صراحةً للـ main Looper بتاع أندرويد (مضمون حتى في Pydroid)."""
    from jnius import autoclass, PythonJavaClass, java_method
    Looper = autoclass("android.os.Looper")
    Handler = autoclass("android.os.Handler")

    class _Run(PythonJavaClass):
        __javainterfaces__ = ["java/lang/Runnable"]
        __javacontext__ = "app"

        def __init__(self, f):
            super().__init__()
            self.f = f

        @java_method("()V")
        def run(self):
            try:
                self.f()
            finally:
                try:
                    _MAIN_KEEP.remove(self)
                except Exception:
                    pass

    def deco(fn):
        def wrapper(*a, **k):
            r = _Run(lambda: fn(*a, **k))
            _MAIN_KEEP.append(r)
            Handler(Looper.getMainLooper()).post(r)
        return wrapper
    return deco


def _on_main_thread():
    try:
        from jnius import autoclass
        L = autoclass("android.os.Looper")
        return bool(L.myLooper() is not None and L.myLooper().equals(L.getMainLooper()))
    except Exception:
        return None


def open_url(url):
    """يفتح رابط في متصفح الهاتف."""
    try:
        from jnius import autoclass
        pa = None
        for name in ("org.kivy.android.PythonActivity", "org.renpy.android.PythonActivity"):
            try:
                pa = autoclass(name)
                break
            except Exception:
                continue
        if pa is not None and pa.mActivity is not None:
            Intent = autoclass("android.content.Intent")
            Uri = autoclass("android.net.Uri")
            intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            pa.mActivity.startActivity(intent)
            return True
    except Exception:
        pass
    try:
        import webbrowser
        return bool(webbrowser.open(url))
    except Exception:
        return False


WA_DIRS = []
for _root in ("/storage/emulated/0", "/sdcard"):
    for _pkg, _nm in (("com.whatsapp", "WhatsApp"), ("com.whatsapp.w4b", "WhatsApp Business"),
                      ("com.gbwhatsapp", "GBWhatsApp"), ("com.fmwhatsapp", "FMWhatsApp"),
                      ("com.yowhatsapp", "YoWhatsApp")):
        WA_DIRS += [
            "%s/Android/media/%s/%s/Media/.Statuses" % (_root, _pkg, _nm),
            "%s/Android/media/%s/%s/Media/Statuses" % (_root, _pkg, _nm),
        ]
    WA_DIRS += [_root + "/WhatsApp/Media/.Statuses", _root + "/WhatsApp Business/Media/.Statuses",
                _root + "/GBWhatsApp/Media/.Statuses", _root + "/FMWhatsApp/Media/.Statuses"]
WA_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".3gp", ".mkv", ".mov", ".avi")
WA_FOUND = []     # (مجلد، عدد الملفات) للتشخيص


def wa_status_files():
    """حالات واتساب المتاحة (الأحدث الأول) + هل قدرنا نفتح مجلد الحالات أصلًا."""
    out, opened, seen, dirs = [], False, set(), set()
    del WA_FOUND[:]
    for d in WA_DIRS:
        try:
            rd = os.path.realpath(d)
            if rd in dirs or not os.path.isdir(d):
                continue
            names = os.listdir(d)
        except Exception:
            continue
        dirs.add(rd)
        opened = True
        cnt = 0
        for n in names:
            fp = os.path.join(d, n)
            if n.lower() == ".nomedia" or os.path.isdir(fp):
                continue
            rp = os.path.realpath(fp)
            if rp in seen:
                continue
            try:
                out.append((os.path.getmtime(fp), fp, os.path.getsize(fp)))
                seen.add(rp)
                cnt += 1
            except Exception:
                pass
        WA_FOUND.append((d, cnt, len(names)))
    out.sort(reverse=True)
    return out, opened


def video_thumb(path):
    """صورة مصغّرة من أول لقطة في الفيديو (أندرويد MediaMetadataRetriever، وبعدين ffmpeg، وبعدين PyAV)."""
    import hashlib
    try:
        d = os.path.join(tempfile.gettempdir(), "novaget_vthumbs")
        os.makedirs(d, exist_ok=True)
        key = hashlib.md5(("%s|%s" % (path, os.path.getmtime(path))).encode("utf-8")).hexdigest()
        out = os.path.join(d, key + ".jpg")
    except Exception:
        return None
    if os.path.isfile(out) and os.path.getsize(out) > 200:
        return out
    # 1) أندرويد نفسه
    try:
        from jnius import autoclass
        retr = autoclass("android.media.MediaMetadataRetriever")()
        try:
            retr.setDataSource(path)
            bmp = retr.getFrameAtTime(500000)        # عند 0.5 ثانية
            if bmp is None:
                bmp = retr.getFrameAtTime(0)
            if bmp is not None:
                w, h = int(bmp.getWidth()), int(bmp.getHeight())
                if max(w, h) > 320:
                    k = 320.0 / max(w, h)
                    bmp = autoclass("android.graphics.Bitmap").createScaledBitmap(
                        bmp, max(1, int(w * k)), max(1, int(h * k)), True)
                fos = autoclass("java.io.FileOutputStream")(out)
                fmt = autoclass("android.graphics.Bitmap$CompressFormat").JPEG
                bmp.compress(fmt, 80, fos)
                fos.close()
        finally:
            try:
                retr.release()
            except Exception:
                pass
        if os.path.isfile(out) and os.path.getsize(out) > 200:
            return out
    except Exception:
        pass
    # 2) ffmpeg
    if HAS_FFMPEG:
        try:
            import subprocess
            subprocess.run([FFMPEG, "-y", "-ss", "0.5", "-i", path, "-frames:v", "1",
                            "-vf", "scale=320:-2", out],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            if os.path.isfile(out) and os.path.getsize(out) > 200:
                return out
        except Exception:
            pass
    # 3) PyAV
    if HAS_AV:
        try:
            c = pyav.open(path)
            try:
                for frame in c.decode(video=0):
                    im = frame.to_image()
                    im.thumbnail((320, 320))
                    im.convert("RGB").save(out, "JPEG", quality=80)
                    break
            finally:
                c.close()
            if os.path.isfile(out) and os.path.getsize(out) > 200:
                return out
        except Exception:
            pass
    return None


def probe_video_url(url, ref=None, cookie=None):
    """حجم + دقة + مدة + لقطة لفيديو على رابط مباشر، من غير ما نحمّله كله."""
    import hashlib
    res = {"size": 0, "w": 0, "h": 0, "dur": 0, "thumb": ""}
    hd = {"User-Agent": _UA_MOBILE, "Accept": "*/*"}
    if ref:
        hd["Referer"] = ref
    if cookie:
        hd["Cookie"] = cookie
    if not re.search(r"\.(m3u8|mpd)(\?|#|$)", url, re.I):
        try:
            import urllib.request
            rq = urllib.request.Request(url, headers=dict(hd, Range="bytes=0-0"))
            with urllib.request.urlopen(rq, timeout=15) as r:
                cr = r.headers.get("Content-Range") or ""
                tail = cr.rsplit("/", 1)[-1] if "/" in cr else ""
                res["size"] = int(tail) if tail.isdigit() else int(r.headers.get("Content-Length") or 0)
        except Exception:
            pass
    try:
        d = os.path.join(tempfile.gettempdir(), "novaget_vthumbs")
        os.makedirs(d, exist_ok=True)
        out = os.path.join(d, hashlib.md5(url.encode("utf-8")).hexdigest() + ".jpg")
    except Exception:
        return res

    def have():
        return os.path.isfile(out) and os.path.getsize(out) > 200
    # 1) أندرويد (MediaMetadataRetriever بيقرا من الرابط مباشرة)
    try:
        from jnius import autoclass
        retr = autoclass("android.media.MediaMetadataRetriever")()
        try:
            jh = autoclass("java.util.HashMap")()
            for k, v in hd.items():
                jh.put(k, v)
            retr.setDataSource(url, jh)

            def _m(key):
                try:
                    return int(str(retr.extractMetadata(key)))
                except Exception:
                    return 0
            res["w"], res["h"], res["dur"] = _m(18), _m(19), _m(9) // 1000
            if not have():
                bmp = retr.getFrameAtTime(1000000)
                if bmp is None:
                    bmp = retr.getFrameAtTime(0)
                if bmp is not None:
                    w, h = int(bmp.getWidth()), int(bmp.getHeight())
                    if max(w, h) > 320:
                        k = 320.0 / max(w, h)
                        bmp = autoclass("android.graphics.Bitmap").createScaledBitmap(
                            bmp, max(1, int(w * k)), max(1, int(h * k)), True)
                    fos = autoclass("java.io.FileOutputStream")(out)
                    bmp.compress(autoclass("android.graphics.Bitmap$CompressFormat").JPEG, 80, fos)
                    fos.close()
        finally:
            try:
                retr.release()
            except Exception:
                pass
    except Exception:
        pass
    # 2) ffmpeg
    if not have() and HAS_FFMPEG:
        try:
            import subprocess
            cmd = [FFMPEG, "-y"]
            if ref:
                cmd += ["-headers", "Referer: %s\r\n" % ref]
            cmd += ["-user_agent", _UA_MOBILE, "-ss", "1", "-i", url, "-frames:v", "1",
                    "-vf", "scale=320:-2", out]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)
        except Exception:
            pass
    # 3) PyAV
    if not have() and HAS_AV:
        try:
            opts = {"timeout": "15000000"}
            if ref:
                opts["headers"] = "Referer: %s\r\n" % ref
            c = pyav.open(url, options=opts)
            try:
                vs = c.streams.video[0]
                if not res["w"]:
                    res["w"], res["h"] = int(vs.width), int(vs.height)
                if not res["dur"] and c.duration:
                    res["dur"] = int(c.duration / 1000000)
                for frame in c.decode(video=0):
                    im = frame.to_image()
                    im.thumbnail((320, 320))
                    im.convert("RGB").save(out, "JPEG", quality=80)
                    break
            finally:
                c.close()
        except Exception:
            pass
    if have():
        res["thumb"] = out
    return res


class SocialBackdrop(Widget):
    """خلفية زخرفية خفيفة: مربعات بألوان المنصات (زي Snaptube)."""
    TILES = (("f", (.09, .47, .95)), ("IG", (.80, .22, .50)), ("YT", (.90, .12, .12)),
             ("TT", (.08, .80, .82)), ("WA", (.15, .72, .36)), ("X", (.55, .55, .62)),
             ("in", (.05, .45, .75)), ("Sc", (.95, .85, .10)))

    def __init__(self, **kw):
        super().__init__(**kw)
        self._tex = {}
        self.bind(pos=self._draw, size=self._draw)

    def _label_tex(self, txt):
        if txt not in self._tex:
            try:
                cl = CoreLabel(text=txt, font_size=dp(18), bold=True)
                cl.refresh()
                self._tex[txt] = cl.texture
            except Exception:
                self._tex[txt] = None
        return self._tex[txt]

    def _draw(self, *a):
        self.canvas.clear()
        x, y = self.pos
        w, h = self.size
        if w < 10 or h < 10:
            return
        tile = dp(50)
        cols = 4
        gap = (w - cols * tile) / float(cols + 1)
        with self.canvas:
            for i, (txt, col) in enumerate(self.TILES):
                r, c = divmod(i, cols)
                tx = x + gap * (c + 1) + tile * c + (gap / 2.0 if r else 0) - (gap / 4.0 if r else 0)
                ty = y + h - tile - dp(6) if r == 0 else y + dp(8)
                Color(col[0], col[1], col[2], .22)
                RoundedRectangle(pos=(tx, ty), size=(tile, tile), radius=[dp(14)])
                tex = self._label_tex(txt)
                if tex is not None:
                    Color(1, 1, 1, .45)
                    Rectangle(texture=tex, size=tex.size,
                              pos=(tx + (tile - tex.width) / 2.0, ty + (tile - tex.height) / 2.0))


class NavIcon(Widget):
    """أيقونة شريط التنقل السفلي (مرسومة بالكود) + عدّاد أحمر."""
    kind = StringProperty("dl")
    on = BooleanProperty(False)
    badge = NumericProperty(0)

    def __init__(self, **kw):
        super().__init__(**kw)
        self._bl = Label(text="", font_size=dp(10), bold=True, size_hint=(None, None),
                         size=(dp(18), dp(18)), text_size=(dp(18), dp(18)),
                         halign="center", valign="middle", color=(1, 1, 1, 1))
        self.add_widget(self._bl)
        self.bind(pos=self._draw, size=self._draw, kind=self._draw,
                  on=self._draw, badge=self._draw)
        self._draw()

    def _draw(self, *a):
        x, y = self.pos
        w, h = self.size
        cx, cy = x + w / 2.0, y + h / 2.0
        s = min(w, h)
        col = ON if self.on else DIM
        self.canvas.before.clear()
        with self.canvas.before:
            Color(*col)
            if self.kind == "dl":
                # Minimal, balanced download icon with a compact arrow and tray.
                sw = max(dp(2.0), s * .072)
                # Shaft and open chevron share one clear vertical axis.
                Line(points=[cx, cy + s * .285, cx, cy - s * .035],
                     width=sw, cap="round")
                ah = s * .145
                Line(points=[cx - ah, cy + s * .085,
                             cx, cy - s * .055,
                             cx + ah, cy + s * .085],
                     width=sw, cap="round", joint="round")
                # Simple open tray; short sides and a level bottom keep it crisp.
                tray_half = s * .285
                tray_top = cy - s * .105
                tray_bottom = cy - s * .285
                side_inset = s * .035
                Line(points=[cx - tray_half, tray_top,
                             cx - tray_half, tray_bottom + side_inset,
                             cx - tray_half + side_inset, tray_bottom,
                             cx + tray_half - side_inset, tray_bottom,
                             cx + tray_half, tray_bottom + side_inset,
                             cx + tray_half, tray_top],
                     width=sw, cap="round", joint="round")
            elif self.kind == "files":
                # A centered, balanced list glyph: three round bullets and matching bars.
                dot_r = max(dp(1.35), s * .052)
                bar_h = max(dp(2.5), s * .092)
                group_w = s * .68
                left = cx - group_w / 2.0
                dot_cx = left + dot_r
                bar_x = left + dot_r * 3.3
                bar_w = group_w - dot_r * 3.3
                for yy in (cy + s * .235, cy, cy - s * .235):
                    Ellipse(pos=(dot_cx - dot_r, yy - dot_r), size=(dot_r * 2, dot_r * 2))
                    RoundedRectangle(pos=(bar_x, yy - bar_h / 2.0),
                                     size=(bar_w, bar_h), radius=[bar_h / 2.0])
            else:
                # A tidy gear: round outer ring, eight short teeth and a clear hub.
                outer = s * .285
                inner = s * .105
                tooth_inner = s * .285
                tooth_outer = s * .385
                sw = max(dp(1.8), s * .065)
                Line(circle=(cx, cy, outer), width=sw)
                for i in range(8):
                    a2 = i * math.pi / 4.0
                    Line(points=[cx + math.cos(a2) * tooth_inner,
                                 cy + math.sin(a2) * tooth_inner,
                                 cx + math.cos(a2) * tooth_outer,
                                 cy + math.sin(a2) * tooth_outer],
                         width=sw, cap="round")
                Line(circle=(cx, cy, inner), width=sw)
        if self.badge > 0:
            with self.canvas.before:
                Color(0.95, 0.25, 0.30, 1)
                Ellipse(pos=(cx + s * .20, cy + s * .19), size=(dp(12), dp(12)))
            self._bl.text = str(min(int(self.badge), 99))
            self._bl.pos = (cx + s * .20 - dp(3), cy + s * .19 - dp(3))
        else:
            self._bl.text = ""


class NavItem(ButtonBehavior, BoxLayout):
    """Premium bottom navigation item with a soft gradient active capsule."""
    act = BooleanProperty(False)

    def __init__(self, kind, **kw):
        super().__init__(orientation="vertical", padding=[dp(5), dp(7), dp(5), dp(5)],
                         spacing=dp(3), **kw)
        # Explicit center_x is important: a fixed-width child in a vertical BoxLayout
        # otherwise stays at the left edge on some Kivy layouts/screens.
        self.icon = NavIcon(kind=kind, size_hint=(None, None), size=(dp(32), dp(30)),
                            pos_hint={"center_x": .5})
        self.lbl = TLabel(font_size=dp(10.5), halign="center", valign="middle", color=DIM,
                          size_hint=(1, None), height=dp(17), shorten=True)
        self.add_widget(self.icon)
        self.add_widget(self.lbl)
        self.bind(on_release=lambda *_: SOUND.play("tap"))
        self.bind(pos=self._bgd_later, size=self._bgd_later, act=self._bgd_later)
        self.icon.bind(pos=self._bgd_later, size=self._bgd_later)

    def _bgd_later(self, *a):
        Clock.unschedule(self._bgd)
        Clock.schedule_once(self._bgd, 0)

    def _bgd(self, *a):
        c = self.canvas.before
        c.clear()
        if not self.act:
            return
        with c:
            # A restrained violet/blue capsule instead of the old flat highlight.
            Color(0.39, 0.29, 0.88, 0.22)
            RoundedRectangle(pos=(self.x + dp(7), self.y + dp(2)),
                             size=(max(dp(20), self.width - dp(14)), self.height - dp(4)),
                             radius=[dp(19)])
            Color(1, 1, 1, 0.10)
            Line(rounded_rectangle=(self.x + dp(7), self.y + dp(2),
                                    max(dp(20), self.width - dp(14)), self.height - dp(4), dp(19)),
                 width=dp(0.7))
            # Small gradient indicator at the top of the selected item.
            Color(1, 1, 1, 1)
            RoundedRectangle(pos=(self.center_x - dp(13), self.top - dp(2)),
                             size=(dp(26), dp(3)), radius=[dp(2)], texture=make_grad(*GRAD))

    def set_active(self, v):
        self.act = bool(v)
        self.icon.on = bool(v)
        self.lbl.color = ON if v else DIM
        self.lbl.bold = bool(v)
        self.lbl.font_size = dp(11 if v else 10.5)


class CheckMark(Widget):
    """مربع اختيار مرسوم بالكود (من غير خطوط ولا صور)."""
    on = BooleanProperty(True)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(pos=self._draw, size=self._draw, on=self._draw)
        self._draw()

    def _draw(self, *a):
        self.canvas.clear()
        x, y = self.pos
        w, h = self.size
        with self.canvas:
            if self.on:
                Color(*ACC1)
                RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(7)])
                Color(*WHITE)
                Line(points=[x + w * .24, y + h * .52, x + w * .43, y + h * .30,
                             x + w * .76, y + h * .72], width=dp(2.4), cap="round", joint="round")
            else:
                Color(.35, .40, .58, 1)
                Line(rounded_rectangle=(x, y, w, h, dp(7)), width=dp(1.6))


class PickRow(ButtonBehavior, Card):
    """صف بلاي ليست قابل للضغط (تحديد/إلغاء تحديد فيديو)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(on_release=lambda *_: SOUND.play("tap"))


class LongRow(ButtonBehavior, Card):
    """صف بيدعم الضغطة العادية والضغطة المطولة."""

    def __init__(self, on_long=None, on_tap=None, **kw):
        super().__init__(**kw)
        self._long_cb = on_long
        self._tap_cb = on_tap
        self._lp_ev = None
        self._lp_fired = False

    def on_press(self):
        self._lp_fired = False
        self._lp_ev = Clock.schedule_once(self._fire, 0.55)

    def _fire(self, dt):
        self._lp_ev = None
        self._lp_fired = True
        SOUND.play("tap")
        if self._long_cb:
            self._long_cb()

    def on_release(self):
        if self._lp_ev is not None:
            self._lp_ev.cancel()
            self._lp_ev = None
        if not self._lp_fired:
            SOUND.play("tap")
        if not self._lp_fired and self._tap_cb:
            self._tap_cb()


# ------------------------------------------------------------------ helpers
def human_size(n):
    if not n:
        return "?"
    n = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.1f %s" % (n, u)
        n /= 1024.0
    return "%.1f TB" % n


def fmt_dur(s):
    if not s:
        return ""
    s = int(s)
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return "%d:%02d:%02d" % (h, m, sec) if h else "%d:%02d" % (m, sec)


def pick_thumb(info):
    """أفضل صورة مصغّرة متاحة (يوتيوب/تيك توك/...)."""
    if not info:
        return ""
    thumbs = [x for x in (info.get("thumbnails") or []) if x.get("url")]
    if thumbs:
        # فضّل عرض قريب من 480، وتجنّب الصور الصغيرة جدًا
        sized = [x for x in thumbs if (x.get("width") or 0) >= 120]
        if not sized:
            sized = [x for x in thumbs if x.get("width")]
        if sized:
            return min(sized, key=lambda x: abs((x.get("width") or 0) - 480))["url"]
        return thumbs[-1]["url"]
    t = info.get("thumbnail") or ""
    if t:
        return t
    # يوتيوب: ابنِ رابط الصورة من الـ id
    vid = info.get("id") or ""
    if vid and ("youtube" in str(info.get("extractor_key") or "").lower()
                or "youtu" in str(info.get("extractor") or "").lower()):
        return "https://i.ytimg.com/vi/%s/hqdefault.jpg" % vid
    return ""


def entry_thumb(e):
    """صورة مصغّرة لعنصر بلاي ليست (حتى مع extract_flat)."""
    if not isinstance(e, dict):
        return ""
    t = e.get("thumbnail") or ""
    if t:
        return t
    thumbs = e.get("thumbnails") or []
    if thumbs:
        best = None
        for x in thumbs:
            if not x.get("url"):
                continue
            if best is None or (x.get("width") or 0) > (best.get("width") or 0):
                best = x
        if best:
            return best["url"]
    vid = e.get("id") or ""
    if vid and re.match(r"^[\w-]{6,}$", str(vid)):
        # غالبًا يوتيوب
        return "https://i.ytimg.com/vi/%s/hqdefault.jpg" % vid
    return ""


def estimate_video_bytes(duration, height=0, audio_kbps=0):
    """تقدير حجم التحميل بالبايت من المدة والجودة."""
    if not duration:
        return None
    if audio_kbps:
        return int(duration * audio_kbps * 125.0)  # kbps → bytes
    # تقدير متوسط لفيديو + صوت
    h = int(height or 720)
    if h <= 0:
        h = 720
    # Mbps تقريبي حسب الارتفاع
    if h >= 2160:
        mbps = 15.0
    elif h >= 1440:
        mbps = 10.0
    elif h >= 1080:
        mbps = 4.5
    elif h >= 720:
        mbps = 2.2
    elif h >= 480:
        mbps = 1.2
    else:
        mbps = 0.7
    return int(duration * mbps * 1e6 / 8.0)


def clean_err(e):
    return re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")


def user_facing_error(e):
    """Give a useful next step for common network/access failures without hiding details."""
    raw = clean_err(e)
    low = raw.lower()
    if any(k in low for k in (
            "timed out", "timeout", "connection reset", "connection aborted",
            "temporary failure in name resolution", "name or service not known",
            "network is unreachable", "remote end closed", "incomplete read",
            "http error 429", "http error 500", "http error 502", "http error 503",
            "http error 504", "urlopen error")):
        return ("Temporary network/server problem. Check the connection; if the site is "
                "rate-limiting requests, wait a little before retrying.\nDetails: " + raw)
    if any(k in low for k in (
            "private video", "login required", "sign in", "authentication required",
            "http error 401", "http error 403", "cookies are no longer valid",
            "confirm you're not a bot", "not available in your country")):
        return ("The site may require login/cookies or may block this request. Check the "
                "account/session and the link permissions.\nDetails: " + raw)
    if any(k in low for k in ("unsupported url", "no suitable extractor", "unsupported site")):
        return "This site or link format may not be supported by the current yt-dlp version.\nDetails: " + raw
    return raw


def badge_color(kind, height):
    if kind == "a":
        return (0.12, 0.62, 0.45, 1)
    if kind == "img":
        return (0.90, 0.45, 0.55, 1)
    if height >= 2160:
        return (0.85, 0.55, 0.10, 1)
    if height >= 1080:
        return (0.424, 0.361, 0.906, 1)
    if height >= 720:
        return (0.10, 0.50, 0.85, 1)
    return (0.30, 0.35, 0.50, 1)


def codec_name(code):
    c = (code or "").lower()
    if not c or c == "none":
        return ""
    if c.startswith(("avc", "h264")):
        return "H.264"
    if c.startswith(("hev", "hvc", "h265", "bytevc1")):
        return "H.265"
    if c.startswith(("vp9", "vp09")):
        return "VP9"
    if c.startswith("vp8"):
        return "VP8"
    if c.startswith("av01"):
        return "AV1"
    if c.startswith("mp4a"):
        return "AAC"
    if c.startswith("opus"):
        return "Opus"
    return c.split(".")[0].upper()[:6]


APP_FOLDER = "NovaGet"
SUB_VIDEO, SUB_IMAGE, SUB_AUDIO = "Videos", "Images", "Audio"
_VIDEO_EXTS = (".mp4", ".m4v", ".mov", ".mkv", ".webm", ".3gp", ".avi", ".ts")
_AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".opus", ".ogg", ".wav", ".flac", ".mka")


def _writable_dir(p):
    """يتأكد إن المجلد ينفع نكتب فيه فعلًا (مش بس موجود)."""
    try:
        os.makedirs(p, exist_ok=True)
        t = os.path.join(p, ".nvg_probe")
        with open(t, "w") as f:
            f.write("1")
        os.remove(t)
        return True
    except Exception:
        return False


def is_apk():
    """True لو البرنامج شغال كـ APK مستقل (مش جوه Pydroid)."""
    return bool(_apk_private_dir())


def has_all_files_access():
    """أندرويد 11+: هل التطبيق بياخد إذن 'كل الملفات' (لازم عشان يحفظ في مجلد NovaGet)؟"""
    try:
        from jnius import autoclass
        if int(autoclass("android.os.Build$VERSION").SDK_INT) < 30:
            return True
        return bool(autoclass("android.os.Environment").isExternalStorageManager())
    except Exception:
        return True


def open_all_files_settings():
    """بيفتح شاشة الإعدادات اللي المستخدم يفعّل منها 'السماح بالوصول لكل الملفات' للتطبيق."""
    try:
        from jnius import autoclass
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        Intent = autoclass("android.content.Intent")
        Settings = autoclass("android.provider.Settings")
        Uri = autoclass("android.net.Uri")
        try:
            act.startActivity(Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                                     Uri.parse("package:" + act.getPackageName())))
        except Exception:
            act.startActivity(Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))
        return True
    except Exception as e:
        nlog("all-files settings: %s" % e)
        return False


def request_legacy_storage():
    """أندرويد 10 وأقل: إذن التخزين العادي."""
    try:
        from jnius import autoclass
        if int(autoclass("android.os.Build$VERSION").SDK_INT) >= 30:
            return
        from android.permissions import request_permissions, Permission
        request_permissions([Permission.READ_EXTERNAL_STORAGE, Permission.WRITE_EXTERNAL_STORAGE])
    except Exception:
        pass


def get_download_dir():
    """مجلد البرنامج: NovaGet/ وجواه Videos و Images و Audio."""
    bases = ("/storage/emulated/0/" + APP_FOLDER,
             "/storage/emulated/0/Download/" + APP_FOLDER,
             "/sdcard/Download/" + APP_FOLDER,
             os.path.join(os.getcwd(), APP_FOLDER))
    for b in bases:
        if _writable_dir(b):
            for sub in (SUB_VIDEO, SUB_IMAGE, SUB_AUDIO):
                try:
                    os.makedirs(os.path.join(b, sub), exist_ok=True)
                except Exception:
                    pass
            return b
    return os.getcwd()


def sub_dir(base, which):
    d = os.path.join(base, which)
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        return base


def sub_for_job(base, it):
    """يحدد المجلد الفرعي (فيديو / صور / صوت) حسب نوع التحميل."""
    it = it or {}
    if it.get("mp3") or it.get("kind") == "a":
        return sub_dir(base, SUB_AUDIO)
    if it.get("kind") == "img":
        ext = ("." + str(it.get("ext") or "").lower().lstrip("."))
        if it.get("vurl") or ext in _VIDEO_EXTS:
            return sub_dir(base, SUB_VIDEO)
        return sub_dir(base, SUB_IMAGE)
    return sub_dir(base, SUB_VIDEO)


class Cancelled(Exception):
    pass


class Paused(Exception):
    pass


class Job(object):
    """تحميل واحد في القائمة."""
    _id_gen = itertools.count(1)
    _id_lock = threading.Lock()

    def __init__(self, url, title, it):
        with Job._id_lock:
            self.id = next(Job._id_gen)
        self.url = url
        self.title = title
        self.it = dict(it)
        self.state = "queued"        # queued / downloading / paused
        self.cancel = False
        self.phase = "dl"            # dl / proc
        self.progress = 0.0
        self.done_b = 0
        self.total_b = None
        self._eta_s = None           # الوقت المتبقي بعد التنعيم
        self._eta_t = 0.0
        self._f0 = None              # (وقت, نسبة) أول قراءة للأجزاء، لحساب متوسط السرعة
        self.frag_i = 0              # HLS/DASH: الجزء الحالي
        self.frag_n = 0              # HLS/DASH: عدد الأجزاء الكلي
        self.speed = 0
        self.eta = None
        self.path = ""
        self.thumb_path = ""
        self.ydl = None
        self.pause_req = False
        self.net_wait = False
        self.notified = False
        self.n_sig = None
        self.n_ts = 0
        self.last_hook = 0.0         # آخر مرة yt-dlp بلّغ بتقدّم (لكشف التوقف)
        self._lock = threading.Lock()  # يحمي تحديثات progress/state من race

    def entry(self, ok, err):
        size = None
        try:
            if self.path and os.path.isfile(self.path):
                size = os.path.getsize(self.path)
        except Exception:
            pass
        return dict(title=self.title, url=self.url, it=self.it,
                    state="done" if ok else "failed", err=err,
                    path=self.path, thumb_path=getattr(self, "thumb_path", ""),
                    bytes=size, ts=time.time())


def can_embed_thumb(it):
    """الصورة المصغرة بتتدمج جوه الملف (محتاج ffmpeg وصيغة بتدعم كده)."""
    # مفيش صور مصغّرة خالص: ده اللي كان بيساب ملف صورة جنب الفيديو في مدير الملفات.
    return False
    if not HAS_FFMPEG:
        return False
    if it.get("kind") in ("img", "a") and not it.get("mp3"):
        return False
    if it.get("kind") == "m":
        return True
    return str(it.get("ext", "")).upper() in ("MP4", "M4A", "MP3", "M4V", "MOV", "MKV", "MKA")


def result_path(y, info):
    try:
        rd = info.get("requested_downloads") or []
        if rd and rd[0].get("filepath"):
            return rd[0]["filepath"]
        return info.get("filepath") or y.prepare_filename(info)
    except Exception:
        return ""


# ---- دمج فيديو + صوت MP4 من غير ffmpeg (لملفات يوتيوب fragmented)
class MuxError(Exception):
    pass


def _top_boxes(f, total):
    out, pos = [], 0
    while pos + 8 <= total:
        f.seek(pos)
        h = f.read(8)
        if len(h) < 8:
            break
        size, typ = struct.unpack(">I4s", h)
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            hdr = 16
        elif size == 0:
            raise MuxError("open-ended box")
        if size < hdr or pos + size > total:
            raise MuxError("bad box")
        out.append((typ, pos, size, hdr))
        pos += size
    return out


def _kids(buf, start=0, end=None):
    end = len(buf) if end is None else end
    res, pos = [], start
    while pos + 8 <= end:
        size, typ = struct.unpack_from(">I4s", buf, pos)
        hdr = 8
        if size == 1:
            size = struct.unpack_from(">Q", buf, pos + 8)[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr or pos + size > end:
            raise MuxError("bad child box")
        res.append((typ, pos, size, hdr))
        pos += size
    return res


def _find(buf, kids, typ):
    for k in kids:
        if k[0] == typ:
            return k
    return None


def _box(typ, payload):
    return struct.pack(">I4s", 8 + len(payload), typ) + payload


def _trak_info(moov, trak):
    """(handler, track_id, timescale, tkhd_id_pos) من trak."""
    t_kids = _kids(moov, trak[1] + trak[3], trak[1] + trak[2])
    tkhd = _find(moov, t_kids, b"tkhd")
    mdia = _find(moov, t_kids, b"mdia")
    if not tkhd or not mdia:
        raise MuxError("bad trak")
    p = tkhd[1] + tkhd[3]
    ver = moov[p]
    id_pos = p + (4 + 8 + 8 if ver == 1 else 4 + 4 + 4)
    tid = struct.unpack_from(">I", moov, id_pos)[0]
    m_kids = _kids(moov, mdia[1] + mdia[3], mdia[1] + mdia[2])
    mdhd = _find(moov, m_kids, b"mdhd")
    hdlr = _find(moov, m_kids, b"hdlr")
    if not mdhd or not hdlr:
        raise MuxError("bad mdia")
    p = mdhd[1] + mdhd[3]
    ts_pos = p + (4 + 8 + 8 if moov[p] == 1 else 4 + 4 + 4)
    timescale = struct.unpack_from(">I", moov, ts_pos)[0]
    handler = bytes(moov[hdlr[1] + hdlr[3] + 8: hdlr[1] + hdlr[3] + 12])
    return handler, tid, timescale, id_pos


def _read_moov(path, want):
    with open(path, "rb") as f:
        f.seek(0, 2)
        total = f.tell()
        top = _top_boxes(f, total)
        ftyp = _find(None, top, b"ftyp")
        moov_b = _find(None, top, b"moov")
        if not moov_b:
            raise MuxError("no moov")
        if not any(b[0] == b"moof" for b in top):
            raise MuxError("not fragmented")
        f.seek(moov_b[1])
        moov = bytearray(f.read(moov_b[2]))
        ftyp_bytes = b""
        if ftyp:
            f.seek(ftyp[1])
            ftyp_bytes = f.read(ftyp[2])
    kids = _kids(moov, 8, len(moov))
    trak = None
    for k in kids:
        if k[0] == b"trak":
            h, tid, ts, idp = _trak_info(moov, k)
            if h == want:
                trak = (k, tid, ts, idp)
                break
    if trak is None:
        raise MuxError("no %s track" % want)
    mvex = _find(moov, kids, b"mvex")
    if not mvex:
        raise MuxError("no mvex")
    trex = None
    for k in _kids(moov, mvex[1] + 8, mvex[1] + mvex[2]):
        if k[0] == b"trex" and struct.unpack_from(">I", moov, k[1] + 12)[0] == trak[1]:
            trex = bytearray(moov[k[1]:k[1] + k[2]])
    if trex is None:
        raise MuxError("no trex")
    mvhd = _find(moov, kids, b"mvhd")
    return dict(top=top, ftyp=ftyp_bytes, moov=moov, kids=kids, trak=trak,
                trex=trex, mvhd=mvhd)


def _fragments(path, timescale):
    """قائمة (time_s, moof_off, moof_size, mdat_off, mdat_size, mdat_hdr)."""
    frags = []
    with open(path, "rb") as f:
        f.seek(0, 2)
        total = f.tell()
        top = _top_boxes(f, total)
        i = 0
        while i < len(top):
            if top[i][0] == b"moof":
                moof = top[i]
                j = i + 1
                while j < len(top) and top[j][0] != b"mdat":
                    if top[j][0] == b"moof":
                        break
                    j += 1
                if j >= len(top) or top[j][0] != b"mdat":
                    raise MuxError("moof without mdat")
                mdat = top[j]
                f.seek(moof[1])
                mb = f.read(moof[2])
                t = _frag_time(mb) / float(timescale)
                frags.append((t, moof[1], moof[2], mdat[1], mdat[2], mdat[3]))
                i = j + 1
            else:
                i += 1
    return frags


def _frag_time(moof):
    for k in _kids(moof, 8, len(moof)):
        if k[0] == b"traf":
            for c in _kids(moof, k[1] + 8, k[1] + k[2]):
                if c[0] == b"tfdt":
                    p = c[1] + c[3]
                    if moof[p] == 1:
                        return struct.unpack_from(">Q", moof, p + 4)[0]
                    return struct.unpack_from(">I", moof, p + 4)[0]
    return 0


def _patch_moof(mb, seq, new_tid, old_tid):
    mb = bytearray(mb)
    for k in _kids(mb, 8, len(mb)):
        if k[0] == b"mfhd":
            struct.pack_into(">I", mb, k[1] + k[3] + 4, seq)
        elif k[0] == b"traf":
            for c in _kids(mb, k[1] + 8, k[1] + k[2]):
                if c[0] == b"tfhd":
                    p = c[1] + c[3]
                    flags = struct.unpack_from(">I", mb, p)[0] & 0xFFFFFF
                    if flags & 0x1:
                        raise MuxError("base_data_offset not supported")
                    if new_tid != old_tid:
                        struct.pack_into(">I", mb, p + 4, new_tid)
    return bytes(mb)


def _build_sidx(order, vf, v_tid, v_ts, duration):
    """فهرس sidx للفيديو: بيخلّي مشغّل الاستيديو يقدّم فورًا بدل ما يمسح الملف كله."""
    try:
        ticks = [int(round(f[0] * v_ts)) for f in vf]
        if not ticks:
            return b""
        durs = [max(1, ticks[i + 1] - ticks[i]) for i in range(len(ticks) - 1)]
        if duration:
            durs.append(max(1, int(duration * v_ts) - ticks[-1]))
        else:
            durs.append(durs[-1] if durs else 1)
        refs, pre = [], 0
        for _t, which, _i, fr in order:
            sz = fr[2] + fr[4]
            if which == 0:
                refs.append(sz)
            elif refs:
                refs[-1] += sz
            else:
                pre += sz
        if len(refs) != len(durs):
            return b""
        body = struct.pack(">I", 0) + struct.pack(">IIII", v_tid, v_ts, ticks[0], pre)
        body += struct.pack(">HH", 0, len(refs))
        for sz, du in zip(refs, durs):
            body += struct.pack(">III", sz & 0x7FFFFFFF, du, 0x90000000)
        return _box(b"sidx", body)
    except Exception:
        return b""


def mux_fragmented(video_path, audio_path, out_path, duration=None, check=None):
    """بيدمج فيديو MP4 + صوت M4A (الاتنين fragmented زي يوتيوب) من غير ffmpeg."""
    v = _read_moov(video_path, b"vide")
    a = _read_moov(audio_path, b"soun")
    vk, v_tid, v_ts, _ = v["trak"]
    ak, a_tid, a_ts, a_idp = a["trak"]
    new_a_tid = a_tid if a_tid != v_tid else max(v_tid, a_tid) + 1

    # trak الصوت (بعد تغيير رقم المسار لو لزم)
    a_trak = bytearray(a["moov"][ak[1]:ak[1] + ak[2]])
    if new_a_tid != a_tid:
        struct.pack_into(">I", a_trak, a_idp - ak[1], new_a_tid)
    v_trak = bytes(v["moov"][vk[1]:vk[1] + vk[2]])

    # mvhd
    mv = v["mvhd"]
    mvhd = bytearray(v["moov"][mv[1]:mv[1] + mv[2]])
    p = mv[3]
    ver = mvhd[p]
    if ver == 1:
        ts_pos, dur_pos, nt_pos = p + 4 + 16, p + 4 + 20, p + 108
    else:
        ts_pos, dur_pos, nt_pos = p + 4 + 8, p + 4 + 12, p + 96
    mv_ts = struct.unpack_from(">I", mvhd, ts_pos)[0]
    struct.pack_into(">I", mvhd, nt_pos, max(v_tid, new_a_tid) + 1)
    mehd = b""
    if duration:
        d = int(duration * mv_ts)
        if ver == 1:
            struct.pack_into(">Q", mvhd, dur_pos, d)
            mehd = _box(b"mehd", struct.pack(">I", 0x01000000) + struct.pack(">Q", d))
        else:
            struct.pack_into(">I", mvhd, dur_pos, d)
            mehd = _box(b"mehd", struct.pack(">II", 0, d))

    trex_a = bytearray(a["trex"])
    struct.pack_into(">I", trex_a, 12, new_a_tid)
    mvex = _box(b"mvex", mehd + bytes(v["trex"]) + bytes(trex_a))
    moov = _box(b"moov", bytes(mvhd) + v_trak + bytes(a_trak) + mvex)

    vf = _fragments(video_path, v_ts)
    af = _fragments(audio_path, a_ts)
    order = sorted([(f[0], 0, i, f) for i, f in enumerate(vf)] +
                   [(f[0], 1, i, f) for i, f in enumerate(af)],
                   key=lambda x: (x[0], x[1], x[2]))

    sidx = _build_sidx(order, vf, v_tid, v_ts, duration)
    total = sum(f[2] + f[4] for f in vf) + sum(f[2] + f[4] for f in af)
    done = 0
    with open(video_path, "rb") as fv, open(audio_path, "rb") as fa, \
            open(out_path, "wb") as out:
        out.write(v["ftyp"])
        out.write(moov)
        if sidx:
            out.write(sidx)
        seq = 0
        for _t, which, _i, fr in order:
            src = fa if which else fv
            seq += 1
            _t2, moof_off, moof_size, mdat_off, mdat_size, mdat_hdr = fr
            src.seek(moof_off)
            mb = src.read(moof_size)
            out.write(_patch_moof(mb, seq, new_a_tid if which else v_tid,
                                  a_tid if which else v_tid))
            src.seek(mdat_off)
            left = mdat_size
            while left > 0:
                chunk = src.read(min(left, 1 << 20))
                if not chunk:
                    raise MuxError("truncated mdat")
                out.write(chunk)
                left -= len(chunk)
                done += len(chunk)
                if check:
                    check(done, total)
            done += moof_size
    return out_path


def remux_progressive(vpath, apath, out_path, check=None):
    """بيدمج الفيديو والصوت في MP4 عادي (مش fragmented) عشان التقديم والتأخير يشتغلوا."""
    import heapq
    iv = pyav.open(vpath)
    ia = pyav.open(apath)
    try:
        ov = pyav.open(out_path, "w", format="mp4", options={"movflags": "+faststart"})
    except Exception:
        ov = pyav.open(out_path, "w", format="mp4")
    try:
        vs = iv.streams.video[0]
        as_ = ia.streams.audio[0]
        if hasattr(ov, "add_stream_from_template"):
            ovs = ov.add_stream_from_template(vs)
            oas = ov.add_stream_from_template(as_)
        else:
            ovs = ov.add_stream(template=vs)
            oas = ov.add_stream(template=as_)

        def gen(cont, st, idx):
            for pkt in cont.demux(st):
                if pkt.dts is None:
                    continue
                yield (float(pkt.dts * pkt.time_base), idx, pkt)
        for _t, idx, pkt in heapq.merge(gen(iv, vs, 0), gen(ia, as_, 1),
                                        key=lambda x: (x[0], x[1])):
            if check:
                check(0, 0)
            pkt.stream = ovs if idx == 0 else oas
            ov.mux(pkt)
    finally:
        try:
            ov.close()
        finally:
            iv.close()
            ia.close()
    return out_path


def mux_to_mp4(vpath, apath, out_path, duration=None, check=None):
    """دمج فيديو + صوت: الأول MP4 عادي (فيه فهرس) عشان التقديم يشتغل من غير تعليق،
    ولو PyAV مش موجود أو فشل نرجع للدمج المقطّع."""
    if HAS_AV:
        try:
            remux_progressive(vpath, apath, out_path, check=check)
            if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
                return out_path
        except (Cancelled, Paused):
            raise
        except Exception:
            pass
        try:
            os.remove(out_path)
        except Exception:
            pass
    return mux_fragmented(vpath, apath, out_path, duration=duration, check=check)


def mp4_needs_fix(path):
    """True لو الملف fragmented أو الـ moov في آخره (دول سبب تعليق التقديم)."""
    try:
        size = os.path.getsize(path)
        order, pos = [], 0
        with open(path, "rb") as f:
            while pos + 8 <= size and len(order) < 200:
                f.seek(pos)
                h = f.read(16)
                if len(h) < 8:
                    break
                sz = struct.unpack(">I", h[:4])[0]
                typ = h[4:8]
                if sz == 1 and len(h) >= 16:
                    sz = struct.unpack(">Q", h[8:16])[0]
                elif sz == 0:
                    sz = size - pos
                if sz < 8:
                    break
                order.append(typ)
                pos += sz
        # ملف امتداده mp4 لكن جوّاه MPEG-TS (HLS) أو أي حاجة مش ISO-MP4: مفيش فهرس => التقديم بيعلّق
        if not order or order[0] not in (b"ftyp", b"styp", b"moov", b"mdat", b"free", b"skip", b"wide", b"pnot", b"uuid", b"meta"):
            return True
        if b"ftyp" not in order and b"moov" not in order:
            return True
        if b"moof" in order:
            return True
        if b"moov" in order and b"mdat" in order and order.index(b"moov") > order.index(b"mdat"):
            return True
    except Exception:
        pass
    return False


def py_faststart(path):
    """ينقل moov لأول الملف (من غير ffmpeg ولا PyAV) ويظبط أماكن الـ chunks. True لو نجح."""
    tmp = os.path.splitext(path)[0] + ".nvg_fs.mp4"
    try:
        size = os.path.getsize(path)
        boxes, pos = [], 0
        with open(path, "rb") as f:
            while pos + 8 <= size:
                f.seek(pos)
                h = f.read(16)
                sz = struct.unpack(">I", h[:4])[0]
                typ = h[4:8]
                if sz == 1:
                    sz = struct.unpack(">Q", h[8:16])[0]
                elif sz == 0:
                    sz = size - pos
                if sz < 8 or pos + sz > size:
                    return False
                boxes.append((typ, pos, sz))
                pos += sz
            mi = [i for i, b in enumerate(boxes) if b[0] == b"moov"]
            di = [i for i, b in enumerate(boxes) if b[0] == b"mdat"]
            if len(mi) != 1 or not di or boxes[mi[0]][1] < boxes[di[0]][1]:
                return False
            if any(b[0] == b"moof" for b in boxes):
                return False
            mb = boxes[mi[0]]
            f.seek(mb[1])
            moov = bytearray(f.read(mb[2]))
            delta = mb[2]

            def walk(lo, hi):
                p = lo
                while p + 8 <= hi:
                    sz = struct.unpack_from(">I", moov, p)[0]
                    typ = bytes(moov[p + 4:p + 8])
                    if sz < 8 or p + sz > hi:
                        return
                    if typ in (b"trak", b"mdia", b"minf", b"stbl"):
                        walk(p + 8, p + sz)
                    elif typ == b"stco":
                        n = struct.unpack_from(">I", moov, p + 12)[0]
                        for k in range(n):
                            o = p + 16 + 4 * k
                            v = struct.unpack_from(">I", moov, o)[0] + delta
                            if v > 0xFFFFFFFF:
                                raise ValueError("stco overflow")
                            struct.pack_into(">I", moov, o, v)
                    elif typ == b"co64":
                        n = struct.unpack_from(">I", moov, p + 12)[0]
                        for k in range(n):
                            o = p + 16 + 8 * k
                            struct.pack_into(">Q", moov, o, struct.unpack_from(">Q", moov, o)[0] + delta)
                    p += sz
            walk(8, len(moov))
            first_mdat = boxes[di[0]][1]
            with open(tmp, "wb") as out:
                f.seek(0)
                left = first_mdat
                while left > 0:
                    c = f.read(min(left, 1 << 20))
                    if not c:
                        raise ValueError("short read")
                    out.write(c)
                    left -= len(c)
                out.write(bytes(moov))
                for i, b in enumerate(boxes):
                    if b[1] < first_mdat or i == mi[0]:
                        continue
                    f.seek(b[1])
                    left = b[2]
                    while left > 0:
                        c = f.read(min(left, 1 << 20))
                        if not c:
                            raise ValueError("short read")
                        out.write(c)
                        left -= len(c)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def mp4_keyframe_gap(path):
    """أكبر فجوة (ثواني) بين الفريمات المفتاحية في أول فيديو تراك. None لو مقدرناش نقرأ."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            pos, moov = 0, None
            while pos + 8 <= size:
                f.seek(pos)
                h = f.read(16)
                sz = struct.unpack(">I", h[:4])[0]
                typ = h[4:8]
                if sz == 1:
                    sz = struct.unpack(">Q", h[8:16])[0]
                elif sz == 0:
                    sz = size - pos
                if sz < 8:
                    return None
                if typ == b"moov":
                    f.seek(pos)
                    moov = f.read(sz)
                    break
                pos += sz
        if not moov:
            return None

        def kids(buf, lo, hi):
            p = lo
            while p + 8 <= hi:
                sz = struct.unpack_from(">I", buf, p)[0]
                if sz < 8 or p + sz > hi:
                    return
                yield bytes(buf[p + 4:p + 8]), p, sz
                p += sz
        for t, p, sz in kids(moov, 8, len(moov)):
            if t != b"trak":
                continue
            mdia = next(((a, b) for tt, a, b in kids(moov, p + 8, p + sz) if tt == b"mdia"), None)
            if not mdia:
                continue
            a, b = mdia
            hdlr = next(((x, y) for tt, x, y in kids(moov, a + 8, a + b) if tt == b"hdlr"), None)
            if not hdlr or bytes(moov[hdlr[0] + 16:hdlr[0] + 20]) != b"vide":
                continue
            mdhd = next(x for tt, x, y in kids(moov, a + 8, a + b) if tt == b"mdhd")
            ver = moov[mdhd + 8]
            ts_ = struct.unpack_from(">I", moov, mdhd + (28 if ver == 1 else 20))[0] or 1
            minf = next((x, y) for tt, x, y in kids(moov, a + 8, a + b) if tt == b"minf")
            stbl = next((x, y) for tt, x, y in kids(moov, minf[0] + 8, minf[0] + minf[1]) if tt == b"stbl")
            sub = {tt: (x, y) for tt, x, y in kids(moov, stbl[0] + 8, stbl[0] + stbl[1])}
            if b"stss" not in sub:
                return 0.0          # كل الفريمات مفتاحية
            x, _y = sub[b"stss"]
            n = struct.unpack_from(">I", moov, x + 12)[0]
            sync = struct.unpack_from(">%dI" % n, moov, x + 16)
            x, _y = sub[b"stts"]
            ne = struct.unpack_from(">I", moov, x + 12)[0]
            ents = [struct.unpack_from(">II", moov, x + 16 + 8 * i) for i in range(ne)]
            # وقت كل sample (بالـ ticks) بالتراكم
            marks, idx, tick = {}, 1, 0
            want = set(sync)
            for cnt, delta in ents:
                if not want:
                    break
                # أقرب sync جوه المدى [idx, idx+cnt)
                for sidx in [v for v in want if idx <= v < idx + cnt]:
                    marks[sidx] = tick + (sidx - idx) * delta
                    want.discard(sidx)
                tick += cnt * delta
                idx += cnt
            ts_list = sorted(marks.values())
            end = tick
            ts_list.append(end)
            gaps = [(b2 - a2) / float(ts_) for a2, b2 in zip(ts_list, ts_list[1:])]
            return max(gaps) if gaps else None
    except Exception:
        return None
    return None


def ffmpeg_faststart(path, check=None):
    """إعادة تغليف بدون إعادة ترميز بـ ffmpeg (بيصلّح fragmented / TS / moov في الآخر). True لو نجح."""
    if not HAS_FFMPEG:
        return False
    import subprocess
    tmp = os.path.splitext(path)[0] + ".nvg_fix.mp4"
    try:
        cmd = [FFMPEG, "-y", "-v", "error", "-i", path, "-map", "0:v:0?", "-map", "0:a:0?",
               "-c", "copy", "-movflags", "+faststart", "-f", "mp4", tmp]
        pr = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        while pr.poll() is None:
            if check:
                try:
                    check(0, 0)
                except BaseException:
                    pr.kill()
                    raise
            if time.time() - t0 > 600:
                pr.kill()
                break
            time.sleep(0.4)
        if pr.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, path)
            return True
    except (Cancelled, Paused):
        raise
    except Exception as ex:
        nlog("ffmpeg_faststart failed: %s" % ex)
    try:
        if os.path.isfile(tmp):
            os.remove(tmp)
    except Exception:
        pass
    return False


# ======== TS -> MP4 (بايثون صرف) ========
def _t2m_u32(x): return struct.pack(">I", x)
def _t2m_u16(x): return struct.pack(">H", x)
def _t2m_box(t, *parts):
    p = b"".join(parts)
    return _t2m_u32(8 + len(p)) + t + p
def _t2m_fbox(t, ver, flags, *parts):
    return _t2m_box(t, _t2m_u32((ver << 24) | flags), *parts)

# ---------------- TS parsing
def _t2m_unwrap(v, last):
    if last is None: return v
    while v < last - (1 << 32): v += 1 << 33
    return v

def _t2m_parse_pes(data):
    if len(data) < 9 or data[0] != 0 or data[1] != 0 or data[2] != 1:
        return None, None, b""
    flags = data[7] >> 6
    hl = data[8]
    pts = dts = None
    def ts(o):
        return (((data[o] >> 1) & 7) << 30) | (data[o + 1] << 22) | ((data[o + 2] >> 1) << 15) | \
               (data[o + 3] << 7) | (data[o + 4] >> 1)
    if flags & 2 and len(data) >= 14:
        pts = ts(9)
    if flags == 3 and len(data) >= 19:
        dts = ts(14)
    return pts, dts, data[9 + hl:]

def _t2m_rbsp_unescape(b):
    return re.sub(b"\x00\x00\x03", b"\x00\x00", b)

class _t2m_BR:
    def __init__(s, b): s.b = b; s.p = 0
    def bit(s):
        v = (s.b[s.p >> 3] >> (7 - (s.p & 7))) & 1; s.p += 1; return v
    def bits(s, n):
        v = 0
        for _ in range(n): v = (v << 1) | s.bit()
        return v
    def ue(s):
        z = 0
        while s.bit() == 0 and z < 32: z += 1
        return (1 << z) - 1 + s.bits(z) if z else 0
    def se(s):
        k = s.ue(); return (k + 1) // 2 if k & 1 else -(k // 2)

def _t2m_parse_sps(sps):
    r = _t2m_BR(_t2m_rbsp_unescape(sps[1:]))
    prof = r.bits(8); r.bits(8); lvl = r.bits(8); r.ue()
    _t2m_parse_sps.info = {"profile": prof, "level": lvl}
    chroma = 1
    if prof in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        chroma = r.ue()
        if chroma == 3: r.bit()
        r.ue(); r.ue(); r.bit()
        if r.bit():
            for i in range(8 if chroma != 3 else 12):
                if r.bit():
                    last = 8; nxt = 8
                    for _ in range(16 if i < 6 else 64):
                        if nxt != 0:
                            nxt = (last + r.se() + 256) % 256
                        last = last if nxt == 0 else nxt
    _t2m_parse_sps.info["l2fn"] = r.ue() + 4
    poc = r.ue()
    if poc == 0: r.ue()
    elif poc == 1:
        r.bit(); r.se(); r.se()
        for _ in range(r.ue()): r.se()
    r.ue(); r.bit()
    wmb = r.ue() + 1; hmu = r.ue() + 1
    fmo = r.bit()
    _t2m_parse_sps.info["frame_mbs_only"] = fmo; _t2m_parse_sps.info["poc_type"] = poc
    if not fmo: r.bit()
    r.bit()
    w = wmb * 16; h = hmu * 16 * (2 - fmo)
    if r.bit():
        cl, cr, ct, cb = r.ue(), r.ue(), r.ue(), r.ue()
        cx = 1 if chroma in (0, 3) else 2
        cy = (1 if chroma in (0, 2) else 2) * (2 - fmo) if chroma != 0 else (2 - fmo)
        w -= (cl + cr) * cx; h -= (ct + cb) * cy
    return w, h

_t2m_SR = [96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350]

def _t2m_convert_raw(path, out, log=print, check=None):
    size = os.path.getsize(path)
    tmp = out + ".mdat.tmp"
    f = open(path, "rb")
    head = f.read(188 * 6)
    start = 0
    for i in range(188):
        if all(head[i + 188 * k:i + 188 * k + 1] == b"\x47" for k in range(5)):
            start = i; break
    else:
        raise RuntimeError("الملف مش MPEG-TS بحزم 188")
    f.seek(start)

    pmt_pid = None; vpid = apid = None
    pend = {}          # pid -> [chunks]
    mdat = open(tmp, "wb"); mpos = 0
    sps = pps = None
    sps_list = []; pps_list = []; ccs = {}; last_pk = {}; cc_stat = {"dup": 0, "gap": 0}
    vs = []            # (off,size,dts,pts,key)
    asamples = []      # (off,size)
    a_first_pts = None; a_cfg = None
    v_last_dts = v_last_pts = None
    v_examples = []
    v_stat = {"nopts": 0, "multi": 0, "damaged": 0, "lost": 0, "bound": 0, "tail": 0, "skipped": 0, "notime": 0}; damaged = [False]
    a_carry = [b""]; a_stat = {"pes": 0, "resync": 0, "split": 0, "last_pts": None}
    v_typ = [None]; v_shift = [0]; v_jumps = [0]; v_raw = [None, None]
    a_last = None
    done = 0; t0 = time.time(); last_print = 0

    seen_key = [False]
    es = bytearray(); es_off = [0]; es_total = [0]
    marks = []; mk_i = [0]; mk_uid = [0]
    cur = {"nals": [], "last_vcl": -1, "dts": None, "pts": None, "uid": None, "last_uid": None}
    fnst = {"ref": None, "bad": 0, "ex": [], "n": 0, "notime": 0}

    def emit(nals, dts, pts):
        nonlocal mpos
        key = any(t == 5 for t, _n in nals)
        if not seen_key[0]:
            if not key:
                v_stat["skipped"] += 1        # صور قبل أول فريم مفتاحي (مش هتتفك)
                return
            seen_key[0] = True
        # فحص تسلسل frame_num: لو في صورة ضايعة هيظهر هنا
        try:
            l2 = getattr(_t2m_parse_sps, "info", {}).get("l2fn")
            if l2:
                fn_nal = next(n_ for t_, n_ in nals if t_ in (1, 5))
                rr = _t2m_BR(fn_nal[1:12]); rr.ue(); rr.ue(); rr.ue()
                fnum = rr.bits(l2)
                M = 1 << l2
                fnst["n"] += 1
                if key:
                    exp = 0
                elif fnst["ref"] is None:
                    exp = fnum
                else:
                    exp = (fnst["ref"] + 1) % M
                if fnum != exp:
                    fnst["bad"] += 1
                    if len(fnst["ex"]) < 5:
                        fnst["ex"].append("pts=%.2f fn=%d متوقع=%d" % ((pts or 0) / 90000.0, fnum, exp))
                if (fn_nal[0] >> 5) & 3:
                    fnst["ref"] = fnum
        except Exception:
            pass
        data = b"".join(_t2m_u32(len(n_)) + n_ for _t, n_ in nals)
        mdat.write(data)
        vs.append((mpos, len(data), dts, pts, key)); mpos += len(data)

    def feed_nal(t, nal, mi):
        nonlocal sps, pps
        if t == 7:
            if nal not in sps_list and len(sps_list) < 31: sps_list.append(nal)
            if sps is None:
                sps = nal
                try: _t2m_parse_sps(sps)
                except Exception: pass
            return
        if t == 8:
            if nal not in pps_list and len(pps_list) < 255: pps_list.append(nal)
            if pps is None: pps = nal
            return
        if t in (9, 10, 11, 12):
            return
        if t in (1, 5):
            try:
                fm = _t2m_BR(nal[1:6]).ue()
            except Exception:
                fm = 0
            if fm == 0 and cur["last_vcl"] >= 0:
                lv = cur["last_vcl"]
                carry = cur["nals"][lv + 1:]
                emit(cur["nals"][:lv + 1], cur["dts"], cur["pts"])
                cur["nals"] = carry; cur["last_vcl"] = -1
            if cur["last_vcl"] < 0:
                m = marks[mi] if marks else (0, None, None, -1)
                if m[3] == cur["last_uid"]:
                    cur["dts"] = cur["pts"] = None; v_stat["multi"] += 1
                else:
                    cur["dts"], cur["pts"] = m[1], m[2]; cur["last_uid"] = m[3]
                    if m[1] is None: v_stat["notime"] += 1
            elif fm != 0 and False:
                pass
            cur["nals"].append((t, nal)); cur["last_vcl"] = len(cur["nals"]) - 1
        else:
            cur["nals"].append((t, nal))

    def drain(final=False):
        buf = es
        pos = [m.start() for m in re.finditer(b"\x00\x00\x01", buf)]
        if not pos:
            if final: buf.clear()
            return
        n = len(pos)
        last = n if final else n - 1
        for k in range(last):
            s0 = pos[k]
            e = pos[k + 1] if k + 1 < n else len(buf)
            nal = bytes(buf[s0 + 3:e])
            if k + 1 < n: nal = nal.rstrip(b"\x00")
            if not nal: continue
            ao = es_off[0] + s0
            while mk_i[0] + 1 < len(marks) and marks[mk_i[0] + 1][0] <= ao:
                mk_i[0] += 1
            feed_nal(nal[0] & 31, nal, mk_i[0])
        if final:
            buf.clear()
        else:
            cut = pos[n - 1]
            del buf[:cut]; es_off[0] += cut
        if mk_i[0] > 2000:
            del marks[:mk_i[0]]; mk_i[0] = 0

    def finish_video():
        drain(True)
        if cur["last_vcl"] >= 0:
            emit(cur["nals"][:cur["last_vcl"] + 1], cur["dts"], cur["pts"])
            cur["nals"] = []; cur["last_vcl"] = -1

    def flush(pid):
        nonlocal mpos, sps, pps, v_last_dts, v_last_pts, a_first_pts, a_cfg, a_last
        ch = pend.get(pid)
        if not ch: return
        data = b"".join(ch); pend[pid] = []
        pts, dts, pay = _t2m_parse_pes(data)
        if pid == vpid:
            if pts is None:
                v_stat["nopts"] += 1
                mdts = mpts = None
            else:
                pts = _t2m_unwrap(pts, v_raw[1]); dts = pts if dts is None else _t2m_unwrap(dts, v_raw[0])
                v_raw[0], v_raw[1] = dts, pts
                pts += v_shift[0]; dts += v_shift[0]
                if v_last_dts is not None:
                    step = dts - v_last_dts
                    if step < 0 or step > 90000 * 5:      # قفزة في الوقت (دمج أجزاء HLS): نكمّل من آخر صورة
                        shift = (v_last_dts + (v_typ[0] or 3000)) - dts
                        v_shift[0] += shift; v_jumps[0] += 1
                        pts += shift; dts += shift
                    elif step > 0:
                        v_typ[0] = step
                v_last_pts, v_last_dts = pts, dts
                mdts, mpts = dts, pts
            mk_uid[0] += 1
            marks.append((es_total[0], mdts, mpts, mk_uid[0]))
            es.extend(pay); es_total[0] += len(pay)
            drain(False)
        elif pid == apid:
            if pts is None and a_last is None: return
            if a_first_pts is None and pts is not None:
                a_first_pts = _t2m_unwrap(pts, None)
            if pts is not None:
                a_stat["pes"] += 1; a_stat["last_pts"] = pts
            pay = a_carry[0] + pay; a_carry[0] = b""
            p = 0
            while p + 7 <= len(pay):
                if pay[p] != 0xFF or (pay[p + 1] & 0xF0) != 0xF0:
                    nxt = pay.find(b"\xff", p + 1)
                    a_stat["resync"] += 1
                    if nxt < 0: p = len(pay); break
                    p = nxt; continue
                hdr = 7 if pay[p + 1] & 1 else 9
                flen = ((pay[p + 3] & 3) << 11) | (pay[p + 4] << 3) | (pay[p + 5] >> 5)
                if flen < hdr:
                    p += 1; a_stat["resync"] += 1; continue
                if p + flen > len(pay):
                    a_carry[0] = pay[p:]; a_stat["split"] += 1; p = len(pay); break
                if a_cfg is None:
                    aot = ((pay[p + 2] >> 6) & 3) + 1
                    sfi = (pay[p + 2] >> 2) & 15
                    ch_ = ((pay[p + 2] & 1) << 2) | (pay[p + 3] >> 6)
                    a_cfg = (aot, sfi, ch_)
                body = pay[p + hdr:p + flen]
                mdat.write(body)
                asamples.append((mpos, len(body))); mpos += len(body)
                a_last = True
                p += flen
            if p < len(pay) and not a_carry[0] and pay[p] == 0xFF:
                a_carry[0] = pay[p:]          # بقية قصيرة (أقل من هيدر كامل): نكمّلها مع الـ PES اللي بعده

    while True:
        if check: check(0, 0)
        buf = f.read(188 * 4096)
        if not buf: break
        done += len(buf)
        n = len(buf) // 188
        for i in range(n):
            o = i * 188
            if buf[o] != 0x47: continue
            pid = ((buf[o + 1] & 0x1F) << 8) | buf[o + 2]
            pusi = buf[o + 1] & 0x40
            afc = (buf[o + 3] >> 4) & 3
            if not (afc & 1): continue
            po = o + 4
            if afc & 2: po += 1 + buf[o + 4]
            if po >= o + 188: continue
            if pid == 0 and pmt_pid is None and pusi:
                q = po + 1 + buf[po]
                sl = ((buf[q + 1] & 15) << 8) | buf[q + 2]
                e = q + 3 + sl - 4
                r = q + 8
                while r + 4 <= e:
                    pn = (buf[r] << 8) | buf[r + 1]
                    if pn != 0:
                        pmt_pid = ((buf[r + 2] & 0x1F) << 8) | buf[r + 3]; break
                    r += 4
            elif pmt_pid is not None and pid == pmt_pid and vpid is None and pusi:
                q = po + 1 + buf[po]
                sl = ((buf[q + 1] & 15) << 8) | buf[q + 2]
                e = q + 3 + sl - 4
                pil = ((buf[q + 10] & 15) << 8) | buf[q + 11]
                r = q + 12 + pil
                while r + 5 <= e:
                    st = buf[r]; ep = ((buf[r + 1] & 0x1F) << 8) | buf[r + 2]
                    il = ((buf[r + 3] & 15) << 8) | buf[r + 4]
                    if st == 0x1B and vpid is None: vpid = ep
                    elif st in (0x0F, 0x11) and apid is None: apid = ep
                    elif st in (0x24, 0x02, 0x10) and vpid is None:
                        raise RuntimeError("الفيديو مش H.264 (نوع %#x) — مش مدعوم" % st)
                    r += 5 + il
                if vpid is None: raise RuntimeError("مفيش فيديو H.264 في الملف")
                pend[vpid] = []
                if apid is not None: pend[apid] = []
            elif pid == vpid or pid == apid:
                cc = buf[o + 3] & 15
                lc = ccs.get(pid)
                pk = buf[o:o + 188]
                if lc is not None:
                    if cc == lc and pk == last_pk.get(pid):
                        cc_stat["dup"] += 1; continue          # حزمة مكررة فعلًا (نفس البايتات)
                    if cc != (lc + 1) & 15:
                        if pusi:
                            v_stat["bound"] += 1               # بداية جزء HLS جديد (طبيعي)
                        else:
                            v_stat["lost"] += 1                # حزمة ضاعت في نص الفريم
                            if pid == vpid: damaged[0] = True
                ccs[pid] = cc; last_pk[pid] = pk
                if pusi: flush(pid)
                pend[pid].append(buf[po:o + 188])
        if time.time() - last_print > 3:
            last_print = time.time()
            log("%d%%  (فريمات: %d)" % (done * 100 // size, len(vs)))
    if vpid is not None: flush(vpid)
    finish_video()
    if apid is not None: flush(apid)
    mdat.close(); f.close()
    if not vs or sps is None or pps is None:
        raise RuntimeError("ماقدرتش ألاقي SPS/PPS أو فريمات فيديو")

    # صور اتجمّعت في PES واحد ومعاه وقت واحد: الوقت ده بتاع آخر صورة (الفجوة اللي قبله كانت ضعف)،
    # فنوزّع الصور بالتساوي بين الصورة اللي قبل الـ PES والوقت بتاعه
    _i = 0
    while _i < len(vs):
        _e = _i + 1
        while _e < len(vs) and vs[_e][2] is None: _e += 1
        _k = _e - _i
        if _k > 1 and vs[_i][2] is not None:
            _d = vs[_i][2]; _off = vs[_i][3] - vs[_i][2]
            _dp = vs[_i - 1][2] if _i > 0 and vs[_i - 1][2] is not None else _d - _k * (v_typ[0] or 1500)
            if _d - _dp < _k * 1:
                _dp = _d - _k * (v_typ[0] or 1500)
            for _m in range(_k):
                _dm = _dp + (_d - _dp) * (_m + 1) // _k
                _o = vs[_i + _m]
                vs[_i + _m] = (_o[0], _o[1], _dm, _dm + _off, _o[4])
        _i = _e
    vs[:] = [x for x in vs if x[2] is not None]
    pmin = min(x[3] for x in vs)

    # ---------------- stats
    try:
        _t2m_parse_sps(sps)
    except Exception:
        pass
    try:
        _d = [vs[i + 1][2] - vs[i][2] for i in range(len(vs) - 1)]
        _span = (vs[-1][2] - vs[0][2]) / 90000.0
        from collections import Counter
        _c = Counter(_d).most_common(4)
        log("--- تقرير ---")
        log("فريمات: %d | مدة الفيديو: %.1f ثانية | معدل: %.2f فريم/ثانية" % (len(vs), _span, len(vs) / max(_span, 1)))
        log("أكتر فروق بين الفريمات (ticks 90k): %s" % _c)
        log("SPS مختلفة: %d | PPS مختلفة: %d | حزم مكررة اتشالت: %d | معلومات SPS: %s" % (
            len(sps_list), len(pps_list), cc_stat["dup"], getattr(_t2m_parse_sps, "info", None)))
        log("بدايات أجزاء: %d | حزم ضايعة في نص فريم: %d | PES من غير PTS: %d | صور مالهاش وقت خاص (اتوزّع): %d | فريمات اتشالت قبل أول مفتاحي: %d" % (
            v_stat["bound"], v_stat["lost"], v_stat["nopts"], v_stat["multi"] + v_stat["notime"], v_stat["skipped"]))
        log("فحص frame_num: %d صورة اتفحصت | شاذ: %d" % (fnst["n"], fnst["bad"]))
        for _fx in fnst["ex"]:
            log("  frame_num شاذ: " + _fx)
        for _ex in v_examples:
            log("مثال PES متعدد: " + _ex)
        log("قفزات وقت اتصلّحت: %d | فريمات مفتاحية: %d" % (v_jumps[0], sum(1 for x in vs if x[4])))
        if a_cfg:
            log("الصوت: %d فريم | معدل العينة %d | مدة تقريبية %.1f ثانية" % (len(asamples), _t2m_SR[a_cfg[1]], len(asamples) * 1024.0 / _t2m_SR[a_cfg[1]]))
            if a_stat["last_pts"] is not None and a_first_pts is not None:
                log("مدة الصوت من الـ PTS: %.1f ثانية | PES: %d | إعادة مزامنة: %d | فريمات مقسومة: %d" % (
                    (_t2m_unwrap(a_stat["last_pts"], a_first_pts) - a_first_pts) / 90000.0,
                    a_stat["pes"], a_stat["resync"], a_stat["split"]))
            log("فرق بداية الصوت عن الفيديو: %.3f ثانية" % ((a_first_pts - pmin) / 90000.0) if a_first_pts is not None else "")
    except Exception as _e:
        log("stats error: %s" % _e)

    # ---------------- timeline
    width, height = _t2m_parse_sps(sps)
    vts = 90000
    base_v = vs[0][2]
    pmin = min(x[3] for x in vs)
    v_start = pmin - base_v          # أول فريم بيتعرض بعد أول dts بالمقدار ده (B-frames)
    a_sr = _t2m_SR[a_cfg[1]] if a_cfg else 0
    # بداية الصوت بالنسبة للفيديو (بالـ 90k)
    a_off90 = (a_first_pts - pmin) if (a_first_pts is not None and a_cfg) else 0
    base = min(0, a_off90)           # لو الصوت بيبدأ قبل الفيديو
    v_off90 = -base
    a_off90 -= base
    v_dts = [x[2] - base_v for x in vs]
    durs = [max(0, v_dts[i + 1] - v_dts[i]) for i in range(len(vs) - 1)]
    durs.append(durs[-1] if durs else 3000)
    v_total = max(v_dts[-1] + durs[-1], max(x[3] for x in vs) - base_v + durs[-1])
    v_pres = v_total - v_start
    a_total = len(asamples) * 1024
    MVTS = 1000
    v_dur_mv = int((v_pres + v_off90) * MVTS / vts)
    a_dur_mv = int((a_total * 90000.0 / a_sr + a_off90) * MVTS / vts) if a_sr else 0
    mv_dur = max(v_dur_mv, a_dur_mv)

    def rle(vals):
        res = []
        for v in vals:
            if res and res[-1][1] == v: res[-1][0] += 1
            else: res.append([1, v])
        return res

    def build_stbl_v(chunk_base):
        stts = _t2m_fbox(b"stts", 0, 0, _t2m_u32(len(rle(durs))), b"".join(_t2m_u32(c) + _t2m_u32(d) for c, d in rle(durs)))
        ctt = [x[3] - x[2] for x in vs]
        ctts = b""
        if any(c != 0 for c in ctt):
            neg = any(c < 0 for c in ctt)
            r_ = rle(ctt)
            ctts = _t2m_fbox(b"ctts", 1 if neg else 0, 0, _t2m_u32(len(r_)),
                        b"".join(_t2m_u32(c) + (struct.pack(">i", d) if neg else _t2m_u32(d)) for c, d in r_))
        keys = [i + 1 for i, x in enumerate(vs) if x[4]]
        stss = _t2m_fbox(b"stss", 0, 0, _t2m_u32(len(keys)), b"".join(_t2m_u32(k) for k in keys))
        stsc = _t2m_fbox(b"stsc", 0, 0, _t2m_u32(1), _t2m_u32(1) + _t2m_u32(1) + _t2m_u32(1))
        stsz = _t2m_fbox(b"stsz", 0, 0, _t2m_u32(0), _t2m_u32(len(vs)), b"".join(_t2m_u32(x[1]) for x in vs))
        stco = _t2m_fbox(b"stco", 0, 0, _t2m_u32(len(vs)), b"".join(_t2m_u32(chunk_base + x[0]) for x in vs))
        avcc = _t2m_box(b"avcC", bytes([1, sps[1], sps[2], sps[3], 0xFF, 0xE0 | len(sps_list)]) +
                   b"".join(_t2m_u16(len(x)) + x for x in sps_list) +
                   bytes([len(pps_list)]) + b"".join(_t2m_u16(len(x)) + x for x in pps_list))
        avc1 = _t2m_box(b"avc1", b"\x00" * 6, _t2m_u16(1), b"\x00" * 16, _t2m_u16(width), _t2m_u16(height),
                   _t2m_u32(0x00480000), _t2m_u32(0x00480000), _t2m_u32(0), _t2m_u16(1), b"\x00" * 32, _t2m_u16(0x18),
                   struct.pack(">h", -1), avcc)
        stsd = _t2m_fbox(b"stsd", 0, 0, _t2m_u32(1), avc1)
        return _t2m_box(b"stbl", stsd, stts, ctts, stss, stsc, stsz, stco)

    def build_stbl_a(chunk_base):
        aot, sfi, ch_ = a_cfg
        asc = bytes([(aot << 3) | (sfi >> 1), ((sfi & 1) << 7) | (ch_ << 3)])
        dsi = bytes([5, len(asc)]) + asc
        dcd = bytes([4, 13 + len(dsi), 0x40, 0x15]) + b"\x00\x00\x00" + _t2m_u32(0) + _t2m_u32(0) + dsi
        es = bytes([3, 3 + len(dcd) + 3, 0, 0, 0]) + dcd + bytes([6, 1, 2])
        esds = _t2m_fbox(b"esds", 0, 0, es)
        mp4a = _t2m_box(b"mp4a", b"\x00" * 6, _t2m_u16(1), b"\x00" * 8, _t2m_u16(ch_ or 2), _t2m_u16(16), _t2m_u32(0),
                   _t2m_u32(a_sr << 16), esds)
        stsd = _t2m_fbox(b"stsd", 0, 0, _t2m_u32(1), mp4a)
        stts = _t2m_fbox(b"stts", 0, 0, _t2m_u32(1), _t2m_u32(len(asamples)) + _t2m_u32(1024))
        stsc = _t2m_fbox(b"stsc", 0, 0, _t2m_u32(1), _t2m_u32(1) + _t2m_u32(1) + _t2m_u32(1))
        stsz = _t2m_fbox(b"stsz", 0, 0, _t2m_u32(0), _t2m_u32(len(asamples)), b"".join(_t2m_u32(x[1]) for x in asamples))
        stco = _t2m_fbox(b"stco", 0, 0, _t2m_u32(len(asamples)), b"".join(_t2m_u32(chunk_base + x[0]) for x in asamples))
        return _t2m_box(b"stbl", stsd, stts, stsc, stsz, stco)

    def trak(tid, kind, ts, dur_media, dur_mv, off_mv, stbl, w=0, h=0, mt=0):
        tkhd = _t2m_fbox(b"tkhd", 0, 7, _t2m_u32(0), _t2m_u32(0), _t2m_u32(tid), _t2m_u32(0), _t2m_u32(dur_mv), b"\x00" * 8,
                    _t2m_u16(0), _t2m_u16(0), _t2m_u16(0x0100 if kind == "soun" else 0), _t2m_u16(0),
                    _t2m_u32(0x10000) + _t2m_u32(0) * 3 + _t2m_u32(0x10000) + _t2m_u32(0) * 3 + _t2m_u32(0x40000000),
                    _t2m_u32(w << 16), _t2m_u32(h << 16))
        ents = []
        if off_mv > 0: ents.append(_t2m_u32(off_mv) + struct.pack(">i", -1) + _t2m_u32(0x10000))
        ents.append(_t2m_u32(max(0, dur_mv - off_mv)) + _t2m_u32(mt) + _t2m_u32(0x10000))
        elst = _t2m_fbox(b"elst", 0, 0, _t2m_u32(len(ents)), b"".join(ents))
        edts = _t2m_box(b"edts", elst)
        mdhd = _t2m_fbox(b"mdhd", 0, 0, _t2m_u32(0), _t2m_u32(0), _t2m_u32(ts), _t2m_u32(dur_media), _t2m_u16(0x55C4), _t2m_u16(0))
        hdlr = _t2m_fbox(b"hdlr", 0, 0, _t2m_u32(0), kind.encode(), _t2m_u32(0) * 3, b"NovaGet\x00")
        mh = _t2m_fbox(b"vmhd", 0, 1, _t2m_u16(0) * 4) if kind == "vide" else _t2m_fbox(b"smhd", 0, 0, _t2m_u16(0), _t2m_u16(0))
        dinf = _t2m_box(b"dinf", _t2m_fbox(b"dref", 0, 0, _t2m_u32(1), _t2m_fbox(b"url ", 0, 1)))
        minf = _t2m_box(b"minf", mh, dinf, stbl)
        return _t2m_box(b"trak", tkhd, edts, _t2m_box(b"mdia", mdhd, hdlr, minf))

    def build_moov(chunk_base):
        tr = [trak(1, "vide", vts, v_total, v_dur_mv, int(v_off90 * MVTS / vts), build_stbl_v(chunk_base), width, height, v_start)]
        if a_cfg and asamples:
            tr.append(trak(2, "soun", a_sr, a_total, a_dur_mv, int(a_off90 * MVTS / vts), build_stbl_a(chunk_base)))
        mvhd = _t2m_fbox(b"mvhd", 0, 0, _t2m_u32(0), _t2m_u32(0), _t2m_u32(MVTS), _t2m_u32(mv_dur), _t2m_u32(0x10000), _t2m_u16(0x100),
                    b"\x00" * 10, _t2m_u32(0x10000) + _t2m_u32(0) * 3 + _t2m_u32(0x10000) + _t2m_u32(0) * 3 + _t2m_u32(0x40000000),
                    b"\x00" * 24, _t2m_u32(len(tr) + 1))
        return _t2m_box(b"moov", mvhd, *tr)

    ftyp = _t2m_box(b"ftyp", b"isom", _t2m_u32(512), b"isomiso2avc1mp41")
    moov0 = build_moov(0)
    base_off = len(ftyp) + len(moov0) + 8
    if base_off + mpos >= 1 << 32:
        raise RuntimeError("الملف أكبر من 4GB — مش مدعوم")
    moov = build_moov(base_off)
    assert len(moov) == len(moov0)
    with open(out, "wb") as o:
        o.write(ftyp); o.write(moov); o.write(_t2m_u32(8 + mpos) + b"mdat")
        with open(tmp, "rb") as m:
            while True:
                c = m.read(1 << 20)
                if not c: break
                o.write(c)
    os.remove(tmp)
    return out, len(vs), len(asamples), (width, height)



def _t2m_convert(path, out, log=print, check=None):
    """TS -> MP4 بكود بايثون صرف (H.264 + AAC). بيمسح الملفات المؤقتة لو فشل أو اتلغى."""
    try:
        sv = os.statvfs(os.path.dirname(os.path.abspath(out)) or ".")
        free = sv.f_bavail * sv.f_frsize
        if free < os.path.getsize(path) * 2.2:
            raise RuntimeError("no free space for TS conversion (%d MB free)" % (free // 1048576))
    except AttributeError:
        pass
    try:
        return _t2m_convert_raw(path, out, log, check)
    except BaseException:
        for _p in (out, out + ".mdat.tmp"):
            try:
                if os.path.isfile(_p):
                    os.remove(_p)
            except Exception:
                pass
        raise


def is_mpegts(path):
    """هل الملف MPEG-TS (حزم 188 بايت بعلامة 0x47)؟ حتى لو امتداده mp4."""
    try:
        with open(path, "rb") as f:
            h = f.read(188 * 6 + 188)
        for i in range(188):
            if len(h) > i + 188 * 5 and all(h[i + 188 * k] == 0x47 for k in range(6)):
                return True
    except Exception:
        pass
    return False


def ts_to_mp4_inplace(path, check=None):
    """يحوّل ملف TS (متسمّي mp4) لـ MP4 حقيقي بفهرس في الأول، من غير إعادة ترميز. True لو نجح."""
    tmp = os.path.splitext(path)[0] + ".nvg_ts.mp4"
    try:
        def _lg(m):
            if not re.match(r"^\d+%", m):
                nlog("ts2mp4: " + str(m))
        _t2m_convert(path, tmp, _lg, check)
        if os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, path)
            return True
    except (Cancelled, Paused):
        raise
    except Exception as ex:
        nlog("ts2mp4 failed: %s" % ex)
    try:
        if os.path.isfile(tmp):
            os.remove(tmp)
    except Exception:
        pass
    return False




def ensure_seekable(path, check=None):
    """بعد التحميل: لو الفيديو MP4 مش seekable، يعيد تغليفه (من غير إعادة ترميز) بفهرس في الأول."""
    try:
        if not (path and os.path.isfile(path)):
            return
        if os.path.splitext(path)[1].lower() not in (".mp4", ".m4v", ".mov"):
            return
        if not mp4_needs_fix(path):
            return
        if is_mpegts(path):
            # ملف بث HLS (MPEG-TS) متسمّي mp4: ffmpeg لو موجود، وإلا المحوّل الداخلي، وإلا PyAV
            if HAS_FFMPEG and ffmpeg_faststart(path, check=check):
                return
            if ts_to_mp4_inplace(path, check=check):
                return
        else:
            if py_faststart(path):
                return
            if ffmpeg_faststart(path, check=check):
                return
        if not HAS_AV:
            return
        tmp = os.path.splitext(path)[0] + ".nvg_fix.mp4"
        ic = pyav.open(path)
        try:
            oc = pyav.open(tmp, "w", format="mp4", options={"movflags": "+faststart"})
        except Exception:
            oc = pyav.open(tmp, "w", format="mp4")
        try:
            sts = []
            if ic.streams.video:
                sts.append(ic.streams.video[0])
            if ic.streams.audio:
                sts.append(ic.streams.audio[0])
            if not sts:
                return
            mp = {}
            for st in sts:
                if hasattr(oc, "add_stream_from_template"):
                    mp[st.index] = oc.add_stream_from_template(st)
                else:
                    mp[st.index] = oc.add_stream(template=st)
            for pkt in ic.demux(*sts):
                if pkt.dts is None:
                    continue
                if check:
                    check(0, 0)
                pkt.stream = mp[pkt.stream.index]
                oc.mux(pkt)
        finally:
            try:
                oc.close()
            finally:
                ic.close()
        if os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, path)
    except (Cancelled, Paused):
        raise
    except Exception:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass


def av_to_mp3(src, dst, kbps, check=None, prog=None, title=None):
    """يحوّل أي ملف صوت (M4A/WebM...) إلى MP3 باستخدام PyAV."""
    inp = pyav.open(src)
    try:
        ain = inp.streams.audio[0]
        sr = int(getattr(ain, "rate", 0) or 44100)
        if sr not in (32000, 44100, 48000):
            sr = 44100
        out = pyav.open(dst, "w", format="mp3")
        try:
            ost = out.add_stream("libmp3lame", rate=sr)
            try:
                ost.codec_context.bit_rate = int(kbps) * 1000
            except Exception:
                ost.bit_rate = int(kbps) * 1000
            try:
                ost.layout = "stereo"
            except Exception:
                pass
            if title:
                try:
                    out.metadata["title"] = str(title)
                except Exception:
                    pass
            for frame in inp.decode(ain):
                if check:
                    check()
                for pkt in ost.encode(frame):
                    out.mux(pkt)
                if prog and frame.time is not None:
                    prog(frame.time)
            for pkt in ost.encode(None):
                out.mux(pkt)
        finally:
            out.close()
    finally:
        inp.close()


def _ytdlp_cache_dir():
    """مجلد كاش لـ yt-dlp (بيوفر تحميل ملفات اليوتيوب في كل تحليل)."""
    try:
        d = os.path.join(data_dir(), "ytdlp_cache")
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        return None


YT_READY = threading.Event()      # بيتفعّل لما yt-dlp يخلّص تحميل الـ extractors (أول مرة بتاخد وقت طويل على الموبايل)


def warm_up_ytdlp():
    """تسخين yt-dlp في الخلفية عند فتح البرنامج (أول تحليل كان بياخد وقت في تحميل الإضافات)."""
    try:
        from yt_dlp.extractor import gen_extractor_classes
        gen_extractor_classes()
        yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "cachedir": _ytdlp_cache_dir()}).close()
    except Exception:
        pass
    YT_READY.set()
    # أول تحليل ليوتيوب بيحمّل ويحلّل سكربت المشغّل (ثواني طويلة) ثم بيتخزّن؛ بنعملها مرة بالخلفية
    try:
        marker = os.path.join(_ytdlp_cache_dir() or data_dir(), "yt_warm.flag")
        if net_kind() != "mobile" and not (os.path.isfile(marker) and time.time() - os.path.getmtime(marker) < 86400):
            time.sleep(6)
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True,
                                   "logger": QuietLogger(), "socket_timeout": 20, "retries": 1,
                                   "cachedir": _ytdlp_cache_dir(),
                                   "skip_download": True}) as _y:
                _y.extract_info("https://www.youtube.com/watch?v=jNQXAC9IVRw", download=False)
            with open(marker, "w") as _f:
                _f.write(str(time.time()))
    except Exception:
        pass


class QuietLogger(object):
    """يمنع yt-dlp من الطباعة في الكونسول (التقدم بيظهر في واجهة البرنامج بس)."""

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def media_scan(path, force=False):
    """يبلّغ أندرويد بالملف الجديد عشان يظهر في المعرض ومشغّلات الفيديو والصوت."""
    if not path or (not force and not os.path.isfile(path)):
        return
    try:
        import mimetypes
        from jnius import autoclass
        pa = None
        for name in ("org.kivy.android.PythonActivity", "org.renpy.android.PythonActivity"):
            try:
                pa = autoclass(name)
                break
            except Exception:
                continue
        if pa is None or pa.mActivity is None:
            return
        mime = mimetypes.guess_type(path)[0]
        if not mime:
            ext = os.path.splitext(path)[1].lower()
            if ext in (".jpg", ".jpeg"):
                mime = "image/jpeg"
            elif ext == ".png":
                mime = "image/png"
            elif ext == ".webp":
                mime = "image/webp"
            elif ext == ".m4a":
                mime = "audio/mp4"
            elif ext == ".mp4":
                mime = "video/mp4"
        autoclass("android.media.MediaScannerConnection").scanFile(
            pa.mActivity, [path], [mime] if mime else None, None)
    except Exception:
        pass


def remove_thumb_leftovers(path):
    """بيمسح أي صورة بنفس اسم الفيديو/الصوت اللي لسه اتحمّل (الصورة المصغّرة)."""
    if not path:
        return
    stem = os.path.splitext(path)[0]
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        for cand in (stem + ext, stem + ".nvg_thumb" + ext):
            try:
                if os.path.isfile(cand) and os.path.abspath(cand) != os.path.abspath(path):
                    os.remove(cand)
            except Exception:
                pass


def cleanup_temp_files(directory, keep_path=None):
    """Remove only NovaGet-marked temporary files; never touch generic partial files.

    yt-dlp and other apps can create names such as video.mp4.part in the same
    public Download folder. Those names are not sufficient proof that NovaGet
    owns them, so they are deliberately preserved.
    """
    if not directory or not os.path.isdir(directory):
        return
    keep = os.path.abspath(keep_path) if keep_path else None
    dirs = [directory]
    for sub in (SUB_VIDEO, SUB_IMAGE, SUB_AUDIO):
        sd = os.path.join(directory, sub)
        if os.path.isdir(sd):
            dirs.append(sd)
    for dd in dirs:
        try:
            for name in os.listdir(dd):
                if ".nvg_" not in name:
                    continue
                path = os.path.join(dd, name)
                if keep and os.path.abspath(path) == keep:
                    continue
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except Exception as exc:
                    if DEBUG_LOG:
                        print("NovaGet temp cleanup failed:", name, str(exc)[:120])
        except Exception as exc:
            if DEBUG_LOG:
                print("NovaGet temp directory scan failed:", str(exc)[:160])


def open_path(path):
    """يفتح ملف أو مجلد على أندرويد/سطح المكتب."""
    if not path:
        return False
    try:
        if os.path.isfile(path):
            folder = os.path.dirname(path)
        elif os.path.isdir(path):
            folder = path
            path = None
        else:
            return False
        try:
            from jnius import autoclass, cast
            pa = None
            for name in ("org.kivy.android.PythonActivity", "org.renpy.android.PythonActivity"):
                try:
                    pa = autoclass(name)
                    break
                except Exception:
                    continue
            if pa is not None and pa.mActivity is not None:
                Intent = autoclass("android.content.Intent")
                Uri = autoclass("android.net.Uri")
                File = autoclass("java.io.File")
                if path and os.path.isfile(path):
                    intent = Intent(Intent.ACTION_VIEW)
                    f = File(path)
                    uri = Uri.fromFile(f)
                    import mimetypes
                    mime = mimetypes.guess_type(path)[0] or "*/*"
                    intent.setDataAndType(uri, mime)
                    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    pa.mActivity.startActivity(intent)
                else:
                    intent = Intent(Intent.ACTION_VIEW)
                    intent.setDataAndType(Uri.fromFile(File(folder)), "resource/folder")
                    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    try:
                        pa.mActivity.startActivity(intent)
                    except Exception:
                        # fallback: open Downloads via file manager intent
                        intent = Intent(Intent.ACTION_VIEW)
                        intent.setData(Uri.parse("content://com.android.externalstorage.documents/document/primary:Download"))
                        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        pa.mActivity.startActivity(intent)
                return True
        except Exception:
            pass
        # desktop fallback
        import subprocess
        import sys
        target = path or folder
        if sys.platform.startswith("win"):
            os.startfile(target)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
        return True
    except Exception:
        return False


def _http_get(url, timeout=20):
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Linux; Android 13; Mobile) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Mobile Safari/537.36"),
        "Accept": "text/html,application/json,*/*",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _unique_images(items):
    seen, out = set(), []
    for it in items:
        u = (it.get("url") or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(it)
    return out


def _is_video_site(info):
    """مواقع الفيديو العادية — thumbnails بتاعتها مش صور للمنشور."""
    key = (info.get("extractor_key") or info.get("ie_key") or info.get("extractor") or "").lower()
    return any(x in key for x in (
        "youtube", "youtu", "vimeo", "dailymotion", "twitch",
        "facebook", "twitter", "x.com", "reddit", "bilibili",
        "streamable", "rumble", "odysee",
    ))


def extract_images_from_info(info):
    """يستخرج صور المنشور فقط (كاروسيل إنستجرام/...)، مش thumbnails فيديو."""
    if not info:
        return []
    key = (info.get("extractor_key") or info.get("ie_key") or info.get("extractor") or "").lower()
    if any(x in key for x in ("youtube", "youtu", "vimeo", "dailymotion", "twitch",
                              "bilibili", "streamable", "rumble", "odysee")):
        return []
    out = []

    def add(url, w=0, h=0, n=None):
        if not url or not isinstance(url, str) or not url.startswith("http"):
            return
        ext = "JPG"
        low = url.lower()
        if ".png" in low:
            ext = "PNG"
        elif ".webp" in low:
            ext = "WEBP"
        out.append(dict(
            kind="img", url=url, label="#%d" % (n or len(out) + 1),
            ext=ext, size=None, fid="img:%s" % url, h=int(h or 0),
            w=int(w or 0), tbr=0, codec="", fps=0, sel=True,
        ))

    # كاروسيل: كل entry صورة مستقلة (إنستجرام وغيره)
    entries = info.get("entries")
    if entries:
        img_entries = 0
        for i, e in enumerate(entries, 1):
            if not isinstance(e, dict):
                continue
            ext = (e.get("ext") or "").lower()
            # صورة حقيقية فقط — مش فيديو جوه playlist
            if ext in ("jpg", "jpeg", "png", "webp"):
                u = e.get("url") or ""
                if u.startswith("http"):
                    add(u, e.get("width"), e.get("height"), i)
                    img_entries += 1
                    continue
            # إنستجرام أحيانًا يحط الصورة في formats
            for f in (e.get("formats") or []):
                fext = (f.get("ext") or "").lower()
                if fext in ("jpg", "jpeg", "png", "webp") and f.get("url"):
                    add(f["url"], f.get("width"), f.get("height"), i)
                    img_entries += 1
                    break
        if img_entries >= 1:
            return _unique_images(out)

    # صيغ صورة مباشرة في المنشور (من غير فيديو)
    has_video = False
    for f in (info.get("formats") or []):
        v, a = f.get("vcodec") or "none", f.get("acodec") or "none"
        if v != "none":
            has_video = True
        fext = (f.get("ext") or "").lower()
        if fext in ("jpg", "jpeg", "png", "webp") and f.get("url"):
            add(f["url"], f.get("width"), f.get("height"))
    # لو فيه فيديو حقيقي، متعتبرش الصيغ دي كاروسيل صور
    if has_video:
        return []
    return _unique_images(out)


def _find_image_post(obj, depth=0):
    """بحث متكرر عن imagePost في JSON تيك توك."""
    if depth > 8 or obj is None:
        return None
    if isinstance(obj, dict):
        if "imagePost" in obj and isinstance(obj.get("imagePost"), dict):
            imgs = (obj["imagePost"] or {}).get("images")
            if imgs:
                return obj
        for v in obj.values():
            found = _find_image_post(v, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj[:50]:
            found = _find_image_post(v, depth + 1)
            if found:
                return found
    return None


def extract_tiktok_images(url):
    """يستخرج صور منشور تيك توك (photo carousel) من JSON الصفحة."""
    if not url:
        return [], None
    low = url.lower()
    if "tiktok" not in low and "vm.tiktok" not in low and "vt.tiktok" not in low:
        return [], None
    try:
        page_url = url.strip()
        # حل الروابط القصيرة
        if re.search(r"(vm|vt)\.tiktok\.com/", page_url, re.I):
            import urllib.request
            req = urllib.request.Request(page_url, headers={
                "User-Agent": "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36",
            })
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    page_url = r.geturl() or page_url
            except Exception:
                pass
        html = _http_get(page_url).decode("utf-8", "replace")
    except Exception:
        return [], None

    data = None
    for pat in (
        r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        r'<script[^>]+id="SIGI_STATE"[^>]*>(.*?)</script>',
        r'<script[^>]+id="sigi-persisted-data"[^>]*>(.*?)</script>',
    ):
        m = re.search(pat, html, re.S | re.I)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
            break
        except Exception:
            data = None
    if not data:
        # محاولة أخيرة: imagePost مباشرة في الـ HTML
        m = re.search(r'"imagePost"\s*:\s*(\{.*?"images"\s*:\s*\[.*?\]\s*\})', html, re.S)
        if m:
            try:
                data = {"imagePost": json.loads(m.group(1))}
            except Exception:
                data = None
    if not data:
        return [], None

    item = _find_image_post(data)
    if not item:
        # مسار معروف
        if isinstance(data, dict):
            scope = data.get("__DEFAULT_SCOPE__") or data
            for key in ("webapp.video-detail", "webapp.photo-detail"):
                detail = scope.get(key) or {}
                item = ((detail.get("itemInfo") or {}).get("itemStruct")
                        or detail.get("itemStruct"))
                if item and item.get("imagePost"):
                    break
                item = None

    if not item or not isinstance(item, dict):
        return [], None

    title = item.get("desc") or item.get("title") or "TikTok"
    author = ""
    auth = item.get("author") or {}
    if isinstance(auth, dict):
        author = auth.get("uniqueId") or auth.get("nickname") or ""
    images = []
    img_post = item.get("imagePost") or {}
    for i, im in enumerate(img_post.get("images") or [], 1):
        if not isinstance(im, dict):
            continue
        urls = ((im.get("imageURL") or {}).get("urlList") or [])
        if not urls:
            urls = im.get("urlList") or []
        if not urls and isinstance(im.get("imageURL"), str):
            urls = [im["imageURL"]]
        if not urls:
            continue
        # أعلى جودة غالبًا آخر رابط أو أطول واحد
        best = max(urls, key=lambda u: len(u) if isinstance(u, str) else 0)
        w = (im.get("imageWidth") or im.get("width") or 0)
        h = (im.get("imageHeight") or im.get("height") or 0)
        images.append(dict(
            kind="img", url=best, label="#%d" % i, ext="JPG", size=None,
            fid="img:%s" % best, h=int(h or 0), w=int(w or 0),
            tbr=0, codec="", fps=0, sel=True,
        ))
    return _unique_images(images), {"title": title, "author": author}



# ------------------------------------------------------------------ social images
_UA_MOBILE = ("Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")
_UA_FB = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
_IMG_EXT_RE = re.compile(r"\.(jpe?g|png|webp)(\?|$|:)", re.I)


def _http_get_ex(url, ua=_UA_MOBILE, timeout=20, extra=None):
    """زي _http_get بس بيرجّع (bytes, final_url) وبيسمح بـ User-Agent مخصص."""
    import urllib.request
    hdrs = {"User-Agent": ua,
            "Accept": "text/html,application/json,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8"}
    if extra:
        hdrs.update(extra)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), (r.geturl() or url)


def _unesc_url(u):
    """بيفك الـ escaping اللي بيجي في JSON جوه الـ HTML."""
    import html as _html
    for _ in range(3):
        u = u.replace("\\\\", "\\")
    u = (u.replace("\\/", "/").replace("\\u0026", "&").replace("\\u003d", "=")
          .replace("\\u0025", "%"))
    u = _html.unescape(u).strip("\\\" ")
    return u


def _mk_img(urls_meta, ref=None):
    """urls_meta: list of (url, w, h) -> عناصر صور جاهزة للتطبيق."""
    out = []
    for n, (u, w, h) in enumerate(urls_meta, 1):
        if not u or not u.startswith("http"):
            continue
        low = u.lower()
        ext = "PNG" if ".png" in low else ("WEBP" if ".webp" in low else "JPG")
        out.append(dict(kind="img", url=u, label="#%d" % n, ext=ext, size=None,
                        fid="img:%s" % u, h=int(h or 0), w=int(w or 0),
                        tbr=0, codec="", fps=0, sel=True, ref=ref))
    return _unique_images(out)


def _og_from_html(html):
    """og:image / twitter:image + العنوان من أي صفحة."""
    import html as _html
    imgs = []
    pat1 = (r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::secure_url|:url)?|'
            r'twitter:image(?::src)?)["\'][^>]*?content=["\']([^"\']+)["\']')
    pat2 = (r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*?(?:property|name)=["\']'
            r'(?:og:image(?::secure_url|:url)?|twitter:image(?::src)?)["\']')
    for pat in (pat1, pat2):
        for m in re.finditer(pat, html, re.I):
            imgs.append(_html.unescape(m.group(1)).strip())
    title = ""
    m = (re.search(r'<meta[^>]+property=["\']og:title["\'][^>]*?content=["\']([^"\']*)["\']', html, re.I)
         or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*?property=["\']og:title["\']', html, re.I)
         or re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S))
    if m:
        title = _html.unescape(m.group(1)).strip()
    return imgs, title


FETCH_LOG = []        # خطوات آخر محاولة تحليل رابط (بتظهر في نافذة الخطأ عشان تتبعت للتشخيص)


def flog(msg):
    try:
        FETCH_LOG.append(str(msg)[:400])
    except Exception:
        pass


SOCIAL_DIAG = [""]     # آخر سبب فشل في استخراج صور السوشيال (بيظهر للمستخدم)
_IG_ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _ig_shortcode_to_pk(code):
    n = 0
    for ch in code[:11]:
        n = n * 64 + _IG_ALPHA.index(ch)
    return n


def _ig_session_value():
    sid = (CFG.get("ig_session") or "").strip()
    if sid.lower().startswith("sessionid="):
        sid = sid.split("=", 1)[1]
    return sid.split(";")[0].strip()


def ig_cookiefile():
    """ملف cookies بصيغة Netscape عشان yt-dlp (لو المستخدم حاطط sessionid)."""
    sid = _ig_session_value()
    if not sid:
        return None
    try:
        path = os.path.join(tempfile.gettempdir(), "novaget_ig_cookies.txt")
        jar = {}
        for part in (CFG.get("ig_cookies") or "").split(";"):
            nm, _eq, val = part.strip().partition("=")
            if nm and val and "\t" not in val:
                jar[nm] = val
        jar["sessionid"] = sid
        with open(path, "w") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for nm, val in jar.items():
                f.write(".instagram.com\tTRUE\t/\tTRUE\t2147483647\t%s\t%s\n" % (nm, val))
        return path
    except Exception:
        return None


def _ig_api_images(code):
    """كل صور البوست/الكاروسيل عن طريق API إنستجرام + sessionid بتاع المستخدم."""
    sid = _ig_session_value()
    if not sid:
        return [], None
    try:
        pk = _ig_shortcode_to_pk(code)
    except ValueError:
        return [], None
    last = ""
    for host, ua in (("www.instagram.com", _UA_MOBILE),
                     ("i.instagram.com", "Instagram 275.0.0.27.98 Android (33/13; 420dpi; 1080x2400; "
                                         "samsung; SM-G991B; o1s; exynos2100; en_US; 458229237)")):
        try:
            raw, _ = _http_get_ex(
                "https://%s/api/v1/media/%d/info/" % (host, pk), ua=ua, extra={
                    "X-IG-App-ID": "936619743392459",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://www.instagram.com/p/%s/" % code,
                    "Cookie": "sessionid=%s" % sid,
                })
            js = json.loads(raw.decode("utf-8", "replace"))
        except Exception as ex:
            last = "IG API: %s" % str(ex)[:80]
            continue
        items = js.get("items") or []
        if not items:
            last = "IG API: %s" % str(js.get("message") or js.get("status") or "empty")[:80]
            continue
        it = items[0]
        nodes = it.get("carousel_media") or [it]
        metas = []
        for nd in nodes:
            if nd.get("media_type") == 2:      # فيديو جوه الكاروسيل: مش صورة
                continue
            cands = ((nd.get("image_versions2") or {}).get("candidates")) or []
            if not cands:
                continue
            best = max(cands, key=lambda c: (c.get("width") or 0) * (c.get("height") or 0))
            metas.append((best.get("url"), best.get("width"), best.get("height")))
        if metas:
            cap = ((it.get("caption") or {}).get("text") or "").strip().split("\n")[0]
            user = (it.get("user") or {}).get("username") or ""
            return _mk_img(metas), {"title": (cap or ("Instagram_" + user))[:60], "author": user}
        last = "IG API: no images in response"
    SOCIAL_DIAG[0] = last
    return [], None


_IG_APP_UA = ("Instagram 275.0.0.27.98 Android (33/13; 420dpi; 1080x2400; "
              "samsung; SM-G991B; o1s; exynos2100; en_US; 458229237)")


def _ig_json(url, ref):
    """طلب JSON من API إنستجرام بكوكيز المستخدم. بيرجّع (json, سبب_الفشل)."""
    ck = (CFG.get("ig_cookies") or "").strip() or ("sessionid=%s" % _ig_session_value())
    extra = {"X-IG-App-ID": "936619743392459", "X-Requested-With": "XMLHttpRequest",
             "Referer": ref, "Cookie": ck}
    last = ""
    urls = [url]
    if "://i.instagram.com/" in url:
        urls.append(url.replace("://i.instagram.com/", "://www.instagram.com/"))
    for u in urls:
        for ua in (_IG_APP_UA, _UA_MOBILE):
            try:
                raw, _ = _http_get_ex(u, ua=ua, extra=extra)
                return json.loads(raw.decode("utf-8", "replace")), ""
            except Exception as ex:
                last = str(ex)[:90]
    return None, last


def _area(c):
    return (c.get("width") or 0) * (c.get("height") or 0)


def _ig_story_items(url):
    """ستوري/هاي لايت إنستجرام (صور + فيديوهات) عن طريق API + كوكيز المستخدم."""
    import urllib.parse
    single = None
    flog("IG story: %s" % url)
    if not _ig_session_value():
        flog("IG story: no sessionid saved (login needed)")
        SOCIAL_DIAG[0] = ("الستوري محتاج تسجيل دخول إنستجرام من الإعدادات"
                          if STATE["lang"] == "ar" else
                          "Stories need an Instagram login (Settings)")
        return [], None
    user, reel = "", None
    m = re.search(r"instagram\.com/stories/highlights/(\d+)", url)
    if m:
        reel = "highlight:%s" % m.group(1)
    else:
        m = re.search(r"instagram\.com/stories/([^/?#]+)/?(\d+)?", url)
        if not m:
            return [], None
        user, want = m.group(1), m.group(2)
        pk, err = None, ""
        if want:      # رقم الستوري في الرابط: أضمن طريقة لمعرفة صاحب الحساب
            js2, err2 = _ig_json("https://i.instagram.com/api/v1/media/%s/info/" % want, url)
            try:
                single = js2["items"][0]
                pk = single["user"]["pk"]
            except Exception:
                err = err2 or str((js2 or {}).get("message") or (js2 or {}).get("status") or "")[:80]
                flog("IG media/info failed: %s" % err)
        if not pk:
            js, err3 = _ig_json("https://i.instagram.com/api/v1/users/web_profile_info/?username=%s"
                                % urllib.parse.quote(user), url)
            try:
                pk = js["data"]["user"]["id"]
            except Exception:
                pk = None
                err = err3 or err
                flog("IG web_profile_info failed: %s" % err)
        if not pk:
            SOCIAL_DIAG[0] = "IG story: %s" % (err or "user not found / login expired")
            return [], None
        reel = str(pk)
    js, err = _ig_json("https://i.instagram.com/api/v1/feed/reels_media/?reel_ids=%s"
                       % urllib.parse.quote(reel), url)
    items = []
    if js:
        reels = js.get("reels_media") or []
        if reels:
            items = reels[0].get("items") or []
        elif isinstance(js.get("reels"), dict) and js["reels"]:
            items = list(js["reels"].values())[0].get("items") or []
    flog("IG reels_media: %d items (err=%s)" % (len(items), err))
    if not items and single:
        items = [single]
        flog("IG: using the single story item from media/info")
    if not items:
        why = err or (str((js or {}).get("message") or (js or {}).get("status") or "")[:80]
                      or "no active stories (maybe expired after 24h)")
        SOCIAL_DIAG[0] = "IG story: %s" % why
        return [], None
    out = []
    for n, it in enumerate(items, 1):
        cands = ((it.get("image_versions2") or {}).get("candidates")) or []
        thumb = max(cands, key=_area).get("url") if cands else ""
        vurl = ""
        vids = it.get("video_versions") or []
        if vids:
            vurl = max(vids, key=_area).get("url") or ""
        if vurl:
            out.append(dict(kind="img", url=thumb or vurl, vurl=vurl, label="#%d video" % n,
                            ext="MP4", size=None, fid="img:%s" % vurl, h=0, w=0, tbr=0,
                            codec="", fps=0, sel=True, ref=None))
        elif thumb:
            out += _mk_img([(thumb, 0, 0)])
            if out:
                out[-1]["label"] = "#%d" % n
    if not out:
        SOCIAL_DIAG[0] = "IG story: no media in response"
        return [], None
    return out, {"title": ("Instagram_%s_story" % (user or "highlight"))[:60], "author": user}


def download_direct_file_smart(url, dest_path, check=None, prog=None, it=None):
    """بيجرّب أكتر من تركيبة headers (Referer/Origin/Cookie/Range) لأن مواقع كتير بترجّع 403/404
    للفيديو لو الطلب مش شبه طلب المتصفح بالظبط. بيوقف أول ما واحدة تنجح."""
    from urllib.parse import urlparse
    it = it or {}
    ref, page, ck = it.get("ref") or "", it.get("page") or "", it.get("cookie") or ""

    def origin(u):
        p = urlparse(u or "")
        return ("%s://%s" % (p.scheme, p.netloc)) if p.netloc else ""
    variants = [dict(ref=ref, cookie=ck)]
    if ref:
        variants.append(dict(ref=origin(ref) + "/", cookie=ck, extra={"Origin": origin(ref)}))
    if page and page != ref:
        variants.append(dict(ref=page, cookie=ck, extra={"Origin": origin(page)}))
    variants.append(dict(ref=ref, cookie=ck, extra={"Range": "bytes=0-"}))
    variants.append(dict(ref=None, cookie=None))
    last = None
    for v in variants:
        try:
            return download_direct_file(url, dest_path, check=check, prog=prog, ref=v["ref"],
                                        cookie=v["cookie"], extra=v.get("extra"))
        except Exception as ex:
            last = ex
            if getattr(ex, "code", None) not in (400, 401, 403, 404, 405, 410, 416):
                raise            # Cancelled/Paused/انقطاع شبكة...: مش مشكلة headers
    raise last


class _NoRange(Exception):
    pass


def _parallel_download(url, hdrs, dest_path, check=None, prog=None, conns=6):
    """تحميل ملف مباشر بعدة اتصالات مع بعض (زي برامج التحميل). لو السيرفر مش بيدعم Range
    بيرمي _NoRange والمستدعي يكمّل بالطريقة العادية."""
    import urllib.request
    import urllib.error
    try:
        rq = urllib.request.Request(url, headers=dict(hdrs, Range="bytes=0-0"))
        with urllib.request.urlopen(rq, timeout=20) as r:
            code = getattr(r, "status", None) or r.getcode()
            cr = r.headers.get("Content-Range") or ""
            r.read(16)
    except urllib.error.HTTPError as ex:
        if ex.code == 416:
            raise _NoRange()
        raise
    m = re.match(r"bytes\s+0-0/(\d+)", cr)
    if code != 206 or not m:
        raise _NoRange()
    total = int(m.group(1))
    if total < 4 * 1024 * 1024:
        raise _NoRange()
    conns = max(2, min(conns, total // (2 * 1024 * 1024)))
    step = -(-total // conns)
    ranges = [(i * step, min(total - 1, (i + 1) * step - 1)) for i in range(conns) if i * step < total]
    lock = threading.Lock()
    st = {"done": 0, "err": None, "stop": False}
    with open(dest_path, "wb") as f:
        f.truncate(total)

    def work(a, b):
        pos, tries = a, 0
        try:
            with open(dest_path, "r+b") as f:
                while pos <= b and not st["stop"]:
                    try:
                        h2 = dict(hdrs, Range="bytes=%d-%d" % (pos, b))
                        with urllib.request.urlopen(urllib.request.Request(url, headers=h2), timeout=30) as r:
                            if (getattr(r, "status", None) or r.getcode()) != 206:
                                raise _NoRange()
                            while pos <= b and not st["stop"]:
                                data = r.read(256 * 1024)
                                if not data:
                                    break
                                data = data[:b - pos + 1]
                                f.seek(pos)
                                f.write(data)
                                pos += len(data)
                                with lock:
                                    st["done"] += len(data)
                        if pos <= b and not st["stop"]:
                            raise OSError("short read")
                    except _NoRange:
                        raise
                    except Exception:
                        tries += 1
                        if tries > 4:
                            raise
                        time.sleep(1)
        except BaseException as ex:
            if st["err"] is None:
                st["err"] = ex
            st["stop"] = True

    ths = [threading.Thread(target=work, args=rg, daemon=True) for rg in ranges]
    for th in ths:
        th.start()
    try:
        while any(th.is_alive() for th in ths):
            if check:
                check()
            if prog:
                prog(st["done"], total)
            time.sleep(0.25)
        if st["err"] is not None:
            raise st["err"]
    except BaseException:
        st["stop"] = True
        for th in ths:
            th.join(timeout=5)
        try:
            os.remove(dest_path)
        except Exception:
            pass
        raise
    if prog:
        prog(total, total)
    return dest_path


def download_direct_file(url, dest_path, check=None, prog=None, ua=None, ref=None, cookie=None, extra=None):
    """تحميل ملف مباشر (فيديو ستوري مثلًا) بالتدفق مع تقدم."""
    import urllib.request
    hdrs = {"User-Agent": ua or _UA_MOBILE, "Accept": "*/*"}
    if extra:
        hdrs.update(extra)
    if ref:      # بعض المواقع بترفض تحميل الفيديو من غير Referer بتاع صفحتها
        hdrs["Referer"] = ref
    if cookie:   # كوكيز الجلسة اللي الموقع ربط بيها رابط الفيديو
        hdrs["Cookie"] = cookie
    if not (extra and "Range" in extra) and frags_n() > 1:
        try:
            return _parallel_download(url, hdrs, dest_path, check=check, prog=prog, conns=frags_n())
        except _NoRange:
            pass            # السيرفر مش بيدعم Range: كمّل بالطريقة العادية
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(dest_path, "wb") as f:
            while True:
                if check:
                    check()
                data = r.read(256 * 1024)
                if not data:
                    break
                f.write(data)
                done += len(data)
                if prog:
                    prog(done, total or done)
    if not os.path.isfile(dest_path) or os.path.getsize(dest_path) <= 0:
        raise RuntimeError("empty file")
    return dest_path


def _social_instagram(url):
    if "/stories/" in url.lower():
        return _ig_story_items(url)
    m = re.search(r"instagram\.com/(?:[\w.]+/)?(?:p|tv)/([\w-]+)", url)
    if not m:
        return [], None
    code = m.group(1)
    api_imgs, api_meta = _ig_api_images(code)
    if api_imgs:
        return api_imgs, api_meta
    imgs = []
    title = "Instagram"
    for page in ("https://www.instagram.com/p/%s/embed/captioned/" % code,
                 "https://www.instagram.com/p/%s/embed/" % code):
        try:
            html = _http_get_ex(page)[0].decode("utf-8", "replace")
        except Exception:
            continue
        for mm in re.finditer(r'display_url\\*"\s*:\s*\\*"(https:[^"]+?)\\*"', html):
            imgs.append(_unesc_url(mm.group(1)))
        if not imgs:
            for mm in re.finditer(r'class="EmbeddedMediaImage"[^>]*?src="([^"]+)"', html):
                imgs.append(_unesc_url(mm.group(1)))
        mt = re.search(r'"username"\s*:\s*"([^"]+)"', html)
        if mt:
            title = "Instagram_" + mt.group(1)
        if imgs:
            break
    if not imgs:   # آخر محاولة: og:image من صفحة المنشور نفسها
        try:
            html = _http_get_ex(url, ua=_UA_FB)[0].decode("utf-8", "replace")
            imgs, t = _og_from_html(html)
            title = t or title
        except Exception:
            pass
    return _mk_img([(u, 0, 0) for u in imgs]), {"title": title, "author": ""}


def _fb_cookie_header():
    return (CFG.get("fb_cookies") or "").strip()


def fb_cookiefile():
    """ملف cookies بصيغة Netscape لفيسبوك (لو المستخدم سجّل دخول من الإعدادات)."""
    ck = _fb_cookie_header()
    if not ck:
        return None
    try:
        path = os.path.join(tempfile.gettempdir(), "novaget_fb_cookies.txt")
        with open(path, "w") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for part in ck.split(";"):
                nm, _eq, val = part.strip().partition("=")
                if nm and val and "\t" not in val:
                    f.write(".facebook.com\tTRUE\t/\tTRUE\t2147483647\t%s\t%s\n" % (nm, val))
        return path
    except Exception:
        return None


def _fb_get(url, mobile=False):
    ck = _fb_cookie_header()
    return _http_get_ex(url, ua=_UA_MOBILE if mobile else _UA_DESKTOP,
                        extra={"Cookie": ck} if ck else None)


def _fb_resolve(url):
    """روابط facebook.com/share/... و fb.watch بتتحوّل للرابط الحقيقي."""
    from urllib.parse import urlparse, parse_qs, unquote
    try:
        _raw, final = _fb_get(url, mobile=True)
    except Exception as ex:
        flog("FB resolve failed: %s" % str(ex)[:120])
        return url
    if "/login" in final.lower():
        nxt = (parse_qs(urlparse(final).query).get("next") or [""])[0]
        if nxt:
            final = unquote(nxt)
    flog("FB resolved: %s" % final)
    return final


def _social_facebook(url):
    title = "Facebook"
    html, final = "", url
    logged = bool(_fb_cookie_header())
    flog("FB: %s (logged in: %s)" % (url, logged))
    if re.search(r"facebook\.com/share/|fb\.watch", url, re.I):
        url = _fb_resolve(url)
    for mobile in (False, True):
        try:
            raw, final = _fb_get(url, mobile=mobile)
            html = raw[:6000000].decode("utf-8", "replace")
            flog("FB page (%s): %s, %d bytes" % ("mobile" if mobile else "desktop", final, len(html)))
        except Exception as ex:
            flog("FB fetch error: %s" % str(ex)[:120])
            html = ""
        if html and ("browser_native" in html or "playable_url" in html or "photo_image" in html):
            break
    login_wall = ("/login" in final.lower()) or ('id="login_form"' in html) or ("login_form" in html[:20000])
    if login_wall:
        flog("FB: got a login page")
    og_imgs, t = _og_from_html(html) if html else ([], "")
    if t and "log in" not in t.lower() and "facebook" != t.strip().lower():
        title = t

    def grab(keys):
        out, seen = [], set()
        for key in keys:
            for mm in re.finditer(r'"%s"\s*:\s*"(https:[^"]+)"' % key, html):
                u = _unesc_url(mm.group(1))
                k = u.split("?")[0]
                if k not in seen:
                    seen.add(k)
                    out.append(u)
        return out

    vids = grab(("browser_native_hd_url", "playable_url_quality_hd", "hd_src")) \
        or grab(("browser_native_sd_url", "playable_url", "sd_src"))
    for mm in re.finditer(r'<meta[^>]+property=["\']og:video(?::url|:secure_url)?["\'][^>]*content=["\']([^"\']+)', html, re.I):
        u = _unesc_url(mm.group(1))
        if u.startswith("http") and u.split("?")[0] not in {v.split("?")[0] for v in vids}:
            vids.append(u)
    imgs = list(og_imgs)
    for mm in re.finditer(r'"(?:photo_image|viewer_image|image)"\s*:\s*\{[^{}]*?"uri"\s*:\s*"(https:[^"]+)"', html):
        imgs.append(_unesc_url(mm.group(1)))
    imgs = [u for u in imgs
            if "static.xx.fbcdn.net" not in u and "/rsrc.php" not in u
            and "emoji" not in u and not re.search(r"[/_][sp]\d{2,3}x\d{2,3}[/_]", u)]
    flog("FB found: %d videos, %d images" % (len(vids), len(imgs)))
    res = []
    thumb = imgs[0] if imgs else ""
    for n, vu in enumerate(vids, 1):
        res.append(dict(kind="img", url=thumb or vu, vurl=vu, label="#%d video" % n, ext="MP4",
                        size=None, fid="img:%s" % vu, h=0, w=0, tbr=0, codec="", fps=0,
                        sel=True, ref=None))
    if not vids:
        res += _mk_img([(u, 0, 0) for u in imgs])
    elif len(imgs) > 1:
        pass
    if not res:
        if not logged:
            SOCIAL_DIAG[0] = ("فيسبوك محتاج تسجيل دخول: ادخل الإعدادات واضغط تسجيل دخول فيسبوك"
                              if STATE["lang"] == "ar" else
                              "Facebook needs a login: open Settings and tap Log in to Facebook")
        else:
            SOCIAL_DIAG[0] = ("مالقيتش فيديو أو صور في الصفحة دي (ممكن الستوري خلص أو الجلسة انتهت)"
                              if STATE["lang"] == "ar" else
                              "No video or images found on this page (story may have expired, or the session ended)")
    return res, {"title": title[:80], "author": ""}


def _social_twitter(url):
    m = re.search(r"(?:twitter|x)\.com/(?:(\w+)/)?status(?:es)?/(\d+)", url)
    if not m:
        return [], None
    user, tid = m.group(1) or "i", m.group(2)
    try:
        raw = _http_get_ex("https://api.fxtwitter.com/%s/status/%s" % (user, tid))[0]
        tw = (json.loads(raw.decode("utf-8", "replace")).get("tweet") or {})
    except Exception:
        return [], None
    metas = []
    for ph in ((tw.get("media") or {}).get("photos") or []):
        u = ph.get("url") or ""
        if "pbs.twimg.com" in u and "?" not in u and ":" not in u.split("//", 1)[-1]:
            u += ":orig"
        metas.append((u, ph.get("width"), ph.get("height")))
    author = ((tw.get("author") or {}).get("screen_name")) or ""
    return _mk_img(metas), {"title": (tw.get("text") or "Tweet")[:60], "author": author}


def _social_reddit(url):
    try:
        raw, final = _http_get_ex(url)    # لو رابط قصير بيتحل هنا
        base = final.split("?")[0].rstrip("/")
        js = json.loads(_http_get_ex(base + ".json?raw_json=1")[0].decode("utf-8", "replace"))
        post = js[0]["data"]["children"][0]["data"]
    except Exception:
        return [], None
    metas = []
    gd = (post.get("gallery_data") or {}).get("items") or []
    mm = post.get("media_metadata") or {}
    for it in gd:
        s_ = (mm.get(it.get("media_id")) or {}).get("s") or {}
        u = s_.get("u") or s_.get("gif")
        if u:
            metas.append((u, s_.get("x"), s_.get("y")))
    if not metas:
        u = post.get("url_overridden_by_dest") or post.get("url") or ""
        if _IMG_EXT_RE.search(u) or post.get("post_hint") == "image":
            metas.append((u, 0, 0))
        else:
            try:
                src = post["preview"]["images"][0]["source"]
                metas.append((src["url"], src.get("width"), src.get("height")))
            except Exception:
                pass
    return _mk_img(metas), {"title": (post.get("title") or "Reddit")[:60],
                            "author": post.get("author") or ""}


_SCRAPE_CACHE = {}
_SCRAPE_LOCK = threading.Lock()


def _page_scrape(url, max_n=60):
    """نفس _page_scrape_raw بس بتفتح الصفحة مرة واحدة حتى لو اتطلبت من أكتر من مكان في نفس الوقت."""
    key = (url, max_n)
    with _SCRAPE_LOCK:
        ent = _SCRAPE_CACHE.get(key)
        if ent is not None and time.time() - ent["t"] > 60:
            ent = None
        owner = ent is None
        if owner:
            ent = {"t": time.time(), "ev": threading.Event(), "res": None, "err": None}
            _SCRAPE_CACHE[key] = ent
            if len(_SCRAPE_CACHE) > 20:
                for k in sorted(_SCRAPE_CACHE, key=lambda k: _SCRAPE_CACHE[k]["t"])[:10]:
                    _SCRAPE_CACHE.pop(k, None)
    if owner:
        try:
            ent["res"] = _page_scrape_raw(url, max_n)
        except Exception as exc:
            ent["err"] = exc
        finally:
            ent["ev"].set()
    else:
        ent["ev"].wait(60)
    if ent["err"] is not None:
        raise ent["err"]
    if ent["res"] is None:
        raise RuntimeError("page scan timed out")
    return ent["res"]


def _page_scrape_raw(url, max_n=60):
    """صور وفيديوهات أي صفحة ويب (مقالات، معارض، نتايج بحث صور جوجل). بيرجّع (images, og_list, title, videos)."""
    from urllib.parse import urljoin, urlparse, parse_qs
    cookie = {"Cookie": "CONSENT=YES+cb; SOCS=CAI"}
    raw, final = _http_get_ex(url, ua=_UA_DESKTOP, extra=cookie)
    host = urlparse(final).netloc.lower()
    # بحث صور جوجل: النسخة العادية (gbv=1) فيها الصور بدون JavaScript
    if "google." in host and "/search" in final and "gbv=1" not in final:
        qs = parse_qs(urlparse(final).query)
        if qs.get("tbm") == ["isch"] or qs.get("udm") == ["2"] or "isch" in final:
            final2 = final + ("&" if "?" in final else "?") + "gbv=1"
            try:
                raw, final = _http_get_ex(final2, ua=_UA_DESKTOP, extra=cookie)
            except Exception:
                pass
    html = raw[:4000000].decode("utf-8", "replace")
    og, title = _og_from_html(html)
    og = [urljoin(final, u) for u in og]
    bad = re.compile(r"favicon|sprite|pixel|spacer|blank\.|1x1|/icons?/|\.svg|\.gif(\?|$)|"
                     r"logo|emoji|badge|avatar|googleusercontent\.com/a/", re.I)
    found = []     # (url, w, h)

    def num(v):
        d = re.sub(r"\D", "", v or "")
        return int(d) if d and len(d) < 6 else 0

    # روابط مباشرة لصور كاملة
    for m in re.finditer(r'<a\b[^>]+href=["\']([^"\']+\.(?:jpe?g|png|webp)(?:\?[^"\']*)?)["\']', html, re.I):
        u = urljoin(final, m.group(1).replace("&amp;", "&"))
        if u.startswith("http") and not bad.search(u):
            found.append((u, 0, 0))
    for m in re.finditer(r"<img\b[^>]*>", html, re.I | re.S):
        tag = m.group(0)
        at = {k.lower(): v for k, v in re.findall(r'([\w:-]+)\s*=\s*["\']([^"\']*)["\']', tag)}
        w, h = num(at.get("width")), num(at.get("height"))
        if (w and w < 120) or (h and h < 120):
            continue
        src = (at.get("data-src") or at.get("data-original") or at.get("data-lazy-src")
               or at.get("src") or "")
        ss = at.get("srcset") or at.get("data-srcset") or ""
        if ss:
            best_u, best_w = "", -1
            for part in ss.split(","):
                bits = part.strip().split()
                if not bits:
                    continue
                wv = num(bits[1]) if len(bits) > 1 else 0
                if wv >= best_w:
                    best_u, best_w = bits[0], wv
            if best_u:
                src = best_u
        src = src.replace("&amp;", "&").strip()
        if not src or src.startswith("data:"):
            continue
        u = urljoin(final, src)
        if u.startswith("http") and not bad.search(u):
            found.append((u, w, h))
    metas = [(u, 0, 0) for u in og] + found
    vids = _page_video_items(html, final, og)
    return _mk_img(metas[:max_n], ref=final), og, title, vids


_VID_EXT_RE = re.compile(r"\.(mp4|webm|m4v|mov|m3u8|mpd)(\?|#|$)", re.I)


def _page_video_items(html, final, og, max_n=30):
    """فيديوهات أي صفحة ويب: <video>/<source>/og:video/روابط mp4 و m3u8 جوه الصفحة أو الـ JSON بتاعها."""
    from urllib.parse import urljoin, urlparse, unquote
    import html as _html
    cands = []
    posters = {}

    def add(u, poster=""):
        if not u:
            return
        u = _html.unescape(u).strip().strip("\\\"' ")
        u = u.replace("\\/", "/").replace("\\u0026", "&").replace("\\u002F", "/")
        if u.startswith(("blob:", "data:", "javascript:")):
            return
        u = urljoin(final, u)
        if u.startswith("http") and _VID_EXT_RE.search(u):
            cands.append(u)
            if poster and u not in posters:
                posters[u] = poster

    cur_poster = ""
    for m in re.finditer(r"<(?:video|source)\b[^>]*>", html, re.I | re.S):
        tag = m.group(0)
        at = {k.lower(): v for k, v in re.findall(r'([\w:-]+)\s*=\s*["\']([^"\']*)["\']', tag)}
        if tag[:6].lower() == "<video":     # صورة الفيديو (poster) بتتطبق على الـ <source> اللي جواه
            p = _html.unescape(at.get("poster") or "").strip()
            cur_poster = urljoin(final, p) if p and not p.startswith("data:") else ""
        for key in ("src", "data-src", "data-video-src", "data-mp4"):
            add(at.get(key), cur_poster)
    for pat in (r'<meta[^>]+(?:property|name)=["\']og:video(?::url|:secure_url)?["\'][^>]*?content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*?(?:property|name)=["\']og:video(?::url|:secure_url)?["\']',
                r'<a\b[^>]+href=["\']([^"\']+)["\']'):
        for m in re.finditer(pat, html, re.I):
            add(m.group(1))
    # روابط كاملة جوه سكربتات/JSON (مع escaping)
    flat = html.replace("\\/", "/").replace("\\u002F", "/")
    for m in re.finditer(r'https?://[^\s"\'<>\\]+?\.(?:mp4|webm|m4v|mov|m3u8)(?:\?[^\s"\'<>\\]*)?', flat, re.I):
        add(m.group(0))

    seen, urls = set(), []
    for u in cands:
        k = u.split("?")[0]
        if k in seen:
            continue
        seen.add(k)
        urls.append(u)
    urls = urls[:max_n]
    out = []
    for n, u in enumerate(urls, 1):
        low = u.lower().split("?")[0].split("#")[0]
        ext = "WEBM" if low.endswith(".webm") else ("MOV" if low.endswith(".mov") else "MP4")
        # صورة الفيديو نفسه (poster)؛ og:image ينفع بس لو الصفحة فيها فيديو واحد
        thumb = posters.get(u) or (og[0] if (og and len(urls) == 1) else "")
        stem = unquote(os.path.splitext(os.path.basename(urlparse(u).path))[0])
        name = "" if re.match(r"^(master|index|playlist|manifest|chunklist|video|stream|\d+)$", stem, re.I) else stem
        out.append(dict(kind="img", url=thumb or u, vurl=u, label="#%d video" % n, ext=ext,
                        name=name[:80], size=None, fid="img:%s" % u, h=0, w=0, tbr=0, codec="",
                        fps=0, sel=True, ref=final, thumb=thumb or ""))
    return out


_KNOWN_HOSTS_RE = re.compile(
    r"youtube\.com|youtu\.be|tiktok\.com|instagram\.com|facebook\.com|fb\.watch|fb\.com|"
    r"twitter\.com|(^|[/.])x\.com|reddit\.com|redd\.it|vimeo\.com|dailymotion\.com|"
    r"twitch\.tv|soundcloud\.com|bilibili\.com", re.I)

# جافاسكريبت بيتشغّل جوه الصفحة في WebView ويرجّع روابط الفيديو الحقيقية اللي الصفحة طلبتها
_SNIFF_JS = r"""(function(){try{
if(!window.__nvh){window.__nvh=1;window.__nvu=[];
 var pu=function(u){try{u=new URL(String((u&&u.url)||u),location.href).href;
  if(/\.(mp4|m3u8|webm|mpd|m4v|mov)(\?|#|$)/i.test(u))window.__nvu.push(u);}catch(e){}};
 try{var xo=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u){pu(u);return xo.apply(this,arguments);};}catch(e){}
 try{var fo=window.fetch;if(fo)window.fetch=function(a){pu(a);return fo.apply(this,arguments);};}catch(e){}
}
var v=[],r=[],f=[],dbg=[],add=function(a,u){if(u&&typeof u==='string'&&/^https?:/i.test(u))a.push(u);};
try{var hu=window.__nvu||[];for(var q=0;q<hu.length;q++)add(r,hu[q]);}catch(x){}
var vs=document.querySelectorAll('video');
for(var i=0;i<vs.length;i++){var e=vs[i];
 try{if(e.paused){e.muted=true;var p=e.play();if(p&&p.catch)p.catch(function(){});}}catch(x){}
 add(v,e.currentSrc);add(v,e.src);
 var ss=e.querySelectorAll('source');for(var j=0;j<ss.length;j++)add(v,ss[j].src);}
try{var en=performance.getEntriesByType('resource');
 for(var k=0;k<en.length;k++){var n=en[k].name;
  if(/\.(mp4|m3u8|webm|mpd|m4v|mov)(\?|#|$)/i.test(n)||en[k].initiatorType==='video'||en[k].initiatorType==='audio')add(r,n);
  if(dbg.length<30&&/m3u8|mpd|mp4|webm|\.ts|m4s|video|stream|playlist|manifest/i.test(n))dbg.push(n.slice(0,200));}}catch(x){}
var fr=document.querySelectorAll('iframe');for(var m=0;m<fr.length;m++)add(f,fr[m].src);
var v0=vs.length?vs[0]:null;
var og=document.querySelector('meta[property="og:image"],meta[name="og:image"],meta[name="twitter:image"]');
for(var z=0;z<vs.length;z++){if(dbg.length<40)dbg.push('video.currentSrc='+String(vs[z].currentSrc||vs[z].src||'').slice(0,200));}
return JSON.stringify({v:v,r:r,f:f,dbg:dbg,t:document.title||'',p:(v0&&v0.poster)||(og&&og.content)||''});
}catch(e){return '{}';}})()"""


def hls_variants(url, ref=None, cookie=None):
    """لو الرابط m3u8 رئيسي فيه جودات: يرجّع [(الارتفاع, البت ريت, رابط الجودة)] من الأعلى للأقل."""
    import urllib.request
    from urllib.parse import urljoin
    hd = {"User-Agent": _UA_MOBILE, "Accept": "*/*"}
    if ref:
        hd["Referer"] = ref
    if cookie:
        hd["Cookie"] = cookie
    with urllib.request.urlopen(urllib.request.Request(url, headers=hd), timeout=15) as r:
        txt = r.read(400000).decode("utf-8", "ignore")
    lines = [l.strip() for l in txt.splitlines()]
    best = {}
    for i, l in enumerate(lines):
        if not l.startswith("#EXT-X-STREAM-INF"):
            continue
        nxt = next((x for x in lines[i + 1:] if x and not x.startswith("#")), "")
        if not nxt:
            continue
        m = re.search(r"RESOLUTION=(\d+)x(\d+)", l)
        b = re.search(r"(?<![\w-])BANDWIDTH=(\d+)", l)
        h = min(int(m.group(1)), int(m.group(2))) if m else 0
        bw = int(b.group(1)) if b else 0
        key = h or bw
        if key not in best or bw > best[key][1]:
            best[key] = (h, bw, urljoin(url, nxt))
    return sorted(best.values(), key=lambda x: (x[0], x[1]), reverse=True)


def _video_item(u, n, ref, thumb="", cookie="", page=""):
    """عنصر فيديو مباشر جاهز للقائمة (نفس شكل عناصر _page_video_items)."""
    from urllib.parse import urlparse, unquote
    low = u.lower().split("?")[0].split("#")[0]
    ext = "WEBM" if low.endswith(".webm") else ("MOV" if low.endswith(".mov") else "MP4")
    stem = unquote(os.path.splitext(os.path.basename(urlparse(u).path))[0])
    name = "" if re.match(r"^(master|index|playlist|manifest|chunklist|video|stream|\d+)$", stem, re.I) else stem
    return dict(kind="img", url=thumb or u, vurl=u, label="#%d video" % n, ext=ext, name=name[:80],
                size=None, fid="img:%s" % u, h=0, w=0, tbr=0, codec="", fps=0, sel=True,
                ref=ref, cookie=cookie or "", page=page or "", thumb=thumb or "")


def extract_page_images(url):
    try:
        imgs, og, title, vids = _page_scrape(url)
        return vids + imgs, {"title": (title or "Images")[:80], "author": ""}
    except Exception:
        return [], None


def extract_social_images(url):
    """صور منشورات السوشيال (فيسبوك/إنستجرام/تويتر/ريديت/...) لما yt-dlp ما يلاقيش فيديو."""
    if not url:
        return [], None
    low = url.lower()
    try:
        if "instagram.com" in low:
            return _social_instagram(url)
        if "facebook.com" in low or "fb.com" in low or "fb.watch" in low:
            return _social_facebook(url)
        if "twitter.com" in low or re.search(r"(^|[/.])x\.com/", low):
            return _social_twitter(url)
        if "reddit.com" in low or "redd.it" in low:
            return _social_reddit(url)
        # أي موقع تاني (جوجل صور، مقالات، threads, pinterest, ...): صور الصفحة
        return extract_page_images(url)
    except Exception:
        return [], None


def _img_headers(url):
    h = {"User-Agent": _UA_MOBILE, "Accept": "image/jpeg,image/png,image/*;q=0.8,*/*;q=0.5",
         "Accept-Language": "en-US,en;q=0.9"}
    if "tiktok" in (url or "").lower():
        h["Referer"] = "https://www.tiktok.com/"
    return h


_UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _img_header_sets(url, ref=None):
    """أكتر من مجموعة headers؛ بنجرب واحدة واحدة لحد ما السيرفر يرجّع صورة فعلًا."""
    sets = [_img_headers(url)]
    if ref:   # مواقع بتمنع تحميل الصور من برّه: نبعت صفحة الموقع كـ Referer
        sets.insert(0, {"User-Agent": _UA_DESKTOP, "Accept": "image/*,*/*;q=0.8", "Referer": ref})
    low = (url or "").lower()
    if any(x in low for x in ("fbcdn", "facebook", "fbsbx")):
        sets.append({"User-Agent": _UA_DESKTOP, "Accept": "*/*",
                     "Referer": "https://www.facebook.com/"})
        sets.append({"User-Agent": _UA_FB, "Accept": "*/*"})
    elif any(x in low for x in ("cdninstagram", "instagram")):
        sets.append({"User-Agent": _UA_DESKTOP, "Accept": "*/*",
                     "Referer": "https://www.instagram.com/"})
    else:
        sets.append({"User-Agent": _UA_DESKTOP, "Accept": "*/*"})
    return sets


def _open_image(url, timeout=30, ref=None):
    """بيفتح رابط الصورة ويرجّع (response, أول_بايتات). لو مفيش محاولة رجّعت صورة بيرمي خطأ فيه التشخيص."""
    import urllib.request
    last = ""
    for hd in _img_header_sets(url, ref):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=hd), timeout=timeout)
            first = r.read(4096)
        except Exception as ex:
            last = str(ex)[:90]
            continue
        if _sniff_img_ext(first):
            return r, first
        ct = r.headers.get("Content-Type", "")
        snippet = re.sub(r"\s+", " ", first[:140].decode("utf-8", "replace"))
        last = "%s | %s | %s" % (getattr(r, "status", ""), ct, snippet)
        try:
            r.close()
        except Exception:
            pass
    raise RuntimeError(last or "no response")


def _sniff_img_ext(data):
    """بيعرف نوع الصورة من أول بايتات (jpg/png/webp/gif) أو None لو مش صورة."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] in (b"GIF8",):
        return "gif"
    return None


def download_image_file(url, dest_path, check=None, prog=None, ref=None):
    """يحمّل صورة واحدة إلى dest_path (بيجرب أكتر من headers لو السيرفر رفض)."""
    r, first = _open_image(url, timeout=60, ref=ref)
    try:
        total = int(r.headers.get("Content-Length") or 0)
        done = len(first)
        with open(dest_path, "wb") as f:
            f.write(first)
            while True:
                if check:
                    check()
                data = r.read(64 * 1024)
                if not data:
                    break
                f.write(data)
                done += len(data)
                if prog:
                    prog(done, total or done)
    finally:
        try:
            r.close()
        except Exception:
            pass
    if not os.path.isfile(dest_path) or os.path.getsize(dest_path) <= 0:
        raise RuntimeError("Image download produced an empty file")
    real = _sniff_img_ext(first) or "jpg"
    cur = os.path.splitext(dest_path)[1].lower().lstrip(".")
    if cur != real and not (cur == "jpeg" and real == "jpg"):
        newp = os.path.splitext(dest_path)[0] + "." + real
        n = 1
        while os.path.exists(newp):
            newp = "%s_%d.%s" % (os.path.splitext(dest_path)[0], n, real)
            n += 1
        os.rename(dest_path, newp)
        dest_path = newp
    return dest_path


# ------------------------------------------------------------------ thumbnails (PIL)
try:
    from yt_dlp.postprocessor.common import PostProcessor as _YDLPP
except Exception:      # pragma: no cover
    _YDLPP = object


class PilThumbConvertor(_YDLPP):
    """بيحوّل الصورة المصغّرة (webp/png/...) لـ jpg بمكتبة Pillow بدل ffmpeg،
    لأن ffmpeg في أندرويد غالبًا مفيهوش decoder لـ webp."""

    def run(self, info):
        try:
            from PIL import Image
        except Exception:
            return [], info
        moves = info.get("__files_to_move") or {}
        for t_ in info.get("thumbnails") or []:
            fp = t_.get("filepath")
            if not fp or not os.path.isfile(fp):
                continue
            base, ext = os.path.splitext(fp)
            if ext.lower() in (".jpg", ".jpeg"):
                continue
            out = base + ".jpg"
            try:
                Image.open(fp).convert("RGB").save(out, "JPEG", quality=92)
            except Exception:
                continue
            try:
                os.remove(fp)
            except Exception:
                pass
            t_["filepath"] = out
            if fp in moves:
                moves[out] = os.path.splitext(moves.pop(fp))[0] + ".jpg"
        return [], info


def _pil_ok():
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ app
class _GroupSink(object):
    """بيحوّل add_widget العادي لإضافة جوه كارت المجموعة الحالية (لصفحة إعدادات التحميل)."""

    def __init__(self, app, real):
        self.app, self.real, self.cur = app, real, None

    def new_group(self, title):
        self.cur = self.app._group(self.real, title)

    def add_widget(self, w):
        (self.cur if self.cur is not None else self.real).add_widget(w)


class CircleImage(Widget):
    """صورة دائرية (صورة القناة)."""
    texture = ObjectProperty(None, allownone=True)

    def __init__(self, **kw):
        super().__init__(**kw)
        with self.canvas:
            self._c = Color(0.18, 0.20, 0.32, 1)
            self._e = Ellipse(pos=self.pos, size=self.size)
        self.bind(pos=self._upd, size=self._upd, texture=self._upd)

    def _upd(self, *a):
        self._e.pos = self.pos
        self._e.size = self.size
        if self.texture is not None:
            self._c.rgba = (1, 1, 1, 1)
            self._e.texture = self.texture


class TapCell(ButtonBehavior, FloatLayout):
    """خلية قابلة للضغط (مربع شورتس)."""


_GLYPH_CACHE = {}


def _glyph_ok(ch):
    """هل خط البحث فيه حرف ده؟ (لو لأ بنرجع للحرف الأساسي بدل المربع)"""
    if not _reg:
        return True
    r = _GLYPH_CACHE.get(ch)
    if r is None:
        try:
            r = bool(font_covers(_reg, need=(ord(ch),)))
        except Exception:
            r = True
        _GLYPH_CACHE[ch] = r
    return r


class DragScrollInput(TextInput):
    """حقل سطر واحد: سحب الصباع عليه بيحرّك النص يمين/شمال عشان تشوف اللينك الطويل كله."""

    def _text_w(self):
        try:
            if not self.text:
                return 0
            return CoreLabel(font_name=self.font_name, font_size=self.font_size).get_extents(self.text)[0]
        except Exception:
            return 0

    def on_touch_move(self, touch):
        if not self.multiline and self.collide_point(touch.ox, touch.oy):
            ud = touch.ud
            if ud.get("nv_drag") or abs(touch.x - touch.ox) > dp(8):
                ud["nv_drag"] = True
                try:
                    self.cancel_selection()
                    avail = self.width - self.padding[0] - self.padding[2]
                    mx = max(0.0, self._text_w() - avail + dp(6))
                    self.scroll_x = max(0.0, min(mx, self.scroll_x - touch.dx))
                    self._trigger_update_graphics()
                except Exception:
                    pass
                return True
        return super().on_touch_move(touch)


class SearchInput(DragScrollInput):
    """حقل بحث بيعرض العربي موصول وبالترتيب الصح (Kivy مابيشكّلش الحروف لوحده).
    النص الحقيقي محفوظ في self.raw، والمعروض (text) هو النسخة المشكّلة.
    التعديل في نص الكلمة بيتحول من مكان المؤشر المرئي لمكانه في النص الحقيقي (تقريبي مع النص المخلوط)."""

    def __init__(self, **kw):
        self.raw = ""
        self._busy = False
        super().__init__(**kw)

    def _is_rtl(self):
        return bool(AR_RE.search(self.raw))

    def _vis_to_log(self, col):
        n = len(self.raw)
        pos = (n - col) if self._is_rtl() else col
        return max(0, min(n, pos))

    def _log_to_vis(self, pos):
        if self._is_rtl():
            return max(0, min(len(self.text), len(self.text) - pos))
        return max(0, min(len(self.text), pos))

    def _visual(self):
        if not self._is_rtl():
            return self.raw
        import unicodedata
        out = []
        for ch in fixp(self.raw):
            if unicodedata.category(ch) == "Cf":      # رموز اتجاه مخفية بتظهر مربع
                continue
            if ord(ch) > 0x7F and not _glyph_ok(ch):
                ch = unicodedata.normalize("NFKC", ch) or ch
            out.append(ch)
        return "".join(out)

    def _refresh(self, log_pos):
        self._busy = True
        try:
            self.cancel_selection()
            self.text = self._visual()
            self.cursor = (self._log_to_vis(log_pos), 0)
        finally:
            self._busy = False

    def set_raw(self, s):
        self.raw = s or ""
        self._refresh(len(self.raw))

    def insert_text(self, substring, from_undo=False):
        if self._busy or from_undo:
            return super().insert_text(substring, from_undo=from_undo)
        substring = (substring or "").replace("\n", " ")
        if not substring:
            return
        if self.selection_text:
            a, b = sorted((self.selection_from, self.selection_to))
            la, lb = sorted((self._vis_to_log(a), self._vis_to_log(b)))
            self.raw = self.raw[:la] + self.raw[lb:]
            pos = la
        else:
            pos = self._vis_to_log(self.cursor_col)
        self.raw = self.raw[:pos] + substring + self.raw[pos:]
        self._refresh(pos + len(substring))

    def do_backspace(self, from_undo=False, mode="bkspc"):
        if self._busy or from_undo:
            return super().do_backspace(from_undo=from_undo, mode=mode)
        if self.selection_text:
            a, b = sorted((self.selection_from, self.selection_to))
            la, lb = sorted((self._vis_to_log(a), self._vis_to_log(b)))
            self.raw = self.raw[:la] + self.raw[lb:]
            self._refresh(la)
            return
        pos = self._vis_to_log(self.cursor_col)
        if pos <= 0:
            return
        self.raw = self.raw[:pos - 1] + self.raw[pos:]
        self._refresh(pos - 1)

    def delete_selection(self, from_undo=False):
        if self._busy or from_undo:
            return super().delete_selection(from_undo=from_undo)
        self.do_backspace()



def ts_search_hint():
    return fix("ابحث باسم فيديو أو قناة، أو الصق رابط" if STATE["lang"] == "ar"
               else "Search videos / channels, or paste a link")


class NovaGetApp(App):
    title = "NovaGet"

    def build(self):
        self.nav = None
        if not getattr(self, "_inited", False):
            self._inited = True
            Builder.load_string(KV)
            load_cfg()
            apply_theme(CFG.get("theme"))
            STATE["lang"] = CFG["lang"]
            SOUND.load()
            NOTIF.setup(self._notif_cb)
            if CFG["notify"]:
                NOTIF.request_permission()
            self.jobs = []
            self.lock = threading.RLock()
            self.history = load_history()
            self._history_thumb_pending = set()
            self._history_thumb_lock = threading.Lock()
            self.download_dir = get_download_dir()
            Window.bind(on_keyboard=self._on_keyboard)
            Clock.schedule_interval(self._tick, 0.5)
            if NOTIF.ok:
                threading.Thread(target=self._notif_loop, daemon=True).start()
        self.rtl = STATE["lang"] == "ar"
        self.url = ""
        self.sel_mode = False
        self.sel_ids = set()
        self.dl_view = None
        self.dl_rows = {}
        self.dl_tab = "active"
        self._msg_until = 0
        self.tab = "video"
        # ---- بحث يوتيوب داخل البرنامج
        self.srch_q = getattr(self, "srch_q", "")
        self.srch_mode = "video"
        self.srch_results = getattr(self, "srch_results", [])
        self.srch_channel = getattr(self, "srch_channel", None)   # (اسم, رابط) لو بنتصفح قناة
        self.srch_count = getattr(self, "srch_count", 25)
        self.srch_chan_tab = getattr(self, "srch_chan_tab", "videos")   # videos / shorts / playlists
        self.srch_chan_info = getattr(self, "srch_chan_info", {})
        self.srch_is_pl = getattr(self, "srch_is_pl", False)
        self.srch_busy = False
        self.srch_token = getattr(self, "srch_token", 0)
        self.srch_status = ""
        self.video_items, self.audio_items, self.image_items = [], [], []
        self.has_info = False
        self.info_data = {}
        self.pl_mode = False          # وضع البلاي ليست
        self.img_mode = False         # وضع الصور (كاروسيل)
        self.img_checks = {}
        self.img_previews = {}
        self.img_cells = {}
        self.img_go = None
        self.img_sel = None
        self.img_batch_left = 0       # صور متبقية في دفعة واحدة (عشان صوت واحد في الآخر)
        self.img_ctrl = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6))
        self.img_ctrl.bind(minimum_height=self.img_ctrl.setter("height"))
        self.pl_entries = []
        self.pl_checks = {}
        self.pl_vq = 720              # جودة الفيديو الافتراضية للبلاي ليست (0 = الأفضل)
        self.pl_aq = 192              # جودة MP3 الافتراضية
        self.pl_go = None
        self.pl_sel = None
        self.pl_est_lbl = None
        self.pl_ctrl = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        self.pl_ctrl.bind(minimum_height=self.pl_ctrl.setter("height"))
        self._st = ("ready", TXT)
        self._dt = ("saves", self.download_dir)
        if not FONT_OK:
            self._dt = "Arabic font problem - open the Settings tab for details"
        elif not AR_OK:
            self._dt = "Install: arabic-reshaper and python-bidi (pip)"

        # الصفحة الرئيسية ثابتة؛ التمرير يكون داخل قائمة الفيديوهات فقط.
        # الصفحة المنفصلة هي التي تستوعب قائمة الرابط بعد التحليل.
        root = BoxLayout(
            orientation="vertical",
            size_hint=(1, 1),
            padding=[dp(12), dp(40), dp(12), dp(8)],
            spacing=dp(8),
        )
        self.root_box = root

        # ---- header (title + gear)
        self.hdr = BoxLayout(size_hint_y=None, height=dp(84), spacing=dp(10))
        self.hdr_text = BoxLayout(orientation="horizontal", spacing=dp(14))
        self.logo = LogoMark(size_hint=(None, None), size=(dp(62), dp(62)),
                             pos_hint={"center_y": .5})
        self.hdr_col = BoxLayout(orientation="vertical", size_hint=(None, 1), width=dp(206))
        self.brand = TLabel(
            text=brand_markup(),
            markup=True, font_size=dp(36), bold=True, shorten=False,
        )
        self.tagline = TLabel(font_size=dp(12.5), color=DIM)
        self.hdr_col.add_widget(self.brand)
        self.hdr_col.add_widget(self.tagline)
        self.hero = FloatLayout(size_hint_y=None, height=dp(130))
        self.hdr.size_hint = (1, None)
        self.hdr.pos_hint = {"center_y": .5}
        self.hero.add_widget(self.hdr)
        root.add_widget(self.hero)

        # ---- input card
        inp = Card(
            orientation="horizontal", size_hint_y=None, height=dp(60),
            padding=[dp(16), 0, dp(9), 0], spacing=dp(10), bg=CARD, radius=dp(20), elev=1,
        )
        # حقل عادي (TextInput) + نص التلميح كـ Label تحته، عشان التلميح يقدر يبقى عربي بخط عربي
        self.url_input = DragScrollInput(
            multiline=False, write_tab=False,
            pos_hint={"x": 0, "y": 0},   # FloatLayout من غير pos_hint بيسيب العنصر في (0,0) بتاع الشاشة
            background_normal="", background_active="",
            background_color=(0, 0, 0, 0),
            foreground_color=TXT, cursor_color=ACC2, font_size=dp(15),
        )
        self.url_input.bind(
            size=lambda w, s: setattr(w, "padding", [0, max(0, (w.height - w.line_height) / 2.0)]),
            text=self._on_text,
        )
        self.url_input.bind(focus=self._on_url_focus)
        self.hint_lbl = TLabel(font_size=dp(15), color=(0.45, 0.50, 0.66, 1),
                               pos_hint={"x": 0, "y": 0})
        input_box = FloatLayout()
        input_box.add_widget(self.hint_lbl)
        input_box.add_widget(self.url_input)
        # لو اللينك فيه عربي: Kivy بيعرضه مفكوك ومعكوس، فبنعرض نسخة مشكّلة فوقه (الحقل نفسه بيفضل بالنص الأصلي)
        self.url_view = TLabel(font_size=dp(15), halign="left", valign="middle", shorten=True,
                               shorten_from="right", pos_hint={"x": 0, "y": 0}, opacity=0)
        self.url_view.bind(size=lambda w, sz: setattr(w, "text_size", (sz[0], sz[1])))
        input_box.add_widget(self.url_view)
        inp.add_widget(LinkIcon(size_hint=(None, None), size=(dp(22), dp(22)),
                                pos_hint={"center_y": .5}))
        inp.add_widget(input_box)
        self.paste_btn = RoundButton(
            size_hint=(None, None), size=(dp(78), dp(40)),
            pos_hint={"center_y": .5}, bg=CARD2, radius=dp(20),
        )
        self.paste_btn.bind(on_release=self._paste_or_clear)
        inp.add_widget(self.paste_btn)
        root.add_widget(inp)

        # ---- analyze
        self.analyze_btn = GradButton(size_hint_y=None, height=dp(56))
        self.analyze_btn.bind(on_release=self.fetch_formats)
        root.add_widget(self.analyze_btn)

        # ---- اختصارات المواقع (بتفتح الموقع عشان تنسخ منه الرابط) + حالات واتساب
        self.chips = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        self.wa_btn = RoundButton(font_size=dp(14), radius=dp(23), bg=CARD)
        self.wa_btn.bind(on_release=lambda *_: self.show_tab("wa"))
        self.chips.add_widget(self.wa_btn)
        root.add_widget(self.chips)

        # ---- dynamic area (info + tabs)
        self.dyn = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(12))
        self.dyn.bind(minimum_height=self.dyn.setter("height"))
        root.add_widget(self.dyn)

        self.info_card = Card(
            orientation="horizontal", size_hint_y=None, height=dp(96),
            padding=dp(10), spacing=dp(12), bg=CARD, elev=1,
        )
        self.thumb = KImage(size_hint_x=None, width=0, opacity=0)
        self.info_col = BoxLayout(orientation="vertical", spacing=dp(2))
        self.info_title = TLabel(font_size=dp(15), bold=True)
        self.info_meta = TLabel(font_size=dp(12), color=DIM, size_hint_y=None, height=dp(22))
        self.info_col.add_widget(self.info_title)
        self.info_col.add_widget(self.info_meta)

        self.tabs = Card(orientation="horizontal", size_hint_y=None, height=dp(50),
                         padding=dp(4), spacing=dp(4), bg=CARD, radius=dp(25))
        self.tab_video = RoundButton(radius=dp(21))
        self.tab_audio = RoundButton(radius=dp(21))
        self.tab_images = RoundButton(radius=dp(21))
        self.tab_video.bind(on_release=lambda *_: self.set_tab("video"))
        self.tab_audio.bind(on_release=lambda *_: self.set_tab("audio"))
        self.tab_images.bind(on_release=lambda *_: self.set_tab("images"))
        self.tabs.add_widget(self.tab_video)
        self.tabs.add_widget(self.tab_audio)
        self.tabs.add_widget(self.tab_images)

        # ---- list
        sv = ScrollView(size_hint_y=1, height=0,
                        bar_width=dp(3), bar_color=(1, 1, 1, .25))
        self.list_box = GridLayout(cols=1, spacing=dp(10), size_hint_y=None,
                                   padding=[0, 0, 0, dp(4)])
        self.list_box.bind(minimum_height=self.list_box.setter("height"))
        sv.add_widget(self.list_box)
        root.add_widget(sv)
        self.list_scroll = sv
        self.input_card = inp

        # ---- progress card
        self.prog_card = Card(orientation="vertical", size_hint_y=None, height=dp(106),
                  padding=[dp(16), dp(12)], spacing=dp(8), bg=CARD, elev=1)
        pc = self.prog_card
        self.status_row = BoxLayout(size_hint_y=None, height=dp(24))
        self.status = TLabel(font_size=dp(14), bold=True)
        self.pct = TLabel(font_size=dp(14), bold=True, size_hint_x=None,
                          width=dp(60), color=ACC2)
        pc.add_widget(self.status_row)
        self.bar = SlimProgress(size_hint_y=None, height=dp(8))
        pc.add_widget(self.bar)
        self.detail = TLabel(font_size=dp(12), color=DIM)
        pc.add_widget(self.detail)
        root.add_widget(pc)

        def _keep_hidden(w, h):
            if h:
                w.height = 0
        pc.bind(height=_keep_hidden)
        pc.height = 0
        pc.opacity = 0
        pc.disabled = True

        self.apply_lang()
        page_scroll = ScrollView(do_scroll_y=False, do_scroll_x=False,
                                 bar_width=dp(3), bar_color=(1, 1, 1, .22),
                                 scroll_type=["bars", "content"])
        page_scroll.add_widget(root)
        self.page_scroll = page_scroll
        self.home_root = root
        self.detail_root = None
        self.detail_page_open = False

        # ---- الشاشة الرئيسية: محتوى + شريط تنقل سفلي (تنزيل / التحميلات / الإعدادات) زي Snaptube
        shell = BoxLayout(orientation="vertical")
        add_aurora(shell)
        self.content = BoxLayout()
        shell.add_widget(self.content)
        self.nav = Card(orientation="horizontal", size_hint_y=None, height=dp(78),
                        bg=(0.045, 0.050, 0.105, 1), radius=dp(24),
                        border=[0, 0, 0, 0], padding=[dp(12), dp(7), dp(12), dp(7)],
                        spacing=dp(7), elev=2)
        add_top_line(self.nav)
        self.nav_items = {
            "download": NavItem("dl"),
            "downloads": NavItem("files"),
            "settings": NavItem("gear"),
        }
        for k, item in self.nav_items.items():
            item.bind(on_release=lambda w, k=k: self.show_tab(k))
        shell.add_widget(self.nav)
        self.cur_nav = getattr(self, "_keep_nav", None) or "download"
        self._keep_nav = None
        self._apply_lang_shell()
        return shell

    def set_theme(self, name):
        """تبديل السمة (ليلي / نهاري) وإعادة بناء الواجهة فورًا."""
        if name == CFG.get("theme"):
            return
        CFG["theme"] = name
        save_cfg()

        def _do(dt):
            apply_theme(name)
            old = self.root
            self._keep_nav = "settings"
            new = self.build()
            if old is not None:
                Window.remove_widget(old)
            self.root = new
            Window.add_widget(new)
        Clock.schedule_once(_do, 0)

    def _on_keyboard(self, window, key, *args):
        if key in (27, 1001, 4) and getattr(self, "_pl_bar", None) is not None:
            if getattr(self, "_pl_full", False) and getattr(self, "_pl_fs_exit", None) is not None:
                self._pl_fs_exit()
                return True
            self._player_close()
            return True
        if key in (27, 1001, 4) and getattr(self, "_sn_wv", None) is not None:
            self._sniff_exit()
            return True
        if key in (27, 1001, 4) and getattr(self, "_ig_wv", None) is not None:
            self._ig_close()
            return True
        if key in (27, 1001, 4):
            # من صفحة التحميلات/الإعدادات: ارجع لتبويب التنزيل بدل ما تقفل التطبيق
            if getattr(self, "sel_mode", False):
                self._sel_exit()
                return True
            if getattr(self, "cur_nav", "download") == "search" and self.srch_channel:
                self._srch_leave_channel()
                return True
            if getattr(self, "cur_nav", "download") == "settings_dl":
                self.show_tab("settings")
                return True
            if getattr(self, "cur_nav", "download") != "download":
                self.show_tab("download")
                return True
            if getattr(self, "detail_page_open", False):
                self._close_detail_page()
                return True
        return False

    # ---------------- bottom navigation
    def _apply_lang_shell(self):
        if getattr(self, "nav", None) is None:
            return
        order = ["download", "downloads", "settings"]
        if self.rtl:
            order.reverse()          # عربي: تنزيل على اليمين زي Snaptube
        self.nav.clear_widgets()
        for k in order:
            self.nav.add_widget(self.nav_items[k])
        self.nav_items["download"].lbl.text = t("nav_download")
        self.nav_items["downloads"].lbl.text = t("downloads")
        self.nav_items["settings"].lbl.text = t("settings")
        self._build_dl_page()
        self.show_tab(getattr(self, "cur_nav", "download"))
        self.nav_items["downloads"].icon.badge = len(self.jobs)

    def show_tab(self, name):
        self.cur_nav = name
        self.content.clear_widgets()
        self.dl_view = None
        if name == "downloads":
            self.content.add_widget(self.dl_page)
            self.dl_view = self.dl_page
            self.set_dl_tab(self.dl_tab)
        elif name == "settings":
            self._build_settings_page()
            self.content.add_widget(self.set_page)
        elif name == "settings_dl":
            self._build_settings_dl_page()
            self.content.add_widget(self.setdl_page)
        elif name == "wa":
            self._build_wa_page()
            self.content.add_widget(self.wa_page)
        elif name == "search":
            self._build_search_page()
            self.content.add_widget(self.search_page)
        else:
            self.cur_nav = "download"
            self.content.add_widget(self.page_scroll)
        try:
            Animation.cancel_all(self.content, "opacity")
            self.content.opacity = 0
            Animation(opacity=1, duration=.22, t="out_quad").start(self.content)
        except Exception:
            self.content.opacity = 1
        act = {"wa": "download", "search": "download", "settings_dl": "settings"}.get(self.cur_nav, self.cur_nav)
        for k, item in self.nav_items.items():
            item.set_active(k == act)

    # ---------------- حالات واتساب
    def _build_wa_page(self):
        rtl = self.rtl
        al = "right" if rtl else "left"
        page = BoxLayout(orientation="vertical", padding=[dp(14), dp(40), dp(14), dp(8)],
                         spacing=dp(10))
        page.add_widget(TLabel(text=fix("حالات واتساب" if rtl else "WhatsApp Status"),
                               font_size=dp(24), bold=True, size_hint_y=None, height=dp(48),
                               halign=al))
        files, opened = wa_status_files()
        if files:
            page.add_widget(TLabel(text=fix(("%d حالة" if rtl else "%d statuses") % len(files)),
                                   font_size=dp(13), color=DIM, halign=al,
                                   size_hint_y=None, height=dp(22)))
        if WA_FOUND:
            info = "\n".join("%s  (%d/%d)" % (d.replace("/storage/emulated/0/", ""), c, tot) for d, c, tot in WA_FOUND[:4])
            page.add_widget(TLabel(text=fix(info), font_size=dp(9), color=DIM, halign=al,
                                   valign="top", shorten=False, size_hint_y=None,
                                   height=dp(12) * (min(4, len(WA_FOUND)) + 1)))
        sv = ScrollView(bar_width=dp(3), bar_color=(1, 1, 1, .25))
        box = GridLayout(cols=1, spacing=dp(8), size_hint_y=None, padding=[0, 0, 0, dp(4)])
        box.bind(minimum_height=box.setter("height"))
        if not files:
            if opened:
                msg = ("مفيش حالات لسه. افتح الحالات في واتساب الأول (لازم تتشاف عشان تتخزن) وبعدين اضغط تحديث."
                       if rtl else "No statuses yet. View them in WhatsApp first (they only get stored once viewed), then tap Refresh.")
            else:
                msg = ("مش قادر أفتح مجلد حالات واتساب. على أندرويد 11 وأحدث الوصول للمجلد ده ممنوع على أغلب التطبيقات، "
                       "فممكن ما يشتغلش من هنا. واتساب مالوش رابط للحالة عشان نحملها بيه."
                       if rtl else "Cannot open the WhatsApp status folder. On Android 11+ most apps are blocked from it, "
                       "so this may not work here. WhatsApp statuses have no link to download from.")
            box.add_widget(TLabel(text=fix(msg), halign="center", valign="middle", shorten=False,
                                  color=DIM, font_size=dp(14), size_hint_y=None, height=dp(200)))
        def make_row(mt, fp, size):
            is_vid = fp.lower().endswith((".mp4", ".3gp", ".mkv", ".mov", ".avi", ".webm"))
            row = Card(orientation="horizontal", size_hint_y=None, height=dp(84),
                       padding=dp(8), spacing=dp(10), bg=CARD)
            if is_vid:
                th = FloatLayout(size_hint=(None, 1), width=dp(68))
                vlbl = TLabel(text="VIDEO", halign="center", font_size=dp(12), color=DIM,
                              size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
                vimg = KImage(size_hint=(1, 1), pos_hint={"x": 0, "y": 0},
                              allow_stretch=True, keep_ratio=True, opacity=0)
                th.add_widget(vlbl)
                th.add_widget(vimg)

                def _vwork(fp=fp, vimg=vimg, vlbl=vlbl):
                    p = video_thumb(fp)
                    if p:
                        self.ui(self._wa_set_thumb, vimg, vlbl, p)
                _THUMB_POOL.submit(_vwork)
            else:
                th = AsyncImage(source=fp, size_hint=(None, 1), width=dp(68))
            col = BoxLayout(orientation="vertical", spacing=dp(2))
            col.add_widget(TLabel(text=fix(os.path.basename(fp)), font_size=dp(13), bold=True,
                                  halign=al))
            try:
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(mt))
            except Exception:
                when = ""
            col.add_widget(TLabel(text=fix("%s · %s" % (human_size(size), when)), font_size=dp(11),
                                  color=DIM, halign=al, size_hint_y=None, height=dp(18)))
            btn = RoundButton(text=fix("حفظ" if rtl else "Save"), font_size=dp(13),
                              size_hint=(None, None), size=(dp(78), dp(44)),
                              pos_hint={"center_y": .5}, bg=list(ACC1) + [1])
            btn.bind(on_release=lambda b, f=fp: self._wa_save(f, b))
            for w in ((btn, col, th) if rtl else (th, col, btn)):
                row.add_widget(w)
            return row
        wa_state = {"n": 0, "btn": None}

        def wa_more(*_):
            if wa_state["btn"] is not None:
                box.remove_widget(wa_state["btn"])
                wa_state["btn"] = None
            for mt, fp, size in files[wa_state["n"]:wa_state["n"] + 30]:
                box.add_widget(make_row(mt, fp, size))
            wa_state["n"] = min(len(files), wa_state["n"] + 30)
            if wa_state["n"] < len(files):
                mb = RoundButton(text=fix(("عرض المزيد (%d)" if rtl else "Show more (%d)")
                                          % (len(files) - wa_state["n"])),
                                 size_hint_y=None, height=dp(46), bg=CARD2)
                mb.bind(on_release=wa_more)
                box.add_widget(mb)
                wa_state["btn"] = mb
        if files:
            wa_more()
        sv.add_widget(box)
        page.add_widget(sv)
        refresh = RoundButton(text=fix("تحديث" if rtl else "Refresh"), bg=CARD2,
                              size_hint_y=None, height=dp(46))
        refresh.bind(on_release=lambda *_: self.show_tab("wa"))
        page.add_widget(refresh)
        self.wa_page = page

    def _wa_set_thumb(self, img, lbl, path):
        try:
            img.source = path
            img.reload()
            img.opacity = 1
            lbl.opacity = 0
        except Exception:
            pass

    def _wa_save(self, path, btn):
        try:
            import shutil
            dst_dir = sub_dir(self.download_dir,
                              SUB_VIDEO if os.path.splitext(path)[1].lower() in _VIDEO_EXTS else SUB_IMAGE)
            dst = os.path.join(dst_dir, os.path.basename(path))
            shutil.copy2(path, dst)
            media_scan(dst)
            btn.text = fix("تم ✓" if self.rtl else "Saved ✓")
            SOUND.play("done")
        except Exception as e:
            self._toast(("فشل الحفظ: " if self.rtl else "Save failed: ") + str(e)[:70])

    def _refresh_settings(self, *_):
        if getattr(self, "cur_nav", None) == "settings":
            self.show_tab("settings")

    # ---------------- dedicated content pages
    def _open_detail_page(self, title=None):
        """ينقل الفيديو/البلاي ليست/الصور لصفحة مستقلة تستغل مساحة الشاشة."""
        if self.detail_page_open:
            return
        # انقل عناصر الصفحة الرئيسية بعيدًا مؤقتًا مع الاحتفاظ بها للرجوع.
        root = self.home_root
        for w in list(root.children):
            root.remove_widget(w)
        self.page_scroll.remove_widget(root)
        self.page_scroll.do_scroll_y = False
        self.page_scroll.do_scroll_x = False

        page = BoxLayout(orientation="vertical", size_hint=(1, 1),
                         padding=[dp(12), dp(24), dp(12), dp(8)], spacing=dp(8))
        top = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        back = RoundButton(text=fix("رجوع" if self.rtl else "Back"),
                           size_hint_x=None, width=dp(86), bg=CARD2, radius=dp(18))
        back.bind(on_release=self._close_detail_page)
        ttl = TLabel(text=fix(title or ("قائمة التنزيل" if self.rtl else "Download")),
                     font_size=dp(18), bold=True, halign="right" if self.rtl else "left")
        if self.rtl:
            top.add_widget(ttl); top.add_widget(back)
        else:
            top.add_widget(back); top.add_widget(ttl)
        page.add_widget(top)

        # محتوى التفاصيل: بيانات الرابط، تبويباته، خياراته، ثم القائمة التي تتمدد.
        if self.info_card.parent is not None:
            self.info_card.parent.remove_widget(self.info_card)
        page.add_widget(self.info_card)
        if self.tabs.parent is not None:
            self.tabs.parent.remove_widget(self.tabs)
        page.add_widget(self.tabs)
        controls = self.pl_ctrl if self.pl_mode else (self.img_ctrl if self.img_mode else None)
        if controls is not None:
            if controls.parent is not None:
                controls.parent.remove_widget(controls)
            page.add_widget(controls)
        self.list_scroll.size_hint_y = 1
        self.list_scroll.height = 0
        self.list_scroll.bar_width = dp(3)
        if self.list_scroll.parent is not None:
            self.list_scroll.parent.remove_widget(self.list_scroll)
        page.add_widget(self.list_scroll)
        # شريط التقدم يظل ظاهرًا لكن مضغوطًا.
        self.prog_card.height = dp(76)
        if self.prog_card.parent is not None:
            self.prog_card.parent.remove_widget(self.prog_card)
        page.add_widget(self.prog_card)
        self.detail_root = page
        self.page_scroll.add_widget(page)
        self.detail_page_open = True

    def _close_detail_page(self, *_):
        """Return to a clean home screen and reset the home container to the viewport."""
        if not getattr(self, "detail_page_open", False):
            return True

        root = getattr(self, "home_root", None)
        page = getattr(self, "detail_root", None)
        viewport = getattr(self, "page_scroll", None)
        if root is None or viewport is None:
            return True

        # Remove the detail page and detach every reusable widget before rebuilding.
        if page is not None and page.parent is not None:
            page.parent.remove_widget(page)
        reusable = (self.hero, self.chips, self.hdr, self.input_card, self.analyze_btn, self.dyn,
                    self.prog_card, self.list_scroll, self.info_card, self.tabs,
                    self.pl_ctrl, self.img_ctrl)
        for widget in reusable:
            if widget is not None and widget.parent is not None:
                widget.parent.remove_widget(widget)

        # Reset analysis-specific state: returning should look like a fresh launch.
        self.dyn.clear_widgets()
        self.list_box.clear_widgets()
        self.has_info = False
        self.pl_mode = False
        self.img_mode = False
        self.info_data = {}
        self.video_items, self.audio_items, self.image_items = [], [], []
        # Clear the URL field without assuming a particular widget attribute name.
        for child in self.input_card.walk():
            if isinstance(child, TextInput):
                child.text = ""

        root.clear_widgets()
        self.prog_card.height = dp(106)
        self.list_scroll.height = 0
        self.list_scroll.size_hint_y = 1
        self.list_scroll.bar_width = dp(3)

        # Rebuild the home widgets in their original order.
        self.hdr.height = dp(84)
        if self.hdr.parent is not self.hero:
            self.hero.add_widget(self.hdr)
        root.add_widget(self.hero)
        root.add_widget(self.input_card)
        root.add_widget(self.analyze_btn)
        root.add_widget(self.chips)
        root.add_widget(self.dyn)
        # المساحة المرنة: لازم list_scroll يرجع هنا (زي أول تشغيل) عشان ياخد
        # الفراغ ويطلّع باقي العناصر لفوق. من غيره العناصر بتنزل لتحت.
        root.add_widget(self.list_scroll)
        root.add_widget(self.prog_card)

        # نفس إعداد أول تشغيل بالظبط: الصفحة بحجم الشاشة.
        root.size_hint = (1, 1)
        if root.parent is not None and root.parent is not viewport:
            root.parent.remove_widget(root)
        if root.parent is None:
            viewport.add_widget(root)
        viewport.do_scroll_y = False
        viewport.do_scroll_x = False
        viewport.scroll_y = 1
        self.detail_root = None
        self.detail_page_open = False

        # Reapply the viewport height after Kivy has completed the layout pass.
        def _finish_home_layout(_dt):
            if not self.detail_page_open and root.parent is viewport:
                root.size_hint = (1, 1)
        Clock.schedule_once(_finish_home_layout, 0)
        return True

    # ---------------- language
    def apply_lang(self):
        self.rtl = STATE["lang"] == "ar"
        al = "right" if self.rtl else "left"

        self.hdr.clear_widgets()
        self.hdr.add_widget(self.hdr_text)
        self._hdr_layout()
        self.tagline.text = t("tagline")

        self.status_row.clear_widgets()
        for w in ((self.pct, self.status) if self.rtl else (self.status, self.pct)):
            self.status_row.add_widget(w)
        self.status.halign = al
        self.detail.halign = al
        self.pct.halign = "left" if self.rtl else "right"

        self.hint_lbl.text = t("hint")
        self.hint_lbl.halign = al
        self._on_text(None, self.url_input.text)
        self.analyze_btn.text = t("analyzing") if self.analyze_btn.disabled else t("analyze")
        self.wa_btn.text = fix("حالات واتساب" if self.rtl else "WhatsApp Status")
        self.tab_video.text = t("tab_video")
        self.tab_audio.text = t("tab_audio")
        self.tab_images.text = t("tab_images")

        self.render_info()
        self.render_status()
        if self.has_info:
            self.set_tab(self.tab)
        else:
            self.show_empty()
        self._apply_lang_shell()

    def set_lang(self, code):
        STATE["lang"] = code
        CFG["lang"] = code
        save_cfg()
        self.apply_lang()

    # ---------------- واجهة: مساعدات التصميم
    def _hdr_layout(self):
        """ترتيب الهيدر: شعار + اسم البرنامج (بيتعكس في العربي)."""
        ht = self.hdr_text
        ht.clear_widgets()
        ht.add_widget(Widget())
        for w in ((self.hdr_col, self.logo) if self.rtl else (self.logo, self.hdr_col)):
            ht.add_widget(w)
        ht.add_widget(Widget())
        al = "right" if self.rtl else "left"
        self.brand.halign = al
        self.tagline.halign = al

    def _on_url_focus(self, w, focused):
        """حلقة لونية حوالين حقل الرابط وقت الكتابة."""
        try:
            self.input_card.border = (list(ACC1) + [.95]) if focused else list(BORDER)
        except Exception:
            pass
        if focused and getattr(self, "cur_nav", "download") == "download" \
                and not getattr(self, "detail_page_open", False):
            def _go(dt):
                try:
                    self.url_input.focus = False
                except Exception:
                    pass
                self.open_search_page()
            Clock.schedule_once(_go, 0)

    def on_start(self):
        """شاشة افتتاحية قصيرة بالشعار."""
        threading.Thread(target=warm_up_ytdlp, daemon=True).start()
        Clock.schedule_once(self._restore_active, 1.5)
        Clock.schedule_once(self._webview_warmup, 0.3)
        Clock.schedule_once(self._show_player_log, 3.0)
        try:
            sp = Splash(size_hint=(1, 1))
            Window.add_widget(sp)

            def _done():
                try:
                    Window.remove_widget(sp)
                except Exception:
                    pass
                Clock.schedule_once(self._storage_check, 0.25)
            sp.play(_done)
        except Exception:
            Clock.schedule_once(self._storage_check, 1.0)

    # ---------------- APK: إذن الملفات (عشان التحميل يتحفظ في مجلد NovaGet)
    def _storage_check(self, *_):
        if not is_apk():
            return
        request_legacy_storage()
        if has_all_files_access() or getattr(self, "_storage_asked", False):
            return
        self._storage_asked = True
        try:
            mv = ModalView(size_hint=(.9, None), height=dp(300), background="",
                           background_color=(0, 0, 0, .72), auto_dismiss=False)
            card = Card(orientation="vertical", padding=dp(18), spacing=dp(10), bg=CARD, radius=dp(24))
            title = TLabel(text=L("Allow file access", "السماح بالوصول للملفات"), bold=True,
                           font_size=dp(19), size_hint_y=None, height=dp(34), halign="center")
            body = TLabel(text=L("NovaGet saves your downloads in the NovaGet folder on your phone. "
                                 "Turn on \"Allow access to all files\" for NovaGet, then come back.",
                                 "نوفاجت بيحفظ التحميلات في مجلد NovaGet على الموبايل. "
                                 "فعّل \"السماح بالوصول إلى كل الملفات\" لـ NovaGet وارجع للتطبيق."),
                          font_size=dp(14), color=DIM, halign="center", valign="middle")
            body.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
            row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(10))
            b_later = RoundButton(text=L("Later", "بعدين"), bg=CARD2, radius=dp(24), font_size=dp(15))
            b_go = RoundButton(text=L("Open settings", "فتح الإعدادات"), bg=list(ACC1) + [1],
                               radius=dp(24), font_size=dp(15), bold=True)

            def _go(*_a):
                mv.dismiss()
                open_all_files_settings()
            b_go.bind(on_release=_go)
            b_later.bind(on_release=lambda *_a: mv.dismiss())
            for b in ((b_go, b_later) if self.rtl else (b_later, b_go)):
                row.add_widget(b)
            card.add_widget(title)
            card.add_widget(body)
            card.add_widget(row)
            mv.add_widget(card)
            mv.open()
        except Exception as e:
            nlog("storage dialog: %s" % e)

    def _storage_recheck(self, *_):
        """بعد الرجوع من الإعدادات: لو الإذن اتفعّل نحوّل مجلد التحميل لـ NovaGet."""
        try:
            if not is_apk():
                return
            cur = getattr(self, "download_dir", "") or ""
            if cur.startswith("/storage/emulated/0/") or cur.startswith("/sdcard/"):
                return
            nd = get_download_dir()
            if nd != cur:
                self.download_dir = nd
                self._dt = ("saves", nd)
                self.render_status()
        except Exception as e:
            nlog("storage recheck: %s" % e)

    def _group(self, box, title=None):
        """مجموعة إعدادات: عنوان صغير + كارت بيحتوي العناصر."""
        al = "right" if self.rtl else "left"
        if title:
            box.add_widget(TLabel(text=title, font_size=dp(13), bold=True, color=DIM,
                                  size_hint_y=None, height=dp(28), halign=al))
        g = Card(orientation="vertical", size_hint_y=None, padding=dp(14), spacing=dp(10),
                 bg=CARD, radius=dp(20), elev=1)
        g.bind(minimum_height=g.setter("height"))
        box.add_widget(g)
        return g

    def _sub(self, g, text):
        al = "right" if self.rtl else "left"
        g.add_widget(TLabel(text=text, font_size=dp(13), color=DIM,
                            size_hint_y=None, height=dp(20), halign=al))

    def _account_row(self, g, name, tag, color, ok, on_login, on_logout):
        al = "right" if self.rtl else "left"
        row = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(12))
        tile = Card(size_hint=(None, None), size=(dp(44), dp(44)), pos_hint={"center_y": .5},
                    radius=dp(13), bg=list(color) + [1], border=[0, 0, 0, 0])
        tile.add_widget(Label(text=tag, bold=True, font_size=dp(16), color=(1, 1, 1, 1)))
        col = BoxLayout(orientation="vertical")
        col.add_widget(TLabel(text=name, font_size=dp(15), bold=True, halign=al))
        col.add_widget(TLabel(text=L("Connected", "متصل") if ok else L("Not connected", "غير متصل"),
                              font_size=dp(12), color=GREEN if ok else DIM, halign=al))
        btn = RoundButton(text=L("Log out", "تسجيل الخروج") if ok else L("Log in", "تسجيل الدخول"),
                          font_size=dp(13), size_hint=(None, None), size=(dp(116), dp(40)),
                          pos_hint={"center_y": .5}, radius=dp(20),
                          bg=list(CARD2) if ok else list(ACC1) + [1])
        btn.bind(on_release=lambda *_: (on_logout() if ok else on_login()))
        for w in ((btn, col, tile) if self.rtl else (tile, col, btn)):
            row.add_widget(w)
        g.add_widget(row)

    def _seg(self, options, getter, setter):
        """شريط اختيار (Segmented control): واحد بس مفعّل بتدرّج الهوية."""
        track = Card(size_hint_y=None, height=dp(50), padding=dp(4), spacing=dp(4),
                     bg=CARD2, radius=dp(25), border=list(TRANSP))
        btns = []

        def refresh():
            cur = getter()
            for key, b in btns:
                on = key == cur
                b.bg = list(ACC1) + [1] if on else list(TRANSP)
                b.color = WHITE if on else DIM

        for key, label in options:
            b = RoundButton(text=label, font_size=dp(14), radius=dp(21))
            b.bind(on_release=lambda w, k=key: (setter(k), refresh()))
            btns.append((key, b))
            track.add_widget(b)
        refresh()
        return track

    def _set_sound(self, key):
        CFG["sound"] = (key == "on")
        save_cfg()
        SOUND.play("queue")

    def _set_notify(self, key):
        CFG["notify"] = (key == "on")
        save_cfg()
        if CFG["notify"]:
            NOTIF.request_permission()
        else:
            with self.lock:
                for j in list(self.jobs):
                    NOTIF.cancel_job(j.id)
                    j.notified = False
                    j.n_sig = None

    def open_settings(self, *_):
        self.show_tab("settings")

    def _build_settings_dl_page(self):
        """صفحة إعدادات التحميل: السرعة، بيانات الهاتف، عدد التحميلات (واي فاي / بيانات)."""
        al = "right" if self.rtl else "left"
        page = BoxLayout(orientation="vertical", padding=[dp(14), dp(40), dp(14), dp(8)],
                         spacing=dp(10))
        top = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        back = RoundButton(text=L("Back", "رجوع"), size_hint_x=None, width=dp(86),
                           bg=CARD2, radius=dp(18))
        back.bind(on_release=lambda *_: self.show_tab("settings"))
        ttl = TLabel(text=L("Download settings", "إعدادات التحميل"), font_size=dp(20),
                     bold=True, halign=al)
        for w in ((ttl, back) if self.rtl else (back, ttl)):
            top.add_widget(w)
        page.add_widget(top)
        sv = NScroll(bar_width=dp(3), bar_color=(1, 1, 1, .25))
        real_box = GridLayout(cols=1, spacing=dp(10), size_hint_y=None, padding=[0, 0, 0, dp(16)])
        real_box.bind(minimum_height=real_box.setter("height"))
        box = _GroupSink(self, real_box)

        def head(text):
            box.new_group(text)

        # السرعة
        head(t("speed"))
        sp_val = TLabel(text=fix(speed_text(CFG["speed_i"])), font_size=dp(16), bold=True,
                        color=ACC2, halign="center", size_hint_y=None, height=dp(26))
        sp_sl = NSlider(min=0, max=SPEED_MAX, step=1, value=CFG["speed_i"],
                       size_hint_y=None, height=dp(44), value_track=True,
                       value_track_color=list(ACC1) + [1], cursor_size=(dp(26), dp(26)))

        _last = [0.0]

        def on_speed(w, v):
            i = int(round(v))
            sp_val.text = fix(speed_text(i))
            if i != CFG["speed_i"]:
                CFG["speed_i"] = i
                save_cfg()
                now = time.time()
                if now - _last[0] > 0.05:
                    _last[0] = now
                    SOUND.play("tap")

        sp_sl.bind(value=on_speed)
        box.add_widget(sp_sl)
        box.add_widget(sp_val)

        # عدد الأجزاء لكل تحميل (1 - 16)
        head(L("Parts per download", "عدد الأجزاء لكل تحميل"))
        fr_val = TLabel(text=str(frags_n()), font_size=dp(16), bold=True,
                        color=ACC2, halign="center", size_hint_y=None, height=dp(26))
        fr_sl = NSlider(min=FRAGS_MIN, max=FRAGS_MAX, step=1, value=frags_n(),
                       size_hint_y=None, height=dp(44), value_track=True,
                       value_track_color=list(ACC1) + [1], cursor_size=(dp(26), dp(26)))
        _lastf = [0.0]

        def on_frags(w, v):
            i = int(round(v))
            fr_val.text = str(i)
            if i != CFG.get("frags"):
                CFG["frags"] = i
                save_cfg()
                now = time.time()
                if now - _lastf[0] > 0.05:
                    _lastf[0] = now
                    SOUND.play("tap")

        fr_sl.bind(value=on_frags)
        box.add_widget(fr_sl)
        box.add_widget(fr_val)
        box.add_widget(TLabel(
            text=L("More parts = faster, but some sites may block too many connections. Applies to new downloads.",
                   "أجزاء أكتر = أسرع، لكن بعض المواقع بتمنع الاتصالات الكتيرة. بيسري على التحميلات الجديدة."),
            font_size=dp(11), color=DIM, halign=al, size_hint_y=None, height=dp(34)))

        # التحميل ببيانات الهاتف
        head(L("Download with mobile data", "التحميل ببيانات الهاتف"))

        def set_mobile(k):
            CFG["mobile_ok"] = (k == "on")
            save_cfg()
            _NET["t"] = 0.0
            self._pump()
        box.add_widget(self._seg(
            [("on", L("Allowed", "مسموح")), ("off", L("Not allowed", "غير مسموح"))],
            lambda: "on" if CFG.get("mobile_ok", True) else "off", set_mobile))

        # عدد التحميلات المتزامنة: رقم للواي فاي ورقم لبيانات الهاتف
        def counter(title, key):
            head(title)
            r = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(10))
            minus = RoundButton(text="-", font_size=dp(24), size_hint_x=None, width=dp(64))
            plus = RoundButton(text="+", font_size=dp(24), size_hint_x=None, width=dp(64))
            val = TLabel(text=str(CFG[key]), font_size=dp(22), bold=True, halign="center")

            def bump(d):
                n = max(1, min(MAX_PAR, CFG[key] + d))
                CFG[key] = n
                save_cfg()
                val.text = str(n)
                self._pump()
            minus.bind(on_release=lambda *_: bump(-1))
            plus.bind(on_release=lambda *_: bump(1))
            r.add_widget(minus)
            r.add_widget(val)
            r.add_widget(plus)
            box.add_widget(r)

        counter(L("Simultaneous downloads - Wi-Fi", "عدد التحميلات في نفس الوقت - واي فاي"),
                "max_dl_wifi")
        counter(L("Simultaneous downloads - Mobile data", "عدد التحميلات في نفس الوقت - بيانات الهاتف"),
                "max_dl_mobile")
        sv.add_widget(real_box)
        page.add_widget(sv)
        self.setdl_page = page

    def _build_settings_page(self):
        al = "right" if self.rtl else "left"
        page = BoxLayout(orientation="vertical", padding=[dp(14), dp(40), dp(14), dp(8)],
                         spacing=dp(8))
        page.add_widget(TLabel(text=t("settings"), font_size=dp(26), bold=True,
                               size_hint_y=None, height=dp(48), halign=al))
        sv = ScrollView(bar_width=dp(3), bar_color=(1, 1, 1, .25))
        box = GridLayout(cols=1, spacing=dp(10), size_hint_y=None, padding=[0, 0, 0, dp(16)])
        box.bind(minimum_height=box.setter("height"))

        # المظهر واللغة
        g = self._group(box, L("Appearance", "المظهر"))
        self._sub(g, t("language"))
        g.add_widget(self._seg([("en", "English"), ("ar", fix("العربية"))],
                               lambda: STATE["lang"],
                               lambda c: Clock.schedule_once(lambda dt: self.set_lang(c), 0)))
        self._sub(g, L("Theme", "السمة"))
        g.add_widget(self._seg([("dark", L("Night", "ليلية")), ("light", L("Day", "نهارية"))],
                               lambda: CFG.get("theme") or "dark",
                               lambda c: self.set_theme(c)))

        # الصوت والإشعارات
        g = self._group(box, L("Sound & alerts", "الصوت والتنبيهات"))
        self._sub(g, t("sound"))
        g.add_widget(self._seg([("on", t("on")), ("off", t("off"))],
                               lambda: "on" if CFG["sound"] else "off", self._set_sound))
        self._sub(g, t("notifications"))
        g.add_widget(self._seg([("on", t("on")), ("off", t("off"))],
                               lambda: "on" if CFG["notify"] else "off", self._set_notify))

        # إعدادات التحميل (السرعة + عدد التحميلات + بيانات الهاتف) في صفحة واحدة
        g = self._group(box, t("downloads"))
        dl_txt = L("Download settings", "إعدادات التحميل")
        dl_txt = ("\u2039   " + dl_txt) if self.rtl else (dl_txt + "   \u203a")
        dl_btn = RoundButton(text=dl_txt, font_size=dp(15), size_hint_y=None, height=dp(50),
                             bg=CARD2, radius=dp(25))
        dl_btn.bind(on_release=lambda *_: Clock.schedule_once(
            lambda dt: self.show_tab("settings_dl"), 0))
        g.add_widget(dl_btn)

        # الحسابات: إنستجرام وفيسبوك (تسجيل دخول جوه التطبيق)
        g = self._group(box, L("Accounts", "الحسابات"))
        ig_ok = bool((CFG.get("ig_session") or "").strip())
        self._account_row(
            g, "Instagram", "IG", (.80, .22, .50), ig_ok,
            lambda: Clock.schedule_once(self.ig_login, 0.2),
            lambda: (self.ig_logout(), self._refresh_settings()))
        fb_ok = bool((CFG.get("fb_cookies") or "").strip())
        self._account_row(
            g, "Facebook", "f", (.09, .47, .95), fb_ok,
            lambda: Clock.schedule_once(lambda dt: self.ig_login(site="fb"), 0.2),
            lambda: (self.fb_logout(), self._refresh_settings()))

        # معلومات الخطوط/الصوت
        lines = list(DIAG if (not FONT_OK or not AR_OK) else PROBLEMS)
        lines.extend(_STORAGE_WARNINGS[-5:])
        if yt_dlp is None:
            lines.append("Missing dependency: yt-dlp. Install with: pip install -U yt-dlp")
        if lines:
            box.add_widget(TLabel(text=fix("\n".join(lines)), font_size=dp(10), color=DIM,
                                  halign="left", valign="top", shorten=False,
                                  size_hint_y=None, height=dp(15) * (len(lines) + 1)))

        # عن البرنامج
        about = Card(orientation="horizontal", size_hint_y=None, height=dp(86), padding=dp(16),
                     spacing=dp(14), bg=CARD, radius=dp(20), elev=1)
        logo = LogoMark(glow=False, size_hint=(None, None), size=(dp(50), dp(50)),
                        pos_hint={"center_y": .5})
        col = BoxLayout(orientation="vertical")
        col.add_widget(TLabel(text=brand_markup(),
                              font_size=dp(22), bold=True, shorten=False, halign=al))
        col.add_widget(TLabel(text="v" + APP_VERSION, font_size=dp(12), color=DIM, halign=al))
        for w in ((col, logo) if self.rtl else (logo, col)):
            about.add_widget(w)
        box.add_widget(about)

        sv.add_widget(box)
        page.add_widget(sv)
        self.set_page = page

    # ---------------- Instagram in-app login (Android WebView)
    def _toast(self, text, secs=2.6):
        mv = ModalView(size_hint=(.86, None), height=dp(84), background="",
                       background_color=(0, 0, 0, .55))
        card = Card(orientation="vertical", padding=dp(12), bg=CARD, radius=dp(18), elev=1)
        card.add_widget(TLabel(text=fix(text), font_size=dp(14), halign="center",
                               valign="middle", shorten=False))
        mv.add_widget(card)
        mv.open()
        Clock.schedule_once(lambda dt: mv.dismiss(), secs)

    def _ui_call(self, fn, timeout=4.0):
        """يشغّل fn على الـ UI thread بتاع أندرويد ويرجّع النتيجة (WebView/CookieManager
        لازم يتلمسوا من thread واحد بس، وهو الـ UI thread، مش thread بتاع Kivy)."""
        try:
            run_on_ui_thread = _main_ui_decorator()
        except Exception:
            return fn()
        box = {}
        ev = threading.Event()

        @run_on_ui_thread
        def _run():
            try:
                box["r"] = fn()
            except Exception as ex:
                box["e"] = ex
            ev.set()
        _run()
        ev.wait(timeout)
        return box.get("r")

    def _webview_warmup(self, *_):
        _webview_prepare()
        # أول استخدام للـ WebView في العملية بيربطه بالـ thread اللي استخدمه، فنخليه الـ UI thread
        try:
            from jnius import autoclass
            CM = autoclass("android.webkit.CookieManager")
            self._ui_call(lambda: CM.getInstance(), timeout=3.0)
        except Exception:
            pass

    def ig_login(self, *_, site="ig"):
        """يفتح صفحة تسجيل دخول إنستجرام جوه التطبيق ويلقط sessionid لوحده."""
        try:
            from jnius import autoclass
            run_on_ui_thread = _main_ui_decorator()
        except Exception:
            self._toast("تسجيل الدخول مش متاح على الجهاز/البيئة دي"
                        if self.rtl else "Login is not available in this environment", 3)
            return
        if getattr(self, "_ig_wv", None) is not None:
            return
        self._lg_site = site
        try:
            PythonActivity = None
            for _n in ("org.kivy.android.PythonActivity", "org.renpy.android.PythonActivity"):
                try:
                    PythonActivity = autoclass(_n)
                    break
                except Exception:
                    continue
            if PythonActivity is None:
                raise RuntimeError("PythonActivity not found")
            JString = autoclass("java.lang.String")
            from jnius import cast
            WebView = autoclass("android.webkit.WebView")
            WebViewClient = autoclass("android.webkit.WebViewClient")
            CookieManager = autoclass("android.webkit.CookieManager")
            LinearLayout = autoclass("android.widget.LinearLayout")
            TextView = autoclass("android.widget.TextView")
            LP = autoclass("android.view.ViewGroup$LayoutParams")
        except Exception as ex:
            self._toast("WebView: %s" % str(ex)[:80])
            return
        app = self
        lg_url = ("https://m.facebook.com/login/" if site == "fb"
                  else "https://www.instagram.com/accounts/login/")
        hint = ("سجّل دخول إنستجرام هنا، وهيقفل لوحده أول ما يتسجّل. (زرار الرجوع للإلغاء)"
                if self.rtl else
                "Log in to Instagram here. This closes by itself once you are in. (Back = cancel)")

        if site == "fb":
            hint = ("سجّل دخول فيسبوك هنا، وهيقفل لوحده أول ما يتسجّل. (زرار الرجوع للإلغاء)"
                    if self.rtl else
                    "Log in to Facebook here. This closes by itself once you are in. (Back = cancel)")

        @run_on_ui_thread
        def _open():
            try:
                act = PythonActivity.mActivity
                dens = act.getResources().getDisplayMetrics().density
                wv = WebView(act)
                st = wv.getSettings()
                st.setJavaScriptEnabled(True)
                st.setDomStorageEnabled(True)
                st.setUserAgentString(_UA_MOBILE)
                cm = CookieManager.getInstance()
                cm.setAcceptCookie(True)
                try:
                    cm.setAcceptThirdPartyCookies(wv, True)
                except Exception:
                    pass
                wv.setWebViewClient(WebViewClient())     # عشان التنقل يفضل جوه الـ WebView
                layout = LinearLayout(act)
                layout.setOrientation(LinearLayout.VERTICAL)
                layout.setBackgroundColor(0xFF0B0F1A - (1 << 32))
                layout.setPadding(0, int(30 * dens), 0, 0)
                tv = TextView(act)
                tv.setText(cast("java.lang.CharSequence", JString(hint)))
                tv.setTextColor(-1)
                tv.setPadding(int(12 * dens), int(6 * dens), int(12 * dens), int(6 * dens))
                layout.addView(tv, LP(-1, -2))
                layout.addView(wv, LP(-1, -1))
                act.addContentView(layout, LP(-1, -1))
                wv.loadUrl(lg_url)
                app._ig_layout, app._ig_wv = layout, wv
            except Exception as ex:
                app._ig_err = str(ex)

        self._ig_err = ""
        self._ig_t0 = time.time()
        _open()
        Clock.schedule_interval(self._ig_poll, 1.0)

    def ig_logout(self):
        CFG["ig_session"] = ""
        CFG["ig_cookies"] = ""
        save_cfg()
        try:
            from jnius import autoclass
            CM = autoclass("android.webkit.CookieManager")

            def _clr():
                cm = CM.getInstance()
                try:
                    cm.removeAllCookies(None)
                except Exception:
                    cm.removeAllCookie()
                try:
                    cm.flush()
                except Exception:
                    pass
            self._ui_call(_clr)
        except Exception:
            pass
        self._toast("اتمسحت جلسة إنستجرام من التطبيق" if self.rtl else "Instagram session erased")

    def fb_logout(self):
        CFG["fb_cookies"] = ""
        save_cfg()
        try:
            from jnius import autoclass
            CM = autoclass("android.webkit.CookieManager")

            def _clr():
                cm = CM.getInstance()
                try:
                    cm.removeAllCookies(None)
                except Exception:
                    cm.removeAllCookie()
            self._ui_call(_clr)
        except Exception:
            pass
        self._toast("اتمسحت جلسة فيسبوك من التطبيق" if self.rtl else "Facebook session erased")

    def _ig_cookie_string(self):
        site = getattr(self, "_lg_site", "ig")
        try:
            from jnius import autoclass
            CM = autoclass("android.webkit.CookieManager")

            def _get(dom):
                return str(CM.getInstance().getCookie(dom) or "")
            if site == "fb":
                parts = []
                for dom in ("https://www.facebook.com", "https://m.facebook.com"):
                    parts.append(self._ui_call(lambda d=dom: _get(d)) or "")
                jar = {}
                for p in "; ".join(x for x in parts if x).split(";"):
                    nm, _e, val = p.strip().partition("=")
                    if nm and val:
                        jar[nm] = val
                return "; ".join("%s=%s" % kv for kv in jar.items())
            return self._ui_call(lambda: _get("https://www.instagram.com")) or ""
        except Exception:
            return ""

    def _ig_cookie_sessionid(self):
        ck = self._ig_cookie_string()
        if getattr(self, "_lg_site", "ig") == "fb":
            ok = re.search(r"(?:^|;\s*)c_user=", ck) and re.search(r"(?:^|;\s*)xs=", ck)
            return "fb" if ok else ""
        m = re.search(r"(?:^|;\s*)sessionid=([^;]+)", ck)
        return m.group(1).strip() if m else ""

    def _ig_poll(self, dt):
        if getattr(self, "_ig_wv", None) is None:
            if getattr(self, "_ig_err", ""):
                self._toast("WebView: %s" % self._ig_err[:80])
                self._ig_err = ""
                return False
            # لسه بيتفتح (الـ UI thread بيشتغل بعدين) أو اتقفل/اتلغى
            return time.time() < getattr(self, "_ig_t0", 0) + 8
        sid = self._ig_cookie_sessionid()
        if sid:
            if getattr(self, "_lg_site", "ig") == "fb":
                CFG["fb_cookies"] = self._ig_cookie_string()
                nm = "فيسبوك" if self.rtl else "Facebook"
            else:
                CFG["ig_session"] = sid
                CFG["ig_cookies"] = self._ig_cookie_string()
                nm = "إنستجرام" if self.rtl else "Instagram"
            save_cfg()
            self._ig_close()
            self._toast(("تم تسجيل الدخول في %s ✓" if self.rtl else "%s login saved ✓") % nm)
            self._refresh_settings()
            return False
        return True

    # ---------------- التقاط رابط الفيديو الحقيقي من أي موقع (متصفح داخلي WebView)
    def _sniff_available(self):
        try:
            from jnius import autoclass
            _main_ui_decorator()
            autoclass("android.webkit.WebView")
            return True
        except Exception:
            return False

    def _sniff_open(self, url):
        try:
            from jnius import autoclass, cast, PythonJavaClass, java_method
            run_on_ui_thread = _main_ui_decorator()
            PythonActivity = None
            for _n in ("org.kivy.android.PythonActivity", "org.renpy.android.PythonActivity"):
                try:
                    PythonActivity = autoclass(_n)
                    break
                except Exception:
                    continue
            if PythonActivity is None:
                raise RuntimeError("PythonActivity not found")
            WebView = autoclass("android.webkit.WebView")
            WebViewClient = autoclass("android.webkit.WebViewClient")
            CookieManager = autoclass("android.webkit.CookieManager")
            LinearLayout = autoclass("android.widget.LinearLayout")
            LLP = autoclass("android.widget.LinearLayout$LayoutParams")
            TextView = autoclass("android.widget.TextView")
            AButton = autoclass("android.widget.Button")
            JString = autoclass("java.lang.String")
            LP = autoclass("android.view.ViewGroup$LayoutParams")
        except Exception as ex:
            self.set_status("read_fail", RED, "WebView: %s" % str(ex)[:120])
            return
        if getattr(self, "_sn_wv", None) is not None:
            return
        app = self
        self._sn = {"url": url, "ref": url, "t0": time.time(), "urls": [], "seen": set(),
                    "last_new": 0.0, "iframe": False, "title": "", "poster": "",
                    "busy": False, "busy_t": 0.0, "revealed": False}
        self._sn_err = ""
        hint = ("جاري التقاط الفيديو... سيب الصفحة شوية (لو ما اشتغلش اضغط تشغيل) وبعدها اضغط تم."
                if self.rtl else
                "Capturing the video... wait a few seconds (tap play if needed), then tap Done.")
        cancel_txt = "تم" if self.rtl else "Done"

        class _Click(PythonJavaClass):
            __javainterfaces__ = ["android/view/View$OnClickListener"]
            __javacontext__ = "app"

            @java_method("(Landroid/view/View;)V")
            def onClick(self, v):
                Clock.schedule_once(lambda dt: app._sniff_exit(), 0)
        self._sn_click = _Click()

        def cs(x):
            return cast("java.lang.CharSequence", JString(str(x)))

        @run_on_ui_thread
        def _open():
            try:
                act = PythonActivity.mActivity
                dens = act.getResources().getDisplayMetrics().density
                wv = WebView(act)
                st = wv.getSettings()
                st.setJavaScriptEnabled(True)
                st.setDomStorageEnabled(True)
                try:
                    st.setMediaPlaybackRequiresUserGesture(False)
                except Exception:
                    pass
                try:
                    st.setMixedContentMode(0)
                except Exception:
                    pass
                st.setUserAgentString(_UA_MOBILE)
                cm = CookieManager.getInstance()
                cm.setAcceptCookie(True)
                try:
                    cm.setAcceptThirdPartyCookies(wv, True)
                except Exception:
                    pass
                wv.setWebViewClient(WebViewClient())
                layout = LinearLayout(act)
                layout.setOrientation(LinearLayout.VERTICAL)
                layout.setBackgroundColor(0xFF0B0F1A - (1 << 32))
                layout.setPadding(0, int(30 * dens), 0, 0)
                bar = LinearLayout(act)
                bar.setOrientation(LinearLayout.HORIZONTAL)
                tv = TextView(act)
                tv.setText(cs(hint))
                tv.setTextColor(-1)
                try:
                    tv.setTextSize(12.0)
                except Exception:
                    pass
                tv.setPadding(int(12 * dens), int(6 * dens), int(8 * dens), int(6 * dens))
                bar.addView(tv, LLP(0, -2, 1.0))
                cb = AButton(act)
                cb.setText(cs(cancel_txt))
                cb.setOnClickListener(app._sn_click)
                bar.addView(cb, LLP(-2, -2))
                layout.addView(bar, LP(-1, -2))
                layout.addView(wv, LP(-1, -1))
                # الصفحة بحجم الشاشة الكامل جوه حاوية 1 بكسل (متقصوصة): بتشتغل وتشغّل الفيديو
                # لوحدها من غير ما تظهر ولا تاخد لمس. لو احتجنا نظهرها نكبّر الحاوية.
                FL = autoclass("android.widget.FrameLayout")
                FLP = autoclass("android.widget.FrameLayout$LayoutParams")
                dm = act.getResources().getDisplayMetrics()
                holder = FL(act)
                holder.addView(layout, FLP(int(dm.widthPixels), int(dm.heightPixels)))
                act.addContentView(holder, FLP(1, 1))
                wv.loadUrl(url)
                app._sn_layout, app._sn_wv = holder, wv
                app._sn_inner = layout
            except Exception as ex:
                app._sn_err = str(ex) or "open failed"
        _open()
        Clock.schedule_interval(self._sniff_poll, 1.0)

    def _sn_js(self, js, cb):
        wv = getattr(self, "_sn_wv", None)
        if wv is None:
            return
        run_on_ui_thread = _main_ui_decorator()
        vcb = _make_value_callback(lambda sv: Clock.schedule_once(lambda dt: cb(sv), 0))

        @run_on_ui_thread
        def go():
            try:
                wv.evaluateJavascript(js, vcb)
            except Exception as ex:
                _plog("sniff js: %s" % str(ex)[:100])
                Clock.schedule_once(lambda dt: cb("null"), 0)
        go()

    def _sn_load(self, src, ref):
        wv = getattr(self, "_sn_wv", None)
        if wv is None:
            return
        from jnius import autoclass
        run_on_ui_thread = _main_ui_decorator()
        HashMap = autoclass("java.util.HashMap")

        @run_on_ui_thread
        def go():
            try:
                h = HashMap()
                h.put("Referer", ref)
                wv.loadUrl(src, h)
            except Exception as ex:
                _plog("sniff load: %s" % str(ex)[:100])
        go()

    def _sniff_poll(self, dt):
        sn = getattr(self, "_sn", None)
        if sn is None:                      # اتلغى
            return False
        wv = getattr(self, "_sn_wv", None)
        now = time.time()
        if wv is None:                      # لسه بيتفتح، أو فشل يتفتح
            if getattr(self, "_sn_err", ""):
                err, self._sn_err, self._sn = self._sn_err, "", None
                self.set_status("read_fail", RED, "WebView: %s" % err[:120])
                return False
            if now - sn["t0"] > 8:
                self._sn = None
                self.set_status("read_fail", RED, "WebView did not open")
                return False
            return True
        if now - sn["t0"] > 75:
            self._sniff_fail()
            return False
        if not sn.get("revealed") and not sn["urls"] and now - sn["t0"] > 15:
            sn["revealed"] = True       # مالقيناش حاجة: نظهر الصفحة عشان تضغط تشغيل بنفسك
            self._sniff_reveal()
        if sn["busy"] and now - sn["busy_t"] > 5:
            sn["busy"] = False
        if not sn["busy"]:
            sn["busy"], sn["busy_t"] = True, now
            self._sn_js(_SNIFF_JS, self._sniff_result)
        # لقينا روابط ومفيش جديد بقاله 3 ثواني: نعرضهم (المستخدم يختار الصح من الحجم والمدة)
        if sn["urls"] and now - sn["t0"] >= 4 and now - sn["last_new"] >= 3:
            self._sniff_finish()
            return False
        return True

    def _sniff_result(self, sv):
        sn = getattr(self, "_sn", None)
        if sn is None:
            return
        sn["busy"] = False
        d = _js_json(sv)
        if not isinstance(d, dict):
            return
        if d.get("t") and not sn["title"]:
            sn["title"] = str(d["t"])[:120]
        if d.get("p") and not sn["poster"]:
            sn["poster"] = str(d["p"])
        if d.get("dbg"):
            sn["dbg"] = [str(x) for x in d["dbg"]][:40]
        for u in list(d.get("v") or []) + list(d.get("r") or []):
            if not isinstance(u, str) or not u.startswith("http"):
                continue
            k = u.split("?")[0].split("#")[0]
            if k in sn["seen"]:
                continue
            sn["seen"].add(k)
            sn["urls"].append(u)
            sn["last_new"] = time.time()
        if not sn["urls"] and not sn["iframe"] and time.time() - sn["t0"] > 8:
            # كتير من المواقع بتحط المشغّل جوه iframe: نفتحه هو
            cands = [s for s in (d.get("f") or []) if isinstance(s, str) and s.startswith("http")
                     and not re.search(r"doubleclick|googlesyndication|adservice|/ads?[/.?]|banner|"
                                       r"facebook\.com/plugins|twitter\.com|disqus|recaptcha|"
                                       r"googletagmanager", s, re.I)]
            if cands:
                pick = next((s for s in cands if re.search(r"embed|player|video|stream|play|watch", s, re.I)),
                            cands[0])
                sn["iframe"], sn["ref"] = True, pick
                self._sn_load(pick, sn["url"])

    def _sniff_finish(self):
        from urllib.parse import urlparse
        sn = getattr(self, "_sn", None)
        if sn is None:
            return
        urls = sn["urls"][:12]
        cookies = []
        try:
            from jnius import autoclass
            CM = autoclass("android.webkit.CookieManager")
            cookies = self._ui_call(
                lambda: [str(CM.getInstance().getCookie(u) or "") for u in urls], timeout=4.0) or []
        except Exception:
            cookies = []
        page, ref, poster = sn["url"], sn["ref"], sn["poster"]
        host = urlparse(page).netloc
        title = sn["title"] or host or "Video"
        self._sniff_close()
        self.url = page
        fake = {"title": title, "uploader": host, "thumbnail": poster or ""}

        def build():
            items, n = [], 0
            for idx, u in enumerate(urls):
                ck = cookies[idx] if idx < len(cookies) else ""
                pst = poster if len(urls) == 1 else ""
                vs = []
                if re.search(r"\.m3u8(\?|#|$)", u, re.I):
                    try:
                        vs = hls_variants(u, ref, ck)
                    except Exception:
                        vs = []
                if vs:      # بث بجودات: نعرض كل جودة لوحدها عشان تختار
                    for h, bw, vu in vs:
                        n += 1
                        it = _video_item(vu, n, ref, pst, ck, page)
                        it["label"] = ("%dp" % h) if h else ("%dk" % (bw // 1000) if bw else "#%d video" % n)
                        it["h"], it["name"] = h, (title or "")[:60]
                        items.append(it)
                else:
                    n += 1
                    items.append(_video_item(u, n, ref, pst, ck, page))
            self.ui(self._on_info, fake, items)
        threading.Thread(target=build, daemon=True).start()

    def _sniff_fail(self):
        dbg = (getattr(self, "_sn", None) or {}).get("dbg") or []
        self._sniff_close()
        if dbg:
            self.ui(self.show_error_details, "\n".join(dbg))
        self.set_status("read_fail", RED,
                        "ما لقيتش فيديو في الصفحة دي (ممكن يكون محتاج تسجيل دخول أو محمي)"
                        if self.rtl else
                        "No video found on this page (it may need a login or be protected)")
        SOUND.play("error")

    def _sniff_exit(self):
        """زرار تم / رجوع: لو اتلقط فيديو نعرضه، غير كده نقفل ونقول إننا مالقيناش."""
        sn = getattr(self, "_sn", None)
        if sn is not None and sn.get("urls"):
            self._sniff_finish()
        else:
            self._sniff_fail()

    def _sniff_reveal(self):
        layout = getattr(self, "_sn_layout", None)
        if layout is None:
            return
        try:
            run_on_ui_thread = _main_ui_decorator()

            inner = getattr(self, "_sn_inner", None)

            @run_on_ui_thread
            def _show(layout=layout, inner=inner):
                try:
                    for v in (layout, inner):
                        if v is not None:
                            lp = v.getLayoutParams()
                            lp.width = -1
                            lp.height = -1
                            v.setLayoutParams(lp)
                    layout.bringToFront()
                except Exception:
                    pass
            _show()
        except Exception:
            pass

    def _sniff_close(self):
        layout, wv = getattr(self, "_sn_layout", None), getattr(self, "_sn_wv", None)
        self._sn_layout = self._sn_wv = None
        self._sn = None
        if wv is None:
            return
        try:
            run_on_ui_thread = _main_ui_decorator()

            @run_on_ui_thread
            def _close(layout=layout, wv=wv):
                try:
                    wv.stopLoading()
                    wv.loadUrl("about:blank")      # يوقف الفيديو والصوت
                except Exception:
                    pass
                _remove_android_view(layout)
                try:
                    wv.destroy()
                except Exception:
                    pass
            _close()
        except Exception:
            pass

    def _ig_close(self):
        layout, wv = getattr(self, "_ig_layout", None), getattr(self, "_ig_wv", None)
        self._ig_layout = self._ig_wv = None
        if wv is None:
            return
        try:
            run_on_ui_thread = _main_ui_decorator()
            @run_on_ui_thread
            def _close(layout=layout, wv=wv):
                try:
                    wv.stopLoading()
                except Exception:
                    pass
                _remove_android_view(layout)
                try:
                    wv.destroy()
                except Exception:
                    pass

            _close()
        except Exception:
            pass

    # ---------------- ui helpers
    def ui(self, fn, *a):
        def _run(dt):
            try:
                fn(*a)
            except Exception as ex:
                nlog("ui callback %s failed: %s" % (getattr(fn, "__name__", fn),
                                                    traceback.format_exc()[-1200:]))
                try:
                    if getattr(fn, "__name__", "") in ("_on_info", "_on_playlist"):
                        self.set_status("read_fail", RED, str(ex)[:160])
                        self.analyze_btn.disabled = False
                        self.analyze_btn.text = t("analyze")
                except Exception:
                    pass
        Clock.schedule_once(_run, 0)

    def set_status(self, key, color=None, detail=None):
        color = color or TXT
        def _do():
            self._st = (key, color)
            if detail is not None:
                self._dt = detail
            # الرسالة تفضل ظاهرة شوية قبل ما يرجع عرض تقدم التحميلات
            self._msg_until = time.time() + (1e9 if key == "fetching" else 3.0)
            self.render_status()
            if color is RED or key == "paste_first":
                try:
                    extra = detail if isinstance(detail, str) and detail else ""
                    self._toast(ts(key) + ((": " + extra[:120]) if extra else ""), 3.2)
                except Exception:
                    pass
        self.ui(_do)

    def render_status(self):
        key, color = self._st
        self.status.text = t(key)
        self.status.color = color
        d = self._dt
        if isinstance(d, tuple):
            self.detail.text = t(d[0], *d[1:])
        elif d:
            self.detail.text = fix(d)
        else:
            self.detail.text = ""

    def set_progress(self, v):
        def _do():
            self.bar.value = v
            self.pct.text = "%d%%" % v if v else ""
        self.ui(_do)

    def show_empty(self):
        self.list_box.clear_widgets()

    def _on_text(self, w, text):
        try:
            if AR_RE.search(text or ""):
                self.url_view.text = fix(text)
                self.url_view.halign = "right" if self.rtl else "left"
                self.url_view.opacity = 1
                self.url_input.foreground_color = (0, 0, 0, 0)
            else:
                self.url_view.text = ""
                self.url_view.opacity = 0
                self.url_input.foreground_color = TXT
        except Exception:
            pass
        self.paste_btn.text = t("clear") if text else t("paste")
        self.hint_lbl.opacity = 0 if text else 1

    def _paste_or_clear(self, *_):
        if self.url_input.text:
            self.url_input.text = ""
        else:
            try:
                self.url_input.text = (Clipboard.paste() or "").strip()
            except Exception:
                pass


    # ================================================================ بحث يوتيوب داخل البرنامج
    def open_search_page(self, *_):
        if yt_dlp is None:
            self.set_status("read_fail", RED,
                            "yt-dlp is missing. Install it with: pip install -U yt-dlp")
            return
        cur = (self.url_input.text or "").strip()
        if cur and not self.srch_q:
            self.srch_q = cur
        self.show_tab("search")

    def _build_search_page(self):
        rtl = self.rtl
        al = "right" if rtl else "left"
        page = BoxLayout(orientation="vertical", padding=[dp(12), dp(36), dp(12), dp(6)],
                         spacing=dp(8))
        self.search_page = page

        # ---- شريط البحث
        bar = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        back = RoundButton(text=fix("رجوع" if rtl else "Back"), size_hint_x=None,
                           width=dp(76), bg=CARD2, radius=dp(18))
        back.bind(on_release=self._srch_back)
        box = Card(orientation="horizontal", padding=[dp(14), 0, dp(6), 0], spacing=dp(6),
                   bg=CARD, radius=dp(20), elev=1, border=list(ACC1) + [.9])
        self.srch_input = SearchInput(
            multiline=False, write_tab=False,
            size_hint=(1, None), height=dp(44), pos_hint={"x": 0, "center_y": .5},
            padding=[dp(2), dp(11), dp(2), dp(11)],
            background_normal="", background_active="", background_color=(0, 0, 0, 0),
            foreground_color=TXT, cursor_color=ACC2, font_size=dp(15),
        )
        if SEARCH_FONT:
            self.srch_input.font_name = SEARCH_FONT
        # الحقل نفسه بيستقبل الكتابة بس، وإحنا اللي بنرسم النص (ارتفاع ثابت) والمؤشر
        self.srch_input.foreground_color = (0, 0, 0, 0)
        self.srch_input.cursor_color = (0, 0, 0, 0)
        self.srch_input.selection_color = list(ACC1) + [.35]
        self.srch_input.set_raw(self.srch_q)
        self.srch_hint = TLabel(text=ts_search_hint(), font_size=dp(14),
                                color=(0.45, 0.50, 0.66, 1), pos_hint={"x": 0, "y": 0},
                                halign=al)
        # TextInput مش بيوصّل الحروف العربي، فبنعرض معاينة صحيحة تحت المربع (والحقل نفسه
        # يفضل ظاهر عشان المؤشر والتعديل في نص الكلمة يشتغلوا عادي)
        self.srch_show = TLabel(text="", font_size=dp(16), bold=True, halign="right",
                                shorten=True, shorten_from="left", size_hint_y=None, height=0)
        self.srch_show.opacity = 0

        def _srch_txt(w, shown):
            text = w.raw
            self.srch_hint.opacity = 0 if text else 1
            ar = False      # الحقل نفسه بقى بيعرض العربي صح، مفيش حاجة لسطر المعاينة
            try:
                self.srch_lbl.text = w.text
            except Exception:
                pass
            if not SEARCH_FONT and AR_FONT != "Roboto":
                # من غير Cairo: خط النظام العربي للنص العربي الصافي فقط (مالوش حروف إنجليزي)
                want = AR_FONT if (ar and not re.search(r"[A-Za-z0-9]", text)) else "Roboto"
                if w.font_name != want:
                    w.font_name = want
            self.srch_show.text = fix(text) if ar else ""
            self.srch_show.opacity = 1 if ar else 0
            self.srch_show.height = dp(30) if ar else 0
        self.srch_input.bind(
            text=_srch_txt,
            on_text_validate=lambda *_: self._srch_submit(),
        )
        input_fl = FloatLayout()
        input_fl.add_widget(self.srch_hint)
        self.srch_lbl = Label(text="", font_name=SEARCH_FONT or "Roboto", font_size=dp(15),
                              color=TXT, halign="left", valign="middle", markup=False,
                              size_hint=(None, None), shorten=False)
        from kivy.uix.stencilview import StencilView
        clip = StencilView(size_hint=(None, None))
        clip.add_widget(self.srch_lbl)
        input_fl.add_widget(clip)
        input_fl.add_widget(self.srch_input)
        with input_fl.canvas.after:
            cur_col = Color(*ACC2, 0)
            cur_rect = Rectangle(pos=(0, 0), size=(0, 0))

        def _upd_lbl(*_):
            clip.pos, clip.size = input_fl.pos, input_fl.size
            lbl = self.srch_lbl
            lbl.text_size = (None, input_fl.height)
            try:
                lbl.texture_update()
                tw = lbl.texture_size[0]
            except Exception:
                tw = 0
            lbl.size = (max(tw + dp(4), input_fl.width), input_fl.height)
            lbl.pos = (input_fl.x + dp(2) - self.srch_input.scroll_x, input_fl.y)

        def _upd_cur(*_):
            inp = self.srch_input
            prefix = inp.text[:inp.cursor_col]
            w = 0
            if prefix:
                try:
                    w = CoreLabel(font_name=inp.font_name, font_size=inp.font_size).get_extents(prefix)[0]
                except Exception:
                    w = 0
            h = inp.font_size * 1.3
            cx = input_fl.x + dp(2) + w - inp.scroll_x
            cur_rect.pos = (cx, input_fl.center_y - h / 2.0)
            cur_rect.size = (dp(1.6), h)
            vis = input_fl.x - 1 <= cx <= input_fl.right
            cur_col.a = 1 if (inp.focus and vis) else 0
        input_fl.bind(pos=lambda *_: (_upd_lbl(), _upd_cur()),
                      size=lambda *_: (_upd_lbl(), _upd_cur()))
        self.srch_input.bind(cursor=_upd_cur, text=_upd_cur, focus=_upd_cur)
        self.srch_input.bind(scroll_x=lambda *_: (_upd_lbl(), _upd_cur()),
                             text=lambda *_: (_upd_lbl(), _upd_cur()))
        _upd_lbl()
        go = RoundButton(text=fix("بحث" if rtl else "Search"), size_hint=(None, None),
                         size=(dp(70), dp(40)), pos_hint={"center_y": .5},
                         bg=list(ACC1) + [1], radius=dp(20))
        go.bind(on_release=lambda *_: self._srch_submit())
        box.add_widget(input_fl)
        box.add_widget(go)
        for w in ((box, back) if rtl else (back, box)):
            bar.add_widget(w)
        page.add_widget(bar)
        page.add_widget(self.srch_show)
        _srch_txt(self.srch_input, self.srch_input.text)

        self.srch_msg = TLabel(text="", font_size=dp(12), color=DIM, size_hint_y=None,
                               height=dp(22), halign="center", shorten=False)
        page.add_widget(self.srch_msg)

        sv = ScrollView(bar_width=dp(3), bar_color=(1, 1, 1, .25))
        self.srch_box = GridLayout(cols=1, spacing=dp(8), size_hint_y=None,
                                   padding=[0, 0, 0, dp(6)])
        self.srch_box.bind(minimum_height=self.srch_box.setter("height"))
        sv.add_widget(self.srch_box)
        page.add_widget(sv)
        self._srch_render()
        if not self.srch_results:
            self.srch_msg.text = fix(
                "اكتب اسم فيديو أو قناة، أو الصق رابط مباشرة" if rtl
                else "Type a video or channel name, or paste a link")
            Clock.schedule_once(lambda dt: setattr(self.srch_input, "focus", True), .25)

    def _srch_back(self, *_):
        if self.srch_channel:
            self._srch_leave_channel()
        else:
            self.show_tab("download")

    def _srch_submit(self):
        q = (self.srch_input.raw or "").strip()
        if not q:
            return
        self.srch_q = q
        try:
            self.srch_input.focus = False
        except Exception:
            pass
        # لو اللي كتبته رابط: روح على التحليل العادي مباشرة
        if re.match(r"^(https?://|www\.)\S+$", q, re.I):
            self.url_input.text = q if q.lower().startswith("http") else "https://" + q
            self.srch_q = ""
            self.srch_results = []
            self.show_tab("download")
            Clock.schedule_once(lambda dt: self.fetch_formats(), .15)
            return
        self.srch_channel = None
        self.srch_is_pl = False
        self.srch_chan_tab = "videos"
        self.srch_chan_info = {}
        self.srch_count = 25
        self._srch_start()

    def _srch_start(self):
        self.srch_token += 1
        tok = self.srch_token
        self.srch_busy = True
        self.srch_results = []
        self._srch_render()
        self._srch_set_msg("جاري البحث..." if self.rtl else "Searching...")
        threading.Thread(target=self._srch_worker, args=(tok,), daemon=True).start()

    def _srch_set_msg(self, text):
        try:
            self.srch_msg.text = fix(text)
        except Exception:
            pass

    def _srch_extract(self, target, n):
        opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                "skip_download": True, "noprogress": True, "logger": QuietLogger(),
                "retries": 2, "socket_timeout": 20, "playlistend": n,
                "extractor_args": {"youtube": {"lang": ["ar" if self.rtl else "en"]}}}
        with yt_dlp.YoutubeDL(opts) as y:
            return y.extract_info(target, download=False)

    def _srch_worker(self, tok, with_chan=True):
        try:
            q, n = self.srch_q, int(self.srch_count)
            chan = self.srch_channel
            tab = self.srch_chan_tab
            is_pl = self.srch_is_pl
            box = {}
            th = None
            self._srch_corrected = ""
            if with_chan and not chan:
                def _chan_job():
                    try:
                        from urllib.parse import quote
                        u = ("https://www.youtube.com/results?search_query=%s&sp=EgIQAg%%253D%%253D"
                             % quote(q))
                        box["c"] = self._srch_norm(self._srch_extract(u, 6), channels=True)[:2]
                    except Exception:
                        box["c"] = []
                th = threading.Thread(target=_chan_job, daemon=True)
                th.start()
            if chan and is_pl:
                target = chan[1]
            elif chan:
                target = chan[1].rstrip("/") + "/" + tab
            else:
                target = "ytsearch%d:%s" % (n, q)
            want_pl = bool(chan) and not is_pl and tab == "playlists"
            info = None
            try:
                info = self._srch_extract(target, n)
            except Exception:
                if not want_pl:
                    raise
            vids = self._srch_norm(info, channels=False, playlists=want_pl) if info else []
            if not vids and not chan:
                # غلطة إملائية: نجرّب صفحة نتايج يوتيوب (بتصحّح لوحدها) وبعدين اقتراحات البحث
                try:
                    fixed, vids2 = self._srch_fuzzy(q, n)
                    if vids2:
                        vids, self._srch_corrected = vids2, fixed
                except Exception as exf:
                    _plog("fuzzy search: %s" % str(exf)[:120])
            if want_pl and not vids:
                # بعض القنوات يوتيوب ما بيرجّعش تبويب قوائمها: ندوّر عليها بالبحث ونفلتر باسم القناة
                from urllib.parse import quote
                nm = (self.srch_chan_info or {}).get("name") or chan[0]
                u2 = ("https://www.youtube.com/results?search_query=%s&sp=EgIQAw%%253D%%253D"
                      % quote(nm))
                pls = self._srch_norm(self._srch_extract(u2, 30), playlists=True)
                low = nm.lower()
                keep = [x for x in pls if low and low in str(x.get("owner", "")).lower()]
                vids = keep or pls
            extra = None
            if chan and not is_pl and info:
                extra = {"desc": (info.get("description") or "")[:260],
                         "subs": info.get("channel_follower_count") or 0,
                         "handle": info.get("uploader_id") or "",
                         "count": info.get("playlist_count") or 0,
                         "name": info.get("channel") or info.get("uploader") or ""}
            if th is not None:
                th.join(25)
            if (self._srch_corrected and with_chan and not chan and not box.get("c")
                    and self._srch_corrected != "*"):
                try:
                    from urllib.parse import quote
                    u3 = ("https://www.youtube.com/results?search_query=%s&sp=EgIQAg%%253D%%253D"
                          % quote(self._srch_corrected))
                    box["c"] = self._srch_norm(self._srch_extract(u3, 6), channels=True)[:2]
                except Exception:
                    pass
            if tok != self.srch_token:
                return
            if with_chan:
                chans = box.get("c") or []
            else:   # عرض المزيد: خلّي القنوات اللي ظاهرة زي ما هي
                chans = [r for r in self.srch_results if r.get("kind") == "channel"]
            self.ui(self._srch_done, tok, chans + vids, "", extra)
        except Exception as e:
            if tok != self.srch_token:
                return
            self.ui(self._srch_done, tok, [], user_facing_error(e))

    def _srch_fuzzy(self, q, n):
        """بحث متسامح مع الأخطاء: بيرجّع (النص المصحّح أو "*", النتايج)."""
        from urllib.parse import quote
        import urllib.request
        try:
            u = "https://www.youtube.com/results?search_query=%s&sp=EgIQAQ%%253D%%253D" % quote(q)
            v = self._srch_norm(self._srch_extract(u, n))
            if v:
                return "*", v
        except Exception as ex:
            _plog("fuzzy web results: %s" % str(ex)[:100])
        cands = []
        for qq in (q, q[:-1], q[:-2]):
            if len(qq) < 3:
                continue
            try:
                req = urllib.request.Request(
                    "https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&hl=%s&q=%s"
                    % ("ar" if self.rtl else "en", quote(qq)), headers={"User-Agent": _UA_MOBILE})
                raw = urllib.request.urlopen(req, timeout=8).read()
                for sg in (json.loads(raw.decode("utf-8", "ignore"))[1] or [])[:2]:
                    if sg and sg != q and sg not in cands:
                        cands.append(sg)
            except Exception as ex:
                _plog("suggest: %s" % str(ex)[:100])
            if len(cands) >= 3:
                break
        for sg in cands[:3]:
            try:
                v = self._srch_norm(self._srch_extract("ytsearch%d:%s" % (n, sg), n))
                if v:
                    return sg, v
            except Exception:
                pass
        return "", []

    def _srch_norm(self, info, channels=False, playlists=False):
        out, seen = [], set()
        for e in list((info or {}).get("entries") or []):
            if not isinstance(e, dict):
                continue
            u = str(e.get("webpage_url") or e.get("url") or "")
            vid = str(e.get("id") or "")
            title = e.get("title") or e.get("channel") or ""
            if not title or title in ("[Private video]", "[Deleted video]"):
                continue
            if playlists:
                if "list=" not in u or u in seen:
                    continue
                seen.add(u)
                th = ""
                for t_ in (e.get("thumbnails") or []):
                    if t_.get("url"):
                        th = t_["url"]
                if th.startswith("//"):
                    th = "https:" + th
                out.append(dict(kind="playlist", title=title, url=u, id=vid, thumb=th,
                                owner=e.get("channel") or e.get("uploader") or "",
                                count=e.get("playlist_count") or e.get("n_entries") or 0, dur=0))
                continue
            if channels:
                if not re.search(r"youtube\.com/(channel/|@|user/|c/)", u):
                    continue
                if u in seen:
                    continue
                seen.add(u)
                th = ""
                for t_ in (e.get("thumbnails") or []):
                    if t_.get("url"):
                        th = t_["url"]
                if th.startswith("//"):
                    th = "https:" + th
                out.append(dict(kind="channel", title=title, url=u, id=vid, thumb=th,
                                subs=e.get("channel_follower_count") or 0, dur=0))
            else:
                if not u.startswith("http"):
                    if not vid:
                        continue
                    u = "https://www.youtube.com/watch?v=%s" % vid
                if "/watch" not in u and "/shorts/" not in u and "youtu.be/" not in u:
                    continue
                m = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{6,})", u)
                if not vid and m:
                    vid = m.group(1)
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                out.append(dict(kind="video", title=title, url=u, id=vid,
                                thumb="https://i.ytimg.com/vi/%s/mqdefault.jpg" % vid,
                                dur=e.get("duration") or 0,
                                chan=e.get("channel") or e.get("uploader") or "",
                                views=e.get("view_count") or 0))
        return out

    def _srch_done(self, tok, res, err, extra=None):
        if tok != self.srch_token:
            return
        self.srch_busy = False
        self.srch_results = res
        if extra:
            self.srch_chan_info = extra
        if getattr(self, "cur_nav", "") != "search":
            return
        if err:
            self._srch_set_msg(("تعذر البحث: " if self.rtl else "Search failed: ") + err[:90])
            SOUND.play("error")
        elif not res:
            self._srch_set_msg("مفيش نتائج" if self.rtl else "No results")
        else:
            c = getattr(self, "_srch_corrected", "")
            if c == "*":
                self._srch_set_msg("اتصحّح الغلط الإملائي تلقائيًا" if self.rtl else "Spelling auto-corrected")
            elif c:
                self._srch_set_msg(("نتائج: " if self.rtl else "Results for: ") + c)
            else:
                self._srch_set_msg("")
        self._srch_render()

    def _srch_render(self):
        box = getattr(self, "srch_box", None)
        if box is None:
            return
        box.clear_widgets()
        in_chan = bool(self.srch_channel)
        if in_chan and not self.srch_is_pl:
            box.add_widget(self._srch_chan_header())
        elif in_chan and self.srch_is_pl:
            box.add_widget(TLabel(text=fix(self.srch_channel[0]), font_size=dp(17), bold=True,
                                  size_hint_y=None, height=dp(40),
                                  halign="right" if self.rtl else "left"))
        if in_chan and not self.srch_is_pl and self.srch_chan_tab == "shorts":
            g = GridLayout(cols=3, spacing=dp(4), size_hint_y=None)
            g.bind(minimum_height=g.setter("height"))
            for e in self.srch_results:
                if e["kind"] == "video":
                    g.add_widget(self._srch_cell_short(e))
            box.add_widget(g)
        else:
            for e in self.srch_results:
                k = e["kind"]
                if k == "channel":
                    box.add_widget(self._srch_row_channel(e))
                elif k == "playlist":
                    box.add_widget(self._srch_row_playlist(e))
                elif in_chan:
                    box.add_widget(self._srch_row_yt(e))
                else:
                    box.add_widget(self._srch_row_video(e))
        if self.srch_busy:
            box.add_widget(TLabel(text=fix("..." ), halign="center", size_hint_y=None, height=dp(40)))
        elif self.srch_results and sum(1 for r in self.srch_results if r["kind"] != "channel") >= self.srch_count - 2 \
                and self.srch_count < 100:
            more = RoundButton(text=fix("عرض المزيد" if self.rtl else "Show more"),
                               size_hint_y=None, height=dp(46), bg=CARD2, radius=dp(18))
            more.bind(on_release=self._srch_more)
            box.add_widget(more)

    def _srch_chan_header(self):
        """رأس صفحة القناة على شكل يوتيوب: صورة دائرية + الاسم + المشتركين + الوصف + تبويبات."""
        rtl = self.rtl
        al = "right" if rtl else "left"
        name, url, thumb, subs0 = (list(self.srch_channel) + ["", 0])[:4]
        info = self.srch_chan_info or {}
        subs = info.get("subs") or subs0
        wrap = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(206),
                         spacing=dp(8), padding=[dp(4), dp(4)])
        top = BoxLayout(size_hint_y=None, height=dp(84), spacing=dp(14))
        av = CircleImage(size_hint=(None, None), size=(dp(80), dp(80)), pos_hint={"center_y": .5})
        ent = {"thumb": thumb, "kind": "channel", "_w": av}
        self._srch_thumb(ent, av)
        col = BoxLayout(orientation="vertical", spacing=dp(2))
        col.add_widget(TLabel(text=fix(info.get("name") or name), font_size=dp(20), bold=True,
                              halign=al))
        parts = []
        if info.get("handle"):
            parts.append(info["handle"])
        if subs:
            parts.append(self._short_num(subs) + (" مشترك" if rtl else " subscribers"))
        if info.get("count"):
            parts.append(self._short_num(info["count"]) + (" فيديو" if rtl else " videos"))
        col.add_widget(TLabel(text=fix(" · ".join(parts)), font_size=dp(12), color=DIM,
                              halign=al, size_hint_y=None, height=dp(22)))
        for w in ((col, av) if rtl else (av, col)):
            top.add_widget(w)
        wrap.add_widget(top)
        desc = (info.get("desc") or "").replace("\n", " ").strip()
        wrap.add_widget(TLabel(text=fix(desc), font_size=dp(12), color=DIM, halign=al,
                               valign="top", shorten=False, max_lines=2,
                               size_hint_y=None, height=dp(38)))
        tabs = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(6))
        defs = [("videos", "الفيديوهات", "Videos"), ("shorts", "Shorts", "Shorts"),
                ("playlists", "قوائم التشغيل", "Playlists")]
        if rtl:
            defs.reverse()
        for key, ar, en in defs:
            b = RoundButton(text=fix(ar if rtl else en), font_size=dp(13), radius=dp(20),
                            bg=list(ACC1) + [1] if self.srch_chan_tab == key else CARD)
            b.bind(on_release=lambda w, k=key: self._srch_chan_set_tab(k))
            tabs.add_widget(b)
        wrap.add_widget(tabs)
        return wrap

    def _srch_chan_set_tab(self, key):
        if key == self.srch_chan_tab or self.srch_busy:
            return
        self.srch_chan_tab = key
        self.srch_count = 25
        self._srch_start()

    def _srch_row_yt(self, e):
        """بطاقة فيديو على شكل يوتيوب: صورة كبيرة بعرض الشاشة والعنوان تحتها."""
        rtl = self.rtl
        al = "right" if (rtl or AR_RE.search(e["title"])) else "left"
        card = PickRow(orientation="vertical", size_hint_y=None, height=dp(300), padding=0,
                       spacing=dp(4), bg=CARD, radius=dp(18), elev=1)
        fl = FloatLayout(size_hint=(1, None), height=dp(200))
        fl.bind(width=lambda w, v: setattr(w, "height", v * 9.0 / 16.0))
        th = KImage(size_hint=(1, 1), pos_hint={"x": 0, "y": 0}, allow_stretch=True,
                    keep_ratio=False, opacity=0.35)
        fl.add_widget(th)
        if e.get("id"):
            e["thumb"] = "https://i.ytimg.com/vi/%s/hq720.jpg" % e["id"]
        e["_w"] = th
        self._srch_thumb(e, th)
        if e.get("dur"):
            badge = Card(size_hint=(None, None), size=(dp(64), dp(22)),
                         pos_hint={"right": .97, "y": .04}, bg=(0, 0, 0, .78),
                         radius=dp(6), border=[0, 0, 0, 0])
            badge.add_widget(Label(text=fmt_dur(e["dur"]), font_size=dp(11), bold=True))
            fl.add_widget(badge)
        card.add_widget(fl)
        info = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(78),
                         padding=[dp(10), 0, dp(10), dp(8)], spacing=dp(8))
        col = BoxLayout(orientation="vertical", spacing=dp(2))
        col.add_widget(TLabel(text=fix(e["title"]), font_size=dp(13), bold=True, halign=al,
                              valign="top", shorten=False, max_lines=2))
        parts = []
        if e.get("views"):
            parts.append(self._short_num(e["views"]) + (" مشاهدة" if rtl else " views"))
        col.add_widget(TLabel(text=fix(" · ".join(parts)), font_size=dp(11), color=DIM,
                              halign=al, size_hint_y=None, height=dp(18)))
        btn = RoundButton(text=t("get"), font_size=dp(13), size_hint=(None, None),
                          size=(dp(76), dp(40)), pos_hint={"center_y": .5},
                          bg=list(ACC1) + [1], radius=dp(20))
        btn.bind(on_release=lambda w, e=e: self._srch_quality_popup(e))
        for w in ((btn, col) if rtl else (col, btn)):
            info.add_widget(w)
        card.add_widget(info)

        def _h(*_):
            card.height = fl.height + dp(78) + dp(4)
        fl.bind(height=_h)
        _h()
        card.bind(on_release=lambda w, e=e: self._player_open(e))
        return card

    def _srch_row_playlist(self, e):
        rtl = self.rtl
        al = "right" if (rtl or AR_RE.search(e["title"])) else "left"
        row = PickRow(orientation="horizontal", size_hint_y=None, height=dp(96),
                      padding=dp(8), spacing=dp(10), bg=CARD, radius=dp(18), elev=1)
        th = KImage(size_hint=(None, 1), width=dp(138), allow_stretch=True,
                    keep_ratio=False, opacity=0.35)
        e["_w"] = th
        self._srch_thumb(e, th)
        col = BoxLayout(orientation="vertical", spacing=dp(2))
        col.add_widget(TLabel(text=fix(e["title"]), font_size=dp(13), bold=True, halign=al,
                              valign="top", shorten=False, max_lines=2))
        if e.get("count"):
            col.add_widget(TLabel(
                text=fix(str(int(e["count"])) + (" فيديو" if rtl else " videos")),
                font_size=dp(11), color=DIM, halign=al, size_hint_y=None, height=dp(18)))
        row.bind(on_release=lambda w, e=e: self._srch_enter_playlist(e))
        for w in ((col, th) if rtl else (th, col)):
            row.add_widget(w)
        return row

    def _srch_enter_playlist(self, e):
        self.srch_pl_prev = (self.srch_channel, self.srch_chan_tab,
                             list(self.srch_results), self.srch_chan_info)
        self.srch_channel = (e["title"], e["url"], e.get("thumb", ""), 0)
        self.srch_is_pl = True
        self.srch_count = 25
        self.show_tab("search")
        self._srch_start()

    def _srch_more(self, *_):
        if self.srch_busy:
            return
        self.srch_count += 25
        self.srch_token += 1
        tok = self.srch_token
        self.srch_busy = True
        self._srch_set_msg("جاري التحميل..." if self.rtl else "Loading...")
        threading.Thread(target=self._srch_worker, args=(tok, False), daemon=True).start()

    @staticmethod
    def _short_num(n):
        n = int(n or 0)
        if n >= 1000000:
            return "%.1fM" % (n / 1000000.0)
        if n >= 1000:
            return "%.1fK" % (n / 1000.0)
        return str(n)

    def _srch_row_video(self, e):
        rtl = self.rtl
        al = "right" if (rtl or AR_RE.search(e["title"])) else "left"
        row = PickRow(orientation="horizontal", size_hint_y=None, height=dp(100),
                      padding=dp(8), spacing=dp(10), bg=CARD, radius=dp(18), elev=1)
        th = KImage(size_hint=(None, 1), width=dp(138), allow_stretch=True,
                    keep_ratio=True, opacity=0.35)
        e["_w"] = th
        self._srch_thumb(e, th)
        col = BoxLayout(orientation="vertical", spacing=dp(2))
        col.add_widget(TLabel(text=fix(e["title"]), font_size=dp(13), bold=True, halign=al,
                              valign="top", shorten=False, max_lines=2))
        parts = []
        if e.get("chan"):
            parts.append(e["chan"])
        if e.get("dur"):
            parts.append(fmt_dur(e["dur"]))
        if e.get("views"):
            parts.append(self._short_num(e["views"]) + (" مشاهدة" if rtl else " views"))
        col.add_widget(TLabel(text=fix(" · ".join(parts)), font_size=dp(11), color=DIM,
                              halign=al, size_hint_y=None, height=dp(18)))
        btn = RoundButton(text=t("get"), font_size=dp(13), size_hint=(None, None),
                          size=(dp(76), dp(42)), pos_hint={"center_y": .5},
                          bg=list(ACC1) + [1], radius=dp(21))
        btn.bind(on_release=lambda w, e=e: self._srch_quality_popup(e))
        row.bind(on_release=lambda w, e=e: self._player_open(e))
        for w in ((btn, col, th) if rtl else (th, col, btn)):
            row.add_widget(w)
        return row

    def _srch_row_channel(self, e):
        rtl = self.rtl
        al = "right" if (rtl or AR_RE.search(e["title"])) else "left"
        row = PickRow(orientation="horizontal", size_hint_y=None, height=dp(86),
                      padding=dp(8), spacing=dp(12), bg=CARD, radius=dp(18), elev=1)
        th = KImage(size_hint=(None, 1), width=dp(68), allow_stretch=True,
                    keep_ratio=True, opacity=0.35)
        e["_w"] = th
        self._srch_thumb(e, th)
        col = BoxLayout(orientation="vertical", spacing=dp(2))
        col.add_widget(TLabel(text=fix(e["title"]), font_size=dp(14), bold=True, halign=al))
        if e.get("subs"):
            col.add_widget(TLabel(
                text=fix(self._short_num(e["subs"]) + (" مشترك" if rtl else " subscribers")),
                font_size=dp(11), color=DIM, halign=al, size_hint_y=None, height=dp(18)))
        btn = RoundButton(text=fix("فتح" if rtl else "Open"), font_size=dp(13),
                          size_hint=(None, None), size=(dp(76), dp(42)),
                          pos_hint={"center_y": .5}, bg=CARD2, radius=dp(21))
        row.bind(on_release=lambda w, e=e: self._srch_enter_channel(e))
        btn.bind(on_release=lambda w, e=e: self._srch_enter_channel(e))
        for w in ((btn, col, th) if rtl else (th, col, btn)):
            row.add_widget(w)
        return row

    def _srch_enter_channel(self, e):
        self.srch_channel = (e["title"], e["url"], e.get("thumb", ""), e.get("subs", 0))
        self.srch_prev = (list(self.srch_results), self.srch_q, self.srch_mode)
        self.srch_chan_tab = "videos"
        self.srch_chan_info = {}
        self.srch_is_pl = False
        self.srch_count = 25
        self.show_tab("search")
        self._srch_start()

    def _srch_leave_channel(self):
        self.srch_token += 1
        self.srch_busy = False
        self.srch_count = 25
        if self.srch_is_pl and getattr(self, "srch_pl_prev", None):
            (self.srch_channel, self.srch_chan_tab,
             self.srch_results, self.srch_chan_info) = self.srch_pl_prev
            self.srch_is_pl = False
            self.show_tab("search")
            return
        prev = getattr(self, "srch_prev", None)
        self.srch_channel = None
        self.srch_is_pl = False
        self.srch_chan_tab = "videos"
        self.srch_chan_info = {}
        if prev:
            self.srch_results, self.srch_q, self.srch_mode = prev
        self.show_tab("search")

    def _srch_thumb(self, e, widget):
        url = e.get("thumb") or ""
        if not url.startswith("http"):
            return

        def work():
            import urllib.request
            cands = list(e.get("cands") or [url])
            if not e.get("cands") and e.get("kind") == "video" and e.get("id"):
                cands.append("https://i.ytimg.com/vi/%s/mqdefault.jpg" % e["id"])
                cands.append("https://i.ytimg.com/vi/%s/hqdefault.jpg" % e["id"])
            for c in cands:
                try:
                    req = urllib.request.Request(c, headers={
                        "User-Agent": "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
                        "Referer": "https://www.youtube.com/"})
                    with urllib.request.urlopen(req, timeout=6) as r:
                        data = r.read()
                    if data and len(data) > 500:
                        self.ui(self._srch_apply_thumb, e, widget, data)
                        return
                except Exception:
                    continue
        _THUMB_POOL.submit(work)

    def _srch_apply_thumb(self, e, widget, data):
        if e.get("_w") is not widget:
            return
        import io
        from kivy.core.image import Image as CoreImage
        tex = None
        try:
            from PIL import Image as PILImage
            im = PILImage.open(io.BytesIO(data)).convert("RGBA")
            if e.get("crop"):
                w_, h_ = im.size
                tw = int(h_ * 9 / 16)
                if w_ > tw + 2:
                    l_ = (w_ - tw) // 2
                    im = im.crop((l_, 0, l_ + tw, h_))
            im.thumbnail((360, 640) if e.get("crop") else (640, 360))
            tex = Texture.create(size=im.size, colorfmt="rgba")
            tex.blit_buffer(im.tobytes(), colorfmt="rgba", bufferfmt="ubyte")
            tex.flip_vertical()
        except Exception:
            for ext in ("jpg", "png"):
                try:
                    tex = CoreImage(io.BytesIO(data), ext=ext).texture
                    if tex is not None:
                        break
                except Exception:
                    tex = None
        if tex is not None:
            widget.texture = tex
            widget.opacity = 1

    def _srch_open_full(self, e):
        """الضغط على الفيديو نفسه: تحليل كامل بكل الجودات (الشاشة العادية)."""
        self._player_close()
        self.url_input.text = e["url"]
        self.show_tab("download")
        Clock.schedule_once(lambda dt: self.fetch_formats(), .15)

    def _srch_pick_size(self, vids, auds, dur, kind, q):
        """نفس منطق اختيار الصيغة وقت التحميل، بس بنرجّع الحجم التقريبي."""
        if kind == "a":
            if not auds:
                return None
            if any(x.get("mp3") for x in auds) and q:
                pick = min(auds, key=lambda x: abs(x["tbr"] - q))
            else:
                pick = auds[0]
            return pick.get("size") or ((q * 125.0 * dur) if (dur and q) else None)
        if not vids:
            return None
        if q:
            ok = [x for x in vids if 0 < x["h"] <= q]
            if ok:
                best_h = max(x["h"] for x in ok)
                same = [x for x in ok if x["h"] == best_h]
                target = {1080: 4500, 720: 2200, 480: 1200, 360: 700}.get(q, 2200)
                pick = min(same, key=lambda x: abs((x.get("tbr") or target) - target))
            else:
                pick = vids[-1]
        else:
            pick = vids[0]
        size = pick.get("size") or estimate_video_bytes(dur, height=pick.get("h") or 720)
        if size and pick.get("kind") in ("m", "mx") and dur:
            size += dur * 128 * 125.0          # + صوت تقريبي
        return size

    def _srch_quality_popup(self, e, on_close=None):
        rtl = self.rtl
        mv = ModalView(size_hint=(.9, None), height=dp(520), background="",
                       background_color=(0, 0, 0, .6))
        card = Card(orientation="vertical", padding=dp(14), spacing=dp(8), bg=CARD,
                    radius=dp(22), elev=2)
        card.add_widget(TLabel(text=fix(e["title"]), font_size=dp(13), bold=True,
                               size_hint_y=None, height=dp(40),
                               halign="right" if (rtl or AR_RE.search(e["title"])) else "left",
                               shorten=False, max_lines=2))
        grid = GridLayout(cols=2, spacing=dp(8), size_hint_y=None, height=dp(58) * 4 + dp(8) * 3)
        mp3 = bool(HAS_FFMPEG or HAS_AV)
        opts = [("v", 0, ts("pl_best")), ("v", 1080, "1080p"), ("v", 720, "720p"),
                ("v", 480, "480p"), ("v", 360, "360p"),
                ("a", 192 if mp3 else 0, "MP3 192k" if mp3 else "M4A")]
        if mp3:
            opts.append(("a", 320, "MP3 320k"))
        else:
            opts.append(("v", 240, "240p"))
        btns = {}
        for kind, q, lab in opts[:8]:
            name = (("فيديو " if rtl else "Video ") + lab) if kind == "v" and q == 0 else lab
            b = RoundButton(text=fix(name), font_size=dp(13), radius=dp(20), halign="center",
                            bg=list(ACC1) + [1] if (kind == "v" and q == 720) else CARD2,
                            size_hint_y=None, height=dp(58))
            b.bind(on_release=lambda w, k=kind, q=q: (mv.dismiss(), self._srch_download(e, k, q)))
            btns[(kind, q)] = (b, name)
            grid.add_widget(b)
        card.add_widget(grid)
        status = TLabel(text=fix("جاري حساب الأحجام..." if rtl else "Calculating sizes..."),
                        font_size=dp(12), color=DIM, halign="center", shorten=False,
                        size_hint_y=None, height=dp(24))
        card.add_widget(status)
        more = RoundButton(text=fix("كل الجودات والتفاصيل" if rtl else "All qualities & details"),
                           size_hint_y=None, height=dp(44), bg=CARD2, radius=dp(20))
        more.bind(on_release=lambda *_: (mv.dismiss(), self._srch_open_full(e)))
        card.add_widget(more)
        card.add_widget(Widget())
        mv.add_widget(card)
        if on_close:
            mv.bind(on_dismiss=lambda *_: on_close())
        mv.open()

        def work():
            try:
                with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True,
                                       "noprogress": True, "logger": QuietLogger(),
                                       "retries": 2, "socket_timeout": 20}) as y:
                    info = y.extract_info(e["url"], download=False)
                vids, auds = self._build_items(info)
                dur = info.get("duration") or e.get("dur") or 0
                sizes = {}
                for key in btns:
                    try:
                        sizes[key] = self._srch_pick_size(vids, auds, dur, key[0], key[1])
                    except Exception:
                        sizes[key] = None

                def apply(*_):
                    for key, (b, name) in btns.items():
                        sz = sizes.get(key)
                        b.text = fix(name) + "\n" + (human_size(sz) if sz else "?")
                    status.text = ""
                self.ui(apply)
            except Exception:
                self.ui(lambda: setattr(status, "text",
                        fix("تعذر حساب الأحجام" if rtl else "Couldn't get sizes")))
        threading.Thread(target=work, daemon=True).start()

    def _srch_cell_short(self, e):
        """مربع شورت (9:16) زي شبكة الشورتس في يوتيوب."""
        cell = TapCell(size_hint_y=None, height=dp(200))
        cell.bind(width=lambda w, v: setattr(w, "height", v * 16.0 / 9.0))
        th = KImage(size_hint=(1, 1), pos_hint={"x": 0, "y": 0}, allow_stretch=True,
                    keep_ratio=False, opacity=0.35)
        cell.add_widget(th)
        if e.get("id"):
            base = "https://i.ytimg.com/vi/%s/" % e["id"]
            e["cands"] = [base + "oardefault.jpg", base + "hq720.jpg", base + "mqdefault.jpg"]
            e["thumb"] = e["cands"][0]
            e["crop"] = True
        e["_w"] = th
        self._srch_thumb(e, th)
        if e.get("views"):
            bar = Card(size_hint=(1, None), height=dp(26), pos_hint={"x": 0, "y": 0},
                       bg=(0, 0, 0, .5), radius=0, border=[0, 0, 0, 0])
            bar.add_widget(Label(text=fix(self._short_num(e["views"]) + (" مشاهدة" if self.rtl else " views")),
                                 font_size=dp(11), bold=True))
            cell.add_widget(bar)
        cell.bind(on_release=lambda w, e=e: self._player_open(e))
        return cell

    # ---------------- مشغّل داخل البرنامج (WebView) + شريط تحميل تحته
    def _player_url(self, e):
        vid = e.get("id") or ""
        if "/shorts/" in str(e.get("url")) and vid:
            return "https://m.youtube.com/shorts/%s" % vid
        if vid:
            return "https://m.youtube.com/watch?v=%s" % vid
        return e.get("url")

    def _player_open(self, e):
        """مشغّل داخلي حقيقي: بنجيب لينك الفيديو المباشر بـ yt-dlp ونشغّله في VideoView
        بتاع أندرويد (من غير WebView ومن غير يوتيوب)."""
        try:
            from jnius import autoclass
            _main_ui_decorator()
        except Exception:
            self._toast("المشغّل الداخلي مش متاح هنا، هيتفتح في المتصفح" if self.rtl
                        else "In-app player unavailable, opening browser", 2.5)
            open_url(e.get("url"))
            return
        self._player_close()
        self._pl_fallback = None
        self._pl_kc = False
        tok = object()
        self._pl_token = tok
        self._pl_entry = e
        rtl = self.rtl
        vh = int(Window.width * 9 / 16)
        self._pl_vh = vh
        right = bool(rtl or AR_RE.search(e["title"]))
        panel = PlayerPanel(orientation="vertical", size_hint=(1, 1))
        with panel.canvas.before:
            Color(*BG)
            _bgr = Rectangle(pos=panel.pos, size=panel.size)
        panel.bind(pos=lambda w, v: setattr(_bgr, "pos", w.pos),
                   size=lambda w, v: setattr(_bgr, "size", w.size))
        # منطقة الفيديو (فوق): صورة مصغّرة لحد ما الفيديو يشتغل
        varea = FloatLayout(size_hint_y=None, height=vh)
        with varea.canvas.before:
            Color(0, 0, 0, 1)
            _vr = Rectangle(pos=varea.pos, size=varea.size)
        varea.bind(pos=lambda w, v: setattr(_vr, "pos", w.pos),
                   size=lambda w, v: setattr(_vr, "size", w.size))
        thumb = AsyncImage(source=e.get("thumb") or "", allow_stretch=True, keep_ratio=True,
                           size_hint=(1, 1), opacity=.55)
        varea.add_widget(thumb)
        stat = TLabel(text=fix("جاري التجهيز…" if rtl else "Preparing…"), font_size=dp(16),
                      bold=True, size_hint=(1, None), height=dp(40),
                      pos_hint={"center_x": .5, "center_y": .5}, halign="center")
        varea.add_widget(stat)
        panel.add_widget(varea)
        # العنوان
        panel.add_widget(TLabel(text=fix(e["title"]), font_size=dp(15), bold=True,
                                size_hint_y=None, height=dp(72), halign="right" if right else "left"))
        # تحميل / إغلاق
        row = BoxLayout(size_hint_y=None, height=dp(52), padding=[dp(12), 0], spacing=dp(10))
        dl = RoundButton(text=fix("تحميل" if rtl else "Download"), bg=list(ACC1) + [1],
                         radius=dp(24))
        dl.bind(on_release=lambda *_: self._player_download())
        cl = RoundButton(text=fix("إغلاق" if rtl else "Close"), bg=CARD2, radius=dp(24),
                         size_hint_x=.38)
        cl.bind(on_release=lambda *_: self._player_close())
        for w in ((cl, dl) if rtl else (dl, cl)):
            row.add_widget(w)
        panel.add_widget(row)
        panel.add_widget(Widget())
        self._pl_bar = panel
        self._pl_lbl, self._pl_thumb = stat, thumb
        self._pl_playing = False
        self._pl_seeking = False
        Window.add_widget(panel)
        _plog("player_open: %s" % (e.get("url") or ""))
        _fn = getattr(self, "_force_native", False)
        self._force_native = False
        if (CFG.get("yt_web_player", True) and not _fn
                and re.search(r"youtu", str(e.get("url") or ""), re.I)):
            # يوتيوب: بث حقيقي زي تطبيق يوتيوب (بينزّل اللي بتشوفه بس، وبيبدأ فورًا)
            if self._player_open_web(tok, e):
                return

        def work():
            try:
                if not YT_READY.is_set():
                    YT_READY.wait(150)
                with yt_dlp.YoutubeDL({
                        "quiet": True, "no_warnings": True, "noplaylist": True,
                        "noprogress": True, "logger": QuietLogger(),
                        "retries": 2, "socket_timeout": 20,
                        "cachedir": _ytdlp_cache_dir(),
                        "ignore_no_formats_error": True,
                        "format": "b*"}) as y:
                    info = y.extract_info(e["url"], download=False)
                if info.get("entries"):
                    info = list(info["entries"])[0]
                fmts = [f for f in (info.get("formats") or []) if f.get("url")]
                try:
                    _plog("formats: " + "; ".join(
                        "%s/%s/%s/%s/%s" % (f.get("format_id"), f.get("ext"), f.get("height"),
                                            (f.get("vcodec") or "-")[:4], (f.get("acodec") or "-")[:4])
                        + "/" + str(f.get("protocol"))[:6] for f in fmts[:40]))
                except Exception:
                    pass

                def _has(c):
                    return c not in (None, "", "none")

                def _h(f):
                    return int(f.get("height") or 0)

                _dur = int(info.get("duration") or 0)
                _tgt = (720 if _dur <= 300 else (480 if _dur <= 900 else
                        (360 if _dur <= 2700 else 240)))

                def _ok(f):
                    return _h(f) <= _tgt

                def _best(lst):
                    good = [f for f in lst if _ok(f)] or lst
                    return max(good, key=lambda f: (_h(f), f.get("tbr") or 0)) if good else None
                prog = (lambda f: str(f.get("protocol")) in ("https", "http"))
                hls = (lambda f: "m3u8" in str(f.get("protocol")))
                muxed = [f for f in fmts if _has(f.get("vcodec")) and _has(f.get("acodec"))]
                pick = (_best([f for f in muxed if prog(f)]) or _best([f for f in muxed if hls(f)]))
                alt_src, alt_h = None, {}
                if not pick and re.search(r"youtu", str(e.get("url") or "") + str(info.get("extractor_key") or ""), re.I):
                    # يوتيوب: نطلب من عملاء ios/tv/android روابط HLS (صوت+صورة في بث واحد)
                    # المشغّل الأصلي بيبثها مباشرة من الإنترنت ويدعم التقديم، من غير تحميل ولا بروكسي محلي
                    for _cl in (["ios"], ["tv"], ["android"]):
                        try:
                            with yt_dlp.YoutubeDL({
                                    "quiet": True, "no_warnings": True, "noplaylist": True,
                                    "noprogress": True, "logger": QuietLogger(), "retries": 1,
                                    "socket_timeout": 20, "ignore_no_formats_error": True,
                                    "format": "b*",
                                    "extractor_args": {"youtube": {"player_client": _cl}}}) as y3:
                                i3 = y3.extract_info(e["url"], download=False)
                            if i3.get("entries"):
                                i3 = list(i3["entries"])[0]
                            f3 = [f for f in (i3.get("formats") or []) if f.get("url")
                                  and _has(f.get("vcodec")) and _has(f.get("acodec"))]
                            _plog("retry client %s: %d muxed" % (_cl, len(f3)))

                            def _b720(lst):
                                good = [f for f in lst if _h(f) <= 720] or lst
                                return max(good, key=lambda f: (_h(f), f.get("tbr") or 0)) if good else None
                            c1 = _b720([f for f in f3 if prog(f)])
                            c2 = _b720([f for f in f3 if hls(f)])
                            cand = c1 if (c1 and (not c2 or _h(c1) >= _h(c2))) else c2
                            if cand:
                                alt_src = cand["url"]
                                alt_h = (cand.get("http_headers") or i3.get("http_headers") or {})
                                _plog("alt stream %s %sp via %s" % (cand.get("protocol"), _h(cand), _cl))
                                break
                        except Exception as exr3:
                            _plog("retry client %s failed: %s" % (_cl, str(exr3)[:150]))
                src = asrc = None
                hdrs = {}
                ahdrs = {}
                if pick:
                    src, hdrs = pick["url"], (pick.get("http_headers") or info.get("http_headers") or {})
                else:
                    vo = [f for f in fmts if _has(f.get("vcodec")) and not _has(f.get("acodec"))
                          and prog(f) and f.get("ext") == "mp4"]
                    ao = [f for f in fmts if _has(f.get("acodec")) and not _has(f.get("vcodec"))
                          and prog(f) and f.get("ext") in ("m4a", "mp4")]
                    v = _best(vo)
                    if ao:
                        if _dur <= 300:
                            a_ = max(ao, key=lambda f: f.get("tbr") or 0)
                        else:
                            _good = [f for f in ao if (f.get("tbr") or 0) >= 48] or ao
                            a_ = min(_good, key=lambda f: f.get("tbr") or 0)
                    else:
                        a_ = None
                    _plog("target=%sp dur=%ss" % (_tgt, _dur))
                    if v:
                        src, hdrs = v["url"], (v.get("http_headers") or info.get("http_headers") or {})
                        if a_:
                            asrc = a_["url"]
                            ahdrs = (a_.get("http_headers") or info.get("http_headers") or {})
                if not src and not alt_src:
                    raise RuntimeError("no playable format (formats=%d)" % len(fmts))
                _plog("picked muxed=%s audio_separate=%s" % (bool(pick), bool(asrc)))
                def do_download():
                    # يوتيوب مبيدّيش ملف جاهز بصوت وصورة، والبث المباشر بيتخنق/بيفشل:
                    # ننزّل الفيديو والصوت بجودة خفيفة، وندمجهم، ونشغّل الملف المحلي.
                    vfmt, afmt = v["format_id"], a_["format_id"]
                    vid = re.sub(r"[^A-Za-z0-9_-]", "", str(info.get("id") or "x"))[:40] or "x"
                    cdir = os.path.join(data_dir(), "player_cache")
                    os.makedirs(cdir, exist_ok=True)
                    for _fn in os.listdir(cdir):
                        try:
                            os.remove(os.path.join(cdir, _fn))
                        except Exception:
                            pass
                    last = [0.0]
                    prog = {"v": [0, (v.get("filesize") or v.get("filesize_approx") or 0)],
                            "a": [0, (a_.get("filesize") or a_.get("filesize_approx") or 0)]}

                    def _label(txt):
                        lb = getattr(self, "_pl_lbl", None)
                        if lb is not None:
                            self.ui(lambda: setattr(lb, "text", txt))

                    def mkhook(tag):
                        def hook(d):
                            if getattr(self, "_pl_token", None) is not tok:
                                raise RuntimeError("cancelled")
                            if d.get("status") == "downloading":
                                tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                                prog[tag][0] = d.get("downloaded_bytes") or 0
                                spd[tag] = d.get("speed") or 0
                                if tot:
                                    prog[tag][1] = tot
                                if time.time() - last[0] > 0.5:
                                    last[0] = time.time()
                                    T = sum(x[1] for x in prog.values()) or 1
                                    G = sum(x[0] for x in prog.values())
                                    _kb = int(sum(spd.values()) / 1024)
                                    _label(fix(("جاري التجهيز %d%% · %d KB/s" if rtl
                                                else "Preparing %d%% · %d KB/s")
                                               % (min(99, G * 100.0 / T), _kb)))
                        return hook
                    res = {}
                    spd = {"v": 0, "a": 0}

                    def grab(fid, tag):
                        try:
                            o = {"format": fid,
                                 "outtmpl": os.path.join(cdir, "%s_%s.%%(ext)s" % (vid, tag)),
                                 "noplaylist": True, "quiet": True, "no_warnings": True,
                                 "noprogress": True, "logger": QuietLogger(), "retries": 3,
                                 "socket_timeout": 30, "http_chunk_size": 10485760,
                                 "progress_hooks": [mkhook(tag)]}
                            with yt_dlp.YoutubeDL(o) as y2:
                                try:
                                    inf2 = y2.process_ie_result(copy.deepcopy(info), download=True)
                                except Exception as ex1:
                                    _plog("reuse info failed (%s): %s" % (tag, ex1))
                                    inf2 = y2.extract_info(e["url"], download=True)
                                res[tag] = result_path(y2, inf2)
                        except Exception as ex2:
                            res[tag + "_err"] = ex2
                    _label(fix("جاري التجهيز 0%" if rtl else "Preparing 0%"))
                    ta = threading.Thread(target=grab, args=(afmt, "a"), daemon=True)
                    ta.start()
                    grab(vfmt, "v")
                    ta.join()
                    for _t in ("v", "a"):
                        if res.get(_t + "_err") is not None:
                            raise res[_t + "_err"]
                    vpath, apath = res["v"], res["a"]
                    _plog("files ok")
                    out = os.path.join(cdir, vid + "_play.mp4")
                    _label(fix("جاري الدمج…" if rtl else "Merging…"))
                    done_mux = False
                    if HAS_AV:
                        try:
                            remux_progressive(vpath, apath, out)
                            done_mux = os.path.getsize(out) > 0
                            _plog("remux progressive ok")
                        except Exception as exr:
                            _plog("remux failed: %s" % exr)
                            _mr = "remux failed: %s" % str(exr)[:160]
                            self.ui(lambda _mr=_mr: self._toast(_mr, 6))
                            try:
                                os.remove(out)
                            except Exception:
                                pass
                    if not done_mux:
                        _plog("WARNING: fragmented mux fallback -> seeking may not work")
                        self.ui(lambda: self._toast(
                            "الدمج العادي فشل: التقديم ممكن مايشتغلش" if rtl
                            else "Normal remux failed: seeking may not work", 5))
                        mux_fragmented(vpath, apath, out, duration=info.get("duration"))
                    _plog("muxed ok: %d bytes" % os.path.getsize(out))
                    self.ui(lambda: self._player_start(tok, out, {}, None, None))
                    return

                def do_download_safe():
                    try:
                        do_download()
                    except Exception as exd:
                        _md = "download: %s" % exd
                        _plog(_md)
                        self.ui(lambda _md=_md: self._player_fail(tok, _md))
                if not pick and alt_src:
                    if v and a_:
                        self._pl_fallback = lambda: threading.Thread(
                            target=do_download_safe, daemon=True).start()
                    self.ui(lambda: self._toast("بث مباشر" if rtl else "Streaming", 1.5))
                    self.ui(lambda: self._player_start(tok, alt_src, alt_h, None, None))
                    return
                if not pick and asrc:
                    pxy = _stream_proxy()
                    if pxy is not None:
                        # بث مباشر عن طريق السيرفر المحلي، ولو اتأخر نرجع للتحميل
                        purl = pxy.add(src, hdrs)
                        aurl = pxy.add(asrc, ahdrs)
                        self._pl_fallback = lambda: threading.Thread(
                            target=do_download_safe, daemon=True).start()
                        _plog("streaming via local proxy")
                        self.ui(lambda: self._toast("بث مباشر" if rtl else "Streaming", 1.5))
                        self.ui(lambda: self._player_start(tok, purl, {}, aurl, {}))
                        return
                    _plog("download mode (no local streaming)")
                    do_download_safe()
                    return
                self.ui(lambda: self._player_start(tok, src, hdrs, asrc, ahdrs))
            except Exception as ex:
                _m = "extract: %s" % ex
                _plog(_m)
                self.ui(lambda _m=_m: self._player_fail(tok, _m))
        threading.Thread(target=work, daemon=True).start()

    def _pl_build_overlay(self, act, frame, vv, PythonActivity, view_h, run_on_ui_thread):
        """أزرار التحكم جوه الفيديو نفسه (Views بتاعة أندرويد فوق الفيديو)."""
        from jnius import autoclass
        app = self
        FrameLayout = autoclass("android.widget.FrameLayout")
        FLP = autoclass("android.widget.FrameLayout$LayoutParams")
        from jnius import PythonJavaClass, java_method
        TextView = autoclass("android.widget.TextView")
        ImageButton = autoclass("android.widget.ImageButton")
        SeekBar = autoclass("android.widget.SeekBar")
        LinearLayout = autoclass("android.widget.LinearLayout")
        LLP = autoclass("android.widget.LinearLayout$LayoutParams")
        Bitmap = autoclass("android.graphics.Bitmap")
        BConfig = autoclass("android.graphics.Bitmap$Config")
        Canvas = autoclass("android.graphics.Canvas")
        Paint = autoclass("android.graphics.Paint")
        PStyle = autoclass("android.graphics.Paint$Style")
        Path = autoclass("android.graphics.Path")
        from jnius import cast
        JString = autoclass("java.lang.String")

        def jstr(x):
            # pyjnius مبيحوّلش str لـ CharSequence لوحده في TextView.setText
            return cast("java.lang.CharSequence", JString(str(x)))
        px = lambda v: int(dp(v))
        st = {"drag": False, "full": False, "orient": None, "last": time.time(),
              "playing": True, "dur": 0, "seek_t": None, "seek_ts": 0.0}

        def icon(kind, size):
            bm = Bitmap.createBitmap(size, size, BConfig.ARGB_8888)
            cv = Canvas(bm)
            p = Paint()
            p.setAntiAlias(True)
            p.setARGB(255, 255, 255, 255)
            sz = float(size)
            if kind == "play":
                p.setStyle(PStyle.FILL)
                path = Path()
                path.moveTo(sz * .32, sz * .18)
                path.lineTo(sz * .32, sz * .82)
                path.lineTo(sz * .80, sz * .50)
                path.close()
                cv.drawPath(path, p)
            elif kind == "pause":
                p.setStyle(PStyle.FILL)
                cv.drawRect(sz * .28, sz * .20, sz * .43, sz * .80, p)
                cv.drawRect(sz * .57, sz * .20, sz * .72, sz * .80, p)
            else:  # full
                p.setStyle(PStyle.STROKE)
                p.setStrokeWidth(sz * .09)
                m, L = sz * .18, sz * .26
                for (x, y, dx, dy) in ((m, m, 1, 1), (sz - m, m, -1, 1),
                                       (m, sz - m, 1, -1), (sz - m, sz - m, -1, -1)):
                    cv.drawLine(x, y, x + dx * L, y, p)
                    cv.drawLine(x, y, x, y + dy * L, p)
            return bm
        big = px(64)
        ic_play, ic_pause = icon("play", big), icon("pause", big)
        ic_full = icon("full", px(40))

        class _Click(PythonJavaClass):
            __javainterfaces__ = ["android/view/View$OnClickListener"]
            __javacontext__ = "app"

            def __init__(self, cb):
                super().__init__()
                self.cb = cb

            @java_method("(Landroid/view/View;)V")
            def onClick(self, v):
                try:
                    self.cb()
                except Exception as exc:
                    _plog("click error: %s" % exc)

        class _SeekL(PythonJavaClass):
            __javainterfaces__ = ["android/widget/SeekBar$OnSeekBarChangeListener"]
            __javacontext__ = "app"

            def __init__(self, f_prog, f_start, f_stop):
                super().__init__()
                self.f_prog, self.f_start, self.f_stop = f_prog, f_start, f_stop

            @java_method("(Landroid/widget/SeekBar;IZ)V")
            def onProgressChanged(self, sb, prog, user):
                try:
                    self.f_prog(prog, user)
                except Exception:
                    pass

            @java_method("(Landroid/widget/SeekBar;)V")
            def onStartTrackingTouch(self, sb):
                self.f_start()

            @java_method("(Landroid/widget/SeekBar;)V")
            def onStopTrackingTouch(self, sb):
                try:
                    self.f_stop(sb.getProgress())
                except Exception as exc:
                    _plog("seek stop error: %s" % exc)
        keep = []

        def mmss(ms):
            sec = max(0, int(ms / 1000))
            return "%d:%02d" % (sec // 60, sec % 60)

        def tv(text, size, w, h):
            t = TextView(act)
            t.setText(jstr(text))
            for _fn, _a in ((t.setTextColor, (-1,)), (t.setTextSize, (float(size),)),
                            (t.setGravity, (17,))):
                try:
                    _fn(*_a)
                except Exception as exs:
                    _plog("textview style: %s" % str(exs)[:120])
            return t

        overlay = FrameLayout(act)
        ctl = FrameLayout(act)
        ctl.setBackgroundColor(0x55000000)
        overlay.addView(ctl, FLP(-1, -1))
        frame.addView(overlay, FLP(-1, -1))

        # الصف اللي في النص: -10  تشغيل/إيقاف  +10
        row = LinearLayout(act)
        row.setOrientation(0)
        row.setGravity(17)
        t_back = tv("-10", 20, big, big)
        t_fwd = tv("+10", 20, big, big)
        b_play = ImageButton(act)
        b_play.setImageBitmap(ic_pause)
        b_play.setBackgroundColor(0)
        for w in (t_back, b_play, t_fwd):
            lp = LLP(big, big)
            lp.setMargins(px(14), 0, px(14), 0)
            row.addView(w, lp)
        ctl.addView(row, FLP(-2, -2, 17))

        # الشريط اللي تحت: الوقت + شريط التقدّم + ملء الشاشة
        bottom = LinearLayout(act)
        bottom.setOrientation(0)
        bottom.setGravity(16)
        bottom.setPadding(px(10), px(4), px(10), px(4))
        t_cur = tv("0:00", 12, 0, 0)
        t_dur = tv("0:00", 12, 0, 0)
        sb = SeekBar(act)
        sb.setMax(1000)
        b_full = ImageButton(act)
        b_full.setImageBitmap(ic_full)
        b_full.setBackgroundColor(0)
        bottom.addView(t_cur, LLP(px(46), -2))
        bottom.addView(sb, LLP(0, -2, 1.0))
        bottom.addView(t_dur, LLP(px(46), -2))
        bottom.addView(b_full, LLP(px(40), px(40)))
        ctl.addView(bottom, FLP(-1, -2, 80))

        def touch():
            st["last"] = time.time()

        def toggle_play():
            touch()
            mp2 = getattr(app, "_pl_mp2", None)
            if vv.isPlaying():
                vv.pause()
                if mp2 is not None:
                    mp2.pause()
                st["playing"] = False
                app._pl_paused = True
                b_play.setImageBitmap(ic_play)
            else:
                vv.start()
                if mp2 is not None:
                    mp2.start()
                st["playing"] = True
                app._pl_paused = False
                b_play.setImageBitmap(ic_pause)

        def do_seek(t):
            dur = int(vv.getDuration())
            if dur > 0:
                t = min(max(0, dur - 300), t)
            t = max(0, int(t))
            st["seek_t"], st["seek_ts"] = t, time.time()
            vv.seekTo(t)
            m2 = getattr(app, "_pl_mp2", None)
            if m2 is not None:
                try:
                    m2.seekTo(t)
                except Exception as exm:
                    _plog("audio seek: %s" % exm)
            try:
                t_cur.setText(jstr(mmss(t)))
                if dur > 0:
                    sb.setProgress(int(min(1000, t * 1000.0 / dur)))
            except Exception:
                pass
            _plog("seek -> %d ms (dur %d)" % (t, dur))

        def seek_by(d):
            touch()
            # لو لسه في تقديم شغال نكمّل من هدفه (عشان الضغطات المتتالية تتراكم)
            if st["seek_t"] is not None and time.time() - st["seek_ts"] < 2.0:
                base = st["seek_t"]
            else:
                base = int(vv.getCurrentPosition())
            do_seek(base + d)

        def set_full(on):
            a = PythonActivity.mActivity
            if on:
                try:
                    st["orient"] = int(a.getRequestedOrientation())
                    a.setRequestedOrientation(0)
                except Exception as exf:
                    _plog("orient error: %s" % exf)
                try:
                    a.getWindow().getDecorView().setSystemUiVisibility(4102)
                except Exception:
                    pass
                frame.setLayoutParams(FLP(-1, -1))
            else:
                frame.setLayoutParams(FLP(-1, view_h))
                try:
                    a.getWindow().getDecorView().setSystemUiVisibility(0)
                except Exception:
                    pass
                try:
                    a.setRequestedOrientation(st["orient"] if st["orient"] is not None else -1)
                except Exception:
                    pass
            st["full"] = bool(on)
            app._pl_full = bool(on)
            frame.requestLayout()

        def toggle_full():
            touch()
            set_full(not st["full"])

        def toggle_ctl():
            if ctl.getVisibility() == 0:
                ctl.setVisibility(8)
            else:
                touch()
                ctl.setVisibility(0)

        def on_prog(prog, user):
            if user:
                d = int(vv.getDuration())
                if d > 0:
                    t_cur.setText(jstr(mmss(d * prog / 1000.0)))

        def on_start():
            st["drag"] = True

        def on_stop(prog):
            st["drag"] = False
            touch()
            d = int(vv.getDuration())
            if d > 0:
                do_seek(int(d * prog / 1000.0))

        for view, cb in ((b_play, toggle_play), (t_back, lambda: seek_by(-10000)),
                         (t_fwd, lambda: seek_by(10000)), (b_full, toggle_full),
                         (overlay, toggle_ctl)):
            lst = _Click(cb)
            keep.append(lst)
            view.setOnClickListener(lst)
        sl_l = _SeekL(on_prog, on_start, on_stop)
        keep.append(sl_l)
        sb.setOnSeekBarChangeListener(sl_l)
        app._pl_ov_keep = keep

        def update(pos, dur, playing):
            try:
                if st["seek_t"] is not None:
                    if time.time() - st["seek_ts"] < 2.0 and abs(pos - st["seek_t"]) > 1500:
                        pos = st["seek_t"]
                    elif time.time() - st["seek_ts"] >= 2.0:
                        if st["seek_t"] > 3000 and pos < 1500:
                            _plog("seek did not stick: want=%d got=%d" % (st["seek_t"], pos))
                        st["seek_t"] = None
                t_dur.setText(jstr(mmss(dur if dur > 0 else 0)))
                if not st["drag"]:
                    t_cur.setText(jstr(mmss(pos)))
                    if dur > 0:
                        sb.setProgress(int(min(1000, pos * 1000.0 / dur)))
                m2 = getattr(app, "_pl_mp2", None)
                if m2 is not None and playing:
                    try:
                        if abs(int(m2.getCurrentPosition()) - int(pos)) > 500:
                            m2.seekTo(int(pos))
                    except Exception:
                        pass
                if playing != st["playing"]:
                    st["playing"] = playing
                    b_play.setImageBitmap(ic_pause if playing else ic_play)
                if (playing and ctl.getVisibility() == 0 and not st["drag"]
                        and time.time() - st["last"] > 3.5):
                    ctl.setVisibility(8)
            except Exception as exu:
                _plog("overlay update error: %s" % exu)

        @run_on_ui_thread
        def _upd(pos, dur, playing):
            update(pos, dur, playing)

        @run_on_ui_thread
        def _fs_exit():
            if st["full"]:
                set_full(False)
        app._pl_ov_update = _upd
        app._pl_fs_exit = _fs_exit
        app._pl_full = False

    def _player_open_web(self, tok, e):
        """بث يوتيوب جوه WebView: بيبدأ فورًا، بينزّل اللي بتشوفه بس، والتقديم بيشتغل.
        أزرار التحكم (شريط/-10/+10/ملء الشاشة) بتاعتنا، بتتحكم في عنصر الفيديو بـ JavaScript.
        بيرجّع False لو WebView مش متاح."""
        try:
            from jnius import autoclass, PythonJavaClass, java_method
            run_on_ui_thread = _main_ui_decorator()
            PythonActivity = None
            for _n in ("org.kivy.android.PythonActivity", "org.renpy.android.PythonActivity"):
                try:
                    PythonActivity = autoclass(_n)
                    break
                except Exception:
                    continue
            if PythonActivity is None:
                return False
            WebView = autoclass("android.webkit.WebView")
            WebViewClient = autoclass("android.webkit.WebViewClient")
            FrameLayout = autoclass("android.widget.FrameLayout")
            FLP = autoclass("android.widget.FrameLayout$LayoutParams")
            LP = autoclass("android.view.ViewGroup$LayoutParams")
            AButton = autoclass("android.widget.Button")
            JString = autoclass("java.lang.String")
            from jnius import cast
        except Exception as ex:
            _plog("web player unavailable: %s" % ex)
            return False
        try:
            if not _WV_PREPARED[0]:
                _WV_PREPARED[0] = True
                self._ui_call(lambda: WebView.setDataDirectorySuffix("novaget"), timeout=3.0)
                _plog("webview data dir suffix set (player)")
        except Exception as exs:
            _plog("webview suffix: %s" % str(exs)[:150])
        app = self
        view_h = int(getattr(self, "_pl_vh", 0) or (Window.width * 9 / 16))
        url = self._player_url(e)
        try:
            self._pl_lbl.text = ""
        except Exception:
            pass

        class _VCB(PythonJavaClass):
            __javainterfaces__ = ["android/webkit/ValueCallback"]
            __javacontext__ = "app"

            def __init__(self, f):
                super().__init__()
                self.f = f

            @java_method("(Ljava/lang/Object;)V")
            def onReceiveValue(self, v):
                try:
                    self.f(str(v))
                except Exception as exc:
                    _plog("vcb: %s" % str(exc)[:100])

        class _WebVV(object):
            """بيتصرّف زي VideoView بس بيحرّك عنصر <video> جوه الصفحة."""
            def __init__(self, wv):
                self.wv = wv
                self.pos = 0
                self.dur = 0
                self.playing = False

            def _js(self, code):
                self.wv.evaluateJavascript(
                    "(function(){var v=document.querySelector('video');if(!v)return;%s})()" % code, None)

            def getCurrentPosition(self):
                return int(self.pos)

            def getDuration(self):
                return int(self.dur)

            def isPlaying(self):
                return bool(self.playing)

            def seekTo(self, t):
                self.pos = int(t)
                self._js("v.currentTime=%f;" % (int(t) / 1000.0))

            def start(self):
                self.playing = True
                self._js("v.play();")

            def pause(self):
                self.playing = False
                self._js("v.pause();")

        @run_on_ui_thread
        def _open():
            try:
                act = PythonActivity.mActivity
                frame = FrameLayout(act)
                frame.setBackgroundColor(-16777216)
                wv = WebView(act)
                ws = wv.getSettings()
                ws.setJavaScriptEnabled(True)
                ws.setDomStorageEnabled(True)
                try:
                    ws.setMediaPlaybackRequiresUserGesture(False)
                except Exception:
                    pass
                try:
                    ws.setUserAgentString(_UA_MOBILE)
                except Exception:
                    pass
                wv.setWebViewClient(WebViewClient())
                frame.addView(wv, FLP(-1, -1))
                act.addContentView(frame, LP(-1, view_h))
                vv = _WebVV(wv)
                app._pl_webvv = vv
                try:
                    app._pl_build_overlay(act, frame, vv, PythonActivity, view_h, run_on_ui_thread)
                    _plog("web overlay ok")
                except Exception:
                    import traceback
                    _plog("web overlay error:\n" + traceback.format_exc())

                dens = act.getResources().getDisplayMetrics().density
                skipb = AButton(act)
                skipb.setText(cast("java.lang.CharSequence",
                                   JString("تخطي الإعلان" if app.rtl else "Skip ad")))
                skipb.setTextColor(-1)
                skipb.setBackgroundColor(0xCC000000 - (1 << 32))
                slp = FLP(-2, int(44 * dens), 85)      # أسفل اليمين
                slp.setMargins(0, 0, int(12 * dens), int(64 * dens))
                frame.addView(skipb, slp)
                skipb.setVisibility(8)
                sk_state = {"on": False}

                class _SkipClick(PythonJavaClass):
                    __javainterfaces__ = ["android/view/View$OnClickListener"]
                    __javacontext__ = "app"

                    @java_method("(Landroid/view/View;)V")
                    def onClick(self, v_):
                        try:
                            app._pl_skip_tap()
                        except Exception as exc:
                            _plog("skip click: %s" % exc)
                skl = _SkipClick()
                app._pl_skip_keep = skl
                skipb.setOnClickListener(skl)

                def on_state(sv):
                    sv = sv.strip().strip('"')
                    if "|" not in sv:
                        return
                    parts_ = sv.split("|")
                    a_, b_, c_ = parts_[:3]
                    want_sk = len(parts_) > 4 and parts_[4] == "1"
                    if want_sk != sk_state["on"]:
                        sk_state["on"] = want_sk
                        skipb.setVisibility(0 if want_sk else 8)
                    vv.pos = int(float(a_) * 1000)
                    vv.dur = int(float(b_) * 1000)
                    vv.playing = (c_ == "1")
                    fn = getattr(app, "_pl_ov_update", None)
                    if fn is not None:
                        fn(vv.pos, vv.dur, vv.playing)
                vcb = _VCB(on_state)
                app._pl_vcb = vcb
                wv.loadUrl(url)
                app._pl_layout, app._pl_wv, app._pl_web = frame, wv, True
                _plog("web player loaded: %s" % url)
            except Exception as ex:
                _plog("web open error: %s" % ex)

                def _fb(dt):
                    app._force_native = True
                    app._player_open(e)
                Clock.schedule_once(_fb, 0)
        _open()
        cnt = [0]

        @run_on_ui_thread
        def _poll_js(wv, vcb):
            try:
                wv.evaluateJavascript(_PL_STATE_JS, vcb)
            except Exception as exj:
                _plog("state js: %s" % str(exj)[:100])

        @run_on_ui_thread
        def _iso_js(wv):
            try:
                wv.evaluateJavascript(_PL_ISOLATE_JS, None)
            except Exception as exj:
                _plog("isolate js: %s" % str(exj)[:100])

        def _tick(dt):
            if getattr(app, "_pl_token", None) is not tok:
                return False
            wv = getattr(app, "_pl_wv", None)
            vcb = getattr(app, "_pl_vcb", None)
            if wv is None or vcb is None or not getattr(app, "_pl_web", False):
                return True
            cnt[0] += 1
            _poll_js(wv, vcb)
            if cnt[0] <= 120 and cnt[0] % 2 == 0:
                _iso_js(wv)
            return True
        Clock.schedule_interval(_tick, 0.5)
        try:
            panel = getattr(app, "_pl_bar", None)
            if panel is not None:
                rtl = app.rtl
                tr = BoxLayout(size_hint_y=None, height=dp(48), padding=[dp(12), 0], spacing=dp(10))
                bq = RoundButton(text=fix("الجودة" if rtl else "Quality"), bg=CARD2, radius=dp(22))
                bq.bind(on_release=lambda *_: app._pl_quality_menu())
                bc = RoundButton(text=fix("الترجمة" if rtl else "Subtitles"), bg=CARD2, radius=dp(22))
                bc.bind(on_release=lambda *_: app._pl_caption_menu())
                for w in ((bc, bq) if rtl else (bq, bc)):
                    tr.add_widget(w)
                tr.padding = [dp(12), dp(4)]
                tr.height = dp(56)
                panel.add_widget(tr, index=2)
                # صف التحميل/الإغلاق: نفس الارتفاع والعرض (عمودين متساويين) تحت صف الجودة/الترجمة
                rowx = panel.children[1]
                if isinstance(rowx, BoxLayout) and len(rowx.children) == 2:
                    rowx.padding = [dp(12), dp(4)]
                    rowx.height = dp(56)
                    for c in rowx.children:
                        c.size_hint_x = .5
        except Exception as exb:
            _plog("quality/caption buttons: %s" % str(exb)[:100])
        return True

    def _pl_js(self, js, cb=None):
        wv = getattr(self, "_pl_wv", None)
        if wv is None or not getattr(self, "_pl_web", False):
            return False
        run_on_ui_thread = _main_ui_decorator()
        vcb = None
        if cb is not None:
            vcb = _make_value_callback(lambda sv: Clock.schedule_once(lambda dt: cb(sv), 0))

        @run_on_ui_thread
        def go():
            try:
                wv.evaluateJavascript(js, vcb)
            except Exception as ex:
                _plog("pl_js: %s" % str(ex)[:100])
        go()
        return True

    def _pl_skip_tap(self):
        """ضغطة لمس حقيقية على زرار تخطي يوتيوب (الضغط البرمجي بالـ JS بيتجاهله يوتيوب)."""
        def got(sv):
            d = str(sv).strip().strip('"')
            try:
                x, y = [float(t) for t in d.split("|")[:2]]
            except Exception:
                _plog("skip rect: %s" % d[:40])
                self._pl_js(_PL_SKIP_JS)
                return
            _plog("skip tap at %.0f,%.0f" % (x, y))
            self._pl_tap(x, y)
        self._pl_js(_PL_SKIP_RECT_JS, got)

    def _pl_tap(self, x, y):
        wv = getattr(self, "_pl_wv", None)
        if wv is None:
            return
        from jnius import autoclass
        run_on_ui_thread = _main_ui_decorator()
        MotionEvent = autoclass("android.view.MotionEvent")
        SysClock = autoclass("android.os.SystemClock")
        t0 = [0]

        @run_on_ui_thread
        def _down():
            try:
                t0[0] = int(SysClock.uptimeMillis())
                ev = MotionEvent.obtain(t0[0], t0[0], 0, float(x), float(y), 0)
                wv.dispatchTouchEvent(ev)
                ev.recycle()
            except Exception as ex:
                _plog("tap down: %s" % str(ex)[:100])

        @run_on_ui_thread
        def _up():
            try:
                tt = int(SysClock.uptimeMillis())
                ev = MotionEvent.obtain(t0[0] or tt, tt, 1, float(x), float(y), 0)
                wv.dispatchTouchEvent(ev)
                ev.recycle()
            except Exception as ex:
                _plog("tap up: %s" % str(ex)[:100])
        _down()
        Clock.schedule_once(lambda dt: _up(), 0.08)

    def _pl_pick(self, title, items, on_pick):
        """نافذة اختيار من قايمة. الفيديو بيتخفى (ويتوقف) لحد ما تتقفل."""
        rtl = self.rtl
        self._player_set_visible(False)
        mv = ModalView(size_hint=(.9, .7), background="", background_color=(0, 0, 0, .85))
        box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
        box.add_widget(TLabel(text=fix(title), font_size=dp(16), bold=True,
                              size_hint_y=None, height=dp(36)))
        sv = ScrollView(bar_width=dp(3), bar_color=(1, 1, 1, .25))
        lst = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6))
        lst.bind(minimum_height=lst.setter("height"))
        for label, val in items:
            b = RoundButton(text=fix(label), bg=CARD2, radius=dp(20), size_hint_y=None, height=dp(46))

            def _pick(_w, val=val):
                mv.dismiss()
                on_pick(val)
            b.bind(on_release=_pick)
            lst.add_widget(b)
        sv.add_widget(lst)
        box.add_widget(sv)
        cl = RoundButton(text=fix("إغلاق" if rtl else "Close"), bg=list(ACC1) + [1],
                         radius=dp(20), size_hint_y=None, height=dp(46))
        cl.bind(on_release=lambda *_: mv.dismiss())
        box.add_widget(cl)
        mv.add_widget(box)
        mv.bind(on_dismiss=lambda *_: self._player_set_visible(True))
        mv.open()

    def _pl_quality_menu(self):
        rtl = self.rtl

        def got(sv):
            d = _js_json(sv) or {}
            q = [x for x in (d.get("q") or []) if x != "auto"]
            if not q:
                self._toast("الجودة مش متاحة دلوقتي، استنى الفيديو يشتغل" if rtl
                            else "Quality not available yet", 3)
                return
            cur = d.get("cur") or ""
            items = [("تلقائي (Auto)" if rtl else "Auto", "auto")]
            for x in q:
                items.append((_PL_Q_NAMES.get(x, x) + ("   \u2713" if x == cur else ""), x))
            self._pl_pick("اختار الدقة" if rtl else "Choose quality", items, self._pl_set_quality)
        if not self._pl_js(_PL_Q_LIST_JS, got):
            self._toast("المشغّل لسه مفتحش" if rtl else "Player not ready", 2)

    def _pl_set_quality(self, q):
        q = re.sub(r"[^A-Za-z0-9]", "", str(q))
        if q == "auto":
            body = "pl.setPlaybackQualityRange('tiny','highres');"
        else:
            body = ("pl.setPlaybackQualityRange('%s','%s');pl.setPlaybackQuality('%s');" % (q, q, q))
        self._pl_js("(function(){" + _PL_HEAD + "try{" + body + "}catch(e){}return '{}';})()")

    def _pl_caption_menu(self):
        rtl = self.rtl
        if not self._pl_js(_PL_CAP_LOAD_JS):
            self._toast("المشغّل لسه مفتحش" if rtl else "Player not ready", 2)
            return

        def got(sv):
            d = _js_json(sv) or {}
            tracks = d.get("tracks") or []
            if not tracks and not d.get("cur"):
                self._toast("مفيش ترجمة متاحة للفيديو ده" if rtl else "No captions for this video", 3)
                return
            items = [("إيقاف الترجمة" if rtl else "Captions off", ("off", 0))]
            for i, t in enumerate(tracks):
                nm = str(t.get("name") or t.get("lc") or "?")
                if t.get("kind") == "asr":
                    nm += " (تلقائي)" if rtl else " (auto)"
                items.append((nm, ("t", i)))
            avail = set(x.get("lc") for x in (d.get("tr") or []))
            for lc, nm in _PL_LANGS:
                if not avail or lc in avail:
                    items.append((("ترجمة إلى: " if rtl else "Translate to: ") + nm, ("x", lc)))
            self._pl_pick("الترجمة واللغة" if rtl else "Subtitles & language", items,
                          self._pl_set_caption)
        Clock.schedule_once(lambda dt: self._pl_js(_PL_CAP_LIST_JS, got), 1.0)

    def _pl_set_caption(self, val):
        kind, arg = val
        if kind == "off":
            body = ("try{pl.setOption('captions','track',{});}catch(e){}"
                    "try{pl.unloadModule('captions');}catch(e){}")
        elif kind == "t":
            body = ("pl.loadModule('captions');var g=getTL();var t=g.tl[%d];"
                    "if(t){pl.setOption('captions','track',{languageCode:t.languageCode,kind:t.kind||''});}"
                    % int(arg))
        else:
            lc = re.sub(r"[^A-Za-z-]", "", str(arg))
            body = ("pl.loadModule('captions');var g=getTL();var b=g.tl[0];"
                    "if(!b){try{b=pl.getOption('captions','track');}catch(e){}}"
                    "if(b&&b.languageCode){pl.setOption('captions','track',"
                    "{languageCode:b.languageCode,kind:b.kind||'',"
                    "translationLanguage:{languageCode:'%s'}});}" % lc)
        self._pl_js("(function(){" + _PL_HEAD + _PL_TL_FN + "try{" + body + "}catch(e){}return '{}';})()")

    def _show_text(self, txt):
        """نافذة فيها نص طويل (للأخطاء) مع زرار Copy."""
        try:
            txt = str(txt)[:3000]
            mv = ModalView(size_hint=(.94, .6), background="", background_color=(0, 0, 0, .75))
            box = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))
            ti = TextInput(text=txt, readonly=True, font_size=dp(11),
                           background_color=(.05, .06, .1, 1), foreground_color=(1, 1, 1, 1))
            box.add_widget(ti)
            row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
            b1 = RoundButton(text="Copy", bg=list(ACC1) + [1], radius=dp(20))
            b1.bind(on_release=lambda *_: Clipboard.copy(txt))
            b2 = RoundButton(text="Close", bg=CARD2, radius=dp(20))
            b2.bind(on_release=lambda *_: mv.dismiss())
            row.add_widget(b1)
            row.add_widget(b2)
            box.add_widget(row)
            mv.add_widget(box)
            mv.open()
        except Exception:
            self._toast(str(txt)[:120], 4)

    def _player_fail(self, tok, msg):
        if getattr(self, "_pl_token", None) is not tok:
            return
        _plog("FAIL: %s" % msg)
        self._player_close()
        self._show_text(msg)

    def _pl_kivy_controls(self):
        """لو أزرار الفيديو الأصلية فشلت: أزرار بديلة تحت الفيديو."""
        panel = getattr(self, "_pl_bar", None)
        if panel is None or getattr(self, "_pl_kc", False):
            return
        self._pl_kc = True
        rtl = self.rtl
        tr = BoxLayout(size_hint_y=None, height=dp(52), padding=[dp(12), 0], spacing=dp(10))
        b_back = RoundButton(text="-10", bg=CARD2, radius=dp(24), size_hint_x=.25)
        b_back.bind(on_release=lambda *_: self._player_seek(delta=-10000))
        pp = RoundButton(text=fix("إيقاف" if rtl else "Pause"), bg=CARD2, radius=dp(24),
                         size_hint_x=.5)
        pp.bind(on_release=lambda b: self._player_toggle(b))
        b_fwd = RoundButton(text="+10", bg=CARD2, radius=dp(24), size_hint_x=.25)
        b_fwd.bind(on_release=lambda *_: self._player_seek(delta=10000))
        for w in (b_back, pp, b_fwd):
            tr.add_widget(w)
        panel.add_widget(tr, index=max(0, len(panel.children) - 1))

    def _player_start(self, tok, src, hdrs, asrc=None, ahdrs=None):
        """VideoView بتاع أندرويد (setZOrderMediaOverlay عشان يظهر فوق سطح Kivy)."""
        if getattr(self, "_pl_token", None) is not tok:
            return
        _plog("start: url len=%d separate_audio=%s" % (len(src or ""), bool(asrc)))
        try:
            from jnius import autoclass
            run_on_ui_thread = _main_ui_decorator()
            PythonActivity = None
            for _n in ("org.kivy.android.PythonActivity", "org.renpy.android.PythonActivity"):
                try:
                    PythonActivity = autoclass(_n)
                    break
                except Exception:
                    continue
            if PythonActivity is None:
                raise RuntimeError("PythonActivity not found")
            VideoView = autoclass("android.widget.VideoView")
            FrameLayout = autoclass("android.widget.FrameLayout")
            FLP = autoclass("android.widget.FrameLayout$LayoutParams")
            LP = autoclass("android.view.ViewGroup$LayoutParams")
            Uri = autoclass("android.net.Uri")
            HashMap = autoclass("java.util.HashMap")
            MediaPlayer = autoclass("android.media.MediaPlayer")
            _plog("classes ok")
        except Exception as ex:
            self._player_fail(tok, "init: %s" % ex)
            return
        app = self
        view_h = int(getattr(self, "_pl_vh", 0) or (Window.width * 9 / 16))
        holder = {}

        def _clean(hd):
            # MediaPlayer مبيفكش gzip، فنبعت User-Agent بس (باقي هيدرز yt-dlp بتبوّظ التشغيل)
            out = {}
            try:
                for k, v in (hd or {}).items():
                    if isinstance(k, str) and isinstance(v, str) and k.lower() in ("user-agent", "referer"):
                        out[k] = v
            except Exception:
                pass
            return out
        hdrs = _clean(hdrs)
        ahdrs = _clean(ahdrs if ahdrs is not None else hdrs)
        _plog("headers: %s" % list(hdrs.keys()))

        try:
            from jnius import PythonJavaClass, java_method

            class _Err(PythonJavaClass):
                __javainterfaces__ = ["android/media/MediaPlayer$OnErrorListener"]
                __javacontext__ = "app"

                @java_method("(Landroid/media/MediaPlayer;II)Z")
                def onError(self, mp, what, extra):
                    _plog("video onError what=%s extra=%s" % (what, extra))
                    _m = "video error what=%s extra=%s" % (what, extra)
                    Clock.schedule_once(lambda dt, _m=_m: app._player_fail(tok, _m), 0)
                    return True
            err_l = _Err()
            self._pl_keep = err_l
        except Exception as ex:
            err_l = None
            _plog("listener unavailable: %s" % ex)

        def _build_overlay(act, frame, vv):
            """أزرار التحكم جوه الفيديو نفسه (Views بتاعة أندرويد فوق الفيديو)."""
            from jnius import PythonJavaClass, java_method
            TextView = autoclass("android.widget.TextView")
            ImageButton = autoclass("android.widget.ImageButton")
            SeekBar = autoclass("android.widget.SeekBar")
            LinearLayout = autoclass("android.widget.LinearLayout")
            LLP = autoclass("android.widget.LinearLayout$LayoutParams")
            Bitmap = autoclass("android.graphics.Bitmap")
            BConfig = autoclass("android.graphics.Bitmap$Config")
            Canvas = autoclass("android.graphics.Canvas")
            Paint = autoclass("android.graphics.Paint")
            PStyle = autoclass("android.graphics.Paint$Style")
            Path = autoclass("android.graphics.Path")
            from jnius import cast
            JString = autoclass("java.lang.String")

            def jstr(x):
                # pyjnius مبيحوّلش str لـ CharSequence لوحده في TextView.setText
                return cast("java.lang.CharSequence", JString(str(x)))
            px = lambda v: int(dp(v))
            st = {"drag": False, "full": False, "orient": None, "last": time.time(),
                  "playing": True, "dur": 0, "seek_t": None, "seek_ts": 0.0}

            def icon(kind, size):
                bm = Bitmap.createBitmap(size, size, BConfig.ARGB_8888)
                cv = Canvas(bm)
                p = Paint()
                p.setAntiAlias(True)
                p.setARGB(255, 255, 255, 255)
                sz = float(size)
                if kind == "play":
                    p.setStyle(PStyle.FILL)
                    path = Path()
                    path.moveTo(sz * .32, sz * .18)
                    path.lineTo(sz * .32, sz * .82)
                    path.lineTo(sz * .80, sz * .50)
                    path.close()
                    cv.drawPath(path, p)
                elif kind == "pause":
                    p.setStyle(PStyle.FILL)
                    cv.drawRect(sz * .28, sz * .20, sz * .43, sz * .80, p)
                    cv.drawRect(sz * .57, sz * .20, sz * .72, sz * .80, p)
                else:  # full
                    p.setStyle(PStyle.STROKE)
                    p.setStrokeWidth(sz * .09)
                    m, L = sz * .18, sz * .26
                    for (x, y, dx, dy) in ((m, m, 1, 1), (sz - m, m, -1, 1),
                                           (m, sz - m, 1, -1), (sz - m, sz - m, -1, -1)):
                        cv.drawLine(x, y, x + dx * L, y, p)
                        cv.drawLine(x, y, x, y + dy * L, p)
                return bm
            big = px(64)
            ic_play, ic_pause = icon("play", big), icon("pause", big)
            ic_full = icon("full", px(40))

            class _Click(PythonJavaClass):
                __javainterfaces__ = ["android/view/View$OnClickListener"]
                __javacontext__ = "app"

                def __init__(self, cb):
                    super().__init__()
                    self.cb = cb

                @java_method("(Landroid/view/View;)V")
                def onClick(self, v):
                    try:
                        self.cb()
                    except Exception as exc:
                        _plog("click error: %s" % exc)

            class _SeekL(PythonJavaClass):
                __javainterfaces__ = ["android/widget/SeekBar$OnSeekBarChangeListener"]
                __javacontext__ = "app"

                def __init__(self, f_prog, f_start, f_stop):
                    super().__init__()
                    self.f_prog, self.f_start, self.f_stop = f_prog, f_start, f_stop

                @java_method("(Landroid/widget/SeekBar;IZ)V")
                def onProgressChanged(self, sb, prog, user):
                    try:
                        self.f_prog(prog, user)
                    except Exception:
                        pass

                @java_method("(Landroid/widget/SeekBar;)V")
                def onStartTrackingTouch(self, sb):
                    self.f_start()

                @java_method("(Landroid/widget/SeekBar;)V")
                def onStopTrackingTouch(self, sb):
                    try:
                        self.f_stop(sb.getProgress())
                    except Exception as exc:
                        _plog("seek stop error: %s" % exc)
            keep = []

            def mmss(ms):
                sec = max(0, int(ms / 1000))
                return "%d:%02d" % (sec // 60, sec % 60)

            def tv(text, size, w, h):
                t = TextView(act)
                t.setText(jstr(text))
                for _fn, _a in ((t.setTextColor, (-1,)), (t.setTextSize, (float(size),)),
                                (t.setGravity, (17,))):
                    try:
                        _fn(*_a)
                    except Exception as exs:
                        _plog("textview style: %s" % str(exs)[:120])
                return t

            overlay = FrameLayout(act)
            ctl = FrameLayout(act)
            ctl.setBackgroundColor(0x55000000)
            overlay.addView(ctl, FLP(-1, -1))
            frame.addView(overlay, FLP(-1, -1))

            # الصف اللي في النص: -10  تشغيل/إيقاف  +10
            row = LinearLayout(act)
            row.setOrientation(0)
            row.setGravity(17)
            t_back = tv("-10", 20, big, big)
            t_fwd = tv("+10", 20, big, big)
            b_play = ImageButton(act)
            b_play.setImageBitmap(ic_pause)
            b_play.setBackgroundColor(0)
            for w in (t_back, b_play, t_fwd):
                lp = LLP(big, big)
                lp.setMargins(px(14), 0, px(14), 0)
                row.addView(w, lp)
            ctl.addView(row, FLP(-2, -2, 17))

            # الشريط اللي تحت: الوقت + شريط التقدّم + ملء الشاشة
            bottom = LinearLayout(act)
            bottom.setOrientation(0)
            bottom.setGravity(16)
            bottom.setPadding(px(10), px(4), px(10), px(4))
            t_cur = tv("0:00", 12, 0, 0)
            t_dur = tv("0:00", 12, 0, 0)
            sb = SeekBar(act)
            sb.setMax(1000)
            b_full = ImageButton(act)
            b_full.setImageBitmap(ic_full)
            b_full.setBackgroundColor(0)
            bottom.addView(t_cur, LLP(px(46), -2))
            bottom.addView(sb, LLP(0, -2, 1.0))
            bottom.addView(t_dur, LLP(px(46), -2))
            bottom.addView(b_full, LLP(px(40), px(40)))
            ctl.addView(bottom, FLP(-1, -2, 80))

            def touch():
                st["last"] = time.time()

            def toggle_play():
                touch()
                mp2 = getattr(app, "_pl_mp2", None)
                if vv.isPlaying():
                    vv.pause()
                    if mp2 is not None:
                        mp2.pause()
                    st["playing"] = False
                    app._pl_paused = True
                    b_play.setImageBitmap(ic_play)
                else:
                    vv.start()
                    if mp2 is not None:
                        mp2.start()
                    st["playing"] = True
                    app._pl_paused = False
                    b_play.setImageBitmap(ic_pause)

            def do_seek(t):
                dur = int(vv.getDuration())
                if dur > 0:
                    t = min(max(0, dur - 300), t)
                t = max(0, int(t))
                st["seek_t"], st["seek_ts"] = t, time.time()
                vv.seekTo(t)
                m2 = getattr(app, "_pl_mp2", None)
                if m2 is not None:
                    try:
                        m2.seekTo(t)
                    except Exception as exm:
                        _plog("audio seek: %s" % exm)
                try:
                    t_cur.setText(jstr(mmss(t)))
                    if dur > 0:
                        sb.setProgress(int(min(1000, t * 1000.0 / dur)))
                except Exception:
                    pass
                _plog("seek -> %d ms (dur %d)" % (t, dur))

            def seek_by(d):
                touch()
                # لو لسه في تقديم شغال نكمّل من هدفه (عشان الضغطات المتتالية تتراكم)
                if st["seek_t"] is not None and time.time() - st["seek_ts"] < 2.0:
                    base = st["seek_t"]
                else:
                    base = int(vv.getCurrentPosition())
                do_seek(base + d)

            def set_full(on):
                a = PythonActivity.mActivity
                if on:
                    try:
                        st["orient"] = int(a.getRequestedOrientation())
                        a.setRequestedOrientation(0)
                    except Exception as exf:
                        _plog("orient error: %s" % exf)
                    try:
                        a.getWindow().getDecorView().setSystemUiVisibility(4102)
                    except Exception:
                        pass
                    frame.setLayoutParams(FLP(-1, -1))
                else:
                    frame.setLayoutParams(FLP(-1, view_h))
                    try:
                        a.getWindow().getDecorView().setSystemUiVisibility(0)
                    except Exception:
                        pass
                    try:
                        a.setRequestedOrientation(st["orient"] if st["orient"] is not None else -1)
                    except Exception:
                        pass
                st["full"] = bool(on)
                app._pl_full = bool(on)
                frame.requestLayout()

            def toggle_full():
                touch()
                set_full(not st["full"])

            def toggle_ctl():
                if ctl.getVisibility() == 0:
                    ctl.setVisibility(8)
                else:
                    touch()
                    ctl.setVisibility(0)

            def on_prog(prog, user):
                if user:
                    d = int(vv.getDuration())
                    if d > 0:
                        t_cur.setText(jstr(mmss(d * prog / 1000.0)))

            def on_start():
                st["drag"] = True

            def on_stop(prog):
                st["drag"] = False
                touch()
                d = int(vv.getDuration())
                if d > 0:
                    do_seek(int(d * prog / 1000.0))

            for view, cb in ((b_play, toggle_play), (t_back, lambda: seek_by(-10000)),
                             (t_fwd, lambda: seek_by(10000)), (b_full, toggle_full),
                             (overlay, toggle_ctl)):
                lst = _Click(cb)
                keep.append(lst)
                view.setOnClickListener(lst)
            sl_l = _SeekL(on_prog, on_start, on_stop)
            keep.append(sl_l)
            sb.setOnSeekBarChangeListener(sl_l)
            app._pl_ov_keep = keep

            def update(pos, dur, playing):
                try:
                    if st["seek_t"] is not None:
                        if time.time() - st["seek_ts"] < 2.0 and abs(pos - st["seek_t"]) > 1500:
                            pos = st["seek_t"]
                        elif time.time() - st["seek_ts"] >= 2.0:
                            if st["seek_t"] > 3000 and pos < 1500:
                                _plog("seek did not stick: want=%d got=%d" % (st["seek_t"], pos))
                            st["seek_t"] = None
                    t_dur.setText(jstr(mmss(dur if dur > 0 else 0)))
                    if not st["drag"]:
                        t_cur.setText(jstr(mmss(pos)))
                        if dur > 0:
                            sb.setProgress(int(min(1000, pos * 1000.0 / dur)))
                    m2 = getattr(app, "_pl_mp2", None)
                    if m2 is not None and playing:
                        try:
                            if abs(int(m2.getCurrentPosition()) - int(pos)) > 500:
                                m2.seekTo(int(pos))
                        except Exception:
                            pass
                    if playing != st["playing"]:
                        st["playing"] = playing
                        b_play.setImageBitmap(ic_pause if playing else ic_play)
                    if (playing and ctl.getVisibility() == 0 and not st["drag"]
                            and time.time() - st["last"] > 3.5):
                        ctl.setVisibility(8)
                except Exception as exu:
                    _plog("overlay update error: %s" % exu)

            @run_on_ui_thread
            def _upd(pos, dur, playing):
                update(pos, dur, playing)

            @run_on_ui_thread
            def _fs_exit():
                if st["full"]:
                    set_full(False)
            app._pl_ov_update = _upd
            app._pl_fs_exit = _fs_exit
            app._pl_full = False

        sync = {"v": False, "a": False, "go": False}

        def maybe_start():
            if sync["v"] and sync["a"] and not sync["go"] and getattr(app, "_pl_token", None) is tok:
                sync["go"] = True

                @run_on_ui_thread
                def _go():
                    try:
                        app._pl_wv.start()
                        m2 = getattr(app, "_pl_mp2", None)
                        if m2 is not None:
                            m2.start()
                        _plog("both started")
                    except Exception as exg:
                        _plog("start error: %s" % exg)
                _go()

        prep_l = None
        if asrc:
            try:
                from jnius import PythonJavaClass, java_method

                class _Prep(PythonJavaClass):
                    __javainterfaces__ = ["android/media/MediaPlayer$OnPreparedListener"]
                    __javacontext__ = "app"

                    @java_method("(Landroid/media/MediaPlayer;)V")
                    def onPrepared(self, mp):
                        sync["v"] = True
                        _plog("video prepared")
                        maybe_start()
                prep_l = _Prep()
                self._pl_prep_keep = prep_l
            except Exception as exp:
                _plog("prepared listener unavailable: %s" % exp)
                sync["v"] = True

        @run_on_ui_thread
        def _open():
            try:
                act = PythonActivity.mActivity
                vv = VideoView(act)
                if err_l is not None:
                    vv.setOnErrorListener(err_l)
                try:
                    vv.setZOrderMediaOverlay(True)
                except Exception as ex:
                    _plog("zorder: %s" % ex)
                frame = FrameLayout(act)
                frame.setBackgroundColor(-16777216)  # أسود مصمت
                try:
                    frame.setClickable(True)
                except Exception:
                    pass
                frame.addView(vv, FLP(-1, -1, 17))
                act.addContentView(frame, LP(-1, view_h))
                _plog("videoview added")
                try:
                    _build_overlay(act, frame, vv)
                    _plog("overlay ok")
                except Exception:
                    import traceback
                    _mo = "overlay error:\n" + traceback.format_exc()
                    _plog(_mo)
                    Clock.schedule_once(lambda dt, _mo=_mo: app._pl_kivy_controls(), 0)
                    Clock.schedule_once(lambda dt, _mo=_mo: app._show_text(_mo), 0.3)
                h = HashMap()
                for k, v in (hdrs or {}).items():
                    if isinstance(k, str) and isinstance(v, str):
                        h.put(k, v)
                if asrc and prep_l is not None:
                    vv.setOnPreparedListener(prep_l)
                if str(src).startswith("/"):
                    vv.setVideoPath(src)
                else:
                    vv.setVideoURI(Uri.parse(src), h)
                if not asrc:
                    vv.start()
                _plog("video started")
                app._pl_layout, app._pl_wv = frame, vv
            except Exception as ex:
                holder["err"] = str(ex)
                _plog("open error: %s" % ex)
                _m = "open: %s" % ex
                Clock.schedule_once(lambda dt, _m=_m: app._player_fail(tok, _m), 0)
        _open()

        if asrc:
            def awork():
                try:
                    mp2 = MediaPlayer()
                    app._pl_mp2 = mp2
                    h2 = HashMap()
                    for k, v in (ahdrs or {}).items():
                        if isinstance(k, str) and isinstance(v, str):
                            h2.put(k, v)
                    mp2.setDataSource(PythonActivity.mActivity, Uri.parse(asrc), h2)
                    mp2.prepare()
                    if getattr(app, "_pl_token", None) is tok:
                        sync["a"] = True
                        _plog("audio prepared")
                        maybe_start()
                    else:
                        mp2.release()
                except Exception as ex:
                    _plog("audio error: %s" % ex)
                    app.ui(lambda: app._toast("الصوت ماشتغلش" if app.rtl else "Audio failed to load", 3))
            threading.Thread(target=awork, daemon=True).start()
        Clock.schedule_once(lambda dt: self._player_watchdog(tok), 18 if asrc else 25)
        try:
            if getattr(self, "_pl_tick", None) is not None:
                self._pl_tick.cancel()
        except Exception:
            pass
        self._pl_tick = Clock.schedule_interval(lambda dt: self._player_poll(tok), 0.7)

    def _player_watchdog(self, tok):
        if getattr(self, "_pl_token", None) is not tok:
            return
        vv = getattr(self, "_pl_wv", None)
        if vv is None:
            self._player_fail(tok, "view never created")
            return

        def chk():
            try:
                pos = self._ui_call(lambda: int(vv.getCurrentPosition()), timeout=3.0)
                play = self._ui_call(lambda: bool(vv.isPlaying()), timeout=3.0)
                _plog("watchdog pos=%s playing=%s" % (pos, play))
                if not pos and not play and not getattr(self, "_pl_paused", False):
                    fb = getattr(self, "_pl_fallback", None)
                    if fb is not None:
                        self._pl_fallback = None
                        _plog("stream too slow -> download fallback")

                        def _fb():
                            self._player_release_video()
                            lb = getattr(self, "_pl_lbl", None)
                            if lb is not None:
                                lb.text = fix("جاري التجهيز…" if self.rtl else "Preparing…")
                            fb()
                        self.ui(_fb)
                        return
                    self.ui(lambda: self._player_fail(tok, "video did not start (pos=0). "
                                                           "The stream may be blocked or unsupported."))
                else:
                    self._pl_playing = True
            except Exception as ex:
                _plog("watchdog error: %s" % ex)
        threading.Thread(target=chk, daemon=True).start()

    def _player_poll(self, tok):
        vv = getattr(self, "_pl_wv", None)
        if vv is None or getattr(self, "_pl_token", None) is not tok or getattr(self, "_pl_polling", False):
            return
        self._pl_polling = True

        def work():
            try:
                pos = self._ui_call(lambda: int(vv.getCurrentPosition()), timeout=2.0)
                dur = self._ui_call(lambda: int(vv.getDuration()), timeout=2.0)
                play = self._ui_call(lambda: bool(vv.isPlaying()), timeout=2.0)
                if pos is not None and dur is not None:
                    self.ui(lambda: self._player_apply(tok, pos, dur, bool(play)))
            except Exception:
                pass
            finally:
                self._pl_polling = False
        threading.Thread(target=work, daemon=True).start()

    def _player_apply(self, tok, pos, dur, play=True):
        if getattr(self, "_pl_token", None) is not tok:
            return
        try:
            if pos > 0:
                self._pl_playing = True
                self._pl_thumb.opacity = 0
                self._pl_lbl.text = ""
            fn = getattr(self, "_pl_ov_update", None)
            if fn is not None:
                fn(pos, dur, play)
        except Exception:
            pass

    def _player_seek(self, frac=None, delta=None):
        vv = getattr(self, "_pl_wv", None)
        if vv is None:
            return

        def go():
            try:
                dur = int(vv.getDuration())
                cur = int(vv.getCurrentPosition())
                t = int(dur * frac) if frac is not None else cur + int(delta or 0)
                vv.seekTo(max(0, min(dur, t)))
            except Exception as ex:
                _plog("seek error: %s" % ex)
        threading.Thread(target=lambda: self._ui_call(go, timeout=3.0), daemon=True).start()

    def _player_toggle(self, btn=None):
        vv = getattr(self, "_pl_wv", None)
        if vv is None:
            return
        self._pl_paused = not getattr(self, "_pl_paused", False)
        paused = self._pl_paused
        mp2 = getattr(self, "_pl_mp2", None)

        def go():
            try:
                if paused:
                    vv.pause()
                    if mp2 is not None:
                        mp2.pause()
                else:
                    vv.start()
                    if mp2 is not None:
                        mp2.start()
            except Exception as ex:
                _plog("toggle error: %s" % ex)
        threading.Thread(target=lambda: self._ui_call(go, timeout=3.0), daemon=True).start()
        if btn is not None:
            btn.text = fix(("تشغيل" if paused else "إيقاف") if self.rtl
                           else ("Play" if paused else "Pause"))

    def _player_set_visible(self, show):
        layout, vv = getattr(self, "_pl_layout", None), getattr(self, "_pl_wv", None)
        mp2 = getattr(self, "_pl_mp2", None)
        if vv is None:
            return
        paused = getattr(self, "_pl_paused", False)

        def go():
            try:
                layout.setVisibility(0 if show else 8)
                if getattr(self, "_pl_web", False):
                    if not show:
                        vv.evaluateJavascript(
                            "document.querySelectorAll('video').forEach(function(v){v.pause()})", None)
                    elif not paused:
                        vv.evaluateJavascript(
                            "document.querySelectorAll('video').forEach(function(v){v.play()})", None)
                    return
                if show:
                    if not paused:
                        vv.start()
                        if mp2 is not None:
                            mp2.start()
                else:
                    vv.pause()
                    if mp2 is not None:
                        mp2.pause()
            except Exception as ex:
                _plog("visible error: %s" % ex)
        threading.Thread(target=lambda: self._ui_call(go, timeout=3.0), daemon=True).start()

    def _player_download(self):
        e = getattr(self, "_pl_entry", None)
        if not e:
            return
        # النافذة بتترسم تحت الفيديو، فنخفيه (ونوقف الصوت) لحد ما النافذة تقفل
        self._player_set_visible(False)
        self._srch_quality_popup(e, on_close=lambda: self._player_set_visible(True))

    def _player_close(self):
        try:
            if getattr(self, "_pl_tick", None) is not None:
                self._pl_tick.cancel()
        except Exception:
            pass
        self._pl_tick = None
        try:
            if getattr(self, "_pl_full", False) and getattr(self, "_pl_fs_exit", None) is not None:
                self._pl_fs_exit()
        except Exception:
            pass
        self._pl_full = False
        self._pl_ov_update = None
        self._pl_fallback = None
        self._pl_token = None
        self._pl_playing = False
        self._pl_paused = False
        bar = getattr(self, "_pl_bar", None)
        self._pl_bar = None
        if bar is not None:
            try:
                Window.remove_widget(bar)
            except Exception:
                pass
        self._player_release_video()

    def _player_release_video(self):
        """بيقفل الفيديو بس (من غير ما يقفل اللوحة)."""
        layout, vv = getattr(self, "_pl_layout", None), getattr(self, "_pl_wv", None)
        mp2 = getattr(self, "_pl_mp2", None)
        self._pl_layout = self._pl_wv = self._pl_mp = self._pl_mp2 = None
        self._pl_web = False
        if mp2 is not None:
            def _rel():
                for fn in (mp2.stop, mp2.release):
                    try:
                        fn()
                    except Exception:
                        pass
            threading.Thread(target=_rel, daemon=True).start()
        if layout is not None:
            try:
                run_on_ui_thread = _main_ui_decorator()

                @run_on_ui_thread
                def _close(layout=layout, vv=vv):
                    try:
                        vv.stopPlayback()
                    except Exception:
                        try:
                            vv.loadUrl("about:blank")
                            vv.destroy()
                        except Exception:
                            pass
                    _remove_android_view(layout)
                _close()
            except Exception:
                pass

    def _show_player_log(self, *_):
        """لو في سجل من تشغيل قبل كده (مثلًا البرنامج قفل)، نعرضه مرة واحدة."""
        try:
            fp = os.path.join(os.path.dirname(settings_path()), "novaget_player_log.txt")
            if not os.path.exists(fp):
                return
            with open(fp, encoding="utf-8") as f:
                lines = [x.rstrip() for x in f.readlines() if x.strip()]
            os.replace(fp, fp + ".old")
            if not lines:
                return
            if not any(re.search(r"error|fail|crash|exception|timeout", x, re.I) for x in lines):
                return
            txt = "\n".join(lines[-14:])
            mv = ModalView(size_hint=(.94, .6), background="", background_color=(0, 0, 0, .75))
            box = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))
            ti = TextInput(text=txt, readonly=True, font_size=dp(11),
                           background_color=(.05, .06, .1, 1), foreground_color=(1, 1, 1, 1))
            box.add_widget(ti)
            row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
            b1 = RoundButton(text="Copy", bg=list(ACC1) + [1], radius=dp(20))
            b1.bind(on_release=lambda *_: Clipboard.copy(txt))
            b2 = RoundButton(text="Close", bg=CARD2, radius=dp(20))
            b2.bind(on_release=lambda *_: mv.dismiss())
            row.add_widget(b1)
            row.add_widget(b2)
            box.add_widget(row)
            mv.add_widget(box)
            mv.open()
        except Exception:
            pass

    def _srch_download(self, e, kind, q):
        if kind == "v":
            it = dict(pl="v", pl_q=q, pl_key="v:%d" % q, kind="m", fid="pl", h=q, tbr=0,
                      label=("%dp" % q) if q else ts("pl_best"), ext="MP4",
                      size=None, codec="", fps=0)
        else:
            mp3 = bool(HAS_FFMPEG or HAS_AV)
            it = dict(pl="a", pl_q=q, pl_key="a:%d" % q, kind="a", fid="pl", h=0, tbr=q,
                      label=("%dk" % q) if q else ts("pl_best"), ext="MP3" if mp3 else "M4A",
                      size=None, codec="", fps=0)
        it["thumb"] = e.get("thumb") or ""
        ok = self.enqueue(Job(e["url"], e["title"], it), quiet=True)
        SOUND.play("start" if ok else "error")
        self._after_change()
        self._toast(("اتضاف للتحميلات ✓" if self.rtl else "Added to downloads") if ok
                    else ("متضاف قبل كده" if self.rtl else "Already in the list"), 1.6)

    # ---------------- fetch
    def fetch_formats(self, *_):
        url = self.url_input.text.strip()
        if not url:
            self.set_status("paste_first", RED)
            return
        if yt_dlp is None:
            self.set_status("read_fail", RED,
                            "yt-dlp is missing. Install it with: pip install -U yt-dlp")
            return
        if self.analyze_btn.disabled:
            return
        self.analyze_btn.disabled = True
        self.analyze_btn.text = t("analyzing")
        self.has_info = False
        self.pl_mode = False
        self.img_mode = False
        self.image_items = []
        self.dyn.clear_widgets()
        self.show_empty()
        if not self.jobs:
            self.set_progress(0)
        self.set_status("fetching", TXT, ("wait",))
        threading.Thread(target=self._fetch_worker, args=(url,), daemon=True).start()

    def _fetch_worker(self, url):
        try:
            self._fetch_worker_inner(url)
        except Exception as e:
            self.set_status("read_fail", RED, user_facing_error(e))
            self.ui(SOUND.play, "error")
        finally:
            def _done():
                self.analyze_btn.disabled = False
                self.analyze_btn.text = t("analyze")
            self.ui(_done)

    def _fetch_worker_inner(self, url):
        info = None
        err = None
        images, meta = [], None
        del FETCH_LOG[:]
        flog("URL: %s" % url)
        if re.search(r"facebook\.com/share/|fb\.watch", url, re.I):
            url = _fb_resolve(url)
        is_tiktok = bool(re.search(r"tiktok\.com|vm\.tiktok|vt\.tiktok", url, re.I))

        # تيك توك: جرب استخراج الصور أولًا (خصوصًا منشورات /photo/)
        tt_box = {}
        tt_thread = None
        if is_tiktok:
            def _tt_job():
                try:
                    tt_box["r"] = extract_tiktok_images(url)
                except Exception:
                    tt_box["r"] = ([], None)
            tt_thread = threading.Thread(target=_tt_job, daemon=True)
            tt_thread.start()          # بالتوازي مع yt-dlp بدل ما نستناه

        # ستوري/هاي لايت إنستجرام: عن طريق API الأول (بيجيب الصور والفيديوهات)
        if "instagram.com/stories/" in url.lower():
            try:
                images, meta = _ig_story_items(url)
            except Exception as ex:
                import traceback
                flog("IG story crashed: %s" % traceback.format_exc()[-300:])
                images, meta = [], None

        try:
            # لينك فيه v= أو youtu.be يعتبر فيديو واحد
            single = bool(re.search(r"[?&]v=|youtu\.be/|/photo/", url))
            _o = {"quiet": True, "no_warnings": True, "noplaylist": single,
                  "extract_flat": False if "/stories/" in url.lower() else "in_playlist",
                  "noprogress": True, "logger": QuietLogger(),
                  "retries": 4, "extractor_retries": 3, "fragment_retries": 3,
                  "socket_timeout": 25, "cachedir": _ytdlp_cache_dir()}
            if "instagram.com" in url.lower() and ig_cookiefile():
                _o["cookiefile"] = ig_cookiefile()
            if re.search(r"facebook\.com|fb\.watch", url, re.I) and fb_cookiefile():
                _o["cookiefile"] = fb_cookiefile()
            ydl_url = url.split("?")[0] if "instagram.com/stories/" in url.lower() else url
            known = bool(_KNOWN_HOSTS_RE.search(url))
            if known and not YT_READY.is_set():
                flog("waiting for yt-dlp first-time load")
                YT_READY.wait(150)
            if not known:
                # موقع غير معروف: yt-dlp بيجرّب extractor عام وممكن يتعلّق دقايق (iframes/إعادة محاولات).
                # فنقصّر المهلات، ونحمّل الصفحة بالتوازي، ونوقف انتظار yt-dlp بعد 25 ثانية.
                _o.update(retries=1, extractor_retries=1, fragment_retries=1, socket_timeout=12)
                # أول مرة yt-dlp بيحمّل مئات الـ extractors (ممكن دقيقة على الموبايل): نستناه من غير ما نحسبه من الـ 25 ثانية
                if not YT_READY.is_set():
                    flog("waiting for yt-dlp first-time load")
                    YT_READY.wait(150)

                def _prefetch():
                    try:
                        _page_scrape(url)
                    except Exception:
                        pass
                threading.Thread(target=_prefetch, daemon=True).start()
            if not (images and "/stories/" in url.lower()):
                def _run_ydl():
                    with yt_dlp.YoutubeDL(_o) as y:
                        return y.extract_info(ydl_url, download=False)
                if known:
                    # أول محاولة ممكن تتعلّق (تحميل سكربت المشغّل): نديها 40 ثانية وبعدها نعيد تلقائيًا
                    kbox = {}

                    def _k_thread(n):
                        try:
                            kbox[n] = ("i", _run_ydl())
                        except Exception as ex:
                            kbox[n] = ("e", ex)
                    for _n in (1, 2):
                        kth = threading.Thread(target=_k_thread, args=(_n,), daemon=True)
                        kth.start()
                        kth.join(40 if _n == 1 else 90)
                        if _n in kbox:
                            break
                        flog("analysis stalled, retrying")
                    got = kbox.get(1) or kbox.get(2)
                    if got is None:
                        raise TimeoutError("analysis timed out, try again")
                    if got[0] == "e":
                        raise got[1]
                    info = got[1]
                else:
                    ybox = {}

                    def _ydl_thread():
                        try:
                            ybox["i"] = _run_ydl()
                        except Exception as ex:
                            ybox["e"] = ex
                    yth = threading.Thread(target=_ydl_thread, daemon=True)
                    yth.start()
                    yth.join(35)
                    if yth.is_alive():
                        raise TimeoutError("yt-dlp took more than 35s on this site; scanning the page directly")
                    if "e" in ybox:
                        raise ybox["e"]
                    info = ybox.get("i")
        except Exception as e:
            err = e
            flog("yt-dlp: %s" % clean_err(e)[:300])

        if tt_thread is not None:
            tt_thread.join(40)
            images, meta = tt_box.get("r") or ([], None)

        # صور حقيقية فقط (كاروسيل) — مش thumbnails فيديو يوتيوب
        if not images and info:
            try:
                images = extract_images_from_info(info)
            except Exception:
                images = []

        # فيسبوك/إنستجرام/تويتر/ريديت...: لو yt-dlp فشل أو ملقاش فيديو → جيب صور المنشور بنفسنا
        SOCIAL_DIAG[0] = ""
        # فيديو من أي صفحة ويب (كروم...): لو مفيش صورة مصغّرة ناخدها من og:image،
        # ولو الاستخراج generic نضيف صور الصفحة في تبويب الصور
        if info is not None and info.get("formats"):
            try:
                ek = (info.get("extractor_key") or info.get("extractor") or "").lower()
                need_thumb = not pick_thumb(info)
                if ek.startswith("generic") or need_thumb:
                    pg_imgs, pg_og, _t, _pg_vids = _page_scrape(url)
                    if need_thumb and pg_og:
                        info["thumbnail"] = pg_og[0]
                    if ek.startswith("generic") and not images:
                        images = pg_imgs
            except Exception:
                pass
        # إنستجرام + sessionid: API بيجيب كل صور الكاروسيل — لو أكتر من اللي yt-dlp جابه نستخدمه
        if "instagram.com" in url.lower() and _ig_session_value():
            try:
                mm = re.search(r"instagram\.com/(?:[\w.]+/)?(?:p|tv)/([\w-]+)", url)
                if mm:
                    api_imgs, api_meta = _ig_api_images(mm.group(1))
                    if len(api_imgs) > len(images):
                        images, meta = api_imgs, api_meta
            except Exception:
                pass
        no_video = (info is None) or (not info.get("formats") and not info.get("entries"))
        if not images and no_video and not is_tiktok:
            try:
                images, meta = extract_social_images(url)
            except Exception:
                images, meta = [], None

        self.url = url
        entries = [e for e in ((info or {}).get("entries") or []) if isinstance(e, dict)]
        all_img_entries = bool(images) and bool(entries) and len(images) >= len(entries)
        if info is not None and info.get("formats"):
            self.ui(self._on_info, info, images)          # فيديو (ومعاه صور لو فيه)
        elif images and (info is None or not entries or all_img_entries):
            # منشور صور فقط (تيك توك / إنستجرام / فيسبوك / ...)
            fake = {
                "title": ((info or {}).get("title") or (meta or {}).get("title") or "Photos"),
                "uploader": ((info or {}).get("uploader") or (meta or {}).get("author") or ""),
                "thumbnail": images[0]["url"],
                "thumbnails": [{"url": images[0]["url"]}],
            }
            self.ui(self._on_info, fake, images)
        elif info is not None and (info.get("_type") == "playlist"
                                   or (info.get("entries") is not None and not info.get("formats"))):
            self.ui(self._on_playlist, info, self._pl_norm(info), [])
        elif info is not None:
            self.ui(self._on_info, info, images)
        else:
            if not _KNOWN_HOSTS_RE.search(url) and self._sniff_available():
                # آخر حل: الموقع بيحمّل الفيديو بجافاسكريبت فمفيش رابط في كود الصفحة.
                # نفتح الصفحة في متصفح داخلي ونلقط رابط الفيديو الحقيقي اللي بيطلبه.
                flog("nothing in page source -> in-app browser capture")
                self.ui(self._sniff_open, url)
                return
            self.set_status("read_fail", RED,
                            SOCIAL_DIAG[0] or (clean_err(err) if err else ts("img_none")))
            self.ui(SOUND.play, "error")
            if FETCH_LOG:
                self.ui(self.show_error_details,
                        (SOCIAL_DIAG[0] + "\n\n" if SOCIAL_DIAG[0] else "") + "\n".join(FETCH_LOG))

    def _build_items(self, info):
        """بيبني قوائم الجودات (فيديو، صوت) من معلومات فيديو واحد."""
        dur = info.get("duration") or 0
        items = []
        seen = set()
        for f in info.get("formats", []):
            v, a = f.get("vcodec") or "none", f.get("acodec") or "none"
            if f.get("ext") == "mhtml" or (v == "none" and a == "none"):
                continue
            fid = f.get("format_id")
            if not fid or fid in seen:
                continue
            seen.add(fid)
            tbr = f.get("tbr") or 0
            size = f.get("filesize") or f.get("filesize_approx")
            if not size and tbr and dur:
                size = tbr * 125.0 * dur
            h = f.get("height") or 0
            if v != "none":
                kind = "av" if a != "none" else "v"
                label = "%sp" % h if h else (f.get("format_note") or fid)
                codec = codec_name(v)
            else:
                kind = "a"
                abr = int(f.get("abr") or tbr or 0)
                label = "%dk" % abr if abr else "Audio"
                codec = codec_name(a)
            items.append(dict(
                kind=kind, label=str(label)[:9], ext=(f.get("ext") or "?").upper(),
                size=size, fid=fid, h=h, tbr=tbr, codec=codec, fps=f.get("fps") or 0,
            ))

        # صف واحد لكل جودة: بنختار أنسب صيغة (MP4/H.264 أولًا عشان تشتغل على كل الهواتف)
        def score(i):
            return (0 if HAS_FFMPEG else int(i["kind"] == "av"),
                    int(i["ext"] in ("MP4", "M4A")),
                    int(i["codec"] in ("H.264", "AAC")),
                    i["tbr"])

        best = {}
        for i in items:
            if i["kind"] == "a":
                key = ("a", int(round(i["tbr"] / 8.0)))
            else:
                key = ("v", i["h"] or i["fid"])
            if key not in best or score(i) > score(best[key]):
                best[key] = i
        grouped = list(best.values())
        for i in grouped:
            if i["kind"] == "v":
                if HAS_FFMPEG:
                    i["kind"] = "m"    # هيتدمج مع أفضل صوت بـ ffmpeg
                elif i["ext"] == "MP4":
                    i["kind"] = "mx"   # هيتدمج مع صوت m4a بالدمج الداخلي
        grouped = [i for i in grouped if i["kind"] != "v"]   # فيديو بدون صوت مش بنعرضه

        video_items = sorted(
            [i for i in grouped if i["kind"] != "a"],
            key=lambda i: (i["h"], i["tbr"]), reverse=True)
        aud = [i for i in grouped if i["kind"] == "a"]
        if (HAS_FFMPEG or HAS_AV) and aud:
            src = max(aud, key=lambda i: i["tbr"])
            audio_items = [
                dict(kind="a", label="%dk" % kb, ext="MP3", size=(kb * 125.0 * dur) if dur else None,
                     fid=src["fid"], h=0, tbr=kb, codec="MP3", fps=0, mp3=kb)
                for kb in (320, 192, 128) if kb <= max(128, src["tbr"] * 1.3)]
        else:   # من غير ffmpeg: صيغة M4A بس (بتشتغل على كل الأجهزة) بدل الصيغ الغريبة
            m4a = [i for i in aud if i["ext"] == "M4A"]
            audio_items = sorted(m4a or aud, key=lambda i: i["tbr"], reverse=True)
        return video_items, audio_items

    def _dyn_add(self, w):
        """يضيف ويدجت لـ dyn بعد ما يشيله من أي أب قديم (كان بيسبب Cannot add ... already has a parent)."""
        try:
            if w.parent is not None:
                w.parent.remove_widget(w)
        except Exception:
            pass
        self.dyn.add_widget(w)

    def _on_info(self, info, images=None):
        self.pl_mode = False
        self.img_mode = False
        self.info_card.height = dp(96)
        dur = info.get("duration") or 0
        meta = " · ".join(x for x in (info.get("uploader") or "", fmt_dur(dur)) if x)
        self.info_data = {"title": info.get("title") or "Untitled", "meta": meta}
        self._load_thumb(pick_thumb(info) or ((images or [{}])[0].get("url") if images else ""))
        self.has_info = True
        self.dyn.clear_widgets()
        self._dyn_add(self.info_card)
        self._dyn_add(self.tabs)

        try:
            self.video_items, self.audio_items = self._build_items(info)
        except Exception:
            self.video_items, self.audio_items = [], []
        # فيديوهات مباشرة لقيناها في الصفحة (mp4/m3u8/ستوري...): مكانها تبويب الفيديو مش الصور
        direct = [dict(im) for im in (images or []) if im.get("vurl")]
        images = [im for im in (images or []) if not im.get("vurl")]
        for im in direct:
            im["thumb"] = im["url"] if im.get("url") != im.get("vurl") else ""
        self.video_items = list(self.video_items) + direct
        # احتفظ بصورة الفيديو مع اختيار الجودة حتى تظهر في كارت التحميل.
        _job_thumb = pick_thumb(info)
        for _item in self.video_items + self.audio_items:
            _item.setdefault("thumb", _job_thumb)
        self.image_items = list(images or [])
        for i, im in enumerate(self.image_items, 1):
            im.setdefault("sel", True)
            im.setdefault("label", "#%d" % i)

        self.tab_images.opacity = 1 if self.image_items else 0
        self.tab_images.disabled = not bool(self.image_items)
        self.tab_video.opacity = 1 if self.video_items else 0
        self.tab_video.disabled = not bool(self.video_items)
        self.tab_audio.opacity = 1 if self.audio_items else 0
        self.tab_audio.disabled = not bool(self.audio_items)

        self.render_info()
        if self.image_items and not self.video_items and not self.audio_items:
            self.set_tab("images")
        elif self.video_items:
            self.set_tab("video")
        elif self.audio_items:
            self.set_tab("audio")
        elif self.image_items:
            self.set_tab("images")
        else:
            self.show_empty()
        self.set_status("choose", TXT, ("saves", self.download_dir))
        self._open_detail_page(self.info_data.get("title"))

    def _load_thumb(self, url):
        self.thumb.texture = None
        self.thumb.opacity = 0      # من غير صورة: نخفي المربع الأبيض
        self.thumb.width = 0
        self._thumb_url = url
        if not url:
            return

        def work():
            try:
                import urllib.request
                req = urllib.request.Request(url, headers={
                    "User-Agent": ("Mozilla/5.0 (Linux; Android 13; Mobile) "
                                   "AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36"),
                    "Accept": "image/jpeg,image/png,image/*;q=0.8,*/*;q=0.5",
                    **({"Referer": "https://www.youtube.com/"} if "ytimg" in url or "youtube" in url else {}),
                })
                data = urllib.request.urlopen(req, timeout=15).read()
                if data:
                    self.ui(self._decode_thumb, data, url)
            except Exception:
                # جرّب hqdefault لو maxres فشل
                if "maxresdefault" in (url or ""):
                    alt = url.replace("maxresdefault", "hqdefault")
                    try:
                        import urllib.request
                        req = urllib.request.Request(alt, headers={
                            "User-Agent": "Mozilla/5.0",
                            "Referer": "https://www.youtube.com/",
                        })
                        data = urllib.request.urlopen(req, timeout=15).read()
                        if data:
                            self.ui(self._decode_thumb, data, url)
                    except Exception:
                        pass
        threading.Thread(target=work, daemon=True).start()

    def _decode_thumb(self, data, url):
        if getattr(self, "_thumb_url", None) != url:
            return
        import io
        from kivy.core.image import Image as CoreImage

        tex = None
        try:
            from PIL import Image as PILImage
            buf = io.BytesIO()
            im = PILImage.open(io.BytesIO(data)).convert("RGB")
            im.thumbnail((320, 320))
            im.save(buf, "JPEG", quality=85)
            buf.seek(0)
            tex = CoreImage(buf, ext="jpg").texture
        except Exception:
            tex = None
        if tex is None:
            for ext in ("jpg", "png"):
                try:
                    tex = CoreImage(io.BytesIO(data), ext=ext).texture
                    if tex is not None:
                        break
                except Exception:
                    tex = None
        if tex is not None:
            self.thumb.texture = tex
            self.thumb.width = dp(120)
            self.thumb.opacity = 1

    def render_info(self):
        if not self.has_info:
            return
        title = self.info_data["title"]
        arabic = bool(AR_RE.search(title))
        self.info_title.text = fix(title)
        self.info_title.halign = "right" if (self.rtl or arabic) else "left"
        if arabic:
            self.info_title.shorten, self.info_title.shorten_from = True, "left"
            self.info_title.max_lines = 1
        else:
            self.info_title.shorten, self.info_title.max_lines = False, 2
        meta = self.info_data["meta"]
        if self.info_data.get("pl_n") is not None:
            meta = ts("pl_meta", self.info_data["pl_n"])
        elif getattr(self, "image_items", None) and not getattr(self, "video_items", None):
            meta = ts("img_meta", len(self.image_items))
        self.info_meta.text = fix(meta)
        self.info_meta.halign = "right" if self.rtl else "left"
        self.info_card.clear_widgets()
        for w in ((self.info_col, self.thumb) if self.rtl else (self.thumb, self.info_col)):
            self.info_card.add_widget(w)

    # ---------------- tabs / list
    def set_tab(self, name):
        self.tab = name
        self.tab_video.bg = list(ACC1) + [1] if name == "video" else list(TRANSP)
        self.tab_audio.bg = list(ACC1) + [1] if name == "audio" else list(TRANSP)
        self.tab_images.bg = list(ACC1) + [1] if name == "images" else list(TRANSP)
        self.tab_video.color = WHITE if name == "video" else DIM
        self.tab_audio.color = WHITE if name == "audio" else DIM
        self.tab_images.color = WHITE if name == "images" else DIM
        # إخفاء/إظهار تبويب الصور حسب التوفر
        if hasattr(self, "image_items"):
            self.tab_images.opacity = 1 if self.image_items else 0
            self.tab_images.disabled = not bool(self.image_items)
        if self.pl_mode:
            self._pl_render()
            return
        if name == "images":
            self._img_render()
            return
        # رجّع المساحات الطبيعية خارج وضع الصور
        self.img_mode = False
        try:
            self.hdr.height = dp(84)
            self.tagline.opacity = 1
            self.tagline.height = dp(20)
            self.info_card.height = dp(96)
            self.prog_card.height = dp(106)
            self.root_box.spacing = dp(8)
        except Exception:
            pass
        # أزل شريط التحكم بالصور لو كنا عليه
        if self.img_ctrl in self.dyn.children:
            self.dyn.remove_widget(self.img_ctrl)
        items = self.video_items if name == "video" else self.audio_items
        self.list_box.clear_widgets()
        if not items:
            lb = TLabel(text=t("no_formats"), halign="center", valign="middle",
                        shorten=False, color=DIM, size_hint_y=None, height=dp(120))
            self.list_box.add_widget(lb)
            return
        if name == "audio" and not (HAS_FFMPEG or HAS_AV):
            self.list_box.add_widget(TLabel(
                text=t("mp3_hint"), halign="center", valign="middle", shorten=False,
                color=DIM, font_size=dp(12), size_hint_y=None, height=dp(52)))
        for it in items:
            self.list_box.add_widget(self.make_row(it))

    def _make_direct_video_row(self, it):
        """صف فيديو مباشر من صفحة ويب: صورته + اسمه + (الحجم والدقة بتتحسب في الخلفية)."""
        al = "right" if self.rtl else "left"
        vurl = it["vurl"]
        ext = str(it.get("ext") or "MP4")
        row = Card(orientation="horizontal", size_hint_y=None, height=dp(96),
                   padding=[dp(10), dp(10)], spacing=dp(12), bg=CARD, radius=dp(20), elev=1)
        box = FloatLayout(size_hint=(None, None), size=(dp(112), dp(76)),
                          pos_hint={"center_y": .5})
        ph = Card(size_hint=(1, 1), pos_hint={"x": 0, "y": 0}, radius=dp(12),
                  bg=badge_color("m", 720), border=[0, 0, 0, 0])
        ph.add_widget(Label(text=str(it.get("label", "")).split(" ")[0], bold=True, font_size=dp(15)))
        img = KImage(size_hint=(1, 1), pos_hint={"x": 0, "y": 0}, allow_stretch=True,
                     keep_ratio=True, opacity=0)
        box.add_widget(ph)
        box.add_widget(img)

        mid = BoxLayout(orientation="vertical", spacing=dp(2))
        mid.add_widget(TLabel(text=fix(it.get("name") or str(it.get("label", ""))),
                              font_size=dp(14), bold=True, halign=al))
        meta = TLabel(text=fix("%s · %s" % (ext, ts("size_unknown"))), font_size=dp(12),
                      color=DIM, halign=al)
        mid.add_widget(meta)
        btn = RoundButton(text=t("get"), size_hint=(None, None), size=(dp(78), dp(42)),
                          pos_hint={"center_y": .5}, bg=list(ACC1) + [1], radius=dp(21))
        btn.bind(on_release=lambda *_: self.start_download(it))
        for w in ((btn, mid, box) if self.rtl else (box, mid, btn)):
            row.add_widget(w)

        # صورة الفيديو من الصفحة (poster) لو موجودة
        poster = str(it.get("thumb") or "")
        if poster.startswith("//"):
            poster = "https:" + poster
        if poster.startswith(("http://", "https://")) and poster != vurl:
            self._load_job_thumb(img, poster, it.get("ref"))

        def apply(r):
            if not r:
                return
            parts = [ext]
            if r.get("w") and r.get("h"):
                parts.append("%dx%d" % (r["w"], r["h"]))
            parts.append(human_size(r["size"]) if r.get("size") else ts("size_unknown"))
            if r.get("dur"):
                parts.append(fmt_dur(r["dur"]))
            meta.text = fix(" · ".join(parts))
            if r.get("size"):
                it["size"] = r["size"]
            if r.get("h"):
                it["h"] = r["h"]
            if r.get("thumb"):
                it["thumb_path"] = r["thumb"]
                if img.texture is None:      # مفيش poster: خد لقطة من الفيديو نفسه
                    try:
                        with open(r["thumb"], "rb") as f:
                            self._apply_job_thumb(img, f.read(), r["thumb"])
                    except Exception:
                        pass

        cache = self.__dict__.setdefault("_probe_cache", {})
        if vurl in cache:
            apply(cache[vurl])
        else:
            def work():
                try:
                    r = probe_video_url(vurl, it.get("ref"), it.get("cookie"))
                except Exception:
                    r = None
                cache[vurl] = r
                if r:
                    self.ui(apply, r)
            _PROBE_POOL.submit(work)
        return row

    def make_row(self, it):
        if it.get("vurl"):
            return self._make_direct_video_row(it)
        al = "right" if self.rtl else "left"
        row = Card(orientation="horizontal", size_hint_y=None, height=dp(76),
                   padding=[dp(12), dp(10)], spacing=dp(12), bg=CARD, radius=dp(20), elev=1)
        badge = Card(size_hint=(None, None), size=(dp(76), dp(46)),
                     pos_hint={"center_y": .5}, radius=dp(12),
                     bg=badge_color("m", it["h"] or 720) if it.get("vurl") else badge_color(it["kind"], it["h"]))
        lab = it["label"]
        badge.add_widget(Label(text=lab, bold=True,
                               font_size=dp(15) if len(lab) <= 6 else dp(12)))

        kind_txt = ts("k_dv") if it.get("vurl") else ts("k_" + it["kind"])
        line1 = ("%s · %s" % (kind_txt, it["ext"])) if self.rtl else ("%s · %s" % (it["ext"], kind_txt))
        parts = [human_size(it["size"]) if it["size"] else ts("size_unknown")]
        if it["codec"]:
            parts.append(it["codec"])
        if it["fps"] and it["fps"] > 30:
            parts.append("%dfps" % it["fps"])
        mid = BoxLayout(orientation="vertical")
        mid.add_widget(TLabel(text=fix(line1), font_size=dp(14), bold=True, halign=al))
        mid.add_widget(TLabel(text=fix(" · ".join(parts)), font_size=dp(12), color=DIM, halign=al))

        btn = RoundButton(text=t("get"), size_hint=(None, None), size=(dp(78), dp(42)),
                          pos_hint={"center_y": .5}, bg=list(ACC1) + [1], radius=dp(21))
        btn.bind(on_release=lambda *_: self.start_download(it))

        for w in ((btn, mid, badge) if self.rtl else (badge, mid, btn)):
            row.add_widget(w)
        return row

    # ---------------- playlist
    def _pl_norm(self, info):
        """بيجهّز عناصر البلاي ليست (بيشيل الفيديوهات الخاصة/المحذوفة)."""
        out = []
        for n, e in enumerate(list(info.get("entries") or []), 1):
            if not isinstance(e, dict):
                continue
            u = str(e.get("webpage_url") or e.get("url") or "")
            if not u.startswith("http"):
                key = str(e.get("ie_key") or info.get("extractor_key") or "").lower()
                if e.get("id") and ("youtube" in key or not key):
                    u = "https://www.youtube.com/watch?v=%s" % e["id"]
                else:
                    continue
            title = e.get("title") or "Untitled"
            if title in ("[Private video]", "[Deleted video]"):
                continue
            out.append(dict(
                n=n, title=title, url=u,
                dur=e.get("duration") or 0, sel=True,
                thumb=entry_thumb(e), id=e.get("id") or "",
            ))
        return out

    def _on_playlist(self, info, entries, images=None):
        if not entries:
            self.set_status("pl_empty", RED)
            SOUND.play("error")
            return
        self.pl_mode = True
        self.img_mode = False
        self.image_items = list(images or [])
        self.tab_images.opacity = 1 if self.image_items else 0
        self.tab_images.disabled = not bool(self.image_items)
        self.pl_entries = entries
        self.info_data = {"title": info.get("title") or "Playlist", "meta": "", "pl_n": len(entries)}
        self._load_thumb(pick_thumb(info))
        self.info_card.height = dp(76)
        self.has_info = True
        self._dyn_add(self.info_card)
        self._dyn_add(self.tabs)
        self._dyn_add(self.pl_ctrl)
        self.render_info()
        self.set_tab("video")
        self.set_status("choose", TXT, ("saves", self.download_dir))
        self._open_detail_page(self.info_data.get("title"))

    def _pl_opts(self):
        """اختيارات الجودة حسب التبويب: [(قيمة, نص)]."""
        if self.tab == "video":
            return [(0, ts("pl_best")), (1080, "1080p"), (720, "720p"), (480, "480p"), (360, "360p")]
        if HAS_FFMPEG or HAS_AV:
            return [(320, "320k"), (192, "192k"), (128, "128k")]
        return [(0, ts("pl_best"))]

    def _pl_cur_q(self):
        keys = [k for k, _ in self._pl_opts()]
        cur = self.pl_vq if self.tab == "video" else self.pl_aq
        return cur if cur in keys else keys[0]

    def _pl_render(self):
        self._pl_ctrl_build()
        self._pl_list_build()

    def _pl_ctrl_build(self):
        c = self.pl_ctrl
        c.clear_widgets()
        cur = self._pl_cur_q()
        row = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(6))
        for k, lab in self._pl_opts():
            b = RoundButton(text=fix(lab), font_size=dp(13), radius=dp(19),
                            bg=list(ACC1) + [1] if k == cur else CARD)
            b.bind(on_release=lambda w, k=k: self._pl_set_q(k))
            row.add_widget(b)
        c.add_widget(row)
        row2 = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        self.pl_sel = RoundButton(font_size=dp(13), bg=CARD2, size_hint_x=.42)
        self.pl_sel.bind(on_release=self._pl_toggle_all)
        self.pl_go = RoundButton()
        self.pl_go.bind(on_release=self._pl_download)
        for w in ((self.pl_go, self.pl_sel) if self.rtl else (self.pl_sel, self.pl_go)):
            row2.add_widget(w)
        c.add_widget(row2)
        self.pl_est_lbl = TLabel(
            text="", font_size=dp(11), color=DIM,
            size_hint_y=None, height=dp(24), halign="center", valign="middle",
            shorten=False)
        c.add_widget(self.pl_est_lbl)
        self._pl_ctrl_update()

    def _pl_est_size(self):
        """تقدير الحجم الإجمالي للفيديوهات المحددة بالجودة الحالية."""
        sel = [e for e in self.pl_entries if e.get("sel")]
        if not sel:
            return None
        q = self._pl_cur_q()
        total = 0
        known = 0
        for e in sel:
            dur = e.get("dur") or 0
            if not dur:
                continue
            known += 1
            if self.tab == "audio":
                kbps = int(q) if q else 192
                b = estimate_video_bytes(dur, audio_kbps=kbps)
            else:
                b = estimate_video_bytes(dur, height=int(q) if q else 720)
            if b:
                total += b
        if not known:
            return None
        return total

    def _pl_ctrl_update(self):
        n = sum(1 for e in self.pl_entries if e["sel"])
        if self.pl_sel is not None:
            self.pl_sel.text = t("sel_none") if n == len(self.pl_entries) else t("sel_all")
        if self.pl_go is not None:
            self.pl_go.text = t("dl_sel", n)
            self.pl_go.bg = list(ACC1) + [1] if n else CARD2
        if self.pl_est_lbl is not None:
            est = self._pl_est_size() if n else None
            if est:
                msg = "الحجم: %s" % human_size(est) if self.rtl else "Size: %s" % human_size(est)
            else:
                msg = "الحجم: غير معروف" if self.rtl else "Size: unknown"
            self.pl_est_lbl.text = fix(msg)

    def _pl_set_q(self, k):
        if self.tab == "video":
            self.pl_vq = k
        else:
            self.pl_aq = k
        self._pl_ctrl_build()

    def _pl_list_build(self):
        """ارسم دفعة صغيرة فقط؛ إنشاء 99+ بطاقة وصورة مرة واحدة كان يجمّد الواجهة."""
        lb = self.list_box
        lb.clear_widgets()
        self.pl_checks = {}
        self._pl_rendered_count = 0
        self._pl_more_button = None
        if self.tab == "audio" and not (HAS_FFMPEG or HAS_AV):
            lb.add_widget(TLabel(
                text=t("mp3_hint"), halign="center", valign="middle", shorten=False,
                color=DIM, font_size=dp(12), size_hint_y=None, height=dp(52)))
        self._pl_add_more_rows(12)

    def _pl_add_more_rows(self, batch=12, *_):
        """إضافة 12 فيديو في المرة بدل بناء القائمة كلها على الـ UI thread."""
        lb = self.list_box
        old_button = getattr(self, "_pl_more_button", None)
        if old_button is not None and old_button.parent is lb:
            lb.remove_widget(old_button)
        self._pl_more_button = None
        entries = getattr(self, "pl_entries", [])
        stop = min(len(entries), getattr(self, "_pl_rendered_count", 0) + batch)
        al = "right" if self.rtl else "left"
        for e in entries[self._pl_rendered_count:stop]:
            row = PickRow(orientation="vertical", size_hint_y=None, height=dp(190),
                          padding=dp(8), spacing=dp(5), bg=CARD)
            thumb = KImage(size_hint=(1, None), height=dp(128),
                           allow_stretch=True, keep_ratio=True, opacity=0.35)
            e["_thumb_widget"] = thumb
            self._load_pl_thumb(e, thumb)

            details = BoxLayout(orientation="horizontal", size_hint_y=None,
                                height=dp(42), spacing=dp(8))
            cm = CheckMark(size_hint=(None, None), size=(dp(26), dp(26)),
                           pos_hint={"center_y": .5}, on=e["sel"])
            self.pl_checks[e["n"]] = cm
            arabic = bool(AR_RE.search(e["title"]))
            mid = BoxLayout(orientation="vertical", spacing=dp(1))
            tl = TLabel(text=fix(e["title"]), font_size=dp(13), bold=True,
                        halign="right" if (self.rtl or arabic) else "left",
                        valign="middle", shorten=True)
            if arabic:
                tl.shorten_from = "left"
            meta = "#%d" % e["n"] + ((" · " + fmt_dur(e["dur"])) if e["dur"] else "")
            mid.add_widget(tl)
            mid.add_widget(TLabel(text=fix(meta), font_size=dp(11), color=DIM, halign=al,
                                  size_hint_y=None, height=dp(16)))
            if self.rtl:
                details.add_widget(cm); details.add_widget(mid)
            else:
                details.add_widget(mid); details.add_widget(cm)
            row.add_widget(thumb); row.add_widget(details)
            row.bind(on_release=lambda w, e=e, cm=cm: self._pl_tap(e, cm))
            lb.add_widget(row)
        self._pl_rendered_count = stop

        if stop < len(entries):
            more = RoundButton(text=fix("عرض المزيد (%d متبقي)" % (len(entries)-stop)),
                               size_hint_y=None, height=dp(46), bg=CARD2, radius=dp(18))
            more.bind(on_release=lambda *_: self._pl_add_more_rows(12))
            lb.add_widget(more)
            self._pl_more_button = more

    def _load_pl_thumb(self, entry, widget):
        """تحميل صورة عنصر البلاي ليست مع استخراج ID من كل أشكال روابط يوتيوب."""
        import urllib.request
        raw_url = str(entry.get("url") or "")
        vid = str(entry.get("id") or "").strip()
        # بعض extractors بيرجعوا url فيه الـ ID بدل id منفصل.
        if not vid or not re.match(r"^[A-Za-z0-9_-]{6,}$", vid):
            m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{6,})", raw_url)
            if m:
                vid = m.group(1)
        url = str(entry.get("thumb") or entry_thumb(entry) or "").strip()
        if url:
            # YouTube thumbnail URLs may append optimization query parameters that cause
            # a WEBP response even when the path ends in .jpg. Strip them and request JPEG.
            from urllib.parse import urlsplit, urlunsplit
            parts = urlsplit(url)
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
            entry["thumb"] = url

        def work():
            candidates = []
            if url.startswith("http"):
                candidates.append(url)
            if vid and re.match(r"^[A-Za-z0-9_-]{6,}$", vid):
                # جرّب الدومينين؛ بعض الشبكات/النسخ بتتعامل مع واحد منهم بشكل أفضل.
                for host in ("https://i.ytimg.com", "https://img.youtube.com"):
                    for size in ("maxresdefault", "sddefault", "hqdefault", "mqdefault", "default"):
                        candidates.append("%s/vi/%s/%s.jpg" % (host, vid, size))
            candidates = list(dict.fromkeys(candidates))
            # جرب رابط الصورة الأصلي ثم hqdefault فقط؛ المحاولات الكثيرة كانت تبطّئ البلاي ليست.
            candidates = candidates[:1] + [c for c in candidates[1:] if "/hqdefault.jpg" in c][:1]
            for candidate in candidates:
                try:
                    req = urllib.request.Request(candidate, headers={
                        "User-Agent": "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
                        "Referer": "https://www.youtube.com/",
                        "Accept": "image/jpeg,image/png,image/*;q=0.8,*/*;q=0.5",
                    })
                    with urllib.request.urlopen(req, timeout=5) as response:
                        data = response.read()
                        ctype = str(response.headers.get("Content-Type", "")).lower()
                    if data and len(data) > 1500 and ("image" in ctype or data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"):
                        self.ui(self._apply_pl_thumb, entry, widget, data, candidate)
                        return
                except Exception as exc:
                    if DEBUG_LOG and candidate == candidates[-1]:
                        print("NovaGet thumbnail: final request failed:", repr(exc), "URL:", candidate)
                    continue
            # سجّل معلومات مفيدة بدل ما نفشل بصمت.
            if DEBUG_LOG:
                print("NovaGet thumbnail: no usable image for playlist item", entry.get("n"), "video_id=", repr(vid), "source_url=", repr(raw_url), "thumb=", repr(url))
        # أربع صور فقط تُحمّل بالتوازي؛ لا ننشئ عشرات الخيوط دفعة واحدة.
        _THUMB_POOL.submit(work)

    def _apply_pl_thumb(self, entry, widget, data, url):
        if entry not in self.pl_entries or entry.get("_thumb_widget") is not widget:
            return
        import io
        from kivy.core.image import Image as CoreImage
        tex = None
        # Pillow يحوّل الصور لأي صيغة مدعومة عند وجوده؛ وإلا جرّب Kivy مباشرة.
        try:
            from PIL import Image as PILImage
            im = PILImage.open(io.BytesIO(data)).convert("RGBA")
            im.thumbnail((320, 180))
            raw = im.tobytes()
            tex = Texture.create(size=im.size, colorfmt="rgba")
            tex.blit_buffer(raw, colorfmt="rgba", bufferfmt="ubyte")
            tex.flip_vertical()
        except Exception:
            for ext in ("jpg", "png"):
                try:
                    tex = CoreImage(io.BytesIO(data), ext=ext).texture
                    if tex is not None:
                        break
                except Exception:
                    tex = None
        if tex is not None:
            widget.texture = tex
            widget.opacity = 1
            widget.canvas.ask_update()
        elif DEBUG_LOG:
            print("NovaGet thumbnail: image decode failed for", url, "bytes=", len(data), "header=", repr(data[:24]))

    def _pl_tap(self, e, cm):
        e["sel"] = not e["sel"]
        cm.on = e["sel"]
        self._pl_ctrl_update()

    def _pl_toggle_all(self, *_):
        v = not all(e["sel"] for e in self.pl_entries)
        for e in self.pl_entries:
            e["sel"] = v
            cm = self.pl_checks.get(e["n"])
            if cm is not None:
                cm.on = v
        self._pl_ctrl_update()

    def _pl_download(self, *_):
        sel = [e for e in self.pl_entries if e["sel"]]
        if not sel:
            self.set_status("pl_none", RED)
            return
        q = self._pl_cur_q()
        if self.tab == "video":
            it = dict(pl="v", pl_q=q, pl_key="v:%d" % q, kind="m", fid="pl", h=q, tbr=0,
                      label="%dp" % q if q else ts("pl_best"), ext="MP4",
                      size=None, codec="", fps=0)
        else:
            mp3 = bool(HAS_FFMPEG or HAS_AV)
            it = dict(pl="a", pl_q=q, pl_key="a:%d" % q, kind="a", fid="pl", h=0, tbr=q,
                      label="%dk" % q if q else ts("pl_best"), ext="MP3" if mp3 else "M4A",
                      size=None, codec="", fps=0)
        added = skipped = 0
        for e in sel:
            it["thumb"] = e.get("thumb") or e.get("thumbnail") or entry_thumb(e)
            if self.enqueue(Job(e["url"], e["title"], it), quiet=True):
                added += 1
            else:
                skipped += 1
        SOUND.play("start" if added else "error")
        if skipped:
            self.set_status("queued", TXT, ("pl_added_skip", added, skipped))
        else:
            self.set_status("queued", TXT, ("pl_added", added))
        self._after_change()

    def _pl_resolve(self, job):
        """بيجيب جودات الفيديو ده بالذات ويختار الأقرب للجودة المطلوبة (كل فيديو جوداته مختلفة)."""
        it = job.it
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True,
                               "noprogress": True, "logger": QuietLogger(),
                               "retries": 3, "socket_timeout": 30}) as y:
            info = y.extract_info(job.url, download=False)
        if job.cancel:
            raise Cancelled()
        vids, auds = self._build_items(info)
        want = int(it.get("pl_q") or 0)
        if it.get("pl") == "a":
            if not auds:
                raise RuntimeError("No audio format found for this video")
            if any(x.get("mp3") for x in auds) and want:
                pick = min(auds, key=lambda x: abs(x["tbr"] - want))
            else:
                pick = auds[0]
        else:
            if not vids:
                raise RuntimeError("No video format found for this video")
            if want:
                ok = [x for x in vids if 0 < x["h"] <= want]
                if ok:
                    # اختار أعلى دقة مسموحة، لكن مش أعلى bitrate بلا داعي؛ ده يقلل الحجم
                    # ويقربه من خيارات 720p المعتادة في تطبيقات التنزيل الأخرى.
                    best_h = max(x["h"] for x in ok)
                    same_h = [x for x in ok if x["h"] == best_h]
                    target_tbr = {1080: 4500, 720: 2200, 480: 1200, 360: 700}.get(want, 2200)
                    pick = min(same_h, key=lambda x: abs((x.get("tbr") or target_tbr) - target_tbr))
                else:
                    pick = vids[-1]
            else:
                pick = vids[0]
        keep = dict(pl=it.get("pl"), pl_q=it.get("pl_q"), pl_key=it.get("pl_key"), res=True)
        job.it = dict(pick)
        job.it.update(keep)

    # ---------------- images (TikTok carousel / multi-photo)
    def _img_render(self):
        self.img_mode = True
        # مساحة أكبر لقائمة الصور: صغّر الهيدر وكرت المعلومات وشريط التقدم
        try:
            self.hdr.height = dp(84)
            self.tagline.opacity = 0
            self.tagline.height = 0
            self.info_card.height = dp(64)
            self.prog_card.height = dp(72)
            self.root_box.spacing = dp(6)
        except Exception:
            pass
        if self.img_ctrl not in self.dyn.children:
            self._dyn_add(self.img_ctrl)
        self._img_ctrl_build()
        self._img_list_build()
        self._open_detail_page(self.info_data.get("title") or ("الصور" if self.rtl else "Images"))

    def _img_ctrl_build(self):
        c = self.img_ctrl
        c.clear_widgets()
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        self.img_sel = RoundButton(font_size=dp(13), bg=CARD2, size_hint_x=.42)
        self.img_sel.bind(on_release=self._img_toggle_all)
        self.img_go = RoundButton()
        self.img_go.bind(on_release=self._img_download)
        for w in ((self.img_go, self.img_sel) if self.rtl else (self.img_sel, self.img_go)):
            row.add_widget(w)
        c.add_widget(row)
        self._img_ctrl_update()

    def _img_ctrl_update(self):
        n = sum(1 for e in self.image_items if e.get("sel"))
        if self.img_sel is not None:
            self.img_sel.text = t("sel_none") if n == len(self.image_items) and n else t("sel_all")
        if self.img_go is not None:
            self.img_go.text = t("dl_sel", n)
            self.img_go.bg = list(ACC1) + [1] if n else CARD2

    def _img_list_build(self):
        lb = self.list_box
        lb.clear_widgets()
        self.img_checks = {}
        self.img_previews = {}
        self.img_cells = {}
        if not self.image_items:
            lb.add_widget(TLabel(
                text=t("img_none"), halign="center", valign="middle", shorten=False,
                color=DIM, size_hint_y=None, height=dp(120)))
            return
        # شبكة صور: كل صف فيه صورتين عشان تشوف الشكل وتختار
        row_h = dp(150)
        i = 0
        n = len(self.image_items)
        while i < n:
            pair = BoxLayout(orientation="horizontal", size_hint_y=None,
                             height=row_h, spacing=dp(8))
            for j in range(2):
                if i + j >= n:
                    pair.add_widget(Widget())
                    continue
                idx = i + j
                e = self.image_items[idx]
                cell = PickRow(orientation="vertical", size_hint_x=0.5,
                               padding=dp(6), spacing=dp(4), bg=CARD)
                # معاينة الصورة
                preview = KImage(size_hint_y=1, allow_stretch=True, keep_ratio=True,
                                 opacity=0.35)
                self.img_previews[idx] = preview
                self.img_cells[idx] = cell
                # شريط سفلي: تحديد + تحميل
                bar = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(6))
                cm = CheckMark(size_hint=(None, None), size=(dp(24), dp(24)),
                               pos_hint={"center_y": .5}, on=e.get("sel", True))
                self.img_checks[idx] = cm
                btn = RoundButton(text=t("get"), font_size=dp(12),
                                  size_hint_y=None, height=dp(28),
                                  bg=list(ACC1) + [1])
                btn.bind(on_release=lambda *_e, it=e: self.start_download(dict(it)))
                bar.add_widget(cm)
                bar.add_widget(btn)
                cell.add_widget(preview)
                cell.add_widget(bar)
                cell.bind(on_release=lambda w, idx=idx, cm=cm: self._img_tap(idx, cm))
                pair.add_widget(cell)
                # حمّل المعاينة في الخلفية
                self._load_img_preview(idx, e.get("url") or "", e.get("ref"))
            lb.add_widget(pair)
            i += 2

    def _load_img_preview(self, idx, url, ref=None):
        if not url:
            return

        def work():
            try:
                r, first = _open_image(url, timeout=25, ref=ref)
                try:
                    data = first + r.read()
                finally:
                    r.close()
                self.ui(self._apply_img_preview, idx, data, url)
            except Exception as ex:
                self.ui(self._img_preview_fail, idx, url, str(ex)[:170])

        threading.Thread(target=work, daemon=True).start()

    def _img_preview_fail(self, idx, url, why):
        """بدل المربع الرمادي الصامت: اعرض سبب فشل المعاينة (التحميل ممكن يشتغل عادي)."""
        if DEBUG_LOG:
            print("[preview fail]", idx, why, url[:120])
        cell = getattr(self, "img_cells", {}).get(idx)
        preview = self.img_previews.get(idx)
        if cell is None or preview is None or preview.parent is not cell:
            return
        try:
            from urllib.parse import urlparse
            host = urlparse(url).netloc
        except Exception:
            host = ""
        lbl = TLabel(text=fix("تعذّر عرض المعاينة" if self.rtl else "Preview failed")
                     + "\n" + host + "\n" + why,
                     halign="center", valign="middle", shorten=False,
                     color=DIM, font_size=dp(10))
        lbl.bind(size=lambda w, sz: setattr(w, "text_size", sz))
        cell.remove_widget(preview)
        cell.add_widget(lbl, index=1)
        self.img_previews.pop(idx, None)

    def _apply_img_preview(self, idx, data, url):
        preview = self.img_previews.get(idx)
        if preview is None:
            return
        # تأكد إن العنصر لسه موجود في القائمة الحالية
        if idx >= len(self.image_items) or self.image_items[idx].get("url") != url:
            return
        import io
        from kivy.core.image import Image as CoreImage
        ext = _sniff_img_ext(data) or "jpg"
        tex = None
        why = ""
        try:
            from PIL import Image as PILImage
            buf = io.BytesIO()
            im = PILImage.open(io.BytesIO(data)).convert("RGB")
            im.thumbnail((480, 480))      # صغّر المعاينة عشان الذاكرة
            im.save(buf, "JPEG", quality=75)
            buf.seek(0)
            tex = CoreImage(buf, ext="jpg").texture
        except Exception as ex:
            why = "PIL: %s" % str(ex)[:40]
            try:
                tex = CoreImage(io.BytesIO(data), ext=ext).texture
            except Exception as ex2:
                why += " | Kivy(%s): %s" % (ext, str(ex2)[:30])
                tex = None
        if tex is not None:
            preview.texture = tex
            preview.opacity = 1
        else:
            self._img_preview_fail(idx, url, why or "decode error")

    def _img_tap(self, idx, cm):
        if idx < 0 or idx >= len(self.image_items):
            return
        e = self.image_items[idx]
        e["sel"] = not e.get("sel", True)
        cm.on = e["sel"]
        self._img_ctrl_update()

    def _img_toggle_all(self, *_):
        v = not all(e.get("sel", True) for e in self.image_items)
        for i, e in enumerate(self.image_items):
            e["sel"] = v
            cm = self.img_checks.get(i)
            if cm is not None:
                cm.on = v
        self._img_ctrl_update()

    def _img_download(self, *_):
        sel = [e for e in self.image_items if e.get("sel")]
        if not sel:
            self.set_status("pl_none", RED)
            return
        title_base = (self.info_data or {}).get("title") or "Image"
        added = skipped = 0
        for i, e in enumerate(sel, 1):
            it = dict(e)
            it["kind"] = "img"
            it["pl_key"] = "img:%s" % (e.get("vurl") or e.get("url") or i)
            it["batch"] = True   # صوت الاكتمال مرة واحدة بعد آخر صورة
            name = "%s_%s" % (title_base[:40], e.get("label") or i)
            job = Job(e.get("url") or self.url, name, it)
            if self.enqueue(job, quiet=True):
                added += 1
            else:
                skipped += 1
        if added:
            self.img_batch_left += added
            SOUND.play("start")
        else:
            SOUND.play("error")
        if skipped:
            self.set_status("queued", TXT, ("img_added_skip", added, skipped))
        else:
            self.set_status("queued", TXT, ("img_added", added))
        self._after_change()

    # ---------------- downloads manager (queue)
    def _running(self):
        return sum(1 for j in list(self.jobs) if j.state == "downloading")

    def start_download(self, it):
        if it.get("kind") == "img":
            title = (self.info_data or {}).get("title") or "Image"
            label = it.get("name") or it.get("label") or ""
            name = ("%s_%s" % (title[:40], label)).strip("_") or title
            self.enqueue(Job(it.get("url") or self.url, name, dict(it)))
            return
        if not self.url:
            return
        title = (self.info_data or {}).get("title") or "Untitled"
        self.enqueue(Job(self.url, title, it))

    def enqueue(self, job, quiet=False):
        will_run = False

        def sig(j):
            pk = j.it.get("pl_key")
            return (j.url, pk or j.it.get("fid"), None if pk else j.it.get("mp3"))
        with self.lock:
            dup = any(sig(j) == sig(job) for j in self.jobs)
            if not dup:
                will_run = net_allowed() and self._running() < cur_limit()
                self.jobs.append(job)
                self._pump_core()
        if dup:
            if not quiet:
                self.set_status("dup", RED)
            return False
        if quiet:
            return True
        SOUND.play("start" if will_run else "queue")
        self.set_status("added" if will_run else "queued", TXT, job.title)
        self._after_change()
        return True

    def _pump_core(self):
        """بيبدأ التحميلات المنتظرة بالترتيب لحد العدد المسموح (آمن من أي thread)."""
        if not net_allowed():
            return
        limit = cur_limit()
        with self.lock:
            for j in list(self.jobs):
                if self._running() >= limit:
                    break
                if j.state == "queued":
                    j.state = "downloading"
                    threading.Thread(target=self._dl_worker, args=(j,), daemon=True).start()

    def _pump(self):
        self._pump_core()
        self._after_change()

    def _save_active(self, force=False):
        """بيحفظ التحميلات الجارية/الموقوفة عشان لو التطبيق اتقفل (أو أندرويد قتله) تكمّل لوحدها."""
        try:
            with self.lock:
                snap = [dict(url=j.url, title=j.title, it=j.it, state=("paused" if j.state == "paused" else "queued"))
                        for j in self.jobs]
            txt = json.dumps(snap, ensure_ascii=False, default=str)
            if not force and txt == getattr(self, "_active_sig", None):
                return
            self._active_sig = txt
            if snap:
                _atomic_json_write(os.path.join(data_dir(), ACTIVE_JOBS_FILE), snap, "active jobs")
            else:
                try:
                    os.remove(os.path.join(data_dir(), ACTIVE_JOBS_FILE))
                except OSError:
                    pass
        except Exception as ex:
            nlog("save_active: %s" % ex)

    def _restore_active(self, *_):
        try:
            p = os.path.join(data_dir(), ACTIVE_JOBS_FILE)
            if not os.path.isfile(p):
                return
            with open(p, "r", encoding="utf-8") as f:
                snap = json.load(f)
            n = 0
            for e in snap if isinstance(snap, list) else []:
                if not isinstance(e, dict) or not e.get("url") or not isinstance(e.get("it"), dict):
                    continue
                job = Job(e["url"], e.get("title") or "Untitled", e["it"])
                if e.get("state") == "paused":
                    job.state = "paused"
                with self.lock:
                    self.jobs.append(job)
                n += 1
            if n:
                self._pump()
                self._toast(("تم استكمال %d تحميل" % n) if self.rtl else ("Resumed %d download(s)" % n), 3)
        except Exception as ex:
            nlog("restore_active: %s" % ex)

    def _after_change(self, *_):
        try:
            self.nav_items["downloads"].icon.badge = len(self.jobs)
        except Exception:
            pass
        self._save_active()
        if self.dl_view:
            self._dl_rebuild()

    def cancel_job(self, job, refresh=True):
        with self.lock:
            if job.state in ("queued", "paused"):
                if job in self.jobs:
                    self.jobs.remove(job)
                NOTIF.cancel_job(job.id)
                job.notified = False
            else:
                job.cancel = True
        if refresh:
            self.ui(self._after_change)

    def toggle_pause(self, job):
        with self.lock:
            if job.cancel:
                return
            if job.state == "paused":
                job.state = "queued"
                job.pause_req = False
                self._pump_core()
            elif job.state == "downloading":
                # Once a pause is requested, wait until the worker confirms it is
                # paused. A rapid second tap must not clear the request and cause
                # the next resume to restart from an inconsistent state.
                if not job.pause_req:
                    job.pause_req = True
            elif job.state == "queued":
                job.state = "paused"
        self.ui(self._after_change)

    # ---- أوامر جاية من أزرار الإشعار (ممكن تيجي من thread تاني)
    def _notif_cb(self, action, jid):
        try:
            jid = int(jid)
        except Exception:
            return
        job = None
        for j in list(self.jobs):
            if j.id == jid:
                job = j
                break
        if job is None:
            return
        if action == A_CANCEL:
            self.cancel_job(job)
        else:
            self.toggle_pause(job)

    @staticmethod
    def _stable_bytes(job, done, total):
        """يمنع الرقم من النزول والطلوع: المحمّل لا يقل أبدًا، والإجمالي لا يقل عن المحمّل وما بيتغيّرش لقطات صغيرة."""
        stage = tuple(getattr(job, "stage", (0.0, 99.0)))
        if getattr(job, "_sb_stage", None) != stage:
            job._sb_stage, job._sb_done, job._sb_total = stage, 0, 0
        done = max(int(done or 0), job._sb_done)
        job._sb_done = done
        total = int(total or 0)
        if total:
            old = job._sb_total
            # قبول الإجمالي الجديد لو أول مرة، أو لو فرق كبير (>8%)، وإلا نثبّت القديم
            if not old or abs(total - old) > 0.08 * old:
                job._sb_total = total
        job._sb_total = max(job._sb_total, done)
        return done, (job._sb_total or None)

    @staticmethod
    def _smooth_eta(job, raw):
        """الوقت المتبقي اللي بيجي من yt-dlp بيقفز مع كل تغيّر لحظي في السرعة: نعمّمه."""
        if raw is None:
            return int(job._eta_s) if job._eta_s is not None else None
        now = time.time()
        try:
            raw = float(raw)
        except Exception:
            return int(job._eta_s) if job._eta_s is not None else None
        if raw != raw or raw < 0:
            return int(job._eta_s) if job._eta_s is not None else None
        raw = min(raw, 6 * 3600.0)        # أرقام خيالية (آلاف الثواني) بتظهر لما التقدم يبدأ ببطء: نحدّها
        # لو عارفين الحجم والسرعة: الوقت الحقيقي = المتبقي ÷ السرعة (أدق من تقدير الأجزاء)
        try:
            if job.speed and job.total_b and job.total_b > job.done_b:
                raw = min(raw, (job.total_b - job.done_b) / float(job.speed) * 1.3 + 2)
        except Exception:
            pass
        if job._eta_s is None:
            job._eta_s = raw
            job._eta_t = now
            return int(raw)
        dt = now - job._eta_t
        pred = max(0.0, job._eta_s - dt)           # الوقت بيقل لوحده مع الساعة
        if dt < 1.0:                               # متغيّرش أكتر من مرة في الثانية
            return int(pred)
        alpha = 1.0 - math.exp(-dt / 20.0)         # تنعيم بطيء (حوالي 20 ثانية)
        job._eta_s = pred + alpha * (raw - pred)
        job._eta_t = now
        return int(job._eta_s)

    def _hook(self, job, d):
        if job.cancel:
            raise Cancelled()
        if job.pause_req:
            raise Paused()
        if not net_allowed():          # اتحوّلنا لبيانات الهاتف وهي ممنوعة: وقّف وانتظر الواي فاي
            job.net_wait = True
            raise Paused()
        st = d.get("status")
        if st == "downloading":
            fc = d.get("fragment_count") or 0
            fi = d.get("fragment_index") or 0
            if fc and fc > 1:
                # بث مقسّم لأجزاء: الإجمالي بالبايت بيتخمّن وبيتغير طول الوقت، فنعتمد على عدد الأجزاء
                done = d.get("downloaded_bytes", 0) or 0
                with job._lock:
                    lo, hi = getattr(job, "stage", (0.0, 99.0))
                    frac = max(0.0, min(1.0, float(fi) / float(fc)))
                    job.progress = max(job.progress, min(hi, lo + (hi - lo) * frac))
                    job.frag_i, job.frag_n = int(fi), int(fc)
                    # تقدير الحجم الكلي من حجم الأجزاء اللي نزلت لحد دلوقتي (بيتثبّت مع الوقت)
                    est = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    if not est and frac >= 0.02:
                        est = done / frac
                    if est:
                        prev = getattr(job, "_tot_est", 0) or 0
                        est = est if not prev else (0.85 * prev + 0.15 * est)
                        job._tot_est = est
                    job.done_b, job.total_b = self._stable_bytes(
                        job, done, max(int(getattr(job, "_tot_est", 0) or 0), done))
                    job.speed = d.get("speed") or 0
                    job.last_hook = time.time()
                    now_t = time.time()
                    if job._f0 is None:
                        job._f0 = (now_t, frac)
                    raw = d.get("eta")
                    t0, f0 = job._f0
                    if now_t - t0 >= 4 and frac - f0 >= 0.01:
                        # متوسط السرعة من أول التحميل أثبت من السرعة اللحظية
                        raw = (1.0 - frac) * (now_t - t0) / (frac - f0)
                    job.eta = self._smooth_eta(job, raw)
                    job.phase = "dl"
                return
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes", 0) or 0
            with job._lock:
                if total:
                    lo, hi = getattr(job, "stage", (0.0, 99.0))
                    job.progress = max(job.progress, min(hi, lo + (hi - lo) * done / float(total)))
                job.done_b, job.total_b = self._stable_bytes(job, done, total)
                job.speed = d.get("speed") or 0
                job.last_hook = time.time()
                job.eta = self._smooth_eta(job, d.get("eta"))
                job.phase = "dl"
            try:   # تغيير السرعة من الإعدادات يسري على التحميل الجاري
                y = job.ydl
                if y is not None:
                    y.params["ratelimit"] = rate_limit()
            except Exception:
                pass
        elif st == "finished":
            with job._lock:
                job.last_hook = 0.0
                job.frag_i = job.frag_n = 0
                job._eta_s, job._f0 = None, None
                job._tot_est = 0
                job.phase = "proc"

    def _ydl_opts(self, job, fmt, outtmpl):
        opts = {
            "format": fmt, "outtmpl": outtmpl,
            "progress_hooks": [lambda d, j=job: self._hook(j, d)],
            "noplaylist": True, "quiet": True, "no_warnings": True,
            "noprogress": True, "logger": QuietLogger(), "consoletitle": False,
            "retries": 5,
            "fragment_retries": 5,
            "socket_timeout": 30,
            "file_access_retries": 3,
            "concurrent_fragment_downloads": frags_n(),   # عدد الأجزاء المتحمّلة مع بعض (من الإعدادات)
            "http_chunk_size": 10485760,
            "continuedl": True,   # استكمال التحميل بعد الإيقاف المؤقت
            "nopart": False,
        }
        rate = rate_limit()
        if rate:
            opts["ratelimit"] = rate
        if "instagram.com" in (getattr(job, "url", "") or "").lower() and ig_cookiefile():
            opts["cookiefile"] = ig_cookiefile()
        if re.search(r"facebook\.com|fb\.watch", getattr(job, "url", "") or "", re.I) and fb_cookiefile():
            opts["cookiefile"] = fb_cookiefile()
        return opts

    def _download_mux(self, job):
        """فيديو MP4 + صوت M4A بيتحمّلوا لوحدهم وبيتدمجوا بكود بايثون (من غير ffmpeg)."""
        d = sub_for_job(self.download_dir, job.it)
        job.phase = "dl"
        job.stage = (0.0, 88.0)
        with yt_dlp.YoutubeDL(self._ydl_opts(
                job, job.it["fid"], os.path.join(d, "%(title).150s.nvg_v.%(ext)s"))) as y:
            job.ydl = y
            info = y.extract_info(job.url, download=True)
            vpath = result_path(y, info)
        # تيك توك وأشباهه: مفيش ملف صوت منفصل، والصوت جوّه ملف الفيديو نفسه → مفيش دمج
        _has_aud = any((f.get("vcodec") in (None, "none")) and (f.get("acodec") not in (None, "none"))
                       and f.get("ext") == "m4a" for f in (info.get("formats") or []))
        if not _has_aud:
            out = os.path.splitext(vpath.replace(".nvg_v.", "."))[0] + ".mp4"
            os.replace(vpath, out)
            job.progress = max(job.progress, 99.0)
            job.path = out
            return
        job.stage = (88.0, 98.0)
        with yt_dlp.YoutubeDL(self._ydl_opts(
                job, "bestaudio[ext=m4a]", os.path.join(d, "%(title).150s.nvg_a.%(ext)s"))) as y:
            job.ydl = y
            info2 = y.extract_info(job.url, download=True)
            apath = result_path(y, info2)
        job.phase = "proc"
        job.progress = max(job.progress, 98.0)
        out = os.path.splitext(vpath.replace(".nvg_v.", "."))[0] + ".mp4"

        def check(done, total):
            if job.cancel:
                raise Cancelled()
        try:
            mux_to_mp4(vpath, apath, out, duration=info.get("duration"), check=check)
        except Exception as e:
            try:
                os.remove(out)
            except Exception:
                pass
            if isinstance(e, Cancelled):
                raise
            raise RuntimeError("Can't merge this format (%s)" % e)
        for x in (vpath, apath):
            try:
                os.remove(x)
            except Exception:
                pass
        job.path = out

    def _download_video_ffmpeg(self, job):
        """Download compatible video/audio streams and mux with this older FFmpeg.

        Falls back to pure-Python fragmented mux if FFmpeg merge fails.
        """
        d = sub_for_job(self.download_dir, job.it)
        it = job.it
        job.phase = "dl"
        job.stage = (0.0, 96.0)
        h = int(it.get("h") or 0)
        cap = ("[height<=%d]" % h) if h > 0 else ""

        # اختار ملف فيديو واحد فيه الصورة والصوت معًا قدر الإمكان.
        # ده يمنع ترك ملف صوت منفصل وملف فيديو خام في مدير الملفات.
        # نفضّل MP4، ولو الموقع لا يوفّر MP4 مدمجًا نرجع لأفضل صيغة مدمجة متاحة.
        limit = h if h > 0 else 99999
        fmt = (
            "best[height<=%d][ext=mp4]/"
            "best[height<=%d]/"
            "best[ext=mp4]/best"
        ) % (limit, limit)

        template = os.path.join(d, "%(title).140s.%(ext)s")

        class VideoDiagnosticLogger(object):
            def __init__(self):
                self.messages = []

            def debug(self, msg):
                self.messages.append("DEBUG: " + str(msg))

            def info(self, msg):
                self.messages.append("INFO: " + str(msg))

            def warning(self, msg):
                self.messages.append("WARNING: " + str(msg))

            def error(self, msg):
                self.messages.append("ERROR: " + str(msg))

        diag = VideoDiagnosticLogger()
        opts = self._ydl_opts(job, fmt, template)
        opts["logger"] = diag
        opts["verbose"] = True
        if HAS_FFMPEG:
            opts["ffmpeg_location"] = FFMPEG
            opts["merge_output_format"] = "mp4"
            opts["postprocessor_args"] = {"merger": ["-movflags", "+faststart"]}

        try:
            # HTTP 416 after resuming means the server rejected the byte range in
            # the stale .part file (often because a social site's media URL changed).
            # Retry once with a fresh extractor request and a clean full download.
            for attempt in range(2):
                run_opts = dict(opts)
                if attempt == 1:
                    job._sb_stage = None          # العدّاد هيبدأ من الصفر تاني
                    run_opts["continuedl"] = False
                    run_opts["overwrites"] = True
                try:
                    with yt_dlp.YoutubeDL(run_opts) as y:
                        job.ydl = y
                        info = y.extract_info(job.url, download=True)
                        path = result_path(y, info)
                    break
                except Exception as retry_exc:
                    retry_text = str(retry_exc).lower()
                    if attempt == 0 and ("416" in retry_text or "requested range not satisfiable" in retry_text):
                        # Refresh signed media URLs on the next extraction and do not
                        # append to the incompatible partial file.
                        continue
                    raise
            if not path or not os.path.isfile(path) or os.path.getsize(path) <= 0:
                raise RuntimeError("yt-dlp reported success but the output file is missing or empty")
            job.path = path
            return
        except Cancelled:
            raise
        except Paused:
            raise
        except Exception as e:
            err_l = str(e).lower()
            # If FFmpeg merge/postprocess failed, try pure-Python mux of separate streams
            merge_fail = any(k in err_l for k in (
                "ffmpeg", "merge", "postprocess", "invalid data", "error while",
                "conversion failed", "muxing"))
            if not merge_fail or job.cancel or job.pause_req:
                clean = lambda x: re.sub(r"\x1b\[[0-9;]*m", "", str(x))
                parts = ["YT-DLP/FFmpeg error:", clean(e)]
                cause = getattr(e, "__cause__", None)
                context = getattr(e, "__context__", None)
                if cause and str(cause) and str(cause) != str(e):
                    parts.append("CAUSE: " + clean(cause))
                if context and str(context) and str(context) != str(e) and context is not cause:
                    parts.append("CONTEXT: " + clean(context))
                useful = [m for m in diag.messages if any(
                    k in m.lower() for k in ("error", "ffmpeg", "postprocess", "stream #", "invalid", "failed"))]
                if useful:
                    parts.append("yt-dlp diagnostic log:\n" + "\n".join(useful[-100:]))
                detail = "\n\n".join(parts)
                try:
                    with open(os.path.join(self.download_dir, "novaget_error.txt"), "w", encoding="utf-8") as f:
                        f.write(detail)
                except Exception:
                    pass
                raise RuntimeError(detail)

        # Fallback: download video + audio separately and mux without FFmpeg
        job.stage = (0.0, 88.0)
        v_fmt = ("bestvideo[vcodec^=avc1]%s[ext=mp4]/bestvideo[height<=%d][ext=mp4]/bestvideo[ext=mp4]"
                 % (cap, h if h > 0 else 99999))
        try:
            with yt_dlp.YoutubeDL(self._ydl_opts(
                    job, v_fmt, os.path.join(d, "%(title).150s.nvg_v.%(ext)s"))) as y:
                job.ydl = y
                info = y.extract_info(job.url, download=True)
                vpath = result_path(y, info)
            job.stage = (88.0, 98.0)
            with yt_dlp.YoutubeDL(self._ydl_opts(
                    job, "bestaudio[ext=m4a]/bestaudio", os.path.join(d, "%(title).150s.nvg_a.%(ext)s"))) as y:
                job.ydl = y
                info2 = y.extract_info(job.url, download=True)
                apath = result_path(y, info2)
            job.phase = "proc"
            job.progress = max(job.progress, 98.0)
            out = os.path.splitext(vpath.replace(".nvg_v.", "."))[0] + ".mp4"

            def check(done, total):
                if job.cancel:
                    raise Cancelled()
                if job.pause_req:
                    raise Paused()

            mux_to_mp4(vpath, apath, out, duration=info.get("duration"), check=check)
            for x in (vpath, apath):
                try:
                    os.remove(x)
                except Exception:
                    pass
            job.path = out
        except (Cancelled, Paused):
            raise
        except Exception as e2:
            raise RuntimeError("FFmpeg merge failed and pure-Python mux also failed: %s" % e2)
    def _download_mp3_av(self, job):
        """تحميل الصوت ثم تحويله لـ MP3 بـ PyAV (من غير ffmpeg)."""
        d = sub_for_job(self.download_dir, job.it)
        job.phase = "dl"
        job.stage = (0.0, 90.0)
        with yt_dlp.YoutubeDL(self._ydl_opts(
                job, job.it["fid"], os.path.join(d, "%(title).150s.nvg_s.%(ext)s"))) as y:
            job.ydl = y
            info = y.extract_info(job.url, download=True)
            spath = result_path(y, info)
        job.phase = "proc"
        job.progress = max(job.progress, 90.0)
        out = os.path.splitext(spath.replace(".nvg_s.", "."))[0] + ".mp3"
        dur = info.get("duration") or 0

        def check():
            if job.cancel:
                raise Cancelled()

        def prog(t):
            if dur:
                job.progress = max(job.progress, min(99.0, 90.0 + 9.0 * t / float(dur)))
        try:
            av_to_mp3(spath, out, job.it["mp3"], check=check, prog=prog, title=info.get("title"))
        except Exception as e:
            for x in (out, spath):
                try:
                    os.remove(x)
                except Exception:
                    pass
            if isinstance(e, Cancelled):
                raise
            raise RuntimeError("MP3 conversion failed (%s)" % str(e)[:80])
        try:
            os.remove(spath)
        except Exception:
            pass
        job.path = out

    def _download_mp3_ffmpeg(self, job):
        """Download the selected audio stream, then encode it directly to a real MP3.

        Avoid yt-dlp's chained postprocessors here: older Android FFmpeg builds may
        fail while embedding thumbnails or leave the source media behind. The
        downloaded source is only a temporary file and is removed after encoding.
        """
        import subprocess
        d = sub_for_job(self.download_dir, job.it)
        job.phase = "dl"
        job.stage = (0.0, 88.0)
        template = os.path.join(d, "%(title).150s.nvg_src.%(ext)s")
        with yt_dlp.YoutubeDL(self._ydl_opts(job, job.it["fid"], template)) as y:
            job.ydl = y
            info = y.extract_info(job.url, download=True)
            src = result_path(y, info)

        if not src or not os.path.isfile(src):
            raise RuntimeError("Audio downloaded, but the source file could not be found")

        job.phase = "proc"
        job.progress = max(job.progress, 89.0)
        base = os.path.basename(src)
        if ".nvg_src." in base:
            out = os.path.join(os.path.dirname(src), base.replace(".nvg_src.", ".", 1))
            out = os.path.splitext(out)[0] + ".mp3"
        else:
            out = os.path.splitext(src)[0] + ".mp3"

        cmd = [FFMPEG, "-y", "-i", src, "-vn", "-codec:a", "libmp3lame",
               "-b:a", "%dk" % int(job.it.get("mp3") or 192), "-f", "mp3", out]
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=3600)
            if result.returncode != 0 or not os.path.isfile(out) or os.path.getsize(out) <= 0:
                detail = result.stderr.decode("utf-8", "replace")[-700:]
                raise RuntimeError("FFmpeg MP3 conversion failed: " + detail)
        except Exception:
            try:
                if os.path.isfile(out):
                    os.remove(out)
            except Exception:
                pass
            raise
        finally:
            try:
                if os.path.isfile(src) and os.path.abspath(src) != os.path.abspath(out):
                    os.remove(src)
            except Exception:
                pass
        job.path = out

    def _download_image(self, job):
        """تحميل صورة واحدة (تيك توك / إنستجرام / ...)."""
        it = job.it
        vurl = it.get("vurl")
        url = vurl or it.get("url") or job.url
        if not url:
            raise RuntimeError("No image URL")
        job.phase = "dl"
        job.stage = (0.0, 99.0)
        # اسم ملف آمن
        base = re.sub(r"[^\w\u0600-\u06FF\-]+", "_", (job.title or "image")[:80]).strip("_") or "image"
        ext = (it.get("ext") or "jpg").lower()
        if ext == "jpeg":
            ext = "jpg"
        _od = sub_for_job(self.download_dir, it)
        dest = os.path.join(_od, "%s.%s" % (base, ext))
        n = 1
        while os.path.isfile(dest):
            dest = os.path.join(_od, "%s_%d.%s" % (base, n, ext))
            n += 1

        def check():
            if job.cancel:
                raise Cancelled()
            if job.pause_req:
                raise Paused()
            if not net_allowed():
                job.net_wait = True
                raise Paused()

        def prog(done, total):
            with job._lock:
                if total:
                    job.progress = max(job.progress, min(99.0, 99.0 * done / float(total)))
                job.done_b, job.total_b = done, total or done

        if vurl and re.search(r"\.(m3u8|mpd)(\?|#|$)", vurl, re.I):
            # بث HLS/DASH: ملف مش مباشر، yt-dlp هو اللي بيجمّع أجزاءه
            if yt_dlp is None:
                raise RuntimeError("yt-dlp is not installed. Install it with: pip install -U yt-dlp")
            import hashlib, shutil
            tmp = os.path.join(tempfile.gettempdir(), "novaget_tmp",
                               hashlib.md5(vurl.encode("utf-8")).hexdigest()[:16])
            os.makedirs(tmp, exist_ok=True)
            # الملفات المؤقتة والأجزاء بتتحط في مجلد خاص بالتطبيق، والملف النهائي بس بيتنقل للتنزيلات
            opts = self._ydl_opts(job, "best", "%s.%%(ext)s" % base)
            opts["paths"] = {"home": _od, "temp": tmp}
            opts["http_headers"] = {"User-Agent": _UA_MOBILE, "Referer": it.get("ref") or vurl}
            if it.get("cookie"):
                opts["http_headers"]["Cookie"] = it["cookie"]
            if it.get("ref"):
                from urllib.parse import urlparse as _up
                _p = _up(it["ref"])
                if _p.netloc:
                    opts["http_headers"]["Origin"] = "%s://%s" % (_p.scheme, _p.netloc)
            if HAS_FFMPEG:
                opts["ffmpeg_location"] = FFMPEG
                opts["merge_output_format"] = "mp4"
                opts["postprocessor_args"] = {"merger": ["-movflags", "+faststart"]}
            try:
                with yt_dlp.YoutubeDL(opts) as y:
                    job.ydl = y
                    info = y.extract_info(vurl, download=True)
                    dest = result_path(y, info)
            except BaseException as ex:
                # لو إيقاف مؤقت نسيب الأجزاء عشان الاستكمال، غير كده نمسحها
                if not (isinstance(ex, Paused) or job.pause_req or job.net_wait):
                    shutil.rmtree(tmp, ignore_errors=True)
                raise
            shutil.rmtree(tmp, ignore_errors=True)
        elif vurl:
            try:
                dest = download_direct_file_smart(vurl, dest, check=check, prog=prog, it=it)
            except (Cancelled, Paused):
                raise
            except Exception as ex:
                raise RuntimeError("%s\n\nURL: %s\nReferer: %s" % (
                    ex, vurl[:300], (it.get("ref") or "-")[:200]))
        else:
            dest = download_image_file(url, dest, check=check, prog=prog, ref=it.get("ref"))
        job.path = dest
        job.progress = 100.0

    def _download(self, job, thumb):
        it = job.it
        job.phase = "dl"
        if it.get("kind") == "img":
            return self._download_image(job)
        if yt_dlp is None:
            raise RuntimeError("yt-dlp is not installed. Install it with: pip install -U yt-dlp")
        fid = it["fid"]
        if it.get("mp3") and HAS_FFMPEG:
            return self._download_mp3_ffmpeg(job)
        if it.get("kind") == "m" and HAS_FFMPEG:
            return self._download_video_ffmpeg(job)
        if it.get("mp3") and not HAS_FFMPEG and HAS_AV:
            return self._download_mp3_av(job)
        if it.get("kind") == "mx" and not HAS_FFMPEG:
            return self._download_mux(job)
        _od = sub_for_job(self.download_dir, it)
        _hh = int(it.get("h") or 0)
        # لو رقم الصيغة اتغيّر (تيك توك بيغيّره كل مرة): جرّب أقرب جودة بالارتفاع، وبعدين أي صيغة
        _fb = "/best[height<=%d]/best" % _hh if _hh > 0 and it.get("kind") != "a" else \
              ("/bestaudio/best" if it.get("kind") == "a" else "/best")
        opts = self._ydl_opts(job, fid + _fb, os.path.join(_od, "%(title).150s.%(ext)s"))
        if HAS_FFMPEG:
            opts["ffmpeg_location"] = FFMPEG
        if it.get("kind") in ("m", "mx") and HAS_FFMPEG:
            opts["format"] = "%s+bestaudio[ext=m4a]/%s+bestaudio/%s%s" % (fid, fid, fid, _fb)
            opts["merge_output_format"] = "mp4"
            opts["postprocessor_args"] = {"merger": ["-movflags", "+faststart"]}
        pps = []
        if it.get("mp3") and HAS_FFMPEG:   # تحويل الصوت إلى MP3
            pps.append({"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                        "preferredquality": str(int(it["mp3"]))})
        use_pil = False
        if thumb:   # الصورة المصغرة تتحول jpg وتتدمج جوه الملف (تظهر في المعرض)
            opts["writethumbnail"] = True
            # ملف الصورة المؤقت اسمه فيه ".nvg_" عشان يتمسح تلقائي حتى لو حصل فشل
            opts["outtmpl"] = {"default": opts["outtmpl"],
                               "thumbnail": os.path.join(_od,
                                                         "%(title).100s.nvg_thumb.%(ext)s")}
            use_pil = _pil_ok()
            if use_pil:
                pps = pps + [{"key": "EmbedThumbnail"}]
            else:
                pps = [{"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"}] \
                    + pps + [{"key": "EmbedThumbnail"}]
        if pps:
            opts["postprocessors"] = pps
        with yt_dlp.YoutubeDL(opts) as y:
            if use_pil:
                y.add_post_processor(PilThumbConvertor(y), when="before_dl")
            job.ydl = y
            info = y.extract_info(job.url, download=True)
            job.path = result_path(y, info)

    def _dl_worker(self, job):
        ok, err, paused = False, "", False
        use_thumb = False
        try:
            if job.it.get("pl") and not job.it.get("res"):
                self._pl_resolve(job)       # بلاي ليست: اختار الصيغة المناسبة للفيديو ده
            use_thumb = can_embed_thumb(job.it)
            try:
                self._download(job, use_thumb)
            except Exception as e:
                m = str(e).lower()
                if (use_thumb and not job.cancel and not job.pause_req
                        and not isinstance(e, (Cancelled, Paused))
                        and any(k in m for k in ("thumb", "postprocess", "preprocess", "decoder",
                                                 "ffmpeg", "webp", "embed", "convert"))):
                    # Do not clean the whole shared Download directory here:
                    # other parallel jobs may own .part/.nvg_* files in it.
                    self._download(job, False)   # فشل دمج الصورة: كمّل من غيرها
                else:
                    raise
            try:
                def _chk(*_a):
                    if job.cancel:
                        raise Cancelled()
                job.phase = "proc"
                _need = None
                try:
                    _need = mp4_needs_fix(job.path) if job.path.lower().endswith((".mp4", ".m4v", ".mov")) else "not-mp4"
                except Exception:
                    pass
                ensure_seekable(job.path, check=_chk)
                _gap = mp4_keyframe_gap(job.path) if job.path.lower().endswith((".mp4", ".m4v", ".mov")) else None
                nlog("seek-check %s | needed_fix=%s | now_needs_fix=%s | max_keyframe_gap=%s | ffmpeg=%s av=%s" % (
                    os.path.basename(job.path or "")[:50], _need,
                    mp4_needs_fix(job.path) if job.path.lower().endswith((".mp4", ".m4v", ".mov")) else "-",
                    ("%.1fs" % _gap) if _gap is not None else "?", HAS_FFMPEG, HAS_AV))
                if _gap is not None and _gap > 8.0:
                    self.ui(self._toast, ("الفيديو ده فريمات مفتاحية متباعدة (%.0f ثانية) فالتقديم هيبطّئ في أي مشغّل" % _gap)
                            if self.rtl else ("This video has sparse keyframes (%.0fs gap): seeking will lag in any player" % _gap), 6)
            except Cancelled:
                raise
            except Exception as _ex:
                nlog("seek-check error: %s" % _ex)
            ok = True
            if job.it.get("kind") != "img":
                remove_thumb_leftovers(job.path)
            # Use a frame from the finished local video for the completed-list card.
            # This avoids black thumbnails caused by blocked/expired social CDN URLs.
            try:
                kind = str(job.it.get("kind") or "").lower()
                ext = os.path.splitext(job.path or "")[1].lower()
                if job.path and os.path.isfile(job.path) and (kind in ("m", "mx") or job.it.get("vurl")) and ext not in (".mp3", ".m4a", ".mp3"):
                    job.thumb_path = video_thumb(job.path) or ""
            except Exception as _thumb_err:
                print("NovaGet: local completed-video thumbnail failed:", str(_thumb_err)[:160])
            media_scan(job.path)
        except Exception as e:
            if job.cancel:
                pass
            elif isinstance(e, Paused) or job.pause_req or job.net_wait:
                paused = True
            else:
                err = user_facing_error(e)
        self._end_core(job, ok, err, paused)

    def _end_core(self, job, ok, err, paused):
        """بيتنفذ في thread التحميل نفسه (يشتغل حتى والتطبيق في الخلفية)."""
        with self.lock:
            job.ydl = None
            if paused and not ok and not job.cancel:
                job.state = "queued" if job.net_wait else "paused"
                job.net_wait = False
                job.pause_req = False
                job.phase = "dl"
                job.speed = 0
                job.eta = None
                outcome = "paused"
            else:
                if job in self.jobs:
                    self.jobs.remove(job)
                NOTIF.cancel_job(job.id)
                job.notified = False
                if job.cancel and not ok:
                    outcome = "cancelled"
                    # امسح الملفات الجزئية عند الإلغاء (بس لو مفيش تحميلات تانية شغالة/موقوفة)
                    if not self.jobs:
                        cleanup_temp_files(self.download_dir)
                else:
                    self.history.insert(0, job.entry(ok, err))
                    del self.history[HIST_MAX:]
                    save_history(self.history)
                    outcome = "done" if ok else "failed"
                    self._notify_done(job, ok, err)
                    # نظّف المؤقتات بعد النجاح/الفشل (مع الإبقاء على الملف النهائي)
                    if not self.jobs:
                        cleanup_temp_files(self.download_dir, keep_path=job.path if ok else None)
            self._pump_core()
        self.ui(self._after_end, job, outcome, err)

    def _notify_done(self, job, ok, err):
        if not (NOTIF.ok and CFG.get("notify", True)):
            return
        if ok:
            NOTIF.post_done(job.id, ts("done"), job.title, True)
        else:
            NOTIF.post_done(job.id, ts("failed"),
                            job.title + ((" - " + err) if err else ""), False)

    def show_error_details(self, err):
        """يعرض الخطأ كاملًا في نافذة قابلة للتمرير والنسخ."""
        try:
            mv = ModalView(size_hint=(0.94, 0.86), auto_dismiss=False,
                           background_color=(0, 0, 0, 0))
            card = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
            with card.canvas.before:
                Color(*CARD)
                bg = RoundedRectangle(pos=card.pos, size=card.size, radius=[dp(16)])
            card.bind(pos=lambda w, v: setattr(bg, "pos", v),
                      size=lambda w, v: setattr(bg, "size", v))
            card.add_widget(TLabel(text=t("err_title"), font_size=dp(18),
                                   size_hint_y=None, height=dp(34), halign="center"))
            details = TextInput(text=str(err or ts("err_unknown")), readonly=True,
                                multiline=True, size_hint=(1, 1), font_size=dp(13),
                                background_color=(0.07, 0.08, 0.11, 1),
                                foreground_color=(0.95, 0.95, 0.95, 1),
                                cursor_blink=False)
            card.add_widget(details)
            actions = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
            copy_btn = RoundButton(text=t("err_copy"), bg=CARD2)
            copy_btn.bind(on_release=lambda *_: Clipboard.copy(str(err or "")))
            close_btn = RoundButton(text=t("close"), bg=CARD2)
            close_btn.bind(on_release=lambda *_: mv.dismiss())
            actions.add_widget(copy_btn)
            actions.add_widget(close_btn)
            card.add_widget(actions)
            mv.add_widget(card)
            mv.open()
        except Exception:
            # لو النافذة لم تُفتح، احتفظ بالخطأ كاملًا في ملف يسهل إرساله.
            try:
                path = os.path.join(self.download_dir, "novaget_error.txt")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(str(err or ts("err_unknown")))
            except Exception:
                pass

    def _after_end(self, job, outcome, err):
        is_batch_img = (job.it.get("kind") == "img" and job.it.get("batch")
                        and self.img_batch_left > 0)
        if is_batch_img and outcome in ("done", "failed", "cancelled"):
            self.img_batch_left = max(0, self.img_batch_left - 1)

        if outcome == "paused":
            self.set_status("paused", TXT, job.title)
        elif outcome == "cancelled":
            self.set_status("cancelled", TXT, job.title)
            if not self.jobs:
                self.set_progress(0)
        elif outcome == "done":
            # صور الدفعة: صوت واحد بعد آخر صورة فقط
            if is_batch_img:
                if self.img_batch_left == 0:
                    SOUND.play("done")
                    self.set_status("done", GREEN, ("saved", self.download_dir))
                    self.set_progress(100)
                else:
                    left = self.img_batch_left
                    self.set_status("downloading", TXT,
                                   ("%d left" % left) if STATE["lang"] == "en"
                                   else ("متبقي %d" % left))
            else:
                SOUND.play("done")
                self.set_status("done", GREEN, ("saved", self.download_dir))
            if not self.jobs:
                self.set_progress(100)
        else:
            if not is_batch_img or self.img_batch_left == 0:
                SOUND.play("error")
            self.set_status("failed", RED, err)
            if err and not job.it.get("pl") and not is_batch_img:
                self.show_error_details(err)
        self._after_change()

    # ---- مزامنة إشعارات التقدم (thread مستقل عشان تشتغل والتطبيق في الخلفية)
    def _notif_loop(self):
        while True:
            time.sleep(1.0)
            try:
                self._notif_sync()
            except Exception:
                pass

    def _notif_texts(self, j):
        pct = int(j.progress)
        if j.cancel:
            s = ts("cancelling")
            return s, s, True
        if j.state == "paused":
            s = "%s · %d%%" % (ts("paused"), pct)
            return s, s, False
        if j.pause_req:
            s = ts("pausing")
            return s, s, False
        if j.phase == "proc":
            s = ts("processing")
            return s, s, True
        parts = ["%d%%" % pct]
        if j.eta:
            parts.append(ts("left_t", fmt_dur(j.eta)))
        if j.speed:
            parts.append(human_size(j.speed) + "/s")
        short = " · ".join(parts)
        if getattr(j, "frag_n", 0) and j.total_b:
            big = short + "\n%s / %s" % (human_size(j.done_b), human_size(j.total_b) if j.total_b else "…")
        else:
            big = short + "\n%s / %s" % (human_size(j.done_b), human_size(j.total_b))
        return short, big, False

    def _notif_sync(self, force=False):
        if not NOTIF.ok:
            return
        on = CFG.get("notify", True)
        with self.lock:
            now = time.time()
            for j in list(self.jobs):
                if not on or j.state == "queued":
                    if j.notified:
                        NOTIF.cancel_job(j.id)
                        j.notified = False
                        j.n_sig = None
                    continue
                if not force and now - j.n_ts < 1.0:
                    continue
                short, big, indet = self._notif_texts(j)
                paused = j.state == "paused"
                sig = (j.state, j.cancel, j.pause_req, j.phase, int(j.progress), short)
                if sig == j.n_sig:
                    continue
                NOTIF.show_progress(j.id, j.title, short, big, int(j.progress), indet, paused,
                                    ts("resume") if paused else ts("pause"), ts("cancel"))
                j.notified = True
                j.n_sig = sig
                j.n_ts = now

    def job_lines(self, job):
        it = job.it
        m1 = "%s · %s" % (it.get("label", ""), it.get("ext", ""))
        if job.progress and not job.cancel:
            m1 += " · %d%%" % job.progress
        if job.cancel:
            m2 = ts("cancelling")
        elif job.state == "paused":
            m2 = ts("paused")
        elif job.pause_req:
            m2 = ts("pausing")
        elif job.state == "queued":
            m2 = ts("waiting") if net_allowed() else ts("net_wait")
        elif job.phase == "proc":
            m2 = ts("processing")
        else:
            m2 = "%s / %s" % (human_size(job.done_b), human_size(job.total_b) if job.total_b else "…")
            if job.speed:
                m2 += "  ·  %s/s" % human_size(job.speed)
            if job.last_hook and time.time() - job.last_hook > 12:
                m2 += "  ·  " + ("الاتصال بطيء… بيحاول تاني" if self.rtl else "slow connection, retrying…")
        return m1, m2

    def _tick(self, dt):
        _hang_arm()
        try:
            KEEP.set(any(j.state == "downloading" for j in list(self.jobs)))
        except Exception:
            pass
        if not self.jobs:
            return
        now_t = time.time()
        if now_t - getattr(self, "_last_save", 0) > 6.0:
            self._last_save = now_t
            self._save_active()
        if now_t - getattr(self, "_last_pump", 0) > 3.0:
            self._last_pump = now_t
            if any(j.state == "queued" for j in list(self.jobs)):
                self._pump_core()
        if self.dl_view:
            self._dl_refresh()
        act = [j for j in list(self.jobs) if j.state == "downloading"]
        if not act:
            pz = [j for j in list(self.jobs) if j.state == "paused"]
            if pz and len(pz) == len(self.jobs) and time.time() >= self._msg_until:
                self.status.text = t("paused")
                self.status.color = TXT
                self.detail.text = fix(pz[0].title)
            return
        lead = act[0]
        self.bar.value = lead.progress
        self.pct.text = "%d%%" % lead.progress if lead.progress else ""
        if time.time() < self._msg_until:
            return
        if len(self.jobs) == 1:
            self.status.text = t("downloading")
        else:
            self.status.text = fix(ts("active_n", len(act), len(self.jobs) - len(act)))
        self.status.color = TXT
        self.detail.text = fix(self.job_lines(lead)[1])

    def on_pause(self):
        self._save_active(force=True)
        return True     # التحميل يكمّل والتطبيق في الخلفية

    def on_resume(self):
        # الرجوع من الخلفية: نجبر إعادة الرسم (بيمنع الشاشة السودا لو الـ GL context اتبدّل)
        def _kick(dt):
            try:
                Window.canvas.ask_update()
                Window.update_viewport()
            except Exception:
                pass
            try:
                if self.dl_view:
                    self._dl_rebuild()
            except Exception:
                pass
        Clock.schedule_once(_kick, 0)
        Clock.schedule_once(_kick, 0.4)
        Clock.schedule_once(self._storage_recheck, 0.6)
        Clock.schedule_once(lambda dt: self._notif_sync(force=True), 0.8)

    def on_stop(self):
        self._save_active(force=True)
        for j in list(self.jobs):
            NOTIF.cancel_job(j.id)
        try:
            if not self.jobs:      # لو فيه تحميلات موقوفة سيب ملفاتها الجزئية عشان تكمّل بعدين
                cleanup_temp_files(self.download_dir)
        except Exception:
            pass

    # ---------------- downloads panel
    def open_downloads(self, *_):
        self.show_tab("downloads")

    def _build_dl_page(self):
        al = "right" if self.rtl else "left"
        page = BoxLayout(orientation="vertical", padding=[dp(14), dp(40), dp(14), dp(8)],
                         spacing=dp(10))
        page.add_widget(TLabel(text=t("downloads"), font_size=dp(24), bold=True,
                               size_hint_y=None, height=dp(48), halign=al))
        tabs = Card(orientation="horizontal", size_hint_y=None, height=dp(50),
                    padding=dp(4), spacing=dp(4), bg=CARD, radius=dp(25))
        self.dl_tab_a = RoundButton(radius=dp(21), text=t("tab_active"))
        self.dl_tab_h = RoundButton(radius=dp(21), text=t("tab_history"))
        self.dl_tab_a.bind(on_release=lambda *_: self.set_dl_tab("active"))
        self.dl_tab_h.bind(on_release=lambda *_: self.set_dl_tab("history"))
        for w in ((self.dl_tab_h, self.dl_tab_a) if self.rtl else (self.dl_tab_a, self.dl_tab_h)):
            tabs.add_widget(w)
        page.add_widget(tabs)

        sv = ScrollView(bar_width=dp(3), bar_color=(1, 1, 1, .25))
        self.dl_box = GridLayout(cols=1, spacing=dp(8), size_hint_y=None,
                                 padding=[0, 0, 0, dp(4)])
        self.dl_box.bind(minimum_height=self.dl_box.setter("height"))
        sv.add_widget(self.dl_box)
        page.add_widget(sv)

        # شريط التحديد (بيظهر بس لما تدوس مطولًا على عنصر في "تم التحميل")
        self.dl_bar = BoxLayout(size_hint_y=None, height=0, spacing=dp(8))
        page.add_widget(self.dl_bar)
        self.dl_page = page
        self.dl_rows = {}
        self.sel_checks = {}

    def set_dl_tab(self, name):
        self.dl_tab = name
        a = name == "active"
        self.dl_tab_a.bg = list(ACC1) + [1] if a else list(TRANSP)
        self.dl_tab_h.bg = list(ACC1) + [1] if not a else list(TRANSP)
        self.dl_tab_a.color = WHITE if a else DIM
        self.dl_tab_h.color = WHITE if not a else DIM
        self.sel_mode = False
        self.sel_ids = set()
        self._sel_bar()
        self._dl_rebuild()

    # ---- تحديد وحذف عناصر "تم التحميل"
    def _sel_start(self, e):
        if self.dl_tab != "history" or self.sel_mode:
            return
        self.sel_mode = True
        self.sel_ids = {id(e)}
        SOUND.play("tap")
        self._dl_rebuild()
        self._sel_bar()

    def _sel_tap(self, e):
        if not self.sel_mode:
            return
        k = id(e)
        if k in self.sel_ids:
            self.sel_ids.discard(k)
        else:
            self.sel_ids.add(k)
        cm = self.sel_checks.get(k)
        if cm is not None:
            cm.on = k in self.sel_ids
        self._sel_bar()

    def _sel_exit(self):
        self.sel_mode = False
        self.sel_ids = set()
        self._dl_rebuild()
        self._sel_bar()

    def _sel_all(self, *_):
        ids = {id(e) for e in self.history}
        self.sel_ids = set() if self.sel_ids >= ids else ids
        for k, cm in self.sel_checks.items():
            cm.on = k in self.sel_ids
        self._sel_bar()

    def _sel_bar(self):
        bar = getattr(self, "dl_bar", None)
        if bar is None:
            return
        bar.clear_widgets()
        if not self.sel_mode:
            bar.height = 0
            return
        bar.height = dp(46)
        n = len(self.sel_ids)
        all_on = bool(self.history) and n >= len(self.history)
        b1 = RoundButton(text=L("Deselect all", "إلغاء التحديد") if all_on else L("Select all", "تحديد الكل"),
                         font_size=dp(13), bg=CARD2)
        b1.bind(on_release=self._sel_all)
        b2 = RoundButton(text=L("Delete (%d)" % n, "حذف (%d)" % n), font_size=dp(13),
                         bg=(0.55, 0.18, 0.25, 1) if n else CARD2)
        b2.bind(on_release=self._sel_delete)
        b3 = RoundButton(text=t("cancel"), font_size=dp(13), bg=CARD2)
        b3.bind(on_release=lambda *_: self._sel_exit())
        for w in ((b3, b2, b1) if self.rtl else (b1, b2, b3)):
            bar.add_widget(w)

    def _sel_delete(self, *_):
        entries = [e for e in self.history if id(e) in self.sel_ids]
        if not entries:
            return
        mv = ModalView(size_hint=(0.9, None), height=dp(260), auto_dismiss=True,
                       background="", background_color=(0, 0, 0, .55))
        card = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10))
        with card.canvas.before:
            Color(*CARD)
            bgr = RoundedRectangle(pos=card.pos, size=card.size, radius=[dp(16)])
        card.bind(pos=lambda w, v: setattr(bgr, "pos", v),
                  size=lambda w, v: setattr(bgr, "size", v))
        card.add_widget(TLabel(text=L("Delete %d item(s)" % len(entries),
                                      "حذف %d عنصر" % len(entries)),
                               font_size=dp(17), bold=True, halign="center",
                               size_hint_y=None, height=dp(30)))
        b_app = RoundButton(text=L("Remove from the app only", "مسح من البرنامج فقط"),
                            font_size=dp(14), bg=CARD2, size_hint_y=None, height=dp(48))
        b_all = RoundButton(text=L("Delete from app and phone permanently",
                                   "مسح من البرنامج والتليفون نهائيًا"),
                            font_size=dp(14), bg=(0.55, 0.18, 0.25, 1),
                            size_hint_y=None, height=dp(48))
        b_no = RoundButton(text=t("cancel"), font_size=dp(14), bg=CARD2,
                           size_hint_y=None, height=dp(44))

        def go(from_phone):
            mv.dismiss()
            self._do_delete(entries, from_phone)
        b_app.bind(on_release=lambda *_: go(False))
        b_all.bind(on_release=lambda *_: go(True))
        b_no.bind(on_release=lambda *_: mv.dismiss())
        for w in (b_app, b_all, b_no):
            card.add_widget(w)
        mv.add_widget(card)
        mv.open()

    def _do_delete(self, entries, from_phone):
        failed = 0
        for e in entries:
            if from_phone:
                p = e.get("path")
                if p and os.path.isfile(p):
                    try:
                        os.remove(p)
                        media_scan(p, force=True)
                    except Exception:
                        failed += 1
        with self.lock:
            for e in entries:
                if e in self.history:
                    self.history.remove(e)
            save_history(self.history)
        self._sel_exit()
        if failed:
            self._toast(L("Couldn't delete %d file(s) from the phone" % failed,
                          "تعذّر حذف %d ملف من التليفون" % failed))

    def _dl_empty(self, key):
        self.dl_box.add_widget(EmptyState(text=t(key), size_hint_y=None, height=dp(230)))

    def _dl_rebuild(self):
        if not self.dl_view:
            return
        with self.lock:
            jobs_snapshot = list(self.jobs)
            history_snapshot = list(self.history)
        self.dl_box.clear_widgets()
        self.dl_rows = {}
        self.sel_checks = {}
        if self.dl_tab == "active":
            if not jobs_snapshot:
                self._dl_empty("no_active")
            for job in jobs_snapshot:
                row, refs = self._make_active_row(job)
                self.dl_rows[job.id] = refs
                self.dl_box.add_widget(row)
            self._dl_refresh()
        else:
            if not history_snapshot:
                self._dl_empty("no_history")
            for e in history_snapshot:
                self.dl_box.add_widget(self._make_hist_row(e))

    def _dl_refresh(self):
        if self.dl_tab != "active":
            return
        for job in list(self.jobs):
            refs = self.dl_rows.get(job.id)
            if not refs:
                continue
            m1, m2 = self.job_lines(job)
            refs["meta"].text = fix(m1)
            refs["detail"].text = fix(m2)
            if job.cancel:
                eta_text = ts("cancelling")
            elif job.state == "paused" or job.pause_req:
                eta_text = ts("paused") if job.state == "paused" else ts("pausing")
            elif job.state == "queued":
                eta_text = ts("waiting") if net_allowed() else ts("net_wait")
            elif job.phase == "proc":
                eta_text = ts("processing")
            elif job.eta is not None:
                eta_text = "متبقي: %s" % fmt_dur(job.eta) if self.rtl else "Remaining: %s" % fmt_dur(job.eta)
            else:
                eta_text = ""
            refs["eta"].text = fix(eta_text)
            refs["bar"].value = job.progress
            want = t("resume") if (job.state == "paused" or job.pause_req) else t("pause")
            if refs["pause"].text != want:
                refs["pause"].text = want

    def _load_job_thumb(self, widget, url, ref=None):
        """تحميل صورة معاينة كارت التنزيل بالخلفية ثم فكها إلى Kivy texture."""
        def work():
            data = b""
            try:
                import urllib.request
                host = url.lower()
                headers = {
                    "User-Agent": ("Mozilla/5.0 (Linux; Android 13; Mobile) "
                                   "AppleWebKit/537.36 Chrome/124.0.0.0 Mobile Safari/537.36"),
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                }
                if "tiktok" in host:
                    headers["Referer"] = "https://www.tiktok.com/"
                elif "instagram" in host or "cdninstagram" in host:
                    headers["Referer"] = "https://www.instagram.com/"
                elif "fbcdn" in host or "facebook" in host:
                    headers["Referer"] = "https://www.facebook.com/"
                elif "ytimg" in host or "youtube" in host:
                    headers["Referer"] = "https://www.youtube.com/"
                elif ref:
                    headers["Referer"] = ref      # مواقع عادية بتمنع الصورة من غير Referer بتاع صفحتها
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=18) as response:
                    data = response.read(5 * 1024 * 1024)
                if data:
                    self.ui(self._apply_job_thumb, widget, data, url)
            except Exception as e:
                print("NovaGet download-card thumbnail fetch failed:", str(e)[:180])

        threading.Thread(target=work, daemon=True).start()

    def _apply_job_thumb(self, widget, data, url):
        """فك JPEG/PNG/WebP (إن دعمته Pillow) ثم عرض الصورة في كارت التحميل."""
        import io
        from kivy.core.image import Image as CoreImage
        tex = None
        try:
            from PIL import Image as PILImage
            im = PILImage.open(io.BytesIO(data)).convert("RGB")
            im.thumbnail((320, 320))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=86)
            buf.seek(0)
            tex = CoreImage(buf, ext="jpg").texture
        except Exception:
            for ext in ("jpg", "png", "webp"):
                try:
                    tex = CoreImage(io.BytesIO(data), ext=ext).texture
                    if tex is not None:
                        break
                except Exception:
                    tex = None
        if tex is not None:
            widget.texture = tex
            widget.opacity = 1
        else:
            print("NovaGet download-card thumbnail decode failed:", url[:180])

    def _make_active_row(self, job):
        al = "right" if self.rtl else "left"
        row = Card(orientation="horizontal", size_hint_y=None, height=dp(112),
                   padding=dp(9), spacing=dp(8), bg=CARD, radius=dp(20), elev=1)
        col = BoxLayout(orientation="vertical", spacing=dp(2), size_hint_x=1)
        title = TLabel(text=fix(job.title), font_size=dp(13), bold=True, halign=al,
                       size_hint_y=None, height=dp(21), shorten=True, shorten_from="left" if self.rtl else "right")
        meta = TLabel(font_size=dp(10), color=DIM, halign=al, size_hint_y=None, height=dp(15),
                      shorten=True, shorten_from="left" if self.rtl else "right")
        detail = TLabel(font_size=dp(10), color=DIM, halign=al, size_hint_y=None, height=dp(15),
                        shorten=True, shorten_from="left" if self.rtl else "right")
        eta = TLabel(font_size=dp(11), color=ACC1, bold=True, halign=al,
                     size_hint_y=None, height=dp(17), shorten=True,
                     shorten_from="left" if self.rtl else "right")
        bar = SlimProgress(size_hint_y=None, height=dp(6))
        for w in (title, meta, detail, eta, bar):
            col.add_widget(w)

        thumb_url = str(job.it.get("thumb") or job.it.get("thumbnail") or "").strip()
        if thumb_url.startswith("//"):
            thumb_url = "https:" + thumb_url
        # لا تعتمد على AsyncImage لتحميل رابط الإنترنت مباشرة؛ بعض إصدارات
        # Kivy على أندرويد تعرض مربعًا أسود بدل الصورة. ننزّل البايتات ثم نفكها.
        thumb = KImage(size_hint=(None, None), size=(dp(66), dp(66)),
                       allow_stretch=True, keep_ratio=True)
        thumb.opacity = 0
        if thumb_url.startswith(("http://", "https://")) and thumb_url != str(job.it.get("vurl") or ""):
            self._load_job_thumb(thumb, thumb_url, job.it.get("ref"))
        else:
            tp = str(job.it.get("thumb_path") or "")      # لقطة من الفيديو نفسه (فيديو مباشر)
            if tp and os.path.isfile(tp):
                try:
                    with open(tp, "rb") as _f:
                        self._apply_job_thumb(thumb, _f.read(), tp)
                except Exception:
                    pass

        btns = BoxLayout(orientation="vertical", spacing=dp(5), size_hint=(None, None),
                         size=(dp(82), dp(80)), pos_hint={"center_y": .5})
        pause = RoundButton(text=t("pause"), font_size=dp(11), bg=(0.20, 0.25, 0.42, 1))
        pause.bind(on_release=lambda *_: self.toggle_pause(job))
        cancel = RoundButton(text=t("cancel"), font_size=dp(11), bg=(0.55, 0.18, 0.25, 1))
        cancel.bind(on_release=lambda *_: self.cancel_job(job))
        btns.add_widget(pause)
        btns.add_widget(cancel)
        if self.rtl:
            for w in (btns, col, thumb):
                row.add_widget(w)
        else:
            for w in (thumb, col, btns):
                row.add_widget(w)
        return row, dict(meta=meta, detail=detail, eta=eta, bar=bar, pause=pause, thumb=thumb)

    def _make_hist_row(self, e):
        al = "right" if self.rtl else "left"
        ok = e.get("state") == "done"
        it = e.get("it") or {}
        row = LongRow(on_long=lambda: self._sel_start(e), on_tap=lambda: self._sel_tap(e),
                      orientation="horizontal", size_hint_y=None, height=dp(112),
                      padding=dp(10), spacing=dp(9), bg=CARD, radius=dp(20), elev=1)
        preview = KImage(size_hint=(None, None), size=(dp(68), dp(68)),
                         allow_stretch=True, keep_ratio=True)
        preview.opacity = 0
        local_thumb = str(e.get("thumb_path") or "")
        local_media = str(e.get("path") or "")
        preview_loaded = False
        # First use the cached frame if it can actually be decoded. A stale/corrupt
        # cached path must NOT prevent the original online thumbnail fallback.
        if local_thumb and os.path.isfile(local_thumb):
            try:
                from kivy.core.image import Image as CoreImage
                tex = CoreImage(local_thumb).texture
                if tex is not None:
                    preview.texture = tex
                    preview.opacity = 1
                    preview_loaded = True
            except Exception as exc:
                print("NovaGet history cached thumbnail decode failed:", str(exc)[:160])
        kind = str(it.get("kind") or "").lower()
        is_video = kind in ("m", "mx", "v", "av", "video") or bool(it.get("vurl"))
        if ok and local_media and os.path.isfile(local_media) and is_video and not preview_loaded:
            # Queue local-frame extraction, but independently try the exact source
            # thumbnail URL too. The online thumbnail is often what was visible in
            # the active row; history should use it as well if local extraction fails.
            self._load_history_video_thumb(preview, e)
        if not preview_loaded:
            remote_thumb = str(
                e.get("thumb") or e.get("thumbnail") or e.get("thumb_url") or
                it.get("thumb") or it.get("thumbnail") or it.get("thumb_url") or ""
            ).strip()
            if remote_thumb.startswith("//"):
                remote_thumb = "https:" + remote_thumb
            if remote_thumb.startswith(("http://", "https://")):
                self._load_job_thumb(preview, remote_thumb)
        col = BoxLayout(orientation="vertical", spacing=dp(3), size_hint_x=1)
        col.add_widget(TLabel(text=fix(e.get("title") or ""), font_size=dp(14), bold=True, halign=al))
        sz = e.get("bytes") or it.get("size")
        try:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.get("ts") or 0))
        except Exception:
            when = ""
        kind = it.get("kind")
        label = str(it.get("label", ""))
        if kind == "img":
            label = label or ts("k_img")
        line = " · ".join(x for x in (label, str(it.get("ext", "")),
                                      human_size(sz) if sz else "", when) if x)
        col.add_widget(TLabel(text=fix(line), font_size=dp(11), color=DIM, halign=al,
                              size_hint_y=None, height=dp(16)))
        if ok:
            st_txt, st_col = ts("done"), GREEN
        else:
            st_txt = ts("failed") + ((": " + e["err"]) if e.get("err") else "")
            st_col = RED
        col.add_widget(TLabel(text=fix(st_txt), font_size=dp(11), color=st_col, halign=al,
                              size_hint_y=None, height=dp(16)))
        btns = BoxLayout(orientation="vertical", spacing=dp(6), size_hint=(None, None),
                         size=(dp(84), dp(80)), pos_hint={"center_y": .5})
        if ok:
            path = e.get("path") or ""
            open_btn = RoundButton(text=t("open_file"), font_size=dp(12), bg=list(ACC1) + [1])
            open_btn.bind(on_release=lambda *_: open_path(path if path and os.path.isfile(path)
                                                         else self.download_dir))
            rem = RoundButton(text=t("remove"), font_size=dp(12), bg=CARD2)
            rem.bind(on_release=lambda *_: self.remove_entry(e))
            btns.add_widget(open_btn)
            btns.add_widget(rem)
        else:
            btn = RoundButton(text=t("retry"), font_size=dp(12), bg=list(ACC1) + [1])
            btn.bind(on_release=lambda *_: self.retry_entry(e))
            btns.add_widget(btn)
        side = btns
        if self.sel_mode:
            side = CheckMark(size_hint=(None, None), size=(dp(28), dp(28)),
                             pos_hint={"center_y": .5}, on=(id(e) in self.sel_ids))
            self.sel_checks[id(e)] = side
        parts = (side, col, preview) if self.rtl else (preview, col, side)
        for w in parts:
            row.add_widget(w)
        return row

    def _load_history_video_thumb(self, widget, entry):
        """Extract a frame on a bounded worker pool; update history only on the UI thread."""
        media = str(entry.get("path") or "")
        if not media or not os.path.isfile(media):
            return
        media_key = os.path.abspath(media)
        with self._history_thumb_lock:
            if media_key in self._history_thumb_pending:
                return
            self._history_thumb_pending.add(media_key)

        def work():
            scheduled = False
            try:
                thumb_path = video_thumb(media)
                if thumb_path and os.path.isfile(thumb_path):
                    self.ui(self._finish_history_video_thumb,
                            media_key, widget, entry, thumb_path, media)
                    scheduled = True
            except Exception as exc:
                print("NovaGet history thumbnail failed:", str(exc)[:160])
            finally:
                if not scheduled:
                    with self._history_thumb_lock:
                        self._history_thumb_pending.discard(media_key)

        try:
            _THUMB_POOL.submit(work)
        except Exception as exc:
            with self._history_thumb_lock:
                self._history_thumb_pending.discard(media_key)
            print("NovaGet history thumbnail queue failed:", str(exc)[:160])

    def _finish_history_video_thumb(self, media_key, widget, entry, thumb_path, media):
        """Finalize a thumbnail on the Kivy thread and persist only live entries."""
        with self._history_thumb_lock:
            self._history_thumb_pending.discard(media_key)
        if not os.path.isfile(media) or not os.path.isfile(thumb_path):
            return
        with self.lock:
            if entry not in self.history:
                return
            entry["thumb_path"] = thumb_path
            save_history(self.history)
        self._apply_history_video_thumb(widget, thumb_path, media)

    def _apply_history_video_thumb(self, widget, thumb_path, media):
        if not os.path.isfile(media) or not os.path.isfile(thumb_path):
            return
        try:
            from kivy.core.image import Image as CoreImage
            widget.texture = CoreImage(thumb_path).texture
            widget.opacity = 1
        except Exception as exc:
            print("NovaGet history thumbnail display failed:", str(exc)[:160])

    def remove_entry(self, e):
        with self.lock:
            if e in self.history:
                self.history.remove(e)
                save_history(self.history)
        self._dl_rebuild()

    def retry_entry(self, e):
        job = Job(e.get("url") or "", e.get("title") or "Untitled", e.get("it") or {})
        if job.url and self.enqueue(job):
            with self.lock:
                if e in self.history:
                    self.history.remove(e)
                    save_history(self.history)
            if self.dl_view:
                self.set_dl_tab("active")


if __name__ == "__main__":
    NovaGetApp().run()
