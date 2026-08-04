import express from 'express';
import path from 'path';
import TelegramBot from 'node-telegram-bot-api';
import { initializeApp, cert } from 'firebase-admin/app';
import { getFirestore } from 'firebase-admin/firestore';
import { FieldValue } from 'firebase-admin/firestore';
import { createServer as createViteServer } from 'vite';
import dotenv from 'dotenv';

dotenv.config();

const app = express();
const PORT = 3000;

// ==========================================
// Firebase Setup
// ==========================================
let db: any = null;
try {
  if (process.env.FIREBASE_SERVICE_ACCOUNT) {
    const serviceAccount = JSON.parse(process.env.FIREBASE_SERVICE_ACCOUNT);
    initializeApp({
      credential: cert(serviceAccount)
    });
    db = getFirestore();
    console.log('âœ… Firebase initialized successfully.');
  } else {
    console.warn('âš ï¸ FIREBASE_SERVICE_ACCOUNT is not set. Firestore will not be available.');
  }
} catch (e) {
  console.error('âŒ Failed to initialize Firebase:', e);
}

// ==========================================
// Telegram Bot Setup
// ==========================================
const token = process.env.TELEGRAM_BOT_TOKEN;
let bot: TelegramBot | null = null;

if (token) {
  bot = new TelegramBot(token, { polling: true });
  console.log('âœ… Telegram Bot initialized with polling.');

  // ==========================================
  // Notification Listeners
  // ==========================================
  if (db) {
    // Completed Tasks Listener
    db.collection('completed_tasks').where('notified', '==', false).onSnapshot(async (snapshot: any) => {
      for (const change of snapshot.docChanges()) {
        const doc = change.doc;
        const data = doc.data();

        if (data.notified === false && (data.review_status === 'approved' || data.review_status === 'rejected')) {
          const userId = data.userId;
          try {
            if (data.review_status === 'approved') {
              const nameToShow = data.username || ((data.firstName || data.first_name || '') + ' ' + (data.lastName || data.last_name || ''));
              await bot!.sendMessage(userId, `âœ… <b>à¦†à¦ªà¦¨à¦¾à¦° à¦Ÿà¦¾à¦¸à§à¦•à¦Ÿà¦¿ à¦à¦ªà§à¦°à§à¦­ à¦¹à§Ÿà§‡à¦›à§‡!</b>\nðŸ’° à¦¯à§à¦•à§à¦¤ à¦¹à§Ÿà§‡à¦›à§‡: ${data.price} BDT\nà¦‡à¦‰à¦œà¦¾à¦°à¦¨à§‡à¦®: <code>${nameToShow}</code>`, { parse_mode: 'HTML' });
              
              // Increment balance using transaction
              await db.runTransaction(async (transaction: any) => {
                const userRef = db.collection('users').doc(userId.toString());
                const userDoc = await transaction.get(userRef);
                if (userDoc.exists) {
                  transaction.update(userRef, {
                    totalEarned: FieldValue.increment(data.price),
                    successfulTasks: FieldValue.increment(1)
                  });
                } else {
                  transaction.set(userRef, {
                    totalEarned: data.price,
                    successfulTasks: 1
                  });
                }
              });

              // Check for referral bonus
              const userDoc = await db.collection('users').doc(userId.toString()).get();
              if (userDoc.exists) {
                const userData = userDoc.data()!;
                if (userData.referredBy && !userData.referralBonusPaid) {
                  // Pay the referrer
                  await db.runTransaction(async (transaction: any) => {
                    const referrerRef = db.collection('users').doc(userData.referredBy);
                    transaction.update(referrerRef, {
                      totalEarned: FieldValue.increment(4)
                    });
                  });
                  
                  // Mark as paid
                  await db.collection('users').doc(userId.toString()).update({
                    referralBonusPaid: true
                  });
                  
                  // Notify referrer
                  await bot!.sendMessage(userData.referredBy, `ðŸŽ‰ <b>à¦†à¦ªà¦¨à¦¾à¦° à¦°à§‡à¦«à¦¾à¦° à¦•à¦°à¦¾ à¦à¦•à¦œà¦¨ à¦‡à¦‰à¦œà¦¾à¦° à¦•à¦¾à¦œ à¦¸à¦®à§à¦ªà¦¨à§à¦¨ à¦•à¦°à§‡à¦›à§‡!</b>\nðŸŽ à¦†à¦ªà¦¨à¦¿ à§ª à¦Ÿà¦¾à¦•à¦¾ à¦¬à§‹à¦¨à¦¾à¦¸ à¦ªà§‡à§Ÿà§‡à¦›à§‡à¦¨à¥¤`, { parse_mode: 'HTML' }).catch(console.error);
                }
              }

              // Delete original task
              if (data.task_doc_id) {
                await db.collection('tasks').doc(data.task_doc_id).delete();
              }
              // Delete completed_task
              await doc.ref.delete();
            } else if (data.review_status === 'rejected') {
              const nameToShow = data.username || ((data.firstName || data.first_name || '') + ' ' + (data.lastName || data.last_name || ''));
              await bot!.sendMessage(userId, `âŒ <b>à¦†à¦ªà¦¨à¦¾à¦° à¦Ÿà¦¾à¦¸à§à¦•à¦Ÿà¦¿ à¦°à¦¿à¦œà§‡à¦•à§à¦Ÿ à¦¹à§Ÿà§‡à¦›à§‡!</b> (à¦­à§à¦² à¦¬à¦¾ à¦¬à§à¦¯à¦¬à¦¹à§ƒà¦¤ à¦†à¦‡à¦¡à¦¿à¦° à¦•à¦¾à¦°à¦£à§‡)\nà¦‡à¦‰à¦œà¦¾à¦°à¦¨à§‡à¦®: <code>${nameToShow}</code>`, { parse_mode: 'HTML' });
              
              // If rejected, put the task back in the pool
              if (data.task_doc_id) {
                await db.collection('tasks').doc(data.task_doc_id).update({ 
                  status: 'pending',
                  attemptedBy: FieldValue.arrayRemove(userId)
                });
              }
              // Delete completed_task
              await doc.ref.delete();
            }
          } catch(e) {
            console.error("Error sending notification:", e);
          }
        }
      }
    }, (error: any) => {
      console.error("Error listening to completed_tasks:", error);
    });

    // Withdrawals Listener
    db.collection('withdrawals').where('notified', '==', false).onSnapshot(async (snapshot: any) => {
      for (const change of snapshot.docChanges()) {
        const doc = change.doc;
        const data = doc.data();

        if (data.notified === false && (data.status === 'approved' || data.status === 'rejected')) {
          const userId = data.userId;
          try {
            if (data.status === 'approved') {
              await bot!.sendMessage(userId, `âœ… <b>à¦†à¦ªà¦¨à¦¾à¦° ${data.amount} à§³ à¦‰à¦¤à§à¦¤à§‹à¦²à¦¨ à¦¸à¦«à¦² à¦¹à¦¯à¦¼à§‡à¦›à§‡!</b>`, { parse_mode: 'HTML' });
            } else if (data.status === 'rejected') {
              await bot!.sendMessage(userId, `âŒ <b>à¦†à¦ªà¦¨à¦¾à¦° ${data.amount} à§³ à¦‰à¦¤à§à¦¤à§‹à¦²à¦¨ à¦¬à¦¾à¦¤à¦¿à¦² à¦•à¦°à¦¾ à¦¹à¦¯à¦¼à§‡à¦›à§‡à¥¤</b>`, { parse_mode: 'HTML' });
            }
            await doc.ref.update({ notified: true });
          } catch(e) {
            console.error("Error sending withdrawal notification:", e);
          }
        }
      }
    }, (error: any) => {
      console.error("Error listening to withdrawals:", error);
    });
  }

} else {
  console.warn('âš ï¸ TELEGRAM_BOT_TOKEN is not set. Bot will not be active.');
}

// ==========================================
// Bot Logic
// ==========================================

const userStates: Record<number, any> = {};
const MAIN_CHANNEL = "@income_box1";
const SUPPORT_CHANNEL = process.env.SUPPORT_CHANNEL_ID || "-1003951413076";

function getMainKeyboard() {
  return {
    reply_markup: {
      keyboard: [
        [{ text: "ðŸ’° à¦¬à§à¦¯à¦¾à¦²à§‡à¦¨à§à¦¸" }, { text: "ðŸ’¼ à¦•à¦¾à¦œ" }],
        [{ text: "ðŸ’¸ à¦‰à¦¤à§à¦¤à§‹à¦²à¦¨à§‡à¦° à¦…à¦¨à§à¦°à§‹à¦§" }, { text: "ðŸŽ§ à¦¸à¦¾à¦ªà§‹à¦°à§à¦Ÿ" }],
        [{ text: "ðŸŽ à¦†à¦®à¦¾à¦° à¦°à§‡à¦«à¦¾à¦°à§‡à¦²" }, { text: "ðŸ”° à¦†à¦®à¦¿ à¦¨à¦¤à§à¦¨" }],
        [{ text: "ðŸŒ à¦­à¦¾à¦·à¦¾ à¦ªà¦°à¦¿à¦¬à¦°à§à¦¤à¦¨" }]
      ],
      resize_keyboard: true
    }
  };
}

function getTaskMenuKeyboard() {
  return {
    reply_markup: {
      keyboard: [
        [{ text: "ðŸ“¸ à¦‡à¦¨à§à¦¸à¦Ÿà¦¾à¦—à§à¦°à¦¾à¦® à¦•à¦¾à¦œ" }],
        [{ text: "ðŸ“˜ à¦«à§‡à¦¸à¦¬à§à¦• à¦•à¦¾à¦œ" }],
        [{ text: "âŒ à¦¬à¦¾à¦¤à¦¿à¦²" }]
      ],
      resize_keyboard: true
    }
  };
}

async function checkMembership(userId: number) {
  if (!bot) return false;
  try {
    const mainMember = await bot.getChatMember(MAIN_CHANNEL, userId);
    const mainStatus = mainMember.status;
    const mainValid = mainStatus === 'member' || mainStatus === 'administrator' || mainStatus === 'creator' || mainStatus === 'restricted';
    
    let supportValid = false;
    try {
      const supportMember = await bot.getChatMember(SUPPORT_CHANNEL, userId);
      const supportStatus = supportMember.status;
      supportValid = supportStatus === 'member' || supportStatus === 'administrator' || supportStatus === 'creator' || supportStatus === 'restricted';
    } catch (e: any) {
      console.error(`Support channel check error:`, e.message);
      supportValid = false; 
    }

    return mainValid && supportValid;
  } catch (e: any) {
    console.error(`Membership check error:`, e.message);
    return false; 
  }
}

if (bot) {
  bot.onText(/\/start(?:\s+(.+))?/, async (msg, match) => {
    const chatId = msg.chat.id;
    const userId = msg.from?.id;
    if (!userId) return;

    if (db) {
      const userRef = db.collection('users').doc(userId.toString());
      const userDoc = await userRef.get();
      
      if (!userDoc.exists) {
        let referredBy = null;
        if (match && match[1] && match[1] !== userId.toString()) {
          referredBy = match[1];
        }
        await userRef.set({
          firstName: msg.from?.first_name || msg.from?.firstName || '',
          lastName: msg.from?.last_name || msg.from?.lastName || '',
          username: msg.from?.username || '',
          referredBy: referredBy,
          referralBonusPaid: false,
          totalEarned: 0,
          successfulTasks: 0,
          joinedAt: FieldValue.serverTimestamp()
        });
      }
    }

    const isMember = await checkMembership(userId);
    
    if (isMember) {
      // FIXED: Handle undefined firstName
      const firstName = msg.from?.first_name || msg.from?.firstName || 'à¦‡à¦‰à¦œà¦¾à¦°';
      const welcomeText = `ðŸ‘‘ <b>à¦¸à§à¦¬à¦¾à¦—à¦¤à¦®, ${firstName}!</b>\n\nðŸ’Ž <b>à¦•à¦¾à¦œ à¦¶à§à¦°à§ à¦•à¦°à¦¤à§‡ à¦¨à¦¿à¦šà§‡à¦° à¦…à¦ªà¦¶à¦¨à¦—à§à¦²à§‹ à¦¬à§à¦¯à¦¬à¦¹à¦¾à¦° à¦•à¦°à§à¦¨</b> ðŸ”½`;
      await bot!.sendMessage(chatId, welcomeText, { parse_mode: 'HTML', ...getMainKeyboard() });
      return;
    }

    const inlineKeyboard = {
      reply_markup: {
        inline_keyboard: [
          [{ text: "ðŸ“¢ à¦œà§Ÿà§‡à¦¨ à¦•à¦°à§à¦¨: à¦®à§‡à¦‡à¦¨ à¦šà§à¦¯à¦¾à¦¨à§‡à¦²", url: "https://t.me/income_box1" }],
          [{ text: "ðŸ“¢ à¦œà§Ÿà§‡à¦¨ à¦•à¦°à§à¦¨: Support", url: "https://t.me/+MDnz3-C-7FkzZDY1" }],
          [{ text: "âœ… Verify (à¦­à§‡à¦°à¦¿à¦«à¦¾à¦‡)", callback_data: "verify_join" }]
        ]
      }
    };

    const text = "âœ… <b>à¦¬à¦Ÿà¦Ÿà¦¿ à¦¬à§à¦¯à¦¬à¦¹à¦¾à¦° à¦•à¦°à¦¤à§‡ à¦¹à¦²à§‡ à¦†à¦ªà¦¨à¦¾à¦•à§‡ à¦†à¦®à¦¾à¦¦à§‡à¦° à¦…à¦«à¦¿à¦¶à¦¿à§Ÿà¦¾à¦² à¦šà§à¦¯à¦¾à¦¨à§‡à¦²à¦—à§à¦²à§‹à¦¤à§‡ à¦œà§Ÿà§‡à¦¨ à¦•à¦°à¦¤à§‡ à¦¹à¦¬à§‡!</b>\n\nà¦¨à¦¿à¦šà§‡à¦° à¦šà§à¦¯à¦¾à¦¨à§‡à¦²à¦—à§à¦²à§‹à¦¤à§‡ à¦œà§Ÿà§‡à¦¨ à¦•à¦°à§‡ 'Verify' à¦¬à¦¾à¦Ÿà¦¨à§‡ à¦•à§à¦²à¦¿à¦• à¦•à¦°à§à¦¨à¥¤";
    await bot!.sendMessage(chatId, text, { parse_mode: 'HTML', ...inlineKeyboard });
  });

  bot.on('callback_query', async (query) => {
    if (query.data === 'verify_join') {
      const chatId = query.message?.chat.id;
      const userId = query.from.id;
      
      if (!chatId) return;

      const isMember = await checkMembership(userId);
      
      if (isMember) {
        await bot!.answerCallbackQuery(query.id, { text: "âœ… à¦­à§‡à¦°à¦¿à¦«à¦¿à¦•à§‡à¦¶à¦¨ à¦¸à¦«à¦² à¦¹à§Ÿà§‡à¦›à§‡!", show_alert: true });
        if (query.message?.message_id) {
          await bot!.deleteMessage(chatId, query.message.message_id);
        }
        
        // FIXED: Handle undefined firstName
        const firstName = query.from.first_name || query.from.firstName || 'à¦‡à¦‰à¦œà¦¾à¦°';
        const welcomeText = `ðŸ‘‘ <b>à¦¸à§à¦¬à¦¾à¦—à¦¤à¦®, ${firstName}!</b>\n\nðŸ’Ž <b>à¦•à¦¾à¦œ à¦¶à§à¦°à§ à¦•à¦°à¦¤à§‡ à¦¨à¦¿à¦šà§‡à¦° à¦…à¦ªà¦¶à¦¨à¦—à§à¦²à§‹ à¦¬à§à¦¯à¦¬à¦¹à¦¾à¦° à¦•à¦°à§à¦¨</b> ðŸ”½`;
        await bot!.sendMessage(chatId, welcomeText, { parse_mode: 'HTML', ...getMainKeyboard() });
      } else {
        await bot!.answerCallbackQuery(query.id, { text: "à¦­à§‡à¦°à¦¿à¦«à¦¿à¦•à§‡à¦¶à¦¨ à¦¬à§à¦¯à¦°à§à¦¥ à¦¹à§Ÿà§‡à¦›à§‡!", show_alert: true });
        await bot!.sendMessage(chatId, "ðŸš« <b>à¦†à¦ªà¦¨à¦¿ à¦à¦–à¦¨à§‹ à¦šà§à¦¯à¦¾à¦¨à§‡à¦²à¦—à§à¦²à§‹à¦¤à§‡ à¦œà§Ÿà§‡à¦¨ à¦•à¦°à§‡à¦¨à¦¨à¦¿! à¦¦à§Ÿà¦¾ à¦•à¦°à§‡ à¦œà§Ÿà§‡à¦¨ à¦•à¦°à§à¦¨à¥¤</b>", { parse_mode: 'HTML' });
      }
    }
  });

  bot.on('message', async (msg) => {
    const text = msg.text;
    const chatId = msg.chat.id;
    const userId = msg.from?.id;
    
    if (!text || !userId || text === '/start') return;

    // ==========================================
    // Cancel Handler
    // ==========================================
    if (text === "âŒ à¦¬à¦¾à¦¤à¦¿à¦²") {
      if (userStates[userId]) {
        const state = userStates[userId];
        if (state.task_doc_id && db) {
          try {
            await db.collection('tasks').doc(state.task_doc_id).update({ status: 'pending' });
          } catch(e) {
            console.error(e);
          }
        }
        if (state.timeoutId) clearTimeout(state.timeoutId);
        // FIXED: Complete state cleanup
        delete userStates[userId];
      }
      await bot!.sendMessage(chatId, "âŒ <b>à¦•à¦¾à¦œ à¦¬à¦¾à¦¤à¦¿à¦² à¦•à¦°à¦¾ à¦¹à§Ÿà§‡à¦›à§‡à¥¤</b>", { parse_mode: 'HTML', ...getMainKeyboard() });
      return;
    }

    const state = userStates[userId];
    
    if (state) {
      // ==========================================
      // Withdrawal Flow
      // ==========================================
      if (state.step === 'AWAITING_WITHDRAWAL_AMOUNT') {
        const amount = parseFloat(text);
        if (isNaN(amount) || amount < 50) {
          await bot!.sendMessage(chatId, "<b>à¦…à¦¨à§à¦—à§à¦°à¦¹ à¦•à¦°à§‡ à¦¸à¦ à¦¿à¦• à¦ªà¦°à¦¿à¦®à¦¾à¦£ à¦²à¦¿à¦–à§à¦¨ (à¦¸à¦°à§à¦¬à¦¨à¦¿à¦®à§à¦¨ à§«à§¦ à¦Ÿà¦¾à¦•à¦¾)à¥¤</b>", { parse_mode: 'HTML' });
          return;
        }
        
        const userDoc = await db!.collection('users').doc(userId.toString()).get();
        const userData = userDoc.data() || {};
        
        const completedTasksSnapshot = await db!.collection('completed_tasks').where('userId', '==', userId).get();
        let totalEarned = userData.totalEarned || 0;
        completedTasksSnapshot.forEach((doc: any) => {
          const data = doc.data();
          if (data.review_status === 'approved') {
            totalEarned += (data.price || 0);
          }
        });
        const withdrawalsSnapshot = await db!.collection('withdrawals').where('userId', '==', userId).get();
        let totalWithdrawn = 0;
        let pendingWithdrawal = 0;
        withdrawalsSnapshot.forEach((doc: any) => {
          const data = doc.data();
          if (data.status === 'approved') totalWithdrawn += data.amount;
          if (data.status === 'pending') pendingWithdrawal += data.amount;
        });
        const balance = totalEarned - totalWithdrawn - pendingWithdrawal;

        if (amount > balance) {
          await bot!.sendMessage(chatId, "<b>à¦†à¦ªà¦¨à¦¾à¦° à¦à¦•à¦¾à¦‰à¦¨à§à¦Ÿà§‡ à¦ªà¦°à§à¦¯à¦¾à¦ªà§à¦¤ à¦¬à§à¦¯à¦¾à¦²à§‡à¦¨à§à¦¸ à¦¨à§‡à¦‡à¥¤</b>", { parse_mode: 'HTML' });
          return;
        }
        userStates[userId].withdrawalAmount = amount;
        userStates[userId].step = 'AWAITING_WITHDRAWAL_METHOD';
        await bot!.sendMessage(chatId, "<b>à¦•à§‹à¦¨ à¦®à¦¾à¦§à§à¦¯à¦®à§‡ à¦Ÿà¦¾à¦•à¦¾ à¦‰à¦¤à§à¦¤à§‹à¦²à¦¨ à¦•à¦°à¦¤à§‡ à¦šà¦¾à¦¨ à¦¤à¦¾ à¦¨à¦¿à¦°à§à¦¬à¦¾à¦šà¦¨ à¦•à¦°à§à¦¨:</b>", {
          parse_mode: 'HTML',
          reply_markup: {
            keyboard: [[{ text: "à¦¬à¦¿à¦•à¦¾à¦¶" }], [{ text: "âŒ à¦¬à¦¾à¦¤à¦¿à¦²" }]],
            resize_keyboard: true
          }
        });
        return;
      }

      if (state.step === 'AWAITING_WITHDRAWAL_METHOD') {
        if (text !== "à¦¬à¦¿à¦•à¦¾à¦¶") {
          await bot!.sendMessage(chatId, "<b>à¦…à¦¨à§à¦—à§à¦°à¦¹ à¦•à¦°à§‡ 'à¦¬à¦¿à¦•à¦¾à¦¶' à¦¨à¦¿à¦°à§à¦¬à¦¾à¦šà¦¨ à¦•à¦°à§à¦¨à¥¤</b>", { parse_mode: 'HTML' });
          return;
        }
        userStates[userId].withdrawalMethod = text;
        userStates[userId].step = 'AWAITING_WITHDRAWAL_NUMBER';
        await bot!.sendMessage(chatId, "<b>à¦†à¦ªà¦¨à¦¾à¦° à¦¬à¦¿à¦•à¦¾à¦¶ à¦¨à¦¾à¦®à§à¦¬à¦¾à¦°à¦Ÿà¦¿ à¦ªà§à¦°à¦¦à¦¾à¦¨ à¦•à¦°à§à¦¨:</b>", {
          parse_mode: 'HTML',
          reply_markup: { keyboard: [[{ text: "âŒ à¦¬à¦¾à¦¤à¦¿à¦²" }]], resize_keyboard: true }
        });
        return;
      }

      if (state.step === 'AWAITING_WITHDRAWAL_NUMBER') {
        const number = text;
        userStates[userId].withdrawalNumber = number;
        userStates[userId].step = 'AWAITING_WITHDRAWAL_CONFIRM';
        await bot!.sendMessage(chatId, `<b>à¦‰à¦¤à§à¦¤à§‹à¦²à¦¨à§‡à¦° à¦¬à¦¿à¦¬à¦°à¦£:</b>\n\nà¦ªà¦°à¦¿à¦®à¦¾à¦£: <b>${userStates[userId].withdrawalAmount} à§³</b>\nà¦®à¦¾à¦§à§à¦¯à¦®: <b>${userStates[userId].withdrawalMethod}</b>\nà¦¨à¦¾à¦®à§à¦¬à¦¾à¦°: <b>${number}</b>\n\n<b>à¦¸à¦¬ à¦ à¦¿à¦• à¦¥à¦¾à¦•à¦²à§‡ 'âœ… à¦¸à¦¾à¦¬à¦®à¦¿à¦Ÿ' à¦¬à¦¾à¦Ÿà¦¨à§‡ à¦•à§à¦²à¦¿à¦• à¦•à¦°à§à¦¨à¥¤</b>`, {
          parse_mode: 'HTML',
          reply_markup: {
            keyboard: [[{ text: "âœ… à¦¸à¦¾à¦¬à¦®à¦¿à¦Ÿ" }], [{ text: "âŒ à¦¬à¦¾à¦¤à¦¿à¦²" }]],
            resize_keyboard: true
          }
        });
        return;
      }

      if (state.step === 'AWAITING_WITHDRAWAL_CONFIRM') {
        if (text === "âœ… à¦¸à¦¾à¦¬à¦®à¦¿à¦Ÿ") {
          const amount = userStates[userId].withdrawalAmount;
          const method = userStates[userId].withdrawalMethod;
          const number = userStates[userId].withdrawalNumber;

          const withdrawalRef = db!.collection('withdrawals').doc();
          await withdrawalRef.set({
            userId: userId,
            amount: amount,
            method: method,
            number: number,
            status: 'pending',
            notified: false,
            timestamp: FieldValue.serverTimestamp()
          });

          delete userStates[userId];
          await bot!.sendMessage(chatId, "<b>à¦†à¦ªà¦¨à¦¾à¦° à¦‰à¦¤à§à¦¤à§‹à¦²à¦¨à§‡à¦° à¦°à¦¿à¦•à§‹à¦¯à¦¼à§‡à¦¸à§à¦Ÿ à¦¸à¦«à¦²à¦­à¦¾à¦¬à§‡ à¦¸à¦¾à¦¬à¦®à¦¿à¦Ÿ à¦¹à¦¯à¦¼à§‡à¦›à§‡à¥¤ à¦à¦¡à¦®à¦¿à¦¨ à¦šà§‡à¦• à¦•à¦°à¦¾à¦° à¦ªà¦° à¦†à¦ªà¦¨à¦¾à¦° à¦Ÿà¦¾à¦•à¦¾ à¦ªà¦¾à¦ à¦¿à§Ÿà§‡ à¦¦à§‡à¦“à§Ÿà¦¾ à¦¹à¦¬à§‡à¥¤</b>", { parse_mode: 'HTML', ...getMainKeyboard() });
        }
        return;
      }
      
      // ==========================================
      // Instagram Flow
      // ==========================================
      if (state.step === 'AWAITING_ACCOUNT_CREATION') {
        if (text === 'ðŸ” 2FA Set') {
          userStates[userId].step = 'AWAITING_2FA_KEY';
          await bot!.sendMessage(chatId, "ðŸ”‘ <b>2FA Key à¦Ÿà¦¿ à¦¦à¦¿à¦¨:</b> â¤µï¸", { 
            parse_mode: 'HTML', 
            reply_markup: { keyboard: [[{ text: "âŒ à¦¬à¦¾à¦¤à¦¿à¦²" }]], resize_keyboard: true }
          });
        } else if (text === 'âš™ï¸ à¦•à¦¿à¦­à¦¾à¦¬à§‡ à¦•à¦¾à¦œ à¦•à¦°à¦¬') {
          // FIXED: Added handler for "à¦•à¦¿à¦­à¦¾à¦¬à§‡ à¦•à¦¾à¦œ à¦•à¦°à¦¬"
          const howToText = `ðŸ“– <b>à¦•à¦¿à¦­à¦¾à¦¬à§‡ Instagram à¦•à¦¾à¦œ à¦•à¦°à¦¬à§‡à¦¨:</b>\n\n1ï¸âƒ£ à¦ªà§à¦°à¦¦à¦¤à§à¦¤ à¦‡à¦‰à¦œà¦¾à¦°à¦¨à§‡à¦® à¦“ à¦ªà¦¾à¦¸à¦“à¦¯à¦¼à¦¾à¦°à§à¦¡ à¦¦à¦¿à¦¯à¦¼à§‡ à¦…à§à¦¯à¦¾à¦•à¦¾à¦‰à¦¨à§à¦Ÿà§‡ à¦²à¦—à¦‡à¦¨ à¦•à¦°à§à¦¨\n2ï¸âƒ£ 2FA à¦¸à§‡à¦Ÿà¦†à¦ª à¦•à¦°à§à¦¨ (à¦¯à¦¦à¦¿ à¦ªà§à¦°à¦¯à¦¼à§‹à¦œà¦¨ à¦¹à¦¯à¦¼)\n3ï¸âƒ£ 2FA Key à¦•à¦ªà¦¿ à¦•à¦°à§‡ à¦ªà¦¾à¦ à¦¾à¦¨\n4ï¸âƒ£ "à¦…à§à¦¯à¦¾à¦•à¦¾à¦‰à¦¨à§à¦Ÿ à¦–à§‹à¦²à¦¾ à¦¶à§‡à¦·" à¦¬à¦¾à¦Ÿà¦¨à§‡ à¦•à§à¦²à¦¿à¦• à¦•à¦°à§à¦¨\n\nðŸ’° à¦ªà§‡à¦®à§‡à¦¨à§à¦Ÿ à¦ªà¦¾à¦¬à§‡à¦¨ à§¨-à§­à§¨ à¦˜à¦¨à§à¦Ÿà¦¾à¦° à¦®à¦§à§à¦¯à§‡`;
          await bot!.sendMessage(chatId, howToText, { parse_mode: 'HTML' });
        }
        return;
      }

      if (state.step === 'AWAITING_2FA_KEY') {
        userStates[userId].step = 'AWAITING_ACCOUNT_FINISH';
        userStates[userId].twoFaKey = text;
        const code = Math.floor(100000 + Math.random() * 900000).toString();
        
        const msgText = `à¦…à§à¦¯à¦¾à¦•à¦¾à¦‰à¦¨à§à¦Ÿ à¦–à§‹à¦²à¦¾ à¦¶à§‡à¦· à¦¹à¦²à§‡ à¦¨à¦¿à¦šà§‡à¦° à¦¬à¦¾à¦Ÿà¦¨à§‡ à¦šà¦¾à¦ª à¦¦à¦¿à¦¨:\nà¦¨à¦¿à¦šà§‡à¦° à¦•à§‹à¦¡à¦Ÿà¦¿à¦° à¦“à¦ªà¦° à¦šà¦¾à¦ª à¦¦à¦¿à¦²à§‡ à¦…à¦Ÿà§‹à¦®à§‡à¦Ÿà¦¿à¦• à¦•à¦ªà¦¿ à¦¹à¦¯à¦¼à§‡ à¦¯à¦¾à¦¬à§‡ â¤µï¸\n\nðŸ”‘ <code>${code}</code>`;
        await bot!.sendMessage(chatId, msgText, { parse_mode: 'HTML' });
        
        await bot!.sendMessage(chatId, "<b>à¦•à¦¾à¦œ à¦¶à§‡à¦· à¦¹à¦²à§‡ à¦¨à¦¿à¦šà§‡à¦° à¦¬à¦¾à¦Ÿà¦¨à§‡ à¦•à§à¦²à¦¿à¦• à¦•à¦°à§à¦¨:</b>", { 
          parse_mode: 'HTML',
          reply_markup: {
            keyboard: [[{ text: "âœ… à¦…à§à¦¯à¦¾à¦•à¦¾à¦‰à¦¨à§à¦Ÿ à¦–à§‹à¦²à¦¾ à¦¶à§‡à¦·" }], [{ text: "âŒ à¦¬à¦¾à¦¤à¦¿à¦²" }]],
            resize_keyboard: true
          }
        });
        return;
      }

      if (state.step === 'AWAITING_ACCOUNT_FINISH') {
        if (text === 'âœ… à¦…à§à¦¯à¦¾à¦•à¦¾à¦‰à¦¨à§à¦Ÿ à¦–à§‹à¦²à¦¾ à¦¶à§‡à¦·') {
          if (db) {
            const completedTask = {
              userId,
              task_doc_id: state.task_doc_id,
              platform: state.platform,
              username: state.assigned_username,
              password: state.password,
              twoFaKey: state.twoFaKey,
              price: 4.30,
              review_status: 'pending',
              notified: false,
              timestamp: FieldValue.serverTimestamp()
            };
            try {
              await db.collection('completed_tasks').add(completedTask);
              await db.collection('tasks').doc(state.task_doc_id).update({
                status: 'completed',
                attemptedBy: FieldValue.arrayUnion(userId)
              });
            } catch(e) {
              console.error(e);
            }
          }
          // FIXED: Clear timeout properly
          if (userStates[userId]?.timeoutId) {
            clearTimeout(userStates[userId].timeoutId);
          }
          delete userStates[userId];
          await bot!.sendMessage(chatId, "âœ… <b>à¦†à¦ªà¦¨à¦¾à¦° Instagram à¦•à¦¾à¦œ à¦¸à¦«à¦²à¦­à¦¾à¦¬à§‡ à¦œà¦®à¦¾ à¦¹à¦¯à¦¼à§‡à¦›à§‡!</b>\nâ³ à¦ªà§‡à¦®à§‡à¦¨à§à¦Ÿ à§¨ à¦˜à¦¨à§à¦Ÿà¦¾ à¦¥à§‡à¦•à§‡ à§­à§¨ à¦˜à¦¨à§à¦Ÿà¦¾à¦° à¦­à¦¿à¦¤à¦° à¦¦à§‡à¦“à§Ÿà¦¾ à¦¹à¦¬à§‡à¥¤ à¦†à¦°à§‹ à¦•à¦¾à¦œ à¦•à¦°à¦¤à§‡ à¦¥à¦¾à¦•à§‡à¦¨à¥¤ ðŸŽ¯", { 
            parse_mode: 'HTML',
            ...getMainKeyboard()
          });
        }
        return;
      }

      // ==========================================
      // Facebook Flow
      // ==========================================
      if (state.step === 'FB_AWAITING_UID_BTN') {
        if (text === 'ðŸŸ¢ Send UID') {
          userStates[userId].step = 'FB_AWAITING_UID';
          await bot!.sendMessage(chatId, "à¦†à¦ªà¦¨à¦¾à¦° ðŸ“˜ <b>Facebook UID à¦¦à¦¿à¦¨:</b>", { 
            parse_mode: 'HTML',
            reply_markup: { keyboard: [[{ text: "âŒ à¦¬à¦¾à¦¤à¦¿à¦²" }]], resize_keyboard: true }
          });
        } else if (text === 'ðŸ¤« à¦•à¦¿à¦­à¦¾à¦¬à§‡ à¦•à¦¾à¦œ à¦•à¦°à¦¬') {
          // FIXED: Added handler for "à¦•à¦¿à¦­à¦¾à¦¬à§‡ à¦•à¦¾à¦œ à¦•à¦°à¦¬"
          const howToText = `ðŸ“– <b>à¦•à¦¿à¦­à¦¾à¦¬à§‡ Facebook à¦•à¦¾à¦œ à¦•à¦°à¦¬à§‡à¦¨:</b>\n\n1ï¸âƒ£ à¦ªà§à¦°à¦¦à¦¤à§à¦¤ à¦¤à¦¥à§à¦¯ à¦¦à¦¿à¦¯à¦¼à§‡ à¦…à§à¦¯à¦¾à¦•à¦¾à¦‰à¦¨à§à¦Ÿà§‡ à¦²à¦—à¦‡à¦¨ à¦•à¦°à§à¦¨\n2ï¸âƒ£ à¦†à¦ªà¦¨à¦¾à¦° Facebook UID à¦–à§à¦à¦œà§‡ à¦¬à§‡à¦° à¦•à¦°à§à¦¨\n3ï¸âƒ£ "Send UID" à¦¬à¦¾à¦Ÿà¦¨à§‡ à¦•à§à¦²à¦¿à¦• à¦•à¦°à§à¦¨ à¦à¦¬à¦‚ UID à¦¦à¦¿à¦¨\n4ï¸âƒ£ à¦†à¦ªà¦¨à¦¾à¦° Cookies à¦¦à¦¿à¦¨\n5ï¸âƒ£ "à¦…à§à¦¯à¦¾à¦•à¦¾à¦‰à¦¨à§à¦Ÿ à¦–à§‹à¦²à¦¾ à¦¶à§‡à¦·" à¦¬à¦¾à¦Ÿà¦¨à§‡ à¦•à§à¦²à¦¿à¦• à¦•à¦°à§à¦¨\n\nðŸ’° à¦ªà§‡à¦®à§‡à¦¨à§à¦Ÿ à¦ªà¦¾à¦¬à§‡à¦¨ à§¨-à§­à§¨ à¦˜à¦¨à§à¦Ÿà¦¾à¦° à¦®à¦§à§à¦¯à§‡`;
          await bot!.sendMessage(chatId, howToText, { parse_mode: 'HTML' });
        }
        return;
      }

      if (state.step === 'FB_AWAITING_UID') {
        if (!text.match(/^\d+$/)) {
          await bot!.sendMessage(chatId, "âŒ <b>à¦¦à§Ÿà¦¾ à¦•à¦°à§‡ à¦†à¦ªà¦¨à¦¾à¦° à¦¸à¦ à¦¿à¦• UID à¦¦à¦¿à¦¨ (à¦¶à§à¦§à§ à¦¸à¦‚à¦–à§à¦¯à¦¾):</b>", { parse_mode: 'HTML' });
          return;
        }
        userStates[userId].step = 'FB_AWAITING_COOKIES';
        userStates[userId].uid = text;
        await bot!.sendMessage(chatId, "à¦†à¦ªà¦¨à¦¾à¦° <b>Cookie à¦¦à¦¿à¦¨ â¤µï¸</b>", { 
          parse_mode: 'HTML',
          reply_markup: { keyboard: [[{ text: "âŒ à¦¬à¦¾à¦¤à¦¿à¦²" }]], resize_keyboard: true }
        });
        return;
      }

      if (state.step === 'FB_AWAITING_COOKIES') {
        if (!text.includes('c_user')) {
          await bot!.sendMessage(chatId, "âŒ <b>à¦¦à§Ÿà¦¾ à¦•à¦°à§‡ à¦†à¦ªà¦¨à¦¾à¦° à¦¸à¦ à¦¿à¦• Cookie à¦¦à¦¿à¦¨ (c_user à¦¥à¦¾à¦•à¦¤à§‡ à¦¹à¦¬à§‡):</b>", { parse_mode: 'HTML' });
          return;
        }
        
        userStates[userId].step = 'FB_AWAITING_SUBMIT';
        userStates[userId].cookies = text;
        await bot!.sendMessage(chatId, "âœ… <b>à¦¸à¦®à§à¦ªà§‚à¦°à§à¦£ à¦•à¦°à¦¤à§‡ à¦¨à¦¿à¦šà§‡à¦° à¦¬à¦¾à¦Ÿà¦¨à§‡ à¦šà¦¾à¦ªà§à¦¨:</b>", { 
          parse_mode: 'HTML',
          reply_markup: { keyboard: [[{ text: "âœ… à¦…à§à¦¯à¦¾à¦•à¦¾à¦‰à¦¨à§à¦Ÿ à¦–à§‹à¦²à¦¾ à¦¶à§‡à¦·" }], [{ text: "âŒ à¦¬à¦¾à¦¤à¦¿à¦²" }]], resize_keyboard: true }
        });
        return;
      }

      if (state.step === 'FB_AWAITING_SUBMIT') {
        if (text === 'âœ… à¦…à§à¦¯à¦¾à¦•à¦¾à¦‰à¦¨à§à¦Ÿ à¦–à§‹à¦²à¦¾ à¦¶à§‡à¦·') {
          if (db) {
            const completedTask = {
              userId,
              task_doc_id: state.task_doc_id,
              platform: state.platform,
              firstName: state.firstName,
              lastName: state.lastName,
              password: state.password,
              uid: state.uid,
              cookies: state.cookies,
              price: 6.55,
              review_status: 'pending',
              notified: false,
              timestamp: FieldValue.serverTimestamp()
            };
            try {
              await db.collection('completed_tasks').add(completedTask);
              await db.collection('tasks').doc(state.task_doc_id).update({
                status: 'completed',
                attemptedBy: FieldValue.arrayUnion(userId)
              });
            } catch(e) {
              console.error(e);
            }
          }
          // FIXED: Clear timeout properly
          if (userStates[userId]?.timeoutId) {
            clearTimeout(userStates[userId].timeoutId);
          }
          delete userStates[userId];
          await bot!.sendMessage(chatId, "ðŸŽ‰ ðŸ“˜ <b>Facebook à¦•à¦¾à¦œ à¦¸à¦«à¦²à¦­à¦¾à¦¬à§‡ à¦œà¦®à¦¾ à¦¹à¦¯à¦¼à§‡à¦›à§‡!</b>\nâ³ à¦ªà§‡à¦®à§‡à¦¨à§à¦Ÿ à§¨ à¦˜à¦¨à§à¦Ÿà¦¾ à¦¥à§‡à¦•à§‡ à§­à§¨ à¦˜à¦¨à§à¦Ÿà¦¾à¦° à¦­à¦¿à¦¤à¦° à¦¦à§‡à¦“à§Ÿà¦¾ à¦¹à¦¬à§‡à¥¤", { 
            parse_mode: 'HTML', 
            ...getMainKeyboard()
          });
        }
        return;
      }
    }

    // ==========================================
    // Main Menu Handlers
    // ==========================================
    if (text === "ðŸ’° à¦¬à§à¦¯à¦¾à¦²à§‡à¦¨à§à¦¸") {
      if (!db) {
        await bot!.sendMessage(chatId, "âš ï¸ à¦¡à¦¾à¦Ÿà¦¾à¦¬à§‡à¦¸ à¦¸à¦‚à¦¯à§à¦•à§à¦¤ à¦¨à§‡à¦‡à¥¤", { parse_mode: 'HTML' });
        return;
      }
      
      try {
        const userDoc = await db.collection('users').doc(userId.toString()).get();
        const userData = userDoc.data() || {};
        
        // FIXED: Don't double count from completed_tasks
        let totalEarned = userData.totalEarned || 0;
        let successfulTasks = userData.successfulTasks || 0;
        let pendingTasks = 0;
        
        // Only count pending tasks from completed_tasks
        const pendingTasksSnapshot = await db.collection('completed_tasks')
          .where('userId', '==', userId)
          .where('review_status', '==', 'pending')
          .get();
        pendingTasks = pendingTasksSnapshot.size;

        const withdrawalsSnapshot = await db.collection('withdrawals').where('userId', '==', userId).get();
        let totalWithdrawn = 0;
        let pendingWithdrawal = 0;
        
        withdrawalsSnapshot.forEach((doc: any) => {
          const data = doc.data();
          if (data.status === 'approved') totalWithdrawn += data.amount;
          if (data.status === 'pending') pendingWithdrawal += data.amount;
        });

        const currentBalance = totalEarned - totalWithdrawn - pendingWithdrawal;

        const balanceText = `ðŸ’  <b>à¦†à¦ªà¦¨à¦¾à¦° à¦¬à§à¦¯à¦¾à¦²à§‡à¦¨à§à¦¸ à¦¡à§à¦¯à¦¾à¦¶à¦¬à§‹à¦°à§à¦¡</b>\nâ”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\nðŸ’° <b>à¦®à§‚à¦² à¦¬à§à¦¯à¦¾à¦²à§‡à¦¨à§à¦¸:</b> ${currentBalance.toFixed(2)} BDT\nâ³ <b>à¦‰à¦¤à§à¦¤à§‹à¦²à¦¨ (à¦ªà§‡à¦¨à§à¦¡à¦¿à¦‚):</b> ${pendingWithdrawal.toFixed(2)} BDT\nðŸ“ˆ <b>à¦¸à¦°à§à¦¬à¦®à§‹à¦Ÿ à¦†à§Ÿ:</b> ${totalEarned.toFixed(2)} BDT\nâ”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\nâœ… <b>à¦¸à¦«à¦² à¦•à¦¾à¦œ:</b> ${successfulTasks} à¦Ÿà¦¿\nðŸ”„ <b>à¦ªà§‡à¦¨à§à¦¡à¦¿à¦‚ à¦•à¦¾à¦œ:</b> ${pendingTasks} à¦Ÿà¦¿`;
        await bot!.sendMessage(chatId, balanceText, { parse_mode: 'HTML' });
      } catch (e) {
        console.error("Error fetching balance", e);
        await bot!.sendMessage(chatId, "âš ï¸ à¦¬à§à¦¯à¦¾à¦²à§‡à¦¨à§à¦¸ à¦²à§‹à¦¡ à¦•à¦°à¦¤à§‡ à¦¸à¦®à¦¸à§à¦¯à¦¾ à¦¹à¦¯à¦¼à§‡à¦›à§‡à¥¤");
      }
    } else if (text === "ðŸ’¼ à¦•à¦¾à¦œ") {
      await bot!.sendMessage(chatId, "ðŸ‘¨â€ðŸ’» <b>à¦¯à§‡à¦•à§‹à¦¨à§‹ à¦à¦•à¦Ÿà¦¿ à¦•à¦¾à¦œ à¦¸à¦¿à¦²à§‡à¦•à§à¦Ÿ à¦•à¦°à§à¦¨</b> â¬‡ï¸", { parse_mode: 'HTML', ...getTaskMenuKeyboard() });
    } else if (text === "ðŸ’¸ à¦‰à¦¤à§à¦¤à§‹à¦²à¦¨à§‡à¦° à¦…à¦¨à§à¦°à§‹à¦§") {
      if (!db) {
        await bot!.sendMessage(chatId, "âš ï¸ à¦¡à¦¾à¦Ÿà¦¾à¦¬à§‡à¦¸ à¦¸à¦‚à¦¯à§à¦•à§à¦¤ à¦¨à§‡à¦‡à¥¤", { parse_mode: 'HTML' });
        return;
      }
      
      try {
        const userDoc = await db.collection('users').doc(userId.toString()).get();
        const userData = userDoc.data() || {};
        
        // FIXED: Don't double count from completed_tasks
        let totalEarned = userData.totalEarned || 0;

        const withdrawalsSnapshot = await db.collection('withdrawals').where('userId', '==', userId).get();
        let totalWithdrawn = 0;
        let pendingWithdrawal = 0;
        withdrawalsSnapshot.forEach((doc: any) => {
          const data = doc.data();
          if (data.status === 'approved') totalWithdrawn += data.amount;
          if (data.status === 'pending') pendingWithdrawal += data.amount;
        });

        const currentBalance = totalEarned - totalWithdrawn - pendingWithdrawal;

        if (currentBalance < 50) {
          await bot!.sendMessage(chatId, "<b>à¦†à¦ªà¦¨à¦¾à¦° à¦à¦•à¦¾à¦‰à¦¨à§à¦Ÿà§‡ à¦¨à§à¦¯à§‚à¦¨à¦¤à¦® à§«à§¦ à¦Ÿà¦¾à¦•à¦¾ à¦¥à¦¾à¦•à¦¤à§‡ à¦¹à¦¬à§‡, à¦¨à¦¾ à¦¹à¦²à§‡ à¦†à¦ªà¦¨à¦¿ à¦‰à¦¤à§à¦¤à§‹à¦²à¦¨ à¦•à¦°à¦¤à§‡ à¦ªà¦¾à¦°à¦¬à§‡à¦¨à¦¬à¦¿à¦§à¦¾ à¦ªà¦¾à¦¬à§‡à¦¨à¥¤</b>", { parse_mode: 'HTML' });
        } else {
          await bot!.sendMessage(chatId, `à¦†à¦ªà¦¨à¦¾à¦° à¦¬à¦°à§à¦¤à¦®à¦¾à¦¨ à¦¬à§à¦¯à¦¾à¦²à§‡à¦¨à§à¦¸ <b>${currentBalance.toFixed(2)} à§³</b>\n\nà¦†à¦ªà¦¨à¦¿ à¦•à¦¤ à¦Ÿà¦¾à¦•à¦¾ à¦‰à¦¤à§à¦¤à§‹à¦²à¦¨ à¦•à¦°à¦¤à§‡ à¦šà¦¾à¦¨ à¦¤à¦¾ à¦¨à¦¿à¦šà§‡ à¦Ÿà¦¾à¦‡à¦ª à¦•à¦°à§à¦¨:`, { 
            parse_mode: 'HTML', 
            reply_markup: {
              keyboard: [[{ text: "âŒ à¦¬à¦¾à¦¤à¦¿à¦²" }]], 
              resize_keyboard: true
            }
          });
          userStates[userId] = { step: 'AWAITING_WITHDRAWAL_AMOUNT' };
        }
      } catch (e) {
        console.error("Error initiating withdrawal", e);
        await bot!.sendMessage(chatId, "âš ï¸ à¦à¦°à¦° à¦¹à§Ÿà§‡à¦›à§‡, à¦†à¦¬à¦¾à¦° à¦šà§‡à¦·à§à¦Ÿà¦¾ à¦•à¦°à§à¦¨à¥¤");
      }
    } else if (text === "ðŸŽ§ à¦¸à¦¾à¦ªà§‹à¦°à§à¦Ÿ") {
      // FIXED: Consistent admin username
      await bot!.sendMessage(chatId, "ðŸ‘¨â€ðŸ’¼ <b>à¦¯à§‡à¦•à§‹à¦¨à§‹ à¦ªà§à¦°à¦¯à¦¼à§‹à¦œà¦¨à§‡ à¦†à¦®à¦¾à¦¦à§‡à¦° à¦à¦¡à¦®à¦¿à¦¨à§‡à¦° à¦¸à¦¾à¦¥à§‡ à¦¯à§‹à¦—à¦¾à¦¯à§‹à¦— à¦•à¦°à§à¦¨:</b>\n\n@KAMRUL_ADMIN", { parse_mode: 'HTML' });
    } else if (text === "ðŸŽ à¦†à¦®à¦¾à¦° à¦°à§‡à¦«à¦¾à¦°à§‡à¦²") {
      // FIXED: Removed space in referral link
      const referralLink = `https://t.me/IncomeBoxXBot?start=${userId}`;
      await bot!.sendMessage(chatId, `ðŸ”— <b>à¦†à¦ªà¦¨à¦¾à¦° à¦°à§‡à¦«à¦¾à¦°à§‡à¦² à¦²à¦¿à¦‚à¦•:</b>\n<code>${referralLink}</code>\n\nà¦†à¦ªà¦¨à¦¾à¦° à¦²à¦¿à¦‚à¦• à¦¥à§‡à¦•à§‡ à¦•à§‡à¦‰ à¦œà§Ÿà§‡à¦¨ à¦•à¦°à§‡ à¦ªà§à¦°à¦¥à¦® à¦•à¦¾à¦œ à¦¸à¦®à§à¦ªà¦¨à§à¦¨ à¦•à¦°à¦²à§‡à¦‡ à¦†à¦ªà¦¨à¦¿ à¦ªà¦¾à¦¬à§‡à¦¨ 4 BDT à¦¬à§‹à¦¨à¦¾à¦¸!`, { parse_mode: 'HTML' });
    } else if (text === "ðŸ”° à¦†à¦®à¦¿ à¦¨à¦¤à§à¦¨") {
      await bot!.sendMessage(chatId, "ðŸ“– <b>à¦•à¦¾à¦œà§‡à¦° à¦¨à¦¿à§Ÿà¦®à¦¾à¦¬à¦²à§€:</b> \nà§§. à¦ªà§à¦°à¦¥à¦®à§‡ à¦•à¦¾à¦œ à¦…à¦ªà¦¶à¦¨à§‡ à¦¯à¦¾à¦¨à¥¤\nà§¨. à¦¸à§‹à¦¶à§à¦¯à¦¾à¦² à¦®à¦¿à¦¡à¦¿à§Ÿà¦¾ à¦Ÿà¦¾à¦¸à§à¦• à¦•à¦®à¦ªà§à¦²à¦¿à¦Ÿ à¦•à¦°à§à¦¨à¥¤\nà§©. à¦Ÿà¦¾à¦¸à§à¦• à¦œà¦®à¦¾ à¦¦à¦¿à¦¨à¥¤\n\nðŸ’° à¦ªà§‡à¦®à§‡à¦¨à§à¦Ÿ à¦ªà¦¾à¦¬à§‡à¦¨ à§¨-à§­à§¨ à¦˜à¦¨à§à¦Ÿà¦¾à¦° à¦®à¦§à§à¦¯à§‡à¥¤", { parse_mode: 'HTML' });
    } else if (text === "ðŸŒ à¦­à¦¾à¦·à¦¾ à¦ªà¦°à¦¿à¦¬à¦°à§à¦¤à¦¨") {
      await bot!.sendMessage(chatId, "ðŸŒ <b>à¦­à¦¾à¦·à¦¾ à¦¨à¦¿à¦°à§à¦¬à¦¾à¦šà¦¨ à¦•à¦°à§à¦¨:</b> \nà¦¬à¦°à§à¦¤à¦®à¦¾à¦¨à§‡ à¦¶à§à¦§à§ à¦¬à¦¾à¦‚à¦²à¦¾ à¦‰à¦ªà¦²à¦¬à§à¦§à¥¤", { parse_mode: 'HTML' });
    } else if (text === "ðŸ“¸ à¦‡à¦¨à§à¦¸à¦Ÿà¦¾à¦—à§à¦°à¦¾à¦® à¦•à¦¾à¦œ") {
      // FIXED: Check membership before starting task
      const isMember = await checkMembership(userId);
      if (!isMember) {
        await bot!.sendMessage(chatId, "âš ï¸ <b>à¦¦à¦¯à¦¼à¦¾ à¦•à¦°à§‡ à¦ªà§à¦°à¦¥à¦®à§‡ à¦šà§à¦¯à¦¾à¦¨à§‡à¦²à§‡ à¦œà¦¯à¦¼à§‡à¦¨ à¦•à¦°à§à¦¨!</b>", { parse_mode: 'HTML' });
        return;
      }
      await bot!.sendMessage(chatId, "ðŸŸ£ <b>à¦¸à¦¿à¦²à§‡à¦•à§à¦Ÿ à¦•à¦°à§à¦¨:</b>", { 
        parse_mode: 'HTML',
        reply_markup: {
          keyboard: [[{ text: "à¦‡à¦¨à§à¦¸à¦Ÿà¦¾à¦—à§à¦°à¦¾à¦® 2fa (à§³4.30)" }], [{ text: "âŒ à¦¬à¦¾à¦¤à¦¿à¦²" }]],
          resize_keyboard: true
        }
      });
    } else if (text === "à¦‡à¦¨à§à¦¸à¦Ÿà¦¾à¦—à§à¦°à¦¾à¦® 2fa (à§³4.30)") {
      if (!db) {
        await bot!.sendMessage(chatId, "âš ï¸ à¦¡à¦¾à¦Ÿà¦¾à¦¬à§‡à¦¸ à¦¸à¦‚à¦¯à§à¦•à§à¦¤ à¦¨à§‡à¦‡à¥¤", { parse_mode: 'HTML' });
        return;
      }
      
      // FIXED: Check membership before starting task
      const isMember = await checkMembership(userId);
      if (!isMember) {
        await bot!.sendMessage(chatId, "âš ï¸ <b>à¦¦à¦¯à¦¼à¦¾ à¦•à¦°à§‡ à¦ªà§à¦°à¦¥à¦®à§‡ à¦šà§à¦¯à¦¾à¦¨à§‡à¦²à§‡ à¦œà¦¯à¦¼à§‡à¦¨ à¦•à¦°à§à¦¨!</b>", { parse_mode: 'HTML' });
        return;
      }
      
      try {
        const tasksSnapshot = await db.collection('tasks')
          .where('platform', '==', 'Instagram')
          .where('status', '==', 'pending')
          .limit(10)
          .get();
          
        if (tasksSnapshot.empty) {
          await bot!.sendMessage(chatId, "ðŸ˜” <b>à¦¦à§à¦ƒà¦–à¦¿à¦¤, à¦¬à¦°à§à¦¤à¦®à¦¾à¦¨à§‡ à¦•à§‹à¦¨à§‹ à¦•à¦¾à¦œ à¦‰à¦ªà¦²à¦¬à§à¦§ à¦¨à§‡à¦‡à¥¤ à¦à¦¡à¦®à¦¿à¦¨ à¦•à¦¾à¦œ à¦…à§à¦¯à¦¾à¦¡ à¦•à¦°à¦²à§‡ à¦†à¦¬à¦¾à¦° à¦šà§‡à¦·à§à¦Ÿà¦¾ à¦•à¦°à§à¦¨à¥¤</b>", { parse_mode: 'HTML' });
          return;
        }

        let taskDoc = null;
        let taskData = null;
        for (const doc of tasksSnapshot.docs) {
          const data = doc.data();
          const attemptedBy = data.attemptedBy || [];
          if (!attemptedBy.includes(userId)) {
            taskDoc = doc;
            taskData = data;
            break;
          }
        }

        if (!taskDoc) {
          await bot!.sendMessage(chatId, "ðŸ˜” <b>à¦¦à§à¦ƒà¦–à¦¿à¦¤, à¦†à¦ªà¦¨à¦¾à¦° à¦œà¦¨à§à¦¯ à¦¬à¦°à§à¦¤à¦®à¦¾à¦¨à§‡ à¦•à§‹à¦¨à§‹ à¦¨à¦¤à§à¦¨ à¦•à¦¾à¦œ à¦‰à¦ªà¦²à¦¬à§à¦§ à¦¨à§‡à¦‡à¥¤</b>", { parse_mode: 'HTML' });
          return;
        }

        await db.collection('tasks').doc(taskDoc.id).update({ status: 'assigned' });

        // FIXED: Clear any existing timeout
        if (userStates[userId]?.timeoutId) {
          clearTimeout(userStates[userId].timeoutId);
        }
        
        const timeoutId = setTimeout(async () => {
          if (userStates[userId] && userStates[userId].task_doc_id === taskDoc.id) {
            try {
              if (db) await db.collection('tasks').doc(taskDoc.id).update({ status: 'pending' });
            } catch(e) { console.error(e); }
            delete userStates[userId];
            await bot!.sendMessage(chatId, "â³ <b>à¦†à¦ªà¦¨à¦¾à¦° à¦Ÿà¦¾à¦¸à§à¦• à¦¬à¦¾à¦¤à¦¿à¦² à¦•à¦°à¦¾ à¦¹à¦¯à¦¼à§‡à¦›à§‡ à¦•à¦¾à¦°à¦£ à¦¸à¦®à¦¯à¦¼ à¦¶à§‡à¦· à¦¹à¦¯à¦¼à§‡ à¦—à§‡à¦›à§‡à¥¤</b>", { parse_mode: 'HTML', ...getMainKeyboard() });
          }
        }, 30 * 60 * 1000);

        userStates[userId] = {
          step: 'AWAITING_ACCOUNT_CREATION',
          task_doc_id: taskDoc.id,
          platform: 'Instagram',
          assigned_username: taskData.username,
          password: taskData.password,
          timeoutId
        };

        const botText = `ðŸ‘¤ <b>à¦‡à¦‰à¦œà¦¾à¦°à¦¨à§‡à¦®:</b> <code>${taskData.username}</code>\nðŸ” <b>à¦ªà¦¾à¦¸à¦“à¦¯à¦¼à¦¾à¦°à§à¦¡:</b> <code>${taskData.password}</code>\n\nðŸ“¸ à¦‰à¦ªà¦°à§‡à¦° à¦‡à¦‰à¦œà¦¾à¦°à¦¨à§‡à¦® à¦à¦¬à¦‚ à¦ªà¦¾à¦¸à¦“à¦¯à¦¼à¦¾à¦°à§à¦¡ à¦¦à¦¿à¦¯à¦¼à§‡ à¦…à§à¦¯à¦¾à¦•à¦¾à¦‰à¦¨à§à¦Ÿà§‡ à¦²à¦—à¦‡à¦¨ à¦•à¦°à§à¦¨à¥¤ à¦¤à¦¾à¦°à¦ªà¦° à¦¨à¦¿à¦šà§‡ <b>ðŸ” 2FA Set</b> à¦¬à¦¾à¦Ÿà¦¨à§‡ à¦•à§à¦²à¦¿à¦• à¦•à¦°à§à¦¨ ðŸ‘€`;
        await bot!.sendMessage(chatId, botText, { 
          parse_mode: 'HTML',
          reply_markup: {
            keyboard: [[{ text: "ðŸ” 2FA Set" }, { text: "âš™ï¸ à¦•à¦¿à¦­à¦¾à¦¬à§‡ à¦•à¦¾à¦œ à¦•à¦°à¦¬" }], [{ text: "âŒ à¦¬à¦¾à¦¤à¦¿à¦²" }]],
            resize_keyboard: true
          }
        });
      } catch (e) {
        console.error("Error fetching tasks", e);
        await bot!.sendMessage(chatId, "âš ï¸ à¦¡à¦¾à¦Ÿà¦¾à¦¬à§‡à¦¸ à¦à¦°à¦°à¥¤ à¦†à¦¬à¦¾à¦° à¦šà§‡à¦·à§à¦Ÿà¦¾ à¦•à¦°à§à¦¨à¥¤");
      }
      
    } else if (text === "ðŸ“˜ à¦«à§‡à¦¸à¦¬à§à¦• à¦•à¦¾à¦œ") {
      // FIXED: Check membership before starting task
      const isMember = await checkMembership(userId);
      if (!isMember) {
        await bot!.sendMessage(chatId, "âš ï¸ <b>à¦¦à¦¯à¦¼à¦¾ à¦•à¦°à§‡ à¦ªà§à¦°à¦¥à¦®à§‡ à¦šà§à¦¯à¦¾à¦¨à§‡à¦²à§‡ à¦œà¦¯à¦¼à§‡à¦¨ à¦•à¦°à§à¦¨!</b>", { parse_mode: 'HTML' });
        return;
      }
      await bot!.sendMessage(chatId, "ðŸŸ£ <b>à¦¸à¦¿à¦²à§‡à¦•à§à¦Ÿ à¦•à¦°à§à¦¨:</b>", { 
        parse_mode: 'HTML',
        reply_markup: {
          keyboard: [[{ text: "0 fnd cookies | 6.55à§³" }], [{ text: "âŒ à¦¬à¦¾à¦¤à¦¿à¦²" }]],
          resize_keyboard: true
        }
      });
    } else if (text === "0 fnd cookies | 6.55à§³") {
      if (!db) {
        await bot!.sendMessage(chatId, "âš ï¸ à¦¡à¦¾à¦Ÿà¦¾à¦¬à§‡à¦¸ à¦¸à¦‚à¦¯à§à¦•à§à¦¤ à¦¨à§‡à¦‡à¥¤", { parse_mode: 'HTML' });
        return;
      }

      // FIXED: Check membership before starting task
      const isMember = await checkMembership(userId);
      if (!isMember) {
        await bot!.sendMessage(chatId, "âš ï¸ <b>à¦¦à¦¯à¦¼à¦¾ à¦•à¦°à§‡ à¦ªà§à¦°à¦¥à¦®à§‡ à¦šà§à¦¯à¦¾à¦¨à§‡à¦²à§‡ à¦œà¦¯à¦¼à§‡à¦¨ à¦•à¦°à§à¦¨!</b>", { parse_mode: 'HTML' });
        return;
      }

      try {
        const tasksSnapshot = await db.collection('tasks')
          .where('platform', '==', 'Facebook')
          .where('status', '==', 'pending')
          .limit(10)
          .get();
          
        if (tasksSnapshot.empty) {
          await bot!.sendMessage(chatId, "ðŸ˜” <b>à¦¦à§à¦ƒà¦–à¦¿à¦¤, à¦¬à¦°à§à¦¤à¦®à¦¾à¦¨à§‡ à¦•à§‹à¦¨à§‹ à¦•à¦¾à¦œ à¦‰à¦ªà¦²à¦¬à§à¦§ à¦¨à§‡à¦‡à¥¤ à¦à¦¡à¦®à¦¿à¦¨ à¦•à¦¾à¦œ à¦…à§à¦¯à¦¾à¦¡ à¦•à¦°à¦²à§‡ à¦†à¦¬à¦¾à¦° à¦šà§‡à¦·à§à¦Ÿà¦¾ à¦•à¦°à§à¦¨à¥¤</b>", { parse_mode: 'HTML' });
          return;
        }

        let taskDoc = null;
        let taskData = null;
        for (const doc of tasksSnapshot.docs) {
          const data = doc.data();
          const attemptedBy = data.attemptedBy || [];
          if (!attemptedBy.includes(userId)) {
            taskDoc = doc;
            taskData = data;
            break;
          }
        }

        if (!taskDoc) {
          await bot!.sendMessage(chatId, "ðŸ˜” <b>à¦¦à§à¦ƒà¦–à¦¿à¦¤, à¦†à¦ªà¦¨à¦¾à¦° à¦œà¦¨à§à¦¯ à¦¬à¦°à§à¦¤à¦®à¦¾à¦¨à§‡ à¦•à§‹à¦¨à§‹ à¦¨à¦¤à§à¦¨ à¦•à¦¾à¦œ à¦‰à¦ªà¦²à¦¬à§à¦§ à¦¨à§‡à¦‡à¥¤</b>", { parse_mode: 'HTML' });
          return;
        }

        await db.collection('tasks').doc(taskDoc.id).update({ status: 'assigned' });

        // FIXED: Clear any existing timeout
        if (userStates[userId]?.timeoutId) {
          clearTimeout(userStates[userId].timeoutId);
        }
        
        const timeoutId = setTimeout(async () => {
          if (userStates[userId] && userStates[userId].task_doc_id === taskDoc.id) {
            try {
              if (db) await db.collection('tasks').doc(taskDoc.id).update({ status: 'pending' });
            } catch(e) { console.error(e); }
            delete userStates[userId];
            await bot!.sendMessage(chatId, "â³ <b>à¦†à¦ªà¦¨à¦¾à¦° à¦Ÿà¦¾à¦¸à§à¦• à¦¬à¦¾à¦¤à¦¿à¦² à¦•à¦°à¦¾ à¦¹à¦¯à¦¼à§‡à¦›à§‡ à¦•à¦¾à¦°à¦£ à¦¸à¦®à¦¯à¦¼ à¦¶à§‡à¦· à¦¹à¦¯à¦¼à§‡ à¦—à§‡à¦›à§‡à¥¤</b>", { parse_mode: 'HTML', ...getMainKeyboard() });
          }
        }, 30 * 60 * 1000);

        userStates[userId] = {
          step: 'FB_AWAITING_UID_BTN',
          task_doc_id: taskDoc.id,
          platform: 'Facebook',
          firstName: taskData.firstName || taskData.first_name || '',
          lastName: taskData.lastName || taskData.last_name || '',
          password: taskData.password || '',
          timeoutId
        };

        const botText = `ðŸ‘¤ <b>à¦¨à¦¾à¦®à§‡à¦° à¦ªà§à¦°à¦¥à¦®à¦¾à¦‚à¦¶:</b> <code>${taskData.firstName || taskData.first_name || ''}</code>\nðŸ‘¤ <b>à¦¨à¦¾à¦®à§‡à¦° à¦¶à§‡à¦·à¦¾à¦‚à¦¶:</b> <code>${taskData.lastName || taskData.last_name || ''}</code>\nðŸ” <b>à¦ªà¦¾à¦¸à¦“à¦¯à¦¼à¦¾à¦°à§à¦¡:</b> <code>${taskData.password || ''}</code>\n\nðŸ†” à¦‰à¦ªà¦°à§‡à¦° à¦¤à¦¥à§à¦¯ à¦¦à¦¿à¦¯à¦¼à§‡ à¦…à§à¦¯à¦¾à¦•à¦¾à¦‰à¦¨à§à¦Ÿà§‡ à¦²à¦—à¦‡à¦¨ à¦•à¦°à§à¦¨à¥¤ à¦¤à¦¾à¦°à¦ªà¦° <b>ðŸŸ¢ Send UID</b> à¦¬à¦¾à¦Ÿà¦¨à§‡ à¦•à§à¦²à¦¿à¦• à¦•à¦°à§à¦¨ ðŸ˜Ž`;
        await bot!.sendMessage(chatId, botText, { 
          parse_mode: 'HTML',
          reply_markup: {
            keyboard: [[{ text: "ðŸŸ¢ Send UID" }], [{ text: "ðŸ¤« à¦•à¦¿à¦­à¦¾à¦¬à§‡ à¦•à¦¾à¦œ à¦•à¦°à¦¬" }], [{ text: "âŒ à¦¬à¦¾à¦¤à¦¿à¦²" }]],
            resize_keyboard: true
          }
        });
      } catch (e) {
        console.error("Error fetching tasks", e);
        await bot!.sendMessage(chatId, "âš ï¸ à¦¡à¦¾à¦Ÿà¦¾à¦¬à§‡à¦¸ à¦à¦°à¦°à¥¤ à¦†à¦¬à¦¾à¦° à¦šà§‡à¦·à§à¦Ÿà¦¾ à¦•à¦°à§à¦¨à¥¤");
      }
    }
  });
}

// ==========================================
// Express Server
// ==========================================

async function startServer() {
  // API routes
  app.get('/api/health', (req, res) => {
    res.json({ status: 'ok', botActive: !!bot });
  });

  // Raw file endpoints for user export
  app.get('/raw/server', (req, res) => {
    res.sendFile(path.join(process.cwd(), 'server.ts'), { headers: { 'Content-Type': 'text/plain' } });
  });
  app.get('/raw/package', (req, res) => {
    res.sendFile(path.join(process.cwd(), 'package.json'), { headers: { 'Content-Type': 'text/plain' } });
  });
  app.get('/raw/admin', (req, res) => {
    res.sendFile(path.join(process.cwd(), 'admin.html'), { headers: { 'Content-Type': 'text/plain' } });
  });

  // Vite middleware for development
  if (process.env.NODE_ENV !== 'production') {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), 'dist');
    app.use(express.static(distPath));
    app.get('*', (req, res) => {
      res.sendFile(path.join(distPath, 'index.html'));
    });
  }

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`ðŸš€ Server running on port ${PORT}`);
  });
}

startServer();

// ==========================================
// Graceful Shutdown
// ==========================================
process.on('SIGINT', () => {
  if (bot) bot.stopPolling();
  console.log('ðŸ‘‹ Shutting down...');
  process.exit();
});

process.on('SIGTERM', () => {
  if (bot) bot.stopPolling();
  console.log('ðŸ‘‹ Shutting down...');
  process.exit();
});
