// UIヒントを LINE Messaging API のメッセージオブジェクトに変換する。
// LINE replyMessage は最大5メッセージまで送れる。

const DOW = ['日', '月', '火', '水', '木', '金', '土'];

function formatJaDate(yyyymmdd) {
  const d = new Date(yyyymmdd + 'T00:00:00');
  if (Number.isNaN(d.getTime())) return yyyymmdd;
  return `${d.getMonth() + 1}/${d.getDate()}(${DOW[d.getDay()]})`;
}

function row(label, value) {
  return {
    type: 'box',
    layout: 'baseline',
    spacing: 'sm',
    contents: [
      { type: 'text', text: label, color: '#888888', size: 'sm', flex: 2 },
      {
        type: 'text',
        text: String(value || '-'),
        color: '#111827',
        size: 'sm',
        flex: 5,
        wrap: true,
        weight: 'bold',
      },
    ],
  };
}

function buildSlotsQuickReply(textMessage, hint) {
  // LINEのquickReplyラベルは最大20文字、items最大13個
  textMessage.quickReply = {
    items: hint.candidates.slice(0, 13).map((c) => {
      const label = `${formatJaDate(c.date)} ${c.time}`.slice(0, 20);
      return {
        type: 'action',
        action: {
          type: 'message',
          label,
          text: `${formatJaDate(c.date)} ${c.time} でお願いします`,
        },
      };
    }),
  };
}

function buildViewingCard(viewing) {
  const isCancelled = viewing.status === 'cancelled';
  const headerColor = isCancelled ? '#9CA3AF' : '#008a45';
  const title = isCancelled
    ? '内見予約 キャンセル'
    : viewing.status === 'rescheduled'
      ? '内見予約 変更'
      : '内見予約 確定';

  return {
    type: 'flex',
    altText: `${title}: ${formatJaDate(viewing.date)} ${viewing.time}`,
    contents: {
      type: 'bubble',
      header: {
        type: 'box',
        layout: 'vertical',
        backgroundColor: headerColor,
        paddingAll: 'lg',
        contents: [
          { type: 'text', text: title, color: '#FFFFFF', weight: 'bold', size: 'lg' },
          {
            type: 'text',
            text: viewing.property || '物件未指定',
            color: '#FFFFFF',
            size: 'sm',
            wrap: true,
          },
        ],
      },
      body: {
        type: 'box',
        layout: 'vertical',
        spacing: 'md',
        contents: [
          row('日時', `${formatJaDate(viewing.date)} ${viewing.time}`),
          row('集合場所', viewing.meeting_point),
          row('担当', viewing.agent),
          row('連絡先', viewing.agent_phone),
        ],
      },
      footer: isCancelled
        ? undefined
        : {
            type: 'box',
            layout: 'vertical',
            spacing: 'sm',
            contents: [
              {
                type: 'button',
                style: 'secondary',
                height: 'sm',
                action: {
                  type: 'message',
                  label: '日程を変更したい',
                  text: '内見の日程を変更したいです',
                },
              },
              {
                type: 'button',
                style: 'secondary',
                height: 'sm',
                action: {
                  type: 'message',
                  label: 'キャンセルしたい',
                  text: '内見をキャンセルしたいです',
                },
              },
            ],
          },
    },
  };
}

function buildDocumentsCard(requested, deadline) {
  const items = requested.map((d) => ({
    type: 'box',
    layout: 'baseline',
    contents: [
      { type: 'text', text: '・', size: 'md', flex: 0 },
      { type: 'text', text: String(d), size: 'md', wrap: true, flex: 5 },
    ],
  }));

  const bubble = {
    type: 'bubble',
    header: {
      type: 'box',
      layout: 'vertical',
      backgroundColor: '#ff8a00',
      paddingAll: 'lg',
      contents: [
        { type: 'text', text: '必要書類のご案内', color: '#FFFFFF', weight: 'bold', size: 'lg' },
      ],
    },
    body: {
      type: 'box',
      layout: 'vertical',
      spacing: 'sm',
      contents: items,
    },
  };
  if (deadline) {
    bubble.footer = {
      type: 'box',
      layout: 'vertical',
      contents: [
        {
          type: 'text',
          text: `提出期限: ${deadline}`,
          color: '#888888',
          size: 'sm',
          align: 'center',
        },
      ],
    };
  }
  return { type: 'flex', altText: '必要書類のご案内', contents: bubble };
}

/**
 * テキスト返答 + UIヒントから LINE メッセージ配列を組み立てる。
 * - quick reply は1メッセージにしか付けられないので最初のテキストに添付
 * - flex メッセージは追加メッセージとしてpush
 * - LINE replyMessage は最大5メッセージ
 */
export function buildLineMessages(textReply, uiHints = []) {
  const text = { type: 'text', text: textReply && textReply.trim() ? textReply : 'ご確認ください。' };

  const slots = uiHints.find((h) => h.type === 'slots_quick_reply');
  if (slots) buildSlotsQuickReply(text, slots);

  const messages = [text];
  for (const h of uiHints) {
    if (h.type === 'viewing_card') messages.push(buildViewingCard(h.viewing));
    else if (h.type === 'documents_card') messages.push(buildDocumentsCard(h.requested, h.deadline));
  }
  return messages.slice(0, 5);
}

/** 友だち追加時のウェルカムメッセージ（よくある要件のQuick Reply付き） */
export function buildWelcomeMessage() {
  return {
    type: 'text',
    text:
      '友だち追加ありがとうございます！\n' +
      '物件の内見予約や、申込み後のご相談をこちらで承ります。\n' +
      'まずはご要件をお選びください😊',
    quickReply: {
      items: [
        {
          type: 'action',
          action: { type: 'message', label: '内見予約したい', text: '内見の予約をしたいです' },
        },
        {
          type: 'action',
          action: { type: 'message', label: '物件を探す', text: 'おすすめの物件を教えてください' },
        },
        {
          type: 'action',
          action: { type: 'message', label: '申込みの状況', text: '申込みの審査状況を教えてください' },
        },
        {
          type: 'action',
          action: { type: 'message', label: '担当者と話したい', text: '担当者の方とお話したいです' },
        },
      ],
    },
  };
}
