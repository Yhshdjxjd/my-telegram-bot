import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import random

# এখানে আপনার টেলিগ্রাম বটের টোকেন দিন
TOKEN = '8696557986:AAHCDyZ4fr3CqHV2LtJPXbWpgbXg6pF81is'
bot = telebot.TeleBot(TOKEN)

# ডেমো ইউজারনেম ও পাসওয়ার্ড (যেগুলো ইউজারদের দেওয়া হবে)
credentials = [
  {"username": "krishst609", "password": "kamrol@22"},
  {"username": "vanyum882", "password": "kamrol@22"},
  {"username": "john_doe12", "password": "password123"},
  {"username": "insta_queen99", "password": "queen!@#"}
]

# ইউজারের কাজের ধাপ মনে রাখার জন্য
user_states = {}

def get_main_menu_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("💳 ব্যালেন্স"), KeyboardButton("🛠️ কাজ"))
    markup.row(KeyboardButton("💸 উত্তোলনের অনুরোধ"), KeyboardButton("🎧 সাপোর্ট"))
    markup.row(KeyboardButton("🎁 আমার রেফারেল"), KeyboardButton("🔰 আমি নতুন"))
    markup.row(KeyboardButton("🌐 ভাষা পরিবর্তন"))
    return markup

def get_task_menu_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("📸 ইন্সটাগ্রাম কাজ"))
    markup.add(KeyboardButton("📘 ফেসবুক কাজ"))
    markup.add(KeyboardButton("❌ বাতিল"))
    return markup

# এখানে আপনার চ্যানেলের ইউজারনেম দিন
MAIN_CHANNEL = "@income_box1"
SUPPORT_CHANNEL = "@income_box1"

def check_membership(user_id):
    try:
        main_member = bot.get_chat_member(MAIN_CHANNEL, user_id)
        support_member = bot.get_chat_member(SUPPORT_CHANNEL, user_id)
        valid_statuses = ['creator', 'administrator', 'member']
        return main_member.status in valid_statuses and support_member.status in valid_statuses
    except Exception as e:
        print(f"Membership check error: {e}")
        # Testing এর সুবিধার জন্য True দেওয়া, প্রোডাকশনে False করবেন যদি চ্যানেল না থাকে
        return False

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    
    # চ্যানেল চেক (আপাতত বাইপাস করা চাইলে নিচের if অংশটি মডিফাই করতে পারেন)
    if check_membership(user_id):
        welcome_text = f"👑 <b>স্বাগতম, {message.from_user.first_name}!</b>\n\n<tg-emoji emoji-id='6312260233171312151'>💎</tg-emoji> <b>কাজ শুরু করতে নিচের অপশনগুলো ব্যবহার করুন</b> 🔽"
        bot.send_message(message.chat.id, welcome_text, reply_markup=get_main_menu_keyboard(), parse_mode='HTML')
        return

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📢 জয়েন করুন: মেইন চ্যানেল", url="https://t.me/income_box1"))
    markup.add(InlineKeyboardButton("📢 জয়েন করুন: 𝗦𝘂𝗽𝗽𝗼𝗿𝘁", url="https://t.me/+MDnz3-C-7FkzZDY1"))
    markup.add(InlineKeyboardButton("✅ Verify (ভেরিফাই)", callback_data="verify_join"))

    text = ("<tg-emoji emoji-id='6311983130471308119'>✅</tg-emoji> <b>বটটি ব্যবহার করতে হলে আপনাকে আমাদের অফিশিয়াল চ্যানেলগুলোতে জয়েন করতে হবে!</b>\n\n"
            "নিচের চ্যানেলগুলোতে জয়েন করে 'Verify' বাটনে ক্লিক করুন।")
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode='HTML')

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    if call.data == 'verify_join':
        user_id = call.from_user.id
        if check_membership(user_id):
            bot.answer_callback_query(call.id, "✅ ভেরিফিকেশন সফল হয়েছে!", show_alert=True)
            bot.delete_message(call.message.chat.id, call.message.message_id)
            
            welcome_text = f"👑 <b>স্বাগতম, {call.from_user.first_name}!</b>\n\n<tg-emoji emoji-id='6312260233171312151'>💎</tg-emoji> <b>কাজ শুরু করতে নিচের অপশনগুলো ব্যবহার করুন</b> 🔽"
            bot.send_message(call.message.chat.id, welcome_text, reply_markup=get_main_menu_keyboard(), parse_mode='HTML')
        else:
            bot.answer_callback_query(call.id, "ভেরিফিকেশন ব্যর্থ হয়েছে!", show_alert=True)
            bot.send_message(call.message.chat.id, "<tg-emoji emoji-id='6312235051778056164'>🚫</tg-emoji> <b>আপনি এখনো চ্যানেলগুলোতে জয়েন করেননি! দয়া করে জয়েন করুন।</b>", parse_mode='HTML')

@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    text = message.text
    user_id = message.from_user.id
    
    # বাতিল বাটন চাপলে সবকিছু রিসেট করে মেইন মেনুতে নিয়ে যাবে
    if text == "❌ বাতিল":
        if user_id in user_states:
            del user_states[user_id]
        bot.send_message(message.chat.id, "<tg-emoji emoji-id='6311965254817423602'>❌</tg-emoji> <b>কাজ বাতিল করা হয়েছে।</b>", reply_markup=get_main_menu_keyboard(), parse_mode='HTML')
        return

    state = user_states.get(user_id)
    if state:
        if state['step'] == 'AWAITING_ACCOUNT_CREATION':
            if text == '🔐 2FA Set':
                user_states[user_id] = {'step': 'AWAITING_2FA_KEY'}
                markup = ReplyKeyboardMarkup(resize_keyboard=True)
                markup.add(KeyboardButton("❌ বাতিল"))
                bot.send_message(message.chat.id, "🔑 <b>2FA Key টি দিন:</b> ⤵️", reply_markup=markup, parse_mode='HTML')
            return

        if state['step'] == 'AWAITING_2FA_KEY':
            code = str(random.randint(100000, 999999))
            user_states[user_id] = {'step': 'AWAITING_ACCOUNT_FINISH', 'twoFaKey': text}
            
            msg_text = f"অ্যাকাউন্ট খোলা শেষ হলে নিচের বাটনে চাপ দিন:\nনিচের কোডটির ওপর চাপ দিলে অটোমেটিক কপি হয়ে যাবে ⤵️\n\n🔑 <code>{code}</code>"
            bot.send_message(message.chat.id, msg_text, parse_mode='HTML')
            
            markup = ReplyKeyboardMarkup(resize_keyboard=True)
            markup.add(KeyboardButton("✅ অ্যাকাউন্ট খোলা শেষ"))
            markup.add(KeyboardButton("❌ বাতিল"))
            bot.send_message(message.chat.id, "<b>কাজ শেষ হলে নিচের বাটনে ক্লিক করুন:</b>", reply_markup=markup, parse_mode='HTML')
            return

        if state['step'] == 'AWAITING_ACCOUNT_FINISH':
            if text == '✅ অ্যাকাউন্ট খোলা শেষ':
                del user_states[user_id]
                bot.send_message(message.chat.id, "এইটার পেমেন্ট ২ ঘন্টা থেকে ৭২ ঘন্টার ভিতর দেওয়া হবে। আরো কাজ করতে চাইলে মেনু থেকে কাজ নির্বাচন করুন।", reply_markup=get_main_menu_keyboard(), parse_mode='HTML')
            return

    # 💳 ব্যালেন্স মেনু
    if text == "💳 ব্যালেন্স":
        balance_text = (
            "💠 <b>আপনার ব্যালেন্স ড্যাশবোর্ড</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<tg-emoji emoji-id='6312344027983257537'>💰</tg-emoji> <b>মূল ব্যালেন্স:</b> 0.00 BDT\n"
            "⏳ <b>উত্তোলন (পেন্ডিং):</b> 0.00 BDT\n"
            "<tg-emoji emoji-id='6312344027983257537'>📈</tg-emoji> <b>সর্বমোট আয়:</b> 0.00 BDT\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<tg-emoji emoji-id='6311983130471308119'>✅</tg-emoji> <b>সফল কাজ:</b> 0 টি\n"
            "🔄 <b>পেন্ডিং কাজ:</b> 0 টি"
        )
        bot.send_message(message.chat.id, balance_text, parse_mode='HTML')
        
    elif text == "🛠️ কাজ":
        bot.send_message(message.chat.id, "<tg-emoji emoji-id='6312260233171312151'>👨‍💻</tg-emoji> <b>যেকোনো একটি কাজ সিলেক্ট করুন</b> ⬇️", reply_markup=get_task_menu_keyboard(), parse_mode='HTML')
        
    elif text == "💸 উত্তোলনের অনুরোধ":
        bot.send_message(message.chat.id, "🏧 *আপনার উত্তোলনের মাধ্যম নির্বাচন করুন:* (শীঘ্রই আসছে...)", parse_mode='Markdown')
        
    elif text == "🎧 সাপোর্ট":
        bot.send_message(message.chat.id, "👨‍💼 *অ্যাডমিনের সাথে যোগাযোগ করুন:* @AdminUser", parse_mode='Markdown')
        
    elif text == "🎁 আমার রেফারেল":
        bot.send_message(message.chat.id, "🔗 *আপনার রেফারেল লিংক:*\\n\u0060https://t.me/YourBot?start=" + str(message.from_user.id) + "\u0060", parse_mode='Markdown')
        
    elif text == "🔰 আমি নতুন":
        bot.send_message(message.chat.id, "📖 *কাজের নিয়মাবলী:* \n১. প্রথমে কাজ অপশনে যান।\n২. সোশ্যাল মিডিয়া টাস্ক কমপ্লিট করুন।\n৩. স্ক্রিনশট জমা দিন।", parse_mode='Markdown')
        
    elif text == "🌐 ভাষা পরিবর্তন":
        bot.send_message(message.chat.id, "🌍 *ভাষা নির্বাচন করুন:* \nবর্তমানে শুধু বাংলা উপলব্ধ।", parse_mode='Markdown')
        
    elif text == "📸 ইন্সটাগ্রাম কাজ":
        markup = ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add(KeyboardButton("ইন্সটাগ্রাম 2fa (৳4.30)"))
        markup.add(KeyboardButton("❌ বাতিল"))
        bot.send_message(message.chat.id, "<tg-emoji emoji-id='6312260233171312151'>🟣</tg-emoji> <b>সিলেক্ট করুন:</b>", reply_markup=markup, parse_mode='HTML')
        
    elif text == "ইন্সটাগ্রাম 2fa (৳4.30)":
        user_states[user_id] = {'step': 'AWAITING_ACCOUNT_CREATION'}
        
        # ডাটাবেস বা লিস্ট থেকে একটি একাউন্ট নিবে
        cred = random.choice(credentials)
        
        markup = ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add(KeyboardButton("🔐 2FA Set"))
        markup.add(KeyboardButton("⚙️ কিভাবে কাজ করব"))
        markup.add(KeyboardButton("❌ বাতিল"))
        
        bot_text = (
            f"👤 <b>Username:</b> <code>{cred['username']}</code>\n"
            f"🔐 <b>Password:</b> <code>{cred['password']}</code>\n\n"
            "📸 উপরের ইউজারনেম এবং পাসওয়ার্ড দিয়ে অ্যাকাউন্ট খুলুন। তারপর নিচে <b>2FA Set</b> বাটনে ক্লিক করুন👀"
        )
        bot.send_message(message.chat.id, bot_text, reply_markup=markup, parse_mode='HTML')
        
    elif text == "📘 ফেসবুক কাজ":
        bot.send_message(message.chat.id, "⏳ *কাজ খোঁজা হচ্ছে... দয়া করে অপেক্ষা করুন...*", parse_mode='Markdown')

if __name__ == '__main__':
    print("🚀 Bot is running... Waiting for users!")
    # none_stop=True রাখলে বট ক্র্যাশ করলেও আবার রিস্টার্ট নিবে
    bot.polling(none_stop=True)
