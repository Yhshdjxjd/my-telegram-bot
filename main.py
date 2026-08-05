import os
import json
import asyncio
import logging
import random
import threading
import time
import re
import base64
from datetime import datetime
from typing import Dict, Any, Optional

from flask import Flask, jsonify
from flask_cors import CORS
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv
import pyotp

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
MAIN_CHANNEL = "@income_box1"
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
                        price = data.get('price', 0)

                        if user_id:
                            if status == 'approved':
                                text = f"✅ <b>আপনার {platform} কাজটি অনুমোদিত হয়েছে!</b>\n💰 আপনার অ্যাকাউন্টে {price} BDT যোগ করা হয়েছে।"
                            else:
                                text = f"❌ <b>আপনার {platform} কাজটি প্রত্যাখ্যাত হয়েছে!</b>\n⚠️ সঠিক তথ্য প্রদান না করার কারণে কাজ বাতিল করা হয়েছে। আপনার ব্যালেন্স থেকে {price} BDT কেটে নেওয়া হয়েছে।"
                            
                            try:
                                await application.bot.send_message(chat_id=int(user_id), text=text, parse_mode='HTML')
                            except Exception as e:
                                logger.error(f"Failed to send notification to {user_id}: {e}")

                        # ডাটাবেজে notified আপডেট করা
                        db.collection('completed_tasks').document(task.id).update({'notified': True})

                        # যদি রিজেক্ট হয়, তবে ব্যালেন্স কেটে নেওয়া (যেহেতু সাবমিট করার সময় এড করা হয়েছিল)
                        if status == 'rejected' and user_id:
                            user_ref = db.collection('users').document(str(user_id))
                            user_doc = user_ref.get()
                            if user_doc.exists:
                                user_data = user_doc.to_dict()
                                current_earned = user_data.get('total_earned', 0)
                                successful_tasks = user_data.get('successful_tasks', 0)
                                user_ref.update({
                                    'total_earned': max(0, current_earned - price),
                                    'successful_tasks': max(0, successful_tasks - 1)
                                })
                                
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
                                text = f"❌ <b>আপনার {amount} টাকার উত্তোলন বাতিল করা হয়েছে!</b>"
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
        import urllib.parse
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

def get_task_menu_keyboard():
    keyboard = [
        [KeyboardButton("📸 ইনস্টাগ্রাম কাজ")],
        [KeyboardButton("📘 ফেসবুক কাজ")],
        [KeyboardButton("❌ বাতিল")]
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
        if state.get('totp_timer'):
            try:
                state['totp_timer'].cancel()
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    user = update.effective_user

    if user_id in user_states:
        state = user_states[user_id]
        if state.get('timeout_task'):
            try:
                state['timeout_task'].cancel()
            except Exception:
                pass
        if state.get('totp_timer'):
            try:
                state['totp_timer'].cancel()
            except Exception:
                pass
        if state.get('task_doc_id') and db:
            try:
                db.collection('tasks').document(state['task_doc_id']).update({'status': 'pending'})
            except Exception as e:
                logger.error(e)
        del user_states[user_id]

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
                'total_earned': 0,
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
        total_earned = user_data.get('total_earned', 0)
        successful_tasks = user_data.get('successful_tasks', 0)

        pending_tasks_snapshot = db.collection('completed_tasks')\
            .where('user_id', '==', user_id).where('review_status', '==', 'pending').get()
        pending_tasks = len(pending_tasks_snapshot)

        withdrawals_snapshot = db.collection('withdrawals').where('user_id', '==', user_id).get()
        total_withdrawn = 0
        pending_withdrawal = 0
        for doc in withdrawals_snapshot:
            data = doc.to_dict()
            if data.get('status') == 'approved':
                total_withdrawn += data.get('amount', 0)
            if data.get('status') == 'pending':
                pending_withdrawal += data.get('amount', 0)

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

async def handle_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_message(
        chat_id,
        "👨‍💻 <b>যেকোনো একটি কাজ সিলেক্ট করুন</b> ⬇️",
        parse_mode='HTML',
        reply_markup=get_task_menu_keyboard()
    )

async def handle_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not db:
        await context.bot.send_message(chat_id, "⚠️ ডাটাবেস সংযুক্ত নেই।", parse_mode='HTML')
        return

    try:
        user_doc = db.collection('users').document(str(user_id)).get()
        user_data = user_doc.to_dict() or {}
        total_earned = user_data.get('total_earned', 0)

        withdrawals_snapshot = db.collection('withdrawals').where('user_id', '==', user_id).get()
        total_withdrawn = 0
        pending_withdrawal = 0
        for doc in withdrawals_snapshot:
            data = doc.to_dict()
            if data.get('status') == 'approved':
                total_withdrawn += data.get('amount', 0)
            if data.get('status') == 'pending':
                pending_withdrawal += data.get('amount', 0)

        current_balance = total_earned - total_withdrawn - pending_withdrawal

        if current_balance < 50:
            await context.bot.send_message(
                chat_id,
                "<b>আপনার একাউন্টে ন্যূনতম ৫০ টাকা থাকতে হবে, না হলে আপনি উত্তোলন করতে পারবেন না।</b>",
                parse_mode='HTML'
            )
        else:
            await context.bot.send_message(
                chat_id,
                f"আপনার বর্তমান ব্যালেন্স <b>{current_balance:.2f} টাকা</b>\n\nআপনি কত টাকা উত্তোলন করতে চান তা নিচে টাইপ করুন:",
                parse_mode='HTML',
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ বাতিল")]], resize_keyboard=True)
            )
            user_states[user_id] = {'step': 'AWAITING_WITHDRAWAL_AMOUNT'}
    except Exception as e:
        logger.error(f"Error initiating withdrawal: {e}")
        await context.bot.send_message(chat_id, "⚠️ এরর হয়েছে, আবার চেষ্টা করুন।")

async def handle_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_message(
        chat_id,
        "👨‍💻 <b>যেকোনো প্রয়োজনে আমাদের এডমিনের সাথে যোগাযোগ করুন:</b>\n\n@KAMRUL_ADMIN",
        parse_mode='HTML'
    )

async def handle_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    referral_link = f"https://t.me/IncomeBoxXBot?start={user_id}"
    await context.bot.send_message(
        chat_id,
        f"🔗 <b>আপনার রেফারেল লিংক:</b>\n<code>{referral_link}</code>\n\nআপনার লিংক থেকে কেউ জয়েন করে প্রথম কাজ সম্পন্ন করলেই আপনি পাবেন 4 BDT বোনাস!",
        parse_mode='HTML'
    )

async def handle_new_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_message(
        chat_id,
        "📖 <b>কাজের নিয়মাবলী:</b> \n১. প্রথমে কাজ অপশনে যান।\n২. সোশ্যাল মিডিয়া টাস্ক কমপ্লিট করুন।\n৩. টাস্ক জমা দিন।\n\n💰 পেমেন্ট পাবেন ২-৭২ ঘন্টার মধ্যে।",
        parse_mode='HTML'
    )

async def handle_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_message(
        chat_id,
        "🌍 <b>ভাষা নির্বাচন করুন:</b> \nবর্তমানে শুধু বাংলা উপলব্ধ।",
        parse_mode='HTML'
    )

async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    await clear_user_state(user_id, context.bot, chat_id)

async def handle_instagram_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    is_member = await check_membership(context.bot, user_id)
    if not is_member:
        await context.bot.send_message(chat_id, "⚠️ <b>দয়া করে প্রথমে চ্যানেলে জয়েন করুন!</b>", parse_mode='HTML')
        return

    await context.bot.send_message(
        chat_id,
        "🟣 <b>সিলেক্ট করুন:</b>",
        parse_mode='HTML',
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("ইনস্টাগ্রাম 2fa (৳4.30)")], [KeyboardButton("❌ বাতিল")]],
            resize_keyboard=True
        )
    )

async def handle_instagram_2fa_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not db:
        await context.bot.send_message(chat_id, "⚠️ ডাটাবেস সংযুক্ত নেই।", parse_mode='HTML')
        return

    is_member = await check_membership(context.bot, user_id)
    if not is_member:
        await context.bot.send_message(chat_id, "⚠️ <b>দয়া করে প্রথমে চ্যানেলে জয়েন করুন!</b>", parse_mode='HTML')
        return

    try:
        tasks_snapshot = db.collection('tasks')\
            .where('platform', '==', 'Instagram').where('status', '==', 'pending').limit(10).get()

        if len(tasks_snapshot) == 0:
            await context.bot.send_message(
                chat_id,
                "😔 <b>দুঃখিত, বর্তমানে কোনো কাজ উপলব্ধ নেই। এডমিন কাজ অ্যাড করলে আবার চেষ্টা করুন।</b>",
                parse_mode='HTML',
                reply_markup=get_main_keyboard()
            )
            return

        task_doc = None
        task_data = None
        for doc in tasks_snapshot:
            data = doc.to_dict()
            if user_id not in data.get('attempted_by', []):
                task_doc = doc
                task_data = data
                break

        if not task_doc:
            await context.bot.send_message(
                chat_id,
                "😔 <b>দুঃখিত, আপনার জন্য বর্তমানে কোনো নতুন কাজ উপলব্ধ নেই।</b>",
                parse_mode='HTML',
                reply_markup=get_main_keyboard()
            )
            return

        db.collection('tasks').document(task_doc.id).update({'status': 'assigned'})

        async def task_timeout():
            await asyncio.sleep(30 * 60)
            if user_id in user_states and user_states[user_id].get('task_doc_id') == task_doc.id:
                try:
                    if db:
                        db.collection('tasks').document(task_doc.id).update({'status': 'pending'})
                except Exception as e:
                    logger.error(e)
                if user_id in user_states and user_states[user_id].get('totp_timer'):
                    try:
                        user_states[user_id]['totp_timer'].cancel()
                    except:
                        pass
                del user_states[user_id]
                await context.bot.send_message(
                    chat_id,
                    "⏳ <b>আপনার টাস্ক বাতিল করা হয়েছে কারণ সময় শেষ হয়ে গেছে।</b>",
                    parse_mode='HTML',
                    reply_markup=get_main_keyboard()
                )

        user_states[user_id] = {
            'step': 'AWAITING_ACCOUNT_CREATION',
            'task_doc_id': task_doc.id,
            'platform': 'Instagram',
            'assigned_username': task_data.get('username', ''),
            'password': task_data.get('password', ''),
            'timeout_task': asyncio.create_task(task_timeout())
        }

        bot_text = (
            f"👤 <b>ইউজারনেম:</b> <code>{task_data.get('username', '')}</code>\n"
            f"🔐 <b>পাসওয়ার্ড:</b> <code>{task_data.get('password', '')}</code>\n\n"
            f"📸 উপরের ইউজারনেম এবং পাসওয়ার্ড দিয়ে অ্যাকাউন্টে লগইন করুন। তারপর নিচে <b>🔐 2FA Set</b> বাটনে ক্লিক করুন 👀"
        )

        await context.bot.send_message(
            chat_id, bot_text, parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("🔐 2FA Set"), KeyboardButton("⚙️ কিভাবে কাজ করব")],
                 [KeyboardButton("❌ বাতিল")]],
                resize_keyboard=True
            )
        )
    except Exception as e:
        logger.error(f"Error fetching tasks: {e}")
        await context.bot.send_message(chat_id, "⚠️ ডাটাবেস এরর। আবার চেষ্টা করুন।")

async def handle_instagram_2fa_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if user_id not in user_states or user_states[user_id].get('step') != 'AWAITING_ACCOUNT_CREATION':
        return

    user_states[user_id]['step'] = 'AWAITING_2FA_KEY'
    await context.bot.send_message(
        chat_id, "🔑 <b>2FA Key টি দিন:</b> ❤️\n\nযেমন: MHJG 7XBT NYCT H5XN YOB4 DWDK GORZ D2DN", 
        parse_mode='HTML',
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ বাতিল")]], resize_keyboard=True)
    )

async def handle_instagram_how_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_message(
        chat_id,
        "📖 <b>কিভাবে Instagram কাজ করবেন:</b>\n\n"
        "1️⃣ প্রদত্ত ইউজারনেম ও পাসওয়ার্ড দিয়ে অ্যাকাউন্টে লগইন করুন\n"
        "2️⃣ 2FA সেটআপ করুন (যদি প্রয়োজন হয়)\n"
        "3️⃣ 2FA Key কপি করে পাঠান\n"
        "4️⃣ বট আপনাকে TOTP কোড দেবে যা Instagram এ ব্যবহার করবেন\n"
        "5️⃣ লগইন সফল হলে \"অ্যাকাউন্ট খোলা শেষ\" বাটনে ক্লিক করুন\n\n"
        "💰 পেমেন্ট পাবেন ২-৭২ ঘন্টার মধ্যে",
        parse_mode='HTML'
    )

async def handle_instagram_2fa_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    two_fa_key = update.message.text.strip()
    
    try:
        secret_key = process_2fa_key(two_fa_key)
        totp_code = generate_totp_code(secret_key)
        
        if not totp_code:
            await context.bot.send_message(
                chat_id,
                "❌ <b>2FA Key প্রসেস করতে সমস্যা হয়েছে!</b>\n\n"
                "দয়া করে সঠিক 2FA Recovery Key দিন।\n"
                "ফরম্যাট: MHJG 7XBT NYCT H5XN YOB4 DWDK GORZ D2DN",
                parse_mode='HTML'
            )
            return
        
        user_states[user_id]['step'] = 'AWAITING_ACCOUNT_FINISH'
        user_states[user_id]['two_fa_key'] = two_fa_key
        user_states[user_id]['totp_secret'] = secret_key
        user_states[user_id]['last_totp'] = totp_code
        
        async def update_totp_timer():
            try:
                totp = pyotp.TOTP(secret_key)
                while True:
                    await asyncio.sleep(30)
                    if user_id in user_states and user_states[user_id].get('step') == 'AWAITING_ACCOUNT_FINISH':
                        new_code = totp.now()
                        user_states[user_id]['last_totp'] = new_code
                        try:
                            await context.bot.send_message(
                                chat_id,
                                f"🔄 <b>নতুন TOTP কোড:</b> <code>{new_code}</code>\n"
                                f"⏱️ এই কোডটি Instagram এ ব্যবহার করুন।",
                                parse_mode='HTML'
                            )
                        except:
                            break
                    else:
                        break
            except:
                pass
        
        user_states[user_id]['totp_timer'] = asyncio.create_task(update_totp_timer())
        
        await context.bot.send_message(
            chat_id,
            f"✅ <b>2FA Key সফলভাবে প্রসেস করা হয়েছে!</b>\n\n"
            f"🔑 <b>বর্তমান TOTP কোড:</b> <code>{totp_code}</code>\n\n"
            f"📌 এখন এই ধাপগুলো করুন:\n"
            f"1️⃣ Instagram এ লগইন করুন (দেওয়া ইউজারনেম ও পাসওয়ার্ড দিয়ে)\n"
            f"2️⃣ 2FA কোড চাইলে উপরের <code>{totp_code}</code> দিন\n"
            f"3️⃣ লগইন সফল হলে '✅ অ্যাকাউন্ট খোলা শেষ' বাটনে ক্লিক করুন\n\n"
            f"⚠️ মনে রাখবেন: এই কোড প্রতি ৩০ সেকেন্ডে পরিবর্তন হয়!\n"
            f"🔄 নতুন কোড পেতে '🔄 নতুন কোড জেনারেট' বাটনে ক্লিক করুন।",
            parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("✅ অ্যাকাউন্ট খোলা শেষ")], 
                 [KeyboardButton("🔄 নতুন কোড জেনারেট"), 
                  KeyboardButton("❌ বাতিল")]],
                resize_keyboard=True
            )
        )
        
    except Exception as e:
        logger.error(f"2FA process error: {e}")
        await context.bot.send_message(
            chat_id,
            "❌ <b>2FA Key প্রসেস করতে সমস্যা হয়েছে!</b>\n\n"
            "দয়া করে সঠিক 2FA Recovery Key দিন।\n"
            "ফরম্যাট: MHJG 7XBT NYCT H5XN YOB4 DWDK GORZ D2DN",
            parse_mode='HTML'
        )

async def handle_new_totp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if user_id not in user_states or user_states[user_id].get('step') != 'AWAITING_ACCOUNT_FINISH':
        return
    
    secret = user_states[user_id].get('totp_secret')
    if secret:
        try:
            totp = pyotp.TOTP(secret)
            new_code = totp.now()
            user_states[user_id]['last_totp'] = new_code
            await context.bot.send_message(
                chat_id,
                f"🔄 <b>নতুন TOTP কোড:</b> <code>{new_code}</code>\n"
                f"⏱️ এই কোডটি Instagram এ ব্যবহার করুন।",
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"TOTP generation error: {e}")
            await context.bot.send_message(
                chat_id,
                "❌ <b>কোড জেনারেট করতে সমস্যা হয়েছে!</b>",
                parse_mode='HTML'
            )

async def handle_instagram_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if db:
        completed_task = {
            'user_id': user_id,
            'task_doc_id': user_states[user_id].get('task_doc_id'),
            'platform': user_states[user_id].get('platform'),
            'username': user_states[user_id].get('assigned_username'),
            'password': user_states[user_id].get('password'),
            'two_fa_key': user_states[user_id].get('two_fa_key'),
            'totp_used': user_states[user_id].get('last_totp'),
            'price': 4.30,
            'review_status': 'pending',
            'notified': False,
            'timestamp': firestore.SERVER_TIMESTAMP
        }
        try:
            db.collection('completed_tasks').add(completed_task)
            db.collection('tasks').document(user_states[user_id]['task_doc_id']).update({
                'status': 'completed',
                'attempted_by': firestore.ArrayUnion([user_id])
            })
            
            user_ref = db.collection('users').document(str(user_id))
            user_doc = user_ref.get()
            if user_doc.exists:
                user_data = user_doc.to_dict()
                current_earned = user_data.get('total_earned', 0)
                user_ref.update({
                    'total_earned': current_earned + 4.30,
                    'successful_tasks': firestore.Increment(1)
                })
            
        except Exception as e:
            logger.error(f"Error saving task: {e}")

    if user_states[user_id].get('totp_timer'):
        try:
            user_states[user_id]['totp_timer'].cancel()
        except:
            pass
    
    if user_states[user_id].get('timeout_task'):
        user_states[user_id]['timeout_task'].cancel()
    
    del user_states[user_id]

    await context.bot.send_message(
        chat_id,
        "✅ <b>আপনার Instagram কাজ সফলভাবে জমা হয়েছে!</b>\n"
        "💰 <b>আপনার আয়:</b> 4.30 BDT\n"
        "⏳ পেমেন্ট ২ ঘন্টা থেকে ৭২ ঘন্টার মধ্যে দেওয়া হবে। এডমিন রিভিউ শেষ হলে নোটিফিকেশন পাবেন। 🎯",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )

async def handle_facebook_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    is_member = await check_membership(context.bot, user_id)
    if not is_member:
        await context.bot.send_message(chat_id, "⚠️ <b>দয়া করে প্রথমে চ্যানেলে জয়েন করুন!</b>", parse_mode='HTML')
        return

    await context.bot.send_message(
        chat_id, "🟣 <b>সিলেক্ট করুন:</b>", parse_mode='HTML',
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("0 fnd cookies | 6.55৳")], [KeyboardButton("❌ বাতিল")]],
            resize_keyboard=True
        )
    )

async def handle_facebook_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not db:
        await context.bot.send_message(chat_id, "⚠️ ডাটাবেস সংযুক্ত নেই।", parse_mode='HTML')
        return

    is_member = await check_membership(context.bot, user_id)
    if not is_member:
        await context.bot.send_message(chat_id, "⚠️ <b>দয়া করে প্রথমে চ্যানেলে জয়েন করুন!</b>", parse_mode='HTML')
        return

    try:
        tasks_snapshot = db.collection('tasks')\
            .where('platform', '==', 'Facebook').where('status', '==', 'pending').limit(10).get()

        if len(tasks_snapshot) == 0:
            await context.bot.send_message(
                chat_id,
                "😔 <b>দুঃখিত, বর্তমানে কোনো কাজ উপলব্ধ নেই। এডমিন কাজ অ্যাড করলে আবার চেষ্টা করুন।</b>",
                parse_mode='HTML',
                reply_markup=get_main_keyboard()
            )
            return

        task_doc = None
        task_data = None
        for doc in tasks_snapshot:
            data = doc.to_dict()
            if user_id not in data.get('attempted_by', []):
                task_doc = doc
                task_data = data
                break

        if not task_doc:
            await context.bot.send_message(
                chat_id,
                "😔 <b>দুঃখিত, আপনার জন্য বর্তমানে কোনো নতুন কাজ উপলব্ধ নেই।</b>",
                parse_mode='HTML',
                reply_markup=get_main_keyboard()
            )
            return

        db.collection('tasks').document(task_doc.id).update({'status': 'assigned'})

        fb_first_name = get_field(task_data, 'first_name', 'firstName', 'fname', 'First_Name', 'First Name', 'first', 'name')
        fb_last_name = get_field(task_data, 'last_name', 'lastName', 'lname', 'Last_Name', 'Last Name', 'last')
        fb_password = get_field(task_data, 'password', 'Password', 'pass', 'pwd', 'passwd')

        async def task_timeout():
            await asyncio.sleep(30 * 60)
            if user_id in user_states and user_states[user_id].get('task_doc_id') == task_doc.id:
                try:
                    if db:
                        db.collection('tasks').document(task_doc.id).update({'status': 'pending'})
                except Exception as e:
                    logger.error(e)
                del user_states[user_id]
                await context.bot.send_message(
                    chat_id,
                    "⏳ <b>আপনার টাস্ক বাতিল করা হয়েছে কারণ সময় শেষ হয়ে গেছে।</b>",
                    parse_mode='HTML',
                    reply_markup=get_main_keyboard()
                )

        user_states[user_id] = {
            'step': 'FB_AWAITING_UID_BTN',
            'task_doc_id': task_doc.id,
            'platform': 'Facebook',
            'first_name': fb_first_name,
            'last_name': fb_last_name,
            'password': fb_password,
            'timeout_task': asyncio.create_task(task_timeout())
        }

        bot_text = (
            f"👤 <b>নামের প্রথমাংশ:</b> <code>{fb_first_name}</code>\n"
            f"👤 <b>নামের শেষাংশ:</b> <code>{fb_last_name}</code>\n"
            f"🔐 <b>পাসওয়ার্ড:</b> <code>{fb_password}</code>\n\n"
            f"🔆 উপরের তথ্য দিয়ে অ্যাকাউন্টে লগইন করুন। তারপর <b>🟢 Send UID</b> বাটনে ক্লিক করুন 😎"
        )

        await context.bot.send_message(
            chat_id, bot_text, parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("🟢 Send UID")],
                 [KeyboardButton("🤫 কিভাবে কাজ করব")],
                 [KeyboardButton("❌ বাতিল")]],
                resize_keyboard=True
            )
        )
    except Exception as e:
        logger.error(f"Error fetching FB tasks: {e}")
        await context.bot.send_message(chat_id, "⚠️ ডাটাবেস এরর। আবার চেষ্টা করুন।")

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

async def handle_facebook_how_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await context.bot.send_message(
        chat_id,
        "📖 <b>কিভাবে Facebook কাজ করবেন:</b>\n\n"
        "1️⃣ প্রদত্ত তথ্য দিয়ে অ্যাকাউন্টে লগইন করুন\n"
        "2️⃣ আপনার Facebook UID খুঁজে বের করুন\n"
        "3️⃣ \"Send UID\" বাটনে ক্লিক করুন এবং UID দিন\n"
        "4️⃣ আপনার Cookies দিন\n"
        "5️⃣ \"অ্যাকাউন্ট খোলা শেষ\" বাটনে ক্লিক করুন\n\n"
        "💰 পেমেন্ট পাবেন ২-৭২ ঘন্টার মধ্যে",
        parse_mode='HTML'
    )

async def handle_facebook_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    uid_text = update.message.text
    if not uid_text.isdigit():
        await context.bot.send_message(
            chat_id,
            "❌ <b>দয়া করে আপনার সঠিক UID দিন (শুধু সংখ্যা):</b>",
            parse_mode='HTML'
        )
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
        await context.bot.send_message(
            chat_id,
            "❌ <b>দয়া করে আপনার সঠিক Cookie দিন (c_user থাকতে হবে):</b>",
            parse_mode='HTML'
        )
        return

    user_states[user_id]['step'] = 'FB_AWAITING_SUBMIT'
    user_states[user_id]['cookies'] = cookies

    await context.bot.send_message(
        chat_id, "✅ <b>সম্পূর্ণ করতে নিচের বাটনে চাপুন:</b>", parse_mode='HTML',
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("✅ অ্যাকাউন্ট খোলা শেষ")], [KeyboardButton("❌ বাতিল")]],
            resize_keyboard=True
        )
    )

async def handle_facebook_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if db:
        completed_task = {
            'user_id': user_id,
            'task_doc_id': user_states[user_id].get('task_doc_id'),
            'platform': user_states[user_id].get('platform'),
            'first_name': user_states[user_id].get('first_name'),
            'last_name': user_states[user_id].get('last_name'),
            'password': user_states[user_id].get('password'),
            'uid': user_states[user_id].get('uid'),
            'cookies': user_states[user_id].get('cookies'),
            'price': 6.55,
            'review_status': 'pending',
            'notified': False,
            'timestamp': firestore.SERVER_TIMESTAMP
        }
        try:
            db.collection('completed_tasks').add(completed_task)
            db.collection('tasks').document(user_states[user_id]['task_doc_id']).update({
                'status': 'completed',
                'attempted_by': firestore.ArrayUnion([user_id])
            })
            
            user_ref = db.collection('users').document(str(user_id))
            user_doc = user_ref.get()
            if user_doc.exists:
                user_data = user_doc.to_dict()
                current_earned = user_data.get('total_earned', 0)
                user_ref.update({
                    'total_earned': current_earned + 6.55,
                    'successful_tasks': firestore.Increment(1)
                })
                
        except Exception as e:
            logger.error(e)

    if user_states[user_id].get('timeout_task'):
        user_states[user_id]['timeout_task'].cancel()
    del user_states[user_id]

    await context.bot.send_message(
        chat_id,
        "🎉 📘 <b>Facebook কাজ সফলভাবে জমা হয়েছে!</b>\n"
        "💰 <b>আপনার আয়:</b> 6.55 BDT\n"
        "⏳ পেমেন্ট ২ ঘন্টা থেকে ৭২ ঘন্টার মধ্যে দেওয়া হবে। এডমিন রিভিউ শেষ হলে নোটিফিকেশন পাবেন।",
        parse_mode='HTML',
        reply_markup=get_main_keyboard()
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not text:
        return

    if text == "❌ বাতিল":
        await handle_cancel(update, context)
        return

    if user_id in user_states:
        step = user_states[user_id].get('step')

        if step == 'AWAITING_WITHDRAWAL_AMOUNT':
            try:
                amount = float(text)
                if amount < 50:
                    await context.bot.send_message(
                        chat_id,
                        "<b>অনুগ্রহ করে সঠিক পরিমাণ লিখুন (সর্বনিম্ন ৫০ টাকা)।</b>",
                        parse_mode='HTML'
                    )
                    return
                user_doc = db.collection('users').document(str(user_id)).get()
                user_data = user_doc.to_dict() or {}
                total_earned = user_data.get('total_earned', 0)

                withdrawals_snapshot = db.collection('withdrawals').where('user_id', '==', user_id).get()
                total_withdrawn = 0
                pending_withdrawal = 0
                for doc in withdrawals_snapshot:
                    data = doc.to_dict()
                    if data.get('status') == 'approved':
                        total_withdrawn += data.get('amount', 0)
                    if data.get('status') == 'pending':
                        pending_withdrawal += data.get('amount', 0)

                balance = total_earned - total_withdrawn - pending_withdrawal
                if amount > balance:
                    await context.bot.send_message(
                        chat_id, "<b>আপনার একাউন্টে পর্যাপ্ত ব্যালেন্স নেই।</b>", parse_mode='HTML'
                    )
                    return

                user_states[user_id]['withdrawal_amount'] = amount
                user_states[user_id]['step'] = 'AWAITING_WITHDRAWAL_METHOD'
                await context.bot.send_message(
                    chat_id,
                    "<b>কোন মাধ্যমে টাকা উত্তোলন করতে চান তা নির্বাচন করুন:</b>",
                    parse_mode='HTML',
                    reply_markup=ReplyKeyboardMarkup(
                        [[KeyboardButton("বিকাশ")], [KeyboardButton("❌ বাতিল")]],
                        resize_keyboard=True
                    )
                )
                return
            except ValueError:
                await context.bot.send_message(
                    chat_id, "<b>অনুগ্রহ করে সঠিক সংখ্যা দিন।</b>", parse_mode='HTML'
                )
                return

        elif step == 'AWAITING_WITHDRAWAL_METHOD':
            if text != "বিকাশ":
                await context.bot.send_message(
                    chat_id, "<b>অনুগ্রহ করে 'বিকাশ' নির্বাচন করুন।</b>", parse_mode='HTML'
                )
                return
            user_states[user_id]['withdrawal_method'] = text
            user_states[user_id]['step'] = 'AWAITING_WITHDRAWAL_NUMBER'
            await context.bot.send_message(
                chat_id, "<b>আপনার বিকাশ নাম্বারটি প্রদান করুন:</b>", parse_mode='HTML',
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
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("✅ সাবমিট")], [KeyboardButton("❌ বাতিল")]],
                    resize_keyboard=True
                )
            )
            return

        elif step == 'AWAITING_WITHDRAWAL_CONFIRM':
            if text == "✅ সাবমিট":
                amount = user_states[user_id]['withdrawal_amount']
                method = user_states[user_id]['withdrawal_method']
                number = user_states[user_id]['withdrawal_number']

                db.collection('withdrawals').document().set({
                    'user_id': user_id,
                    'amount': amount,
                    'method': method,
                    'number': number,
                    'status': 'pending',
                    'notified': False,
                    'timestamp': firestore.SERVER_TIMESTAMP
                })
                del user_states[user_id]
                await context.bot.send_message(
                    chat_id,
                    "<b>আপনার উত্তোলনের রিকোয়েস্ট সফলভাবে সাবমিট হয়েছে। এডমিন চেক করার পর আপনার টাকা পাঠিয়ে দেওয়া হবে।</b>",
                    parse_mode='HTML',
                    reply_markup=get_main_keyboard()
                )
            else:
                await context.bot.send_message(
                    chat_id,
                    "<b>সাবমিট করতে '✅ সাবমিট' বাটনে চাপুন অথবা বাতিল করতে '❌ বাতিল' চাপুন।</b>",
                    parse_mode='HTML'
                )
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

    if text == "💰 ব্যালেন্স":
        await handle_balance(update, context)
    elif text == "💼 কাজ":
        await handle_tasks(update, context)
    elif text == "💸 উত্তোলনের অনুরোধ":
        await handle_withdrawal(update, context)
    elif text == "🎧 সাপোর্ট":
        await handle_support(update, context)
    elif text == "🎁 আমার রেফারেল":
        await handle_referral(update, context)
    elif text == "🔄 আমি নতুন":
        await handle_new_user(update, context)
    elif text == "🌐 ভাষা পরিবর্তন":
        await handle_language(update, context)
    elif text == "📸 ইনস্টাগ্রাম কাজ":
        await handle_instagram_tasks(update, context)
    elif text == "ইনস্টাগ্রাম 2fa (৳4.30)":
        await handle_instagram_2fa_task(update, context)
    elif text == "🔐 2FA Set":
        await handle_instagram_2fa_set(update, context)
    elif text == "⚙️ কিভাবে কাজ করব":
        await handle_instagram_how_to(update, context)
    elif text == "🔄 নতুন কোড জেনারেট":
        await handle_new_totp(update, context)
    elif text == "✅ অ্যাকাউন্ট খোলা শেষ":
        if user_id in user_states:
            step = user_states[user_id].get('step')
            if step == 'AWAITING_ACCOUNT_FINISH':
                await handle_instagram_finish(update, context)
            elif step == 'FB_AWAITING_SUBMIT':
                await handle_facebook_finish(update, context)
    elif text == "📘 ফেসবুক কাজ":
        await handle_facebook_tasks(update, context)
    elif text == "0 fnd cookies | 6.55৳":
        await handle_facebook_task(update, context)
    elif text == "🟢 Send UID":
        await handle_facebook_uid_btn(update, context)
    elif text == "🤫 কিভাবে কাজ করব":
        await handle_facebook_how_to(update, context)

# ==========================================
# Flask Routes
# ==========================================
@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'bot_active': True})

@app.route('/')
def home():
    return "Bot Server is Running!"

# ==========================================
# Main Application
# ==========================================
def main():
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Bot will not start.")
        return

    # এখানে post_init যুক্ত করা হয়েছে ব্যাকগ্রাউন্ড টাস্ক চালানোর জন্য
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
