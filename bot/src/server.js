import 'dotenv/config';
import express from 'express';
import line from '@line/bot-sdk';
import { lineConfig, lineClient, notifyEscalation } from './line.js';
import { runAgent } from './agent.js';
import { store } from './store.js';
import { buildLineMessages, buildWelcomeMessage } from './messages.js';

const app = express();
const PORT = Number(process.env.PORT || 3000);

app.get('/health', (_req, res) => res.json({ ok: true }));

// --- LINE Webhook ---
// line.middlewareが署名検証 + JSON parseを行うため、ここでは express.json() を使わない
app.post('/webhook', line.middleware(lineConfig), async (req, res) => {
  res.status(200).end(); // LINEには即時200を返し、処理はバックグラウンドで
  try {
    await Promise.all((req.body.events || []).map(handleEvent));
  } catch (err) {
    console.error('[webhook handler]', err);
  }
});

async function handleEvent(event) {
  if (event.type === 'follow') {
    return lineClient.replyMessage(event.replyToken, buildWelcomeMessage());
  }

  if (event.type !== 'message' || event.message.type !== 'text') {
    // 画像・スタンプ等は今は対象外。必要に応じて拡張。
    if (event.type === 'message') {
      return lineClient.replyMessage(event.replyToken, {
        type: 'text',
        text: '恐れ入りますが、テキストでご要件をお送りいただけますでしょうか。',
      });
    }
    return;
  }

  const userId = event.source.userId;
  const userText = event.message.text;

  try {
    const reply = await runAgent({ userId, userText, notifyEscalation });
    const uiHints = store.drainUi(userId);
    const messages = buildLineMessages(reply, uiHints);
    await lineClient.replyMessage(event.replyToken, messages);
  } catch (err) {
    console.error('[agent error]', err);
    await lineClient.replyMessage(event.replyToken, {
      type: 'text',
      text: 'システム側で一時的な不具合が発生しました。担当者にお繋ぎしますので、少々お待ちください。',
    }).catch(() => {});
    await notifyEscalation({
      userId,
      reason: `agent error: ${err.message}`,
      urgency: 'high',
    });
  }
}

// --- 開発/検証用エンドポイント: LINEを経由せずにagentと対話する ---
app.use(express.json());
app.post('/simulate', async (req, res) => {
  const { userId = 'dev-user', text } = req.body || {};
  if (!text) return res.status(400).json({ error: 'text required' });
  try {
    const reply = await runAgent({
      userId,
      userText: text,
      notifyEscalation: async (p) => console.log('[escalation:simulate]', p),
    });
    const uiHints = store.drainUi(userId);
    const messages = buildLineMessages(reply, uiHints);
    res.json({ reply, messages, uiHints });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.listen(PORT, () => {
  console.log(`engine-line-bot listening on :${PORT}`);
  if (!process.env.OPENAI_API_KEY) console.warn('  ! OPENAI_API_KEY is not set');
  if (!process.env.LINE_CHANNEL_ACCESS_TOKEN) console.warn('  ! LINE_CHANNEL_ACCESS_TOKEN is not set');
});
