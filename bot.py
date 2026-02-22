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

# 2. Flask Setup
app = Flask(__name__)

# 3. Load Environment
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()
DATABASE_URL = os.getenv('DATABASE_URL')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN not set!")

# 4. Telegram Application Setup
telegram_app = Application.builder().token(BOT_TOKEN).build()

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    try:
        conn = get_db_connection()
        conn.autocommit = True
        cursor = conn.cursor()
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
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_chat_hash ON messages(chat_id, message_hash)')
        conn.close()
        logger.info("📊 Database initialized")
    except Exception as e:
        logger.error(f"❌ DB Init Error: {e}")

# Call init
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
        
        # Store current occurrence
        cursor.execute(
            "INSERT INTO messages (chat_id, message_hash, message_text, user_id, timestamp, user_name) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (chat_id, msg_hash, text, user_id, datetime.now(), user_name)
        )
        conn.commit()

        # Fetch all history
        cursor.execute(
            "SELECT user_name, timestamp FROM messages WHERE chat_id = %s AND message_hash = %s ORDER BY timestamp ASC",
            (chat_id, msg_hash)
        )
        history = cursor.fetchall()
        
        if len(history) > 1:
            msg_parts = ["❌**DETEKSI DITEMUKAN**❌", f"Isi pesan : {text}", ""]
            for i, (u_name, u_time) in enumerate(history):
                time_str = u_time.strftime("%H:%M:%S")
                if i == 0:
                    label = "Pengirim pertama kali"
                elif i == len(history) - 1:
                    label = "Pengirim saat ini"
                elif i == 1:
                    label = "pengirim kedua kali"
                else:
                    label = f"pengirim ke-{i+1}"
                msg_parts.append(f"{u_name} : {label} {time_str}")
            await update.message.reply_text("\n".join(msg_parts), parse_mode='Markdown')
        conn.close()
    except Exception as e:
        logger.error(f"❌ Error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Bot Aktif!")

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), check_duplicate))

# 6. Webhook Routes
@app.route('/', methods=['GET', 'HEAD'])
def index():
    return "Bot is running!", 200

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
async def webhook():
    if request.method == "POST":
        try:
            update = Update.de_json(request.get_json(force=True), telegram_app.bot)
            # Initialize app if needed (Render/Gunicorn compatibility)
            if not telegram_app._initialized:
                await telegram_app.initialize()
            await telegram_app.process_update(update)
            return "OK", 200
        except Exception as e:
            logger.error(f"❌ Webhook Error: {e}")
            return "Error", 500
    return "Invalid", 400

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    app.run(host='0.0.0.0', port=port)
