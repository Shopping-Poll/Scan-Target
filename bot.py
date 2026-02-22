import os
import sys
import logging
import asyncio
import hashlib
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
import psycopg2

# 1. Setup Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# 2. Flask Setup (WSGI compatibility for PythonAnywhere)
app = Flask(__name__)

# 3. Load Environment
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()
DATABASE_URL = os.getenv('DATABASE_URL')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN not set!")

# 4. Telegram Application Setup
# We'll initialize this as global to reuse it across requests
telegram_app = Application.builder().token(BOT_TOKEN).build()

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    try:
        conn = get_db_connection()
        conn.autocommit = True
        cursor = conn.cursor()
        # Remove UNIQUE constraint to track history
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT,
                message_hash TEXT,
                message_text TEXT,
                user_id BIGINT,
                timestamp TIMESTAMP,
                user_name TEXT DEFAULT 'Unknown'
            )
        ''')
        # Migration: Drop old unique constraint if it exists
        try:
            cursor.execute('ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_chat_id_message_hash_key')
        except Exception:
            pass
            
        # Create an index for faster searching
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_chat_hash ON messages(chat_id, message_hash)')
        conn.close()
        logger.info("📊 Database initialized (PostgreSQL)")
    except Exception as e:
        logger.error(f"❌ DB Init Error: {e}")

# Call init once
if DATABASE_URL:
    init_db()

# 5. Bot Logic
async def check_duplicate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id = update.message.chat_id
    text = update.message.text
    user_id = update.message.from_user.id
    user_name = update.message.from_user.full_name
    
    msg_hash = hashlib.md5(text.encode()).hexdigest()
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 1. Store current occurrence
        cursor.execute(
            "INSERT INTO messages (chat_id, message_hash, message_text, user_id, timestamp, user_name) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (chat_id, msg_hash, text, user_id, datetime.now(), user_name)
        )
        conn.commit()

        # 2. Fetch all occurrences (including the one just inserted)
        cursor.execute(
            "SELECT user_name, timestamp FROM messages WHERE chat_id = %s AND message_hash = %s ORDER BY timestamp ASC",
            (chat_id, msg_hash)
        )
        history = cursor.fetchall()
        
        # If more than 1 occurrence, it's a duplicate
        if len(history) > 1:
            # Build the message string
            msg_parts = [
                "❌**DETEKSI DITEMUKAN**❌",
                f"Isi pesan : {text}",
                ""
            ]
            
            # Label mappings
            for i, (u_name, u_time) in enumerate(history):
                time_str = u_time.strftime("%H:%M:%S")
                if i == 0:
                    label = "Pengirim pertama kali"
                elif i == len(history) - 1:
                    label = "Pengirim saat ini"
                elif i == 1:
                    label = "pengirim kedua kali"
                else:
                    # After 2, we just list them or show dst if list is too long
                    # For now, let's follow the user's specific request structure
                    if i == 2 and len(history) > 4:
                        msg_parts.append("...")
                        continue
                    elif i > 2 and i < len(history) - 1 and len(history) > 4:
                        continue
                    label = f"pengirim ke-{i+1}"

                msg_parts.append(f"{u_name} : {label} {time_str}")

            await update.message.reply_text("\n".join(msg_parts), parse_mode='Markdown')
            
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error checking duplicate: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Bot Duplicate Detector Aktif! Tambahkan saya ke grup agar saya bisa bekerja.")

# Setup Handlers
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), check_duplicate))

# 6. Webhook Routes
@app.route('/', methods=['GET', 'HEAD'])
def index():
    """Home route for Render health checks"""
    return "Bot is running via Webhook!", 200

@app.route('/health', methods=['GET'])
def health():
    """Detailed health check"""
    status = {
        "status": "ok",
        "database": "unknown",
        "initialized": is_initialized
    }
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        conn.close()
        status["database"] = "connected"
    except Exception as e:
        status["database"] = f"error: {str(e)}"
        status["status"] = "error"
    return jsonify(status), 200 if status["status"] == "ok" else 500

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
async def webhook():
    """Handle incoming Telegram updates"""
    try:
        logger.info(f"📥 Received webhook request on /{BOT_TOKEN[:5]}...")
        if request.method == "POST":
            data = request.get_json(force=True)
            update = Update.de_json(data, telegram_app.bot)
            await telegram_app.process_update(update)
            return "OK", 200
    except Exception as e:
        logger.error(f"❌ Webhook Logic Error: {e}", exc_info=True)
        return f"Internal Error: {str(e)}", 500
    return "Invalid Request", 400

# 7. Helper for setting webhook automatically
async def start_bot():
    """Initialize and start the telegram app"""
    try:
        logger.info("🚀 Starting bot initialization sequence...")
        # Check if already running to avoid "Already running" errors
        await telegram_app.initialize()
        await telegram_app.start()
        
        if WEBHOOK_URL:
            bot = telegram_app.bot
            full_url = f"{WEBHOOK_URL.rstrip('/')}/{BOT_TOKEN}"
            await bot.set_webhook(url=full_url)
            logger.info(f"🌐 Webhook successfully set to: {full_url}")
        else:
            logger.warning("⚠️ WEBHOOK_URL not set in environment variables!")
            
    except Exception as e:
        logger.error(f"❌ Initialization Traceback:", exc_info=True)
        raise e

# Use a lock to ensure only one worker/thread initializes the bot
init_lock = asyncio.Lock()
is_initialized = False

@app.before_request
async def ensure_initialized():
    global is_initialized
    # Skip initialization for health check if we want to debug health separately
    if request.path == '/health':
        return
        
    if not is_initialized:
        try:
            async with init_lock:
                if not is_initialized:
                    await start_bot()
                    is_initialized = True
                    logger.info("✅ Bot initialization complete.")
        except Exception as e:
            logger.error(f"❌ before_request initialization failed: {e}", exc_info=True)
            # We don't raise here so the request can still try to return an error page
            # instead of a generic 500 without our logs.

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    app.run(host='0.0.0.0', port=port)
