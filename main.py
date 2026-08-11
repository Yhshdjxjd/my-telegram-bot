import os
import json
import asyncio
import logging
import re
import base64
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

from flask import Flask, jsonify, send_file
from flask_cors import CORS
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv
import pyotp
import threading

# Load environment variables
load_dotenv()

# ==========================================
# Logging Setup
# ==========================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==========================================
# Flask App Setup
# ==========================================
app = Flask(__name__)
CORS(app)
PORT = int(os.environ.get('PORT', 3000))

# ==========================================
# Firebase Setup
# ==========================================
db = None
try:
    if os.getenv('FIREBASE_SERVICE_ACCOUNT'):
        service_account = json.loads(os.getenv('FIREBASE_SERVICE_ACCOUNT'))
        cred = credentials.Certificate(service_account)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info('Firebase initialized successfully.')
    else:
        logger.warning('FIREBASE_SERVICE_ACCOUNT is not set.')
except Exception as e:
    logger.error(f'Failed to initialize Firebase: {e}')

# ==========================================
# Constants
# ==========================================
MAIN_CHANNEL = "https://t.me/income_box_x"
SUPPORT_CHANNEL = os.getenv('SUPPORT_CHANNEL_ID', '-1003951413076')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# User states storage
user_states: Dict[int, Dict[str, Any]] = {}

# ==========================================
# Background Tasks Checker
# ==========================================
async def check_reviewed_tasks(application: Application):
    """Admin প্যানেল থেকে Approve/Reject করলে ইউজারকে নোটিফিকেশন দেওয়ার ব্যাকগ্রাউন্ড টাস্ক"""
    while True:
        try:
            if db:
                # টাস্ক রিভিউ চেক করা
                tasks = db.collection('completed_tasks').where('notified', '==', False).get()
                for task in tasks:
                    data = task.to_dict()
                    status = data.get('review_status', 'pending')
                    
                    if status in ['approved', 'rejected']:
                        user_id = data.get('user_id') or data.get('userId')
                        platform = data.get('platform', 'Unknown')
                        price = data.get('price', 0.0)

                        if user_id:
                            if status == 'approved':
                                text = f"✅ <b>আপনার {platform} কাজটি অনুমোদিত হয়েছে!</b>\n💰 আপনার অ্যাকাউন্টে {price} BDT যোগ করা হয়েছে।"
                            else:
                                reason = data.get('reject_reason', 'সঠিক তথ্য প্রদান না করার কারণে')
                                text = f"❌ <b>আপনার {platform} কাজটি প্রত্যাখ্যাত হয়েছে!</b>\n⚠️ কারণ: {reason}"
                            
                            try:
                                await application.bot.send_message(chat_id=int(user_id), text=text, parse_mode='HTML')
                            except Exception as e:
                                logger.error(f"Failed to send notification to {user_id}: {e}")

                        # ডাটাবেজে notified আপডেট করা
                        db.collection('completed_tasks').document(task.id).update({'notified': True})

                        # ব্যালেন্স আপডেট (Approve হলে ব্যালেন্স অ্যাড হবে - Concurrency Safe)
                        if status == 'approved' and user_id:
                            user_ref = db.collection('users').document(str(user_id))
                            try:
                                user_ref.set({
                                    'total_earned': firestore.Increment(float(price)),
                                    'successful_tasks': firestore.Increment(1)
                                }, merge=True)
                            except Exception as e:
                                logger.error(f"Error updating balance for {user_id}: {e}")
                                
                # উইথড্র রিকোয়েস্ট চেক করা
                withdrawals = db.collection('withdrawals').where('notified', '==', False).get()
                for w in withdrawals:
                    data = w.to_dict()
                    status = data.get('status', 'pending')
                    if status in ['approved', 'rejected']:
                        user_id = data.get('user_id')
                        amount = data.get('amount', 0)
                        if user_id:
                            if status == 'approved':
                                text = f"✅ <b>আপনার {amount} টাকার উত্তোলন অনুমোদিত হয়েছে!</b>\nটাকা আপনার অ্যাকাউন্টে পাঠানো হয়েছে।"
                            else:
                                reason = data.get('reject_reason', 'অ্যাকাউন্টে সমস্যা বা ভুল নাম্বার')
                                text = f"❌ <b>আপনার {amount} টাকার উত্তোলন বাতিল করা হয়েছে!</b>\n⚠️ কারণ: {reason}\nটাকা আপনার ব্যালেন্সে রিফান্ড করা হয়েছে।"
                            
                            try:
                                await application.bot.send_message(chat_id=int(user_id), text=text, parse_mode='HTML')
                            except Exception:
                                pass
                        db.collection('withdrawals').document(w.id).update({'notified': True})
                        
        except Exception as e:
            logger.error(f"Error checking reviewed tasks: {e}")

        # প্রতি ১০ সেকেন্ড পরপর চেক করবে
        await asyncio.sleep(10)

async def post_init(application: Application):
    """বট স্টার্ট হওয়ার পর ব্যাকগ্রাউন্ড টাস্ক চালু করা"""
    asyncio.create_task(check_reviewed_tasks(application))


# ==========================================
# Helper functions & Handlers
# ==========================================
def get_field(data: dict, *keys: str) -> str:
    for key in keys:
        val = data.get(key, '')
        if val:
            return str(val)
    return ''

def process_2fa_key(key_string: str) -> str:
    key = re.sub(r'\s+', '', key_string.upper())
    if 'otpauth://' in key_string:
        parsed = urllib.parse.urlparse(key_string)
        params = urllib.parse.parse_qs(parsed.query)
        if 'secret' in params:
            return params['secret'][0]
    try:
        base64.b32decode(key)
        return key
    except:
        return key

def generate_totp_code(secret_key: str) -> str:
    try:
        totp = pyotp.TOTP(secret_key)
        return totp.now()
    except Exception as e:
        logger.error(f"TOTP generation error: {e}")
        return None

def get_main_keyboard():
    keyboard = [
        [KeyboardButton("💰 ব্যালেন্স"), KeyboardButton("💼 কাজ")],
        [KeyboardButton("💸 উত্তোলনের অনুরোধ"), KeyboardButton("🎧 সাপোর্ট")],
        [KeyboardButton("🎁 আমার রেফারেল"), KeyboardButton("🔄 আমি নতুন")],
        [KeyboardButton("🌐 ভাষা পরিবর্তন")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def check_membership(bot, user_id: int) -> bool:
    try:
        main_member = await bot.get_chat_member(MAIN_CHANNEL, user_id)
        main_valid = main_member.status in ['member', 'administrator', 'creator', 'restricted']

        support_valid = False
        try:
            support_member = await bot.get_chat_member(SUPPORT_CHANNEL, user_id)
            support_valid = support_member.status in ['member', 'administrator', 'creator', 'restricted']
        except Exception as e:
            logger.error(f"Support channel check error: {e}")

        return main_valid and support_valid
    except Exception as e:
        logger.error(f"Membership check error: {e}")
        return False

async def clear_user_state(user_id: int, bot, chat_id: int = None):
    if user_id in user_states:
        state = user_states[user_id]
        if state.get('timeout_task'):
            try:
                state['timeout_task'].cancel()
            except Exception:
                pass
        if state.get('task_doc_id') and db:
            try:
                db.collection('tasks').document(state['task_doc_id']).update({'status': 'pending'})
            except Exception as e:
                logger.error(e)
        del user_states[user_id]

    if chat_id:
        await bot.send_message(
            chat_id,
            "❌ <b>কাজ বাতিল করা হয়েছে।</b>",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )

# ==========================================
# Application Handlers
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    user = update.effective_user

    await clear_user_state(user_id, context.bot)

    if db:
        user_ref = db.collection('users').document(str(user_id))
        user_doc = user_ref.get()
        if not user_doc.exists:
            referred_by = None
            if context.args and context.args[0] != str(user_id):
                referred_by = context.args[0]
            user_ref.set({
                'first_name': user.first_name or '',
                'last_name': user.last_name or '',
                'username': user.username or '',
                'referred_by': referred_by,
                'referral_bonus_paid': False,
                'total_earned': 0.0,
                'successful_tasks': 0,
                'joined_at': firestore.SERVER_TIMESTAMP
            })

    is_member = await check_membership(context.bot, user_id)

    if is_member:
        first_name = user.first_name or 'ইউজার'
        await context.bot.send_message(
            chat_id,
            f"📌 <b>স্বাগতম, {first_name}!</b>\n\n💎 <b>কাজ শুরু করতে নিচের অপশনগুলো ব্যবহার করুন</b> 🔽",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
        return

    inline_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 জয়েন করুন: মেইন চ্যানেল", url="https://t.me/income_box1")],
        [InlineKeyboardButton("📢 জয়েন করুন: Support", url="https://t.me/+MDnz3-C-7FkzZDY1")],
        [InlineKeyboardButton("✅ Verify (ভেরিফাই)", callback_data="verify_join")]
    ])
    await context.bot.send_message(
        chat_id,
        "✅ <b>বটটি ব্যবহার করতে হলে আপনাকে আমাদের অফিশিয়াল চ্যানেলগুলোতে জয়েন করতে হবে!</b>\n\nনিচের চ্যানেলগুলোতে জয়েন করে 'Verify' বাটনে ক্লিক করুন।",
        parse_mode='HTML',
        reply_markup=inline_keyboard
    )

async def verify_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    user_id = query.from_user.id
    user = query.from_user

    is_member = await check_membership(context.bot, user_id)

    if is_member:
        await query.answer("✅ ভেরিফিকেশন সফল হয়েছে!", show_alert=True)
        await context.bot.delete_message(chat_id, query.message.message_id)
        first_name = user.first_name or 'ইউজার'
        await context.bot.send_message(
            chat_id,
            f"📌 <b>স্বাগতম, {first_name}!</b>\n\n💎 <b>কাজ শুরু করতে নিচের অপশনগুলো ব্যবহার করুন</b> 🔽",
            parse_mode='HTML',
            reply_markup=get_main_keyboard()
        )
    else:
        await query.answer("ভেরিফিকেশন ব্যর্থ হয়েছে!", show_alert=True)
        await context.bot.send_message(
            chat_id,
            "🚫 <b>আপনি এখনো চ্যানেলগুলোতে জয়েন করেননি! দয়া করে জয়েন করুন।</b>",
            parse_mode='HTML'
        )

async def handle_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not db:
        await context.bot.send_message(chat_id, "⚠️ ডাটাবেস সংযুক্ত নেই।", parse_mode='HTML')
        return

    try:
        user_doc = db.collection('users').document(str(user_id)).get()
        user_data = user_doc.to_dict() or {}
        total_earned = user_data.get('total_earned', 0.0)
        successful_tasks = user_data.get('successful_tasks', 0)

        tasks_snapshot = db.collection('completed_tasks').where('user_id', '==', user_id).get()
        pending_tasks = sum(1 for doc in tasks_snapshot if doc.to_dict().get('review_status') == 'pending')

        withdrawals_snapshot = db.collection('withdrawals').where('user_id', '==', user_id).get()
        total_withdrawn = 0.0
        pending_withdrawal = 0.0
        for doc in withdrawals_snapshot:
            data = doc.to_dict()
            if data.get('status') == 'approved':
                total_withdrawn += data.get('amount', 0.0)
            if data.get('status') == 'pending':
                pending_withdrawal += data.get('amount', 0.0)

        current_balance = total_earned - total_withdrawn - pending_withdrawal

        balance_text = f"""💵 <b>আপনার ব্যালেন্স ড্যাশবোর্ড</b>
────────────────────
💰 <b>মূল ব্যালেন্স:</b> {current_balance:.2f} BDT
⏳ <b>উত্তোলন (পেন্ডিং):</b> {pending_withdrawal:.2f} BDT
📈 <b>সর্বমোট আয়:</b> {total_earned:.2f} BDT
────────────────────
✅ <b>সফল কাজ:</b> {successful_tasks} টি
🔄 <b>পেন্ডিং কাজ:</b> {pending_tasks} টি"""

        await context.bot.send_message(chat_id, balance_text, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Error fetching balance: {e}")
        await context.bot.send_message(chat_id, "⚠️ ব্যালেন্স লোড করতে সমস্যা হয়েছে।")

async def handle_tasks_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not db:
        await context.bot.send_message(chat_id, "⚠️ ডাটাবেস সংযুক্ত নেই।", parse_mode='HTML')
        return

    is_member = await check_membership(context.bot, user_id)
    if not is_member:
        await context.bot.send_message(chat_id, "⚠️ <b>দয়া করে প্রথমে চ্যানেলে জয়েন করুন!</b>", parse_mode='HTML')
        return

    # Fetch settings from Firebase dynamically
    settings_doc = db.collection('settings').document('app_settings').get()
    settings = settings_doc.to_dict() if settings_doc.exists else {}

    # Default value set to False so no demo tasks appear before admin turns them on
    ig_set = settings.get('instagram', {'enabled': False, 'price': 0.0})
    fb_set = settings.get('facebook', {'enabled': False, 'price': 0.0})
    gm_set = settings.get('gmail', {'enabled': False, 'price': 0.0})

    keyboard = []
    
    if ig_set.get('enabled', False):
        keyboard.append([KeyboardButton(f"📸 ইনস্টাগ্রাম কাজ (৳{ig_set.get('price', 0.0):.2f})")])
        
    if fb_set.get('enabled', False):
        keyboard.append([KeyboardButton(f"📘 ফেসবুক কাজ (৳{fb_set.get('price', 0.0):.2f})")])
        
    if gm_set.get('enabled', False):
        keyboard.append([KeyboardButton(f"📧 জিমেইল কাজ (৳{gm_set.get('price', 0.0):.2f})")])

    keyboard.append([KeyboardButton("❌ বাতিল")])

    if len(keyboard) == 1:
        await context.bot.send_message(chat_id, "😔 <b>বর্তমানে কোনো প্ল্যাটফর্মের কাজ চালু নেই। এডমিন আপডেট দিলে কাজ শুরু হবে।</b>", parse_mode='HTML')
    else:
        await context.bot.send_message(
            chat_id,
            "👨‍💻 <b>যেকোনো একটি কাজ সিলেক্ট করুন:</b> ⬇️",
            parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )

# ==========================================
# Withdrawals
# ==========================================
async def handle_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not db:
        return

    try:
        user_doc = db.collection('users').document(str(user_id)).get()
        user_data = user_doc.to_dict() or {}
        total_earned = user_data.get('total_earned', 0.0)

        withdrawals_snapshot = db.collection('withdrawals').where('user_id', '==', user_id).get()
        total_withdrawn = 0.0
        pending_withdrawal = 0.0
        for doc in withdrawals_snapshot:
            data = doc.to_dict()
            if data.get('status') == 'approved':
                total_withdrawn += data.get('amount', 0.0)
            if data.get('status') == 'pending':
                pending_withdrawal += data.get('amount', 0.0)

        current_balance = total_earned - total_withdrawn - pending_withdrawal

        if current_balance < 50:
            await context.bot.send_message(
                chat_id,
                f"❌ <b>আপনার একাউন্টে ন্যূনতম ৫০ টাকা থাকতে হবে।</b>\nআপনার বর্তমান ব্যালেন্স: {current_balance:.2f} BDT",
                parse_mode='HTML'
            )
        else:
            await context.bot.send_message(
                chat_id,
                f"আপনার বর্তমান ব্যালেন্স <b>{current_balance:.2f} টাকা</b>\n\nআপনি কত টাকা উত্তোলন করতে চান তা ইংরেজিতে নিচে টাইপ করুন (যেমন: 50):",
                parse_mode='HTML',
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ বাতিল")]], resize_keyboard=True)
            )
            user_states[user_id] = {'step': 'AWAITING_WITHDRAWAL_AMOUNT'}
    except Exception as e:
        logger.error(f"Error initiating withdrawal: {e}")
        await context.bot.send_message(chat_id, "⚠️ এরর হয়েছে, আবার চেষ্টা করুন।")

# ==========================================
# Task Assigners (IG, FB, Gmail)
# ==========================================
def get_dynamic_password() -> str:
    tz = timezone(timedelta(hours=6))
    now = datetime.now(tz)
    day = f"{now.day:02d}"
    return f"Forhad@{day}"

@firestore.transactional
def _assign_task_tx(transaction, platform, user_id):
    tasks_ref = db.collection('tasks').where('platform', '==', platform)
    docs = tasks_ref.stream(transaction=transaction)
    for doc in docs:
        data = doc.to_dict()
        if data.get('status') == 'pending' and user_id not in data.get('attempted_by', []):
            transaction.update(doc.reference, {'status': 'assigned'})
            return doc, data
    return None, None

async def assign_task(platform: str, chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    if not db:
        return
        
    try:
        transaction = db.transaction()
        task_doc, task_data = _assign_task_tx(transaction, platform, user_id)

        if not task_doc:
            await context.bot.send_message(
                chat_id,
                f"😔 <b>দুঃখিত, বর্তমানে {platform} এর নতুন কোনো কাজ নেই। এডমিন কাজ অ্যাড করলে আবার চেষ্টা করুন।</b>",
                parse_mode='HTML',
                reply_markup=get_main_keyboard()
            )
            return

        # Timeout task to reset assignment if user takes too long
        async def task_timeout():
            await asyncio.sleep(30 * 60) # 30 mins
            if user_id in user_states and user_states[user_id].get('task_doc_id') == task_doc.id:
                try:
                    if db:
                        db.collection('tasks').document(task_doc.id).update({'status': 'pending'})
                except Exception:
                    pass
                del user_states[user_id]
                await context.bot.send_message(
                    chat_id,
                    "⏳ <b>আপনার টাস্ক বাতিল করা হয়েছে কারণ সময় শেষ হয়ে গেছে।</b>",
                    parse_mode='HTML',
                    reply_markup=get_main_keyboard()
                )

        # Base State
        state = {
            'task_doc_id': task_doc.id,
            'platform': platform,
            'timeout_task': asyncio.create_task(task_timeout())
        }
        
        # Read price from settings to attach to the task session
        settings_doc = db.collection('settings').document('app_settings').get()
        settings = settings_doc.to_dict() if settings_doc.exists else {}
        state['price'] = settings.get(platform.lower(), {}).get('price', 0.0)

        # Handle specific platforms
        dynamic_password = get_dynamic_password()

        if platform == 'Instagram':
            state['step'] = 'AWAITING_ACCOUNT_CREATION'
            state['assigned_username'] = task_data.get('username', '')
            state['password'] = dynamic_password
            user_states[user_id] = state
            
            bot_text = (
                f"👤 Username: <code>{state['assigned_username']}</code>\n"
                f"🔐 Password: <code>{state['password']}</code>\n\n"
                f"📸 উপরের ইউজারনেম এবং পাসওয়ার্ড দিয়ে অ্যাকাউন্ট খুলুন। তারপর নিচে 2FA Set বাটনে ক্লিক করুন 🤫"
            )
            markup = ReplyKeyboardMarkup([
                [KeyboardButton("🔐 2FA Set")],
                [KeyboardButton("❌ বাতিল")]
            ], resize_keyboard=True)
            await context.bot.send_message(chat_id, bot_text, parse_mode='HTML', reply_markup=markup)

        elif platform == 'Facebook':
            state['step'] = 'FB_AWAITING_UID_BTN'
            state['first_name'] = get_field(task_data, 'firstName', 'first_name', 'fname')
            state['last_name'] = get_field(task_data, 'lastName', 'last_name', 'lname')
            state['password'] = dynamic_password
            user_states[user_id] = state
            
            bot_text = (
                f"👤 <b>নামের প্রথমাংশ:</b> <code>{state['first_name']}</code>\n"
                f"👤 <b>নামের শেষাংশ:</b> <code>{state['last_name']}</code>\n"
                f"🔐 <b>পাসওয়ার্ড:</b> <code>{state['password']}</code>\n\n"
                f"🔆 উপরের তথ্য দিয়ে অ্যাকাউন্টে লগইন করুন। তারপর <b>🟢 Send UID</b> বাটনে ক্লিক করুন 😎"
            )
            markup = ReplyKeyboardMarkup([
                [KeyboardButton("🟢 Send UID")],
                [KeyboardButton("🤫 কিভাবে কাজ করব")],
                [KeyboardButton("❌ বাতিল")]
            ], resize_keyboard=True)
            await context.bot.send_message(chat_id, bot_text, parse_mode='HTML', reply_markup=markup)

        elif platform == 'Gmail':
            state['step'] = 'GM_AWAITING_FINISH'
            state['email'] = get_field(task_data, 'email', 'Email')
            state['password'] = dynamic_password
            user_states[user_id] = state
            
            bot_text = (
                f"📧 <b>ইমেইল ঠিকানা:</b> <code>{state['email']}</code>\n"
                f"🔐 <b>পাসওয়ার্ড:</b> <code>{state['password']}</code>\n\n"
                f"✉️ উপরের ইমেইল এবং পাসওয়ার্ড দিয়ে একটি জিমেইল অ্যাকাউন্ট খুলুন। অ্যাকাউন্ট খোলা শেষ হলে <b>✅ অ্যাকাউন্ট খোলা শেষ</b> বাটনে চাপ দিন।"
            )
            markup = ReplyKeyboardMarkup([
                [KeyboardButton("✅ অ্যাকাউন্ট খোলা শেষ")],
                [KeyboardButton("❌ বাতিল")]
            ], resize_keyboard=True)
            await context.bot.send_message(chat_id, bot_text, parse_mode='HTML', reply_markup=markup)

    except Exception as e:
        logger.error(f"Error fetching {platform} tasks: {e}")
        await context.bot.send_message(chat_id, "⚠️ ডাটাবেস এরর। আবার চেষ্টা করুন।")


# ==========================================
# Platform Specific Flows
# ==========================================

# 1. Instagram
async def handle_instagram_2fa_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id].get('step') != 'AWAITING_ACCOUNT_CREATION':
        return
    user_states[user_id]['step'] = 'AWAITING_2FA_KEY'
    await context.bot.send_message(
        chat_id, "🔑 <b>2FA Key টি দিন:</b> ⬇️", 
        parse_mode='HTML', reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ বাতিল")]], resize_keyboard=True)
    )

async def handle_instagram_2fa_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    two_fa_key = update.message.text.strip()
    try:
        secret_key = process_2fa_key(two_fa_key)
        totp_code = generate_totp_code(secret_key)
        
        if not totp_code:
            await context.bot.send_message(chat_id, "❌ <b>2FA Key প্রসেস করতে সমস্যা হয়েছে!</b>\nসঠিক 2FA Recovery Key দিন।", parse_mode='HTML')
            return
        
        user_states[user_id]['step'] = 'AWAITING_ACCOUNT_FINISH'
        user_states[user_id]['two_fa_key'] = two_fa_key
        user_states[user_id]['totp_secret'] = secret_key
        user_states[user_id]['last_totp'] = totp_code
        
        # Message 1
        await context.bot.send_message(
            chat_id,
            "অ্যাকাউন্ট খোলা শেষ হলে নিচের বাটনে চাপ দিন:",
            parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton("✅ অ্যাকাউন্ট খোলা শেষ")], 
                [KeyboardButton("❌ বাতিল")]
            ], resize_keyboard=True)
        )

        # Message 2
        await context.bot.send_message(
            chat_id,
            f"নিচের কোডটিতে ক্লিক করলেই কপি হয়ে যাবে ⤵️\n\n<code>{totp_code}</code>",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"2FA process error: {e}")
        await context.bot.send_message(chat_id, "❌ <b>2FA Key প্রসেস করতে সমস্যা হয়েছে!</b>", parse_mode='HTML')

# 2. Facebook
async def handle_facebook_uid_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if user_id not in user_states or user_states[user_id].get('step') != 'FB_AWAITING_UID_BTN':
        return
    user_states[user_id]['step'] = 'FB_AWAITING_UID'
    await context.bot.send_message(
        chat_id, "আপনার 📘 <b>Facebook UID দিন:</b>", parse_mode='HTML',
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ বাতিল")]], resize_keyboard=True)
    )

async def handle_facebook_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    uid_text = update.message.text
    if not uid_text.isdigit():
        await context.bot.send_message(chat_id, "❌ <b>দয়া করে আপনার সঠিক UID দিন (শুধু সংখ্যা):</b>", parse_mode='HTML')
        return
    user_states[user_id]['step'] = 'FB_AWAITING_COOKIES'
    user_states[user_id]['uid'] = uid_text
    await context.bot.send_message(
        chat_id, "আপনার <b>Cookie দিন ❤️</b>", parse_mode='HTML',
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ বাতিল")]], resize_keyboard=True)
    )

async def handle_facebook_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    cookies = update.message.text
    if 'c_user' not in cookies:
        await context.bot.send_message(chat_id, "❌ <b>দয়া করে আপনার সঠিক Cookie দিন (c_user থাকতে হবে):</b>", parse_mode='HTML')
        return
    user_states[user_id]['step'] = 'AWAITING_ACCOUNT_FINISH'
    user_states[user_id]['cookies'] = cookies
    await context.bot.send_message(
        chat_id, "✅ <b>সম্পূর্ণ করতে নিচের বাটনে চাপুন:</b>", parse_mode='HTML',
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("✅ অ্যাকাউন্ট খোলা শেষ")], [KeyboardButton("❌ বাতিল")]], resize_keyboard=True)
    )

# Generic Submit Function (For ALL Platforms)
async def handle_task_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not db or user_id not in user_states:
        return

    state = user_states[user_id]
    platform = state.get('platform')
    price = state.get('price', 0.0)

    completed_task = {
        'user_id': user_id,
        'task_doc_id': state.get('task_doc_id'),
        'platform': platform,
        'price': price,
        'review_status': 'pending',
        'notified': False,
        'timestamp': firestore.SERVER_TIMESTAMP
    }

    if platform == 'Instagram':
        completed_task['username'] = state.get('assigned_username')
        completed_task['password'] = state.get('password')
        completed_task['two_fa_key'] = state.get('two_fa_key')
        completed_task['totp_used'] = state.get('last_totp')
    elif platform == 'Facebook':
        completed_task['firstName'] = state.get('first_name')
        completed_task['lastName'] = state.get('last_name')
        completed_task['password'] = state.get('password')
        completed_task['uid'] = state.get('uid')
        completed_task['cookies'] = state.get('cookies')
    elif platform == 'Gmail':
        completed_task['email'] = state.get('email')
        completed_task['password'] = state.get('password')

    try:
        db.collection('completed_tasks').add(completed_task)
        db.collection('tasks').document(state['task_doc_id']).update({
            'status': 'completed',
            'attempted_by': firestore.ArrayUnion([user_id])
        })
    except Exception as e:
        logger.error(f"Error saving task: {e}")

    # Clear timers and states
    if state.get('timeout_task'):
        try:
            state['timeout_task'].cancel()
        except: pass
    del user_states[user_id]

    await context.bot.send_message(
        chat_id,
        f"✅ <b>আপনার {platform} কাজ সফলভাবে রিভিউতে জমা হয়েছে!</b>\n"
        f"💰 <b>অ্যাপ্রুভ হলে আয়:</b> {price:.2f} BDT\n"
        f"⏳ এডমিন রিভিউ শেষ হলে নোটিফিকেশন পাবেন।",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )


# ==========================================
# Main Message Router
# ==========================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not text: return

    if text == "❌ বাতিল":
        await clear_user_state(user_id, context.bot, chat_id)
        return

    # Check states first
    if user_id in user_states:
        step = user_states[user_id].get('step')

        if step == 'AWAITING_WITHDRAWAL_AMOUNT':
            try:
                amount = float(text)
                if amount < 50:
                    await context.bot.send_message(chat_id, "<b>অনুগ্রহ করে সঠিক পরিমাণ লিখুন (সর্বনিম্ন ৫০ টাকা)।</b>", parse_mode='HTML')
                    return
                
                # Check DB for balance
                user_doc = db.collection('users').document(str(user_id)).get()
                user_data = user_doc.to_dict() or {}
                total_earned = user_data.get('total_earned', 0.0)
                
                withdrawals_snapshot = db.collection('withdrawals').where('user_id', '==', user_id).get()
                total_withdrawn = 0.0
                pending_withdrawal = 0.0
                for doc in withdrawals_snapshot:
                    data = doc.to_dict()
                    if data.get('status') == 'approved': total_withdrawn += data.get('amount', 0.0)
                    if data.get('status') == 'pending': pending_withdrawal += data.get('amount', 0.0)
                
                balance = total_earned - total_withdrawn - pending_withdrawal
                if amount > balance:
                    await context.bot.send_message(chat_id, "<b>আপনার একাউন্টে পর্যাপ্ত ব্যালেন্স নেই।</b>", parse_mode='HTML')
                    return

                user_states[user_id]['withdrawal_amount'] = amount
                user_states[user_id]['step'] = 'AWAITING_WITHDRAWAL_METHOD'
                await context.bot.send_message(
                    chat_id, "<b>কোন মাধ্যমে টাকা উত্তোলন করতে চান তা নির্বাচন করুন:</b>", parse_mode='HTML',
                    reply_markup=ReplyKeyboardMarkup([[KeyboardButton("বিকাশ"), KeyboardButton("নগদ")], [KeyboardButton("❌ বাতিল")]], resize_keyboard=True)
                )
                return
            except ValueError:
                await context.bot.send_message(chat_id, "<b>অনুগ্রহ করে সঠিক সংখ্যা দিন।</b>", parse_mode='HTML')
                return

        elif step == 'AWAITING_WITHDRAWAL_METHOD':
            if text not in ["বিকাশ", "নগদ"]:
                await context.bot.send_message(chat_id, "<b>অনুগ্রহ করে 'বিকাশ' অথবা 'নগদ' নির্বাচন করুন।</b>", parse_mode='HTML')
                return
            user_states[user_id]['withdrawal_method'] = text
            user_states[user_id]['step'] = 'AWAITING_WITHDRAWAL_NUMBER'
            await context.bot.send_message(
                chat_id, "<b>আপনার নাম্বারটি প্রদান করুন:</b>", parse_mode='HTML',
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ বাতিল")]], resize_keyboard=True)
            )
            return

        elif step == 'AWAITING_WITHDRAWAL_NUMBER':
            user_states[user_id]['withdrawal_number'] = text
            user_states[user_id]['step'] = 'AWAITING_WITHDRAWAL_CONFIRM'
            await context.bot.send_message(
                chat_id,
                f"<b>উত্তোলনের বিবরণ:</b>\n\n"
                f"পরিমাণ: <b>{user_states[user_id]['withdrawal_amount']} টাকা</b>\n"
                f"মাধ্যম: <b>{user_states[user_id]['withdrawal_method']}</b>\n"
                f"নাম্বার: <b>{text}</b>\n\n"
                f"<b>সব ঠিক থাকলে '✅ সাবমিট' বাটনে ক্লিক করুন।</b>",
                parse_mode='HTML',
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("✅ সাবমিট")], [KeyboardButton("❌ বাতিল")]], resize_keyboard=True)
            )
            return

        elif step == 'AWAITING_WITHDRAWAL_CONFIRM':
            if text == "✅ সাবমিট":
                db.collection('withdrawals').document().set({
                    'user_id': user_id,
                    'amount': user_states[user_id]['withdrawal_amount'],
                    'method': user_states[user_id]['withdrawal_method'],
                    'number': user_states[user_id]['withdrawal_number'],
                    'status': 'pending',
                    'notified': False,
                    'timestamp': firestore.SERVER_TIMESTAMP
                })
                del user_states[user_id]
                await context.bot.send_message(
                    chat_id, "<b>✅ আপনার উত্তোলনের রিকোয়েস্ট সফলভাবে সাবমিট হয়েছে। এডমিন চেক করার পর আপনার টাকা পাঠিয়ে দেওয়া হবে।</b>", 
                    parse_mode='HTML', reply_markup=get_main_keyboard()
                )
            else:
                await context.bot.send_message(chat_id, "<b>সাবমিট করতে '✅ সাবমিট' বাটনে চাপুন অথবা বাতিল করতে '❌ বাতিল' চাপুন।</b>", parse_mode='HTML')
            return

        elif step == 'AWAITING_2FA_KEY':
            await handle_instagram_2fa_key(update, context)
            return
        elif step == 'FB_AWAITING_UID':
            await handle_facebook_uid(update, context)
            return
        elif step == 'FB_AWAITING_COOKIES':
            await handle_facebook_cookies(update, context)
            return


    # Button Routing
    if text == "💰 ব্যালেন্স":
        await handle_balance(update, context)
    elif text == "💼 কাজ":
        await handle_tasks_menu(update, context)
    elif text == "💸 উত্তোলনের অনুরোধ":
        await handle_withdrawal(update, context)
    elif text == "🎧 সাপোর্ট":
        await context.bot.send_message(chat_id, "👨💻 <b>যোগাযোগ করুন:</b>\n\n@KAMRUL_ADMIN", parse_mode='HTML')
    elif text == "🎁 আমার রেফারেল":
        await context.bot.send_message(chat_id, f"🔗 <b>আপনার রেফারেল লিংক:</b>\n<code>https://t.me/IncomeBoxXBot?start={user_id}</code>\n\nবন্ধুদের আমন্ত্রণ জানান!", parse_mode='HTML')
    elif text == "🔄 আমি নতুন":
        await context.bot.send_message(chat_id, "📖 <b>নিয়মাবলী:</b>\n১. কাজ অপশনে যান।\n২. নির্দেশিকা মেনে কাজ করুন।\n৩. টাস্ক জমা দিন।\nপেমেন্ট ২-৭২ ঘন্টার মধ্যে।", parse_mode='HTML')
    elif text == "🌐 ভাষা পরিবর্তন":
        await context.bot.send_message(chat_id, "🌍 বর্তমানে শুধু বাংলা উপলব্ধ।", parse_mode='HTML')
        
    # Dynamic Task Selection match
    elif text.startswith("📸 ইনস্টাগ্রাম"):
        await assign_task('Instagram', chat_id, user_id, context)
    elif text.startswith("📘 ফেসবুক"):
        await assign_task('Facebook', chat_id, user_id, context)
    elif text.startswith("📧 জিমেইল"):
        await assign_task('Gmail', chat_id, user_id, context)

    # In-Task Buttons
    elif text == "🔐 2FA Set":
        await handle_instagram_2fa_set(update, context)
    elif text == "⚙️ কিভাবে কাজ করব":
        await context.bot.send_message(chat_id, "প্রদত্ত ইউজারনেম ও পাসওয়ার্ড দিয়ে লগইন করুন। 2FA সেট করে কোড দিন।", parse_mode='HTML')
    elif text == "🤫 কিভাবে কাজ করব":
        await context.bot.send_message(chat_id, "তথ্য দিয়ে লগইন করে UID ও Cookies দিন।", parse_mode='HTML')
    elif text == "🟢 Send UID":
        await handle_facebook_uid_btn(update, context)
    elif text == "✅ অ্যাকাউন্ট খোলা শেষ":
        if user_id in user_states:
            step = user_states[user_id].get('step')
            if step in ['AWAITING_ACCOUNT_FINISH', 'GM_AWAITING_FINISH']:
                await handle_task_finish(update, context)

# ==========================================
# Flask Routes
# ==========================================
@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'bot_active': True})

@app.route('/')
def home():
    return "Bot Server is Running!"

@app.route('/admin')
def admin_panel():
    return send_file('admin.html')

# ==========================================
# Main Application
# ==========================================
def main():
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Bot will not start.")
        return

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(verify_join_callback, pattern="verify_join"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    def run_flask():
        app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    logger.info(f"Bot running on port {PORT}")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
