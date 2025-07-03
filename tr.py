import os
import random
import datetime
import asyncio
import re
import json
from io import BytesIO
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, CallbackContext
from telegram.error import NetworkError, RetryAfter
import logging

# === CONFIGURATION ===
TOKEN = "7579241847:AAEr_ZlDsfcD2Ouxd4f3a9FpEbztbFrIMS4"
ADMIN_ID = 6637067482
DATABASE_DIR = "database"
ACCESS_FILE = "access.json"
USER_DROPS_DIR = "userdrops"
DEPLETION_FILE = "depletion.json"
MAX_RETRIES = 3
RETRY_DELAY = 2

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Simplified Emoji configuration
EMOJI = {
    "admin": "👤",
    "popular": "⭐",
    "regular": "📄",
    "success": "✔️",
    "error": "❌",
    "warning": "⚠️",
    "info": "ℹ️",
    "lock": "🔒",
    "key": "🔑",
    "time": "⏱️",
    "loading": "↻",
    "database": "🗂️",
    "menu": "📋",
    "help": "❔",
    "start": "🤖",
    "depleted": "🔄",
    "vip": "🌟",
    "new": "🆕",
    "limited": "⏳",
    "security": "🔐",
    "download": "⬇️",
    "stats": "📈",
    "broadcast": "📢",
    "categories": "📋"
}

# Clean styling templates
STYLES = {
    "header": "━" * 30,
    "divider": "─" * 30,
    "footer": "━" * 30,
    "alert": "⚠️" * 5,
    "success_bar": "✔️" * 5
}

# Predefined categories with simple icons
PREDEFINED_CATEGORIES = {
    "GARENA": {"file": "garena.txt", "emoji": "🎮"},
    "100082": {"file": "100082.txt", "emoji": "🔢"},
    "GASLITE": {"file": "gaslite.txt", "emoji": "⛽"},
    "AUTHGOP": {"file": "authgop.txt", "emoji": "🔐"},
    "ML": {"file": "ml.txt", "emoji": "🤖"}
}

# Load additional database files from directory
additional_files = {
    filename[:-4]: {"file": filename, "emoji": "📄"} 
    for filename in os.listdir(DATABASE_DIR) 
    if filename.endswith('.txt') and filename not in [v["file"] for v in PREDEFINED_CATEGORIES.values()]
}

# Combine predefined and additional files
DATABASE_FILES = {**PREDEFINED_CATEGORIES, **additional_files}

ACCESS_KEYS = {}
USER_ACCESS = {}
LAST_GENERATE = {}
COOLDOWN_SECONDS = 10
DEPLETED_ITEMS = {}

# === UTILITY FUNCTIONS ===
async def safe_send_message(update, text, parse_mode="Markdown", **kwargs):
    """Send message with retry logic"""
    for attempt in range(MAX_RETRIES):
        try:
            if isinstance(update, Update):
                return await update.message.reply_text(text, parse_mode=parse_mode, **kwargs)
            else:  # CallbackQuery
                return await update.message.edit_text(text, parse_mode=parse_mode, **kwargs)
        except NetworkError as e:
            if attempt == MAX_RETRIES - 1:
                raise
            await asyncio.sleep(RETRY_DELAY * (attempt + 1))
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)

async def safe_send_document(update, document, caption=None, **kwargs):
    """Send document with retry logic"""
    for attempt in range(MAX_RETRIES):
        try:
            return await update.message.reply_document(document, caption=caption, **kwargs)
        except NetworkError as e:
            if attempt == MAX_RETRIES - 1:
                raise
            await asyncio.sleep(RETRY_DELAY * (attempt + 1))
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)

def load_access():
    global USER_ACCESS
    try:
        if os.path.exists(ACCESS_FILE):
            with open(ACCESS_FILE, "r") as f:
                data = json.load(f)
                USER_ACCESS = {int(k): (v if v is None else float(v)) for k, v in data.items()}
    except Exception as e:
        logger.error(f"Error loading access file: {e}")
        USER_ACCESS = {}

def save_access():
    try:
        with open(ACCESS_FILE, "w") as f:
            json.dump(USER_ACCESS, f)
    except Exception as e:
        logger.error(f"Error saving access file: {e}")

def load_depleted():
    global DEPLETED_ITEMS
    try:
        if os.path.exists(DEPLETION_FILE):
            with open(DEPLETION_FILE, "r") as f:
                DEPLETED_ITEMS = json.load(f)
    except Exception as e:
        logger.error(f"Error loading depletion file: {e}")
        DEPLETED_ITEMS = {}

def save_depleted():
    try:
        with open(DEPLETION_FILE, "w") as f:
            json.dump(DEPLETED_ITEMS, f)
    except Exception as e:
        logger.error(f"Error saving depletion file: {e}")

def format_message(title, message, message_type="info", style=None):
    emoji = EMOJI.get(message_type, "")
    style = style or {}
    
    template = style.get("template", """
{header}
{emoji} *{title}*
{divider}
{message}
{footer}
""")
    
    return template.format(
        header=style.get("header", STYLES["header"]),
        divider=style.get("divider", STYLES["divider"]),
        footer=style.get("footer", STYLES["footer"]),
        emoji=emoji,
        title=title,
        message=message
    )

def create_menu_buttons(items, columns=2):
    keyboard = []
    row = []
    for i, (name, data) in enumerate(items.items(), 1):
        emoji = data['emoji']
        if name in DEPLETED_ITEMS and len(DEPLETED_ITEMS[name]) >= get_total_items(name):
            emoji = EMOJI['depleted']
        btn = InlineKeyboardButton(
            text=f"{emoji} {name}",
            callback_data=f"generate:{name}"
        )
        row.append(btn)
        if i % columns == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    # Add refresh button at bottom
    keyboard.append([InlineKeyboardButton(f"{EMOJI['loading']} Refresh", callback_data="refresh_menu")])
    return keyboard

def get_total_items(category):
    try:
        file_path = os.path.join(DATABASE_DIR, DATABASE_FILES[category]["file"])
        if not os.path.exists(file_path):
            return 0
        with open(file_path, "r", encoding="utf-8") as f:
            return len([line for line in f if line.strip()])
    except Exception as e:
        logger.error(f"Error counting items in {category}: {e}")
        return 0

def get_available_items(category):
    try:
        if category not in DEPLETED_ITEMS:
            DEPLETED_ITEMS[category] = []
            save_depleted()
        
        file_path = os.path.join(DATABASE_DIR, DATABASE_FILES[category]["file"])
        if not os.path.exists(file_path):
            return []
        
        with open(file_path, "r", encoding="utf-8") as f:
            all_items = [line.strip() for line in f if line.strip()]
        
        return [item for item in all_items if item not in DEPLETED_ITEMS[category]]
    except Exception as e:
        logger.error(f"Error getting available items for {category}: {e}")
        return []

def generate_unique_key():
    """Create a more unique and secure key"""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    key_parts = [
        ''.join(random.choices(chars, k=4)),
        ''.join(random.choices(chars, k=4)),
        ''.join(random.choices(chars, k=4))
    ]
    return "-".join(key_parts)

def has_access(user_id):
    if user_id not in USER_ACCESS:
        return False
    if USER_ACCESS[user_id] is None:
        return True
    return USER_ACCESS[user_id] > datetime.datetime.now().timestamp()

# === COMMAND HANDLERS ===
async def start(update: Update, context: CallbackContext):
    try:
        await update.message.reply_chat_action(ChatAction.TYPING)
        await asyncio.sleep(1)
        
        welcome_msg = """
━ Welcome to Premium Generator ━

🤖 *Bot Commands:*
🔑 `/key <access_key>` - Activate your access
🗂️ `/generate` - Browse databases
📋 `/listcategories` - Show available categories
❔ `/help` - Show command guide
📈 `/stats` - Check your usage

🌟 *VIP Features:*
• Unique item generation
• Depletion tracking
• Priority access
━━━━━━━━━━━━━━━━━━━━━━
"""
        await safe_send_message(update, welcome_msg)
    except Exception as e:
        logger.error(f"Error in start handler: {e}")

async def help_command(update: Update, context: CallbackContext):
    try:
        help_msg = """
━ Command Reference Guide ━

*User Commands:*
🤖 `/start` - Welcome message
🔑 `/key <key>` - Activate access
🗂️ `/generate` - Database menu
📋 `/listcategories` - Show all categories
📈 `/stats` - Your usage stats

*Admin Commands:*
👤 `/genkey <time>` - Generate access key
👤 `/mykeys` - List active keys
👤 `/listaccess` - View active users
👤 `/revoke <id>` - Revoke access
👤 `/uploadfile` - Add new database
👤 `/resetdepleted` - Reset all items
📢 `/broadcast <message>` - Send to all users

🔐 *Note:* All commands are secured
━━━━━━━━━━━━━━━━━━━━━━
"""
        await safe_send_message(update, help_msg)
    except Exception as e:
        logger.error(f"Error in help handler: {e}")

async def generate_key(update: Update, context: CallbackContext):
    try:
        if update.message.from_user.id != ADMIN_ID:
            await safe_send_message(
                update,
                format_message(
                    "ACCESS DENIED",
                    "Admin privileges required!",
                    "error"
                )
            )
            return
            
        if len(context.args) == 0:
            usage_msg = """
━ Key Generation Usage ━

`/genkey <time>`
  
Examples:
• `/genkey 30m` - 30 minutes
• `/genkey 24h` - 24 hours
• `/genkey 7d` - 7 days
• `/genkey lifetime` - Permanent access
━━━━━━━━━━━━━━━━━━━━━━
"""
            await safe_send_message(update, usage_msg)
            return
            
        duration_text = context.args[0].lower()
        
        if duration_text == "lifetime":
            expires_at = None
            expiry_text = "🌟 Lifetime VIP Access"
        else:
            match = re.match(r"(\d+)([smhd])", duration_text)
            if not match:
                await safe_send_message(
                    update,
                    format_message(
                        "INVALID FORMAT",
                        "Use: 30m, 2h, 7d, or 'lifetime'",
                        "error"
                    )
                )
                return
                
            value, unit = int(match[1]), match[2]
            time_multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
            expires_at = (datetime.datetime.now() + datetime.timedelta(seconds=value * time_multipliers[unit])).timestamp()
            
            time_units = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
            expiry_text = f"⏱️ Expires in: {value}{unit} ({time_units[unit]})"
        
        key = generate_unique_key()
        ACCESS_KEYS[key] = {"expires_at": expires_at}
        
        key_msg = f"""
━ Premium Access Key Generated ━

```{key}```

{expiry_text}

*Share this with users:*
`/key {key}`
━━━━━━━━━━━━━━━━━━━━━━
"""
        await safe_send_message(update, key_msg)
    except Exception as e:
        logger.error(f"Error in generate_key handler: {e}")

async def enter_key(update: Update, context: CallbackContext):
    try:
        if len(context.args) == 0:
            await safe_send_message(
                update,
                format_message(
                    "KEY USAGE",
                    "Usage: `/key <access_key>`\nExample: `/key ABCD-1234-EFGH`",
                    "info"
                )
            )
            return
            
        key = context.args[0]
        user_id = update.message.from_user.id
        
        if key in ACCESS_KEYS:
            key_data = ACCESS_KEYS[key]
            
            if key_data["expires_at"] and key_data["expires_at"] < datetime.datetime.now().timestamp():
                del ACCESS_KEYS[key]
                await safe_send_message(
                    update,
                    format_message(
                        "KEY EXPIRED",
                        "This key is no longer valid!",
                        "error"
                    )
                )
                return
                
            USER_ACCESS[user_id] = key_data["expires_at"]
            save_access()
            del ACCESS_KEYS[key]
            
            if key_data["expires_at"] is None:
                expiry = "🌟 Lifetime VIP Access"
            else:
                expiry = f"⏱️ Expires: {datetime.datetime.fromtimestamp(key_data['expires_at']).strftime('%Y-%m-%d %H:%M')}"
                
            await safe_send_message(
                update,
                format_message(
                    "ACCESS GRANTED",
                    f"Welcome to VIP membership!\n\n{expiry}",
                    "success"
                )
            )
        else:
            await safe_send_message(
                update,
                format_message(
                    "INVALID KEY",
                    "The key is invalid, already used, or doesn't exist",
                    "error"
                )
            )
    except Exception as e:
        logger.error(f"Error in enter_key handler: {e}")

async def generate_menu(update: Update, context: CallbackContext):
    try:
        user_id = update.message.from_user.id
        
        if not has_access(user_id):
            await safe_send_message(
                update,
                format_message(
                    "ACCESS REQUIRED",
                    "Use `/key <access_key>` to unlock VIP features",
                    "lock"
                )
            )
            return
        
        keyboard = create_menu_buttons(DATABASE_FILES, columns=2)
        
        menu_msg = """
━ Premium Database Menu ━

⭐ Popular Categories (Top row)
🗂️ All Databases (Below)

🔄 = Depleted category
↻ = Refresh menu

Select a category to generate:
━━━━━━━━━━━━━━━━━━━━━━
"""
        await safe_send_message(
            update,
            menu_msg,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error in generate_menu handler: {e}")

async def list_categories(update: Update, context: CallbackContext):
    try:
        user_id = update.message.from_user.id
        if not has_access(user_id):
            await safe_send_message(
                update,
                format_message(
                    "ACCESS REQUIRED",
                    "Use `/key <access_key>` to unlock VIP features",
                    "lock"
                )
            )
            return

        categories_msg = "━ Available Categories ━\n\n"
        for category, data in DATABASE_FILES.items():
            total_items = get_total_items(category)
            available_items = len(get_available_items(category))
            emoji = data['emoji']
            if available_items == 0:
                emoji = EMOJI['depleted']
            categories_msg += (
                f"{emoji} *{category}*: "
                f"{available_items}/{total_items} items\n"
            )

        categories_msg += f"\n{STYLES['footer']}"
        await safe_send_message(update, categories_msg)
    except Exception as e:
        logger.error(f"Error in list_categories handler: {e}")

async def list_keys(update: Update, context: CallbackContext):
    try:
        if update.message.from_user.id != ADMIN_ID:
            await safe_send_message(
                update,
                format_message(
                    "ACCESS DENIED",
                    "Admin privileges required!",
                    "error"
                )
            )
            return

        if not ACCESS_KEYS:
            await safe_send_message(
                update,
                format_message(
                    "NO ACTIVE KEYS",
                    "No access keys have been generated yet.",
                    "info"
                )
            )
            return

        keys_msg = "━ Active Access Keys ━\n\n"
        for key, data in ACCESS_KEYS.items():
            if data["expires_at"] is None:
                expiry = "🌟 Lifetime"
            else:
                expiry = f"⏱️ {datetime.datetime.fromtimestamp(data['expires_at']).strftime('%Y-%m-%d %H:%M')}"
            keys_msg += f"🔑 `{key}`\n   {expiry}\n\n"

        keys_msg += STYLES['footer']
        await safe_send_message(update, keys_msg)
    except Exception as e:
        logger.error(f"Error in list_keys handler: {e}")

async def broadcast_message(update: Update, context: CallbackContext):
    try:
        if update.message.from_user.id != ADMIN_ID:
            await safe_send_message(
                update,
                format_message(
                    "ACCESS DENIED",
                    "Admin privileges required!",
                    "error"
                )
            )
            return

        if len(context.args) == 0:
            await safe_send_message(
                update,
                format_message(
                    "BROADCAST USAGE",
                    "Usage: `/broadcast <message>`",
                    "info"
                )
            )
            return

        message = " ".join(context.args)
        confirm_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm", callback_data=f"broadcast_confirm:{message}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="broadcast_cancel")]
        ])

        await safe_send_message(
            update,
            format_message(
                "CONFIRM BROADCAST",
                f"Message to send:\n\n{message}",
                "warning"
            ),
            reply_markup=confirm_keyboard
        )
    except Exception as e:
        logger.error(f"Error in broadcast_message handler: {e}")

async def callback_handler(update: Update, context: CallbackContext):
    try:
        query = update.callback_query
        user_id = query.from_user.id
        await query.answer()

        if query.data == "refresh_menu":
            await generate_menu(query.message, context)
            return

        if query.data.startswith("broadcast_confirm:"):
            _, message = query.data.split(":", 1)
            users = list(USER_ACCESS.keys())
            success = 0
            for user_id in users:
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=format_message(
                            "📢 ADMIN BROADCAST",
                            message,
                            "alert"
                        )
                    )
                    success += 1
                except Exception as e:
                    logger.error(f"Failed to send to {user_id}: {e}")
            await query.edit_message_text(
                f"Broadcast completed!\nSuccess: {success}/{len(users)} users."
            )
            return

        if query.data == "broadcast_cancel":
            await query.edit_message_text("Broadcast canceled.")
            return

        if query.data.startswith("generate:"):
            now = datetime.datetime.now().timestamp()
            if user_id in LAST_GENERATE and (now - LAST_GENERATE[user_id]) < COOLDOWN_SECONDS:
                remaining = COOLDOWN_SECONDS - int(now - LAST_GENERATE[user_id])
                await query.answer(
                    f"⏱️ Cooldown: Please wait {remaining}s",
                    show_alert=True
                )
                return
                
            LAST_GENERATE[user_id] = now
            _, category = query.data.split(':')
            
            available_items = get_available_items(category)
            total_items = get_total_items(category)
            
            if len(available_items) == 0:
                await query.answer(
                    "🔄 Category empty! Admin must reset",
                    show_alert=True
                )
                return

            # Loading steps
            steps = [
                "↻ Connecting to database server...",
                "↻ Validating txt files...",
                "↻ Generating txt...",
                "↻ Finalizing..."
            ]
            
            for step in steps:
                try:
                    await safe_send_message(
                        query,
                        format_message(
                            "PROCESSING",
                            step,
                            "info"
                        )
                    )
                except Exception as e:
                    logger.error(f"Error during loading: {e}")
                await asyncio.sleep(1.2)

            selected = random.sample(available_items, min(100, len(available_items)))
            DEPLETED_ITEMS[category].extend(selected)
            save_depleted()
            
            remaining = total_items - len(DEPLETED_ITEMS[category])
            percent_remaining = (remaining / total_items) * 100 if total_items > 0 else 0
            
            memory_file = BytesIO()
            memory_file.write("\n".join(selected).encode("utf-8"))
            memory_file.name = f"{category}_{datetime.datetime.now().strftime('%Y%m%d')}.txt"
            memory_file.seek(0)

            # Status indicator
            if percent_remaining > 50:
                status_emoji = "🟢"
            elif percent_remaining > 20:
                status_emoji = "🟡"
            else:
                status_emoji = "🔴"
            
            await safe_send_document(
                query,
                memory_file,
                caption=format_message(
                    "GENERATION COMPLETE",
                    f"""📌 *Category:* `{category}`
📊 *Status:* {status_emoji} {remaining}/{total_items} ({percent_remaining:.1f}%)
📥 *Items Generated:* {len(selected)}

✔️ Fresh items ready for use!""",
                    "success"
                ),
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"Error in callback handler: {e}")
        try:
            await query.answer("An error occurred. Please try again.", show_alert=True)
        except:
            pass

async def reset_depleted(update: Update, context: CallbackContext):
    try:
        if update.message.from_user.id != ADMIN_ID:
            await safe_send_message(
                update,
                format_message(
                    "ACCESS DENIED",
                    "Admin privileges required!",
                    "error"
                )
            )
            return
            
        global DEPLETED_ITEMS
        DEPLETED_ITEMS = {}
        save_depleted()
        
        await safe_send_message(
            update,
            format_message(
                "RESET COMPLETE",
                "All categories have been restocked!",
                "success"
            )
        )
    except Exception as e:
        logger.error(f"Error in reset_depleted handler: {e}")

async def stats_command(update: Update, context: CallbackContext):
    try:
        user_id = update.message.from_user.id
        
        if not has_access(user_id):
            await safe_send_message(
                update,
                format_message(
                    "ACCESS REQUIRED",
                    "Use `/key <access_key>` to view stats",
                    "lock"
                )
            )
            return
        
        # Calculate user stats
        total_categories = len(DATABASE_FILES)
        available_categories = sum(
            1 for cat in DATABASE_FILES 
            if cat not in DEPLETED_ITEMS or len(DEPLETED_ITEMS[cat]) < get_total_items(cat)
        )
        
        if USER_ACCESS[user_id] is None:
            expiry = "Lifetime VIP Access"
        else:
            expiry = f"Until {datetime.datetime.fromtimestamp(USER_ACCESS[user_id]).strftime('%Y-%m-%d %H:%M')}"
        
        await safe_send_message(
            update,
            format_message(
                "YOUR STATS",
                f"""🔹 *Access Level:* Premium VIP
🔹 *Expiry:* {expiry}
🔹 *Categories Available:* {available_categories}/{total_categories}
🔹 *Last Generated:* {datetime.datetime.fromtimestamp(LAST_GENERATE.get(user_id, 0)).strftime('%Y-%m-%d %H:%M') if user_id in LAST_GENERATE else 'Never'}

📈 *Usage Tips:*
• Check menu for available categories
• Depleted categories show 🔄
• Admin can reset depleted items""",
                "info"
            )
        )
    except Exception as e:
        logger.error(f"Error in stats handler: {e}")

async def error_handler(update: object, context: CallbackContext) -> None:
    """Log errors caused by Updates."""
    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)

# === MAIN ===
def main():
    # Load data
    load_access()
    load_depleted()
    
    # Create application
    app = Application.builder().token(TOKEN).build()
    
    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("genkey", generate_key))
    app.add_handler(CommandHandler("key", enter_key))
    app.add_handler(CommandHandler("generate", generate_menu))
    app.add_handler(CommandHandler("listcategories", list_categories))
    app.add_handler(CommandHandler("mykeys", list_keys))
    app.add_handler(CommandHandler("broadcast", broadcast_message))
    app.add_handler(CommandHandler("resetdepleted", reset_depleted))
    app.add_handler(CommandHandler("stats", stats_command))
    
    # Callback handler
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    # Error handler
    app.add_error_handler(error_handler)
    
    # Start polling
    logger.info("Starting bot...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()