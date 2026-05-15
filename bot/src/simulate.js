// CLIから対話してエージェントを試すスクリプト
//   $ node src/simulate.js
//   You> 文京区で1LDKを探しています
//   Bot> ...
import 'dotenv/config';
import readline from 'node:readline';
import { runAgent } from './agent.js';

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
const userId = process.env.SIM_USER_ID || 'cli-user';

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
      console.log(`Bot> ${reply}\n`);
    } catch (e) {
      console.error('  [error]', e.message);
    }
    ask();
  });
}

console.log('Type your message. /exit to quit.\n');
ask();
