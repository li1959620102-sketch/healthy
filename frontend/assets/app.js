/* ============================================================
   AI智参（药食同源）平台 · 前端逻辑
   ------------------------------------------------------------
   接口调用一律走 assets/api.js 的 API 客户端（统一响应解包 / 错误码 / 鉴权 / SSE）。
   本文件只负责：页面路由、交互、把后端数据渲染到 DOM。

   已对齐的真实接口（勿再凭记忆写路径）：
     登录 POST /api/v1/auth/login        → data:{access_token, refresh_token, user}
     注册 POST /api/v1/auth/register     → 只返回用户信息，令牌需再登录换取
     用户 GET  /api/v1/users/me
     探针 GET  /api/v1/ready             → data:{ready, checks}
     问答 GET  /api/v1/chat/query/stream?ticket=&question=&agent=   (SSE)
     概览 GET  /api/v1/dashboard/personal/overview
   ============================================================ */

/* ---------------- 常量与配置 ---------------- */
const CFG_KEY = 'aizhishen_api_cfg';
const DEFAULT_CFG = {
  base: '',      // 留空 = 同源（前端由 FastAPI 托管）；独立部署时填后端地址
  collection: '',// Milvus 集合名（知识库检索用，由后端 .env 决定，前端仅展示）
  fileId: '',
  topK: 2,
  live: 1        // 1 = 走真实后端
};
const TITLES = {
  home: '健康首页', assessment: '健康风险测评', nutrition: '营养师智能体',
  diet: '食养食疗计划', scenes: '五大健康场景', data: '个人数据分析',
  dietitian: '营养师工作台', analytics: 'Text2SQL智能分析',
  admin: '运营可视化大屏', agents: '智能体中心'
};

let CFG = loadCfg();
let lastResultRows = [];   // Text2SQL 最近一次结果，用于导出 CSV
let lastAssessment = null; // 最近一次测评结果，用于保存到健康档案

/* ---------------- 通用工具 ---------------- */
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function toast(msg, ms) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  clearTimeout(window._tt);
  window._tt = setTimeout(() => { t.style.display = 'none'; }, ms || 2600);
}
function go(id) {
  document.querySelectorAll('.screen').forEach(x => x.classList.remove('active'));
  const target = document.getElementById(id);
  if (target) target.classList.add('active');
  document.querySelectorAll('.nav a').forEach(x => x.classList.toggle('active', x.dataset.screen === id));
  document.getElementById('pageTitle').textContent = TITLES[id] || 'AI智参';
  /* 越权兜底：当前角色无权访问的屏，直接落回健康首页（并如实提示） */
  if (!canSee(id)) {
    const fallback = 'home';
    document.querySelectorAll('.screen').forEach(x => x.classList.remove('active'));
    const t = document.getElementById(fallback);
    if (t) t.classList.add('active');
    document.querySelectorAll('.nav a').forEach(x => x.classList.toggle('active', x.dataset.screen === fallback));
    document.getElementById('pageTitle').textContent = TITLES[fallback];
    toast('当前身份为' + (ROLE_LABEL[currentRole()] || '访客') + '，无权访问该页面');
    if (typeof refreshHome === 'function') refreshHome();
    return;
  }
  if (id === 'home') { refreshHome(); }
  if (id === 'data') { loadDataAnalysis(); }
  if (id === 'assessment') { loadTemplates(); loadHistory(); }
  if (id === 'diet') { loadDiet(); }
  if (id === 'nutrition') { loadHealthContext(); loadContacts(); loadKbInfo(); loadChatHistory(); }
  if (id === 'dietitian') { loadDietitian(); }
  if (id === 'scenes') { loadScenes(); }
  if (id === 'agents') { loadAgentsPanel(); }
  if (id === 'admin') { loadOps(); }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ---------------- 路由与顶栏 ---------------- */
function bindNav() {
  document.querySelectorAll('.nav a').forEach(a => {
    a.onclick = () => go(a.dataset.screen);
  });
  document.querySelectorAll('.contact').forEach(c => {
    c.onclick = () => {
      document.querySelectorAll('.contact').forEach(x => x.classList.remove('active'));
      c.classList.add('active');
    };
  });
}

/* ---------------- 数字人弹窗 ---------------- */
function openDigitalHuman() { document.getElementById('dhModal').classList.add('open'); }
function closeDigitalHuman() { document.getElementById('dhModal').classList.remove('open'); }
function goFromModal(id) { closeDigitalHuman(); go(id); }
function voiceCommand() {
  toast('识别到语音：“帮我做一个健康风险测评”');
  setTimeout(() => goFromModal('assessment'), 900);
}

/* ---------------- 接口设置 ---------------- */
/* 用「文件方式」（file://）双击打开 HTML 时，相对路径请求会变成 file:// 必然失败；
   这里统一给出"本机后端"的默认地址，让这种打开方式也能正常使用。 */
function defaultBase() {
  return (location.protocol === 'file:') ? 'http://127.0.0.1:8019' : '';
}
function loadCfg() {
  let c;
  try {
    c = Object.assign({}, DEFAULT_CFG, JSON.parse(localStorage.getItem(CFG_KEY) || '{}'));
  } catch (e) { c = Object.assign({}, DEFAULT_CFG); }
  /* file:// 打开且没填过可用地址时，自动指向本机后端 */
  if (location.protocol === 'file:' && (!c.base || c.base.indexOf('http') !== 0)) {
    c.base = defaultBase();
  }
  if (window.API) { API.cfg.base = c.base || ''; }   // 同步给 API 客户端
  return c;
}
function saveCfg() {
  localStorage.setItem(CFG_KEY, JSON.stringify(CFG));
  if (window.API) { API.cfg.base = CFG.base || ''; }
}
function openSettings() {
  /* 双重保险：即便通过控制台调用，非管理员也拒绝打开（除非调试模式） */
  if (!(canSee('admin') || isDebugMode())) {
    toast('接口设置仅管理员可见');
    return;
  }
  document.getElementById('cfgBase').value = CFG.base;
  document.getElementById('cfgCollection').value = CFG.collection;
  document.getElementById('cfgFileId').value = CFG.fileId;
  document.getElementById('cfgTopK').value = CFG.topK;
  document.getElementById('cfgLive').value = String(CFG.live);
  document.getElementById('setModal').classList.add('open');
}
function closeSettings() { document.getElementById('setModal').classList.remove('open'); }
function saveSettings() {
  CFG.base = (document.getElementById('cfgBase').value || '').trim().replace(/\/+$/, '') || DEFAULT_CFG.base;
  CFG.collection = (document.getElementById('cfgCollection').value || '').trim() || DEFAULT_CFG.collection;
  CFG.fileId = (document.getElementById('cfgFileId').value || '').trim();
  CFG.topK = parseInt(document.getElementById('cfgTopK').value, 10) || 2;
  CFG.live = parseInt(document.getElementById('cfgLive').value, 10) || 0;
  saveCfg();
  document.getElementById('ctxKb').textContent =
    'collection: ' + CFG.collection + (CFG.fileId ? ' | file_id: ' + CFG.fileId.slice(0, 8) + '…' : '');
  toast('配置已保存');
  checkHealth();
}

function setConn(state, text) {
  const dot = document.getElementById('connDot');
  if (dot) dot.className = 'conn-dot ' + (state === 'on' ? 'on' : (state === 'off' ? 'off' : ''));
  const t = document.getElementById('connText');
  if (t) t.textContent = text;
  /* 登录页同步显示后端状态（点一下即可重新检测，避免"无法连接"一头雾水） */
  const lp = document.getElementById('lpLink');
  if (lp) {
    lp.className = 'lp-link ' + (state === 'on' ? 'on' : (state === 'off' ? 'off' : ''));
    const txt = document.getElementById('lpLinkText');
    if (txt) txt.textContent = state === 'on' ? '后端已连接' :
      (state === 'off' ? (text + '，点此重试') : '正在检测后端…');
  }
}

/* ---------------- 后端接入层（委托给 assets/api.js） ----------------
   令牌统一由 API.Token 管理（sessionStorage，关闭标签页即失效）。
   后端约定：业务结果一律 HTTP 200，成败看 body.code；解包已在 API 内完成，
   因此这里返回的 data 就是 body.data，失败会抛 ApiError（带 code/message）。 */
function getToken() { return API.Token.get(); }
function setToken(t) { if (t) { API.Token.set(t); } else { API.Token.clear(); } }

/* 兼容旧调用点的薄封装 */
async function api(path, body, needAuth) { return API.post(path, body || {}, !!needAuth); }
async function apiGet(path, needAuth) { return API.get(path, !!needAuth); }

/* 就绪探针：GET /api/v1/ready → data:{ ready, checks:{mysql,milvus,redis,minio} } */
async function checkHealth() {
  setConn('', '检测中…');
  try {
    const d = await API.system.ready();
    const checks = (d && d.checks) || {};
    const bad = Object.keys(checks).filter(function (k) {
      return String(checks[k]).toLowerCase() !== 'ok';
    });
    const ok = !!(d && d.ready);
    setConn(ok ? 'on' : 'off',
      ok ? '后端已连接（依赖全部就绪）'
        : (bad.length ? ('依赖异常：' + bad.join('、')) : '后端响应异常'));
    return ok;
  } catch (e) {
    /* 自愈：若曾把接口地址改成不可达的绝对地址（设置里填过），自动回退同源并提示 */
    if (CFG.base && CFG.base !== defaultBase()) {
      const saved = CFG.base;
      CFG.base = defaultBase();
      saveCfg();
      toast('接口地址 ' + saved + ' 不可达，已自动切回 ' + (CFG.base || '当前站点'));
      return await checkHealth();
    }
    setConn('off', '后端未连接');
    return false;
  }
}

/* ---------------- 登录与用户态 ---------------- */
let CURRENT_USER = null;

/* 角色 → 显示名 / 可见屏（顶栏只显示本账号角色；侧栏专业端按权限显隐） */
const ROLE_LABEL = { user: '普通用户', dietitian: '营养师', admin: '管理员', operator: '运营' };
const ROLE_SCREENS = {
  user: ['home', 'assessment', 'nutrition', 'diet', 'scenes', 'data', 'agents'],
  dietitian: ['home', 'assessment', 'nutrition', 'diet', 'scenes', 'data', 'agents', 'dietitian', 'analytics'],
  admin: ['home', 'assessment', 'nutrition', 'diet', 'scenes', 'data', 'agents', 'dietitian', 'analytics', 'admin'],
  operator: ['home', 'assessment', 'nutrition', 'diet', 'scenes', 'data', 'agents', 'dietitian', 'analytics', 'admin']
};
function currentRole() { return (CURRENT_USER && CURRENT_USER.role) || ''; }
function canSee(screen) {
  const list = ROLE_SCREENS[currentRole()] || ROLE_SCREENS.user;
  return list.indexOf(screen) >= 0;
}
function applyRoleUI() {
  const role = currentRole();
  const badge = document.getElementById('roleBadge');
  if (badge) {
    badge.textContent = ROLE_LABEL[role] || '访客';
    badge.className = 'role-badge' + (role ? ' role-' + role : '');
  }
  const show = function (id, on) {
    const el = document.getElementById(id);
    if (el) el.style.display = on ? '' : 'none';
  };
  show('navDietitian', canSee('dietitian'));
  show('navAnalytics', canSee('analytics'));
  show('navAdmin', canSee('admin'));
  show('navAdminConsole', canSee('admin'));   // 管理后台（独立页，用户/模板/审计/预警/权限）
  const proVisible = canSee('dietitian') || canSee('analytics') || canSee('admin');
  show('navProTitle', proVisible);
  show('navPro', proVisible);
  /* 接口设置属开发/运维配置（改后端地址、集合名、topK）：仅管理员可见。
     调试期可在网址后加 ?debug=1 临时显示（便于以用户身份排查），不带参数即隐藏。 */
  show('btnApiSettings', canSee('admin') || isDebugMode());
}

/* 调试模式：?debug=1 或 localStorage.ai_health_debug=1 —— 仅用于开发排查 */
function isDebugMode() {
  try {
    if (/[?&]debug=1/.test(location.search)) { localStorage.setItem('ai_health_debug', '1'); return true; }
    return localStorage.getItem('ai_health_debug') === '1';
  } catch (e) { return false; }
}

function updateAuthUI() {
  const box = document.getElementById('authArea');
  if (!box) return;
  /* 独立登录页：未登录 → 全屏登录页；已登录 → 隐藏并显示主应用 */
  const lp = document.getElementById('loginPage');
  if (lp) lp.classList.toggle('hidden', !!CURRENT_USER);
  applyRoleUI();
  if (CURRENT_USER) {
    const name = CURRENT_USER.real_name || CURRENT_USER.username;
    box.innerHTML =
      '<span class="badge green" style="margin-right:8px">' + esc(name) + '</span>' +
      '<div class="avatar-mini" style="display:inline-grid;margin-right:8px">' + esc(name.slice(0, 1)) + '</div>' +
      '<button class="btn" onclick="logout()">退出</button>';
  } else {
    box.innerHTML = '<button class="btn primary" onclick="openLogin()">登录 / 注册</button>';
  }
  loadNotices();
}

/* 登录态过期（10001/10002/10003）统一处理：清用户态 → 弹登录框 */
API.onUnauthorized(function () {
  const hadUser = !!CURRENT_USER;
  CURRENT_USER = null;
  updateAuthUI();
  if (hadUser) toast('登录已过期，请重新登录');
  openLogin();
});

function openLogin() {
  const lp = document.getElementById('loginPage');
  if (lp) lp.classList.remove('hidden');
  document.getElementById('loginErr').textContent = '';
}
function closeLogin() {
  const lp = document.getElementById('loginPage');
  if (lp && CURRENT_USER) lp.classList.add('hidden');
}

/* ---------------- 登录页：身份选择（普通用户 / 营养师 / 管理员） ----------------
 * 选择的角色用于：①注册时作为账号角色；②登录后按服务端返回的真实角色路由落地屏。 */
const LOGIN_UI = { tab: 'phone', role: 'user', timer: null, emailTimer: null };

function switchLoginRole(role) {
  LOGIN_UI.role = role;
  document.querySelectorAll('#lpRoles .lp-role').forEach(function (el) {
    el.classList.toggle('active', el.dataset.role === role);
  });
}

function afterLogin(data) {
  /* 后端登录返回：{ access_token, refresh_token, token_type, expires_in, user } */
  API.Token.set(data.access_token, data.refresh_token, data.user);
  CURRENT_USER = data.user;
  updateAuthUI();
  toast('登录成功：' + (data.user.real_name || data.user.username));
  /* 按服务端返回的真实角色路由落地屏（与登录页所选身份可不同） */
  const role = data.user.role || 'user';
  go(role === 'admin' ? 'admin' : (role === 'dietitian' ? 'dietitian' : 'home'));
  onLoginChanged();
}

/* ---------------- 登录双通道（手机号 / 账号） ---------------- */

function switchLoginTab(t) {
  LOGIN_UI.tab = t;
  document.getElementById('panePhone').style.display = (t === 'phone') ? '' : 'none';
  document.getElementById('paneEmail').style.display = (t === 'email') ? '' : 'none';
  document.getElementById('paneAccount').style.display = (t === 'account') ? '' : 'none';
  /* 通道分段控件（参考页风格）：仅切换 active 类，不改 btn 外观 */
  document.getElementById('tabPhone').classList.toggle('active', t === 'phone');
  document.getElementById('tabEmail').classList.toggle('active', t === 'email');
  document.getElementById('tabAccount').classList.toggle('active', t === 'account');
  document.getElementById('loginErr').textContent = '';
}

/* 密码明文/密文切换 */
function togglePwd(btn) {
  const input = btn.parentNode.querySelector('input');
  if (!input) return;
  const show = input.type === 'password';
  input.type = show ? 'text' : 'password';
  btn.style.color = show ? '#2f3038' : '';
}

/* 忘记密码：平台暂未接入自助重置，如实提示（不伪造流程） */
function forgotPassword() {
  toast('请联系管理员重置密码（自助重置待接入短信通道）');
}

/* 记住我：仅记住账号名（令牌仍只存 sessionStorage，不落本地） */
function rememberMe(el) {
  try {
    if (el.checked) { localStorage.setItem('ai_health_remember', '1'); saveRememberedAccount(); }
    else { localStorage.removeItem('ai_health_remember'); localStorage.removeItem('ai_health_account'); }
  } catch (e) { /* 隐私模式下忽略 */ }
}
function saveRememberedAccount() {
  try {
    const idByTab = { phone: 'lgPhone', email: 'egEmail', account: 'lgUser' };
    const el = document.getElementById(idByTab[LOGIN_UI.tab] || 'lgUser');
    const acc = (el && el.value) || '';
    localStorage.setItem('ai_health_account', acc);
  } catch (e) { }
}
function restoreRemembered() {
  try {
    if (localStorage.getItem('ai_health_remember') !== '1') return;
    const cb = document.getElementById('lpRemember');
    if (cb) cb.checked = true;
    const acc = localStorage.getItem('ai_health_account') || '';
    if (!acc) return;
    /* 按内容判断该填回哪个通道：11 位数字→手机号，含 @→邮箱，其余→账号 */
    let target = 'lgUser', tab = 'account';
    if (/^1\d{10}$/.test(acc)) { target = 'lgPhone'; tab = 'phone'; }
    else if (acc.indexOf('@') > 0) { target = 'egEmail'; tab = 'email'; }
    const el = document.getElementById(target);
    if (el) el.value = acc;
    if (tab !== 'phone') switchLoginTab(tab);
  } catch (e) { }
}

/* 登录页飘落草叶（参考页的动态点缀，纯装饰） */
function initLoginLeaves() {
  const box = document.getElementById('lpLeaves');
  if (!box || box.childElementCount) return;
  const pool = ['🌿', '🍃', '🍂', '🌸', '🌱', '🌾', '🍃', '🌿'];
  pool.forEach(function (emoji, i) {
    const el = document.createElement('span');
    el.className = 'lp-leaf';
    el.textContent = emoji;
    el.style.left = (4 + i * 12 + Math.random() * 6) + '%';
    el.style.fontSize = (13 + Math.random() * 10).toFixed(1) + 'px';
    el.style.animationDuration = (16 + Math.random() * 14).toFixed(1) + 's';
    el.style.animationDelay = (-Math.random() * 18).toFixed(1) + 's';
    el.style.setProperty('--dx', (Math.random() * 120 - 60).toFixed(0) + 'px');
    el.style.setProperty('--rot', (120 + Math.random() * 260).toFixed(0) + 'deg');
    box.appendChild(el);
  });
}

async function sendEmailCode() {
  const email = (document.getElementById('egEmail').value || '').trim();
  const err = document.getElementById('loginErr');
  err.textContent = '';
  if (!/^[^\s@]+@[^\s@]+\.[A-Za-z]{2,}$/.test(email)) { err.textContent = '请输入正确的邮箱，如 name@example.com'; return; }
  const btn = document.getElementById('btnSendEmailCode');
  btn.disabled = true; btn.textContent = '发送中…';
  try {
    const d = await API.auth.sendEmail(email);
    if (d && d.demo_mode) {
      /* 演示模式（SMTP 未配置）：验证码直接展示并自动填入，明示非真实邮件 */
      toast('【演示模式·未接邮件网关】验证码 ' + d.demo_code + '，已自动填入');
      const codeInput = document.getElementById('egCode');
      if (codeInput) codeInput.value = d.demo_code;
    } else {
      toast('验证码已发送至 ' + ((d && d.to) || email) + '（' + ((d && d.expires_in) || 300) + ' 秒内有效）');
    }
    let left = (d && d.interval) || 60;
    btn.textContent = left + 's 后重发';
    if (LOGIN_UI.emailTimer) clearInterval(LOGIN_UI.emailTimer);
    LOGIN_UI.emailTimer = setInterval(function () {
      left -= 1;
      if (left <= 0) {
        clearInterval(LOGIN_UI.emailTimer); LOGIN_UI.emailTimer = null;
        btn.disabled = false; btn.textContent = '获取验证码';
      } else { btn.textContent = left + 's 后重发'; }
    }, 1000);
  } catch (e) {
    err.textContent = e.message || '验证码发送失败';
    btn.disabled = false; btn.textContent = '获取验证码';
  }
}

async function sendSmsCode() {
  const phone = (document.getElementById('lgPhone').value || '').trim();
  const err = document.getElementById('loginErr');
  err.textContent = '';
  if (!/^1[3-9]\d{9}$/.test(phone)) { err.textContent = '请输入正确的 11 位手机号'; return; }
  const btn = document.getElementById('btnSendCode');
  btn.disabled = true; btn.textContent = '发送中…';
  try {
    const d = await API.auth.sendSms(phone);
    if (d && d.demo_mode) {
      /* 演示模式（短信网关未配置）：验证码直接展示并自动填入，明示非真实短信 */
      toast('【演示模式·未接短信网关】验证码 ' + d.demo_code + '，已自动填入');
      const codeInput = document.getElementById('lgCode');
      if (codeInput) codeInput.value = d.demo_code;
    } else {
      toast('验证码已发送（' + ((d && d.expires_in) || 300) + ' 秒内有效）');
    }
    let left = (d && d.interval) || 60;
    btn.textContent = left + 's 后重发';
    if (LOGIN_UI.timer) clearInterval(LOGIN_UI.timer);
    LOGIN_UI.timer = setInterval(function () {
      left -= 1;
      if (left <= 0) {
        clearInterval(LOGIN_UI.timer); LOGIN_UI.timer = null;
        btn.disabled = false; btn.textContent = '获取验证码';
      } else { btn.textContent = left + 's 后重发'; }
    }, 1000);
  } catch (e) {
    err.textContent = e.message || '验证码发送失败';
    btn.disabled = false; btn.textContent = '获取验证码';
  }
}

async function doLogin() {
  const err = document.getElementById('loginErr');
  err.textContent = '';
  if (LOGIN_UI.tab === 'phone') {
    const phone = (document.getElementById('lgPhone').value || '').trim();
    const pwd = document.getElementById('lgPwd').value || '';
    if (!phone || !pwd) { err.textContent = '请输入手机号和密码'; return; }
    err.textContent = '登录中…';
    try {
      saveRememberedAccount();
      const data = await API.auth.login(null, pwd, phone);
      err.textContent = '';
      afterLogin(data);
    } catch (e) { err.textContent = e.message || '登录失败'; }
  } else if (LOGIN_UI.tab === 'email') {
    const email = (document.getElementById('egEmail').value || '').trim();
    const pwd = document.getElementById('egPwd').value || '';
    if (!email || !pwd) { err.textContent = '请输入邮箱和密码'; return; }
    err.textContent = '登录中…';
    try {
      saveRememberedAccount();
      const data = await API.auth.login(null, pwd, null, email);
      err.textContent = '';
      afterLogin(data);
    } catch (e) { err.textContent = e.message || '登录失败'; }
  } else {
    const username = (document.getElementById('lgUser').value || '').trim();
    const password = document.getElementById('lgPwdA').value || '';
    if (!username || !password) { err.textContent = '请输入账号和密码'; return; }
    err.textContent = '登录中…';
    try {
      saveRememberedAccount();
      const data = await API.auth.login(username, password);
      err.textContent = '';
      afterLogin(data);
    } catch (e) { err.textContent = e.message || '登录失败'; }
  }
}

async function doRegister() {
  const err = document.getElementById('loginErr');
  err.textContent = '';
  if (LOGIN_UI.tab === 'phone') {
    const phone = (document.getElementById('lgPhone').value || '').trim();
    const code = (document.getElementById('lgCode').value || '').trim();
    const password = document.getElementById('lgPwd').value || '';
    const realName = (document.getElementById('lgName').value || '').trim();
    if (!/^1[3-9]\d{9}$/.test(phone)) { err.textContent = '请输入正确的 11 位手机号'; return; }
    if (!/^\d{6}$/.test(code)) { err.textContent = '请输入 6 位短信验证码'; return; }
    if (password.length < 8) { err.textContent = '密码至少 8 位（后端规则：8–64 位）'; return; }
    err.textContent = '注册中…';
    try {
      await API.auth.register({
        phone: phone, verify_code: code, password: password,
        real_name: realName || (phone.slice(0, 3) + '****' + phone.slice(-4)),
        role: LOGIN_UI.role || 'user'
      });
      err.textContent = '';
      toast('注册成功，正在登录…');
      await doLogin();   // 注册成功后用手机号+密码自动登录
    } catch (e) { err.textContent = e.message || '注册失败'; }
    return;
  }
  if (LOGIN_UI.tab === 'email') {
    const email = (document.getElementById('egEmail').value || '').trim();
    const code = (document.getElementById('egCode').value || '').trim();
    const password = document.getElementById('egPwd').value || '';
    const realName = (document.getElementById('egName').value || '').trim();
    if (!/^[^\s@]+@[^\s@]+\.[A-Za-z]{2,}$/.test(email)) { err.textContent = '请输入正确的邮箱，如 name@example.com'; return; }
    if (!/^\d{6}$/.test(code)) { err.textContent = '请输入 6 位邮箱验证码'; return; }
    if (password.length < 8) { err.textContent = '密码至少 8 位（后端规则：8–64 位）'; return; }
    err.textContent = '注册中…';
    try {
      await API.auth.register({
        email: email, email_code: code, password: password,
        real_name: realName || email.split('@')[0],
        role: LOGIN_UI.role || 'user'
      });
      err.textContent = '';
      toast('注册成功，正在登录…');
      await doLogin();   // 注册成功后用邮箱+密码自动登录
    } catch (e) { err.textContent = e.message || '注册失败'; }
    return;
  }
  const username = (document.getElementById('lgUser').value || '').trim();
  const password = document.getElementById('lgPwdA').value || '';
  const realName = (document.getElementById('lgName').value || '').trim();
  if (!username || !password) { err.textContent = '请输入账号和密码'; return; }
  if (password.length < 8) { err.textContent = '密码至少 8 位（后端规则：8–64 位）'; return; }
  err.textContent = '注册中…';
  try {
    await API.auth.register({
      username: username, password: password, real_name: realName || username,
      role: LOGIN_UI.role || 'user'
    });
    err.textContent = '';
    toast('注册成功，正在登录…');
    await doLogin();   // 注册只返回用户信息，令牌需登录换取
  } catch (e) {
    err.textContent = e.message || '注册失败';
  }
}

async function logout() {
  try { await API.auth.logout(); } catch (e) { /* 后端登出失败也继续清本地态 */ }
  API.Token.clear();
  CURRENT_USER = null;
  updateAuthUI();
  toast('已退出登录');
  onLoginChanged();
}

/* 登录态变化后刷新各屏数据（各屏自行实现，缺省为空操作） */
function onLoginChanged() {
  /* 换账号/退出：会话上下文不能串号 */
  CHAT.sessionId = null;
  window.LAST_CHAT_QUESTION = '';
  if (typeof refreshHome === 'function') { refreshHome(); }
  if (typeof loadNotices === 'function') { loadNotices(); }
  if (typeof loadContacts === 'function') { loadContacts(); }
  if (typeof loadHealthContext === 'function') { loadHealthContext(); }
}

async function refreshUser() {
  if (!getToken()) { CURRENT_USER = null; updateAuthUI(); return; }
  try {
    CURRENT_USER = await API.users.me();
  } catch (e) {
    API.Token.clear();
    CURRENT_USER = null;
  }
  updateAuthUI();
}

function requireLogin() {
  if (CURRENT_USER) return true;
  openLogin();
  toast('请先登录后再执行该操作');
  return false;
}

/* ---------------- 营养师智能体：SSE 流式问答 ----------------
   端点：GET /api/v1/chat/query/stream?ticket=&question=&agent=
   事件：start | reference{docs:[{title,fragment}]} | delta{text}
        | done{elapsed_ms, refused?, reason?, degraded?} | error{code,message}
   鉴权：浏览器 EventSource 不能带 header → 由 API.openStream 先换 60s 短票
   ---------------------------------------------------------- */
function streamChat(question, agent, handlers) {
  const modeEl = document.getElementById('chatMode');
  return API.openStream('/api/v1/chat/query/stream', {
    question: question,
    agent: agent || (modeEl && modeEl.value) || 'dietitian',
    session_id: CHAT.sessionId || ''      // 带上去 → 服务端回传历史，实现多轮上下文
  }, handlers);
}

/* ---------------- 多轮会话（2026-09-11） ---------------- */
const CHAT = { sessionId: null };

/* 进入智能体页面时恢复最近会话：让"继续追问/上下文"在刷新后依然成立 */
async function loadChatHistory() {
  const box = document.getElementById('chatBox');
  if (!box || !getToken()) return;
  try {
    const d = await API.chat.latestSession();
    const tag = document.getElementById('chatSessionTag');
    if (!d || !d.session_id || !(d.messages || []).length) {
      if (tag) tag.textContent = '';
      return;
    }
    CHAT.sessionId = d.session_id;
    if (tag) tag.textContent = '已恢复上次会话（' + d.messages.length + ' 条消息）';
    box.innerHTML = '';
    d.messages.forEach(function (m) {
      const who = m.role === 'user' ? '你' : 'AI营养师';
      const cls = m.role === 'user' ? 'user' : 'ai';
      pushBubble(cls, '<b>' + who + '：</b>' + esc(m.content).replace(/\n/g, '<br>'));
    });
    for (let i = d.messages.length - 1; i >= 0; i -= 1) {
      if (d.messages[i].role === 'user') { window.LAST_CHAT_QUESTION = d.messages[i].content; break; }
    }
  } catch (e) { /* 未登录或读取失败：静默，保持空白会话 */ }
}

/* 开启新会话（清空上下文，服务端下次会新建会话） */
function newChatSession() {
  CHAT.sessionId = null;
  window.LAST_CHAT_QUESTION = '';
  const tag = document.getElementById('chatSessionTag');
  if (tag) tag.textContent = '';
  const box = document.getElementById('chatBox');
  if (box) {
    box.innerHTML = '<div class="bubble ai"><b>AI营养师：</b><br>已开启新会话，之前的对话不再作为上下文。</div>';
  }
  toast('已开启新会话');
}

/* ---------------- 营养师智能体：对话 ---------------- */
function pushBubble(cls, html) {
  const box = document.getElementById('chatBox');
  box.insertAdjacentHTML('beforeend', '<div class="bubble ' + cls + '">' + html + '</div>');
  box.scrollTop = box.scrollHeight;
  return box;
}
function pushTyping() {
  const box = document.getElementById('chatBox');
  box.insertAdjacentHTML('beforeend',
    '<div class="bubble ai" id="typingBubble">AI营养师正在检索知识库… <span class="typing"><i></i><i></i><i></i></span></div>');
  box.scrollTop = box.scrollHeight;
}
function removeTyping() {
  const el = document.getElementById('typingBubble');
  if (el) el.remove();
}

async function sendChat() {
  const input = document.getElementById('chatInput');
  const text = (input.value || '').trim();
  if (!text) return;
  pushBubble('user', esc(text));
  input.value = '';

  const btn = document.getElementById('btnSend');
  if (btn) btn.disabled = true;

  /* 先插入一个可增量更新的 AI 气泡 */
  const box = document.getElementById('chatBox');
  const aiId = 'ai-' + Date.now();
  box.insertAdjacentHTML('beforeend',
    '<div class="bubble ai" id="' + aiId + '"><b>AI营养师：</b><div class="body"></div>' +
    '<div class="typing"><i></i><i></i><i></i></div></div>');
  box.scrollTop = box.scrollHeight;

  const el = document.getElementById(aiId);
  const bodyEl = el.querySelector('.body');
  const typingEl = el.querySelector('.typing');
  let acc = '';
  let refs = [];
  let finished = false;

  window.LAST_CHAT_QUESTION = text;   // 供「基于本次咨询生成食养计划」取上下文
  function stopTyping() { if (typingEl && typingEl.parentNode) { typingEl.remove(); } }
  function append(t) {
    acc += t;
    bodyEl.innerHTML = esc(acc).replace(/\n/g, '<br>');
    box.scrollTop = box.scrollHeight;
  }
  function done() { if (btn) btn.disabled = false; }

  const stream = streamChat(text, null, {
    start: function (d) {
      /* 服务端在 start 事件回传会话 ID：存下来，后续提问即自动带上 → 多轮上下文 */
      if (d && d.session_id) {
        CHAT.sessionId = d.session_id;
        const tag = document.getElementById('chatSessionTag');
        if (tag) tag.textContent = d.has_history ? '多轮对话中' : '新会话';
      }
    },
    reference: function (d) { refs = (d && d.docs) || []; },
    delta: function (d) {
      if (d && d.text) { stopTyping(); append(d.text); }
    },
    done: function (d) {
      finished = true;
      stopTyping();
      let extra = '';
      if (d && d.refused) {
        extra = '<div class="src"><span class="badge orange">合规拒答</span>' +
          '<span class="badge">原因：' + esc(d.reason || 'medical_advice') + '</span></div>';
      } else if (d && d.degraded) {
        extra = '<div class="src"><span class="badge orange">降级回答</span>' +
          '<span class="badge">模型未就绪，仅返回知识库片段</span></div>';
      }
      /* 2026-09-11 按用户要求：不再在回答后追加"引用：xxx.md"徽标（检索照常进行，仅不展示） */
      if (extra) { el.insertAdjacentHTML('beforeend', extra); }
      box.scrollTop = box.scrollHeight;
      done();
    },
    error: function (e) {
      if (finished) return;   // 正常结束后 EventSource 仍会触发一次 error，忽略
      finished = true;
      stopTyping();
      const msg = (e && e.code === API.ErrCode.QUERY_RATE_LIMITED)
        ? '游客免费查询次数已用完，注册后可继续咨询'
        : ((e && e.message) || '连接中断，请重试');
      if (!acc) { bodyEl.innerHTML = '<span style="color:#c0392b">' + esc(msg) + '</span>'; }
      el.insertAdjacentHTML('beforeend', '<div class="src"><span class="badge red">' + esc(msg) + '</span></div>');
      done();
    }
  });

  try {
    await stream.ready;
  } catch (e) {
    if (finished) return;
    finished = true;
    stopTyping();
    bodyEl.innerHTML = '<span style="color:#c0392b">' + esc(e.message || '无法建立连接') + '</span>';
    done();
  }
}

/* ---------------- 健康风险测评：真实全流程 ----------------
   契约（已实测，勿凭记忆改）：
     GET  /api/v1/templates                    → {items:[{id,name,type,version,description}]}
     POST /api/v1/assessments {template_id}    → {assessment_id, status, expire_at, ...}
     GET  /api/v1/assessments/{id}             → {answers, progress, questions:[{question_id,
                                                  title, type, dimension, options:[{value,score}],
                                                  max_score}], dimensions, expire_at}
     PATCH /api/v1/assessments/{id}/answers    → {saved_at, progress:"3/8"}
     POST /api/v1/assessments/{id}/submit      → {report_id, score, level, dimension_scores, generation}
     GET  /api/v1/assessments/{id}/report/stream?ticket=   SSE:
          start{report_id,score,level,state} / reference{docs} / delta{text}
          / done{state,elapsed_ms,disclaimer} / error{code,message}
     GET  /api/v1/reports/{id}                 → {state,score,level,dimension_scores,
                                                  suggestions,analysis_text,references,
                                                  disclaimer,degraded,can_retry}
   ⚠️ 红线：无模板 / 无权限 / 请求失败一律如实提示，绝不编造数据。
   ---------------------------------------------------------- */
const LEVEL_TEXT = { excellent: '优秀', good: '良好', needs_improvement: '待改善' };
const LEVEL_CLASS = { excellent: 'green', good: 'green', needs_improvement: 'red' };
const STATUS_TEXT = { in_progress: '作答中', completed: '已完成', void: '已作废' };

let AS = {
  recordId: null, reportId: null, templateId: null,
  questions: [], answers: {}, stream: null, saveTimer: null, savedAt: null,
  page: 1, pageSize: 8
};

function showStep(which) {
  ['asStepPick', 'asStepQuiz', 'asStepReport'].forEach(function (id) {
    const el = document.getElementById(id);
    if (el) { el.style.display = (id === which) ? '' : 'none'; }
  });
}
function setBadge(txt, cls) {
  const el = document.getElementById('assessBadge');
  if (el) { el.textContent = txt; el.className = 'badge ' + (cls || ''); }
}

/* ---------------- ① 模板列表 ---------------- */
async function loadTemplates() {
  const box = document.getElementById('tplList');
  if (!box) return;
  box.innerHTML = '<div class="desc">加载中…</div>';
  try {
    const d = await API.templates.list();
    const items = (d && d.items) || [];
    const meta = document.getElementById('tplMeta');
    if (meta) { meta.textContent = '共 ' + items.length + ' 个已发布模板'; }
    if (!items.length) {
      box.innerHTML = '<div class="desc">暂无已发布模板（需管理员创建并审核通过后才能测评）</div>';
      return;
    }
    box.innerHTML = items.map(function (t) {
      return '<div class="card quick" style="cursor:pointer" onclick="startAssessment(' + t.id + ')">' +
        '<h3>' + esc(t.name || ('模板 ' + t.id)) + '</h3>' +
        '<p>' + esc(t.description || ('类型 ' + (t.type || '-') + '，' +
          (((t.question_count || 0)) ? t.question_count + ' 题' : '题目见模板详情'))) + '</p>' +
        '<span class="badge">v' + (t.version || 1) + '</span> ' +
        '<span class="badge">' + esc(t.type || '-') + '</span></div>';
    }).join('');
  } catch (e) {
    box.innerHTML = '<div class="desc">加载失败：' + esc(e.message) + '</div>';
  }
}

/* ---------------- ② 发起 + 作答 ---------------- */
async function startAssessment(templateId) {
  if (!requireLogin()) { return; }
  setBadge('发起中…', 'orange');
  try {
    const d = await API.assessments.start(templateId);
    AS.recordId = d.assessment_id;
    AS.templateId = templateId;
    AS.reportId = null;
    AS.answers = {};
    const det = await API.assessments.detail(AS.recordId);
    AS.savedAt = det.saved_at || null;
    renderQuiz(det);
    showStep('asStepQuiz');
    setBadge('作答中', 'orange');
    if (d.resumed) {
      toast('检测到未完成的测评，已为你恢复到上次进度');
    } else {
      toast('测评已发起，共 ' + (det.questions || []).length + ' 题');
    }
  } catch (e) {
    setBadge('发起失败', 'red');
    toast('发起失败：' + e.message);
  }
}

function renderQuiz(det) {
  AS.questions = det.questions || [];
  AS.answers = det.answers || {};
  AS.page = 1;
  const title = document.getElementById('quizTitle');
  if (title) { title.textContent = '② 作答（共 ' + AS.questions.length + ' 题）'; }
  const list = document.getElementById('questionList');
  if (!list) { return; }
  if (!AS.questions.length) {
    list.innerHTML = '<div class="desc">该模板没有题目，无法作答</div>';
    renderQuizPager();
    return;
  }
  bindQuizEvents(list);
  renderQuizPage();
}

/* 单页只渲染 8 题（BV-10）。答案不随翻页丢弃：
   翻页前先 collectAnswers() 收进 AS.answers，回来时按 qid 回填 checked。 */
function renderQuizPage() {
  const list = document.getElementById('questionList');
  if (!list) { return; }
  const total = Math.max(1, Math.ceil(AS.questions.length / AS.pageSize));
  if (AS.page > total) { AS.page = total; }
  const from = (AS.page - 1) * AS.pageSize;
  const slice = AS.questions.slice(from, from + AS.pageSize);

  list.innerHTML = slice.map(function (q, i) {
    const cur = AS.answers[q.question_id];
    let body = '';
    if (q.type === 'number') {
      body = '<input type="number" data-qid="' + esc(q.question_id) + '" ' +
        'value="' + (cur == null ? '' : esc(cur)) + '" placeholder="请输入数值" />';
    } else {
      const multi = (q.type === 'multi');
      body = '<div class="radio-row">' + (q.options || []).map(function (o) {
        const checked = multi
          ? (Array.isArray(cur) && cur.indexOf(o.value) >= 0 ? ' checked' : '')
          : (cur === o.value ? ' checked' : '');
        return '<label><input type="' + (multi ? 'checkbox' : 'radio') + '" ' +
          'name="' + esc(q.question_id) + '" value="' + esc(o.value) + '"' + checked + ' />' +
          '<span>' + esc(o.value) + '</span></label>';
      }).join('') + '</div>';
    }
    return '<div class="q-item"><div style="margin-bottom:9px"><b>' + (from + i + 1) + '. ' +
      esc(q.title || q.question_id) + '</b> <span class="badge">' + esc(q.dimension || '-') +
      '</span></div>' + body + '</div>';
  }).join('');
  renderQuizPager();
  updateQuizProgress();
}

function renderQuizPager() {
  const pager = document.getElementById('quizPager');
  if (!pager) { return; }
  const total = Math.ceil(AS.questions.length / AS.pageSize);
  if (total <= 1) { pager.style.display = 'none'; pager.innerHTML = ''; return; }
  pager.style.display = '';
  let html = '<button type="button" data-pg="prev"' + (AS.page === 1 ? ' disabled' : '') +
    '>上一页</button>';
  for (let p = 1; p <= total; p++) {
    /* 页数多时只留首尾 + 当前页附近，避免 200 题 = 25 个按钮糊成一片 */
    if (total > 9 && p !== 1 && p !== total && Math.abs(p - AS.page) > 2) {
      if (p === 2 || p === total - 1) { html += '<span class="pager-info" style="margin:0 2px">…</span>'; }
      continue;
    }
    html += '<button type="button" data-pg="' + p + '"' +
      (p === AS.page ? ' class="cur"' : '') + '>' + p + '</button>';
  }
  html += '<button type="button" data-pg="next"' + (AS.page === total ? ' disabled' : '') +
    '>下一页</button>';
  html += '<span class="pager-info">第 ' + AS.page + ' / ' + total + ' 页 · 本页 ' +
    AS.pageSize + ' 题</span>';
  pager.innerHTML = html;
  pager.onclick = function (e) {
    const t = e.target;
    if (!t || !t.dataset || !t.dataset.pg) { return; }
    const v = t.dataset.pg;
    let target = AS.page;
    if (v === 'prev') { target = AS.page - 1; }
    else if (v === 'next') { target = AS.page + 1; }
    else { target = Number(v) || 1; }
    const tt = Math.max(1, Math.ceil(AS.questions.length / AS.pageSize));
    if (target < 1 || target > tt) { return; }
    /* 顺序要紧：必须**先**按当前页收答案，再改 AS.page。
       反过来（先翻页再收集）会拿新页的切片去读旧页的 DOM，
       结果是把当前页答案全删掉——翻一次页就丢一次作答。 */
    collectAnswers();
    AS.page = target;
    renderQuizPage();
  };
}

function bindQuizEvents(list) {
  list.onchange = function () { collectAnswers(); updateQuizProgress(); scheduleDraftSave(); };
  list.oninput = function (e) {
    if (e.target && e.target.tagName === 'INPUT' && e.target.type === 'number') {
      collectAnswers(); updateQuizProgress(); scheduleDraftSave();
    }
  };
}

/* 只收**当前页**渲染出来的题，其余题的答案沿用 AS.answers 里的旧值 */
function collectAnswers() {
  const slice = AS.questions.slice((AS.page - 1) * AS.pageSize, AS.page * AS.pageSize);
  slice.forEach(function (q) {
    delete AS.answers[q.question_id];
    if (q.type === 'multi') {
      const picked = [];
      document.querySelectorAll('input[name="' + q.question_id + '"]:checked').forEach(function (el) {
        picked.push(el.value);
      });
      if (picked.length) { AS.answers[q.question_id] = picked; }
    } else if (q.type === 'number') {
      const el = document.querySelector('input[type=number][data-qid="' + q.question_id + '"]');
      if (el && el.value !== '') { AS.answers[q.question_id] = Number(el.value); }
    } else {
      const el = document.querySelector('input[name="' + q.question_id + '"]:checked');
      if (el) { AS.answers[q.question_id] = el.value; }
    }
  });
  return AS.answers;
}

function updateQuizProgress() {
  const el = document.getElementById('quizProgress');
  if (!el) { return; }
  const n = Object.keys(AS.answers).length;
  el.textContent = '作答进度 ' + n + ' / ' + AS.questions.length;
}

/* 自动存草稿（防抖 900ms） */
function scheduleDraftSave() {
  if (AS.saveTimer) { clearTimeout(AS.saveTimer); }
  AS.saveTimer = setTimeout(saveDraft, 900);
}

async function saveDraft() {
  if (!AS.recordId) { return; }
  try {
    const d = await API.assessments.saveDraft(AS.recordId, AS.answers, AS.savedAt);
    AS.savedAt = d.saved_at || AS.savedAt;
    const el = document.getElementById('quizHint');
    if (el) {
      const warn = (d.warnings && d.warnings.length)
        ? '　⚠ ' + d.warnings.map(function (w) { return w.message; }).join('；')
        : '';
      el.textContent = '草稿已自动保存 · 进度 ' + (d.progress || '') +
        '（未作答题目按 0 分计入）' + warn;
    }
  } catch (e) {
    const el = document.getElementById('quizHint');
    if (el) { el.textContent = '草稿保存失败：' + e.message; }
  }
}

async function cancelAssessment() {
  if (!AS.recordId) { showStep('asStepPick'); return; }
  try { await API.assessments.cancel(AS.recordId); toast('已放弃本次测评'); }
  catch (e) { toast('放弃失败：' + e.message); }
  AS.recordId = null;
  showStep('asStepPick');
  setBadge('就绪', 'green');
  loadHistory();
}

/* ---------------- ③ 提交 + 报告流式 ---------------- */
async function submitAssessment() {
  if (!AS.recordId) { toast('请先发起测评'); return; }
  const answers = collectAnswers();
  const missing = AS.questions.length - Object.keys(answers).length;
  if (missing > 0 && !confirm('还有 ' + missing + ' 题未作答，未作答将按 0 分计入。确认提交？')) { return; }

  const btn = document.getElementById('btnSubmitAssess');
  if (btn) { btn.disabled = true; btn.textContent = '提交中…'; }
  setBadge('提交中…', 'orange');
  try {
    if (AS.saveTimer) { clearTimeout(AS.saveTimer); }
    await API.assessments.saveDraft(AS.recordId, answers);
    // 幂等键：同一测评重复点击不会生成第二份报告
    const key = 'as-' + AS.recordId + '-' + Date.now();
    const sub = await API.assessments.submit(AS.recordId, key);
    AS.reportId = sub.report_id;
    renderScore(sub);
    showStep('asStepReport');
    setBadge('已提交', 'orange');
    await streamReport(AS.recordId);
  } catch (e) {
    setBadge('提交失败', 'red');
    toast('提交失败：' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '提交并生成报告'; }
  }
}

function renderScore(sub) {
  const box = document.getElementById('reportKpi');
  const lv = sub.level || '';
  const dims = sub.dimension_scores || [];
  let html =
    '<div class="card kpi"><span class="badge">总分</span><div class="num">' +
    (sub.score == null ? '—' : esc(sub.score)) + '</div>' +
    '<div class="trend">满分 100</div></div>' +
    '<div class="card kpi"><span class="badge">等级</span><div class="num"><span class="badge ' +
    (LEVEL_CLASS[lv] || '') + '">' + esc(LEVEL_TEXT[lv] || lv || '—') + '</span></div>' +
    '<div class="trend">由后端规则引擎判定</div></div>' +
    '<div class="card kpi"><span class="badge">报告编号</span><div class="num">#' +
    esc(sub.report_id) + '</div><div class="trend">' + esc(STATUS_TEXT[sub.status] || sub.status || '') +
    '</div></div>' +
    '<div class="card kpi"><span class="badge">维度数</span><div class="num">' + dims.length +
    '</div><div class="trend">' + dims.map(function (d) { return esc(d.name || d.key); }).join(' · ') +
    '</div></div>';
  if (box) { box.innerHTML = html; }

  const meta = document.getElementById('reportMeta');
  if (meta) { meta.textContent = '报告 #' + sub.report_id + ' · 测评 #' + sub.assessment_id; }

  const sg = document.getElementById('reportSuggestions');
  if (sg) {
    sg.innerHTML = dims.map(function (d) {
      return '<div class="risk-item"><span class="risk-dot ' +
        (d.level === 'needs_improvement' ? 'high' : 'low') + '"></span>' +
        '<div><b>' + esc(d.name || d.key) + '</b>　得分 ' + esc(d.score) +
        '（权重 ' + esc(d.weight) + '）　<span class="badge ' + (LEVEL_CLASS[d.level] || '') + '">' +
        esc(d.level_name || LEVEL_TEXT[d.level] || d.level || '') + '</span></div></div>';
    }).join('') || '<div class="desc">—</div>';
  }
}

async function streamReport(recordId) {
  const box = document.getElementById('reportText');
  const state = document.getElementById('genState');
  if (box) { box.innerHTML = '<div class="desc">正在生成分析文本…</div>'; }
  if (state) { state.textContent = '生成中'; state.className = 'badge orange'; }
  let acc = '';
  let refs = [];
  let done = false;

  const s = API.openStream('/api/v1/assessments/' + recordId + '/report/stream', {}, {
    reference: function (d) { refs = (d && d.docs) || []; },
    delta: function (d) { if (d && d.text) { acc += d.text; if (box) { box.textContent = acc; } } },
    done: function (d) {
      done = true;
      if (state) {
        state.textContent = (d && d.state === 'complete') ? '生成完成' : ('状态：' + ((d && d.state) || '-'));
        state.className = 'badge ' + ((d && d.state === 'complete') ? 'green' : 'orange');
      }
      if (d && d.degraded && box) {
        box.insertAdjacentHTML('beforeend',
          '<div class="hint" style="color:var(--orange)">模型未就绪，已降级为基础版（评分骨架仍完整）。</div>');
      }
      if (refs.length) {
        const r = document.getElementById('reportRefs');
        if (r) {
          r.innerHTML = refs.map(function (x) {
            return '<div class="risk-item"><span class="risk-dot low"></span><div><b>' +
              esc(x.title || '知识库片段') + '</b><div class="desc">' +
              esc((x.fragment || '').slice(0, 120)) + '…</div></div></div>';
          }).join('');
        }
      }
      const dis = document.getElementById('reportDisclaimer');
      if (dis && d && d.disclaimer) { dis.textContent = d.disclaimer; }
      loadHistory();
    },
    error: function (e) {
      if (done) { return; }
      done = true;
      if (state) { state.textContent = '生成失败'; state.className = 'badge red'; }
      if (box && !acc) { box.innerHTML = '<div class="desc">' + esc(e.message || '生成失败') + '</div>'; }
      toast('报告生成中断：' + (e.message || ''));
    }
  });
  try { await s.ready; } catch (e) {
    if (state) { state.textContent = '连接失败'; state.className = 'badge red'; }
    if (box && !acc) { box.innerHTML = '<div class="desc">' + esc(e.message || '无法连接') + '</div>'; }
  }
}

async function reloadReport() {
  if (!AS.reportId) { toast('还没有报告'); return; }
  try {
    const d = await API.reports.detail(AS.reportId);
    const box = document.getElementById('reportText');
    if (box) {
      box.textContent = d.analysis_text ||
        ('（本次未生成分析文本，报告状态：' + (d.state || '-') + '）');
    }
    const state = document.getElementById('genState');
    if (state) {
      state.textContent = '状态：' + (d.state || '-') + (d.degraded ? '（降级）' : '');
      state.className = 'badge ' + (d.state === 'complete' ? 'green' : 'orange');
    }
    if ((d.references || []).length) {
      const r = document.getElementById('reportRefs');
      if (r) {
        r.innerHTML = d.references.map(function (x) {
          const t = x.title || x.file_name || '知识库片段';
          return '<div class="risk-item"><span class="risk-dot low"></span><div><b>' + esc(t) +
            '</b></div></div>';
        }).join('');
      }
    }
    const dis = document.getElementById('reportDisclaimer');
    if (dis && d.disclaimer) { dis.textContent = d.disclaimer; }
    toast('报告已刷新');
  } catch (e) {
    toast('刷新失败：' + e.message);
  }
}

function startNewAssessment() {
  AS.recordId = null; AS.reportId = null; AS.questions = []; AS.answers = {}; AS.savedAt = null;
  showStep('asStepPick');
  setBadge('就绪', 'green');
  loadTemplates();
}

/* ---------------- 我的测评记录 ---------------- */
async function loadHistory() {
  const box = document.getElementById('historyBox');
  if (!box) { return; }
  if (!getToken()) {
    box.innerHTML = '<div class="desc">登录后可查看我的测评记录</div>';
    return;
  }
  box.innerHTML = '<div class="desc">加载中…</div>';
  try {
    const d = await API.assessments.list({ limit: 20 });
    const items = (d && d.items) || [];
    if (!items.length) {
      box.innerHTML = '<div class="desc">暂无测评记录，选一个模板开始吧</div>';
      return;
    }
    let html = '<table class="table"><thead><tr><th>发起时间</th><th>状态</th><th>进度</th>' +
      '<th>得分</th><th>等级</th><th>报告</th></tr></thead><tbody>';
    items.forEach(function (r) {
      const lv = r.level || '';
      html += '<tr><td>' + esc(r.started_at || '-') + '</td>' +
        '<td>' + esc(STATUS_TEXT[r.status] || r.status || '-') + '</td>' +
        '<td>' + esc(r.progress || '-') + '</td>' +
        '<td><b>' + (r.score == null ? '-' : esc(r.score)) + '</b></td>' +
        '<td><span class="badge ' + (LEVEL_CLASS[lv] || '') + '">' + esc(LEVEL_TEXT[lv] || lv || '-') +
        '</span></td>' +
        '<td>' + (r.report_id ? ('#' + esc(r.report_id)) : '-') + '</td></tr>';
    });
    html += '</tbody></table>';
    box.innerHTML = html;
  } catch (e) {
    box.innerHTML = '<div class="desc">加载失败：' + esc(e.message) + '</div>';
  }
}
/* ---------------- Text2SQL 智能分析 ---------------- */
function pickQuery(el) {
  document.querySelectorAll('.query-item').forEach(x => x.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('queryInput').value = el.textContent.trim();
}

function renderTable(rows) {
  const host = document.getElementById('resultTable');
  if (!rows || !rows.length) {
    host.innerHTML = '<div class="desc">暂无数据</div>';
    return;
  }
  const cols = Object.keys(rows[0]);
  let html = '<table class="table"><thead><tr>';
  cols.forEach(c => { html += '<th>' + esc(c) + '</th>'; });
  html += '</tr></thead><tbody>';
  rows.forEach(r => {
    html += '<tr>';
    cols.forEach(c => { html += '<td>' + esc(r[c]) + '</td>'; });
    html += '</tr>';
  });
  html += '</tbody></table>';
  host.innerHTML = html;
}

function renderChart(rows) {
  const host = document.getElementById('resultChart');
  const meta = document.getElementById('chartMeta');
  if (!rows || !rows.length) { host.innerHTML = ''; meta.textContent = '暂无数据'; return; }
  const cols = Object.keys(rows[0]);
  const labelCol = cols[0];
  const numCol = cols.find(c => rows.some(r => typeof r[c] === 'number')) ||
    cols.find(c => !isNaN(parseFloat(rows[0][c])));
  if (!numCol) { host.innerHTML = ''; meta.textContent = '结果中未找到可绘制的数值列'; return; }

  const vals = rows.map(r => parseFloat(r[numCol]) || 0);
  const max = Math.max.apply(null, vals.concat([1]));
  host.innerHTML = vals.map(v =>
    '<i style="height:' + Math.max(4, Math.round(v / max * 100)) + '%" title="' + esc(v) + '"></i>').join('');
  meta.textContent = '柱状图 · ' + esc(numCol) + '（共 ' + rows.length + ' 条）';
}

async function runQuery() {
  const q = (document.getElementById('queryInput').value || '').trim();
  if (!q) { toast('请输入查询问题'); return; }
  const btn = document.getElementById('btnQuery');
  const errBox = document.getElementById('queryErr');
  errBox.innerHTML = '';
  btn.disabled = true;
  btn.textContent = '执行中…';

  /* 受控 Text2SQL：POST /api/v1/analytics/text2sql/query?question=
     返回 {sql, rows, columns, total, chart, interpretation, row_scope, note}
     ⚠️ 绝不伪造数据：查询失败/不支持时如实提示，不再回退演示数值。 */
  let rows = [], sql = '', insight = '', note = '';
  try {
    const d = await API.analytics.query(q);
    sql = d.sql || '';
    rows = d.rows || [];
    insight = d.interpretation || '';
    note = d.note || '';
  } catch (e) {
    const hint = (e.code === API.ErrCode.PARAM_INVALID)
      ? '（可点击左侧「支持的问法」查看模板清单）' : '';
    errBox.innerHTML = '<div class="note err" style="margin-top:12px">' +
      esc(e.message || '查询失败') + hint + '</div>';
    document.getElementById('sqlBox').textContent = '';
    lastResultRows = [];
    renderTable([]);
    renderChart([]);
    document.getElementById('resultInsight').textContent = '';
    btn.disabled = false;
    btn.textContent = '执行查询';
    return;
  }

  document.getElementById('sqlBox').textContent = sql;
  lastResultRows = rows;
  renderTable(rows);
  renderChart(rows);
  document.getElementById('resultInsight').textContent =
    (insight || ('本次查询共返回 ' + rows.length + ' 条记录。')) + (note ? '（' + note + '）' : '');

  btn.disabled = false;
  btn.textContent = '执行查询';
  toast(rows.length ? ('查询完成，共 ' + rows.length + ' 条记录') : '查询完成（暂无数据）');
}

function exportCsv() {
  if (!lastResultRows.length) { toast('暂无可导出的查询结果'); return; }
  const cols = Object.keys(lastResultRows[0]);
  const lines = [cols.join(',')].concat(lastResultRows.map(r =>
    cols.map(c => '"' + String(r[c] == null ? '' : r[c]).replace(/"/g, '""') + '"').join(',')));
  const blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'text2sql_result.csv';
  a.click();
  URL.revokeObjectURL(a.href);
  toast('已导出 CSV（Excel 可直接打开）');
}

/* ---------------- 营养师工作台 ----------------
   GET /api/v1/nutritionists/me/customers →
     { items:[{customer_id, latest_score, latest_level, height_cm, weight_kg,
               diet_preference, tags, execution_rate, last_consult_at}], total }
   ⚠️ 无权限/无数据时如实提示，不展示编造的客户行。
   ---------------------------------------------------------- */
/* ---------------- 营养师工作台（真实数据） ---------------- */
async function loadDietitian() {
  const ids = ['dtCustomers', 'dtHighRisk', 'dtToday', 'dtSatisfaction'];
  try {
    const d = await API.nutritionists.overview();
    setText('dtCustomers', d.customer_count == null ? '—' : String(d.customer_count));
    setText('dtHighRisk', d.high_risk_count == null ? '—' : String(d.high_risk_count));
    setText('dtToday', d.today_consult_count == null ? '—' : String(d.today_consult_count));
    setText('dtSatisfaction',
      d.satisfaction == null ? '—' : (d.satisfaction + '%（' + (d.rating_count || 0) + ' 次评分）'));
  } catch (e) {
    ids.forEach(i => setText(i, '—'));
  }
  loadLicense();
  loadMyTemplates();
  loadClients(DT.level);
  loadConsultBoard();
}

/* ---------------- 咨询接待（营养师工作台 · 故事 4） ----------------
 * 流程：待接入队列接入 → 打开会话 → 点「AI 辅助建议」生成**草稿** → 人工修改 → 发送。
 * AI 只填输入框，绝不自动发送；发送时按 state 标记 ai_assisted（来源可回溯）。
 */
async function loadConsultBoard() {
  await Promise.all([loadPendingConsults(), loadMyConsults()]);
}

async function loadPendingConsults() {
  const box = document.getElementById('dtPendingList');
  if (!box) return;
  try {
    const d = await API.consultations.pending();
    const items = (d && d.items) || [];
    box.innerHTML = items.length
      ? items.map(function (c) {
        return '<div class="dt-pick"><span>#' + c.consultation_id + '　' +
          esc(c.created_at || '') + '</span>' +
          '<button class="btn" onclick="acceptConsult(' + c.consultation_id + ')">接入</button></div>';
      }).join('')
      : '<div class="desc">当前没有待接入的咨询</div>';
  } catch (e) {
    box.innerHTML = '<div class="desc">加载失败：' + esc(e.message || '') + '</div>';
  }
}

async function loadMyConsults() {
  const box = document.getElementById('dtConsultList');
  if (!box) return;
  try {
    const d = await API.consultations.list({ limit: 20 });
    const items = (d && d.items) || [];
    box.innerHTML = items.length
      ? items.map(function (c) {
        const active = (DT.consultId === c.consultation_id) ? ' active' : '';
        const st = { pending: '待接入', active: '进行中', closed: '已结束', timeout: '超时' }[c.status] || c.status;
        return '<div class="dt-pick' + active + '"><span>#' + c.consultation_id +
          '　<span class="badge">' + esc(st) + '</span></span>' +
          '<button class="btn" onclick="openConsult(' + c.consultation_id + ')">打开</button></div>';
      }).join('')
      : '<div class="desc">暂无分配给自己的咨询</div>';
  } catch (e) {
    box.innerHTML = '<div class="desc">加载失败：' + esc(e.message || '') + '</div>';
  }
}

async function acceptConsult(id) {
  try {
    await API.consultations.accept(id);
    toast('已接入咨询 #' + id);
    await loadConsultBoard();
    await openConsult(id);
  } catch (e) { toast('接入失败：' + e.message); }
}

async function openConsult(id) {
  DT.consultId = id;
  DT.aiAssisted = false;
  setText('dtConsultWhich', '咨询 #' + id);
  setText('dtAiNote', '');
  setText('dtAiRefs', '');
  const box = document.getElementById('dtConsultMsgs');
  try {
    const d = await API.consultations.messages(id);
    const items = (d && d.items) || [];
    box.innerHTML = items.length
      ? items.map(renderConsultMsg).join('')
      : '<div class="desc">暂无消息</div>';
    box.scrollTop = box.scrollHeight;
  } catch (e) {
    box.innerHTML = '<div class="desc">加载失败：' + esc(e.message || '') + '</div>';
  }
  await loadMyConsults();
}

function renderConsultMsg(m) {
  const who = { user: '客户', dietitian: '营养师', system: '系统' }[m.sender_type] || m.sender_type;
  const cls = m.sender_type === 'dietitian' ? 'me' : (m.sender_type === 'system' ? 'sys' : 'user');
  const tag = m.ai_assisted ? ' <span class="badge">AI 辅助</span>' : '';
  return '<div class="dt-msg ' + cls + '"><span class="who">' + who + tag + '</span>' +
    '<span class="body">' + esc(m.content) + '</span></div>';
}

async function genAiSuggestion() {
  if (!DT.consultId) { toast('请先从左侧选择一条咨询'); return; }
  const note = document.getElementById('dtAiNote');
  note.textContent = '生成中…（首次调用模型较慢）';
  try {
    const hint = (document.getElementById('dtConsultInput').value || '').trim();
    const d = await API.consultations.aiSuggestion(DT.consultId, hint);
    const box = document.getElementById('dtConsultInput');
    box.value = d.suggestion || '';
    /* 来源标记：只有模型/检索真出了内容才算"AI 辅助"；
       refused 是平台标准就医话术（不是模型输出），标了会让使用率虚高 */
    DT.aiAssisted = (d.state === 'ok' || d.state === 'basic');
    setText('dtAiNote', d.hint || '');
    const refs = (d.references || []).map(function (r) { return '《' + r.title + '》'; }).join('、');
    setText('dtAiRefs', refs ? ('知识库依据：' + refs) : '');
    if (d.refused) { toast('该诉求属高风险类别，已给出就医指引（未调用模型）'); }
    else if (d.state === 'basic') { toast('模型暂不可用，已退回知识库片段，请自行整理'); }
    else { toast('草稿已生成，请修改确认后发送'); }
  } catch (e) {
    note.textContent = '';
    toast('生成失败：' + e.message);
  }
}

async function sendConsultMsg() {
  if (!DT.consultId) { toast('请先从左侧选择一条咨询'); return; }
  const el = document.getElementById('dtConsultInput');
  const content = (el.value || '').trim();
  if (!content) { toast('请输入回复内容'); return; }
  try {
    await API.consultations.send(DT.consultId, content, DT.aiAssisted);
    el.value = '';
    DT.aiAssisted = false;
    setText('dtAiNote', '');
    setText('dtAiRefs', '');
    toast('已发送');
    await openConsult(DT.consultId);
  } catch (e) { toast('发送失败：' + e.message); }
}

async function exportCustomers(fmt) {
  try {
    const d = await API.nutritionists.exportCustomers(fmt || 'csv');
    const url = d && (d.url || d.download_url || d.signed_url);
    if (url) {
      toast('报表已生成，正在打开…');
      window.open(url, '_blank');
    } else {
      toast('报表已生成');
    }
  } catch (e) {
    const tip = (e.code === API.ErrCode.PERMISSION_DENIED || e.code === API.ErrCode.UNAUTHENTICATED)
      ? '需要营养师账号登录后才能导出客户报表。' : ('导出失败：' + e.message);
    toast(tip);
  }
}

async function loadClients() {
  const body = document.getElementById('clientBody');
  if (!body) return;
  body.innerHTML = '<tr><td colspan="7" class="desc">加载中…</td></tr>';
  try {
    const d = await API.nutritionists.customers();
    const items = (d && d.items) || [];
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="7" class="desc">暂无管辖客户（需营养师账号且已绑定客户）</td></tr>';
    } else {
      body.innerHTML = items.map(function (c) {
        const lv = c.latest_level || '';
        const tags = (c.tags && c.tags.length) ? c.tags.join(' · ') : (c.diet_preference || '-');
        return '<tr><td>' + esc(c.customer_id || '-') + '</td>' +
          '<td>' + esc(tags) + '</td>' +
          '<td>' + (c.latest_score == null ? '-' : esc(c.latest_score)) + ' / ' + esc(LEVEL_TEXT[lv] || lv || '-') + '</td>' +
          '<td><span class="badge ' + (LEVEL_CLASS[lv] || '') + '">' + esc(LEVEL_TEXT[lv] || '-') + '</span></td>' +
          '<td>' + (c.execution_rate == null ? '-' : (esc(c.execution_rate) + '%')) + '</td>' +
          '<td>' + esc(c.last_consult_at || '-') + '</td>' +
          "<td><button class=\"btn\" onclick=\"go('nutrition')\">进入咨询</button></td></tr>";
      }).join('');
    }
    toast('客户列表已刷新（共 ' + items.length + ' 位）');
  } catch (e) {
    const tip = (e.code === API.ErrCode.PERMISSION_DENIED || e.code === API.ErrCode.UNAUTHENTICATED)
      ? '需要营养师账号登录' : ('加载失败：' + e.message);
    body.innerHTML = '<tr><td colspan="7" class="desc">' + esc(tip) + '</td></tr>';
  }
}


/* ================= 营养师专业端（批次2：资质 / 客户 / 模板） ================= */
const DT = { level: '', me: null, invite: '', templates: [], editingId: null, customer: null,
             consultId: null, aiAssisted: false };
const AUDIT_TEXT = { pending: '待审核', approved: '已通过', rejected: '已驳回', none: '未提交' };
const AUDIT_CLASS = { pending: 'orange', approved: 'green', rejected: 'red' };
/* 后端 template_status 真实取值：draft / pending / published / offline（驳回会退回 draft 并带 audit_remark） */
const TPL_STATUS_TEXT = { draft: '草稿', pending: '待审核', published: '已发布', offline: '已下线' };
const TPL_STATUS_CLASS = { draft: 'orange', pending: 'orange', published: 'green', offline: '' };
const LIC_SPECS = ['孕期营养', '糖尿病饮食', '高血压饮食', '体重管理', '儿童青少年营养', '老年营养', '术后康复', '中医食养'];

/* ---------- 资质与服务 ---------- */
async function loadLicense() {
  const box = document.getElementById('dtLicenseBody');
  if (!box) return;
  box.innerHTML = '<span class="desc">加载中…</span>';
  try {
    const d = await API.nutritionists.me();
    DT.me = d;
    const st = AUDIT_TEXT[d.audit_status] || d.audit_status || '未知';
    const sp = (d.specialties || []).join('、') || '未填写';
    let html = '<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">' +
      '<span class="badge ' + (AUDIT_CLASS[d.audit_status] || '') + '">资质' + esc(st) + '</span>' +
      '<span class="desc">资格证：' + esc(d.cert_name || '—') + '｜编号：' + esc(d.cert_no || '—') + '</span>' +
      '</div>' +
      '<div class="hint" style="margin-top:8px">擅长领域：' + esc(sp) +
      '｜管辖客户：' + (d.customer_count == null ? '—' : d.customer_count) + ' 位</div>';
    if (d.audit_status === 'rejected' && d.reject_reason) {
      html += '<div class="hint" style="margin-top:6px;color:var(--red)">驳回原因：' + esc(d.reject_reason) + '（可修改后重新提交）</div>';
    }
    if (d.audit_status === 'approved') {
      html += '<div class="hint" style="margin-top:10px">我的邀请码：<b style="letter-spacing:2px">' +
        esc(DT.invite || '（点右上「生成邀请码」获取）') + '</b>' +
        (DT.invite ? ' <a onclick="copyText(\'' + esc(DT.invite) + '\')" style="cursor:pointer;color:var(--brand2)">复制</a>' : '') +
        '｜把邀请码发给客户即可绑定</div>';
    } else {
      html += '<div class="hint" style="margin-top:10px">审核通过后才能生成邀请码、绑定客户与创建模板。</div>';
    }
    if (d.cases) { html += '<div class="hint" style="margin-top:6px">服务案例：' + esc(d.cases) + '</div>'; }
    box.innerHTML = html;
  } catch (e) {
    if (e.code === 30001) {
      box.innerHTML = '<span class="badge orange">未提交资质</span>' +
        '<div class="hint" style="margin-top:8px">点右上「提交 / 修改资质」提交资格证信息，管理员审核通过后即可接单。</div>';
    } else if (e.code === API.ErrCode.PERMISSION_DENIED) {
      box.innerHTML = '<span class="desc">当前账号不是营养师（无资质信息查看权限）</span>';
    } else {
      box.innerHTML = '<span class="desc">读取失败：' + esc(e.message) + '</span>';
    }
  }
}

function openLicenseForm() {
  if (!requireLogin()) return;
  const box = document.getElementById('licSpecs');
  if (box && !box.childElementCount) {
    box.innerHTML = LIC_SPECS.map(function (n) {
      return '<button class="pf-chip" data-name="' + esc(n) + '" onclick="this.classList.toggle(\'active\')">' + esc(n) + '</button>';
    }).join('');
  }
  const m = DT.me || {};
  const set = function (id, v) { const el = document.getElementById(id); if (el) el.value = (v == null ? '' : v); };
  set('licName', m.cert_name); set('licNo', m.cert_no); set('licExpire', m.cert_expire_at); set('licCases', m.cases);
  document.querySelectorAll('#licSpecs .pf-chip').forEach(function (b) {
    b.classList.toggle('active', (m.specialties || []).indexOf(b.dataset.name) >= 0);
  });
  document.getElementById('licErr').textContent = '';
  document.getElementById('licenseModal').classList.add('open');
}
function closeLicenseForm() { document.getElementById('licenseModal').classList.remove('open'); }

async function saveLicense() {
  const err = document.getElementById('licErr');
  err.textContent = '';
  const name = (document.getElementById('licName').value || '').trim();
  const no = (document.getElementById('licNo').value || '').trim();
  if (!name) { err.textContent = '请填写资格证名称'; return; }
  if (!no) { err.textContent = '请填写证书编号（审核需要）'; return; }
  const expire = (document.getElementById('licExpire').value || '').trim();
  if (expire && !/^\d{4}-\d{2}-\d{2}$/.test(expire)) { err.textContent = '有效期格式应为 YYYY-MM-DD'; return; }
  const specs = [];
  document.querySelectorAll('#licSpecs .pf-chip.active').forEach(function (b) { specs.push(b.dataset.name); });
  err.textContent = '提交中…';
  try {
    await API.nutritionists.apply({
      cert_name: name, cert_no: no, cert_expire_at: expire || null,
      cert_url: (document.getElementById('licUrl').value || '').trim() || null,
      work_proof_url: (document.getElementById('licWork').value || '').trim() || null,
      specialties: specs,
      cases: (document.getElementById('licCases').value || '').trim() || null
    });
    err.textContent = '';
    toast('资质已提交，等待管理员审核');
    closeLicenseForm();
    loadLicense();
  } catch (e) { err.textContent = e.message || '提交失败'; }
}

async function genInviteCode() {
  if (!requireLogin()) return;
  try {
    const d = await API.nutritionists.inviteCode();
    DT.invite = d.invite_code || '';
    toast('邀请码：' + DT.invite + '（已显示在资质卡中）');
    loadLicense();
  } catch (e) {
    toast(e.code === 30001 ? '尚未提交资质申请' : ('生成失败：' + e.message));
  }
}

function copyText(t) {
  try {
    navigator.clipboard.writeText(t);
    toast('已复制：' + t);
  } catch (e) { toast('复制失败，请手动记录：' + t); }
}

/* ---------- 客户列表（含等级筛选）+ 报告查看 + 解除绑定 ---------- */
async function loadClients(level) {
  const body = document.getElementById('clientBody');
  if (!body) return;
  if (typeof level === 'string') {
    DT.level = level;
    document.querySelectorAll('#dtFilter .pf-chip').forEach(function (b) {
      b.classList.toggle('active', b.dataset.level === level);
    });
  }
  body.innerHTML = '<tr><td colspan="7" class="desc">加载中…</td></tr>';
  try {
    const d = await API.nutritionists.customers(DT.level ? { level: DT.level } : undefined);
    const items = (d && d.items) || [];
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="7" class="desc">' +
        (DT.level ? '该筛选条件下暂无客户' : '暂无管辖客户：让客户在「营养师智能体」左栏点「绑定营养师」并输入你的邀请码') +
        '</td></tr>';
    } else {
      body.innerHTML = items.map(function (c) {
        const lv = c.latest_level || '';
        const tags = (c.tags && c.tags.length) ? c.tags.join(' · ') : (c.diet_preference || '-');
        const uid = esc(c.customer_id || '');
        const nm = esc(c.real_name || c.customer_id || '-');
        return '<tr><td>' + nm + (c.real_name ? '<div class="desc" style="font-size:11px">' + uid + '</div>' : '') + '</td>' +
          '<td>' + esc(tags) + '</td>' +
          '<td>' + (c.latest_score == null ? '-' : esc(c.latest_score)) + ' / ' + esc(LEVEL_TEXT[lv] || lv || '-') + '</td>' +
          '<td><span class="badge ' + (LEVEL_CLASS[lv] || '') + '">' + esc(LEVEL_TEXT[lv] || '-') + '</span></td>' +
          '<td>' + (c.execution_rate == null ? '-' : (esc(c.execution_rate) + '%')) + '</td>' +
          '<td>' + esc(c.last_consult_at || '-') + '</td>' +
          '<td><button class="btn" onclick="openCustomerReports(\'' + uid + '\')">查看报告</button> ' +
          '<button class="btn" onclick="unbindCustomer(\'' + uid + '\')">解除绑定</button></td></tr>';
      }).join('');
    }
  } catch (e) {
    const tip = (e.code === API.ErrCode.PERMISSION_DENIED || e.code === API.ErrCode.UNAUTHENTICATED)
      ? '需要营养师账号登录' : ('加载失败：' + e.message);
    body.innerHTML = '<tr><td colspan="7" class="desc">' + esc(tip) + '</td></tr>';
  }
}

async function openCustomerReports(uid) {
  DT.customer = uid;
  const modal = document.getElementById('cusReportModal');
  const body = document.getElementById('crBody');
  document.getElementById('crTitle').textContent = '客户测评报告';
  document.getElementById('crSub').textContent = '客户 ID：' + uid + '（仅可查看已绑定给你的客户）';
  body.innerHTML = '加载中…';
  modal.classList.add('open');
  try {
    const d = await API.nutritionists.customerReports(uid);
    const items = (d && d.items) || [];
    if (!items.length) { body.innerHTML = '<div class="desc">该客户暂无测评报告。</div>'; return; }
    body.innerHTML = '<div class="table-wrap"><table class="table"><thead><tr><th>报告</th><th>总分</th><th>等级</th><th>时间</th><th>操作</th></tr></thead><tbody>' +
      items.map(function (r) {
        const lv = r.level || '';
        return '<tr><td>#' + esc(r.report_id) + '</td><td>' + (r.score == null ? '-' : esc(r.score)) + '</td>' +
          '<td><span class="badge ' + (LEVEL_CLASS[lv] || '') + '">' + esc(LEVEL_TEXT[lv] || lv || '-') + '</span></td>' +
          '<td>' + esc(r.created_at || '-') + '</td>' +
          '<td><button class="btn primary" onclick="openReportDetail(' + esc(r.report_id) + ')">查看详情</button></td></tr>';
      }).join('') + '</tbody></table></div>';
  } catch (e) {
    body.innerHTML = '<div class="desc">' + esc(e.code === 90002 ? '该客户未绑定给你（无权查看）' : ('加载失败：' + e.message)) + '</div>';
  }
}
function closeCustomerReports() { document.getElementById('cusReportModal').classList.remove('open'); }

async function openReportDetail(reportId) {
  const body = document.getElementById('crBody');
  body.innerHTML = '加载详情中…';
  try {
    const d = await API.reports.detail(reportId);
    const dims = (d.dimensions || d.dimension_scores || []).map(function (x) {
      const name = x.name || x.key || '';
      const sc = (x.score == null ? '-' : x.score);
      return '<div class="hint">' + esc(name) + '：<b>' + esc(sc) + '</b>' +
        (x.level_name || x.level ? '（' + esc(x.level_name || x.level) + '）' : '') + '</div>';
    }).join('');
    body.innerHTML =
      '<div class="hint"><a onclick="openCustomerReports(DT.customer)" style="cursor:pointer;color:var(--brand2)">← 返回报告列表</a></div>' +
      '<div style="margin-top:8px"><span class="badge green">总分 ' + esc(d.score == null ? '—' : d.score) + '</span> ' +
      '<span class="badge">' + esc(LEVEL_TEXT[d.level] || d.level || '—') + '</span> ' +
      '<span class="desc">报告 #' + esc(reportId) + '</span></div>' +
      '<div style="margin-top:10px">' + (dims || '<div class="desc">无维度数据</div>') + '</div>' +
      '<div class="sub-title" style="margin:12px 0 6px">报告正文</div>' +
      '<div class="hint" style="white-space:pre-wrap;line-height:1.8">' + esc(d.summary_text || d.text || '（无正文）') + '</div>' +
      '<div class="sub-title" style="margin:14px 0 6px">人工批注（与 AI 原文并存）</div>' +
      '<textarea id="crAnno" rows="3" placeholder="写下你的复核意见" style="width:100%;padding:8px 12px;border:1px solid var(--line);border-radius:10px;font-family:inherit;font-size:13px">' +
      esc(d.annotation || '') + '</textarea>' +
      '<div class="hero-actions" style="margin-top:8px"><button class="btn primary" onclick="saveAnnotation(' + esc(reportId) + ')">保存批注</button></div>';
  } catch (e) {
    body.innerHTML = '<div class="desc">无法读取报告：' + esc(e.message) + '</div>';
  }
}

async function saveAnnotation(reportId) {
  const el = document.getElementById('crAnno');
  const content = (el && el.value || '').trim();
  if (!content) { toast('请输入批注内容'); return; }
  try {
    await API.reports.annotate(reportId, { content: content });
    toast('批注已保存（与 AI 原文并存）');
  } catch (e) { toast('保存失败：' + e.message); }
}

async function unbindCustomer(uid) {
  if (!confirm('确认解除与客户 ' + uid + ' 的绑定？解除后你将无法查看其报告。')) return;
  try {
    await API.nutritionists.unbindCustomer(uid);
    toast('已解除绑定');
    loadClients(DT.level);
    loadDietitian();
  } catch (e) { toast('解除失败：' + e.message); }
}

/* ---------- 我的测评模板 ---------- */
async function loadMyTemplates() {
  const body = document.getElementById('tplMineBody');
  if (!body) return;
  body.innerHTML = '<tr><td colspan="7" class="desc">加载中…</td></tr>';
  try {
    const d = await API.templates.mine();
    const items = (d && d.items) || [];
    DT.templates = items;
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="7" class="desc">还没有模板：点右上「新建模板」创建，提交审核通过后用户端即可见。</td></tr>';
      return;
    }
    body.innerHTML = items.map(function (t) {
      const st = TPL_STATUS_TEXT[t.status] || t.status;
      const canEdit = (t.status === 'draft');
      const remark = (t.audit_remark && t.status === 'draft')
        ? '<div class="desc" style="font-size:11px;color:var(--red)">审核意见：' + esc(t.audit_remark) + '</div>' : '';
      return '<tr><td>' + esc(t.code) + '</td><td>' + esc(t.name) + remark + '</td><td>' + esc(t.type) + '</td>' +
        '<td>v' + esc(t.version) + '</td>' +
        '<td><span class="badge ' + (TPL_STATUS_CLASS[t.status] || '') + '">' + esc(st) + '</span></td>' +
        '<td>' + esc((t.updated_at || t.created_at || '-')) + '</td>' +
        '<td>' + (canEdit ? '<button class="btn" onclick="openTemplateForm(' + esc(t.id) + ')">编辑</button> ' : '') +
        (t.status === 'draft'
          ? '<button class="btn primary" onclick="submitTemplateAudit(' + esc(t.id) + ')">提交审核</button> ' : '') +
        '<button class="btn" onclick="openTemplateVersions(' + esc(t.id) + ')">版本</button></td></tr>';
    }).join('');
  } catch (e) {
    const tip = (e.code === API.ErrCode.PERMISSION_DENIED || e.code === API.ErrCode.UNAUTHENTICATED)
      ? '需要营养师账号登录' : ('加载失败：' + e.message);
    body.innerHTML = '<tr><td colspan="7" class="desc">' + esc(tip) + '</td></tr>';
  }
}

function tplDimRow(key, name, weight) {
  return '<div class="setting-row" style="grid-template-columns:1fr 1fr 90px 40px;gap:6px;margin-bottom:6px">' +
    '<input class="tpl-dim-key" placeholder="维度key（英文）" value="' + esc(key || '') + '" />' +
    '<input class="tpl-dim-name" placeholder="维度名称" value="' + esc(name || '') + '" />' +
    '<input class="tpl-dim-weight" placeholder="权重" value="' + esc(weight == null ? '' : weight) + '" />' +
    '<button class="btn" onclick="this.parentNode.remove()">×</button></div>';
}
function tplAddDim() {
  const box = document.getElementById('tplDims');
  box.insertAdjacentHTML('beforeend', tplDimRow('', '', ''));
}
function tplQuestionRow(q) {
  q = q || {};
  const dims = (q.dimension || '');
  return '<div class="card" style="padding:10px;margin-bottom:8px" data-q>' +
    '<div class="setting-row" style="grid-template-columns:110px 1fr 110px 40px;gap:6px">' +
    '<input class="tpl-q-id" placeholder="题目ID q1" value="' + esc(q.question_id || '') + '" />' +
    '<input class="tpl-q-title" placeholder="题目标题" value="' + esc(q.title || '') + '" />' +
    '<input class="tpl-q-dim" placeholder="所属维度key" value="' + esc(dims) + '" />' +
    '<button class="btn" onclick="this.closest(\'[data-q]\').remove()">×</button></div>' +
    '<div class="setting-row" style="grid-template-columns:110px 1fr 110px;gap:6px;margin-top:6px">' +
    '<select class="tpl-q-type" style="height:38px;border:1px solid var(--line);border-radius:10px;padding:0 8px">' +
    ['single', 'multi', 'number'].map(function (t) {
      return '<option value="' + t + '"' + (q.type === t ? ' selected' : '') + '>' + t + '</option>';
    }).join('') + '</select>' +
    '<textarea class="tpl-q-opts" rows="2" placeholder="选项：优秀|100（换行分隔；number 题用 最小|最大|上限分）" style="padding:6px 10px;border:1px solid var(--line);border-radius:10px;font-family:inherit;font-size:12px">' +
    esc((q.options || []).map(function (o) { return o.value + '|' + o.score; }).join('\n')) + '</textarea>' +
    '<input class="tpl-q-max" placeholder="该题满分" value="' + esc(q.max_score == null ? '' : q.max_score) + '" /></div></div>';
}
function tplAddQuestion() {
  const box = document.getElementById('tplQs');
  box.insertAdjacentHTML('beforeend', tplQuestionRow({ question_id: 'q' + (box.childElementCount + 1), type: 'single' }));
}

async function openTemplateForm(id) {
  DT.editingId = id || null;
  document.getElementById('tplModalTitle').textContent = id ? ('编辑模板 #' + id + '（保存后生成新版本）') : '新建测评模板';
  document.getElementById('tplErr').textContent = '';
  const dims = document.getElementById('tplDims');
  const qs = document.getElementById('tplQs');
  dims.innerHTML = ''; qs.innerHTML = '';
  let t = null;
  if (id) {
    try { t = await API.templates.detail(id); } catch (e) { toast('读取模板失败：' + e.message); return; }
  }
  document.getElementById('tplCode').value = t ? (t.code || '') : '';
  document.getElementById('tplCode').disabled = !!t;
  document.getElementById('tplName').value = t ? (t.name || '') : '';
  document.getElementById('tplType').value = t ? (t.type || 'risk') : 'risk';
  document.getElementById('tplDesc').value = t ? (t.description || '') : '';
  const dc = (t && t.dimension_config) || [{ key: 'basic', name: '基础营养', weight: 1 }];
  const qc = (t && t.question_config) || [{ question_id: 'q1', title: '', dimension: 'basic', type: 'single', options: [], max_score: 100 }];
  dc.forEach(function (x) { dims.insertAdjacentHTML('beforeend', tplDimRow(x.key, x.name, x.weight)); });
  (Array.isArray(qc) ? qc : (qc.questions || [])).forEach(function (q) { qs.insertAdjacentHTML('beforeend', tplQuestionRow(q)); });
  document.getElementById('tplModal').classList.add('open');
}
function closeTemplateForm() { document.getElementById('tplModal').classList.remove('open'); }

function collectTemplate() {
  const dims = [];
  document.querySelectorAll('#tplDims .tpl-dim-key').forEach(function (el, i) {
    const row = el.parentNode;
    dims.push({
      key: el.value.trim(),
      name: row.querySelector('.tpl-dim-name').value.trim(),
      weight: parseFloat(row.querySelector('.tpl-dim-weight').value)
    });
  });
  const questions = [];
  document.querySelectorAll('#tplQs [data-q]').forEach(function (row) {
    const type = row.querySelector('.tpl-q-type').value;
    const raw = (row.querySelector('.tpl-q-opts').value || '').trim();
    let options = [];
    if (type !== 'number' && raw) {
      options = raw.split('\n').map(function (line) {
        const p = line.split('|');
        return { value: (p[0] || '').trim(), score: parseFloat(p[1] || '0') };
      }).filter(function (o) { return o.value; });
    }
    questions.push({
      question_id: row.querySelector('.tpl-q-id').value.trim(),
      title: row.querySelector('.tpl-q-title').value.trim(),
      dimension: row.querySelector('.tpl-q-dim').value.trim(),
      type: type,
      options: options,
      max_score: parseFloat(row.querySelector('.tpl-q-max').value || '0')
    });
  });
  return {
    code: (document.getElementById('tplCode').value || '').trim(),
    name: (document.getElementById('tplName').value || '').trim(),
    type: document.getElementById('tplType').value,
    description: (document.getElementById('tplDesc').value || '').trim(),
    dimension_config: dims,
    question_config: questions
  };
}

async function saveTemplate() {
  const err = document.getElementById('tplErr');
  err.textContent = '';
  const b = collectTemplate();
  if (!b.code) { err.textContent = '请填写模板编码'; return; }
  if (!b.name) { err.textContent = '请填写模板名称'; return; }
  if (!b.dimension_config.length || b.dimension_config.some(function (x) { return !x.key || !x.name || isNaN(x.weight); })) {
    err.textContent = '请补全维度：key / 名称 / 权重（数字）'; return;
  }
  const dimKeys = b.dimension_config.map(function (x) { return x.key; });
  if (!b.question_config.length) { err.textContent = '至少需要 1 道题目'; return; }
  const bad = b.question_config.find(function (q) {
    return !q.question_id || !q.title || dimKeys.indexOf(q.dimension) < 0 || isNaN(q.max_score);
  });
  if (bad) { err.textContent = '题目需填写 ID / 标题 / 维度（必须是已定义的维度key）/ 满分'; return; }
  err.textContent = '保存中…';
  try {
    const d = DT.editingId
      ? await API.templates.update(DT.editingId, b)
      : await API.templates.create(b);
    err.textContent = '';
    toast(DT.editingId ? ('已生成新版本 v' + (d.version || '')) : '模板已保存为草稿');
    closeTemplateForm();
    loadMyTemplates();
  } catch (e) {
    err.textContent = (e.code === 40002) ? '模板编码已存在：请改用「编辑」生成新版本' : (e.message || '保存失败');
  }
}

async function submitTemplateAudit(id) {
  try {
    await API.templates.submitAudit(id);
    toast('已提交审核，等待管理员处理');
    loadMyTemplates();
  } catch (e) { toast('提交失败：' + e.message); }
}

async function openTemplateVersions(id) {
  const body = document.getElementById('tplVerBody');
  document.getElementById('tplVerSub').textContent = '模板 #' + id + ' 的历史版本（原版本永久保留）';
  body.innerHTML = '加载中…';
  document.getElementById('tplVerModal').classList.add('open');
  try {
    const d = await API.templates.versions(id);
    const items = (d && d.items) || [];
    body.innerHTML = '<div class="table-wrap"><table class="table"><thead><tr><th>版本</th><th>名称</th><th>状态</th><th>创建时间</th></tr></thead><tbody>' +
      items.map(function (t) {
        return '<tr><td>v' + esc(t.version) + '</td><td>' + esc(t.name) + '</td>' +
          '<td><span class="badge ' + (TPL_STATUS_CLASS[t.status] || '') + '">' + esc(TPL_STATUS_TEXT[t.status] || t.status) + '</span></td>' +
          '<td>' + esc(t.created_at || '-') + '</td></tr>';
      }).join('') + '</tbody></table></div>';
  } catch (e) { body.innerHTML = '<div class="desc">加载失败：' + esc(e.message) + '</div>'; }
}
function closeTemplateVersions() { document.getElementById('tplVerModal').classList.remove('open'); }

/* ---------- 用户端：绑定营养师 ---------- */
function openBindDtn() {
  if (!requireLogin()) return;
  document.getElementById('bindErr').textContent = '';
  document.getElementById('bindCode').value = '';
  document.getElementById('bindDtnModal').classList.add('open');
}
function closeBindDtn() { document.getElementById('bindDtnModal').classList.remove('open'); }
async function saveBindDtn() {
  const err = document.getElementById('bindErr');
  const code = (document.getElementById('bindCode').value || '').trim();
  if (!code) { err.textContent = '请输入邀请码'; return; }
  err.textContent = '绑定中…';
  try {
    await API.nutritionists.bind(code);
    err.textContent = '';
    toast('绑定成功：该营养师可查看你的测评报告');
    closeBindDtn();
  } catch (e) { err.textContent = e.message || '绑定失败'; }
}

function toggleFullscreen() {
  const el = document.getElementById('screenCanvas');
  if (!document.fullscreenElement) {
    if (el.requestFullscreen) el.requestFullscreen();
    else toast('当前浏览器不支持全屏 API');
  } else {
    document.exitFullscreen();
  }
}

/* 知识库信息卡（右栏）：集合名/向量数/阈值由后端真实返回 */
async function loadKbInfo() {
  const el = document.getElementById('ctxKb');
  if (!el) return;
  if (!getToken()) { el.textContent = '登录后可见'; return; }
  try {
    const d = await API.chat.kbInfo();
    el.textContent = 'collection: ' + (d.collection || '—') +
      '｜知识块：' + (d.entities == null ? '—' : d.entities) +
      '｜检索阈值：' + (d.min_similarity == null ? '—' : d.min_similarity);
  } catch (e) {
    el.textContent = '知识库信息加载失败';
  }
}

/* ---------------- 初始化 ---------------- */
/* 问候语：按本地时间 + 当前登录用户（未登录显示「访客」，不再写死姓名） */
function greet() {
  const h = new Date().getHours();
  const word = h < 6 ? '凌晨好' : h < 12 ? '上午好' : h < 14 ? '午安' : h < 18 ? '下午好' : '晚上好';
  const who = (CURRENT_USER && (CURRENT_USER.real_name || CURRENT_USER.username)) || '访客';
  const el = document.getElementById('greeting');
  if (el) el.textContent = word + '，' + who;
}

/* ---------------- 首页 KPI（真实数据） ----------------
   GET /api/v1/dashboard/personal/overview →
     { health_index, level, risk_count, top_risks,
       checkin:{week_checked,week_total,week_rate,checked_today,streak},
       checkin_text, plan }
   ⚠️ 未登录 / 请求失败一律显示 —，绝不展示编造数值（合规红线）。
   ---------------------------------------------------------- */
function setText(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }

async function refreshHome() {
  greet();
  const blanks = ['kpiIndex', 'kpiCheckin', 'kpiRisk', 'kpiPlan',
    'kpiIndexSub', 'kpiCheckinSub', 'kpiRiskSub', 'kpiPlanSub', 'homeScore'];
  if (!getToken()) {
    blanks.forEach(i => setText(i, '—'));
    const ms = document.getElementById('homeMetrics');
    if (ms) ms.innerHTML = '<div class="desc">登录并完成一次测评后，这里显示各维度得分。</div>';
    const rs = document.getElementById('homeRisks');
    if (rs) rs.innerHTML = '<div class="desc">登录并完成一次测评后，这里按最新报告显示待关注维度。</div>';
    return;
  }
  try {
    const d = await API.dashboard.overview();
    const idx = d.health_index == null ? '—' : String(d.health_index);
    setText('kpiIndex', idx);
    setText('kpiCheckin', d.checkin_text
      || (d.checkin ? (d.checkin.week_checked + ' / ' + d.checkin.week_total) : '—'));
    setText('kpiRisk', d.risk_count == null ? '—' : String(d.risk_count));
    setText('kpiPlan', d.plan ? (d.plan.title || '进行中') : '暂无计划');
    setText('homeScore', idx);
    setText('kpiIndexSub', d.level ? ('最近等级：' + (LEVEL_TEXT[d.level] || d.level)) : '暂无测评报告');
    setText('kpiCheckinSub', (d.checkin && d.checkin.streak != null)
      ? ('连续 ' + d.checkin.streak + ' 天') : '—');
    setText('kpiRiskSub', (d.top_risks && d.top_risks.length) ? d.top_risks.join(' · ') : '暂无待关注维度');
    setText('kpiPlanSub', d.plan ? ('第 ' + (d.plan.week_no || 1) + ' 周') : '尚未生成');
  } catch (e) {
    blanks.forEach(i => setText(i, '—'));
  }
  loadHomeMetrics();
  loadHomeRisks();
}

/* 首页：各维度得分（真实测评报告维度） */
async function loadHomeMetrics() {
  const box = document.getElementById('homeMetrics');
  if (!box) return;
  if (!getToken()) { box.innerHTML = '<div class="desc">登录后可查看维度得分。</div>'; return; }
  try {
    const d = await API.dashboard.dimensions();
    const dims = (d && d.dimensions) || [];
    box.innerHTML = dims.length
      ? dims.slice(0, 4).map(function (x) {
        const s = Number(x.score) || 0;
        return '<div class="metric"><div class="metric-head"><span>' + esc(x.name || x.key) +
          '</span><b>' + esc(x.score) + '</b></div><div class="bar"><span style="width:' +
          Math.max(0, Math.min(100, s)) + '%"></span></div></div>';
      }).join('')
      : '<div class="desc">' + esc((d && d.note) || '暂无维度得分，完成一次测评后生成。') + '</div>';
  } catch (e) {
    box.innerHTML = '<div class="desc">' + esc(e.code === 10001 ? '登录后可查看维度得分。' : ('加载失败：' + e.message)) + '</div>';
  }
}

/* 首页：今日风险提醒（真实待关注维度） */
async function loadHomeRisks() {
  const box = document.getElementById('homeRisks');
  if (!box) return;
  if (!getToken()) { box.innerHTML = '<div class="desc">登录后可查看风险提醒。</div>'; return; }
  try {
    const r = await API.dashboard.risks();
    const items = (r && r.items) || [];
    box.innerHTML = items.length
      ? items.map(function (x) {
        const cls = x.level === 'high' ? 'red' : 'orange';
        const dot = x.level === 'high' ? 'high' : '';
        return '<div class="risk-item"><i class="risk-dot ' + dot + '"></i><div><b>' + esc(x.name) +
          '</b><div class="desc">' + esc(x.desc || ('该维度得分 ' + x.score)) + '</div></div>' +
          '<span class="badge ' + cls + '" style="margin-left:auto">' + esc(x.level_name) + '</span></div>';
      }).join('')
      : '<div class="desc">' + esc((r && r.note) || '暂无待关注维度') + '</div>';
  } catch (e) {
    box.innerHTML = '<div class="desc">' + esc(e.code === 10001 ? '登录后可查看风险提醒。' : ('加载失败：' + e.message)) + '</div>';
  }
}

/* 营养师智能体右栏：当前健康上下文（真实档案/风险） */
async function loadHealthContext() {
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = (v == null || v === '' ? '—' : v); };
  if (!getToken()) { set('ctxConstitution', '登录后可见'); set('ctxRisks', '登录后可见'); set('ctxAllergy', '登录后可见'); return; }
  try {
    const d = await API.users.healthContext();
    set('ctxConstitution', d.constitution || '暂无（生成食养计划后写入体质）');
    set('ctxRisks', (d.key_risks && d.key_risks.length) ? d.key_risks.join('、') : '暂无待关注风险');
    const al = d.allergy_labels ? ('过敏：' + d.allergy_labels) : '';
    const tb = d.taboo_labels ? ('忌口：' + d.taboo_labels) : '';
    set('ctxAllergy', (al || tb) ? [al, tb].filter(Boolean).join('；')
        : (d.allergies || d.diet_preference || '未填写（完成健康风险测评后自动写入）'));
    /* 身体数据：身高/体重/BMI（来自健康档案）+ 疾病史；缺数据如实显示"未填写" */
    const parts = [];
    if (d.height_cm) parts.push('身高 ' + d.height_cm + 'cm');
    if (d.weight_kg) parts.push('体重 ' + d.weight_kg + 'kg');
    if (d.bmi) parts.push('BMI ' + d.bmi);
    if (d.diet_preference) parts.push('偏好 ' + d.diet_preference);
    let bodyText = parts.length ? parts.join(' · ') : '未填写';
    if (d.diseases && d.diseases.length) bodyText += '；疾病史：' + d.diseases.join('、');
    set('ctxBody', parts.length || (d.diseases || []).length
        ? bodyText : '未填写（点左下「健康档案」补充）');
    /* AI 建议方向：后端按规则拼装，绝不前端编造 */
    set('ctxSuggest', (d.suggestions && d.suggestions.length) ? d.suggestions.join('；') : '暂无可给建议');
  } catch (e) {
    set('ctxConstitution', '—'); set('ctxRisks', '—'); set('ctxAllergy', '—');
  }
}

/* 营养师智能体左栏：咨询记录（真实咨询会话） */
async function loadContacts() {
  const box = document.getElementById('contactList');
  if (!box) return;
  if (!getToken()) { box.innerHTML = '<div class="desc" style="padding:6px 8px">登录后可查看咨询记录</div>'; return; }
  try {
    const d = await API.consultations.list({ limit: 10 });
    const items = (d && d.items) || [];
    box.innerHTML = items.length
      ? items.map(function (c) {
        const name = c.dietitian_name || c.dietitian_id || '营养师';
        return '<div class="contact"><div class="avatar-mini">' + esc(String(name).slice(0, 1)) +
          '</div><div><b>' + esc(name) + '</b><div class="desc">' +
          esc((c.status || '') + (c.created_at ? ' · ' + String(c.created_at).slice(0, 10) : '')) +
          '</div></div></div>';
      }).join('')
      : '<div class="desc" style="padding:6px 8px">暂无人工咨询记录</div>';
  } catch (e) {
    box.innerHTML = '<div class="desc" style="padding:6px 8px">' +
      esc(e.code === 10001 ? '登录后可查看咨询记录' : ('加载失败：' + e.message)) + '</div>';
  }
}

/* 未读通知数（顶栏红点）；页面无该元素或未登录时静默跳过 */
async function loadNotices() {
  const el = document.getElementById('noticeDot');
  if (!el || !getToken()) { if (el) el.style.display = 'none'; return; }
  try {
    const d = await API.notifications.list({ limit: 5 });
    const n = (d && d.unread) || 0;
    el.className = 'badge red';
    el.textContent = n > 0 ? String(n) : '';
    el.style.display = n > 0 ? '' : 'none';
  } catch (e) { /* 静默（含未登录） */ }
}

/* ---------------- 消息中心 ---------------- */
function openNotices() {
  if (!requireLogin()) return;
  document.getElementById('noticeModal').classList.add('open');
  renderNotices();
}
function closeNotices() {
  document.getElementById('noticeModal').classList.remove('open');
}
async function renderNotices() {
  const box = document.getElementById('noticeList');
  const sub = document.getElementById('noticeSub');
  box.innerHTML = '<div class="desc">加载中…</div>';
  try {
    const d = await API.notifications.list({ limit: 10 });
    const items = (d && d.items) || [];
    sub.textContent = '未读 ' + ((d && d.unread) || 0) + ' 条 · 共 ' + ((d && d.total) || 0) + ' 条';
    if (!items.length) {
      box.innerHTML = '<div class="desc">暂无消息。打卡提醒、报告生成完成等通知会出现在这里。</div>';
      return;
    }
    box.innerHTML = items.map(n =>
      '<div class="card" style="padding:10px 14px;margin-bottom:8px">' +
      '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px">' +
      '<b style="font-size:13px">' + esc(n.title || '通知') + '</b>' +
      (n.is_read ? '<span class="badge" style="flex-shrink:0">已读</span>'
                 : '<span class="badge red" style="flex-shrink:0">未读</span>') +
      '</div>' +
      '<div class="desc" style="margin-top:4px">' + esc(n.content || '') + '</div>' +
      '<div class="desc" style="margin-top:4px;font-size:11px">' + esc(n.created_at || '') + '</div>' +
      '</div>').join('');
  } catch (e) {
    box.innerHTML = '<div class="desc">' + esc(e.message) + '</div>';
  }
}
async function markAllRead() {
  try {
    await API.notifications.readAll();
    toast('已全部标记为已读');
    renderNotices();
    loadNotices();
  } catch (e) {
    toast('操作失败：' + e.message);
  }
}

/* ================= 食养食疗计划（L 组 + Q 组打卡） ================= */
const DIET = { period: 'week', current: null };
const MEAL_NAME = { breakfast: '早餐', lunch: '午餐', dinner: '晚餐', snack: '加餐' };

async function loadDiet() {
  if (!document.getElementById('dietRate')) return;
  // 1) 打卡统计
  try {
    const s = await API.checkins.stats();
    setText('dietRate', s.week_rate == null ? '—' : Math.round(s.week_rate * 100) + '%');
    setText('dietRateSub', (s.week_start || '') + ' ~ ' + (s.week_end || '') + '（分母固定 7 天）');
    setText('dietChecked', (s.week_checked || 0) + ' / ' + (s.week_total || 7));
    setText('dietStreak', String(s.streak || 0));
    setText('dietToday', s.checked_today ? '已打卡' : '未打卡');
  } catch (e) {
    ['dietRate', 'dietChecked', 'dietStreak', 'dietToday'].forEach(i => setText(i, '—'));
    setText('dietRateSub', e.code === 10001 ? '登录后可查看打卡统计' : e.message);
  }
  // 2) 计划
  await loadDietPlan();
}

async function loadDietPlan() {
  const box = document.getElementById('planDays');
  if (!box) return;
  setText('planMeta', '加载中…');
  try {
    const d = await API.plans.list({});
    const all = (d && d.items) || [];
    /* 后端列表接口不返回明细、也不支持 period 过滤 → 前端按所选周期筛选，再取详情拿每日食谱 */
    const items = all.filter(p => (p.period || 'week') === DIET.period);
    if (!items.length) {
      DIET.current = null;
      setText('planTitle', '食养计划');
      setText('planState', '未生成');
      setText('planMeta', '当前没有' + (DIET.period === 'month' ? '月' : '周') +
        '计划。食谱骨架由规则生成，方案总述由 AI 撰写（AI 不可用时自动降级为基础版）。');
      setText('planSummary', '—');
      setText('planDisclaimer', '');
      box.textContent = '暂无计划。';
      return;
    }
    const det = await API.plans.detail(items[0].plan_id);
    const p = Object.assign({}, items[0], det || {});
    DIET.current = p;
    setText('planTitle', p.title || '食养计划');
    setText('planState', p.state === 'complete' ? '已生成' : (p.state === 'basic' ? '基础版' : '生成中'));
    setText('planMeta', '周期：' + (p.period === 'month' ? '月' : '周') + ' · ' +
      ((p.days && p.days.length) || 0) + ' 天 · 生成于 ' + (p.generated_at || '—'));
    setText('planSummary', p.summary_text || '（该计划暂无总述）');
    setText('planDisclaimer', p.disclaimer || '');
    renderPlanDays(p.days || []);
  } catch (e) {
    setText('planMeta', e.code === 10001 ? '登录后可生成食养计划' : e.message);
    box.textContent = e.code === 10001 ? '请先登录。' : ('加载失败：' + e.message);
  }
}

function renderPlanDays(days) {
  const box = document.getElementById('planDays');
  if (!days.length) { box.textContent = '该计划暂无每日食谱。'; return; }
  const cell = m => m ? (esc(m.dish_name) + (m.efficacy ? '<div class="desc">' + esc(m.efficacy) + '</div>' : '')) : '—';
  box.innerHTML = '<div class="table-wrap"><table class="table"><thead><tr>' +
    '<th>天</th><th>早餐</th><th>午餐</th><th>晚餐</th></tr></thead><tbody>' +
    days.map(d => {
      const m = {};
      (d.meals || []).forEach(x => { m[x.meal_type] = x; });
      return '<tr><td>第 ' + d.day_no + ' 天</td><td>' + cell(m.breakfast) + '</td><td>' +
        cell(m.lunch) + '</td><td>' + cell(m.dinner) + '</td></tr>';
    }).join('') + '</tbody></table></div>';
}

function switchDietPeriod(p) {
  DIET.period = p;
  const w = document.getElementById('btnDietWeek');
  const m = document.getElementById('btnDietMonth');
  if (w) w.className = 'btn' + (p === 'week' ? ' primary' : '');
  if (m) m.className = 'btn' + (p === 'month' ? ' primary' : '');
  loadDietPlan();
}

async function generatePlan() {
  if (!requireLogin()) return;
  toast('正在生成' + (DIET.period === 'month' ? '月' : '周') + '计划，AI 撰写总述需要十几秒…');
  try {
    const d = await API.plans.create({ period: DIET.period });
    toast('已生成：' + (d.title || '食养方案'));
    await loadDietPlan();
  } catch (e) {
    toast('生成失败：' + e.message);
  }
}

async function checkinToday() {
  if (!requireLogin()) return;
  try {
    await API.checkins.create({ check_type: 'plan', plan_id: DIET.current ? DIET.current.plan_id : null });
    toast('今日打卡成功（同日重复打卡为幂等更新，不重复计数）');
    await loadDiet();
  } catch (e) {
    toast('打卡失败：' + e.message);
  }
}

/* 右栏「基于本次咨询生成食养计划」：跳转食养页并自动触发生成（食谱骨架规则生成，总述 AI 撰写） */
async function genPlanFromChat() {
  if (!requireLogin()) return;
  go('diet');
  // 2026-09-11：周计划 + 自动融合最近测评结果与本次咨询问题（后端负责场景匹配与忌口避让）
  if (DIET.period !== 'week') { switchDietPeriod('week'); }
  toast('正在按测评结果与本次咨询生成 7 天周计划（三餐轮换、避开过敏忌口）…');
  const q = (window.LAST_CHAT_QUESTION || '').slice(0, 200);
  try {
    const d = await API.plans.create(Object.assign({ period: 'week' }, q ? { question: q } : {}));
    toast('已生成：' + (d.title || '食养方案'));
  } catch (e) {
    toast('生成失败：' + e.message);
  }
  await loadDietPlan();
}

async function loadShoppingList() {
  if (!requireLogin()) return;
  const box = document.getElementById('shoppingList');
  if (!DIET.current) { box.textContent = '请先生成食养计划，再生成采购清单。'; return; }
  box.textContent = '加载中…';
  try {
    const d = await API.plans.shoppingList(DIET.current.plan_id);
    const items = (d && d.items) || [];
    if (!items.length) { box.textContent = '该计划暂无采购项。'; return; }
    const avoid = (d.avoid_foods || []);
    box.innerHTML = '<div class="desc">共 ' + (d.total || items.length) + ' 项' +
      (avoid.length ? '（已剔除忌口：' + esc(avoid.join('、')) + '）' : '') + '</div>' +
      items.map(i => '<span class="badge" style="margin:6px 6px 0 0">' + esc(i.name) +
        ' ×' + i.used_times + '</span>').join('');
  } catch (e) {
    box.textContent = '加载失败：' + e.message;
  }
}

/* ---------------- 健康档案（身高/体重/疾病史/饮食偏好） ---------------- */
const PF_DISEASES = ['高血压', '糖尿病', '高血脂', '高尿酸/痛风', '冠心病', '慢性肾病', '脂肪肝', '贫血', '甲亢', '甲减', '胃病'];

function openProfile() {
  if (!requireLogin()) return;
  const box = document.getElementById('pfDiseases');
  if (box && !box.childElementCount) {
    box.innerHTML = PF_DISEASES.map(function (n) {
      return '<button class="pf-chip" data-name="' + esc(n) + '" onclick="this.classList.toggle(\'active\')">' + esc(n) + '</button>';
    }).join('');
  }
  document.getElementById('pfErr').textContent = '';
  document.getElementById('profileModal').classList.add('open');
  loadProfileForm();
}

function closeProfile() { document.getElementById('profileModal').classList.remove('open'); }

async function loadProfileForm() {
  try {
    const d = await API.users.healthContext();
    const set = function (id, v) { const el = document.getElementById(id); if (el) el.value = (v == null ? '' : v); };
    set('pfHeight', d.height_cm);
    set('pfWeight', d.weight_kg);
    set('pfDiet', d.diet_preference);
    const known = (d.diseases || []);
    document.querySelectorAll('#pfDiseases .pf-chip').forEach(function (b) {
      b.classList.toggle('active', known.indexOf(b.dataset.name) >= 0);
    });
    const other = known.filter(function (x) { return PF_DISEASES.indexOf(x) < 0; });
    set('pfDiseaseOther', other.join('、'));
    calcBmi();
  } catch (e) {
    document.getElementById('pfErr').textContent = e.message || '读取档案失败';
  }
}

/* BMI 实时计算 + 中文分级（口径：WS/T 428 成人标准） */
function calcBmi() {
  const h = parseFloat((document.getElementById('pfHeight').value || '').trim());
  const w = parseFloat((document.getElementById('pfWeight').value || '').trim());
  const out = document.getElementById('pfBmi');
  const note = document.getElementById('pfBmiNote');
  if (!h || !w || h <= 0 || w <= 0) { out.textContent = '—'; note.textContent = ''; return; }
  const bmi = w / Math.pow(h / 100, 2);
  out.textContent = bmi.toFixed(1);
  note.textContent = bmi < 18.5 ? '偏瘦' : (bmi < 24 ? '正常' : (bmi < 28 ? '超重' : '肥胖'));
}

async function saveProfile() {
  const err = document.getElementById('pfErr');
  err.textContent = '';
  const hRaw = (document.getElementById('pfHeight').value || '').trim();
  const wRaw = (document.getElementById('pfWeight').value || '').trim();
  const h = hRaw ? parseInt(hRaw, 10) : null;
  const w = wRaw ? parseFloat(wRaw) : null;
  if (h !== null && (isNaN(h) || h < 50 || h > 250)) { err.textContent = '身高需在 50–250 cm 之间'; return; }
  if (w !== null && (isNaN(w) || w < 2 || w > 300)) { err.textContent = '体重需在 2–300 kg 之间'; return; }
  const diseases = [];
  document.querySelectorAll('#pfDiseases .pf-chip.active').forEach(function (b) { diseases.push(b.dataset.name); });
  const other = (document.getElementById('pfDiseaseOther').value || '').trim();
  if (other) { other.split(/[、,，;；\s]+/).filter(Boolean).forEach(function (x) { diseases.push(x); }); }
  // 合规 D1：疾病史属敏感个人信息，须单独同意（服务端同样硬校验，前端只是提前拦一层）
  const consentEl = document.getElementById('pfConsent');
  if (diseases.length && !(consentEl && consentEl.checked)) {
    err.textContent = '请先勾选单独同意《敏感个人信息处理规则》，再提交疾病史';
    return;
  }
  const payload = {
    diseases: diseases,
    diet_preference: (document.getElementById('pfDiet').value || '').trim()
  };
  if (h !== null) { payload.height_cm = h; }
  if (w !== null) { payload.weight_kg = w; }
  if (diseases.length) { payload.sensitive_consent = true; }
  err.textContent = '保存中…';
  try {
    await API.users.updateProfile(payload);
    err.textContent = '';
    toast('健康档案已保存');
    closeProfile();
    if (typeof loadHealthContext === 'function') loadHealthContext();
    if (typeof refreshHome === 'function') refreshHome();
  } catch (e) {
    err.textContent = e.message || '保存失败';
  }
}

/* ================= 五大健康场景（N 组） ================= */
let SCENES = [];
let SCENE_CURRENT = null;
/* 场景依赖的智能体（用于如实展示可用状态；未接入的能力不做伪装） */
const SCENE_AGENTS = {
  home_diet: ['dish', 'diet'],
  chronic: ['diet', 'steward'],
  banquet: ['dish'],
  maternal: ['assessment', 'dietitian'],
  travel: []
};

async function loadScenes() {
  const box = document.getElementById('sceneGrid');
  if (!box) return;
  box.innerHTML = '<div class="card desc">加载中…</div>';
  try {
    const d = await API.agents.scenes();
    SCENES = (d && d.items) || [];
    if (!SCENES.length) { box.innerHTML = '<div class="card desc">暂无场景配置。</div>'; return; }
    box.innerHTML = SCENES.map(s =>
      '<div class="card" style="cursor:pointer" onclick="showScene(\'' + esc(s.key) + '\')">' +
      '<h3>' + esc(s.name) + '</h3><div class="desc">' + esc(s.desc) + '</div></div>').join('');
  } catch (e) {
    box.innerHTML = '<div class="card desc">' + esc(e.code === 10001 ? '登录后可查看场景' : e.message) + '</div>';
  }
}

async function showScene(key) {
  const s = SCENES.filter(x => x.key === key)[0];
  if (!s) return;
  SCENE_CURRENT = s;
  setText('sceneDetailTitle', s.name);
  const keys = SCENE_AGENTS[key] || [];
  let body = esc(s.desc);
  if (!keys.length) {
    body += '\n\n该场景依赖第三方能力（如目的地数据与餐厅库），当前为留桩状态，暂不可用。';
    setText('sceneDetailBody', body);
  } else {
    body += '\n\n依赖智能体：';
    setText('sceneDetailBody', body);
    try {
      const d = await API.agents.list();
      const all = (d && d.items) || [];
      const rows = keys.map(k => {
        const a = all.filter(x => x.key === k)[0];
        if (!a) return '<div class="desc">· ' + esc(k) + '（未配置）</div>';
        return '<div class="desc">· ' + esc(a.name) + '：' + (a.enabled
          ? '<span class="badge green">可用</span>'
          : '<span class="badge">待接入</span> ' + esc(a.note || '')) + '</div>';
      }).join('');
      document.getElementById('sceneDetailBody').innerHTML =
        '<div>' + esc(s.desc) + '</div>' + rows;
    } catch (e) {
      document.getElementById('sceneDetailBody').innerHTML =
        '<div>' + esc(s.desc) + '</div><div class="desc">智能体状态加载失败：' + esc(e.message) + '</div>';
    }
  }
  const btn = document.getElementById('sceneEnterBtn');
  if (btn) { btn.style.display = ''; btn.textContent = '带着「' + s.name + '」去咨询'; }
}

function enterScene() {
  if (!SCENE_CURRENT) return;
  go('nutrition');
  const input = document.getElementById('chatInput');
  if (input) {
    input.value = '我想咨询「' + SCENE_CURRENT.name + '」相关问题：' + SCENE_CURRENT.desc;
    input.focus();
  }
}

/* ================= 智能体中心（主智能体 + 六个子智能体） ================= */
async function loadAgentsPanel() {
  const box = document.getElementById('primaryAgentBody');
  if (!box) return;
  box.innerHTML = '<div class="desc">加载中…</div>';
  try {
    const d = await API.agents.list();
    const items = (d && d.items) || [];
    const pk = (d && d.primary_key) || 'orchestrator';
    const primary = items.filter(a => a.key === pk)[0];
    const subs = items.filter(a => a.key !== pk);
    if (!primary) { box.innerHTML = '<div class="desc">目录里未配置主智能体。</div>'; return; }
    const chips = subs.map(a =>
      '<span class="badge' + (a.enabled ? ' green' : '') + '">' + esc(a.name) + '</span>').join(' ');
    box.innerHTML =
      '<div class="desc">' + esc(primary.desc) + '</div>' +
      '<div style="margin-top:10px">管辖子智能体（' + subs.length + ' 个）：' + chips + '</div>';
    setText('subAgentCount', subs.length + ' 个');
  } catch (e) {
    box.innerHTML = '<div class="desc">' +
      esc(e.code === 10001 ? '登录后可查看智能体目录' : e.message) + '</div>';
  }
}

async function runDispatch() {
  const input = document.getElementById('dispatchInput');
  const out = document.getElementById('dispatchResult');
  if (!input || !out) return;
  const text = (input.value || '').trim();
  if (!text) { out.innerHTML = '<div class="desc">请先输入一句需求。</div>'; return; }
  out.innerHTML = '<div class="desc">调度中…</div>';
  try {
    const d = await API.agents.dispatch(text);
    const plan = (d && d.plan) || [];
    if (!plan.length) { out.innerHTML = '<div class="desc">未生成调度方案。</div>'; return; }
    out.innerHTML =
      '<div class="desc">调度链路：' + esc(d.strategy || '') + '</div>' +
      plan.map(p =>
        '<div style="margin-top:6px"><span class="badge green">第 ' + p.order + ' 步</span> ' +
        '<b>' + esc(p.name) + '</b> — ' + esc(p.reason) +
        (p.matched_terms && p.matched_terms.length
          ? ' <span class="desc">（命中：' + esc(p.matched_terms.join('、')) + '）</span>' : '') +
        '</div>').join('') +
      '<div class="desc" style="margin-top:10px">' + esc(d.note || '') +
      (d.fallback ? '；本次为兜底调度' : '') + '</div>';
  } catch (e) {
    out.innerHTML = '<div class="desc">' +
      esc(e.code === 10001 ? '登录后可使用调度' : e.message) + '</div>';
  }
}

/* ================= 主智能体编排试跑（识别 → 规划 → 分派 → 汇总） ================= */
async function runOrchestrate(execute) {
  const input = document.getElementById('orchInput');
  const out = document.getElementById('orchResult');
  if (!input || !out) return;
  const text = (input.value || '').trim();
  if (!text) { out.innerHTML = '<div class="desc">请先输入一句需求。</div>'; return; }
  out.innerHTML = '<div class="desc">' + (execute ? '识别并分派执行中…' : '识别中…') + '</div>';
  try {
    const d = await API.agents.orchestrate(text, 3, !!execute);
    const it = (d && d.intent) || {};
    const plan = (d && d.plan) || [];
    const steps = (d && d.steps) || [];
    const res = (d && d.result) || {};
    let html = '';

    // 1) 意图识别
    html += '<div class="card" style="padding:12px">'
      + '<div><span class="badge green">① 意图识别</span> '
      + (it.blocked
        ? '<span class="badge">高危拦截</span> ' + esc(it.refuse_reason || '')
        : '<b>' + esc(it.primary || '—') + '</b> 置信度 '
          + (it.confidence == null ? '—' : Math.round(it.confidence * 100) + '%')
          + (it.fallback ? ' <span class="desc">（置信不足，已兜底）</span>' : ''))
      + '</div>'
      + '<div class="desc" style="margin-top:6px">' + esc(it.explain || '') + '</div>'
      + (it.baseline_hit && it.baseline_hit.length
        ? '<div class="desc">基线信号：' + esc(it.baseline_hit.join('、'))
          + '（只作为链路基线，不抢主意图）</div>' : '')
      + '<div class="desc" style="margin-top:4px">分类器：'
      + esc(it.classifier || '确定性规则分类器') + '</div></div>';

    // 2) 规划
    html += '<div class="card" style="padding:12px;margin-top:8px">'
      + '<div><span class="badge green">② 规划链路</span> '
      + (plan.length ? esc(d.strategy || '') : '高危问题未纳入任何链路') + '</div>'
      + plan.map(p => '<div class="desc" style="margin-top:6px">· <b>' + esc(p.name)
        + '</b>（' + esc(p.agent) + '）— ' + esc(p.reason || '') + '</div>').join('')
      + '</div>';

    // 3) 分派执行
    if (execute) {
      html += '<div class="card" style="padding:12px;margin-top:8px">'
        + '<div><span class="badge green">③ 分派执行</span> '
        + (steps.length ? steps.length + ' 个子智能体' : '未调用任何子智能体')
        + ' · 总耗时 ' + esc(d.elapsed_ms == null ? '—' : d.elapsed_ms + 'ms') + '</div>'
        + steps.map(s => '<div class="desc" style="margin-top:6px">· '
          + '<span class="badge' + (s.ok ? ' green' : '') + '">'
          + (s.ok ? '成功' : '失败') + '</span> <b>' + esc(s.agent) + '</b> '
          + esc(s.elapsed_ms + 'ms') + '<br>'
          + esc((s.summary || s.note || '').slice(0, 220)) + '</div>').join('')
        + '</div>';

      // 4) 汇总
      const refs = res.references || [];
      html += '<div class="card" style="padding:12px;margin-top:8px">'
        + '<div><span class="badge green">④ 结构化汇总</span> 成功 '
        + esc(res.ok_count || 0) + ' · 失败 ' + esc(res.failed_count || 0)
        + ' · 如实降级 ' + esc(res.degraded_count || 0) + '</div>'
        + '<div class="desc" style="margin-top:6px">' + esc(res.summary || '') + '</div>'
        + (refs.length
          ? '<div class="desc" style="margin-top:6px">引用来源 ' + refs.length + ' 条：'
            + refs.map(r => esc((r.title || r.source || ('doc#' + r.doc_id)).slice(0, 40)))
              .join('、') + '</div>'
          : '')
        + '<div class="desc" style="margin-top:6px">' + esc(res.citations_note || '') + '</div>'
        + '</div>';
    }

    out.innerHTML = html;
  } catch (e) {
    out.innerHTML = '<div class="desc">'
      + esc(e.code === 10001 ? '登录后可使用编排' : e.message) + '</div>';
  }
}

/* ================= 个人健康数据分析（I 组看板） ================= */
async function loadDataAnalysis() {
  await Promise.all([loadTrend(), loadDimsAndRisks(), loadInsight()]);
  await loadHistory();
}

async function loadTrend() {
  const chart = document.getElementById('trendChart');
  if (!chart) return;
  chart.innerHTML = '<div class="desc">加载中…</div>';
  try {
    const d = await API.dashboard.trend({});
    const pts = (d && d.points) || [];
    if (!pts.length) {
      chart.innerHTML = '<div class="desc">暂无趋势数据</div>';
      setText('trendMeta', (d && d.note) || '完成一次测评并生成报告后，这里会显示得分趋势。');
      renderTrendTable(d);            // 空态也要有表头骨架，结构固定不跳动
      return;
    }
    const max = Math.max.apply(null, pts.map(p => Number(p.standardized_score != null ? p.standardized_score : p.score) || 0)) || 1;
    // 跨模板时用标准化百分制作图，否则不同量表的分数画在一条轴上会误导
    const crossTemplate = new Set(pts.map(p => p.template_name)).size > 1;
    chart.innerHTML = pts.map(p => {
      const v = Number(crossTemplate ? (p.standardized_score != null ? p.standardized_score : p.score) : p.score) || 0;
      const tip = p.date + ' · ' + (p.template_name || '') +
        (p.template_version ? ' v' + p.template_version : '') +
        ' · 百分制 ' + (p.standardized_score == null ? '—' : p.standardized_score) +
        ' · 等级 ' + (p.level || '—');
      return '<i style="height:' + Math.max(4, Math.round(v / max * 100)) +
        '%" title="' + esc(tip) + '"></i>';
    }).join('');
    setText('trendMeta', '共 ' + pts.length + ' 次测评记录' +
      (crossTemplate ? '（跨模板，已统一按标准化百分制对比）' : '') +
      '（鼠标悬停查看单次得分）');
    renderTrendTable(d);
  } catch (e) {
    chart.innerHTML = '<div class="desc">' + esc(e.code === 10001 ? '请先登录' : e.message) + '</div>';
  }
}

/* 趋势的等价数据表：折线图对读屏软件不可读，这张表是同一份数据的无障碍替代 */
function renderTrendTable(d) {
  const box = document.getElementById('trendTable');
  if (!box) return;
  const tbl = d && d.table;
  const cols = (tbl && tbl.columns) || [
    { key: 'date', name: '测评日期' }, { key: 'template_name', name: '模板' },
    { key: 'template_version', name: '模板版本' }, { key: 'score', name: '总分' },
    { key: 'standardized_score', name: '标准化百分制' }, { key: 'level', name: '等级' }
  ];
  const rows = (tbl && tbl.rows) || [];
  let html = '<table class="table"><caption>测评得分趋势数据表</caption><thead><tr>';
  cols.forEach(c => { html += '<th scope="col">' + esc(c.name) + '</th>'; });
  html += '</tr></thead><tbody>';
  if (!rows.length) {
    html += '<tr><td colspan="' + cols.length + '" class="desc">暂无记录</td></tr>';
  } else {
    rows.forEach(r => {
      html += '<tr>';
      cols.forEach(c => {
        const v = r[c.key];
        html += '<td>' + esc(v === null || v === undefined || v === '' ? '—' : String(v)) + '</td>';
      });
      html += '</tr>';
    });
  }
  html += '</tbody></table>';
  const sum = (d && d.summary) || '';
  box.innerHTML = html + (sum ? '<div class="desc" style="margin-top:6px">' + esc(sum) + '</div>' : '');
}

async function loadDimsAndRisks() {
  const dimBox = document.getElementById('dimBox');
  const riskBox = document.getElementById('riskBox');
  // 维度
  try {
    const d = await API.dashboard.dimensions();
    const dims = (d && d.dimensions) || [];
    if (dimBox) {
      dimBox.innerHTML = dims.length
        ? dims.map(x => '<div class="desc" style="margin:4px 0">' + esc(x.name || x.key) +
          '：<b>' + esc(x.score) + '</b>' + (x.level_name ? ' <span class="badge">' + esc(x.level_name) + '</span>' : '') + '</div>').join('')
        : '<div class="desc">' + esc((d && d.note) || '暂无维度得分') + '</div>';
      // 文字版结论 + 等价表格（IT-D-07）：图之外的第二条读取路径
      const tbl = (d && d.table) || { columns: [], rows: [] };
      let html = '';
      if ((d && d.summary)) html += '<div class="desc" style="margin-top:8px">' + esc(d.summary) + '</div>';
      html += '<table class="table" style="margin-top:8px"><thead><tr>';
      (tbl.columns || []).forEach(c => { html += '<th scope="col">' + esc(c.name) + '</th>'; });
      html += '</tr></thead><tbody>';
      if (!(tbl.rows || []).length) {
        html += '<tr><td colspan="' + Math.max(1, (tbl.columns || []).length) + '" class="desc">暂无数据</td></tr>';
      } else {
        tbl.rows.forEach(r => {
          html += '<tr>';
          (tbl.columns || []).forEach(c => {
            const v = r[c.key];
            html += '<td>' + esc(v === null || v === undefined || v === '' ? '—' : String(v)) + '</td>';
          });
          html += '</tr>';
        });
      }
      html += '</tbody></table>';
      dimBox.innerHTML += html;
    }
  } catch (e) { if (dimBox) dimBox.innerHTML = '<div class="desc">' + esc(e.message) + '</div>'; }
  // 风险
  try {
    const r = await API.dashboard.risks();
    const items = (r && r.items) || [];
    if (riskBox) {
      riskBox.innerHTML = items.length
        ? items.map(x => '<div class="desc" style="margin:4px 0">' + esc(x.name) +
          '：<b>' + esc(x.score) + '</b> <span class="badge ' + (x.level === 'high' ? 'red' : 'orange') + '">' +
          esc(x.level_name) + '</span></div>').join('')
        : '<div class="desc">' + esc((r && r.note) || '暂无待关注风险') + '</div>';
    }
  } catch (e) { if (riskBox) riskBox.innerHTML = '<div class="desc">' + esc(e.message) + '</div>'; }
}

async function loadInsight() {
  const box = document.getElementById('insightText');
  if (!box) return;
  box.textContent = '加载中…';
  try {
    const d = await API.dashboard.insight();
    if (d && d.insight) {
      box.textContent = d.insight;
      setText('insightNote', d.disclaimer || '');
    } else {
      box.textContent = '—';
      setText('insightNote', (d && d.note) || '暂无解读内容。');
    }
  } catch (e) {
    box.textContent = '—';
    setText('insightNote', e.code === 10001 ? '登录后可查看智能解读' : e.message);
  }
}

/* ================= 运营数据大屏（J 组，需管理员权限） ================= */
async function loadOps() {
  const note = document.getElementById('opsNote');
  const ids = ['opsUsers', 'opsAssess', 'opsReports', 'opsChat'];
  try {
    const d = await API.admin.opsDashboard();
    setText('opsUsers', d.users == null ? '—' : String(d.users));
    setText('opsNewUsers', '今日新增 ' + (d.new_users_today == null ? '—' : d.new_users_today));
    setText('opsAssess', d.assessments == null ? '—' : String(d.assessments));
    setText('opsAssessDone', '已完成 ' + (d.completed_assessments == null ? '—' : d.completed_assessments));
    setText('opsReports', d.reports == null ? '—' : String(d.reports));
    setText('opsAvg', d.avg_score == null ? '—' : String(d.avg_score));
    setText('opsChat', d.chat_messages == null ? '—' : String(d.chat_messages));
    setText('opsDocs', d.knowledge_docs == null ? '—' : String(d.knowledge_docs));
    // 活跃与使用率（故事 4）
    setText('opsDau', d.dau == null ? '—' : String(d.dau));
    setText('opsMau', d.mau == null ? '—' : String(d.mau));
    setText('opsDauMeta', d.active_note || '—');
    setText('opsMauMeta', d.active_note || '—');
    setText('opsMonth', d.month_assessment == null ? '—' : String(d.month_assessment));
    setText('opsAiRatio', d.ai_ratio == null ? '—' : (d.ai_ratio * 100).toFixed(1) + '%');
    setText('opsAiRatioMeta', d.ai_ratio_note || '—');
    renderOpsTemplates(d.template_usage);
    // 趋势
    const chart = document.getElementById('opsTrend');
    const trend = d.assessment_trend_7d || [];
    if (chart) {
      const max = Math.max.apply(null, trend.map(x => Number(x.count) || 0).concat([1]));
      chart.innerHTML = trend.length
        ? trend.map(x => '<i style="height:' + Math.max(4, Math.round((Number(x.count) || 0) / max * 100)) +
          '%" title="' + esc(x.date + ' · ' + x.count) + '"></i>').join('')
        : '<div class="desc">暂无趋势数据</div>';
    }
    setText('opsTrendMeta', '近 7 日每日测评发起量（悬停查看）');
    // 等级分布
    const dist = d.level_distribution || {};
    const KEY_NAME = { excellent: '优秀', good: '良好', needs_improvement: '待改善', poor: '较差', unknown: '未知' };
    const lv = document.getElementById('opsLevels');
    if (lv) {
      const keys = Object.keys(dist);
      lv.innerHTML = keys.length
        ? keys.map(k => '<span class="badge" style="margin:4px 6px 0 0">' + esc(KEY_NAME[k] || k) +
          '：' + esc(dist[k]) + '</span>').join('')
        : '<span class="desc">暂无等级分布数据</span>';
    }
    if (note) note.textContent = '数据生成时间：' + (d.generated_at || '—') + (d.note ? ' · ' + d.note : '');
  } catch (e) {
    ids.forEach(i => setText(i, '—'));
    ['opsNewUsers', 'opsAssessDone', 'opsAvg', 'opsDocs', 'opsTrendMeta',
      'opsDau', 'opsMau', 'opsMonth', 'opsAiRatio', 'opsAiRatioMeta'].forEach(i => setText(i, '—'));
    const chart = document.getElementById('opsTrend');
    if (chart) chart.innerHTML = '<div class="desc">无数据</div>';
    const lv = document.getElementById('opsLevels');
    if (lv) lv.innerHTML = '<span class="desc">—</span>';
    const tp = document.getElementById('opsTemplates');
    if (tp) tp.innerHTML = '<span class="desc">—</span>';
    if (note) {
      note.textContent = e.code === 10005
        ? '当前账号不是管理员，运营大屏数据不可见。此处不显示任何编造数据 —— 如需查看请用管理员账号登录。'
        : ('加载失败：' + e.message);
    }
  }
}

/** 模板使用率：逐模板条状占比 + 汇总口径。无数据一律显示 —，不补零。 */
function renderOpsTemplates(usage) {
  const box = document.getElementById('opsTemplates');
  const meta = document.getElementById('opsTemplatesMeta');
  if (!box) return;
  if (!usage || !Array.isArray(usage.items) || !usage.items.length) {
    box.innerHTML = '<span class="desc">暂无模板数据</span>';
    if (meta) setText('opsTemplatesMeta', '—');
    return;
  }
  const total = Number(usage.total_records) || 0;
  box.innerHTML = usage.items.map(function (it) {
    const pct = total ? (Number(it.records) || 0) * 100 / total : 0;
    const st = it.status === 'published' ? '' : '（' + esc(it.status || '未发布') + '）';
    return '<div class="metric"><div class="metric-head"><span>' +
      esc(it.name || it.code || ('#' + it.template_id)) + st + '</span><span>' +
      (Number(it.records) || 0).toLocaleString() + '（' + pct.toFixed(1) + '%）</span></div>' +
      '<div class="bar"><span style="width:' + pct.toFixed(1) + '%"></span></div></div>';
  }).join('');
  if (meta) {
    setText('opsTemplatesMeta', '共 ' + (usage.template_count || 0) + ' 个模板，' +
      '已使用 ' + (usage.used_template_count || 0) + ' 个，' +
      '已发布 ' + (usage.published_template_count || 0) + ' 个，' +
      '累计发起 ' + total.toLocaleString() + ' 次测评（占比按有模板归属的记录计算）');
  }
}
function init() {
  greet();
  bindNav();
  loadTemplates();
  document.getElementById('chatInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') sendChat();
  });
  document.getElementById('queryInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') runQuery();
  });
  [['dhModal', closeDigitalHuman], ['setModal', closeSettings], ['noticeModal', closeNotices], ['profileModal', closeProfile],
   ['licenseModal', closeLicenseForm], ['cusReportModal', closeCustomerReports], ['tplModal', closeTemplateForm],
   ['tplVerModal', closeTemplateVersions], ['bindDtnModal', closeBindDtn]].forEach(([id, fn]) => {
    document.getElementById(id).addEventListener('click', e => {
      if (e.target.id === id) fn();
    });
  });
  /* 登录页：输入密码后回车直接登录（登录页不做"点背景关闭"） */
  document.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !document.getElementById('loginPage').classList.contains('hidden')) doLogin();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { closeDigitalHuman(); closeSettings(); closeProfile(); closeLicenseForm();
      closeCustomerReports(); closeTemplateForm(); closeTemplateVersions(); closeBindDtn(); }
  });
  loadKbInfo();
  initLoginLeaves();
  restoreRemembered();
  /* 以文件方式打开（file://）时给出明确提示 */
  if (location.protocol === 'file:') {
    const w = document.getElementById('lpFileWarn');
    if (w) w.style.display = '';
  }
  /* 登录页常驻：每 20s 复检后端是否在线 */
  setInterval(function () {
    const lp = document.getElementById('loginPage');
    if (lp && !lp.classList.contains('hidden')) checkHealth();
  }, 20000);
  checkHealth();
  refreshUser().then(function () { refreshHome(); loadNotices(); });
}

document.addEventListener('DOMContentLoaded', init);
