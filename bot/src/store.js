// 会話履歴・予約・申込みステータスをユーザーごとに保持するシンプルなインメモリストア。
// 本番運用ではRedis / Postgres / DynamoDB等に差し替える前提。

const MAX_MESSAGES = 30; // OpenAIに送る履歴の最大件数

const users = new Map();

function ensure(userId) {
  if (!users.has(userId)) {
    users.set(userId, {
      messages: [],
      profile: {},       // 希望エリア、予算、家族構成 など
      viewings: [],      // 予約済み内見
      candidates: [],    // 直近に提示した候補日時
      application: null, // 申込み情報
      documents: {},     // 書類提出状況 { 本人確認書類: 'received', ... }
      escalated: false,
    });
  }
  return users.get(userId);
}

export const store = {
  getState(userId) {
    return ensure(userId);
  },
  appendMessage(userId, msg) {
    const s = ensure(userId);
    s.messages.push(msg);
    if (s.messages.length > MAX_MESSAGES) {
      // 安全な切り詰め: assistantのtool_callsとそれに対応するtool結果が
      // 分断されないように、user / 単純なassistantメッセージから始まる位置で切る
      const tail = s.messages.slice(-MAX_MESSAGES);
      let safeStart = 0;
      while (safeStart < tail.length) {
        const m = tail[safeStart];
        if (m.role === 'user') break;
        if (m.role === 'assistant' && !m.tool_calls) break;
        safeStart++;
      }
      s.messages = tail.slice(safeStart);
    }
  },
  getMessages(userId) {
    return ensure(userId).messages;
  },
  setCandidates(userId, candidates) {
    ensure(userId).candidates = candidates;
  },
  findCandidate(userId, slotId) {
    return ensure(userId).candidates.find((c) => c.slot_id === slotId);
  },
  addViewing(userId, viewing) {
    ensure(userId).viewings.push(viewing);
  },
  findViewing(userId, viewingId) {
    return ensure(userId).viewings.find((v) => v.viewing_id === viewingId);
  },
  updateProfile(userId, patch) {
    const s = ensure(userId);
    s.profile = { ...s.profile, ...patch };
  },
  setApplication(userId, application) {
    ensure(userId).application = application;
  },
  setDocument(userId, name, status) {
    ensure(userId).documents[name] = status;
  },
  escalate(userId) {
    ensure(userId).escalated = true;
  },
  isEscalated(userId) {
    return ensure(userId).escalated;
  },
};
