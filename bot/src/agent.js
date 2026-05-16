import OpenAI from 'openai';
import { SYSTEM_PROMPT } from './prompts.js';
import { TOOL_DEFS, executeTool } from './tools.js';
import { store } from './store.js';

const MAX_TOOL_ITERATIONS = 5;

const openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
const MODEL = process.env.OPENAI_MODEL || 'gpt-4o-mini';

/**
 * 1ターン分の会話を回す。OpenAIがツール呼び出しを返すあいだループし、
 * 最終的にお客様に返すテキストを返却する。
 */
export async function runAgent({ userId, userText, notifyEscalation }) {
  store.appendMessage(userId, { role: 'user', content: userText });

  for (let iter = 0; iter < MAX_TOOL_ITERATIONS; iter++) {
    const history = store.getMessages(userId);
    const resp = await openai.chat.completions.create({
      model: MODEL,
      temperature: 0.4,
      messages: [{ role: 'system', content: SYSTEM_PROMPT }, ...history],
      tools: TOOL_DEFS,
      tool_choice: 'auto',
    });

    const msg = resp.choices[0].message;

    const assistantMsg = { role: 'assistant', content: msg.content ?? null };
    if (msg.tool_calls && msg.tool_calls.length > 0) {
      assistantMsg.tool_calls = msg.tool_calls;
    }
    store.appendMessage(userId, assistantMsg);

    if (msg.tool_calls && msg.tool_calls.length > 0) {
      for (const tc of msg.tool_calls) {
        let args = {};
        try {
          args = JSON.parse(tc.function.arguments || '{}');
        } catch (e) {
          args = {};
        }
        const result = await executeTool(tc.function.name, args, {
          store,
          userId,
          notifyEscalation,
        });
        store.appendMessage(userId, {
          role: 'tool',
          tool_call_id: tc.id,
          content: JSON.stringify(result),
        });
      }
      continue; // ツール結果を踏まえてもう一度モデルに考えさせる
    }

    return msg.content || 'すみません、もう一度お聞かせいただけますか？';
  }

  return 'お問い合わせ内容を担当者に引き継ぎますので、少々お待ちください。';
}
