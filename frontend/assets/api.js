/* =============================================================================
 * AI 智参平台 · 统一 API 客户端
 * -----------------------------------------------------------------------------
 * 职责：把后端「统一响应 + 错误码 + 鉴权 + SSE」的约定收敛到一处，页面只调用
 *      语义化的方法，不再各写各的 fetch。
 *
 * 后端约定（务必对齐，勿凭记忆改）：
 *   1) 业务结果一律 HTTP 200，真正的成败在 body.code：code === 0 才是成功；
 *   2) 响应体：{ code, message, data, request_id, page }
 *   3) 鉴权：Authorization: Bearer <access_token>
 *   4) SSE：浏览器 EventSource 不能带 header → 先 POST /auth/stream-ticket 换
 *      60 秒短票，再 GET 流式地址 ?ticket=xxx
 *
 * 安全：访问令牌存 sessionStorage（关闭标签页即失效），不使用 localStorage。
 * ========================================================================= */
(function (global) {
  'use strict';

  var CFG = {
    // 留空 = 同源（前端由 FastAPI 的 StaticFiles 托管，无跨域）
    base: '',
    timeout: 30000,
    tokenKey: 'ai_health_token',
    refreshKey: 'ai_health_refresh',
    userKey: 'ai_health_user'
  };

  /* ------------------------------ 错误码 ------------------------------ */
  var ErrCode = {
    OK: 0,
    SYSTEM_ERROR: 90000,
    PARAM_INVALID: 90001,
    RESOURCE_NOT_FOUND: 90002,
    DB_ERROR: 90003,
    UNAUTHENTICATED: 10001,
    TOKEN_EXPIRED: 10002,
    TOKEN_INVALID: 10003,
    GUEST_NOT_ALLOWED: 10004,
    PERMISSION_DENIED: 10005,
    AUTH_FAILED: 10006,
    USER_NOT_FOUND: 20001,
    USER_EXISTS: 20002,
    VERIFY_CODE_INVALID: 20003,
    DIETITIAN_NOT_FOUND: 30001,
    CUSTOMER_BIND_CONFLICT: 30002,
    TEMPLATE_NOT_FOUND: 40001,
    TEMPLATE_VERSION_CONFLICT: 40002,
    ASSESSMENT_NOT_FOUND: 50001,
    REPORT_GEN_FAILED: 50002,
    SCORE_ENGINE_ERROR: 50003,
    ASSESSMENT_CONFLICT: 50004,
    REPORT_NOT_FOUND: 50005,
    AGENT_UNKNOWN: 60001,
    QUERY_RATE_LIMITED: 60002,
    KB_DOC_NOT_FOUND: 80001
  };

  var ERR_HINT = {
    10001: '请先登录',
    10002: '登录已过期，请重新登录',
    10003: '登录凭证无效，请重新登录',
    10004: '游客免费次数已用完，注册后可继续',
    10005: '当前账号无权访问该功能',
    60002: '操作过于频繁，请稍后再试'
  };

  function ApiError(code, message, requestId) {
    var e = new Error(message || ERR_HINT[code] || ('请求失败（' + code + '）'));
    e.name = 'ApiError';
    e.code = code;
    e.requestId = requestId;
    return e;
  }

  /* ------------------------------ 令牌 ------------------------------ */
  var Token = {
    get: function () { try { return sessionStorage.getItem(CFG.tokenKey) || ''; } catch (e) { return ''; } },
    refresh: function () { try { return sessionStorage.getItem(CFG.refreshKey) || ''; } catch (e) { return ''; } },
    set: function (tk, rk, user) {
      try {
        if (tk) { sessionStorage.setItem(CFG.tokenKey, tk); } else { sessionStorage.removeItem(CFG.tokenKey); }
        if (rk) { sessionStorage.setItem(CFG.refreshKey, rk); }
        if (user) { sessionStorage.setItem(CFG.userKey, JSON.stringify(user)); }
      } catch (e) { /* 隐私模式下 sessionStorage 可能不可用，忽略 */ }
    },
    user: function () {
      try { return JSON.parse(sessionStorage.getItem(CFG.userKey) || 'null'); } catch (e) { return null; }
    },
    clear: function () {
      try {
        sessionStorage.removeItem(CFG.tokenKey);
        sessionStorage.removeItem(CFG.refreshKey);
        sessionStorage.removeItem(CFG.userKey);
      } catch (e) { /* ignore */ }
    }
  };

  /* 未登录 / 令牌失效的统一回调，由页面注册 */
  var onUnauthorized = function () { Token.clear(); };

  /* ------------------------------ 核心请求 ------------------------------ */
  function request(path, opt) {
    opt = opt || {};
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, opt.timeout || CFG.timeout);
    var headers = {};
    for (var k in (opt.headers || {})) { if (Object.prototype.hasOwnProperty.call(opt.headers, k)) { headers[k] = opt.headers[k]; } }

    var isForm = (typeof FormData !== 'undefined') && (opt.body instanceof FormData);
    if (opt.body !== undefined && !isForm) { headers['Content-Type'] = 'application/json'; }
    if (opt.auth) {
      var tk = Token.get();
      if (tk) { headers['Authorization'] = 'Bearer ' + tk; }
    }

    var init = { method: opt.method || 'GET', headers: headers, signal: ctrl.signal };
    if (opt.body !== undefined) { init.body = isForm ? opt.body : JSON.stringify(opt.body); }

    return fetch(CFG.base + path, init).then(function (res) {
      return res.text().then(function (text) {
        var payload = null;
        try { payload = text ? JSON.parse(text) : null; } catch (e) { payload = null; }

        if (!res.ok) {
          throw ApiError(res.status,
            (payload && (payload.message || payload.detail)) || ('HTTP ' + res.status));
        }
        // 统一响应体：判断业务码
        if (payload && typeof payload === 'object' && 'code' in payload) {
          if (payload.code === ErrCode.OK) { return payload.data; }
          if (payload.code === ErrCode.UNAUTHENTICATED ||
              payload.code === ErrCode.TOKEN_EXPIRED ||
              payload.code === ErrCode.TOKEN_INVALID) {
            onUnauthorized();
          }
          throw ApiError(payload.code, payload.message, payload.request_id);
        }
        return payload; // 非标准响应（如 /docs）原样返回
      });
    }).catch(function (err) {
      if (err && err.name === 'AbortError') { throw ApiError(-1, '请求超时，请检查后端服务是否运行'); }
      if (err && err.name === 'TypeError') {
        const target = CFG.base || location.origin;
        throw ApiError(-2, '无法连接后端服务（目标 ' + target + '，请确认后端已启动）');
      }
      throw err;
    }).then(function (v) { clearTimeout(timer); return v; },
      function (e) { clearTimeout(timer); throw e; });
  }

  var get = function (p, auth) { return request(p, { method: 'GET', auth: !!auth }); };
  var post = function (p, body, auth) { return request(p, { method: 'POST', body: body || {}, auth: !!auth }); };
  var put = function (p, body, auth) { return request(p, { method: 'PUT', body: body || {}, auth: !!auth }); };
  var patch = function (p, body, auth) { return request(p, { method: 'PATCH', body: body || {}, auth: !!auth }); };
  var del = function (p, auth) { return request(p, { method: 'DELETE', auth: !!auth }); };

  function qs(params) {
    var parts = [];
    for (var k in (params || {})) {
      var v = params[k];
      if (v === undefined || v === null || v === '') { continue; }
      parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(v));
    }
    return parts.length ? ('?' + parts.join('&')) : '';
  }

  /* ------------------------------ SSE ------------------------------ */
  function safe(raw) {
    try { return raw ? JSON.parse(raw) : {}; } catch (e) { return {}; }
  }

  /* 换取短票 → 用 EventSource 打开流。返回 {ready: Promise, close()}。 */
  function openStream(path, params, handlers) {
    handlers = handlers || {};
    var ticketP;
    var tk = Token.get();
    if (tk) {
      ticketP = post('/api/v1/auth/stream-ticket', {}, true).then(function (d) { return d.ticket; });
    } else {
      // 游客：后端允许匿名查询（有配额），短票接口需登录，故无票直连
      ticketP = Promise.resolve('');
    }
    return {
      ready: ticketP.then(function (ticket) {
        var url = CFG.base + path + qs(Object.assign({}, params, ticket ? { ticket: ticket } : {}));
        var es = new EventSource(url);
        var closed = false;
        function shut() { if (!closed) { closed = true; try { es.close(); } catch (err) { /* ignore */ } } }
        es.addEventListener('start', function (e) { handlers.start && handlers.start(safe(e.data)); });
        es.addEventListener('delta', function (e) { handlers.delta && handlers.delta(safe(e.data)); });
        es.addEventListener('reference', function (e) { handlers.reference && handlers.reference(safe(e.data)); });
        es.addEventListener('done', function (e) {
          var d = safe(e.data);
          shut();                       // 先关，避免服务端结束后触发重连 error
          handlers.done && handlers.done(d);
        });
        es.addEventListener('error', function () {
          if (closed) { return; }       // 已正常结束，忽略
          shut();
          handlers.error && handlers.error(ApiError(-3, '连接中断，请重试'));
        });
        return { close: shut };
      })
    };
  }

  /* ------------------------------ 接口分组 ------------------------------ */
  var API = {
    cfg: CFG,
    ErrCode: ErrCode,
    ApiError: ApiError,
    Token: Token,
    request: request,
    get: get,
    post: post,
    put: put,
    patch: patch,
    del: del,
    qs: qs,
    openStream: openStream,
    onUnauthorized: function (fn) { onUnauthorized = fn; },

    /* A. 认证 */
    auth: {
      register: function (b) { return post('/api/v1/auth/register', b); },
      login: function (username, password, phone) {
        var body = { password: password };
        if (phone) { body.phone = phone; } else { body.username = username; }
        return post('/api/v1/auth/login', body);
      },
      guest: function () { return post('/api/v1/auth/guest', {}); },
      sendSms: function (phone) { return post('/api/v1/auth/sms/send', { phone: phone }); },
      refresh: function (refreshToken) { return post('/api/v1/auth/token/refresh', { refresh_token: refreshToken }); },
      logout: function () { return post('/api/v1/auth/logout', {}, true); },
      streamTicket: function () { return post('/api/v1/auth/stream-ticket', {}, true); }
    },

    /* B. 用户 */
    users: {
      me: function () { return get('/api/v1/users/me', true); },
      updateProfile: function (b) { return put('/api/v1/users/me/profile', b, true); },
      changePassword: function (b) { return put('/api/v1/users/me/password', b, true); },
      verifyPhone: function (b) { return post('/api/v1/users/me/phone/verify', b, true); },
      healthContext: function () { return get('/api/v1/users/me/health-context', true); },
      dataExport: function () { return get('/api/v1/users/me/data-export', true); },
      removeAccount: function (b) { return request('/api/v1/users/me', { method: 'DELETE', body: b || {}, auth: true }); }
    },

    /* D. 测评模板 */
    templates: {
      list: function () { return get('/api/v1/templates', true); },
      mine: function () { return get('/api/v1/templates/mine', true); },
      detail: function (id) { return get('/api/v1/templates/' + id, true); },
      versions: function (id) { return get('/api/v1/templates/' + id + '/versions', true); },
      create: function (b) { return post('/api/v1/templates', b, true); },
      update: function (id, b) { return put('/api/v1/templates/' + id, b, true); },
      submitAudit: function (id) { return post('/api/v1/templates/' + id + '/submit-audit', {}, true); },
      audit: function (id, approve, remark) { return post('/api/v1/templates/' + id + '/audit', { approve: approve, remark: remark || '' }, true); },
      offline: function (id) { return post('/api/v1/templates/' + id + '/offline', {}, true); }
    },

    /* E. 测评 */
    assessments: {
      list: function (params) { return get('/api/v1/assessments' + qs(params), true); },
      start: function (templateId, templateVersion) {
        return post('/api/v1/assessments', { template_id: templateId, template_version: templateVersion }, true);
      },
      detail: function (id) { return get('/api/v1/assessments/' + id, true); },
      saveDraft: function (id, answers, version) {
        var body = { answers: answers };
        if (version) { body.version = version; }   // 带上次 saved_at → 多端并发保护
        return patch('/api/v1/assessments/' + id + '/answers', body, true);
      },
      submit: function (id, idempotencyKey) {
        return request('/api/v1/assessments/' + id + '/submit',
          { method: 'POST', body: {}, auth: true, headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {} });
      },
      cancel: function (id) { return post('/api/v1/assessments/' + id + '/cancel', {}, true); },
      regenerateReport: function (id) { return post('/api/v1/assessments/' + id + '/report/regenerate', {}, true); },
      claimGuest: function (guestToken, phone, verifyCode) {
        return post('/api/v1/assessments/guest/claim',
          { guest_token: guestToken, phone: phone, verify_code: verifyCode }, true);
      },
      reportStream: function (recordId) { return '/api/v1/assessments/' + recordId + '/report/stream'; }
    },

    /* F. 报告 */
    reports: {
      detail: function (id) { return get('/api/v1/reports/' + id, true); },
      annotate: function (id, b) { return put('/api/v1/reports/' + id + '/annotation', b, true); },
      pdfUrl: function (id) { return '/api/v1/reports/' + id + '/pdf'; },
      pdf: function (id) { return get('/api/v1/reports/' + id + '/pdf', true); }
    },

    /* G. 智能查询（SSE） */
    chat: {
      queryStream: function (question, agent) { return '/api/v1/chat/query/stream'; },
      queryParams: function (question, agent, sessionId) {
        var p = { question: question, agent: agent || 'dietitian' };
        if (sessionId) { p.session_id = sessionId; }   // 续接会话 → 多轮上下文
        return p;
      },
      kbInfo: function () { return get('/api/v1/chat/kb-info', true); },
      latestSession: function () { return get('/api/v1/chat/sessions/latest', true); }
    },

    /* H. 咨询 */
    consultations: {
      list: function (params) { return get('/api/v1/consultations' + qs(params), true); },
      create: function (b) { return post('/api/v1/consultations', b, true); },
      pending: function () { return get('/api/v1/consultations/pending', true); },
      accept: function (id) { return post('/api/v1/consultations/' + id + '/accept', {}, true); },
      close: function (id) { return post('/api/v1/consultations/' + id + '/close', {}, true); },
      messages: function (id, params) { return get('/api/v1/consultations/' + id + '/messages' + qs(params), true); },
      send: function (id, content) { return post('/api/v1/consultations/' + id + '/messages', { content: content }, true); },
      rate: function (id, score, comment) { return post('/api/v1/consultations/' + id + '/rating', { score: score, comment: comment || '' }, true); }
    },

    /* I. 看板 */
    dashboard: {
      overview: function () { return get('/api/v1/dashboard/personal/overview', true); },
      risks: function () { return get('/api/v1/dashboard/personal/risks', true); },
      trend: function (params) { return get('/api/v1/dashboard/personal/trend' + qs(params), true); },
      dimensions: function () { return get('/api/v1/dashboard/personal/dimensions', true); },
      attribution: function (params) { return get('/api/v1/dashboard/personal/attribution' + qs(params), true); },
      insight: function () { return get('/api/v1/dashboard/personal/insight', true); }
    },

    /* J. 管理 */
    admin: {
      users: function (params) { return get('/api/v1/admin/users' + qs(params), true); },
      setUserStatus: function (uid, status) { return put('/api/v1/admin/users/' + uid + '/status', { status: status }, true); },
      nutritionistAudits: function () { return get('/api/v1/admin/nutritionists/audits', true); },
      auditNutritionist: function (uid, b) { return post('/api/v1/admin/nutritionists/' + uid + '/audit', b, true); },
      templateAudits: function () { return get('/api/v1/admin/templates/audits', true); },
      templates: function (params) { return get('/api/v1/admin/templates' + qs(params), true); },
      auditTemplate: function (id, approve, remark) { return post('/api/v1/templates/' + id + '/audit', { approve: approve, remark: remark || '' }, true); },
      offlineTemplate: function (id) { return post('/api/v1/templates/' + id + '/offline', {}, true); },
      alerts: function () { return get('/api/v1/admin/alerts/thresholds', true); },
      saveAlerts: function (b) { return put('/api/v1/admin/alerts/thresholds', b, true); },
      roles: function () { return get('/api/v1/admin/roles', true); },
      saveRole: function (b) { return put('/api/v1/admin/roles', b, true); },
      config: function () { return get('/api/v1/admin/config', true); },
      saveConfig: function (b) { return put('/api/v1/admin/config', b, true); },
      overview: function () { return get('/api/v1/admin/overview', true); },
      logs: function (params) { return get('/api/v1/admin/operation-logs' + qs(params), true); },
      opsDashboard: function () { return get('/api/v1/admin/dashboard/ops', true); },
      opsConfig: function () { return get('/api/v1/admin/dashboard/ops/config', true); },
      saveOpsConfig: function (b) { return put('/api/v1/admin/dashboard/ops/config', b, true); },
      opsSnapshot: function () { return post('/api/v1/admin/dashboard/ops/snapshot', {}, true); }
    },

    /* K. 知识库 */
    knowledge: {
      list: function (params) { return get('/api/v1/knowledge/docs' + qs(params), true); },
      upload: function (file, collectionName, category, source) {
        var fd = new FormData();
        fd.append('file', file);
        fd.append('collection_name', collectionName);
        if (category) { fd.append('category', category); }
        if (source) { fd.append('source', source); }
        return request('/api/v1/knowledge/docs', { method: 'POST', body: fd, auth: true, timeout: 120000 });
      },
      embed: function (docId) { return post('/api/v1/knowledge/docs/' + docId + '/embed', {}, true); },
      remove: function (docId) { return del('/api/v1/knowledge/docs/' + docId, true); }
    },

    /* L. 食养计划 */
    plans: {
      list: function (params) { return get('/api/v1/diet-plans' + qs(params), true); },
      create: function (b) { return post('/api/v1/diet-plans', b, true); },
      detail: function (id) { return get('/api/v1/diet-plans/' + id, true); },
      update: function (id, b) { return put('/api/v1/diet-plans/' + id, b, true); },
      regenerate: function (id) { return post('/api/v1/diet-plans/' + id + '/regenerate', {}, true); },
      shoppingList: function (id) { return get('/api/v1/diet-plans/' + id + '/shopping-list', true); }
    },

    /* M. 智能荐菜 */
    recommend: {
      dishes: function (b) { return post('/api/v1/recommend/dishes', b, true); },
      ingredients: function (params) { return get('/api/v1/recommend/ingredients' + qs(params), true); }
    },

    /* N. 智能体中心 */
    agents: {
      list: function () { return get('/api/v1/agents', true); },
      detail: function (key) { return get('/api/v1/agents/' + key, true); },
      dispatch: function (text) { return post('/api/v1/agents/dispatch', { text: text }, true); },
      scenes: function () { return get('/api/v1/scenes', true); }
    },

    /* O. 舌象 */
    tongue: {
      upload: function (file) {
        var fd = new FormData();
        fd.append('file', file);
        return request('/api/v1/tongue/upload', { method: 'POST', body: fd, auth: true, timeout: 120000 });
      },
      detail: function (id) { return get('/api/v1/tongue/' + id, true); }
    },

    /* Q. 打卡 */
    checkins: {
      list: function (params) { return get('/api/v1/checkins' + qs(params), true); },
      create: function (b) { return post('/api/v1/checkins', b, true); },
      stats: function () { return get('/api/v1/checkins/stats', true); }
    },

    /* R. 通知 */
    notifications: {
      list: function (params) { return get('/api/v1/notifications' + qs(params), true); },
      read: function (id) { return post('/api/v1/notifications/' + id + '/read', {}, true); },
      readAll: function () { return post('/api/v1/notifications/read-all', {}, true); }
    },

    /* S. 数据分析智能体（受控 Text2SQL）
       注意：question 走 query 参数，不是 JSON body（后端签名为 Query(...)） */
    analytics: {
      query: function (question) { return post('/api/v1/analytics/text2sql/query' + qs({ question: question }), {}, true); },
      catalog: function () { return get('/api/v1/analytics/text2sql/catalog', true); },
      history: function (limit) { return get('/api/v1/analytics/text2sql/history' + qs({ limit: limit }), true); },
      exportReport: function (question, fmt) {
        return get('/api/v1/analytics/text2sql/export' + qs({ question: question, fmt: fmt || 'csv' }), true);
      },
      monitor: function () { return get('/api/v1/analytics/agents/monitor', true); }
    },

    /* T. 社交宴请 */
    banquets: {
      list: function (params) { return get('/api/v1/banquets' + qs(params), true); },
      create: function (b) { return post('/api/v1/banquets', b, true); },
      detail: function (id) { return get('/api/v1/banquets/' + id, true); },
      update: function (id, b) { return put('/api/v1/banquets/' + id, b, true); }
    },

    /* U. 旅行美食智荐 */
    travel: {
      list: function () { return get('/api/v1/travel-plans', true); },
      create: function (b) { return post('/api/v1/travel-plans', b, true); },
      detail: function (id) { return get('/api/v1/travel-plans/' + id, true); }
    },

    /* V. OCR 体检报告 */
    ocr: {
      list: function () { return get('/api/v1/ocr-reports', true); },
      upload: function (file) {
        var fd = new FormData();
        fd.append('file', file);
        return request('/api/v1/ocr-reports', { method: 'POST', body: fd, auth: true, timeout: 120000 });
      },
      detail: function (id) { return get('/api/v1/ocr-reports/' + id, true); }
    },

    /* X. 数字人助手 */
    digitalHuman: {
      config: function () { return get('/api/v1/digital-human/config', true); },
      session: function () { return post('/api/v1/digital-human/session', {}, true); },
      voiceCommand: function (text) { return post('/api/v1/digital-human/voice-command', { text: text }, true); }
    },

    /* 营养师 */
    nutritionists: {
      me: function () { return get('/api/v1/nutritionists/me', true); },
      updateMe: function (b) { return put('/api/v1/nutritionists/me', b, true); },
      apply: function (b) { return post('/api/v1/nutritionists/apply', b, true); },
      overview: function () { return get('/api/v1/nutritionists/me/overview', true); },
      customers: function (params) { return get('/api/v1/nutritionists/me/customers' + qs(params), true); },
      customerReports: function (uid, params) { return get('/api/v1/nutritionists/me/customers/' + uid + '/reports' + qs(params), true); },
      bind: function (code) { return post('/api/v1/nutritionists/me/customers/bind', { invite_code: code }, true); },
      unbindCustomer: function (uid) { return del('/api/v1/nutritionists/me/customers/' + uid, true); },
      inviteCode: function () { return post('/api/v1/nutritionists/me/invite-code', {}, true); },
      unbindMine: function () { return post('/api/v1/nutritionists/me/unbind', {}, true); },
      search: function (params) { return get('/api/v1/nutritionists/search' + qs(params), true); },
      detail: function (id) { return get('/api/v1/nutritionists/' + id, true); },
      exportCustomers: function (fmt) { return get('/api/v1/nutritionists/me/customers/export?fmt=' + (fmt || 'csv'), true); }
    },

    /* 系统探针（公开） */
    system: {
      health: function () { return get('/api/health'); },
      ready: function () { return get('/api/v1/ready'); }
    },

    /* 运维监控（管理员 / 运营） */
    ops: {
      metrics: function () { return get('/api/v1/ops/metrics', true); },
      resetMetrics: function () { return post('/api/v1/ops/metrics/reset', {}, true); }
    }
  };

  global.API = API;
})(window);
