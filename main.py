import os
import logging
import yt_dlp
import time
from threading import Thread
from flask import Flask, Response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, CallbackContext

# ==================== Health Check Setup ====================
app = Flask(__name__)
@app.route('/')
def health_check():
    return Response("OK", status=200)

def run_flask_app():
    app.run(host='0.0.0.0', port=8000)

# Start Flask in a separate thread
Thread(target=run_flask_app, daemon=True).start()

# ==================== Bot Configuration ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Constants
MAX_TELEGRAM_FILE_SIZE = 2097152000  # 2GB
DOWNLOAD_TIMEOUT = 300  # 5 minutes
CACHE_EXPIRY = 3600  # 1 hour

# Store user data temporarily
user_data = {}

# ==================== Bot Handlers ====================
async def start(update: Update, context: CallbackContext) -> None:
    keyboard = [[InlineKeyboardButton("🎥 Enter YouTube Link", callback_data="enter_link")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "👋 Welcome to YouTube Downloader Bot!\n"
        "I can download videos from YouTube for you.\n\n"
        "Press the button below to start ➡️",
        reply_markup=reply_markup,
    )

async def button_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.message.chat_id

    if query.data == "enter_link":
        await query.message.reply_text("📥 Please send the YouTube video link:")
        return

    if query.data.startswith("quality_"):
        if user_id in user_data:
            quality = query.data.split("_")[1]
            video_info = user_data[user_id]

            await query.message.reply_text(f"⏳ Downloading in {quality}p... Please wait.")
            await download_video(video_info["url"], quality, query)
        else:
            await query.message.reply_text("❌ Session expired. Please send the link again.")

async def handle_message(update: Update, context: CallbackContext) -> None:
    user_id = update.message.chat_id
    url = update.message.text.strip()
    url = normalize_youtube_url(url)

    try:
        if not is_valid_youtube_url(url):
            await update.message.reply_text("⚠️ Please send a valid YouTube URL.")
            return

        formats = get_video_qualities(url)
        if not formats:
            await update.message.reply_text("❌ No downloadable formats found. Try another video.")
            return

        # Store user data with timestamp
        user_data[user_id] = {
            "url": url,
            "formats": formats,
            "timestamp": time.time()
        }

        # Create quality buttons (top 4 resolutions)
        keyboard = []
        available_res = sorted([int(r) for r in formats.keys()], reverse=True)[:4]
        for res in available_res:
            keyboard.append([InlineKeyboardButton(f"{res}p", callback_data=f"quality_{res}")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("🎞️ Select video quality:", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error processing URL: {e}")
        await update.message.reply_text("❌ Error processing link. Please try again.")

# ==================== Utility Functions ====================
def is_valid_youtube_url(url: str) -> bool:
    return any(domain in url for domain in ["youtube.com", "youtu.be"])

def normalize_youtube_url(url: str) -> str:
    if "youtu.be" in url:
        video_id = url.split("/")[-1].split("?")[0]
        return f"https://www.youtube.com/watch?v={video_id}"
    return url.split("&")[0]  # Remove tracking parameters

def get_video_qualities(url):
    try:
        ydl_opts = {
            "quiet": True,
            "extract_flat": True,
            "socket_timeout": 30,
            "retries": 3
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get("formats", [])
            return {
                str(f["height"]): f["format_id"]
                for f in formats
                if f.get("height") and f.get("vcodec") != "none"
            }
    except Exception as e:
        logger.error(f"Error getting qualities: {e}")
        return None

async def download_video(url, resolution, query) -> None:
    user_id = query.message.chat_id
    file_path = f"download_{user_id}_{int(time.time())}.mp4"

    try:
        ydl_opts = {
            "format": f"bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
            "outtmpl": file_path,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            "extract_flat": True,
            "max_filesize": MAX_TELEGRAM_FILE_SIZE,
            "http_chunk_size": 1048576,  # 1MB chunks
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        start_time = time.time()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if not os.path.exists(file_path):
            raise FileNotFoundError("Download failed - no output file")

        file_size = os.path.getsize(file_path)
        if file_size > MAX_TELEGRAM_FILE_SIZE:
            raise ValueError(f"File too large ({file_size/1024/1024:.2f}MB > 2000MB)")

        await query.message.reply_text(f"✅ Download complete! Uploading now...")
        with open(file_path, "rb") as video_file:
            await query.message.reply_video(
                video=video_file,
                supports_streaming=True,
                read_timeout=DOWNLOAD_TIMEOUT,
                write_timeout=DOWNLOAD_TIMEOUT,
                connect_timeout=DOWNLOAD_TIMEOUT
            )

        await query.message.reply_text(
            "🎉 Done! Want to download another video?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬇️ Download Another", callback_data="enter_link")]
            )
        )
    except Exception as e:
        logger.error(f"Download error: {e}")
        await query.message.reply_text(f"❌ Download failed: {str(e)}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

def cleanup_old_data():
    """Periodically clean up old user data"""
    while True:
        try:
            current_time = time.time()
            expired_users = [
                user_id for user_id, data in user_data.items()
                if current_time - data.get("timestamp", 0) > CACHE_EXPIRY
            ]
            for user_id in expired_users:
                user_data.pop(user_id, None)
            time.sleep(3600)  # Run hourly
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

# ==================== Main Execution ====================
def main():
    # Load token from environment variable
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
        return

    # Create application
    application = Application.builder().token(TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Start cleanup thread
    Thread(target=cleanup_old_data, daemon=True).start()
    
    logger.info("Bot starting with health check on port 8000...")
    application.run_polling(
        poll_interval=1,
        timeout=30,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()