"""
Telegram QR Bot — bilingual Persian/English
Features:
- Text/link -> QR
- Small file -> QR (Base64 payload)
- Center banana logo with conservative size for reliable scanning
- Bilingual UI (Persian + English)
- Per-user history: last 10 generated QR images
- SQLite storage
- Friendly error handling
"""

import base64
import io
import logging
import os
import random
import sqlite3
from pathlib import Path

import qrcode
import qrcode.exceptions
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    RTL_SUPPORT = True
except ImportError:
    RTL_SUPPORT = False


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = "8718984983:AAGblp3ye7vc7ns8gA77SU2mi6maETALNFQ"

# 900 bytes is intentionally conservative. The actual QR capacity depends on
# encoding/version/error correction, so DataOverflowError is still handled.
MAX_FILE_BYTES = 900
HISTORY_LIMIT = 10
DB_PATH = BASE_DIR / "qr_history.sqlite3"

# A center logo should not cover too many modules. 18% is safer than the old 22%.
LOGO_FRACTION = 0.18

CAPTIONS_FA = [
    "این QR کد بیشتر از تو به زندگیت اهمیت میده 😂",
    "اسکنش کن، ولی مسئولیت هرچی دیدی با خودته!",
    "ساخته‌شده با عشق، اسکن‌شده با شک 🤳",
    "اگه اسکن نشد، بدون گوشیت داره باهات قهر می‌کنه.",
]
CAPTIONS_EN = [
    "This QR cares about your life more than you do 😂",
    "Scan it, but you're responsible for what you find!",
    "Made with love, scanned with suspicion 🤳",
    "If it doesn't scan, your phone is probably mad at you.",
]

START_TEXT = (
    "سلام! 👋\n"
    "من ربات تبدیل متن و فایل به QR هستم.\n\n"
    "🇮🇷 فارسی: متن/لینک یا یک فایل کوچک بفرست تا QR بسازم.\n"
    "🇬🇧 English: Send text/link or a small file and I'll create a QR code.\n\n"
    f"حداکثر حجم فایل / Max file size: {MAX_FILE_BYTES} bytes"
)

HELP_TEXT = (
    "📖 راهنما / Help\n\n"
    "🇮🇷 فارسی\n"
    "• متن یا لینک بفرست → QR ساخته می‌شود.\n"
    "• فایل کوچک بفرست → محتوای فایل داخل QR قرار می‌گیرد.\n"
    "• ۱۰ خروجی آخر در تاریخچه ذخیره می‌شوند.\n"
    "• برای فایل‌های بزرگ، لینک دانلود را داخل QR بفرست.\n\n"
    "🇬🇧 English\n"
    "• Send text or a link → a QR code is created.\n"
    "• Send a small file → its content is encoded into the QR.\n"
    "• Your last 10 generated QR codes are kept in history.\n"
    "• For large files, put a download link in the QR instead.\n\n"
    "دستورها / Commands:\n"
    "/start — شروع / Start\n"
    "/help — راهنما / Help\n"
    "/history — تاریخچه ۱۰ خروجی آخر / Last 10 QR codes"
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("qr_bot")


class QRPayloadTooLargeError(Exception):
    pass


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qr_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                label TEXT NOT NULL,
                qr_png BLOB NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_qr_history_user "
            "ON qr_history(user_id, id DESC)"
        )
        conn.commit()


def save_history(user_id: int, label: str, png_bytes: bytes) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO qr_history(user_id, label, qr_png) VALUES (?, ?, ?)",
            (user_id, label[:200], sqlite3.Binary(png_bytes)),
        )
        # Keep only the newest HISTORY_LIMIT entries for this user.
        conn.execute(
            """
            DELETE FROM qr_history
            WHERE user_id = ?
              AND id NOT IN (
                  SELECT id FROM qr_history
                  WHERE user_id = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            """,
            (user_id, user_id, HISTORY_LIMIT),
        )
        conn.commit()


def get_history(user_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            """
            SELECT id, created_at, label, qr_png
            FROM qr_history
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, HISTORY_LIMIT),
        ).fetchall()


def to_visual_text(text: str) -> str:
    if not RTL_SUPPORT:
        return text
    return get_display(arabic_reshaper.reshape(text))


def load_caption_font(size: int):
    candidates = []
    fonts_dir = BASE_DIR / "fonts"
    candidates.extend(sorted(fonts_dir.glob("*.ttf")))
    candidates.extend(sorted(fonts_dir.glob("*.otf")))
    candidates.extend(
        [
            Path("/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Bold.ttf"),
            Path("/usr/share/fonts/truetype/vazir/Vazir-Bold.ttf"),
            Path("/usr/share/fonts/opentype/noto/NotoNaskhArabic-Bold.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ]
    )
    for path in candidates:
        try:
            return ImageFont.truetype(str(path), size)
        except (OSError, IOError):
            pass
    return ImageFont.load_default()


def draw_banana_logo(size: int) -> Image.Image:
    """Small, high-contrast center logo with a white quiet area."""
    logo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(logo)
    draw.ellipse(
        [1, 1, size - 2, size - 2],
        fill="white",
        outline="black",
        width=max(1, size // 45),
    )

    width = max(2, int(size * 0.13))
    box = [size * 0.20, size * 0.16, size * 0.86, size * 0.84]
    draw.arc(box, start=200, end=340, fill="#F5C518", width=width)
    draw.arc(box, start=200, end=340, fill="#3A2E00", width=max(1, size // 55))

    r = max(2, int(size * 0.04))
    for cx, cy in [(size * 0.24, size * 0.30), (size * 0.78, size * 0.62)]:
        draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill="#6B4A1B")
    return logo


def build_qr_image(payload: str, language: str = "fa") -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=12,
        border=4,
    )
    qr.add_data(payload)
    try:
        qr.make(fit=True)
    except qrcode.exceptions.DataOverflowError as exc:
        raise QRPayloadTooLargeError from exc

    qr_img = qr.make_image(
        fill_color="black",
        back_color="white",
    ).convert("RGBA")

    # 18% is deliberately conservative for reliable scanning.
    logo_size = int(min(qr_img.size) * LOGO_FRACTION)
    logo = draw_banana_logo(logo_size)
    pos = ((qr_img.width - logo_size) // 2, (qr_img.height - logo_size) // 2)
    qr_img.alpha_composite(logo, dest=pos)
    qr_img = qr_img.convert("RGB")

    caption = random.choice(CAPTIONS_FA if language == "fa" else CAPTIONS_EN)
    font = load_caption_font(18)
    padding_bottom = 90
    final_img = Image.new(
        "RGB", (qr_img.width, qr_img.height + padding_bottom), "white"
    )
    final_img.paste(qr_img, (0, 0))

    draw = ImageDraw.Draw(final_img)
    words = caption.split()
    lines = []
    current = ""
    max_width = final_img.width - 24

    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(to_visual_text(candidate), font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)

    y = qr_img.height + 10
    for line in lines[:3]:
        visual = to_visual_text(line)
        w = draw.textlength(visual, font=font)
        draw.text(((final_img.width - w) / 2, y), visual, fill="black", font=font)
        y += 24

    buf = io.BytesIO()
    final_img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📖 راهنما / Help", callback_data="help"),
                InlineKeyboardButton("🕘 تاریخچه / History", callback_data="history"),
            ]
        ]
    )
    await update.message.reply_text(
        START_TEXT,
        reply_markup=keyboard,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(HELP_TEXT)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_history(update.effective_user.id)
    if not rows:
        await update.message.reply_text(
            "🕘 هنوز چیزی در تاریخچه نیست.\n"
            "No QR codes in history yet."
        )
        return

    await update.message.reply_text(
        f"🕘 آخرین {len(rows)} خروجی / Last {len(rows)} QR codes:"
    )
    for index, (_row_id, created_at, label, png) in enumerate(rows, 1):
        bio = io.BytesIO(png)
        bio.name = f"history_{index}.png"
        await update.message.reply_photo(
            photo=bio,
            caption=f"#{index} — {label}\n{created_at} UTC",
        )


async def history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rows = get_history(query.from_user.id)
    if not rows:
        await query.message.reply_text(
            "🕘 هنوز چیزی در تاریخچه نیست.\nNo QR codes in history yet."
        )
        return
    await query.message.reply_text(
        f"🕘 آخرین {len(rows)} خروجی / Last {len(rows)} QR codes:"
    )
    for index, (_row_id, created_at, label, png) in enumerate(rows, 1):
        bio = io.BytesIO(png)
        bio.name = f"history_{index}.png"
        await query.message.reply_photo(
            photo=bio,
            caption=f"#{index} — {label}\n{created_at} UTC",
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text:
        await send_qr(update, text, label="Text / متن")


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    doc = msg.document or (msg.photo[-1] if msg.photo else None)
    if doc is None:
        await msg.reply_text(
            "❌ نوع فایل پشتیبانی نمی‌شود.\n"
            "❌ Unsupported file type."
        )
        return

    declared_size = getattr(doc, "file_size", None)
    if declared_size is not None and declared_size > MAX_FILE_BYTES:
        await msg.reply_text(
            f"❌ فایل بزرگ است: بیش از {MAX_FILE_BYTES} bytes.\n"
            f"❌ File is too large: over {MAX_FILE_BYTES} bytes."
        )
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.UPLOAD_PHOTO,
    )

    try:
        tg_file = await context.bot.get_file(doc.file_id)
        file_bytes = bytes(await tg_file.download_as_bytearray())
    except Exception:
        logger.exception("File download failed")
        await msg.reply_text(
            "❌ دانلود فایل ناموفق بود. دوباره امتحان کن.\n"
            "❌ File download failed. Please try again."
        )
        return

    if len(file_bytes) > MAX_FILE_BYTES:
        await msg.reply_text(
            f"❌ فایل بزرگ است: بیش از {MAX_FILE_BYTES} bytes.\n"
            f"❌ File is too large: over {MAX_FILE_BYTES} bytes."
        )
        return

    encoded = base64.b64encode(file_bytes).decode("ascii")
    filename = getattr(doc, "file_name", None) or "photo"
    await send_qr(update, encoded, label=f"File / فایل: {filename}")


async def send_qr(update: Update, payload: str, label: str):
    try:
        png = build_qr_image(payload, language="fa")
    except QRPayloadTooLargeError:
        await update.message.reply_text(
            "❌ داده برای یک QR کد خیلی بزرگ است.\n"
            "❌ The data is too large for one QR code."
        )
        return
    except Exception:
        logger.exception("QR generation failed")
        await update.message.reply_text(
            "❌ ساخت QR ناموفق بود. دوباره امتحان کن.\n"
            "❌ QR generation failed. Please try again."
        )
        return

    try:
        save_history(update.effective_user.id, label, png)
    except Exception:
        logger.exception("History save failed")
        # Do not fail the QR delivery just because history storage failed.

    bio = io.BytesIO(png)
    bio.name = "qr_code.png"
    await update.message.reply_photo(
        photo=bio,
        caption="🇮🇷 آماده شد / 🇬🇧 Done",
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception", exc_info=context.error)


async def post_init(app: Application):
    init_db()
    await app.bot.set_my_commands(
        [
            BotCommand("start", "شروع / Start"),
            BotCommand("help", "راهنما / Help"),
            BotCommand("history", "تاریخچه ۱۰ خروجی / Last 10 QR codes"),
        ]
    )


def main():
    if not RTL_SUPPORT:
        logger.warning(
            "arabic-reshaper/python-bidi unavailable; Persian caption rendering may be incorrect."
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CallbackQueryHandler(help_callback, pattern="^help$"))
    app.add_handler(CallbackQueryHandler(history_callback, pattern="^history$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
    app.add_error_handler(error_handler)

    logger.info("Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
