import line from '@line/bot-sdk';

export const lineConfig = {
  channelAccessToken: process.env.LINE_CHANNEL_ACCESS_TOKEN || '',
  channelSecret: process.env.LINE_CHANNEL_SECRET || '',
};

export const lineClient = new line.Client(lineConfig);

export async function notifyEscalation(payload) {
  const to = process.env.ESCALATION_NOTIFY_TO;
  const text =
    `🚨 エスカレーション\n` +
    `理由: ${payload.reason}\n` +
    `緊急度: ${payload.urgency}\n` +
    `userId: ${payload.userId}\n` +
    (payload.profile?.area ? `希望: ${payload.profile.area} / ${payload.profile.layout || '-'} / ${payload.profile.budget_jpy || '-'}円\n` : '') +
    (payload.viewings?.length ? `内見件数: ${payload.viewings.length}` : '');

  if (to && process.env.LINE_CHANNEL_ACCESS_TOKEN) {
    try {
      await lineClient.pushMessage(to, { type: 'text', text });
    } catch (e) {
      console.error('[escalation push failed]', e.message);
    }
  } else {
    console.warn('[escalation]', text);
  }
}
