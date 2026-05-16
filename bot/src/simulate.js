// CLIから対話してエージェントを試すスクリプト
//   $ node src/simulate.js
//   You> 文京区で1LDKを探しています
//   Bot> ...
import 'dotenv/config';
import readline from 'node:readline';
import { runAgent } from './agent.js';
import { store } from './store.js';
import { buildLineMessages } from './messages.js';

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
const userId = process.env.SIM_USER_ID || 'cli-user';
const showJson = process.env.SIM_JSON === '1';

function renderMessage(m, i) {
  const lines = [];
  if (m.type === 'text') {
    lines.push(`  [msg${i}:text] ${m.text}`);
    if (m.quickReply?.items?.length) {
      const labels = m.quickReply.items.map((it) => `[${it.action.label}]`).join(' ');
      lines.push(`    quickReply: ${labels}`);
    }
  } else if (m.type === 'flex') {
    lines.push(`  [msg${i}:flex] ${m.altText}`);
    const bubble = m.contents;
    const headerTexts = (bubble.header?.contents || [])
      .filter((c) => c.type === 'text')
      .map((c) => c.text);
    if (headerTexts.length) lines.push(`    header: ${headerTexts.join(' / ')}`);
    for (const c of bubble.body?.contents || []) {
      const tx = (c.contents || []).filter((x) => x.type === 'text').map((x) => x.text);
      if (tx.length) lines.push(`    body  : ${tx.join('  ')}`);
    }
    for (const c of bubble.footer?.contents || []) {
      if (c.type === 'text') lines.push(`    footer: ${c.text}`);
      else if (c.type === 'button') lines.push(`    button: [${c.action.label}]`);
    }
  }
  return lines.join('\n');
}

function ask() {
  rl.question('You> ', async (text) => {
    const t = text.trim();
    if (!t) return ask();
    if (t === '/exit') return rl.close();
    try {
      const reply = await runAgent({
        userId,
        userText: t,
        notifyEscalation: async (p) => console.log('  [escalation]', p.reason),
      });
      const uiHints = store.drainUi(userId);
      const messages = buildLineMessages(reply, uiHints);
      console.log(`Bot> ${reply}`);
      if (messages.length > 1 || messages[0]?.quickReply) {
        console.log('--- LINE messages ---');
        messages.forEach((m, i) => console.log(renderMessage(m, i)));
        if (showJson) console.log(JSON.stringify(messages, null, 2));
        console.log('---------------------');
      }
      console.log('');
    } catch (e) {
      console.error('  [error]', e.message);
    }
    ask();
  });
}

console.log('Type your message. /exit to quit.');
console.log('(SIM_JSON=1 to also print raw LINE message JSON)\n');
ask();
