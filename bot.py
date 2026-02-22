import os
import logging
import asyncio
from dotenv import load_dotenv
from telegram.ext import Application, MessageHandler, filters
from telegram import Update
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
import hashlib
from datetime import datetime, timedelta
import signal
import sys
import pytz
import urllib.parse as urlparse
from flask import Flask
import threading

# Flask app untuk health check (HuggingFace/Koyeb)
server = Flask(__name__)

@server.route('/')
def health():
    return "Bot is alive!", 200

def run_server():
    port = int(os.getenv('PORT', 7860))
    server.run(host='0.0.0.0', port=port)

# Setup logging
try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

class ProductionDuplicateBot:
    def __init__(self):
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
        load_dotenv(env_path)
        self.token = os.getenv('BOT_TOKEN', '').strip()
        if not self.token:
            raise ValueError("❌ BOT_TOKEN environment variable not set")
            
        # Network Check
        try:
            import socket
            hostname = "api.telegram.org"
            ip = socket.gethostbyname(hostname)
            logger.info(f"🌐 DNS Check: {hostname} resolved to {ip}")
        except Exception as e:
            logger.warning(f"⚠️ DNS Check Failed for {hostname}: {e}")
            
        self.app = Application.builder().token(self.token).build()
        self.setup_database()
        self.setup_handlers()
        self.setup_error_handler()
        self.setup_signal_handlers()
        
        logger.info("🤖 Bot initialized successfully")
        
    def setup_database(self):
        """Setup database (PostgreSQL if DATABASE_URL exists, otherwise SQLite)"""
        db_url = os.getenv('DATABASE_URL')
        
        if db_url:
            logger.info("🔌 Connecting to PostgreSQL (Supabase)...")
            self.db_type = 'postgresql'
            self.conn = psycopg2.connect(db_url)
            self.conn.autocommit = True
        else:
            logger.info("📁 Connecting to SQLite...")
            self.db_type = 'sqlite'
            db_path = os.getenv('DB_PATH', 'messages.db')
            self.conn = sqlite3.connect(db_path, check_same_thread=False)
            
        cursor = self.conn.cursor()
        
        if self.db_type == 'postgresql':
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT,
                    message_hash TEXT,
                    message_text TEXT,
                    user_id BIGINT,
                    timestamp TIMESTAMP,
                    user_name TEXT DEFAULT 'Unknown',
                    UNIQUE(chat_id, message_hash)
                )
            ''')
        else:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    message_hash TEXT,
                    message_text TEXT,
                    user_id INTEGER,
                    timestamp DATETIME,
                    UNIQUE(chat_id, message_hash)
                )
            ''')
            # Migrasi SQLite: Tambahkan kolom user_name jika belum ada
            try:
                cursor.execute('ALTER TABLE messages ADD COLUMN user_name TEXT DEFAULT "Unknown"')
            except sqlite3.OperationalError:
                pass # Kolom sudah ada
        
        logger.info(f"📊 Database initialized ({self.db_type})")
        
    def setup_handlers(self):
        """Setup handler untuk pesan teks"""
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
    def setup_error_handler(self):
        """Handle errors untuk production"""
        async def error_handler(update: Update, context):
            logger.error(f"Error: {context.error}")
            
        self.app.add_error_handler(error_handler)
        
    def setup_signal_handlers(self):
        """Handle shutdown signals"""
        def signal_handler(signum, frame):
            logger.info("🛑 Received shutdown signal")
            asyncio.create_task(self.graceful_shutdown())
            
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
    async def graceful_shutdown(self):
        """Shutdown yang graceful"""
        logger.info("🔚 Shutting down gracefully...")
        self.conn.close()
        await self.app.shutdown()
        sys.exit(0)
        
    def generate_message_hash(self, text):
        """Generate hash untuk pesan untuk deteksi duplikat"""
        normalized_text = ' '.join(text.lower().split())
        return hashlib.md5(normalized_text.encode()).hexdigest()
        
    async def handle_message(self, update: Update, context):
        """Handle incoming messages"""
        try:
            message = update.message
            if not message or not message.text:
                return
                
            chat_id = message.chat_id
            user_id = message.from_user.id
            user_name = message.from_user.first_name if message.from_user.first_name else str(user_id)
            message_text = message.text
            
            # Skip jika pesan terlalu pendek (dikurangi jadi 5 agar fitur No HP terdeteksi)
            if len(message_text.strip()) < 5:  
                return
                
            message_hash = self.generate_message_hash(message_text)
            
            cursor = self.conn.cursor()
            
            # Pengecekan 24 jam terakhir (Sintaks berbeda antara PG dan SQLite)
            if self.db_type == 'postgresql':
                query = '''
                    SELECT user_id, message_text, timestamp, user_name 
                    FROM messages 
                    WHERE chat_id = %s AND message_hash = %s 
                    AND timestamp > now() - interval '1 day'
                '''
                cursor.execute(query, (chat_id, message_hash))
            else:
                query = '''
                    SELECT user_id, message_text, timestamp, user_name 
                    FROM messages 
                    WHERE chat_id = ? AND message_hash = ? 
                    AND timestamp > datetime('now', '-1 day')
                '''
                cursor.execute(query, (chat_id, message_hash))
            
            existing_message = cursor.fetchone()
            
            if existing_message:
                original_user_id, original_text, original_time, original_user_name = existing_message
                
                # Format Waktu: 2026/02/22 10:21:23
                tz_jakarta = pytz.timezone('Asia/Jakarta')
                
                if isinstance(original_time, str):
                    original_time_dt = datetime.strptime(original_time, '%Y-%m-%d %H:%M:%S')
                else:
                    original_time_dt = original_time # PostgreSQL returns datetime object
                
                original_time_str = original_time_dt.strftime('%Y/%m/%d %H:%M:%S')
                current_time_str = datetime.now(tz_jakarta).strftime('%Y/%m/%d %H:%M:%S')
                
                response_message = (
                    f"❌Nomor sudah pernah bergabung❌\n"
                    f"Nomor yang terdeteksi: {original_text}\n"
                    f"{original_user_name} : {original_time_str} (pertama kali)\n"
                    f"{user_name} : {current_time_str} (kali ini)"
                )
                
                msg = await message.reply_text(response_message)
                logger.info(f"🚫 Duplicate detected in chat {chat_id}")
            else:
                # Simpan pesan baru
                tz_jakarta = pytz.timezone('Asia/Jakarta')
                current_time = datetime.now(tz_jakarta).strftime('%Y-%m-%d %H:%M:%S')
                
                if self.db_type == 'postgresql':
                    cursor.execute('''
                        INSERT INTO messages 
                        (chat_id, message_hash, message_text, user_id, timestamp, user_name)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (chat_id, message_hash) DO NOTHING
                    ''', (chat_id, message_hash, message_text, user_id, current_time, user_name))
                else:
                    cursor.execute('''
                        INSERT OR REPLACE INTO messages 
                        (chat_id, message_hash, message_text, user_id, timestamp, user_name)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (chat_id, message_hash, message_text, user_id, current_time, user_name))
                    self.conn.commit()
                
            # Bersihkan pesan lama
            if self.db_type == 'postgresql':
                cursor.execute("DELETE FROM messages WHERE timestamp < now() - interval '7 days'")
            else:
                cursor.execute('DELETE FROM messages WHERE timestamp < datetime("now", "-7 days")')
                self.conn.commit()
            
        except Exception as e:
            logger.error(f"Error handling message: {e}")
        
    def run_polling(self):
        """Jalankan dengan polling (untuk development)"""
        logger.info("🔄 Starting bot with polling...")
        self.app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )
        
    def run_webhook(self):
        """Jalankan dengan webhook (untuk production)"""
        webhook_url = os.getenv('WEBHOOK_URL')
        port = int(os.getenv('PORT', 8080))
        
        if not webhook_url:
            logger.warning("⚠️ WEBHOOK_URL not set, falling back to polling")
            return self.run_polling()
            
        logger.info(f"🌐 Starting bot with webhook: {webhook_url}")
        self.app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=self.token,
            webhook_url=f"{webhook_url}/{self.token}",
            drop_pending_updates=True
        )

if __name__ == "__main__":
    try:
        # Jalankan server flask di thread terpisah agar bot tidak mati di HuggingFace
        threading.Thread(target=run_server, daemon=True).start()
        
        bot = ProductionDuplicateBot()
        
        # Pilih mode berdasarkan environment
        if os.getenv('USE_WEBHOOK', 'false').lower() == 'true':
            bot.run_webhook()
        else:
            bot.run_polling()
            
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
        sys.exit(1)
