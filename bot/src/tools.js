// OpenAIのfunction callingで呼び出されるツール群。
// 実装はモックですが、実運用時はそれぞれ
//  - 物件管理システム (例: ITANDI / SUUMO Business / 自社DB)
//  - スマート申込 / 電子契約サービス
//  - Googleカレンダー / Slack / CRM
// などへの呼び出しに差し替えてください。

const id = (prefix) => `${prefix}_${Math.random().toString(36).slice(2, 10)}`;

const TIME_BAND_TO_HHMM = {
  morning: '10:30',
  afternoon: '14:00',
  evening: '18:00',
};

function nextWeekdays(count, fromDate = new Date()) {
  const out = [];
  const d = new Date(fromDate);
  while (out.length < count) {
    d.setDate(d.getDate() + 1);
    const dow = d.getDay();
    if (dow !== 0 && dow !== 6) out.push(new Date(d));
  }
  return out;
}

export const TOOL_DEFS = [
  {
    type: 'function',
    function: {
      name: 'update_customer_profile',
      description: 'お客様から聞き取れた希望条件・属性を保存する。複数フィールドをまとめて更新可。',
      parameters: {
        type: 'object',
        properties: {
          area: { type: 'string', description: '希望エリア（例: 文京区, 池袋駅徒歩10分以内）' },
          layout: { type: 'string', description: '間取り（例: 1LDK, 2DK）' },
          budget_jpy: { type: 'integer', description: '家賃予算（月額・円）' },
          move_in_by: { type: 'string', description: '入居希望時期（YYYY-MM 推奨）' },
          household: { type: 'string', description: '世帯構成（例: 単身, 夫婦+子1）' },
          notes: { type: 'string', description: 'その他要望（ペット可、駐車場必須など）' },
        },
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'propose_viewing_slots',
      description: 'お客様の希望条件に合う内見候補日時を最大3件提示する。',
      parameters: {
        type: 'object',
        properties: {
          property_keyword: { type: 'string', description: '物件名 / エリア / 物件ID' },
          preferred_dates: {
            type: 'array',
            items: { type: 'string' },
            description: 'お客様の希望日（YYYY-MM-DD）。空なら直近の平日を提案する',
          },
          preferred_time_band: {
            type: 'string',
            enum: ['morning', 'afternoon', 'evening'],
            description: '希望時間帯',
          },
        },
        required: ['property_keyword'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'confirm_viewing',
      description: 'propose_viewing_slotsで提示した候補のうち1つを確定する。',
      parameters: {
        type: 'object',
        properties: {
          slot_id: { type: 'string', description: '候補のslot_id' },
          customer_name: { type: 'string', description: 'お客様氏名（聞き取れていれば）' },
        },
        required: ['slot_id'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'reschedule_viewing',
      description: '既存の内見予約を別日時に変更する。',
      parameters: {
        type: 'object',
        properties: {
          viewing_id: { type: 'string' },
          new_date: { type: 'string', description: 'YYYY-MM-DD' },
          new_time: { type: 'string', description: 'HH:MM' },
        },
        required: ['viewing_id', 'new_date', 'new_time'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'cancel_viewing',
      description: '内見予約をキャンセルする。',
      parameters: {
        type: 'object',
        properties: {
          viewing_id: { type: 'string' },
          reason: { type: 'string' },
        },
        required: ['viewing_id'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'get_application_status',
      description: '入居申込みの審査状況を取得する。',
      parameters: {
        type: 'object',
        properties: {
          application_id: { type: 'string', description: '申込ID。お客様が分からない場合は省略可' },
        },
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'request_documents',
      description: 'お客様に提出が必要な書類を案内する。',
      parameters: {
        type: 'object',
        properties: {
          documents: {
            type: 'array',
            items: { type: 'string' },
            description: '例: ["本人確認書類","源泉徴収票","在籍確認先"]',
          },
          deadline: { type: 'string', description: '提出期限（YYYY-MM-DD）' },
        },
        required: ['documents'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'mark_document_received',
      description: 'お客様から提出された書類を受領済みとして記録する。',
      parameters: {
        type: 'object',
        properties: {
          document: { type: 'string' },
        },
        required: ['document'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'escalate_to_human',
      description: 'AIだけでは対応できない / 価格交渉 / クレーム / 契約条件変更 など、人間担当者にエスカレーションする。',
      parameters: {
        type: 'object',
        properties: {
          reason: { type: 'string', description: 'エスカレーション理由' },
          urgency: { type: 'string', enum: ['low', 'normal', 'high'] },
        },
        required: ['reason'],
      },
    },
  },
];

export async function executeTool(name, args, ctx) {
  const { store, userId, notifyEscalation } = ctx;

  switch (name) {
    case 'update_customer_profile': {
      store.updateProfile(userId, args);
      return { ok: true, profile: store.getState(userId).profile };
    }

    case 'propose_viewing_slots': {
      const band = args.preferred_time_band || 'afternoon';
      const time = TIME_BAND_TO_HHMM[band];
      const dates = (args.preferred_dates && args.preferred_dates.length)
        ? args.preferred_dates.slice(0, 3).map((s) => new Date(s))
        : nextWeekdays(3);
      const candidates = dates.map((d) => ({
        slot_id: id('slot'),
        date: d.toISOString().slice(0, 10),
        time,
        property: args.property_keyword,
      }));
      store.setCandidates(userId, candidates);
      return { candidates };
    }

    case 'confirm_viewing': {
      const cand = store.findCandidate(userId, args.slot_id);
      if (!cand) {
        return { ok: false, error: 'slot_idが見つかりません。propose_viewing_slotsを呼び直してください。' };
      }
      const viewing = {
        viewing_id: id('vw'),
        date: cand.date,
        time: cand.time,
        property: cand.property,
        customer_name: args.customer_name || null,
        status: 'confirmed',
        meeting_point: '物件エントランス前（現地集合）',
        agent: process.env.SHOP_AGENT_NAME || '担当者',
        agent_phone: process.env.SHOP_AGENT_PHONE || '',
      };
      store.addViewing(userId, viewing);
      return { ok: true, viewing };
    }

    case 'reschedule_viewing': {
      const vw = store.findViewing(userId, args.viewing_id);
      if (!vw) return { ok: false, error: 'viewing_idが見つかりません' };
      vw.date = args.new_date;
      vw.time = args.new_time;
      vw.status = 'rescheduled';
      return { ok: true, viewing: vw };
    }

    case 'cancel_viewing': {
      const vw = store.findViewing(userId, args.viewing_id);
      if (!vw) return { ok: false, error: 'viewing_idが見つかりません' };
      vw.status = 'cancelled';
      vw.cancel_reason = args.reason || '';
      return { ok: true, viewing: vw };
    }

    case 'get_application_status': {
      // モック: 実運用は申込み管理システムへ問い合わせる
      const state = store.getState(userId);
      const app = state.application || {
        application_id: args.application_id || id('app'),
        property: state.viewings[0]?.property || '未確定',
        stage: 'screening',
        stage_label: '入居審査中',
        next_action: '保証会社の審査結果待ち（通常 2〜3 営業日）',
      };
      store.setApplication(userId, app);
      return { ok: true, application: app, documents: state.documents };
    }

    case 'request_documents': {
      const required = args.documents;
      for (const doc of required) {
        const cur = store.getState(userId).documents[doc];
        if (!cur) store.setDocument(userId, doc, 'requested');
      }
      return { ok: true, requested: required, deadline: args.deadline || null };
    }

    case 'mark_document_received': {
      store.setDocument(userId, args.document, 'received');
      return { ok: true, documents: store.getState(userId).documents };
    }

    case 'escalate_to_human': {
      store.escalate(userId);
      const payload = {
        userId,
        reason: args.reason,
        urgency: args.urgency || 'normal',
        profile: store.getState(userId).profile,
        viewings: store.getState(userId).viewings,
      };
      await notifyEscalation?.(payload);
      return { ok: true, escalated: true };
    }

    default:
      return { ok: false, error: `unknown tool: ${name}` };
  }
}
