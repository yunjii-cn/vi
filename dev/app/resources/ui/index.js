// ═══ 微信登录门控 (2026-06-08) ═══
// 流程(基于 VI v3 教程):
//   1. 有 ?yunji_user= 参数 → 解析 base64 → 已登录,显示用户信息 + 正常使用
//   2. 没有参数 → 调 islogin.php 检查 cookie → 已登录就构造 yunji_user 复用
//   3. 都没登录 → 跳 vi.yunjii.cn/sl/login.php?next=本机地址,扫码后跳回
//
// 关键点:启动器(PyQt6 EXE)完全不参与登录,只负责开端口
//         所有登录逻辑在前端 JS 里完成,UI 都在浏览器
(function() {
    'use strict';
    const params = new URLSearchParams(location.search);
    const userB64 = params.get('yunji_user');
    let yunjiUser = null;

    // 解析 yunji_user 参数(用 TextDecoder 处理 UTF-8,避免中文乱码)
    function parseUserB64(b64) {
        try {
            // ★ 2026-06-08: 修复中文昵称乱码(atob 不自动转 UTF-8)
            const cleaned = b64.replace(/ /g, '+');  // base64 url-safe 兼容
            const bytes = Uint8Array.from(atob(cleaned), c => c.charCodeAt(0));
            const json = new TextDecoder('utf-8').decode(bytes);
            const u = JSON.parse(json);
            return (u && typeof u === 'object') ? u : null;
        } catch (e) {
            console.warn('[yunji] yunji_user 参数解析失败:', e);
            return null;
        }
    }

    // 构造 yunji_user base64 + 跳到 next 页面
    function gotoNext(userObj) {
        try {
            // ★ 2026-06-16 v4 修复: 用 sessionStorage 传 user,不再做整页重定向
            //   旧逻辑: location.href 跳到 "?yunji_user=base64" → 浏览器重新加载所有资源
            //           (虽然有 cache 也要重 parse+execute 385KB JS) → 多花 300-1000ms
            //   新逻辑: 写 sessionStorage(本 tab 共享,刷新就清),直接调 onLoggedIn
            //           视觉效果: 0ms 切换,跟用户没有"刷一下"的感觉
            //   兜底: 如果 sessionStorage 不可用,再走老路径(给极少数老浏览器)
            const enc = btoa(unescape(encodeURIComponent(JSON.stringify(userObj))));
            try {
                sessionStorage.setItem('__yunji_user', enc);
                onLoggedIn(userObj);
                return;
            } catch (e) {
                // sessionStorage 写失败(隐私模式 / 配额满),降级到 URL 跳转
                console.warn('[yunji] sessionStorage 不可用,降级到 URL 跳转:', e);
            }
            const next = `${location.origin}${location.pathname}`;
            location.href = `${next}?yunji_user=${encodeURIComponent(enc)}`;
        } catch (e) {
            console.error('[yunji] 构造跳转 URL 失败:', e);
            redirectToLogin();
        }
    }

    // 跳到 vi 官方登录页
    function redirectToLogin() {
        const next = `${location.origin}${location.pathname}`;
        location.href = `https://vi.yunjii.cn/sl/login.php?next=${encodeURIComponent(next)}`;
    }

    // 走 islogin.php 检查服务端 cookie 是否已登录
    // ★ 2026-06-16 v4 修复: 超时从 5s 缩到 1.5s
    //   旧逻辑: vi.yunjii.cn 不通时用户卡黑屏 5 秒才出回退按钮
    //   局域网/本机访问 1.5s 内必然有响应(成功 or 失败),真不通就立刻降级
    //   - 1.5s 真超时(AbortError)→ 返回 'TIMEOUT' → 显示回退按钮
    //   - CORS 拦截/网络错误(TypeError)→ 返回 null → 走 redirectToLogin 跳 vi 网站
    //     (vi 的 CORS 是 * + 无 Allow-Credentials,credentials:include 必失败,
    //      但这只是没法跨域读 cookie,跳到 vi 网站登录是正常的)
    async function checkIsLogin() {
        const ctl = new AbortController();
        const tid = setTimeout(() => ctl.abort(), 1500);
        try {
            const r = await fetch('https://vi.yunjii.cn/sl/islogin.php', {
                credentials: 'include',
                signal: ctl.signal,
            });
            clearTimeout(tid);
            const data = await r.json();
            if (data && data.code === 1 && data.data) {
                return data.data;  // {nickname, avatar, openid, username, token, site}
            }
            return null;
        } catch (e) {
            clearTimeout(tid);
            if (e && (e.name === 'AbortError' || String(e).includes('abort'))) {
                console.warn('[yunji] islogin.php 1.5s 超时 (网络真不通)');
                return 'TIMEOUT';
            }
            // TypeError = CORS 拦截 / DNS 失败 / 离线 等
            console.warn('[yunji] islogin.php 跨域/网络错误,降级走登录页:', e && e.message || e);
            return null;  // 走 redirectToLogin → 跳 vi 网站看二维码
        }
    }

    // ★ 2026-06-16: 登录超时回退 - 显示"跳过登录"按钮让用户进入本地模式
    function showVeilFallback() {
        const fb = document.getElementById('yunji-veil-fallback');
        if (fb) fb.style.display = 'flex';
        const sub = document.querySelector('#yunji-login-veil .veil-sub');
        if (sub) sub.textContent = '登录服务无响应,可选择重试或进入本地模式';
    }

    function hideVeilFallback() {
        const fb = document.getElementById('yunji-veil-fallback');
        if (fb) fb.style.display = 'none';
        const sub = document.querySelector('#yunji-login-veil .veil-sub');
        if (sub) sub.textContent = '正在准备登录环境…';
    }

    function buildLocalUser() {
        // 本地模式: 用浏览器时区+时间做唯一 id,避免多个本地用户混淆
        const d = new Date();
        const pad = n => String(n).padStart(2, '0');
        const localId = 'local-' + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate())
                       + '-' + pad(d.getHours()) + pad(d.getMinutes());
        return {
            nickname: '本地用户',
            username: localId,
            openid: localId,
            token: 'local-mode',
            avatar: '',
            site: 'local',
        };
    }

    // 注入已登录用户信息框(整体一个下拉按钮: 头像+昵称+在线)
    function injectUserInfoBox(user) {
        const box = document.getElementById('user-info-box');
        if (!box) return;
        if (box.dataset.injected === '1') return;  // 防止重复注入
        box.dataset.injected = '1';

        const nick = user.nickname || user.username || '已登录';
        const av = user.avatar || '';
        const initial = (nick[0] || '?').toUpperCase();
        const avMini = av
            ? `<img src="${av.replace(/"/g, '&quot;')}" style="width:22px;height:22px;border-radius:50%;object-fit:cover;display:block;" onerror="this.outerHTML='<div style=&quot;width:22px;height:22px;border-radius:50%;background:linear-gradient(135deg,#3b82f6,#8b5cf6);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#fff&quot;>${initial}</div>'">`
            : `<div style="width:22px;height:22px;border-radius:50%;background:linear-gradient(135deg,#3b82f6,#8b5cf6);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#fff;flex-shrink:0;">${initial}</div>`;

        // 保留 sys-indicator(系统状态小圆点,移到按钮里跟"在线"合并)
        const indicator = box.querySelector('#sys-indicator');
        box.innerHTML = '';
        if (indicator) indicator.remove();  // 不再用外置小圆点,合并进"在线"标识

        // 整体下拉按钮
        const trigger = document.createElement('button');
        trigger.id = 'user-info-trigger';
        trigger.type = 'button';
        trigger.title = `${nick}  ·  点击查看账户信息`;  // hover 显示完整昵称(截断时不丢信息)
        trigger.style.cssText = [
            'display:inline-flex',
            'align-items:center',
            'gap:6px',
            'padding:3px 8px 3px 3px',
            'border-radius:999px',
            'background:rgba(255,255,255,.05)',
            'border:1px solid rgba(255,255,255,.12)',
            'cursor:pointer',
            'color:#e5e7eb',
            'font-family:inherit',
            'font-size:12px',
            'line-height:1.2',
            'transition:background .15s,border-color .15s',
            'max-width:200px',     // ★ 2026-06-08: 算到极致 — 460(侧栏) - 48(左右内边距) - 188(调试按钮组) - 20(间距) ≈ 204px,留 4px 缓冲
            'flex-shrink:0'
        ].join(';');
        trigger.innerHTML =
            avMini +
            `<span style="font-weight:600;max-width:108px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(nick)}">${escapeHtml(nick)}</span>` +
            `<span style="display:inline-flex;align-items:center;gap:3px;padding:1px 5px;border-radius:999px;background:rgba(34,197,94,.14);color:#4ade80;font-size:9px;font-weight:600;line-height:1.3;flex-shrink:0;">` +
                `<span style="width:5px;height:5px;border-radius:50%;background:#22c55e;box-shadow:0 0 4px #22c55e;"></span>在线` +
            `</span>` +
            `<span style="opacity:.5;font-size:9px;margin-left:1px;flex-shrink:0;">▾</span>`;
        // hover 效果
        trigger.addEventListener('mouseenter', () => {
            trigger.style.background = 'rgba(255,255,255,.1)';
            trigger.style.borderColor = 'rgba(255,255,255,.2)';
        });
        trigger.addEventListener('mouseleave', () => {
            if (panel.style.display !== 'block') {  // 下拉打开时不重置
                trigger.style.background = 'rgba(255,255,255,.05)';
                trigger.style.borderColor = 'rgba(255,255,255,.12)';
            }
        });
        box.appendChild(trigger);

        // 创建下拉面板(只创建一次)
        let panel = document.getElementById('user-info-panel');
        if (!panel) {
            panel = document.createElement('div');
            panel.id = 'user-info-panel';
            panel.style.cssText = 'display:none;position:absolute;z-index:99999;min-width:300px;background:#111111;backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.12);border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,.5);padding:0;overflow:hidden;color:#e5e7eb;font-family:system-ui,-apple-system,sans-serif;';
            document.body.appendChild(panel);
        }

        // 切换显示
        const toggle = (e) => {
            e.stopPropagation();
            const visible = panel.style.display === 'block';
            if (visible) {
                closePanel();
            } else {
                renderUserPanel(panel, user);
                positionPanel(panel, trigger);
                panel.style.display = 'block';
                trigger.style.background = 'rgba(255,255,255,.1)';
                trigger.style.borderColor = 'rgba(34,197,94,.4)';
            }
        };
        trigger.addEventListener('click', toggle);

        function closePanel() {
            panel.style.display = 'none';
            trigger.style.background = 'rgba(255,255,255,.05)';
            trigger.style.borderColor = 'rgba(255,255,255,.12)';
        }

        // 点击空白处关闭
        setTimeout(() => {
            document.addEventListener('click', (e) => {
                if (!panel.contains(e.target) && !trigger.contains(e.target)) {
                    closePanel();
                }
            });
            // ESC 关闭
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && panel.style.display === 'block') closePanel();
            });
            // 滚动/窗口大小变化时关闭
            window.addEventListener('scroll', closePanel, true);
            window.addEventListener('resize', closePanel);
        }, 0);
    }

    // 定位下拉面板
    function positionPanel(panel, trigger) {
        const rect = trigger.getBoundingClientRect();
        let top = rect.bottom + 6 + window.scrollY;
        let left = rect.left + window.scrollX;
        const panelW = 300;
        // 防止右侧超出
        if (left + panelW > window.innerWidth - 8) {
            left = Math.max(8, window.innerWidth - panelW - 8 + window.scrollX);
        }
        panel.style.top = top + 'px';
        panel.style.left = left + 'px';
    }

    // 渲染用户信息下拉面板
    function renderUserPanel(panel, user) {
        const nick = user.nickname || user.username || '已登录';
        const av = user.avatar || '';
        const initial = (nick[0] || '?').toUpperCase();
        const avBig = av
            ? `<img src="${av.replace(/"/g, '&quot;')}" style="width:56px;height:56px;border-radius:50%;object-fit:cover;border:2px solid rgba(255,255,255,.2);" onerror="this.outerHTML='<div style=&quot;width:56px;height:56px;border-radius:50%;background:linear-gradient(135deg,#3b82f6,#8b5cf6);display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:700;color:#fff&quot;>${initial}</div>'">`
            : `<div style="width:56px;height:56px;border-radius:50%;background:linear-gradient(135deg,#3b82f6,#8b5cf6);display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:700;color:#fff;">${initial}</div>`;

        // 格式化时间戳
        let loginAtStr = '—';
        if (user.login_at) {
            try {
                const d = new Date(user.login_at * 1000);
                loginAtStr = d.toLocaleString('zh-CN', { hour12: false });
            } catch (_) {}
        }
        // 简化 openid(中间打码)
        const openidShort = user.openid ? (user.openid.substring(0, 6) + '…' + user.openid.substring(user.openid.length - 4)) : '—';
        const siteLabel = user.site === '1' ? '主站' : (user.site || '—');
        const tokenShort = user.token ? (user.token.substring(0, 8) + '…' + user.token.substring(user.token.length - 6)) : '—';

        const rowStyle = 'display:flex;justify-content:space-between;align-items:center;padding:6px 0;font-size:12px;';
        const kStyle = 'color:#94a3b8;';
        const vStyle = 'color:#e5e7eb;font-family:ui-monospace,Consolas,monospace;font-size:11px;';

        panel.innerHTML = `
            <div style="padding:16px;border-bottom:1px solid rgba(255,255,255,.08);display:flex;align-items:center;gap:12px;">
                ${avBig}
                <div style="flex:1;min-width:0;">
                    <div style="font-size:15px;font-weight:700;color:#f1f5f9;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(nick)}</div>
                    <div style="font-size:11px;color:#94a3b8;margin-top:2px;display:flex;align-items:center;gap:4px;">
                        <span style="width:6px;height:6px;border-radius:50%;background:#22c55e;box-shadow:0 0 6px #22c55e;"></span>
                        <span>已登录 · ${escapeHtml(siteLabel)}</span>
                    </div>
                </div>
            </div>
            <div style="padding:12px 16px;">
                <div style="${rowStyle}">
                    <span style="${kStyle}">用户名</span>
                    <span style="${vStyle}" title="${escapeHtml(user.username || '')}">${escapeHtml(user.username || '—')}</span>
                </div>
                <div style="${rowStyle}">
                    <span style="${kStyle}">OpenID</span>
                    <span style="${vStyle}" title="${escapeHtml(user.openid || '')}">${escapeHtml(openidShort)}</span>
                </div>
                <div style="${rowStyle}">
                    <span style="${kStyle}">登录时间</span>
                    <span style="${vStyle}">${escapeHtml(loginAtStr)}</span>
                </div>
                <div style="${rowStyle}">
                    <span style="${kStyle}">Token</span>
                    <span style="${vStyle}" title="${escapeHtml(user.token || '')}">${escapeHtml(tokenShort)}</span>
                </div>
            </div>
            <div style="border-top:1px solid rgba(255,255,255,.08);">
                <button id="user-panel-switch" type="button" style="width:100%;text-align:left;padding:10px 16px;background:transparent;border:none;color:#e5e7eb;font-size:13px;cursor:pointer;display:flex;align-items:center;gap:8px;" onmouseover="this.style.background='rgba(255,255,255,.05)'" onmouseout="this.style.background='transparent'">
                    <span>🔄</span><span>切换账号</span>
                </button>
                <button id="user-panel-logout" type="button" style="width:100%;text-align:left;padding:10px 16px;background:transparent;border:none;color:#f87171;font-size:13px;cursor:pointer;display:flex;align-items:center;gap:8px;" onmouseover="this.style.background='rgba(239,68,68,.1)'" onmouseout="this.style.background='transparent'">
                    <span>⏏</span><span>退出登录</span>
                </button>
            </div>
        `;

        // 绑定按钮事件
        panel.querySelector('#user-panel-switch').onclick = () => {
            if (confirm('确定要切换账号吗?将退出当前登录并跳到登录页。')) {
                const next = `${location.origin}${location.pathname}`;
                location.href = `https://vi.yunjii.cn/sl/logout.php?next=${encodeURIComponent('https://vi.yunjii.cn/sl/login.php?next=' + next)}`;
            }
        };
        panel.querySelector('#user-panel-logout').onclick = window.yunjiLogout;
    }

    // HTML 转义辅助
    function escapeHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    // 退出登录(给 injectUserInfoBox 暴露成 window.yunjiLogout)
function yunjiLogout() {
        if (!confirm('确定要退出登录吗?退出后需要重新扫码。')) return;
        // ★ 2026-06-16 v5: 主动清 localStorage,否则 index.html 的内联脱遮罩脚本会一直命中
        //   用户登出后,F5 仍看到内容,会困惑"我已经登出为什么还显示?"
        try { localStorage.removeItem('__yunji_user_lc'); } catch (_) {}
        try { sessionStorage.removeItem('__yunji_user'); } catch (_) {}
        const next = `${location.origin}${location.pathname}`;
        // 用 vi 的 logout.php 清 cookie,带 next 跳回登录页
        location.href = `https://vi.yunjii.cn/sl/logout.php?next=${encodeURIComponent(next)}`;

    }
    window.yunjiLogout = yunjiLogout;

    // 隐藏登录遮罩(已登录时调用)
    function hideVeil() {
        const veil = document.getElementById('yunji-login-veil');
        if (!veil) return;
        veil.classList.add('hide');
        // 淡出后从 DOM 移除(避免遮挡其他弹窗)
        setTimeout(() => { try { veil.remove(); } catch (_) {} }, 500);
    }

    // 跳到 vi 官方登录页(带遮罩保持显示,让跳转更顺滑)
    function redirectToLogin() {
        const next = `${location.origin}${location.pathname}`;
        // 遮罩文案改为"正在跳转登录页…"
        const veil = document.getElementById('yunji-login-veil');
        if (veil) {
            const sub = veil.querySelector('.veil-sub');
            if (sub) sub.textContent = '正在跳转登录页…';
        }
        // 短延迟让用户看到文案变化
        setTimeout(() => {
            location.href = `https://vi.yunjii.cn/sl/login.php?next=${encodeURIComponent(next)}`;
        }, 150);
    }

    // 已登录:暴露全局变量 + 注入用户信息框 + 移除遮罩
    function onLoggedIn(user) {
        window.__yunjiUser = user;
        try { history.replaceState(null, '', location.pathname); } catch (_) {}
        // ★ 2026-06-16 v5 提速: 写到 localStorage,让 F5 后的 inline 脚本瞬间脱遮罩
        //   sessionStorage 只活当前 tab,F5 不清;但配合 index.html 的内联 <script> + CSS
        //   才能在 index.js(363KB) 还没 parse 完时就让用户看到内容
        try {
            const enc = btoa(unescape(encodeURIComponent(JSON.stringify(user))));
            localStorage.setItem('__yunji_user_lc', enc);
            sessionStorage.setItem('__yunji_user', enc);
        } catch (_) { /* 隐私模式也不影响,降级到原本的 URL/远程校验 */ }
        hideVeil();  // 已登录:立即淡出遮罩
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => injectUserInfoBox(user));
        } else {
            injectUserInfoBox(user);
        }
        // ★ 2026-06-16 v4 修复: 不再在登录后做"全局历史预热 + 视频预下载"
        //   旧逻辑会做两件糟糕的事:
        //     1) 又拉一遍 /api/system/history?page=1 (fetchHistory 反正会拉,白调一次)
        //     2) 立刻后台预下载 5 个完整视频文件(各 5-50MB)
        //        → 抢光浏览器 6 个并发连接名额,导致后续缩略图全部卡死,首页"转圈 1 分钟"
        //   新逻辑: 什么都不做,等用户切到"我的作品" tab,fetchHistory 会自然加载
        //   视频预下载已经由 IntersectionObserver 接管(进视口才下载,自然不会和缩略图抢带宽)
    }

    // 异步门控主流程
    (async function gate() {
        // 0) ★ 2026-06-16 v4 提速: 先看 sessionStorage,避免无谓的 checkIsLogin
        //    上次已登录过,user 被存到 sessionStorage(F5 不清),直接复用
        //    旧逻辑: 每次 F5 都 fetch vi.yunjii.cn → 200ms-1.5s 网络等待
        //    新逻辑: 本地 0ms 取,跳过整轮 islogin
        try {
            const cached = sessionStorage.getItem('__yunji_user');
            if (cached) {
                const u = parseUserB64(cached);
                if (u) { onLoggedIn(u); return; }
            }
        } catch (_) { /* 隐私模式/不可用,降级到下面 */ }

        // 1) 优先用 URL 上的 yunji_user 参数
        if (userB64) {
            const u = parseUserB64(userB64);
            if (u) {
                onLoggedIn(u);
                return;
            }
        }

        // 2) 没参数 → 检查服务端 cookie(浏览器可能在 vi.yunjii.cn 已登录)
        //    ★ 2026-06-16: 返回 'TIMEOUT' 时不跳官方登录,直接显示回退面板
        const serverUser = await checkIsLogin();
        if (serverUser && serverUser !== 'TIMEOUT') {
            gotoNext(serverUser);
            return;
        }
        if (serverUser === 'TIMEOUT') {
            // 网络挂了/超时,显示回退面板让用户选择
            showVeilFallback();
            const skipBtn = document.getElementById('yunji-veil-skip');
            const retryBtn = document.getElementById('yunji-veil-retry');
            const enterLocal = () => {
                hideVeilFallback();
                onLoggedIn(buildLocalUser());
            };
            if (skipBtn) skipBtn.addEventListener('click', enterLocal, { once: true });
            if (retryBtn) retryBtn.addEventListener('click', () => {
                hideVeilFallback();
                gate();  // 重新走流程
            }, { once: true });
            return;
        }

        // 3) 都没登录 → 跳官方登录页
        redirectToLogin();
    })();
})();

// ─── Resizable panel drag logic ───────────────────────────────────────────────
(function() {
    const handle = document.getElementById('resize-handle');
    const viewer = document.getElementById('viewer-section');
    const library = document.getElementById('library-section');
    const workspace = document.querySelector('.workspace');
    if (!handle || !viewer || !library || !workspace) return;
    let dragging = false, startY = 0, startVH = 0;

    handle.addEventListener('mousedown', (e) => {
        dragging = true;
        startY = e.clientY;
        startVH = viewer.getBoundingClientRect().height;
        document.body.style.cursor = 'row-resize';
        document.body.style.userSelect = 'none';
        handle.querySelector('div').style.background = 'var(--accent)';
        e.preventDefault();
    });
    document.addEventListener('mousemove', (e) => {
        if (!dragging) return;
        const wsH = workspace.getBoundingClientRect().height;
        const delta = e.clientY - startY;
        let newVH = startVH + delta;
        // Clamp: viewer min 150px, library min 100px
        newVH = Math.max(150, Math.min(wsH - 100 - 5, newVH));
        viewer.style.flex = 'none';
        viewer.style.height = newVH + 'px';
        library.style.flex = '1';
    });
    document.addEventListener('mouseup', () => {
        if (dragging) {
            dragging = false;
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            handle.querySelector('div').style.background = 'var(--border)';
        }
    });
    // Hover highlight
    handle.addEventListener('mouseenter', () => { handle.querySelector('div').style.background = 'var(--text-dim)'; });
    handle.addEventListener('mouseleave', () => { if (!dragging) handle.querySelector('div').style.background = 'var(--border)'; });
})();
// ──────────────────────────────────────────────────────────────────────────────






// 动态获取当前访问的域名或 IP，API 请求通过前端服务代理转发
    const BASE = `http://${window.location.hostname}:${window.location.port}`;

    function _t(k) {
        return typeof window.t === 'function' ? window.t(k) : k;
    }

    function _fmt(key, vars) {
        let text = _t(key);
        Object.entries(vars || {}).forEach(([name, value]) => {
            text = text.replaceAll(`{${name}}`, String(value));
        });
        return text;
    }
    
    let currentMode = 'image';
    let pollInterval = null;
    let availableLoras = [];
    let player = null;
    let audioPlayer = null;
    const MODEL_CHECKPOINT_STORAGE_KEY = 'ltx_selected_model_path';
    const VIDEO_SUB_MODES = ['i2v', 'batch', 'motion'];
    let _videoSubMode = 'i2v';
    const TTS_SUB_MODES = ['text_only', 'clone', 'ultimate_clone', 'asr'];
    let _ttsSubMode = 'text_only';
    const UPSCALE_SUB_MODES = ['upscale_video', 'upscale_image'];
    let _upscaleSubMode = 'upscale_video';
    let _upscaleAutoTileSize = 0;

    // ★ 2026-06-16 修复 TDZ: 下列声明必须在 loadAllSettings() 之前完成。
    //   原因:loadAllSettings() (L2929) 在模块加载时同步执行,内部会调用
    //        switchMode() 触发 _refreshModelRequirement(),以及给 upscale-ratio /
    //        upscale-quality 等 select 派发 change 事件,同步触发
    //        onUpscaleRatioChange() → 直接访问下面三个 const/let。
    //        原代码把它们声明在 L3399/L3869/L3878,运行时处于 TDZ → ReferenceError。
    let _modelReqCheckToken = 0;  // 防止竞态
    const UPSCALE_RATIO_PRESETS = {
        '16:9': { rw: 16, rh: 9 },
        '9:16': { rw: 9, rh: 16 },
        '21:9': { rw: 21, rh: 9 },
        '3:2': { rw: 3, rh: 2 },
        '4:3': { rw: 4, rh: 3 },
        '1:1': { rw: 1, rh: 1 },
    };
    const UPSCALE_QUALITY_PRESETS = {
        '720p': { shortSide: 720 },
        '1080p': { shortSide: 1080 },
        '2k': { shortSide: 1440 },
        '4k': { shortSide: 2160 },
    };

    const SETTINGS_KEY = 'yunji_ui_settings';
    const PERSIST_SELECTS = [
        'gpu-selector', 'model-checkpoint-select', 'vid-quality', 'vid-ratio',
        'vid-motion', 'vid-model-select', 'motion-conditioning-type', 'motion-quality', 'img-res-preset',
        'batch-quality', 'batch-ratio', 'upscale-engine', 'upscale-scale', 'upscale-model', 'upscale-ratio', 'upscale-quality'
    ];
    const PERSIST_INPUTS = [
        'vram-limit-input', 'lora-dir-input', 'seed-value',
        'vid-custom-w', 'vid-custom-h', 'vid-duration', 'vid-steps', 'vid-fps',
        'img-w', 'img-h', 'img-steps',
        'batch-custom-w', 'batch-custom-h',
        'tts-cfg', 'tts-steps', 'global-out-dir',
        'start-frame-path', 'end-frame-path', 'uploaded-audio-path',
        'motion-video-path', 'motion-image-path', 'motion-duration', 'motion-fps', 'motion-seed', 'batch-background-audio-path',
        'upscale-video-path', 'upscale-image-path'
    ];
    const PERSIST_TEXTAREAS = ['prompt', 'tts-text'];
    const PERSIST_CHECKBOXES = ['vid-audio'];
    const PERSIST_RADIOS = [
        { name: 'seed-mode', values: ['random', 'fixed'] },
        { name: 'batch-workflow', values: ['single', 'segments'] }
    ];
    const PERSIST_RANGES = ['motion-strength', 'motion-attention-strength', 'vid-motion-speed', 'motion-motion-speed', 'batch-motion-speed', 'upscale-denoise'];

    function saveAllSettings() {
        const s = {};
        PERSIST_SELECTS.forEach(id => {
            const el = document.getElementById(id);
            if (el) s[id] = el.value;
        });
        PERSIST_INPUTS.forEach(id => {
            const el = document.getElementById(id);
            if (el) s[id] = el.value;
        });
        PERSIST_TEXTAREAS.forEach(id => {
            const el = document.getElementById(id);
            if (el) s[id] = el.value;
        });
        PERSIST_CHECKBOXES.forEach(id => {
            const el = document.getElementById(id);
            if (el) s[id] = el.checked;
        });
        PERSIST_RADIOS.forEach(r => {
            const checked = document.querySelector(`input[name="${r.name}"]:checked`);
            if (checked) s['radio_' + r.name] = checked.value;
        });
        PERSIST_RANGES.forEach(id => {
            const el = document.getElementById(id);
            if (el) s[id] = el.value;
        });
        s._currentMode = currentMode;
        s._ttsSubMode = _ttsSubMode;
        s._upscaleSubMode = _upscaleSubMode;
        s._uploadNames = {
            startFrame: pathFileName(s['start-frame-path'] || ''),
            endFrame: pathFileName(s['end-frame-path'] || ''),
            audio: pathFileName(s['uploaded-audio-path'] || ''),
            motionVideo: pathFileName(s['motion-video-path'] || ''),
            motionImage: pathFileName(s['motion-image-path'] || ''),
            batchAudio: pathFileName(s['batch-background-audio-path'] || ''),
            upscaleVideo: pathFileName(s['upscale-video-path'] || ''),
            upscaleImage: pathFileName(s['upscale-image-path'] || '')
        };
        if (batchImages && batchImages.length > 0) {
            s._batchImages = batchImages.map(img => ({
                name: img.name || '',
                path: img.path || '',
                width: img.width || null,
                height: img.height || null
            }));
        }
        try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); } catch(_) {}
    }

    function loadAllSettings() {
        let s;
        try { s = JSON.parse(localStorage.getItem(SETTINGS_KEY)); } catch(_) { return; }
        if (!s || typeof s !== 'object') return;

        PERSIST_SELECTS.forEach(id => {
            const el = document.getElementById(id);
            if (el && s[id] !== undefined) {
                el.value = s[id];
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }
        });
        PERSIST_INPUTS.forEach(id => {
            const el = document.getElementById(id);
            if (el && s[id] !== undefined) {
                el.value = s[id];
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }
        });
        PERSIST_TEXTAREAS.forEach(id => {
            const el = document.getElementById(id);
            if (el && s[id] !== undefined) el.value = s[id];
        });
        PERSIST_CHECKBOXES.forEach(id => {
            const el = document.getElementById(id);
            if (el && s[id] !== undefined) {
                el.checked = s[id];
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }
        });
        PERSIST_RADIOS.forEach(r => {
            const val = s['radio_' + r.name];
            if (val) {
                const radio = document.querySelector(`input[name="${r.name}"][value="${val}"]`);
                if (radio) {
                    radio.checked = true;
                    radio.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }
        });
        PERSIST_RANGES.forEach(id => {
            const el = document.getElementById(id);
            if (el && s[id] !== undefined) {
                el.value = s[id];
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }
        });
        if (s._currentMode && typeof switchMode === 'function') {
            if (s._ttsSubMode && TTS_SUB_MODES.includes(s._currentMode)) {
                _ttsSubMode = s._ttsSubMode;
            }
            if (s._upscaleSubMode && UPSCALE_SUB_MODES.includes(s._currentMode)) {
                _upscaleSubMode = s._upscaleSubMode;
            }
            switchMode(s._currentMode);
        }
        restoreUploadPreviews(s);
    }

    function restoreUploadPreviews(s) {
        if (!s) return;
        const names = s._uploadNames || {};

        const framePath = s['start-frame-path'];
        if (framePath) {
            const preview = document.getElementById('start-frame-preview');
            const placeholder = document.getElementById('start-frame-placeholder');
            const clearOverlay = document.getElementById('clear-start-frame-overlay');
            if (preview) { preview.src = mediaUrlForPath(framePath); preview.style.display = 'block'; }
            if (placeholder) placeholder.style.display = 'none';
            if (clearOverlay) clearOverlay.style.display = 'flex';
        }

        const endPath = s['end-frame-path'];
        if (endPath) {
            const preview = document.getElementById('end-frame-preview');
            const placeholder = document.getElementById('end-frame-placeholder');
            const clearOverlay = document.getElementById('clear-end-frame-overlay');
            if (preview) { preview.src = mediaUrlForPath(endPath); preview.style.display = 'block'; }
            if (placeholder) placeholder.style.display = 'none';
            if (clearOverlay) clearOverlay.style.display = 'flex';
        }

        const audioPath = s['uploaded-audio-path'];
        if (audioPath) {
            const placeholder = document.getElementById('audio-upload-placeholder');
            const statusDiv = document.getElementById('audio-upload-status');
            const filenameStatus = document.getElementById('audio-filename-status');
            const clearOverlay = document.getElementById('clear-audio-overlay');
            if (placeholder) placeholder.style.display = 'none';
            if (statusDiv) statusDiv.style.display = 'block';
            if (filenameStatus) filenameStatus.innerText = names.audio || pathFileName(audioPath);
            if (clearOverlay) clearOverlay.style.display = 'flex';
        }

        const motionVideoPath = s['motion-video-path'];
        if (motionVideoPath) {
            const placeholder = document.getElementById('motion-video-placeholder');
            const statusDiv = document.getElementById('motion-video-status');
            const nameEl = document.getElementById('motion-video-name');
            const clearOverlay = document.getElementById('clear-motion-video-overlay');
            const preview = document.getElementById('motion-video-preview');
            if (placeholder) placeholder.style.display = 'none';
            if (statusDiv) statusDiv.style.display = 'block';
            if (nameEl) nameEl.textContent = names.motionVideo || pathFileName(motionVideoPath);
            if (clearOverlay) clearOverlay.style.display = 'flex';
            if (preview) {
                preview.poster = `${BASE}/api/system/video-thumbnail?path=${encodeURIComponent(motionVideoPath)}&_t=${Date.now()}`;
                preview.src = mediaUrlForPath(motionVideoPath) + '&_t=' + Date.now();
            }
        }

        const motionImagePath = s['motion-image-path'];
        if (motionImagePath) {
            const preview = document.getElementById('motion-image-preview');
            const placeholder = document.getElementById('motion-image-placeholder');
            const clearOverlay = document.getElementById('clear-motion-image-overlay');
            if (preview) { preview.src = mediaUrlForPath(motionImagePath); preview.style.display = 'block'; }
            if (placeholder) placeholder.style.display = 'none';
            if (clearOverlay) clearOverlay.style.display = 'flex';
        }

        const batchAudioPath = s['batch-background-audio-path'];
        if (batchAudioPath) {
            const placeholder = document.getElementById('batch-audio-placeholder');
            const statusDiv = document.getElementById('batch-audio-status');
            const clearOverlay = document.getElementById('clear-batch-audio-overlay');
            if (placeholder) placeholder.style.display = 'none';
            if (statusDiv) { statusDiv.style.display = 'block'; statusDiv.textContent = '✓ ' + (names.batchAudio || pathFileName(batchAudioPath)); }
            if (clearOverlay) clearOverlay.style.display = 'flex';
        }

        const upscaleVideoPath = s['upscale-video-path'];
        if (upscaleVideoPath) {
            const placeholder = document.getElementById('upscale-video-placeholder');
            const nameEl = document.getElementById('upscale-video-name');
            const clearOverlay = document.getElementById('clear-upscale-video-overlay');
            const preview = document.getElementById('upscale-video-preview');
            if (placeholder) placeholder.style.display = 'none';
            if (nameEl) { nameEl.textContent = names.upscaleVideo || pathFileName(upscaleVideoPath); nameEl.style.display = 'block'; }
            if (clearOverlay) clearOverlay.style.display = 'flex';
            if (preview) {
                preview.style.display = 'block';
                preview.poster = `${BASE}/api/system/video-thumbnail?path=${encodeURIComponent(upscaleVideoPath)}&_t=${Date.now()}`;
                preview.src = mediaUrlForPath(upscaleVideoPath) + '&_t=' + Date.now();
            }
        }

        const upscaleImagePath = s['upscale-image-path'];
        if (upscaleImagePath) {
            const preview = document.getElementById('upscale-image-preview');
            const placeholder = document.getElementById('upscale-image-placeholder');
            const clearOverlay = document.getElementById('clear-upscale-image-overlay');
            if (preview) { preview.src = mediaUrlForPath(upscaleImagePath); preview.style.display = 'block'; }
            if (placeholder) placeholder.style.display = 'none';
            if (clearOverlay) clearOverlay.style.display = 'flex';
        }

        if (s._batchImages && s._batchImages.length > 0) {
            batchImages = s._batchImages.map(img => ({
                name: img.name || '',
                path: img.path || '',
                preview: img.path ? mediaUrlForPath(img.path) : '',
                width: img.width || null,
                height: img.height || null
            }));
            renderBatchImages();
            updateBatchSegments();
            updateBatchResPreview();
        }
    }

    let _saveTimer = null;
    function scheduleSave() {
        if (_saveTimer) clearTimeout(_saveTimer);
        _saveTimer = setTimeout(saveAllSettings, 500);
    }

    function initSettingsPersistence() {
        PERSIST_SELECTS.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('change', scheduleSave);
        });
        PERSIST_INPUTS.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('input', scheduleSave);
        });
        PERSIST_TEXTAREAS.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('input', scheduleSave);
        });
        PERSIST_CHECKBOXES.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('change', scheduleSave);
        });
        PERSIST_RADIOS.forEach(r => {
            document.querySelectorAll(`input[name="${r.name}"]`).forEach(radio => {
                radio.addEventListener('change', scheduleSave);
            });
        });
        PERSIST_RANGES.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('input', scheduleSave);
        });
    }

    // 建议增加一个简单的调试日志，方便在控制台确认地址是否正确
    console.log("Connecting to Backend API at:", BASE);

    function applyLoraScanData(data) {
        availableLoras = data?.loras || [];
        updateLoraDropdown();
        updateBatchLoraDropdown();
        updateImageLoraDropdown();
        if (data?.loras_dir) {
            const hintEl = document.getElementById('lora-placement-hint');
            if (hintEl) {
                const tpl = _t('loraPlacementHintWithDir');
                hintEl.innerHTML = tpl.replace(
                    '{dir}',
                    escapeHtmlAttr(data.models_dir || data.loras_dir)
                );
            }
        }
        if (availableLoras.length > 0) {
            addLog(`📂 已扫描到 ${availableLoras.length} 个 LoRA: ${availableLoras.map(l => l.name).join(', ')}`);
        }
    }

    // LoRA 扫描功能：页面启动时先完成 LoRA UI，再刷新历史缩略图。
    async function scanLoras() {
        let lorasFromRegistry = [];
        try {
            const res = await fetch(`${BASE}/api/models/registry`);
            const data = await res.json().catch(() => ({}));
            const models = data.models || [];
            // 2026-06-10 修复: lora 过滤也用 model_category === 'image-lora' / 'video-lora'(社区自定义目录的 lora)
            const loraModels = models.filter(m =>
                ((m.tags && m.tags.includes('lora')) ||
                 m.model_category === 'lora' ||
                 m.model_category === 'image-lora' ||
                 m.model_category === 'video-lora') &&
                m.downloaded && m.local_path
            );
            lorasFromRegistry = loraModels.map(m => ({
                name: m.filename || m.name,
                path: m.local_path,
                description: m.description || '',
                trigger_words: m.trigger_word ? [m.trigger_word] : (m.trigger_words || []),
                base_model: m.base_model || '',
                // 2026-06-10 修复: 透传 model_category / dir_path,便于 addLoraSelection 内对图像/视频 lora 过滤
                model_category: m.model_category || '',
                dir_path: m.dir_path || '',
                relative_path: m.relative_path || '',
            }));
            const localDirs = data.local_dirs || [];
            for (const dir of localDirs) {
                for (const m of (dir.models || [])) {
                    if (m.model_type === 'lora') {
                        lorasFromRegistry.push({
                            name: _beautifyModelName(m.name),
                            path: m.path,
                            description: m.description || '',
                            trigger_words: m.trigger_words || [],
                            base_model: m.base_model || '',
                            model_category: m.model_category || '',
                            dir_path: m.dir_path || '',
                            relative_path: m.relative_path || '',
                        });
                    }
                }
            }
        } catch (e) {
            console.log("LoRA registry scan error:", e);
        }

        let lorasWithMeta = [];
        try {
            const metaRes = await fetch(`${BASE}/api/loras?meta=1`);
            const metaData = await metaRes.json().catch(() => ({}));
            lorasWithMeta = metaData.loras || [];
        } catch (e) {
            console.log("LoRA meta scan error:", e);
        }

        const metaByPath = new Map();
        for (const l of lorasWithMeta) {
            if (l.path) metaByPath.set(l.path, l);
        }

        const seenPaths = new Set();
        const merged = [];

        for (const l of lorasFromRegistry) {
            if (!seenPaths.has(l.path)) {
                seenPaths.add(l.path);
                const meta = metaByPath.get(l.path);
                if (meta) {
                    merged.push({
                        ...l,
                        description: l.description || meta.description || '',
                        trigger_words: meta.trigger_words || [],
                        base_model: meta.base_model || ''
                    });
                } else {
                    merged.push(l);
                }
            }
        }

        for (const l of lorasWithMeta) {
            if (!seenPaths.has(l.path)) {
                seenPaths.add(l.path);
                merged.push(l);
            }
        }

        merged.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));

        applyLoraScanData({ loras: merged, loras_dir: '', models_dir: '' });
        return merged;
    }

function addLoraSelection(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;

        const wrapper = document.createElement('div');
        wrapper.className = 'lora-entry';
        wrapper.style.display = 'flex';
        wrapper.style.flexDirection = 'column';
        wrapper.style.gap = '4px';
        wrapper.style.padding = '8px';
        wrapper.style.background = 'rgba(255,255,255,0.03)';
        wrapper.style.borderRadius = '6px';
        wrapper.style.border = '1px solid var(--border)';

        const row1 = document.createElement('div');
        row1.style.display = 'flex';
        row1.style.gap = '8px';
        row1.style.alignItems = 'center';

        const select = document.createElement('select');
        select.className = 'lora-select';
        select.style.flex = '1';
        select.innerHTML = '<option value="">' + _t('noLora') + '</option>';
        let filteredLoras = availableLoras;
        if (containerId === 'img-loras-container') {
            filteredLoras = availableLoras.filter(lora => {
                // 2026-06-10 修复: 图像 lora 判定优先级: model_category > base_model > path > name
                // 因为很多 lora 文件的 safetensors metadata 没有 base_model 字段,单凭 base_model 会漏掉
                const cat = (lora.model_category || '').toLowerCase();
                const bm = (lora.base_model || '').toLowerCase();
                const nm = (lora.name || '').toLowerCase();
                const pth = (lora.path || '').toLowerCase();
                if (cat === 'image-lora') return true;
                if (bm.includes('z-image') || bm.includes('z_image') || bm.includes('zimage') || bm.includes('zit') || bm.includes('zib')) return true;
                if (pth.includes('z-image') || pth.includes('z_image') || pth.includes('zimage') || pth.includes('zit') || pth.includes('zib')) return true;
                if (nm.includes('z-image') || nm.includes('z_image') || nm.includes('zimage')) return true;
                return false;
            });
        } else {
            filteredLoras = availableLoras.filter(lora => {
                // 2026-06-10 修复: 视频 lora 判定,同样支持多字段
                const cat = (lora.model_category || '').toLowerCase();
                const bm = (lora.base_model || '').toLowerCase();
                const pth = (lora.path || '').toLowerCase();
                if (cat === 'video-lora') return true;
                if (bm.includes('ltx') || bm.includes('lightricks') || bm.includes('wan')) return true;
                if (pth.includes('ltx') || pth.includes('lightricks') || pth.includes('wan') || pth.includes('video')) return true;
                return false;
            });
        }
        filteredLoras.forEach(lora => {
            const opt = document.createElement('option');
            opt.value = lora.path;
            const desc = lora.description ? lora.description.slice(0, 60) : '';
            opt.textContent = desc ? `${lora.name} — ${desc}` : lora.name;
            if (lora.description) opt.title = lora.description;
            if (lora.trigger_words && lora.trigger_words.length > 0) {
                const existing = opt.title ? opt.title + '\n' : '';
                opt.title = existing + '触发词: ' + lora.trigger_words.join(', ');
            }
            select.appendChild(opt);
        });

        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.title = '移除';
        removeBtn.innerHTML = '×';
        removeBtn.style.background = 'none';
        removeBtn.style.border = 'none';
        removeBtn.style.color = 'var(--text-dim)';
        removeBtn.style.cursor = 'pointer';
        removeBtn.style.fontSize = '18px';
        removeBtn.style.padding = '0 6px';
        removeBtn.onclick = () => { wrapper.remove(); 
    }
    window.addLoraSelection = addLoraSelection;

        row1.appendChild(select);
        row1.appendChild(removeBtn);

        const loraInfoBox = document.createElement('div');
        loraInfoBox.className = 'lora-info-box';
        loraInfoBox.style.display = 'none';
        loraInfoBox.style.fontSize = '11px';
        loraInfoBox.style.color = 'var(--text-dim)';
        loraInfoBox.style.padding = '4px 6px';
        loraInfoBox.style.lineHeight = '1.5';
        loraInfoBox.style.maxHeight = '80px';
        loraInfoBox.style.overflow = 'hidden';
        loraInfoBox.style.background = 'rgba(255,255,255,0.04)';
        loraInfoBox.style.borderRadius = '4px';
        loraInfoBox.style.marginTop = '2px';

        const strengthContainer = document.createElement('div');
        strengthContainer.className = 'lora-strength-container';
        strengthContainer.style.display = 'none';
        strengthContainer.style.alignItems = 'center';
        strengthContainer.style.gap = '8px';
        strengthContainer.style.marginTop = '4px';

        const strLabel = document.createElement('label');
        strLabel.textContent = _t('loraStrength') || 'LoRA 强度';
        strLabel.style.margin = '0';
        strLabel.style.whiteSpace = 'nowrap';
        strLabel.style.fontSize = '11px';

        const strInput = document.createElement('input');
        strInput.type = 'range';
        strInput.className = 'lora-strength';
        strInput.min = '0.1';
        strInput.max = '2.0';
        strInput.step = '0.1';
        strInput.value = '1.0';
        strInput.style.flex = '1';
        strInput.style.margin = '0';

        const strVal = document.createElement('span');
        strVal.className = 'lora-strength-val';
        strVal.textContent = '1.0';
        strVal.style.fontSize = '11px';
        strVal.style.color = 'var(--accent)';
        strVal.style.width = '24px';
        strVal.style.textAlign = 'right';

        strInput.oninput = () => { strVal.textContent = strInput.value; };

        strengthContainer.appendChild(strLabel);
        strengthContainer.appendChild(strInput);
        strengthContainer.appendChild(strVal);

        wrapper.appendChild(row1);
        wrapper.appendChild(loraInfoBox);
        wrapper.appendChild(strengthContainer);

        function updateLoraInfo() {
            if (!select.value) {
                loraInfoBox.style.display = 'none';
                loraInfoBox.innerHTML = '';
                return;
            }
            const lora = availableLoras.find(l => l.path === select.value);
            if (!lora || (!lora.description && (!lora.trigger_words || lora.trigger_words.length === 0) && !lora.base_model)) {
                loraInfoBox.style.display = 'none';
                loraInfoBox.innerHTML = '';
                return;
            }
            const html = _renderLoraInfoHtml(lora);
            loraInfoBox.innerHTML = html;
            loraInfoBox.style.display = html ? 'block' : 'none';
        }

        select.onchange = () => {
            strengthContainer.style.display = select.value ? 'flex' : 'none';
            updateLoraInfo();
        };

        container.appendChild(wrapper);
    };

    function collectLoras(containerId) {
        const results = [];
        document.querySelectorAll(`#${containerId} .lora-entry`).forEach(entry => {
            const sel = entry.querySelector('.lora-select');
            const strIn = entry.querySelector('.lora-strength');
            if (sel && sel.value) {
                results.push({ path: sel.value, strength: strIn ? parseFloat(strIn.value) : 1.0 });
            }
        });
        return results;
    }

    function getSelectedImgModelPath() {
        const select = document.getElementById('img-model-select');
        return (select?.value || '').trim();
    }

    function _collectLocalModelsFromRegistry(data) {
        const allModels = [];
        const seen = new Set();
        for (const m of (data.models || [])) {
            if (m.downloaded && m.local_path && !seen.has(m.local_path)) {
                seen.add(m.local_path);
                // 2026-06-10 修复: 透传后端返回的 model_category / tags / variant / pipeline_mode / dir_path
                // dir_path 在 builtin 的 folder 模型(如 z-image-turbo)里没显式设,这里用 local_path 兜底作为目录路径
                const dp = m.dir_path || (m.is_folder ? m.local_path : (m.local_path ? m.local_path.replace(/[/\\][^/\\]+$/, '') : ''));
                allModels.push({
                    name: m.filename || m.name,
                    path: m.local_path,
                    model_type: m.model_category || 'checkpoint',
                    model_category: m.model_category || '',
                    tags: m.tags || [],
                    variant: m.variant || '',
                    pipeline_mode: m.pipeline_mode || '',
                    dir_path: dp,
                    relative_path: m.relative_path || '',
                });
            }
        }
        for (const dir of (data.local_dirs || [])) {
            for (const m of (dir.models || [])) {
                if (!seen.has(m.path)) {
                    seen.add(m.path);
                    // 2026-06-10 修复: 透传 dir_path / relative_path / model_category / base_model(便于 _isImageModel 命中 unet/z_image 目录规则,以及 lora 过滤)
                    allModels.push({
                        name: m.name,
                        path: m.path,
                        model_type: m.model_type || 'checkpoint',
                        model_category: m.model_category || '',
                        tags: m.tags || [],
                        variant: m.variant || '',
                        pipeline_mode: m.pipeline_mode || '',
                        dir_path: m.dir_path || '',
                        relative_path: m.relative_path || '',
                        base_model: m.base_model || '',
                        description: m.description || '',
                        trigger_words: m.trigger_words || [],
                    });
                }
            }
        }
        return allModels;
    }

    function _isImageModel(m) {
        const n = (m.name || '').toLowerCase();
        const dp = (m.dir_path || m.relative_path || '').toLowerCase();
        // 2026-06-10 修复: 严格排除所有 lora(包括从 local_dirs 扫到的 model_type='lora' 和 registry 的 model_category='lora')
        if (m.model_category === 'lora' || m.model_type === 'lora') return false;
        if (m.model_category === 'image-lora' || m.model_category === 'video-lora') return false;
        if (n.includes('ic-lora') || n.includes('control')) return false;
        // lora 文件名中即使没有 'lora' 字样也走 lora 路径(防误判 zit-/zib- 前缀的 lora)
        if (m.model_category === 'upscaler' || m.model_type === 'upscaler') return false;
        // 2026-06-10 修复: 优先以 model_category / tags / variant / pipeline_mode 字段判断
        if (m.model_category === 'image' || m.model_category === 'image-checkpoint') return true;
        if (m.tags && Array.isArray(m.tags) && m.tags.includes('image-gen')) return true;
        if (m.variant === 'image-gen' || m.variant === 'image') return true;
        if (m.pipeline_mode === 'image-gen' || m.pipeline_mode === 'image') return true;
        // 2026-06-10 修复: 启动器目录约定 — unet/ 或 z_image/ 目录下的所有 .safetensors/.ckpt 都视为图像模型
        if (dp.includes('unet') || dp.includes('z_image') || dp.includes('z-image') || dp.includes('zimage')) return true;
        // 兼容旧判断: 名称包含 z-image/zimage/zit-/zib- 的文件,且不是 lora/control
        if (n.includes('lora')) return false;
        return n.includes('z-image') || n.includes('zimage') || n.includes('zit-') || n.includes('zib-') || m.model_type === 'image';
    }

    function _isVideoCheckpoint(m) {
        const n = (m.name || '').toLowerCase();
        if (!n.endsWith('.safetensors')) return false;
        if (!n.includes('ltx')) return false;
        return !n.includes('ic-lora') && !n.includes('control') && !n.includes('upscaler') && !n.includes('vae') && !n.includes('lora');
    }

    function _beautifyModelName(name) {
        let n = name.replace(/\.safetensors$/i, '').replace(/\.ckpt$/i, '').replace(/\.pt$/i, '').replace(/\.bin$/i, '').replace(/\.pth$/i, '');
        n = n.replace(/[-_]+/g, ' ').replace(/\b(\d+b)\b/gi, ' $1').trim();
        return n || name;
    }

async function loadImgModelList() {
        const select = document.getElementById('img-model-select');
        if (!select) return;
        const currentVal = select.value;
        try {
            const r = await fetch(`${BASE}/api/models/registry`);
            const d = await r.json();
            const allModels = _collectLocalModelsFromRegistry(d);
            const imageModels = allModels.filter(_isImageModel);
            select.innerHTML = '<option value="">' + (_t('imgModelDefault') || '默认 Z-Image-Turbo') + '</option>';
            imageModels.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m.path;
                opt.textContent = _beautifyModelName(m.name);
                select.appendChild(opt);
            });
            if (currentVal && select.querySelector(`option[value="${CSS.escape(currentVal)}"]`)) {
                select.value = currentVal;
            }
        } catch (_) {}
    
    }
    window.loadImgModelList = loadImgModelList;

    function _fillLoraSelect(select, baseModelKind) {
        const currentVal = select.value;
        select.innerHTML = '<option value="">' + _t('noLora') + '</option>';
        const filtered = availableLoras.filter(lora => {
            if (!baseModelKind) return true;
            const bm = (lora.base_model || '').toLowerCase();
            if (baseModelKind === 'ltx') {
                return bm.includes('ltx') || bm.includes('lightricks');
            }
            if (baseModelKind === 'zimage') {
                return bm.includes('z-image') || bm.includes('z_image') || bm.includes('zimage');
            }
            return true;
        });
        filtered.forEach(lora => {
            const opt = document.createElement('option');
            opt.value = lora.path;
            const desc = lora.description ? lora.description.slice(0, 60) : '';
            opt.textContent = desc ? `${lora.name} — ${desc}` : lora.name;
            if (lora.description) opt.title = lora.description;
            if (lora.trigger_words && lora.trigger_words.length > 0) {
                const existing = opt.title ? opt.title + '\n' : '';
                opt.title = existing + '触发词: ' + lora.trigger_words.join(', ');
            }
            select.appendChild(opt);
        });
        if (currentVal && filtered.some(l => l.path === currentVal)) {
            select.value = currentVal;
        }
    }

    function _renderLoraInfoHtml(lora) {
        let html = '';
        if (lora.description) html += `<div style="margin-bottom:3px;color:var(--text-secondary)">${escapeHtmlAttr(lora.description)}</div>`;
        if (lora.trigger_words && lora.trigger_words.length > 0) {
            const twHtml = lora.trigger_words.map(tw => `<span style="display:inline-block;background:rgba(255,255,255,0.08);padding:1px 6px;border-radius:3px;margin:1px 3px 1px 0;color:var(--accent);font-size:11px">${escapeHtmlAttr(tw)}</span>`).join('');
            html += `<div style="margin-bottom:2px;color:#66BB6A;">✓ 触发词已自动注入: ${twHtml}</div>`;
        } else {
            html += `<div style="margin-bottom:2px;color:#888;">✓ 通用LoRA，无需触发词即可生效</div>`;
        }
        if (lora.base_model) html += `<div style="color:var(--text-secondary)">基模: ${escapeHtmlAttr(lora.base_model)}</div>`;
        return html;
    }

    function updateLoraDropdown() {
        if (document.getElementById('loras-container') && document.getElementById('loras-container').children.length === 0) {
            window.addLoraSelection('loras-container');
        }
        const selects = document.querySelectorAll('#loras-container .lora-select');
        selects.forEach(select => {
            _fillLoraSelect(select, 'ltx');
            const infoBox = select.closest('.lora-entry')?.querySelector('.lora-info-box');
            if (infoBox && select.value) {
                const lora = availableLoras.find(l => l.path === select.value);
                if (lora && (lora.description || (lora.trigger_words && lora.trigger_words.length > 0) || lora.base_model)) {
                    const html = _renderLoraInfoHtml(lora);
                    infoBox.innerHTML = html;
                    infoBox.style.display = html ? 'block' : 'none';
                } else {
                    infoBox.style.display = 'none';
                    infoBox.innerHTML = '';
                }
            }
        });
    }

window.updateLoraStrength = function() {};

    function updateBatchLoraDropdown() {
        if (document.getElementById('batch-loras-container') && document.getElementById('batch-loras-container').children.length === 0) {
            window.addLoraSelection('batch-loras-container');
        }
        const selects = document.querySelectorAll('#batch-loras-container .lora-select');
        selects.forEach(select => {
            _fillLoraSelect(select, 'ltx');
            const infoBox = select.closest('.lora-entry')?.querySelector('.lora-info-box');
            if (infoBox && select.value) {
                const lora = availableLoras.find(l => l.path === select.value);
                if (lora && (lora.description || (lora.trigger_words && lora.trigger_words.length > 0) || lora.base_model)) {
                    const html = _renderLoraInfoHtml(lora);
                    infoBox.innerHTML = html;
                    infoBox.style.display = html ? 'block' : 'none';
                } else {
                    infoBox.style.display = 'none';
                    infoBox.innerHTML = '';
                }
            }
        });
    }
    
window.updateBatchLoraStrength = function() {};

    function updateImageLoraDropdown() {
        if (document.getElementById('img-loras-container') && document.getElementById('img-loras-container').children.length === 0) {
            window.addLoraSelection('img-loras-container');
        }
        const selects = document.querySelectorAll('#img-loras-container .lora-select');
        selects.forEach(select => {
            _fillLoraSelect(select, 'zimage');
            const infoBox = select.closest('.lora-entry')?.querySelector('.lora-info-box');
            if (infoBox && select.value) {
                const lora = availableLoras.find(l => l.path === select.value);
                if (lora && (lora.description || (lora.trigger_words && lora.trigger_words.length > 0) || lora.base_model)) {
                    const html = _renderLoraInfoHtml(lora);
                    infoBox.innerHTML = html;
                    infoBox.style.display = html ? 'block' : 'none';
                } else {
                    infoBox.style.display = 'none';
                    infoBox.innerHTML = '';
                }
            }
        });
    }
    window.updateImageLoraDropdown = updateImageLoraDropdown;

    // 页面加载时更新批量模式的下拉框
    function initBatchDropdowns() {
        updateBatchLoraDropdown();
        updateImageLoraDropdown();
    }

    // 已移除：模型/LoRA 目录自定义与浏览（保持后端默认路径扫描）

    // 页面加载时只做轻量初始化；启动流程在 DOMContentLoaded 中按 LoRA -> 历史记录执行。
    (function() {
        ['vid-quality', 'batch-quality'].forEach((id) => {
            const sel = document.getElementById(id);
            if (sel && sel.value === '544') sel.value = '540';
        });
        initBatchDropdowns();
    })();

    // 分辨率自动计算逻辑
    const uploadedFrameDims = { start: null, end: null };

    function roundTo64(value) {
        const n = Math.round(Number(value || 0) / 64) * 64;
        return Math.max(64, n);
    }

    function parseRatioValue(value) {
        const [a, b] = String(value || '16:9').split(':').map(Number);
        if (!a || !b) return 16 / 9;
        return a / b;
    }

    function sizeForRatio(quality, ratioValue) {
        const shortSideMap = { '1080': 1088, '720': 704, '540': 576, '480': 416, '360': 352 };
        const shortSide = shortSideMap[quality] || 576;
        const ratio = parseRatioValue(ratioValue);
        if (ratio >= 1) return { width: roundTo64(shortSide * ratio), height: roundTo64(shortSide) };
        return { width: roundTo64(shortSide), height: roundTo64(shortSide / ratio) };
    }

    function getReferenceSize(kind) {
        if (kind === 'batch') {
            return batchImages?.[0]?.width && batchImages?.[0]?.height
                ? { width: batchImages[0].width, height: batchImages[0].height }
                : null;
        }
        return uploadedFrameDims.start;
    }

    function getGenerationSize(kind) {
        const isBatch = kind === 'batch';
        const qEl = document.getElementById(isBatch ? 'batch-quality' : 'vid-quality');
        const q = qEl ? qEl.value : '720';
        const ratioEl = document.getElementById(isBatch ? 'batch-ratio' : 'vid-ratio');
        const ratio = ratioEl?.value || '16:9';
        const customBox = document.getElementById(isBatch ? 'batch-custom-size' : 'vid-custom-size');
        if (customBox) customBox.style.display = ratio === 'custom' ? 'flex' : 'none';

        if (ratio === 'custom') {
            const w = document.getElementById(isBatch ? 'batch-custom-w' : 'vid-custom-w')?.value;
            const h = document.getElementById(isBatch ? 'batch-custom-h' : 'vid-custom-h')?.value;
            return { width: roundTo64(w), height: roundTo64(h), source: 'custom' };
        }
        if (ratio === 'ref') {
            const ref = getReferenceSize(kind);
            if (ref) return { width: roundTo64(ref.width), height: roundTo64(ref.height), source: 'ref' };
            return { ...sizeForRatio(q, '16:9'), source: 'ref-missing' };
        }
        return { ...sizeForRatio(q, ratio), source: ratio };
    }

    function resLabelFromQuality(q) {
        const map = { '1080': '1080p', '720': '720p', '540': '540p', '480': '480p', '360': '360p' };
        return map[q] || '540p';
    }

    function isVidModelDev() {
        const sel = document.getElementById('vid-model-select');
        if (!sel) return false;
        const modelName = (sel.value || '').toLowerCase();
        return modelName.includes('dev') && !modelName.includes('distilled');
    }

    function updateVidStepsUI() {
        const stepsDistilled = document.getElementById('vid-steps-distilled');
        const stepsInput = document.getElementById('vid-steps');
        if (!stepsDistilled || !stepsInput) return;
        const isDev = isVidModelDev();
        stepsDistilled.style.display = isDev ? 'none' : 'block';
        stepsInput.style.display = isDev ? 'block' : 'none';
    }

    function onVidModelChange() {
        const sel = document.getElementById('vid-model-select');
        const hintEl = document.getElementById('vid-model-hint');
        if (!sel) return;
        updateVidStepsUI();
        const isDev = isVidModelDev();
        if (hintEl && isDev) {
            hintEl.textContent = _t('vidModelAutoStandard');
            setTimeout(() => { hintEl.textContent = _t('vidModelHint'); }, 3000);
        }
        const globalSelect = document.getElementById('model-checkpoint-select');
        if (globalSelect) {
            globalSelect.value = sel.value;
            saveSelectedModelCheckpoint();
        }
    }
    window.onVidModelChange = onVidModelChange;

    function syncVidModelFromGlobal() {
        const globalSelect = document.getElementById('model-checkpoint-select');
        const vidSelect = document.getElementById('vid-model-select');
        if (!globalSelect || !vidSelect) return;
        // 2026-06-10 修复: 优先以 globalSelect 当前值为准(用户/同步刚改完),没有就退到 localStorage
        // 这样多次调用时不会回退到旧值
        let targetValue = (globalSelect.value || '').trim();
        if (!targetValue) {
            try { targetValue = (localStorage.getItem(MODEL_CHECKPOINT_STORAGE_KEY) || '').trim(); } catch (_) {}
        }
        vidSelect.innerHTML = globalSelect.innerHTML;
        if (targetValue) {
            // 校验 target 是否在 vidSelect 的选项里
            const has = Array.from(vidSelect.options).some(o => o.value === targetValue);
            if (has) vidSelect.value = targetValue;
        } else {
            vidSelect.value = globalSelect.value;
        }
        // 同步 steps UI(可能模型变了,需更新 dev/distilled 模式显示)
        if (typeof updateVidStepsUI === 'function') updateVidStepsUI();
    }
    window.syncVidModelFromGlobal = syncVidModelFromGlobal;

    function updateResPreview() {
        const vidQuality = document.getElementById('vid-quality');
        if (!vidQuality) return;
        const q = vidQuality.value;
        const size = getGenerationSize('video');
        const recEl = document.getElementById('vid-quality-rec');
        if (recEl && recEl.style.display !== 'none' && recEl.textContent) {
            const base = recEl.textContent.replace(/（\d+×\d+）.*$/, '');
            recEl.textContent = `${base}（${size.width}×${size.height}）`;
        }
        return resLabelFromQuality(q);
    }

    function _imgSizeForQualityRatio(quality, ratio) {
        const shortEdge = parseInt(quality) || 720;
        const [rw, rh] = ratio.split(':').map(Number);
        let w, h;
        if (rw >= rh) {
            h = shortEdge;
            w = Math.round(shortEdge * rw / rh);
        } else {
            w = shortEdge;
            h = Math.round(shortEdge * rh / rw);
        }
        w = Math.round(w / 16) * 16;
        h = Math.round(h / 16) * 16;
        return { w, h };
    }

    function updateImgResPreview() {
        const qualityEl = document.getElementById('img-quality');
        const ratioEl = document.getElementById('img-ratio');
        const customRes = document.getElementById('img-custom-res');
        const preview = document.getElementById('img-res-preview');
        const imgW = document.getElementById('img-w');
        const imgH = document.getElementById('img-h');
        if (!qualityEl || !ratioEl || !preview) return;

        const ratio = ratioEl.value;
        if (ratio === 'custom') {
            if (customRes) customRes.style.display = 'flex';
            if (imgW && imgH) preview.innerText = `${imgW.value}×${imgH.value}`;
            return;
        }
        if (customRes) customRes.style.display = 'none';
        const { w, h } = _imgSizeForQualityRatio(qualityEl.value, ratio);
        if (imgW) imgW.value = w;
        if (imgH) imgH.value = h;
        preview.innerText = `${w}×${h}`;
    }

    function updateBatchResPreview() {
        const batchQuality = document.getElementById('batch-quality');
        if (!batchQuality) return;
        const q = batchQuality.value;
        const size = getGenerationSize('batch');
        const recEl = document.getElementById('batch-quality-rec');
        if (recEl && recEl.style.display !== 'none' && recEl.textContent) {
            const base = recEl.textContent.replace(/（\d+×\d+）.*$/, '');
            recEl.textContent = `${base}（${size.width}×${size.height}）`;
        }
        return resLabelFromQuality(q);
    }

function updateMotionResPreview() {
        const qEl = document.getElementById('motion-quality');
        const preview = document.getElementById('motion-res-preview');
        if (!qEl || !preview) return;
        const q = qEl.value;
        const shortSideMap = { '1080': 1088, '720': 704, '540': 576, '480': 416, '360': 352 
    }
    window.updateMotionResPreview = updateMotionResPreview;
        const shortSide = shortSideMap[q] || 704;
        preview.textContent = `${shortSide}p · ` + (q === '1080' ? 'Full HD' : q === '720' ? 'Standard' : q === '540' ? 'Preview' : q === '480' ? 'Lite' : 'Minimal') + ' · 跟随参考视频比例';
    };

    // 批量模式 LoRA 强度切换
    function updateBatchLoraStrength() {
        const select = document.getElementById('batch-lora');
        const container = document.getElementById('batch-lora-strength-container');
        if (select && container) {
            container.style.display = select.value ? 'flex' : 'none';
        }
    }



    // 处理帧图片上传
    function getImageDataUrlSize(dataUrl) {
        return new Promise((resolve) => {
            const probe = new Image();
            probe.onload = () => resolve({ width: probe.naturalWidth || probe.width, height: probe.naturalHeight || probe.height });
            probe.onerror = () => resolve(null);
            probe.src = dataUrl;
        });
    }

    async function handleFrameUpload(file, frameType) {
        if (!file) return;

        const preview = document.getElementById(`${frameType}-frame-preview`);
        const placeholder = document.getElementById(`${frameType}-frame-placeholder`);
        const clearOverlay = document.getElementById(`clear-${frameType}-frame-overlay`);

        const previewReader = new FileReader();
        previewReader.onload = async (e) => {
            preview.src = e.target.result;
            preview.style.display = 'block';
            placeholder.style.display = 'none';
            clearOverlay.style.display = 'flex';
            uploadedFrameDims[frameType] = await getImageDataUrlSize(e.target.result);
            updateResPreview();
        };
        previewReader.readAsDataURL(file);

        const reader = new FileReader();
        reader.onload = async (e) => {
            const b64Data = e.target.result;
            addLog(`正在上传 ${frameType === 'start' ? '起始帧' : '结束帧'}: ${file.name}...`);
            try {
                const res = await fetch(`${BASE}/api/system/upload-image`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ image: b64Data, filename: file.name })
                });
                const data = await res.json();
                if (res.ok && data.path) {
                    document.getElementById(`${frameType}-frame-path`).value = data.path;
                    addLog(`✅ ${frameType === 'start' ? '起始帧' : '结束帧'}上传成功`);
                    scheduleSave();
                } else {
                    throw new Error(data.error || data.detail || "上传失败");
                }
            } catch (e) {
                addLog(`❌ 帧图片上传失败: ${e.message}`);
            }
        };
        reader.readAsDataURL(file);
    }

    function clearFrame(frameType) {
        document.getElementById(`${frameType}-frame-input`).value = "";
        document.getElementById(`${frameType}-frame-path`).value = "";
        uploadedFrameDims[frameType] = null;
        document.getElementById(`${frameType}-frame-preview`).style.display = 'none';
        document.getElementById(`${frameType}-frame-preview`).src = "";
        document.getElementById(`${frameType}-frame-placeholder`).style.display = 'block';
        document.getElementById(`clear-${frameType}-frame-overlay`).style.display = 'none';
        addLog(`🧹 已清除${frameType === 'start' ? '起始帧' : '结束帧'}`);
        updateResPreview();
        scheduleSave();
    }

    // 处理图片上传
    async function handleImageUpload(file) {
        if (!file) return;
        
        // 预览图片
        const preview = document.getElementById('upload-preview');
        const placeholder = document.getElementById('upload-placeholder');
        const clearOverlay = document.getElementById('clear-img-overlay');
        
        const previewReader = new FileReader();
        preview.onload = () => {
            preview.style.display = 'block';
            placeholder.style.display = 'none';
            clearOverlay.style.display = 'flex';
        };
        previewReader.onload = (e) => preview.src = e.target.result;
        previewReader.readAsDataURL(file);

        // 使用 FileReader 转换为 Base64，绕过后端缺失 python-multipart 的问题
        const reader = new FileReader();
        reader.onload = async (e) => {
            const b64Data = e.target.result;
            addLog(`正在上传参考图: ${file.name}...`);
            try {
                const res = await fetch(`${BASE}/api/system/upload-image`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        image: b64Data,
                        filename: file.name
                    })
                });
                const data = await res.json();
                if (res.ok && data.path) {
                    document.getElementById('uploaded-img-path').value = data.path;
                    addLog(`✅ 参考图上传成功: ${file.name}`);
                    scheduleSave();
                } else {
                    const errMsg = data.error || data.detail || "上传失败";
                    throw new Error(typeof errMsg === 'string' ? errMsg : JSON.stringify(errMsg));
                }
            } catch (e) {
                addLog(`❌ 图片上传失败: ${e.message}`);
            }
        };
        reader.onerror = () => addLog("❌ 读取本地文件失败");
        reader.readAsDataURL(file);
    }

    function clearUploadedImage() {
        document.getElementById('vid-image-input').value = "";
        document.getElementById('uploaded-img-path').value = "";
        document.getElementById('upload-preview').style.display = 'none';
        document.getElementById('upload-preview').src = "";
        document.getElementById('upload-placeholder').style.display = 'block';
        document.getElementById('clear-img-overlay').style.display = 'none';
        addLog("🧹 已清除参考图");
        scheduleSave();
    }

    // 处理音频上传
    async function handleAudioUpload(file) {
        if (!file) return;

        const placeholder = document.getElementById('audio-upload-placeholder');
        const statusDiv = document.getElementById('audio-upload-status');
        const filenameStatus = document.getElementById('audio-filename-status');
        const clearOverlay = document.getElementById('clear-audio-overlay');

        placeholder.style.display = 'none';
        filenameStatus.innerText = file.name;
        statusDiv.style.display = 'block';
        clearOverlay.style.display = 'flex';

        const reader = new FileReader();
        reader.onload = async (e) => {
            const b64Data = e.target.result;
            addLog(`正在上传音频: ${file.name}...`);
            try {
                // 复用图片上传接口，后端已支持任意文件类型
                const res = await fetch(`${BASE}/api/system/upload-image`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        image: b64Data,
                        filename: file.name
                    })
                });
                const data = await res.json();
                if (res.ok && data.path) {
                    document.getElementById('uploaded-audio-path').value = data.path;
                    addLog(`✅ 音频上传成功: ${file.name}`);
                    scheduleSave();
                } else {
                    const errMsg = data.error || data.detail || "上传失败";
                    throw new Error(typeof errMsg === 'string' ? errMsg : JSON.stringify(errMsg));
                }
            } catch (e) {
                addLog(`❌ 音频上传失败: ${e.message}`);
            }
        };
        reader.onerror = () => addLog("❌ 读取本地音频文件失败");
        reader.readAsDataURL(file);
    }

    function clearUploadedAudio() {
        document.getElementById('vid-audio-input').value = "";
        document.getElementById('uploaded-audio-path').value = "";
        document.getElementById('audio-upload-placeholder').style.display = 'block';
        document.getElementById('audio-upload-status').style.display = 'none';
        document.getElementById('clear-audio-overlay').style.display = 'none';
        addLog("🧹 已清除音频文件");
        scheduleSave();
    }

    async function uploadBase64File(file, logLabel) {
        const b64Data = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = (e) => resolve(e.target.result);
            reader.onerror = () => reject(new Error(_t('fileReadFail')));
            reader.readAsDataURL(file);
        });
        addLog(_fmt('uploadFileStart', { label: logLabel, name: file.name }));
        const res = await fetch(`${BASE}/api/system/upload-image`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: b64Data, filename: file.name })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.path) {
            const errMsg = data.error || data.detail || "上传失败";
            throw new Error(typeof errMsg === 'string' ? errMsg : JSON.stringify(errMsg));
        }
        return data.path;
    }

    let _motionVideoUploadGen = 0;

async function handleMotionVideoUpload(file) {
        if (!file) return;
        const gen = ++_motionVideoUploadGen;
        const label = _t('motionRefVideoName');
        try {
            const preview = document.getElementById('motion-video-preview');
            if (preview) {
                preview.pause();
                preview.removeAttribute('src');
                preview.removeAttribute('poster');
                preview.load();
            }
            const videoUrl = URL.createObjectURL(file);
            if (preview) {
                preview.src = videoUrl;
                preview.load();
                preview.onloadedmetadata = () => {
                    if (gen !== _motionVideoUploadGen) return;
                    const dur = preview.duration;
                    if (dur && isFinite(dur) && dur > 0) {
                        const durEl = document.getElementById('motion-duration');
                        if (durEl) {
                            durEl.value = Math.round(dur * 10) / 10;
                            if (typeof _updateMotionParamWarnings === 'function') _updateMotionParamWarnings();
                        }
                        addLog(_fmt('motionVideoDuration', { duration: dur.toFixed(1) }));
                    }
                
    }
    window.handleMotionVideoUpload = handleMotionVideoUpload;
            }
            const path = await uploadBase64File(file, label);
            if (gen !== _motionVideoUploadGen) return;
            document.getElementById('motion-video-path').value = path;
            document.getElementById('motion-video-placeholder').style.display = 'none';
            document.getElementById('motion-video-status').style.display = 'block';
            document.getElementById('motion-video-name').textContent = file.name;
            document.getElementById('clear-motion-video-overlay').style.display = 'flex';
            addLog(_fmt('motionUploadOk', { label, name: file.name }));
            scheduleSave();
            document.getElementById('motion-video-input').value = "";
        } catch (e) {
            if (gen === _motionVideoUploadGen) {
                addLog(_fmt('motionUploadFail', { label, message: e.message }));
            }
        }
    };

function clearMotionVideo() {
        _motionVideoUploadGen++;
        document.getElementById('motion-video-input').value = "";
        document.getElementById('motion-video-path').value = "";
        const preview = document.getElementById('motion-video-preview');
        if (preview) { preview.pause(); preview.removeAttribute('src'); preview.removeAttribute('poster'); preview.load(); }
        document.getElementById('motion-video-placeholder').style.display = 'block';
        document.getElementById('motion-video-status').style.display = 'none';
        document.getElementById('motion-video-name').textContent = "";
        document.getElementById('clear-motion-video-overlay').style.display = 'none';
        addLog(_t('motionClearRefVideo'));
        scheduleSave();
    
    }
    window.clearMotionVideo = clearMotionVideo;

    let _motionImageUploadGen = 0;

async function handleMotionImageUpload(file) {
        if (!file) return;
        const gen = ++_motionImageUploadGen;
        const preview = document.getElementById('motion-image-preview');
        const placeholder = document.getElementById('motion-image-placeholder');
        const clearOverlay = document.getElementById('clear-motion-image-overlay');
        const reader = new FileReader();
        reader.onload = (e) => {
            if (gen !== _motionImageUploadGen) return;
            preview.src = e.target.result;
            preview.style.display = 'block';
            placeholder.style.display = 'none';
            clearOverlay.style.display = 'flex';
        
    }
    window.handleMotionImageUpload = handleMotionImageUpload;
        reader.readAsDataURL(file);
        const label = _t('motionTargetImageName');
        try {
            const path = await uploadBase64File(file, label);
            if (gen !== _motionImageUploadGen) return;
            document.getElementById('motion-image-path').value = path;
            addLog(_fmt('motionUploadOk', { label, name: file.name }));
            scheduleSave();
            document.getElementById('motion-image-input').value = "";
        } catch (e) {
            if (gen === _motionImageUploadGen) {
                addLog(_fmt('motionUploadFail', { label, message: e.message }));
            }
        }
    };

function clearMotionImage() {
        _motionImageUploadGen++;
        document.getElementById('motion-image-input').value = "";
        document.getElementById('motion-image-path').value = "";
        document.getElementById('motion-image-preview').style.display = 'none';
        document.getElementById('motion-image-preview').src = "";
        document.getElementById('motion-image-placeholder').style.display = 'block';
        document.getElementById('clear-motion-image-overlay').style.display = 'none';
        addLog(_t('motionClearTargetImage'));
        scheduleSave();
    
    }
    window.clearMotionImage = clearMotionImage;

    // 初始化拖拽上传逻辑
    function initDragAndDrop() {
        const audioDropZone = document.getElementById('audio-drop-zone');
        const startFrameDropZone = document.getElementById('start-frame-drop-zone');
        const endFrameDropZone = document.getElementById('end-frame-drop-zone');
        const batchImagesDropZone = document.getElementById('batch-images-drop-zone');
        const motionVideoDropZone = document.getElementById('motion-video-drop-zone');
        const motionImageDropZone = document.getElementById('motion-image-drop-zone');
        const ttsRefDropZone = document.getElementById('tts-ref-drop');
        const upscaleVideoDropZone = document.getElementById('upscale-video-drop-zone');
        const upscaleImageDropZone = document.getElementById('upscale-image-drop-zone');

        const zones = [audioDropZone, startFrameDropZone, endFrameDropZone, batchImagesDropZone, motionVideoDropZone, motionImageDropZone, ttsRefDropZone, upscaleVideoDropZone, upscaleImageDropZone].filter(z => z);

        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            zones.forEach(zone => {
                if (!zone) return;
                zone.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                }, false);
            });
        });

        ['dragenter', 'dragover'].forEach(eventName => {
            zones.forEach(zone => {
                if (!zone) return;
                zone.addEventListener(eventName, () => zone.classList.add('dragover'), false);
            });
        });

        ['dragleave', 'drop'].forEach(eventName => {
            zones.forEach(zone => {
                if (!zone) return;
                zone.addEventListener(eventName, () => zone.classList.remove('dragover'), false);
            });
        });

        // 2026-06-10 修复: 统一接管历史资产拖拽 + 本地文件拖拽两种来源
        // 优先用 dataTransfer.files(从操作系统/外部资源拖来),再用 application/x-yunji-asset(从历史资产列表拖来)
        const isVideoFile = (file) => file && (file.type.startsWith('video/') || /\.(mp4|mov|webm|mkv|avi)$/i.test(file.name));
        const isImageFile = (file) => file && file.type.startsWith('image/');
        const isAudioFile = (file) => file && (file.type.startsWith('audio/') || /\.(wav|mp3|flac|ogg|m4a)$/i.test(file.name));

        async function resolveDroppedFile(e, kind) {
            // 1) 优先: 操作系统直接拖拽来的文件
            const f = e.dataTransfer.files && e.dataTransfer.files[0];
            if (f) return f;
            // 2) 次选: 从历史资产拖拽来的(自定义 MIME 携带 filename+type)
            const payload = (() => {
                try { return JSON.parse(e.dataTransfer.getData('application/x-yunji-asset') || 'null'); }
                catch (_) { return null; }
            })();
            if (!payload || !payload.filename || !payload.type) return null;
            // 类型匹配: 历史资产类型必须和 drop zone 期望的类型一致
            if (kind === 'video' && payload.type !== 'video') return null;
            if (kind === 'image' && payload.type !== 'image') return null;
            if (kind === 'audio' && payload.type !== 'audio') return null;
            return await _fetchAssetAsFile(payload);
        }

        if (audioDropZone) audioDropZone.addEventListener('drop', async (e) => {
            const file = await resolveDroppedFile(e, 'audio');
            if (file && isAudioFile(file)) handleAudioUpload(file);
        }, false);

        if (startFrameDropZone) startFrameDropZone.addEventListener('drop', async (e) => {
            const file = await resolveDroppedFile(e, 'image');
            if (file && isImageFile(file)) handleFrameUpload(file, 'start');
        }, false);

        if (endFrameDropZone) endFrameDropZone.addEventListener('drop', async (e) => {
            const file = await resolveDroppedFile(e, 'image');
            if (file && isImageFile(file)) handleFrameUpload(file, 'end');
        }, false);

        // 批量图片拖拽上传
        if (batchImagesDropZone) {
            batchImagesDropZone.addEventListener('drop', async (e) => {
                e.preventDefault();
                e.stopPropagation();
                batchImagesDropZone.classList.remove('dragover');
                const files = Array.from(e.dataTransfer.files).filter(f => isImageFile(f));
                if (files.length > 0) {
                    handleBatchImagesUpload(files);
                    return;
                }
                // 历史资产单文件拖拽也支持
                const assetFile = await resolveDroppedFile(e, 'image');
                if (assetFile && isImageFile(assetFile)) handleBatchImagesUpload([assetFile]);
            }, false);
        }

        if (motionVideoDropZone) {
            motionVideoDropZone.addEventListener('drop', async (e) => {
                const file = await resolveDroppedFile(e, 'video');
                if (file && isVideoFile(file)) {
                    window.handleMotionVideoUpload(file);
                }
            }, false);
        }

        if (motionImageDropZone) {
            motionImageDropZone.addEventListener('drop', async (e) => {
                const file = await resolveDroppedFile(e, 'image');
                if (file && isImageFile(file)) {
                    window.handleMotionImageUpload(file);
                }
            }, false);
        }

        if (ttsRefDropZone) {
            ttsRefDropZone.addEventListener('drop', async (e) => {
                const file = await resolveDroppedFile(e, 'audio');
                if (file && isAudioFile(file)) {
                    handleTtsRefUpload(file);
                }
            }, false);
        }

        if (upscaleVideoDropZone) {
            upscaleVideoDropZone.addEventListener('drop', async (e) => {
                const file = await resolveDroppedFile(e, 'video');
                if (file && isVideoFile(file)) {
                    handleUpscaleVideoUpload(file);
                }
            }, false);
        }

        if (upscaleImageDropZone) {
            upscaleImageDropZone.addEventListener('drop', async (e) => {
                const file = await resolveDroppedFile(e, 'image');
                if (file && isImageFile(file)) {
                    handleUpscaleImageUpload(file);
                }
            }, false);
        }
    }

    // 2026-06-10 修复: 从历史资产拖拽到 drop zone 时,系统会传 application/x-yunji-asset (含 filename+type)
    // 接收端 fetch 实际文件并包装为 File 对象,转交给 handler
    function _mimeForType(t) {
        if (t === 'video') return 'video/mp4';
        if (t === 'image') return 'image/png';
        if (t === 'audio') return 'audio/wav';
        return 'application/octet-stream';
    }
    async function _fetchAssetAsFile(payload) {
        const fileUrl = `${BASE}/api/system/file?path=${encodeURIComponent(payload.filename)}&v=${Date.now()}`;
        const mime = _mimeForType(payload.type);
        try {
            const r = await fetch(fileUrl);
            if (!r.ok) throw new Error('fetch failed: ' + r.status);
            const blob = await r.blob();
            return new File([blob], payload.filename, { type: mime });
        } catch (e) {
            addLog(`❌ 拖拽历史资产失败: ${e.message}`);
            return null;
        }
    }

    // 批量图片上传处理
    let batchImages = [];
    /** 单次多关键帧：按 path 记引导强度；按帧索引记每张图占用秒数 */
    const batchKfStrengthByPath = {};
    const batchKfSegDurByIndex = {};

    function escapeHtmlAttr(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/</g, '&lt;');
    }

    function defaultKeyframeStrengthForIndex(i, n) {
        if (n <= 2) return '1';
        if (i === 0) return '0.62';
        if (i === n - 1) return '1';
        return '0.42';
    }

    function captureBatchKfTimelineFromDom() {
        batchImages.forEach((img, i) => {
            if (!img.path) return;
            const sEl = document.getElementById(`batch-kf-strength-${i}`);
            if (sEl) batchKfStrengthByPath[img.path] = sEl.value.trim();
        });
        const n = batchImages.length;
        for (let j = 0; j < n; j++) {
            const el = document.getElementById(`batch-kf-seg-dur-${j}`);
            if (el) batchKfSegDurByIndex[j] = el.value.trim();
        }
    }

    /** 读取每帧占用时长（秒），非法则回退为 minSeg */
    function readBatchKfFrameSeconds(n, minSeg) {
        const secs = [];
        for (let j = 0; j < n; j++) {
            let v = parseFloat(document.getElementById(`batch-kf-seg-dur-${j}`)?.value);
            if (!Number.isFinite(v) || v < minSeg) v = minSeg;
            secs.push(v);
        }
        return secs;
    }

    function updateBatchKfTimelineDerivedUI() {
        if (!batchWorkflowIsSingle() || batchImages.length < 2) return;
        const n = batchImages.length;
        const minSeg = 0.1;
        const frameSecs = readBatchKfFrameSeconds(n, minSeg);
        let t = 0;
        for (let i = 0; i < n; i++) {
            const label = document.getElementById(`batch-kf-anchor-label-${i}`);
            if (!label) continue;
            if (i === 0) {
                label.textContent = `0.0 s · ${_t('batchAnchorStart')}`;
            } else {
                t += frameSecs[i - 1];
                label.textContent =
                    i === n - 1
                        ? `${t.toFixed(1)} s · ${_t('batchAnchorLast')}`
                        : `${t.toFixed(1)} s`;
            }
        }
        const totalEl = document.getElementById('batch-kf-total-seconds');
        if (totalEl) {
            const sum = frameSecs.reduce((a, b) => a + b, 0);
            totalEl.textContent = sum.toFixed(1);
        }
    }
    async function handleBatchImagesUpload(files, append = true) {
        if (!files || files.length === 0) return;
        addLog(`正在上传 ${files.length} 张图片...`);

        for (let i = 0; i < files.length; i++) {
            const file = files[i];
            const reader = new FileReader();

            const imgData = await new Promise((resolve) => {
                reader.onload = async (e) => {
                    const b64Data = e.target.result;
                    try {
                        const res = await fetch(`${BASE}/api/system/upload-image`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ image: b64Data, filename: file.name })
                        });
                        const data = await res.json();
                        if (res.ok && data.path) {
                            const dims = await getImageDataUrlSize(e.target.result);
                            resolve({ name: file.name, path: data.path, preview: e.target.result, width: dims?.width || null, height: dims?.height || null });
                        } else {
                            resolve(null);
                        }
                    } catch (e) {
                        resolve(null);
                    }
                };
                reader.readAsDataURL(file);
            });

            if (imgData) {
                batchImages.push(imgData);
                addLog(`✅ 图片 ${i + 1}/${files.length} 上传成功: ${file.name}`);
            }
        }

        renderBatchImages();
        updateBatchSegments();
        updateBatchResPreview();
        scheduleSave();
    }

    async function handleBatchBackgroundAudioUpload(file) {
        if (!file) return;
        const ph = document.getElementById('batch-audio-placeholder');
        const st = document.getElementById('batch-audio-status');
        const overlay = document.getElementById('clear-batch-audio-overlay');
        const reader = new FileReader();
        reader.onload = async (e) => {
            const b64Data = e.target.result;
            addLog(`正在上传成片配乐: ${file.name}...`);
            try {
                const res = await fetch(`${BASE}/api/system/upload-image`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ image: b64Data, filename: file.name })
                });
                const data = await res.json();
                if (res.ok && data.path) {
                    const hid = document.getElementById('batch-background-audio-path');
                    if (hid) hid.value = data.path;
                    if (ph) ph.style.display = 'none';
                    if (st) {
                        st.style.display = 'block';
                        st.textContent = '✓ ' + file.name;
                    }
                    if (overlay) overlay.style.display = 'flex';
                    addLog('✅ 成片配乐已上传（将覆盖各片段自带音轨）');
                    scheduleSave();
                } else {
                    addLog(`❌ 配乐上传失败: ${data.error || '未知错误'}`);
                }
            } catch (err) {
                addLog(`❌ 配乐上传失败: ${err.message}`);
            }
        };
        reader.onerror = () => addLog('❌ 读取音频文件失败');
        reader.readAsDataURL(file);
    }

    function clearBatchBackgroundAudio() {
        const hid = document.getElementById('batch-background-audio-path');
        const inp = document.getElementById('batch-audio-input');
        if (hid) hid.value = '';
        if (inp) inp.value = '';
        const ph = document.getElementById('batch-audio-placeholder');
        const st = document.getElementById('batch-audio-status');
        const overlay = document.getElementById('clear-batch-audio-overlay');
        if (ph) ph.style.display = 'block';
        if (st) {
            st.style.display = 'none';
            st.textContent = '';
        }
        if (overlay) overlay.style.display = 'none';
        addLog('🧹 已清除成片配乐');
        scheduleSave();
    }

    function syncBatchDropZoneChrome() {
        const dropZone = document.getElementById('batch-images-drop-zone');
        const placeholder = document.getElementById('batch-images-placeholder');
        const stripWrap = document.getElementById('batch-thumb-strip-wrap');
        if (batchImages.length === 0) {
            if (dropZone) {
                dropZone.classList.remove('has-images');
                const mini = dropZone.querySelector('.upload-placeholder-mini');
                if (mini) mini.remove();
            }
            if (placeholder) placeholder.style.display = 'block';
            if (stripWrap) stripWrap.style.display = 'none';
            return;
        }
        if (placeholder) placeholder.style.display = 'none';
        if (dropZone) dropZone.classList.add('has-images');
        if (stripWrap) stripWrap.style.display = 'block';
        if (dropZone && !dropZone.querySelector('.upload-placeholder-mini')) {
            const mini = document.createElement('div');
            mini.className = 'upload-placeholder-mini';
            mini.innerHTML = '<span>' + _t('batchAddMore') + '</span>';
            dropZone.appendChild(mini);
        }
    }

    let batchDragPlaceholderEl = null;
    let batchPointerState = null;
    let batchPendingPhX = null;
    let batchPhMoveRaf = null;

    function batchRemoveFloatingGhost() {
        document.querySelectorAll('.batch-thumb-floating-ghost').forEach((n) => n.remove());
    }

    function batchCancelPhMoveRaf() {
        if (batchPhMoveRaf != null) {
            cancelAnimationFrame(batchPhMoveRaf);
            batchPhMoveRaf = null;
        }
        batchPendingPhX = null;
    }

    function batchEnsurePlaceholder() {
        if (batchDragPlaceholderEl && batchDragPlaceholderEl.isConnected) return batchDragPlaceholderEl;
        const el = document.createElement('div');
        el.className = 'batch-thumb-drop-slot';
        el.setAttribute('aria-hidden', 'true');
        batchDragPlaceholderEl = el;
        return el;
    }

    function batchRemovePlaceholder() {
        if (batchDragPlaceholderEl && batchDragPlaceholderEl.parentNode) {
            batchDragPlaceholderEl.parentNode.removeChild(batchDragPlaceholderEl);
        }
    }

    function batchComputeInsertIndex(container, placeholder) {
        let t = 0;
        for (const child of container.children) {
            if (child === placeholder) return t;
            if (child.classList && child.classList.contains('batch-image-wrapper')) {
                if (!child.classList.contains('batch-thumb--source')) t++;
            }
        }
        return t;
    }

    function batchMovePlaceholderFromPoint(container, clientX) {
        const ph = batchEnsurePlaceholder();
        const wrappers = [...container.querySelectorAll('.batch-image-wrapper')];
        let insertBefore = null;
        for (const w of wrappers) {
            if (w.classList.contains('batch-thumb--source')) continue;
            const r = w.getBoundingClientRect();
            if (clientX < r.left + r.width / 2) {
                insertBefore = w;
                break;
            }
        }
        if (insertBefore === null) {
            const vis = wrappers.filter((w) => !w.classList.contains('batch-thumb--source'));
            const last = vis[vis.length - 1];
            if (last) {
                if (last.nextSibling) {
                    container.insertBefore(ph, last.nextSibling);
                } else {
                    container.appendChild(ph);
                }
            } else {
                container.appendChild(ph);
            }
        } else {
            container.insertBefore(ph, insertBefore);
        }
    }

    function batchFlushPlaceholderMove() {
        batchPhMoveRaf = null;
        if (!batchPointerState || batchPendingPhX == null) return;
        batchMovePlaceholderFromPoint(batchPointerState.container, batchPendingPhX);
    }

    function handleBatchPointerMove(e) {
        if (!batchPointerState) return;
        e.preventDefault();
        const st = batchPointerState;
        st.ghostTX = e.clientX - st.offsetX;
        st.ghostTY = e.clientY - st.offsetY;
        batchPendingPhX = e.clientX;
        if (batchPhMoveRaf == null) {
            batchPhMoveRaf = requestAnimationFrame(batchFlushPlaceholderMove);
        }
    }

    function batchGhostFrame() {
        const st = batchPointerState;
        if (!st || !st.ghostEl || !st.ghostEl.isConnected) {
            return;
        }
        const t = 0.42;
        st.ghostCX += (st.ghostTX - st.ghostCX) * t;
        st.ghostCY += (st.ghostTY - st.ghostCY) * t;
        st.ghostEl.style.transform =
            `translate3d(${st.ghostCX}px,${st.ghostCY}px,0) scale(1.06) rotate(-1deg)`;
        st.ghostRaf = requestAnimationFrame(batchGhostFrame);
    }

    function batchStartGhostLoop() {
        const st = batchPointerState;
        if (!st || !st.ghostEl) return;
        if (st.ghostRaf != null) cancelAnimationFrame(st.ghostRaf);
        st.ghostRaf = requestAnimationFrame(batchGhostFrame);
    }

    function batchEndPointerDrag(e) {
        if (!batchPointerState) return;
        if (e.pointerId !== batchPointerState.pointerId) return;
        const st = batchPointerState;

        batchCancelPhMoveRaf();
        if (st.ghostRaf != null) {
            cancelAnimationFrame(st.ghostRaf);
            st.ghostRaf = null;
        }
        if (st.ghostEl && st.ghostEl.parentNode) {
            st.ghostEl.remove();
        }
        batchPointerState = null;

        document.removeEventListener('pointermove', handleBatchPointerMove);
        document.removeEventListener('pointerup', batchEndPointerDrag);
        document.removeEventListener('pointercancel', batchEndPointerDrag);

        try {
            if (st.wrapperEl) st.wrapperEl.releasePointerCapture(st.pointerId);
        } catch (_) {}

        const { fromIndex, container, wrapperEl } = st;
        container.classList.remove('is-batch-settling');
        if (!batchDragPlaceholderEl || !batchDragPlaceholderEl.parentNode) {
            if (wrapperEl) wrapperEl.classList.remove('batch-thumb--source');
            renderBatchImages();
            updateBatchSegments();
            return;
        }
        const to = batchComputeInsertIndex(container, batchDragPlaceholderEl);
        batchRemovePlaceholder();
        if (wrapperEl) wrapperEl.classList.remove('batch-thumb--source');

        if (fromIndex !== to && fromIndex >= 0 && to >= 0) {
            const [item] = batchImages.splice(fromIndex, 1);
            batchImages.splice(to, 0, item);
            updateBatchSegments();
            scheduleSave();
        }
        renderBatchImages();
    }

    function handleBatchPointerDown(e) {
        if (batchPointerState) return;
        if (e.button !== 0) return;
        if (e.target.closest && e.target.closest('.batch-thumb-remove')) return;

        const wrapper = e.currentTarget;
        const container = document.getElementById('batch-images-container');
        if (!container) return;

        e.preventDefault();
        e.stopPropagation();

        const fromIndex = parseInt(wrapper.dataset.index, 10);
        if (Number.isNaN(fromIndex)) return;

        const rect = wrapper.getBoundingClientRect();
        const offsetX = e.clientX - rect.left;
        const offsetY = e.clientY - rect.top;
        const startLeft = rect.left;
        const startTop = rect.top;

        const ghost = document.createElement('div');
        ghost.className = 'batch-thumb-floating-ghost';
        const gImg = document.createElement('img');
        const srcImg = wrapper.querySelector('img');
        gImg.src = srcImg ? srcImg.src : '';
        gImg.alt = '';
        ghost.appendChild(gImg);
        document.body.appendChild(ghost);

        batchPointerState = {
            fromIndex,
            pointerId: e.pointerId,
            wrapperEl: wrapper,
            container,
            ghostEl: ghost,
            offsetX,
            offsetY,
            ghostTX: e.clientX - offsetX,
            ghostTY: e.clientY - offsetY,
            ghostCX: startLeft,
            ghostCY: startTop,
            ghostRaf: null
        };

        ghost.style.transform =
            `translate3d(${startLeft}px,${startTop}px,0) scale(1.06) rotate(-1deg)`;

        container.classList.add('is-batch-settling');
        wrapper.classList.add('batch-thumb--source');
        const ph = batchEnsurePlaceholder();
        container.insertBefore(ph, wrapper.nextSibling);
        /* 不在 pointerdown 立刻重算槽位；双 rAF 后再恢复邻居 transition，保证先完成本帧布局再动画 */
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                container.classList.remove('is-batch-settling');
            });
        });

        batchStartGhostLoop();

        document.addEventListener('pointermove', handleBatchPointerMove, { passive: false });
        document.addEventListener('pointerup', batchEndPointerDrag);
        document.addEventListener('pointercancel', batchEndPointerDrag);

        try {
            wrapper.setPointerCapture(e.pointerId);
        } catch (_) {}
    }

    function removeBatchImage(index) {
        if (index < 0 || index >= batchImages.length) return;
        batchImages.splice(index, 1);
        renderBatchImages();
        updateBatchSegments();
        scheduleSave();
    }

    // 横向缩略图：Pointer 拖动排序（避免 HTML5 DnD 在 WebView/部分浏览器失效）
    function renderBatchImages() {
        const container = document.getElementById('batch-images-container');
        if (!container) return;

        syncBatchDropZoneChrome();
        batchRemovePlaceholder();
        batchCancelPhMoveRaf();
        batchRemoveFloatingGhost();
        batchPointerState = null;
        container.classList.remove('is-batch-settling');
        container.innerHTML = '';

        batchImages.forEach((img, index) => {
            const wrapper = document.createElement('div');
            wrapper.className = 'batch-image-wrapper';
            wrapper.dataset.index = String(index);
            wrapper.title = _t('batchThumbDrag');

            const imgWrap = document.createElement('div');
            imgWrap.className = 'batch-thumb-img-wrap';
            const im = document.createElement('img');
            im.className = 'batch-thumb-img';
            im.src = img.preview;
            im.alt = img.name || '';
            im.draggable = false;
            imgWrap.appendChild(im);

            const del = document.createElement('button');
            del.type = 'button';
            del.className = 'batch-thumb-remove';
            del.title = _t('batchThumbRemove');
            del.setAttribute('aria-label', _t('batchThumbRemove'));
            del.textContent = '×';
            del.addEventListener('pointerdown', (ev) => ev.stopPropagation());
            del.addEventListener('click', (ev) => {
                ev.stopPropagation();
                removeBatchImage(index);
            });

            wrapper.appendChild(imgWrap);
            wrapper.appendChild(del);

            wrapper.addEventListener('pointerdown', handleBatchPointerDown);

            container.appendChild(wrapper);
        });
    }

    function batchWorkflowIsSingle() {
        const r = document.querySelector('input[name="batch-workflow"]:checked');
        return !!(r && r.value === 'single');
    }

    function onBatchWorkflowChange() {
        updateBatchSegments();
    }

    // 更新片段设置 UI（分段模式）或单次多关键帧设置
    function updateBatchSegments() {
        const container = document.getElementById('batch-segments-container');
        if (!container) return;
        
        if (batchImages.length < 2) {
            container.innerHTML =
                '<div style="color: var(--text-dim); font-size: 11px;">' +
                escapeHtmlAttr(_t('batchNeedTwo')) +
                '</div>';
            return;
        }

        if (batchWorkflowIsSingle()) {
            if (batchImages.length >= 2) captureBatchKfTimelineFromDom();
            const n = batchImages.length;
            const defaultTotal = 8;
            const defaultSeg =
                n > 0 ? (defaultTotal / n).toFixed(1) : '4';
            let blocks = '';
            batchImages.forEach((img, i) => {
                const path = img.path || '';
                const stDef = defaultKeyframeStrengthForIndex(i, n);
                const stStored = batchKfStrengthByPath[path];
                const stVal = stStored !== undefined && stStored !== ''
                    ? escapeHtmlAttr(stStored)
                    : stDef;
                const prev = escapeHtmlAttr(img.preview || '');
                const sdStored = batchKfSegDurByIndex[i];
                const segVal =
                    sdStored !== undefined && sdStored !== ''
                        ? escapeHtmlAttr(sdStored)
                        : defaultSeg;
                blocks += `
                <div class="batch-kf-kcard">
                    <div class="batch-kf-kcard-head">
                        <img class="batch-kf-kthumb" src="${prev}" alt="">
                        <div class="batch-kf-kcard-titles">
                            <span class="batch-kf-ktitle">${escapeHtmlAttr(_t('batchKfTitle'))} ${i + 1} / ${n}</span>
                            <span class="batch-kf-anchor" id="batch-kf-anchor-label-${i}">—</span>
                        </div>
                    </div>
                    <div class="batch-kf-kcard-ctrl">
                        <label class="batch-kf-klabel">${escapeHtmlAttr(_t('batchStrength'))}
                            <input type="number" id="batch-kf-strength-${i}" value="${stVal}" min="0.1" max="1" step="0.01"
                                title="${escapeHtmlAttr(_t('batchStrengthTitle'))}">
                        </label>
                        <label class="batch-kf-klabel">${escapeHtmlAttr(_t('batchFrameDuration'))}
                            <input type="number" class="batch-kf-seg-input" id="batch-kf-seg-dur-${i}"
                                value="${segVal}" min="0.1" max="120" step="0.1"
                                title="${escapeHtmlAttr(_t('batchFrameDurationTitle'))}"
                                oninput="updateBatchKfTimelineDerivedUI()">
                            <span class="batch-kf-gap-unit">${escapeHtmlAttr(_t('batchSec'))}</span>
                        </label>
                    </div>
                </div>`;
            });
            container.innerHTML = `
                <div class="batch-kf-panel" id="batch-kf-timeline-root">
                    <div class="batch-kf-panel-hd">
                        <div class="batch-kf-panel-title">${escapeHtmlAttr(_t('batchKfPanelTitle'))}</div>
                        <div class="batch-kf-total-pill" title="${escapeHtmlAttr(_t('batchTotalPillTitle'))}">
                            ${escapeHtmlAttr(_t('batchTotalDur'))} <strong id="batch-kf-total-seconds">—</strong> <span class="batch-kf-total-unit">${escapeHtmlAttr(_t('batchTotalSec'))}</span>
                        </div>
                    </div>
                    <p class="batch-kf-panel-hint">${escapeHtmlAttr(_t('batchPanelHint'))}</p>
                    <div class="batch-kf-timeline-col">
                        ${blocks}
                    </div>
                </div>`;
            updateBatchKfTimelineDerivedUI();
            return;
        }
        
        let html =
            '<div style="font-size: 12px; font-weight: bold; margin-bottom: 10px;">' +
            escapeHtmlAttr(_t('batchSegTitle')) +
            '</div>';
        
        for (let i = 0; i < batchImages.length - 1; i++) {
            const segPh = escapeHtmlAttr(_t('batchSegPromptPh'));
            html += `
                <div style="background: var(--item); border-radius: 8px; padding: 10px; margin-bottom: 10px; border: 1px solid var(--border);">
                    <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <img src="${batchImages[i].preview}" style="width: 40px; height: 40px; border-radius: 4px; object-fit: cover;">
                            <span style="color: var(--accent);">→</span>
                            <img src="${batchImages[i + 1].preview}" style="width: 40px; height: 40px; border-radius: 4px; object-fit: cover;">
                            <span style="font-size: 11px; color: var(--text-dim);">${escapeHtmlAttr(_t('batchSegClip'))} ${i + 1}</span>
                        </div>
                        <div style="display: flex; align-items: center; gap: 6px;">
                            <label style="font-size: 10px; color: var(--text-dim);">${escapeHtmlAttr(_t('batchSegDuration'))}</label>
                            <input type="number" id="batch-segment-duration-${i}" value="5" min="1" max="30" step="1" style="width: 50px; padding: 4px; font-size: 11px;">
                            <span style="font-size: 10px; color: var(--text-dim);">${escapeHtmlAttr(_t('batchSegSec'))}</span>
                        </div>
                    </div>
                    <div>
                        <label style="font-size: 10px;">${escapeHtmlAttr(_t('batchSegPrompt'))}</label>
                        <textarea id="batch-segment-prompt-${i}" placeholder="${segPh}" style="width: 100%; height: 60px; padding: 6px; font-size: 11px; box-sizing: border-box; resize: vertical;"></textarea>
                    </div>
                </div>
            `;
        }
        
        container.innerHTML = html;
    }

    let _isGeneratingFlag = false;
    let _isQueuePaused = false;
    let _currentQueueTaskId = null;
    let _awaitingQueueStart = false;
    const queueTaskTerminalSeen = new Set();
    let lastAutoDisplayedQueueTaskId = null;
    let queuePollInFlight = false;
    let queueInitialPollDone = false;
    let statusPollInFlight = false;
    let currentPreviewAssetUrl = '';
    let currentPreviewAssetName = '';
    let currentPreviewReplayId = '';

    function queueStatusLabel(task) {
        if (!task) return _t('queueIdle');
        const mapping = {
            queued: _t('queueQueued'),
            running: _t('queueRunning'),
            complete: _t('queueComplete'),
            error: _t('queueError'),
            cancelled: _t('queueCancelled'),
            cancelling: '取消中...',
            paused: '已暂停',
        };
        return mapping[task.status] || task.status || '未知';
    }

    function queueModeLabel(task) {
        const mode = task?.mode || 'task';
        const mapping = {
            video: _t('queueTaskTypeVideo'),
            motion: _t('queueTaskTypeMotion'),
            batch: _t('queueTaskTypeBatch'),
            image: _t('queueTaskTypeImage'),
            tts: _t('queueTaskTypeTts'),
            upscale: _t('tabUpscale') || '高清放大'
        };
        return mapping[mode] || mode;
    }

    let _lastQueueRenderKey = '';

    function renderQueueStatus(data) {
        const summary = document.getElementById('queue-summary');
        const list = document.getElementById('queue-list');
        if (!summary || !list) return;

        const pending = Array.isArray(data?.pending) ? data.pending : [];
        const current = data?.current || null;
        summary.textContent = current
            ? _fmt('queueRunningSummary', { n: pending.length })
            : (pending.length ? _fmt('queueWaiting', { n: pending.length }) : _t('queueIdle'));

        const items = [];
        if (current) items.push({ ...current, _current: true });
        items.push(...pending.slice(0, 4));

        if (!items.length) {
            list.innerHTML = `<div style="font-size:11px;color:var(--text-dim);padding:6px 0;">${_t('queueNoTasks')}</div>`;
            _lastQueueRenderKey = '';
            return;
        }

        const renderKey = items.map(t => `${t.id}:${t.status}:${t._current ? 1 : 0}`).join('|');
        const progressOnly = (renderKey === _lastQueueRenderKey) && current;

        if (progressOnly) {
            const bar = list.querySelector('[data-qbar]');
            const ptText = list.querySelector('[data-qptext]');
            if (bar) bar.style.width = Math.min(100, current.progress || 0) + '%';
            if (ptText) {
                const pct = current.progress || 0;
                const modeLbl = queueModeLabel({...current, _current: true});
                const statusLbl = queueStatusLabel(current);
                ptText.textContent = `${_t('queueProgressLabel')}${pct}% ${modeLbl} · ${statusLbl}`;
            }
            return;
        }
        _lastQueueRenderKey = renderKey;

        list.innerHTML = items.map(task => {
            const title = escapeHtmlAttr(task.label || queueModeLabel(task));
            const status = queueStatusLabel(task);
            const meta = task._current
                ? ''
                : (task.position ? _fmt('queuePosition', { n: task.position }) : status);
            const metaLine = task._current
                ? ''
                : `<div data-qmeta style="font-size:10px;color:var(--text-dim);margin-top:2px;">${queueModeLabel(task)} · ${meta}</div>`;
            const error = task.error ? `<div style="font-size:10px;color:#f87171;line-height:1.35;">${escapeHtmlAttr(task.error)}</div>` : '';
            const resultPath = task.result?.video_path || task.result?.image_paths?.[0] || task.result?.audio_path || '';
            const isTtsAudio = !task.result?.video_path && !task.result?.image_paths?.length && (task.result?.audio_path || task.result?.audio_url);
            const openBtn = resultPath
                ? `<button onclick='${isTtsAudio ? `playTtsFromQueue(${JSON.stringify(task.result.audio_url || '')})` : `displayOutput(${JSON.stringify(resultPath)})`}' style="margin-top:4px;font-size:10px;padding:3px 8px;border-radius:999px;border:1px solid rgba(255,255,255,0.12);background:rgba(255,255,255,0.04);color:var(--text-main);cursor:pointer;">${_t('queueViewResult')}</button>`
                : '';
            const isRunning = task._current && (task.status === 'running' || task.status === 'queued');
            const progressDesc = task._current
                ? `${_t('queueProgressLabel')}${task.progress || 0}% ${queueModeLabel(task)} · ${status}`
                : '';
            const progressBar = task._current
                ? `<div style="margin-top:6px;display:flex;align-items:center;gap:8px;">
                    <div style="width:50%;height:6px;border-radius:3px;background:rgba(255,255,255,0.08);overflow:hidden;flex-shrink:0;">
                        <div data-qbar style="height:100%;width:${Math.min(100, task.progress || 0)}%;background:var(--accent);border-radius:3px;transition:width 0.8s ease;"></div>
                    </div>
                    <span data-qptext style="flex:1;font-size:10px;color:var(--text-dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${progressDesc}</span>
                    ${isRunning ? `
                    <button onclick="togglePause()" style="font-size:10px;padding:2px 6px;border-radius:4px;border:1px solid rgba(250,204,21,0.3);background:rgba(250,204,21,0.1);color:#facc15;cursor:pointer;white-space:nowrap;" title="${_isQueuePaused ? '恢复' : '暂停'}">${_isQueuePaused ? '▶' : '⏸'}</button>
                    <button onclick="cancelQueueTask('${task.id}')" style="font-size:10px;padding:2px 6px;border-radius:4px;border:1px solid rgba(248,113,113,0.3);background:rgba(248,113,113,0.1);color:#f87171;cursor:pointer;white-space:nowrap;" title="${_t('queueCancelBtn')}">✕</button>
                    ` : ''}
                </div>`
                : '';
            const cancelQueuedBtn = (!task._current && task.status === 'queued')
                ? `<button onclick="cancelQueueTask('${task.id}')" style="margin-top:4px;font-size:10px;padding:2px 8px;border-radius:4px;border:1px solid rgba(248,113,113,0.3);background:rgba(248,113,113,0.1);color:#f87171;cursor:pointer;">${_t('queueCancelBtn')}</button>`
                : '';
            return `
                <div style="padding:8px 10px;border-radius:10px;background:${task._current ? 'rgba(92,214,143,0.08)' : 'rgba(255,255,255,0.04)'};border:1px solid ${task._current ? 'rgba(92,214,143,0.22)' : 'rgba(255,255,255,0.06)'};">
                    <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start;">
                        <div style="min-width:0;">
                            <div style="font-size:11px;font-weight:700;color:var(--text-main);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${title}</div>
                            ${metaLine}
                        </div>
                        <div style="font-size:10px;color:${task.status === 'error' ? '#f87171' : (task._current ? 'var(--accent)' : 'var(--text-dim)')};flex-shrink:0;">${status}</div>
                    </div>
                    ${progressBar}
                    ${error}
                    ${openBtn}
                    ${cancelQueuedBtn}
                </div>
            `;
        }).join('');
    }

    async function cancelQueueTask(taskId) {
        if (!taskId) return;
        try {
            const res = await fetch(`${BASE}/api/queue/cancel/${taskId}`, { method: 'POST' });
            const data = await res.json().catch(() => ({}));
            if (data.status === 'cancelled') {
                addLog(_fmt('queueCancelLog', { label: taskId }));
            } else if (data.status === 'cancelling') {
                addLog('⚠️ 正在取消任务...');
            }
            pollQueueStatus();
        } catch (e) {
            addLog(`❌ ${_t('queueCancelFail')}: ${e.message}`);
        }
    }

async function togglePause() {
        try {
            if (_isQueuePaused) {
                const res = await fetch(`${BASE}/api/queue/resume`, { method: 'POST' });
                const data = await res.json().catch(() => ({}));
                if (data.status === 'resumed') {
                    _isQueuePaused = false;
                    addLog('▶️ 队列已恢复');
                }
            } else {
                const res = await fetch(`${BASE}/api/queue/pause`, { method: 'POST' });
                const data = await res.json().catch(() => ({}));
                if (data.status === 'paused') {
                    _isQueuePaused = true;
                    addLog('⏸️ 队列已暂停（当前任务继续执行，后续任务等待恢复）');
                }
            }
            _updateGenButtons();
        } catch (e) {
            addLog(`❌ 暂停/恢复失败: ${e.message}`);
        }
    
    }
    window.togglePause = togglePause;

async function cancelCurrentTask() {
        if (!_currentQueueTaskId && !_isGeneratingFlag) return;
        const taskId = _currentQueueTaskId;
        if (taskId) {
            await cancelQueueTask(taskId);
        } else {
            try {
                const res = await fetch(`${BASE}/api/generate/force-cancel`, { method: 'POST' });
                const data = await res.json().catch(() => ({}));
                if (data.status === 'cancelling') {
                    addLog(_t('forceCancelLog'));
                }
            } catch (e) {
                addLog(`❌ ${_t('forceCancelFail')}: ${e.message}`);
            }
        }
    
    }
    window.cancelCurrentTask = cancelCurrentTask;

    function _updateGenButtons() {
        const mainBtn = document.getElementById('mainBtn');
        const ttsTopBtn = document.getElementById('tts-top-btn');
        const upscaleTopBtn = document.getElementById('upscale-top-btn');
        const genBtnRow = document.getElementById('gen-btn-row');
        const pauseBtn = document.getElementById('pauseBtn');
        const isTtsMode = TTS_SUB_MODES.includes(currentMode);
        const isUpscaleMode = UPSCALE_SUB_MODES.includes(currentMode);

        if (_isGeneratingFlag) {
            if (mainBtn) mainBtn.style.display = 'none';
            if (ttsTopBtn) ttsTopBtn.style.display = 'none';
            if (upscaleTopBtn) upscaleTopBtn.style.display = 'none';
            if (genBtnRow) genBtnRow.style.display = 'flex';
            if (pauseBtn) {
                pauseBtn.textContent = _isQueuePaused ? '▶ 恢复' : '⏸ 暂停';
            }
        } else {
            if (mainBtn) mainBtn.style.display = (isTtsMode || isUpscaleMode) ? 'none' : '';
            if (ttsTopBtn) ttsTopBtn.style.display = isTtsMode ? '' : 'none';
            if (upscaleTopBtn) {
                upscaleTopBtn.style.display = isUpscaleMode ? '' : 'none';
                upscaleTopBtn.disabled = false;
                upscaleTopBtn.textContent = _t('upscaleGenBtn') || '🔍 开始高清放大';
            }
            if (mainBtn) mainBtn.disabled = false;
            if (ttsTopBtn) ttsTopBtn.disabled = false;
            if (genBtnRow) genBtnRow.style.display = 'none';
            _isQueuePaused = false;
        }
    }

    async function pollQueueStatus() {
        if (queuePollInFlight) return;
        queuePollInFlight = true;
        try {
            const res = await fetch(`${BASE}/api/queue/status`);
            const data = await res.json();
            renderQueueStatus(data);

            if (data?.current && data.current.status === 'running') {
                const isQueueManaged = data.current.mode === 'upscale' || data.current.mode === 'tts';
                if (!pollInterval && !isQueueManaged) startProgressPolling();
                if (isQueueManaged && pollInterval) stopProgressPolling();
                _isGeneratingFlag = true;
                _awaitingQueueStart = false;
                _currentQueueTaskId = data.current.id;
                _isQueuePaused = !!data.paused;
                _updateGenButtons();
            } else if (!data?.current) {
                if (_awaitingQueueStart) {
                    const hist = Array.isArray(data?.history) ? data.history : [];
                    const found = _currentQueueTaskId && hist.some(t => t && t.id === _currentQueueTaskId);
                    if (!found) {
                        _isQueuePaused = !!data.paused;
                        return;
                    }
                    _awaitingQueueStart = false;
                }
                if (pollInterval) stopProgressPolling();
                _isGeneratingFlag = false;
                _currentQueueTaskId = null;
                _isQueuePaused = !!data.paused;
                _updateGenButtons();
            } else {
                if (_awaitingQueueStart && data.current.status === 'queued') {
                    _isQueuePaused = !!data.paused;
                    return;
                }
                _awaitingQueueStart = false;
                _isQueuePaused = !!data.paused;
                _updateGenButtons();
            }

            const tasks = [];
            if (data?.current) tasks.push(data.current);
            if (Array.isArray(data?.history)) tasks.push(...data.history);
            let latestNewComplete = null;
            for (const task of tasks) {
                if (!task || !task.id || queueTaskTerminalSeen.has(task.id)) continue;
                if (task.status !== 'complete' && task.status !== 'error' && task.status !== 'cancelled') continue;
                queueTaskTerminalSeen.add(task.id);

                if (!queueInitialPollDone) continue;

                if (task.status === 'complete') {
                    addLog(_fmt('queueDoneLog', { label: task.label || task.id }));
                    latestNewComplete = latestNewComplete || task;
                    setTimeout(() => {
                        isLoadingHistory = false;
                        if (typeof fetchHistory === 'function') fetchHistory(1);
                    }, 300);
                    if (task.mode === 'tts' && task.result) {
                        _handleTtsQueueComplete(task.result);
                    }
                } else if (task.status === 'error') {
                    console.error('[QUEUE] Task error:', task.id, task.error);
                    const errDetail = task.error || 'unknown';
                    addLog(`❌ 任务失败 [${task.label || task.id}]: ${errDetail}`);
                    if (task.mode === 'tts') {
                        _handleTtsQueueError(errDetail);
                    } else {
                        const loader = document.getElementById('loading-txt');
                        if (loader) {
                            loader.style.display = 'flex';
                            loader.style.flexDirection = 'column';
                            loader.style.alignItems = 'center';
                            loader.style.gap = '8px';
                            loader.innerHTML = '';
                            const span = document.createElement('span');
                            span.style.cssText = 'color:#ff6b6b;font-size:13px;padding:12px;text-align:center;max-width:400px;word-break:break-word;';
                            span.textContent = `任务失败：${errDetail}`;
                            loader.appendChild(span);
                        }
                        _isGeneratingFlag = false;
                        _awaitingQueueStart = false;
                        _updateGenButtons();
                    }
                } else if (task.status === 'cancelled') {
                    addLog(_fmt('queueCancelLog', { label: task.label || task.id }));
                    _isGeneratingFlag = false;
                    _awaitingQueueStart = false;
                    _updateGenButtons();
                }
            }

            if (latestNewComplete && latestNewComplete.id !== lastAutoDisplayedQueueTaskId) {
                const rawPath = latestNewComplete.result?.image_paths?.[0] || latestNewComplete.result?.video_path;
                const outputType = latestNewComplete.result?.image_paths?.length ? 'image' : 'video';
                if (rawPath) {
                    lastAutoDisplayedQueueTaskId = latestNewComplete.id;
                    try { displayOutput(rawPath, outputType, { silentLog: true, replay: latestNewComplete }); } catch (_) {}
                }
            }
            queueInitialPollDone = true;
        } catch (e) {
            console.error('[QUEUE] pollQueueStatus error:', e);
            const list = document.getElementById('queue-list');
            const summary = document.getElementById('queue-summary');
            if (list && list.innerHTML.trim() === '') {
                list.innerHTML = `<div style="font-size:11px;color:var(--text-dim);padding:6px 0;">后端未连接，队列暂不可用</div>`;
            }
            if (summary) summary.textContent = '后端离线';
        }
        finally {
            queuePollInFlight = false;
        }
    }

    let _queuePollTimer = null;
    function startQueuePolling() {
        if (_queuePollTimer) return;
        _scheduleQueuePoll();
        pollQueueStatus();
    }
    function _scheduleQueuePoll() {
        if (_queuePollTimer) clearTimeout(_queuePollTimer);
        const interval = _isGeneratingFlag ? 1500 : 5000;
        _queuePollTimer = setTimeout(() => {
            pollQueueStatus().finally(_scheduleQueuePoll);
        }, interval);
    }

    // 系统状态轮询
    async function checkStatus() {
        if (statusPollInFlight) return;
        statusPollInFlight = true;
        try {
            const [h, g, p, sysGpus] = await Promise.all([
                fetch(`${BASE}/health`).then(r => r.json()).catch(() => ({status: "error"})),
                fetch(`${BASE}/api/gpu-info`).then(r => r.json()).catch(() => ({gpu_info: {}})),
                fetch(`${BASE}/api/generation/progress`).then(r => r.json()).catch(() => ({progress: 0})),
                fetch(`${BASE}/api/system/list-gpus`).then(r => r.json()).catch(() => ({gpus: []})),
            ]);
            
            const activeGpu = (sysGpus.gpus || []).find(x => x.active) || (sysGpus.gpus || [])[0] || {};
            const gpuName = activeGpu.name || g.gpu_info?.name || "GPU";
            
            const s = document.getElementById('sys-status');
            const indicator = document.getElementById('sys-indicator');
            
            const isReady = h.status === "ok" || h.status === "ready" || h.models_loaded;
            const backendActive = (p && p.progress > 0);
            
            if (_isGeneratingFlag || backendActive) {
                s.innerText = `${gpuName}: ${_t('sysBusy')}`;
                if(indicator) indicator.className = 'indicator-busy';
            } else {
                s.innerText = isReady ? `${gpuName}: ${_t('sysOnline')}` : `${gpuName}: ${_t('sysStarting')}`;
                if(indicator) indicator.className = isReady ? 'indicator-ready' : 'indicator-offline';
            }
            s.style.color = "var(--text-dim)";

            const vUsedMB = g.gpu_info?.vramUsed || 0;
            const vTotalMB = activeGpu.vram_mb || g.gpu_info?.vram || 32768; 
            const vUsedGB = vUsedMB / 1024;
            const vTotalGB = vTotalMB / 1024;
            
            const vramFill = document.getElementById('vram-fill');
            const vramText = document.getElementById('vram-text');
            if (vramFill) vramFill.style.width = (vUsedMB / vTotalMB * 100) + "%";
            if (vramText) vramText.innerText = `${vUsedGB.toFixed(1)} / ${vTotalGB.toFixed(0)} GB`;

            if (activeGpu.vram_mb && activeGpu.vram_mb > 0) {
                if (activeGpu.vram_mb < 6000) _upscaleAutoTileSize = 256;
                else if (activeGpu.vram_mb < 10000) _upscaleAutoTileSize = 400;
                else _upscaleAutoTileSize = 512;
                _updateTileSizeDisplay();
            }
        } catch(e) { const ss = document.getElementById('sys-status'); if (ss) ss.innerText = _t('sysOffline'); }
        finally { statusPollInFlight = false; }
    }
    try {
    let _statusPollTimer = null;
    function _scheduleStatusPoll() {
        if (_statusPollTimer) clearTimeout(_statusPollTimer);
        const interval = _isGeneratingFlag ? 3000 : 8000;
        _statusPollTimer = setTimeout(() => {
            checkStatus().finally(_scheduleStatusPoll);
        }, interval);
    }
    _scheduleStatusPoll();
    checkStatus();
    startQueuePolling();
    initDragAndDrop();
    listGpus();

    loadAllSettings();
    initSettingsPersistence();

    document.querySelectorAll('.setting-group').forEach(group => {
        const title = group.querySelector('.group-title');
        if (!title) return;
        const content = document.createElement('div');
        content.className = 'setting-group-content';
        let next = title.nextElementSibling;
        while (next) {
            const cur = next;
            next = next.nextElementSibling;
            content.appendChild(cur);
        }
        group.appendChild(content);
        const savedKey = 'collapsed_' + (title.textContent || '').trim().substring(0, 30);
        if (localStorage.getItem(savedKey) === '1') {
            title.classList.add('collapsed');
            content.classList.add('collapsed');
        }
        title.addEventListener('click', () => {
            title.classList.toggle('collapsed');
            content.classList.toggle('collapsed');
            localStorage.setItem(savedKey, title.classList.contains('collapsed') ? '1' : '0');
        });
    });

    updateResPreview();
    updateBatchResPreview();
    updateImgResPreview();
    refreshPromptPlaceholder();
    _restoreFeDebugState();
    } catch(e) { console.error('[INIT] Top-level init error:', e); }

function onUiLanguageChanged() {
        updateResPreview();
        updateBatchResPreview();
        updateImgResPreview();
        refreshPromptPlaceholder();
        if (typeof currentMode !== 'undefined' && currentMode === 'batch') {
            updateBatchSegments();
        }
        if (typeof currentMode !== 'undefined' && TTS_SUB_MODES.includes(currentMode)) {
            checkTtsStatus();
        }
        if (typeof currentMode !== 'undefined' && UPSCALE_SUB_MODES.includes(currentMode)) {
            checkUpscaleStatus();
        }
        updateLoraDropdown();
        updateBatchLoraDropdown();
        pollQueueStatus();
    
    }
    window.onUiLanguageChanged = onUiLanguageChanged;

    async function setOutputDir() {
        const dir = document.getElementById('global-out-dir').value.trim();
        localStorage.setItem('output_dir', dir);
        try {
            const res = await fetch(`${BASE}/api/system/set-dir`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ directory: dir })
            });
            if (res.ok) {
                addLog(`✅ 存储路径更新成功! 当前路径: ${dir || _t('defaultPath')}`);
                if (typeof resetHistoryAndReload === 'function') resetHistoryAndReload();
            }
        } catch (e) {
            addLog(`❌ 设置路径时连接异常: ${e.message}`);
        }
    }

    async function browseOutputDir() {
        try {
            const res = await fetch(`${BASE}/api/system/browse-dir`);
            const data = await res.json();
            if (data.status === "success" && data.directory) {
                document.getElementById('global-out-dir').value = data.directory;
                // auto apply immediately
                setOutputDir();
                addLog(`📂 检测到新路径，已自动套用！`);
            } else if (data.error) {
                addLog(`❌ 内部系统权限拦截了弹窗: ${data.error}`);
            }
        } catch (e) {
            addLog(`❌ 无法调出文件夹浏览弹窗, 请直接复制粘贴绝对路径。`);
        }
    }

    async function getOutputDir() {
        try {
            const res = await fetch(`${BASE}/api/system/get-dir`);
            const data = await res.json();
            if (data.directory && data.directory.indexOf('LTXDesktop') === -1 && document.getElementById('global-out-dir')) {
                document.getElementById('global-out-dir').value = data.directory;
            }
        } catch (e) {}
    }

    async function saveLoraDir() {
        const input = document.getElementById('lora-dir-input');
        const status = document.getElementById('lora-dir-status');
        if (!input || !status) return;
        
        const loraDir = input.value.trim();
        try {
            const res = await fetch(`${BASE}/api/lora-dir`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ loraDir: loraDir })
            });
            const data = await res.json();
            if (data && data.status === 'ok') {
                status.textContent = '✓ 已保存';
                status.style.color = '#4caf50';
                scanLoras();
                setTimeout(() => { status.textContent = ''; }, 3000);
            } else {
                status.textContent = '✗ 保存失败: ' + (data.message || JSON.stringify(data));
                status.style.color = '#f44336';
            }
        } catch (e) {
            status.textContent = '✗ 保存失败: ' + e.message;
            status.style.color = '#f44336';
        }
    }

    async function loadLoraDir() {
        try {
            const res = await fetch(`${BASE}/api/lora-dir`);
            const data = await res.json();
            if (data && document.getElementById('lora-dir-input')) {
                document.getElementById('lora-dir-input').value = data.loraDir || '';
                const hintEl = document.getElementById('lora-placement-hint');
                if (hintEl) {
                    const tpl = _t('loraPlacementHintWithDir');
                    const modelsDir = data.modelsDir || '';
                    if (modelsDir) {
                        hintEl.innerHTML = tpl.replace('{dir}', escapeHtmlAttr(modelsDir));
                    } else {
                        hintEl.innerHTML = _t('loraPlacementHint');
                    }
                }
            }
        } catch (e) {}
    }

    function isSwitchableDistilledCheckpoint(model) {
        const name = String(model?.name || '').toLowerCase();
        if (!name.endsWith('.safetensors')) return false;
        if (!name.includes('ltx')) return false;
        return !name.includes('ic-lora')
            && !name.includes('control')
            && !name.includes('upscaler')
            && !name.includes('vae');
    }

    function getSelectedModelCheckpointPath() {
        const select = document.getElementById('model-checkpoint-select');
        const explicit = (select?.value || localStorage.getItem(MODEL_CHECKPOINT_STORAGE_KEY) || '').trim();
        if (explicit) return explicit;
        return _resolveDefaultCheckpointPath();
    }

    function _resolveDefaultCheckpointPath() {
        const select = document.getElementById('model-checkpoint-select');
        if (!select) return '';
        const bf16Opt = Array.from(select.options).find(o => o.value && o.value.includes('distilled') && !o.value.includes('fp8'));
        if (bf16Opt && bf16Opt.value) return bf16Opt.value;
        const firstOpt = Array.from(select.options).find(o => o.value);
        if (firstOpt && firstOpt.value) return firstOpt.value;
        return '';
    }

    function saveSelectedModelCheckpoint() {                                  // 2026-06-09: 改 function 声明 + window 暴露,修复 hoisting(同 IIFE 内更早位置 onVidModelChange 内调用)
        const select = document.getElementById('model-checkpoint-select');
        const status = document.getElementById('model-checkpoint-status');
        const value = (select?.value || '').trim();
        if (value) {
            localStorage.setItem(MODEL_CHECKPOINT_STORAGE_KEY, value);
        } else {
            localStorage.removeItem(MODEL_CHECKPOINT_STORAGE_KEY);
        }
        // 2026-06-10 修复: 任一 select 变化都同步另一个,保证两个 select 始终保持一致
        try {
            const vidSelect = document.getElementById('vid-model-select');
            if (vidSelect && typeof syncVidModelFromGlobal === 'function') {
                syncVidModelFromGlobal();
            }
        } catch (_) {}
        if (status) {
            status.textContent = value ? _t('modelCheckpointSaved') : _t('modelCheckpointHint');
            status.style.color = value ? '#4caf50' : 'var(--text-dim)';
            if (value) setTimeout(() => {
                status.textContent = _t('modelCheckpointHint');
                status.style.color = 'var(--text-dim)';
            }, 2500);
        }
    }
    window.saveSelectedModelCheckpoint = saveSelectedModelCheckpoint;

    // ★ 兼容旧 HTML 诊断脚本:loadModelRegistry 在 3402d56 之后被 _refreshModelRequirement
    //   取代,这里保留一个空实现,避免 [DEBUG] JS加载异常 - 缺失函数: loadModelRegistry。
    //   真正的必需模型检查由 _refreshModelRequirement() 在 switchMode 末尾自动调用完成。
function loadModelRegistry() {
        try { if (typeof _refreshModelRequirement === 'function') _refreshModelRequirement(); } catch (e) { /* ignore */ }
    
    }
    window.loadModelRegistry = loadModelRegistry;

async function loadModelCheckpoints() {
        const select = document.getElementById('model-checkpoint-select');
        const status = document.getElementById('model-checkpoint-status');
        if (!select) return;
        const saved = localStorage.getItem(MODEL_CHECKPOINT_STORAGE_KEY) || '';
        try {
            if (status) {
                status.textContent = '...';
                status.style.color = 'var(--text-dim)';
            }
            const res = await fetch(`${BASE}/api/models/registry`);
            const data = await res.json();
            const allModels = _collectLocalModelsFromRegistry(data);
            const models = allModels.filter(_isVideoCheckpoint);
            select.innerHTML = '';
            const defOpt = document.createElement('option');
            defOpt.value = '';
            defOpt.textContent = _t('modelCheckpointDefault');
            select.appendChild(defOpt);
            models.forEach((model) => {
                const opt = document.createElement('option');
                opt.value = model.path;
                opt.textContent = _beautifyModelName(model.name);
                select.appendChild(opt);
            });
            if (saved && models.some((model) => model.path === saved)) {
                select.value = saved;
            } else if (saved) {
                localStorage.removeItem(MODEL_CHECKPOINT_STORAGE_KEY);
                select.value = '';
            }
            if (status) {
                status.textContent = models.length ? _t('modelCheckpointHint') : _t('modelCheckpointNone');
                status.style.color = 'var(--text-dim)';
            }
        } catch (e) {
            if (status) {
                status.textContent = `${_t('modelCheckpointLoadFail')}: ${e.message}`;
                status.style.color = '#f44336';
            }
        }
        if (typeof syncVidModelFromGlobal === 'function') syncVidModelFromGlobal();
    
    }
    window.loadModelCheckpoints = loadModelCheckpoints;

    let _coreModelsData = null;

async function loadCoreModels() {
        const list = document.getElementById('core-models-list');
        if (!list) return;
        list.innerHTML = '<div style="font-size:10px;color:var(--text-dim);">...</div>';
        try {
            const res = await fetch(`${BASE}/api/models/status`);
            const data = await res.json();
            _coreModelsData = data;
            list.innerHTML = '';
            for (const m of data.models) {
                const row = document.createElement('div');
                row.style.cssText = 'display:flex;align-items:center;gap:6px;padding:4px 6px;border-radius:5px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.05);';
                const nameSpan = document.createElement('span');
                nameSpan.style.cssText = 'font-size:10px;font-weight:600;color:var(--text-main);min-width:60px;';
                nameSpan.textContent = m.name;
                const descSpan = document.createElement('span');
                descSpan.style.cssText = 'font-size:9px;color:var(--text-dim);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
                descSpan.textContent = m.description || '';
                descSpan.title = m.description || '';
                const sizeSpan = document.createElement('span');
                sizeSpan.style.cssText = 'font-size:9px;color:var(--text-dim);flex-shrink:0;';
                sizeSpan.textContent = (m.expected_size / (1024**3)).toFixed(1) + 'GB';
                const statusSpan = document.createElement('span');
                statusSpan.style.cssText = 'font-size:9px;flex-shrink:0;font-weight:600;';
                if (m.downloaded) {
                    statusSpan.textContent = '✓';
                    statusSpan.style.color = '#4CAF50';
                } else {
                    const dlBtn = document.createElement('button');
                    dlBtn.style.cssText = 'font-size:9px;padding:2px 8px;border-radius:3px;cursor:pointer;font-weight:600;border:1px solid;background:rgba(21,101,192,0.2);border-color:rgba(25,118,210,0.5);color:#42A5F5;';
                    dlBtn.textContent = _t('coreModelDownload') || '下载';
                    dlBtn.dataset.modelType = m.id;
                    dlBtn.onclick = function() { startCoreModelDownload(m.id, dlBtn); 
    }
    window.loadCoreModels = loadCoreModels;
                    row.appendChild(nameSpan);
                    row.appendChild(descSpan);
                    row.appendChild(sizeSpan);
                    row.appendChild(dlBtn);
                    list.appendChild(row);
                    continue;
                }
                row.appendChild(nameSpan);
                row.appendChild(descSpan);
                row.appendChild(sizeSpan);
                row.appendChild(statusSpan);
                list.appendChild(row);
            }
        } catch (e) {
            list.innerHTML = '<div style="font-size:10px;color:#f44336;">' + e.message + '</div>';
        }
    };

    async function startCoreModelDownload(modelType, btn) {
        const useMirror = document.getElementById('use-mirror-cb')?.checked || false;
        btn.textContent = '...';
        btn.disabled = true;
        btn.style.opacity = '0.6';
        try {
            const res = await fetch(`${BASE}/api/models/download`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({modelTypes: [modelType], useMirror: useMirror}),
            });
            const data = await res.json();
            if (data.sessionId) {
                pollCoreModelDownload(data.sessionId, modelType, btn);
            } else {
                btn.textContent = _t('coreModelFailed') || '失败';
                btn.style.color = '#f44336';
            }
        } catch (e) {
            btn.textContent = _t('coreModelFailed') || '失败';
            btn.style.color = '#f44336';
        }
    }

    function pollCoreModelDownload(sessionId, modelType, btn) {
        const interval = setInterval(async () => {
            try {
                const res = await fetch(`${BASE}/api/models/download/progress?sessionId=${sessionId}`);
                const data = await res.json();
                if (data.status === 'complete') {
                    clearInterval(interval);
                    btn.textContent = '✓';
                    btn.style.background = 'rgba(76,175,80,0.15)';
                    btn.style.borderColor = 'rgba(76,175,80,0.3)';
                    btn.style.color = '#4CAF50';
                    btn.disabled = true;
                    loadModelCheckpoints();
                } else if (data.status === 'error') {
                    clearInterval(interval);
                    btn.textContent = _t('coreModelFailed') || '失败';
                    btn.style.color = '#f44336';
                    btn.disabled = false;
                } else if (data.status === 'downloading') {
                    const pct = data.total_progress ? data.total_progress.toFixed(0) + '%' : '...';
                    btn.textContent = pct;
                }
            } catch (e) {
                clearInterval(interval);
                btn.textContent = _t('coreModelFailed') || '失败';
                btn.style.color = '#f44336';
                btn.disabled = false;
            }
        }, 2000);
    }

    // ── 2026-06-10: 按需下载入口检测 ───────────────────────────────
    // 每个功能入口(文生图/文生视频/TTS)激活时,检查"必需模型"是否已下载。
    // 若未下载,在参数面板顶部插入横幅,提供"立即下载"按钮。
    // (前端不再做完整模型管理 —— 模型管理是启动器的职责)
    // ─────────────────────────────────────────────────────────────
    let _activeModelBanner = null;   // 当前展示的横幅 DOM 引用

    async function _fetchRegistryForBanner() {
        // 轻量读取,只取核心字段,不订阅
        try {
            const res = await fetch(`${BASE}/api/models/registry`);
            if (!res.ok) return null;
            const data = await res.json();
            return data.models || [];
        } catch (e) {
            return null;
        }
    }

    // 根据 mode 找"必需"checkpoint (返回 model 字典或 null)
    function _findRequiredModelForMode(models, mode) {
        if (!Array.isArray(models) || models.length === 0) return null;
        // 优先级:
        // 1. tags 包含 'recommended' + pipeline_mode 匹配
        // 2. pipeline_mode 匹配 + checkpoint 类
        // 3. tags 包含 'image'/'video'/'tts' + checkpoint 类
        const isVideo = mode === 'video' || (typeof VIDEO_SUB_MODES !== 'undefined' && VIDEO_SUB_MODES.includes(mode));
        const isTts = mode === 'tts' || (typeof TTS_SUB_MODES !== 'undefined' && TTS_SUB_MODES.includes(mode));
        const isUpscale = mode === 'upscale' || (typeof UPSCALE_SUB_MODES !== 'undefined' && UPSCALE_SUB_MODES.includes(mode));
        const candidates = models.filter(m =>
            m.model_category === 'checkpoint' &&
            !m.downloaded &&
            (m.pipeline_mode === mode ||
             (isVideo && m.pipeline_mode === 'video') ||
             (isTts && m.pipeline_mode === 'tts') ||
             (isUpscale && m.pipeline_mode === 'upscale'))
        );
        if (candidates.length === 0) return null;
        // 推荐优先
        const rec = candidates.find(m => m.tags && m.tags.includes('recommended'));
        return rec || candidates[0];
    }

    function _hideModelRequiredBanner() {
        if (_activeModelBanner && _activeModelBanner.parentNode) {
            _activeModelBanner.parentNode.removeChild(_activeModelBanner);
        }
        _activeModelBanner = null;
    }

    // 在指定 panel 顶部插入"需先下载 X 模型"横幅
    function _showModelRequiredBanner(panelId, model) {
        _hideModelRequiredBanner();
        const panel = document.getElementById(panelId);
        if (!panel || !model) return;
        const banner = document.createElement('div');
        banner.id = 'model-required-banner';
        banner.style.cssText = [
            'margin-bottom:10px',
            'padding:10px 12px',
            'border-radius:6px',
            'background:linear-gradient(135deg, rgba(255,152,0,0.12), rgba(255,193,7,0.06))',
            'border:1px solid rgba(255,152,0,0.4)',
            'color:var(--text-main)',
            'display:flex',
            'align-items:center',
            'gap:10px',
            'font-size:11px',
            'line-height:1.5',
        ].join(';');

        const text = document.createElement('div');
        text.style.cssText = 'flex:1;';
        const nameSpan = document.createElement('div');
        nameSpan.style.cssText = 'font-weight:700;color:#FF9800;';
        nameSpan.textContent = model.name || model.model_id;
        const descSpan = document.createElement('div');
        descSpan.style.cssText = 'color:var(--text-dim);font-size:10px;margin-top:2px;';
        const sizeText = model.size_gb ? `${model.size_gb} GB` : '';
        const vramText = model.min_vram_gb ? `需显存 ≥ ${model.min_vram_gb} GB` : '';
        const meta = [sizeText, vramText].filter(Boolean).join(' · ');
        descSpan.textContent = (_t('modelRequiredHint') || '需要先下载此模型才能使用此功能') + (meta ? `  ·  ${meta}` : '');

        text.appendChild(nameSpan);
        text.appendChild(descSpan);

        const dlBtn = document.createElement('button');
        dlBtn.style.cssText = [
            'flex-shrink:0',
            'padding:6px 14px',
            'background:#FF9800',
            'border:none',
            'color:#000',
            'border-radius:5px',
            'cursor:pointer',
            'font-weight:700',
            'font-size:11px',
        ].join(';');
        dlBtn.textContent = _t('modelRequiredDownload') || '立即下载';
        dlBtn.onclick = () => triggerModelDownload(model.model_id, dlBtn, () => {
            // 下载成功 → 自动刷新检查
            _refreshModelRequirement();
        });

        const closeBtn = document.createElement('button');
        closeBtn.style.cssText = 'background:none;border:none;color:var(--text-dim);cursor:pointer;font-size:16px;padding:0 4px;';
        closeBtn.textContent = '✕';
        closeBtn.title = _t('close') || '关闭';
        closeBtn.onclick = () => _hideModelRequiredBanner();

        banner.appendChild(text);
        banner.appendChild(dlBtn);
        banner.appendChild(closeBtn);
        panel.insertBefore(banner, panel.firstChild);
        _activeModelBanner = banner;
    }

    // 检测并展示/隐藏当前 mode 的必需模型横幅
    //   _modelReqCheckToken 已在文件顶部声明,避免 loadAllSettings 同步触发时出现 TDZ。
    async function _refreshModelRequirement() {
        const token = ++_modelReqCheckToken;
        const mode = typeof currentMode !== 'undefined' ? currentMode : null;
        if (!mode) {
            _hideModelRequiredBanner();
            return;
        }
        const models = await _fetchRegistryForBanner();
        if (token !== _modelReqCheckToken) return;  // 被更新的请求覆盖
        if (!models) {
            _hideModelRequiredBanner();
            return;
        }
        const required = _findRequiredModelForMode(models, mode);
        if (required) {
            const panelId = _panelIdForMode(mode);
            if (panelId) _showModelRequiredBanner(panelId, required);
            else _hideModelRequiredBanner();
        } else {
            _hideModelRequiredBanner();
        }
    }

    function _panelIdForMode(mode) {
        if (mode === 'image') return 'image-opts';
        if (mode === 'video' || (typeof VIDEO_SUB_MODES !== 'undefined' && VIDEO_SUB_MODES.includes(mode))) return 'video-opts';
        if (mode === 'tts' || (typeof TTS_SUB_MODES !== 'undefined' && TTS_SUB_MODES.includes(mode))) return 'tts-opts';
        if (mode === 'upscale' || (typeof UPSCALE_SUB_MODES !== 'undefined' && UPSCALE_SUB_MODES.includes(mode))) return 'upscale-opts';
        if (mode === 'batch' || mode === 'motion') return 'batch-opts';
        return null;
    }

    // 触发下载(供横幅按钮调用) —— 复用原 pollModelDownload 逻辑
    async function triggerModelDownload(modelId, btn, onSuccess) {
        const useMirror = document.getElementById('use-mirror-cb')?.checked || false;
        const origText = btn.textContent;
        btn.textContent = _t('downloading') || '下载中...';
        btn.disabled = true;
        btn.style.opacity = '0.6';
        try {
            const res = await fetch(`${BASE}/api/models/registry/download`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({model_id: modelId, use_mirror: useMirror}),
            });
            const data = await res.json();
            if (data.status === 'already_exists') {
                btn.textContent = '✓';
                btn.style.background = 'rgba(76,175,80,0.5)';
                if (onSuccess) onSuccess();
                return;
            }
            if (data.status === 'started') {
                btn.textContent = _t('downloading') || '下载中...';
                pollModelDownload(modelId, btn, onSuccess);
                return;
            }
            btn.textContent = _t('downloadFailed') || '下载失败';
            btn.style.background = 'rgba(244,67,54,0.4)';
            btn.disabled = false;
        } catch (e) {
            btn.textContent = _t('downloadFailed') || '下载失败';
            btn.style.background = 'rgba(244,67,54,0.4)';
            btn.disabled = false;
        }
    }

    function pollModelDownload(modelId, btn, onSuccess) {
        const interval = setInterval(async () => {
            try {
                const res = await fetch(`${BASE}/api/models/registry/status`);
                const data = await res.json();
                if (!data.active && data.model_id === modelId) {
                    clearInterval(interval);
                    if (data.error) {
                        btn.textContent = _t('downloadFailed') || '下载失败';
                        btn.style.background = 'rgba(244,67,54,0.4)';
                        btn.disabled = false;
                    } else {
                        btn.textContent = '✓';
                        btn.style.background = 'rgba(76,175,80,0.5)';
                        // 通知上层
                        if (onSuccess) onSuccess();
                        // 刷新相关数据
                        if (typeof loadModelCheckpoints === 'function') loadModelCheckpoints();
                        if (typeof loadCoreModels === 'function') loadCoreModels();
                        if (typeof scanLoras === 'function') scanLoras();
                    }
                } else if (data.active && data.model_id === modelId) {
                    const pct = Math.round(data.progress || 0);
                    btn.textContent = `${pct}%`;
                }
            } catch (e) {
                clearInterval(interval);
                btn.textContent = _t('downloadFailed') || '下载失败';
                btn.style.background = 'rgba(244,67,54,0.4)';
                btn.disabled = false;
            }
        }, 3000);
    }

    // ─────────────────────────────────────────────────────────────

    function switchMode(m) {
        const isVideoCategory = m === 'video' || VIDEO_SUB_MODES.includes(m);
        const isTtsCategory = m === 'tts' || TTS_SUB_MODES.includes(m);
        const isUpscaleCategory = m === 'upscale' || UPSCALE_SUB_MODES.includes(m);
        if (m === 'video') {
            m = _videoSubMode || 'i2v';
            _videoSubMode = m;
            currentMode = m;
        } else if (VIDEO_SUB_MODES.includes(m)) {
            _videoSubMode = m;
            currentMode = m;
        } else if (m === 'tts') {
            m = _ttsSubMode || 'text_only';
            _ttsSubMode = m;
            currentMode = m;
        } else if (TTS_SUB_MODES.includes(m)) {
            _ttsSubMode = m;
            currentMode = m;
        } else if (m === 'upscale') {
            m = _upscaleSubMode || 'upscale_video';
            _upscaleSubMode = m;
            currentMode = m;
        } else if (UPSCALE_SUB_MODES.includes(m)) {
            _upscaleSubMode = m;
            currentMode = m;
        } else {
            currentMode = m;
        }
        scheduleSave();

        const tabVideo = document.getElementById('tab-video');
        const tabImage = document.getElementById('tab-image');
        const tabTts = document.getElementById('tab-tts');
        const tabUpscale = document.getElementById('tab-upscale');
        if (tabVideo) tabVideo.classList.toggle('active', isVideoCategory);
        if (tabImage) tabImage.classList.toggle('active', m === 'image');
        if (tabTts) tabTts.classList.toggle('active', isTtsCategory);
        if (tabUpscale) tabUpscale.classList.toggle('active', isUpscaleCategory);

        const subTabs = document.getElementById('video-sub-tabs');
        if (subTabs) subTabs.style.display = isVideoCategory ? 'block' : 'none';

        const upscaleSubTabs = document.getElementById('upscale-sub-tabs');
        if (upscaleSubTabs) upscaleSubTabs.style.display = isUpscaleCategory ? 'block' : 'none';

        document.querySelectorAll('.video-sub-tab').forEach(btn => {
            const subId = btn.id.replace('subtab-', '');
            btn.classList.toggle('active', subId === _videoSubMode);
        });

        document.querySelectorAll('.tts-sub-tab').forEach(btn => {
            const subId = btn.id.replace('tts-subtab-', '');
            btn.classList.toggle('active', subId === _ttsSubMode);
        });

        document.querySelectorAll('.upscale-sub-tab').forEach(btn => {
            const subId = btn.id.replace('upscale-subtab-', '');
            btn.classList.toggle('active', subId === _upscaleSubMode.replace('upscale_', ''));
        });

        const videoOpts = document.getElementById('video-opts');
        const batchOpts = document.getElementById('batch-opts');
        const motionOpts = document.getElementById('motion-opts');
        const imageOpts = document.getElementById('image-opts');
        const ttsOpts = document.getElementById('tts-opts');
        const upscaleOpts = document.getElementById('upscale-opts');
        if (videoOpts) videoOpts.style.display = (isVideoCategory && _videoSubMode === 'i2v') ? 'block' : 'none';
        if (batchOpts) batchOpts.style.display = (isVideoCategory && _videoSubMode === 'batch') ? 'block' : 'none';
        if (motionOpts) motionOpts.style.display = (isVideoCategory && _videoSubMode === 'motion') ? 'block' : 'none';
        if (imageOpts) imageOpts.style.display = m === 'image' ? 'block' : 'none';
        if (ttsOpts) ttsOpts.style.display = isTtsCategory ? 'block' : 'none';
        if (upscaleOpts) upscaleOpts.style.display = isUpscaleCategory ? 'block' : 'none';

        if (isTtsCategory) _updateTtsSubModeUI();
        if (isUpscaleCategory) _updateUpscaleSubModeUI();

        const mainBtn = document.getElementById('mainBtn');
        const ttsTopBtn = document.getElementById('tts-top-btn');
        const upscaleTopBtn = document.getElementById('upscale-top-btn');
        const renderBtnGroup = document.getElementById('render-btn-group');
        if (isTtsCategory) {
            if (mainBtn) mainBtn.style.display = 'none';
            if (ttsTopBtn) ttsTopBtn.style.display = '';
            if (upscaleTopBtn) upscaleTopBtn.style.display = 'none';
            if (renderBtnGroup) renderBtnGroup.style.display = '';
        } else if (isUpscaleCategory) {
            if (mainBtn) mainBtn.style.display = 'none';
            if (ttsTopBtn) ttsTopBtn.style.display = 'none';
            if (upscaleTopBtn) upscaleTopBtn.style.display = '';
            if (renderBtnGroup) renderBtnGroup.style.display = '';
        } else {
            if (mainBtn) mainBtn.style.display = '';
            if (ttsTopBtn) ttsTopBtn.style.display = 'none';
            if (upscaleTopBtn) upscaleTopBtn.style.display = 'none';
            if (renderBtnGroup) renderBtnGroup.style.display = '';
        }

        const pc = document.getElementById('prompt-container');
        if (pc) pc.style.display = (isTtsCategory || isUpscaleCategory) ? 'none' : '';

        const seedSettings = document.getElementById('seed-settings');
        if (seedSettings) seedSettings.style.display = (isTtsCategory || isUpscaleCategory) ? 'none' : '';

        const mainTabsSection = document.getElementById('main-tabs-section');
        if (mainTabsSection) {
            mainTabsSection.style.borderBottom = (isTtsCategory || isUpscaleCategory) ? 'none' : '';
            mainTabsSection.style.paddingBottom = (isTtsCategory || isUpscaleCategory) ? '0' : '';
        }

        if (m === 'batch') updateBatchSegments();
        if (isTtsCategory) checkTtsStatus();
        if (isUpscaleCategory) checkUpscaleStatus();
        if (m === 'image') { updateImgResPreview(); loadImgModelList(); }
        refreshPromptPlaceholder();

        // 2026-06-10: 切换 mode 后检查必需模型 —— 缺失则显示横幅
        if (typeof _refreshModelRequirement === 'function') _refreshModelRequirement();
    }

function switchVideoSubMode(sub) {
        switchMode(sub);
    
    }
    window.switchVideoSubMode = switchVideoSubMode;

function setVideoTransferMode(mode) {
        window._videoTransferMode = mode || 'action';
        const select = document.getElementById('motion-conditioning-type');
        const actionBtn = document.getElementById('motion-mode-action');
        const cameraBtn = document.getElementById('motion-mode-camera');
        const repaintBtn = document.getElementById('motion-mode-repaint');
        const actionControl = document.getElementById('motion-action-control-section');
        const imageSection = document.getElementById('motion-image-section');
        const strength = document.getElementById('motion-strength');
        const strengthVal = document.getElementById('motion-strength-val');
        const strengthLabel = document.getElementById('motion-strength-label');
        const isAction = window._videoTransferMode === 'action';
        const isRepaint = window._videoTransferMode === 'repaint';
        if (select) select.value = isAction ? 'pose' : 'video';
        if (actionControl) actionControl.style.display = isAction ? 'block' : 'none';
        if (imageSection) imageSection.style.display = isRepaint ? 'none' : 'block';
        if (strength) {
            strength.min = '0';
            strength.max = '2';
            strength.step = '0.05';
            strength.value = '1';
            if (strengthVal) strengthVal.textContent = strength.value;
        }
        if (strengthLabel) {
            strengthLabel.textContent = isRepaint ? '重绘幅度' : _t('motionControlStrength');
        }

        const activeStyle = (btn, active) => {
            if (!btn) return;
            btn.style.background = active ? 'var(--accent)' : 'var(--panel-2)';
            btn.style.color = active ? '#fff' : 'var(--text-sub)';
            btn.style.borderColor = active ? 'var(--accent)' : 'var(--border)';
        
    }
    window.setVideoTransferMode = setVideoTransferMode;
        activeStyle(actionBtn, isAction);
        activeStyle(cameraBtn, window._videoTransferMode === 'camera');
        activeStyle(repaintBtn, isRepaint);
    };

function toggleHelpBubble(id) {
        const el = document.getElementById(id);
        if (!el) return;
        const isVisible = el.style.display !== 'none';
        document.querySelectorAll('.help-bubble-content').forEach(b => b.style.display = 'none');
        if (!isVisible) el.style.display = 'block';
    
    }
    window.toggleHelpBubble = toggleHelpBubble;
    document.addEventListener('click', function(e) {
        if (!e.target.closest('.help-bubble') && !e.target.closest('.help-bubble-content')) {
            document.querySelectorAll('.help-bubble-content').forEach(b => b.style.display = 'none');
        }
    });

    function refreshPromptPlaceholder() {
        const pe = document.getElementById('prompt');
        if (!pe) return;
        pe.placeholder =
            TTS_SUB_MODES.includes(currentMode) ? '切换到 TTS 语音模式时此框不参与生成' :
            _t('promptPlaceholder');
    }

    // ─── TTS 语音合成 ──────────────────────────────────────────────────────────
    let _ttsRefB64 = null;
    let _pendingTtsTaskId = null;

    function _handleTtsQueueComplete(result) {
        const resultSec = document.getElementById('tts-result-section');
        const btn = document.getElementById('tts-top-btn');

        if (result && result.audio_url) {
            const audioUrl = `${BASE}${result.audio_url}`;
            const player = document.getElementById('tts-audio-player');
            const dlLink = document.getElementById('tts-download-link');

            if (player) { player.src = audioUrl; player.load(); }
            if (dlLink) {
                try {
                    fetch(audioUrl).then(r => r.blob()).then(blob => {
                        dlLink.href = URL.createObjectURL(blob);
                    }).catch(() => {
                        dlLink.href = audioUrl;
                    });
                    dlLink.download = result.audio_path || 'tts_output.wav';
                } catch (e) {
                    dlLink.href = audioUrl;
                    dlLink.download = result.audio_path || 'tts_output.wav';
                }
            }
            if (resultSec) resultSec.style.display = 'block';
            addLog(`✅ TTS: ${result.audio_path} (${result.sample_rate || 0} Hz)`);
        }

        if (btn) { btn.disabled = false; btn.textContent = _t('ttsGenBtn'); }
        _pendingTtsTaskId = null;
    }

    function _handleTtsQueueError(errDetail) {
        const btn = document.getElementById('tts-top-btn');
        addLog(`❌ TTS 任务失败: ${errDetail}`);

        if (btn) { btn.disabled = false; btn.textContent = _t('ttsGenBtn'); }
        _pendingTtsTaskId = null;
    }

function onTtsModeChange() {
        _updateTtsSubModeUI();
    
    }
    window.onTtsModeChange = onTtsModeChange;

function switchTtsSubMode(sub) {
        switchMode(sub);
    
    }
    window.switchTtsSubMode = switchTtsSubMode;

function switchUpscaleSubMode(sub) {
        const modeMap = { 'video': 'upscale_video', 'image': 'upscale_image' 
    }
    window.switchUpscaleSubMode = switchUpscaleSubMode;
        switchMode(modeMap[sub] || sub);
    };

    function _updateUpscaleSubModeUI() {
        const isVideo = _upscaleSubMode === 'upscale_video';
        const videoSec = document.getElementById('upscale-video-section');
        const imageSec = document.getElementById('upscale-image-section');
        const fpsSec = document.getElementById('upscale-fps-section');
        if (videoSec) videoSec.style.display = isVideo ? 'block' : 'none';
        if (imageSec) imageSec.style.display = isVideo ? 'none' : 'block';
        if (fpsSec) fpsSec.style.display = isVideo ? 'block' : 'none';
    }

    let _upscaleStatus = null;

    async function checkUpscaleStatus() {
        const bar = document.getElementById('upscale-status-bar');
        if (!bar) return;
        try {
            const res = await fetch(`${BASE}/api/upscale/status`);
            _upscaleStatus = await res.json();
            const re = _upscaleStatus.realesrgan;
            const ltx = _upscaleStatus.ltx_upscaler;
            const gpu = _upscaleStatus.gpu;
            const parts = [];
            let realesrganAvailable = false;
            if (re) { parts.push('✅ Real-ESRGAN 可用'); realesrganAvailable = true; }
            else parts.push('❌ Real-ESRGAN 未安装');
            if (ltx) parts.push('✅ LTX 快速放大可用');
            else parts.push('⚠️ LTX 快速放大不可用');
            if (gpu) {
                if (gpu.device === 'cuda') {
                    parts.push(`🚀 GPU: ${gpu.name} (${gpu.vram_mb}MB)`);
                } else if (gpu.device === 'cpu') {
                    parts.push('⚠️ 仅 CPU 模式（未检测到CUDA）');
                }
            }

            bar.textContent = '';
            bar.appendChild(document.createTextNode(parts.join('  |  ')));

            if (!realesrganAvailable) {
                const installBtn = document.createElement('button');
                installBtn.style.cssText = 'margin-left:8px;font-size:10px;padding:2px 8px;border-radius:4px;cursor:pointer;font-weight:600;border:1px solid rgba(25,118,210,0.5);background:rgba(21,101,192,0.2);color:#42A5F5;white-space:nowrap;';
                installBtn.textContent = _t('envInstall');
                installBtn.onclick = async () => {
                    installBtn.textContent = _t('envFixing');
                    installBtn.disabled = true;
                    installBtn.style.opacity = '0.6';
                    try {
                        const fixRes = await fetch(`${BASE}/api/system/env-fix`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ component: 'realesrgan' }),
                        });
                        const data = await fixRes.json();
                        if (data.status === 'success') {
                            installBtn.textContent = _t('envFixDone');
                            installBtn.style.color = '#4CAF50';
                            installBtn.style.borderColor = 'rgba(76,175,80,0.5)';
                            installBtn.style.background = 'rgba(76,175,80,0.2)';
                            setTimeout(() => checkUpscaleStatus(), 1000);
                        } else {
                            installBtn.textContent = _t('envFixFailed');
                            installBtn.style.color = '#f44336';
                            addLog((_t('upscaleInstallFailed') || '安装失败') + ': ' + (data.error || 'unknown'));
                        }
                    } catch (e) {
                        installBtn.textContent = _t('envFixFailed');
                        installBtn.style.color = '#f44336';
                    }
                };
                bar.appendChild(installBtn);
            }

            onUpscaleEngineChange();
            if (gpu) {
                if (gpu.device === 'cuda' && gpu.vram_mb) {
                    if (gpu.vram_mb < 6000) _upscaleAutoTileSize = 256;
                    else if (gpu.vram_mb < 10000) _upscaleAutoTileSize = 400;
                    else _upscaleAutoTileSize = 512;
                } else {
                    _upscaleAutoTileSize = 0;
                }
            }
            _updateTileSizeDisplay();
        } catch (e) {
            bar.textContent = '❌ 无法检测放大引擎状态';
        }
        onUpscaleRatioChange();
    }

    function _updateTileSizeDisplay() {
        const slider = document.getElementById('upscale-tile-size');
        const valEl = document.getElementById('upscale-tile-val');
        const hintEl = document.getElementById('upscale-tile-hint');
        if (!slider || !valEl) return;
        const v = parseInt(slider.value) || 0;
        if (v === 0) {
            if (_upscaleAutoTileSize > 0) {
                valEl.textContent = _fmt('upscaleTileAuto', { val: _upscaleAutoTileSize });
            } else {
                valEl.textContent = _t('upscaleTileAutoCpu');
            }
        } else {
            valEl.textContent = v;
        }
    }

    function onUpscaleEngineChange() {
        const engineEl = document.getElementById('upscale-engine');
        const modelSec = document.getElementById('upscale-model-section');
        const denoiseSec = document.getElementById('upscale-denoise-section');
        const tileSec = document.getElementById('upscale-tile-section');
        const fpsSec = document.getElementById('upscale-fps-section');
        const engine = engineEl ? engineEl.value : 'realesrgan';
        if (modelSec) modelSec.style.display = engine === 'realesrgan' ? 'block' : 'none';
        if (denoiseSec) denoiseSec.style.display = engine === 'realesrgan' ? 'block' : 'none';
        if (tileSec) tileSec.style.display = engine === 'realesrgan' ? 'block' : 'none';
        const typeEl = document.getElementById('upscale-type');
        const isVideo = typeEl ? typeEl.value === 'video' : true;
        if (fpsSec) fpsSec.style.display = isVideo ? 'block' : 'none';
    }

    function onUpscaleFpsChange() {
        const fpsEl = document.getElementById('upscale-fps');
        const customEl = document.getElementById('upscale-fps-custom');
        if (!fpsEl || !customEl) return;
        customEl.style.display = fpsEl.value === 'custom' ? 'block' : 'none';
    }

    // UPSCALE_RATIO_PRESETS / UPSCALE_QUALITY_PRESETS 已在文件顶部声明。
    function _computeResolutionForRatio(ratio, quality) {
        const r = UPSCALE_RATIO_PRESETS[ratio];
        const q = UPSCALE_QUALITY_PRESETS[quality];
        if (!r || !q) return null;
        const { rw, rh } = r;
        const shortSide = q.shortSide;
        if (rw >= rh) {
            const w = Math.round(shortSide * rw / rh);
            return { w, h: shortSide };
        } else {
            const h = Math.round(shortSide * rh / rw);
            return { w: shortSide, h };
        }
    }

    function onUpscaleRatioChange() {                                          // 2026-06-09: 改 function 声明 + window 暴露,修复 hoisting
        const ratio = document.getElementById('upscale-ratio')?.value || 'original';
        const qualitySec = document.getElementById('upscale-quality-section');
        const scaleSec = document.getElementById('upscale-scale-section');
        const customSec = document.getElementById('upscale-custom-size-section');
        const resizeModeSec = document.getElementById('upscale-resize-mode-section');
        if (qualitySec) qualitySec.style.display = 'none';
        if (scaleSec) scaleSec.style.display = 'none';
        if (customSec) customSec.style.display = 'none';
        if (resizeModeSec) resizeModeSec.style.display = 'none';
        if (ratio === 'original') {
            if (qualitySec) qualitySec.style.display = 'block';
            onUpscaleQualityChange();
        } else if (UPSCALE_RATIO_PRESETS[ratio]) {
            if (qualitySec) qualitySec.style.display = 'block';
            if (resizeModeSec) resizeModeSec.style.display = 'block';
            onUpscaleQualityChange();
            onUpscaleResizeModeChange();
        } else if (ratio === 'scale') {
            if (scaleSec) scaleSec.style.display = 'flex';
        } else if (ratio === 'custom') {
            if (customSec) customSec.style.display = 'block';
            if (resizeModeSec) resizeModeSec.style.display = 'block';
            onUpscaleResizeModeChange();
        }
    }
    window.onUpscaleRatioChange = onUpscaleRatioChange;

    function onUpscaleQualityChange() {                                        // 同上
        const ratio = document.getElementById('upscale-ratio')?.value || 'original';
        const quality = document.getElementById('upscale-quality')?.value || '1080p';
        const preview = document.getElementById('upscale-resolution-preview');
        if (!preview) return;
        if (ratio === 'original') {
            preview.textContent = _fmt('upscaleOriginalQualityHint', { quality });
        } else {
            const res = _computeResolutionForRatio(ratio, quality);
            if (res) {
                preview.textContent = `${res.w} × ${res.h}`;
            } else {
                preview.textContent = '';
            }
        }
    }
    window.onUpscaleQualityChange = onUpscaleQualityChange;

    function onUpscaleResizeModeChange() {                                     // 同上
        const preview = document.getElementById('upscale-resize-preview');
        if (!preview) return;
        const mode = document.getElementById('upscale-resize-mode')?.value || 'fit';
        const modeDesc = {
            'fit': '📐 适应：保持原始比例，缩放到目标尺寸内，可能留黑边后缩放到目标',
            'crop': '✂️ 裁剪：保持原始比例，放大后从中心裁剪到目标尺寸',
            'stretch': '↔️ 拉伸：不保持比例，直接拉伸到目标宽高',
        };
        preview.textContent = modeDesc[mode] || '';
    }
    window.onUpscaleResizeModeChange = onUpscaleResizeModeChange;

    async function handleUpscaleVideoUpload(file) {
        if (!file) return;
        try {
            const formData = new FormData();
            formData.append("file", file);
            addLog(_fmt('uploadFileStart', { label: _t('upscaleVideo') || '高清视频', name: file.name }));
            const res = await fetch(`${BASE}/api/system/upload-file`, {
                method: 'POST',
                body: formData
            });
            const data = await res.json();
            if (res.ok && data.path) {
                document.getElementById('upscale-video-path').value = data.path;
                const preview = document.getElementById('upscale-video-preview');
                const nameEl = document.getElementById('upscale-video-name');
                const placeholder = document.getElementById('upscale-video-placeholder');
                const clearOverlay = document.getElementById('clear-upscale-video-overlay');
                if (preview) { preview.src = `${BASE}/api/system/file?path=${encodeURIComponent(data.path)}`; preview.style.display = 'block'; preview.load(); }
                if (nameEl) { nameEl.textContent = file.name; nameEl.style.display = 'block'; }
                if (placeholder) placeholder.style.display = 'none';
                if (clearOverlay) clearOverlay.style.display = 'flex';
                addLog('✅ ' + (_t('upscaleVideo') || '高清视频') + ' ' + file.name);
                scheduleSave();
            } else {
                throw new Error(data.error || '上传失败');
            }
        } catch (e) {
            addLog('上传视频失败: ' + e.message);
        }
    }

    async function handleUpscaleImageUpload(file) {
        if (!file) return;
        try {
            const b64Data = await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = (e) => resolve(e.target.result);
                reader.onerror = () => reject(new Error(_t('fileReadFail')));
                reader.readAsDataURL(file);
            });
            addLog(_fmt('uploadFileStart', { label: _t('upscaleImage') || '高清图片', name: file.name }));
            const res = await fetch(`${BASE}/api/system/upload-image`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ image: b64Data, filename: file.name })
            });
            const data = await res.json();
            if (res.ok && data.path) {
                document.getElementById('upscale-image-path').value = data.path;
                const preview = document.getElementById('upscale-image-preview');
                const placeholder = document.getElementById('upscale-image-placeholder');
                const clearOverlay = document.getElementById('clear-upscale-image-overlay');
                if (preview) { preview.src = `${BASE}/api/system/file?path=${encodeURIComponent(data.path)}`; preview.style.display = 'block'; }
                if (placeholder) placeholder.style.display = 'none';
                if (clearOverlay) clearOverlay.style.display = 'flex';
                addLog('✅ ' + (_t('upscaleImage') || '高清图片') + ' ' + file.name);
                scheduleSave();
            } else {
                throw new Error(data.error || '上传失败');
            }
        } catch (e) {
            addLog('上传图片失败: ' + e.message);
        }
    }

    function clearUpscaleVideo() {
        document.getElementById('upscale-video-path').value = '';
        const preview = document.getElementById('upscale-video-preview');
        const nameEl = document.getElementById('upscale-video-name');
        const placeholder = document.getElementById('upscale-video-placeholder');
        const clearOverlay = document.getElementById('clear-upscale-video-overlay');
        if (preview) { preview.removeAttribute('src'); preview.style.display = 'none'; preview.load(); }
        if (nameEl) { nameEl.textContent = ''; nameEl.style.display = 'none'; }
        if (placeholder) placeholder.style.display = 'flex';
        if (clearOverlay) clearOverlay.style.display = 'none';
        scheduleSave();
    }

    function clearUpscaleImage() {
        document.getElementById('upscale-image-path').value = '';
        const preview = document.getElementById('upscale-image-preview');
        const placeholder = document.getElementById('upscale-image-placeholder');
        const clearOverlay = document.getElementById('clear-upscale-image-overlay');
        if (preview) { preview.removeAttribute('src'); preview.style.display = 'none'; }
        if (placeholder) placeholder.style.display = 'flex';
        if (clearOverlay) clearOverlay.style.display = 'none';
        scheduleSave();
    }

    let _isUpscaling = false;

    async function runUpscale() {
        if (_isUpscaling) return;
        const isVideo = _upscaleSubMode === 'upscale_video';
        const inputPathEl = isVideo ? document.getElementById('upscale-video-path') : document.getElementById('upscale-image-path');
        const inputPath = inputPathEl ? inputPathEl.value.trim() : '';
        if (!inputPath) {
            addLog(isVideo ? _t('upscaleWarnNoVideo') : _t('upscaleWarnNoImage'));
            return;
        }
        const engine = document.getElementById('upscale-engine')?.value || 'realesrgan';
        const scale = parseInt(document.getElementById('upscale-scale')?.value || '2');
        const model = document.getElementById('upscale-model')?.value || 'realesrgan-x4plus';
        const denoise = parseFloat(document.getElementById('upscale-denoise')?.value || '0.5');
        const ratio = document.getElementById('upscale-ratio')?.value || 'original';
        const quality = document.getElementById('upscale-quality')?.value || '1080p';
        const resizeMode = document.getElementById('upscale-resize-mode')?.value || 'fit';
        const tileSize = parseInt(document.getElementById('upscale-tile-size')?.value || '0');
        let targetWidth = null;
        let targetHeight = null;
        let useResizeMode = false;

        let targetFps = null;
        if (isVideo) {
            const fpsEl = document.getElementById('upscale-fps');
            const fpsVal = fpsEl ? fpsEl.value : '0';
            if (fpsVal === 'custom') {
                const customFps = parseInt(document.getElementById('upscale-fps-custom')?.value || '0');
                if (customFps > 0) targetFps = customFps;
            } else if (fpsVal !== '0') {
                targetFps = parseInt(fpsVal);
            }
        }

        if (ratio === 'original') {
            targetWidth = null;
            targetHeight = parseInt(quality.replace('k', '')) > 0
                ? UPSCALE_QUALITY_PRESETS[quality]?.shortSide || 1080
                : 1080;
            useResizeMode = true;
        } else if (UPSCALE_RATIO_PRESETS[ratio]) {
            const res = _computeResolutionForRatio(ratio, quality);
            if (res) {
                targetWidth = res.w;
                targetHeight = res.h;
            }
            useResizeMode = true;
        } else if (ratio === 'custom') {
            targetWidth = parseInt(document.getElementById('upscale-target-width')?.value) || null;
            targetHeight = parseInt(document.getElementById('upscale-target-height')?.value) || null;
            useResizeMode = true;
        }

        const btn = document.getElementById('upscale-top-btn');
        _isUpscaling = true;
        if (btn) { btn.disabled = true; btn.textContent = _t('upscaleGenRunning') || '放大中...'; }

        try {
            const endpoint = isVideo ? '/api/upscale/video' : '/api/upscale/image';
            const payload = { inputPath, engine, scale, model, denoise };
            if (targetWidth !== null) payload.targetWidth = targetWidth;
            if (targetHeight !== null) payload.targetHeight = targetHeight;
            if (useResizeMode) payload.resizeMode = resizeMode;
            if (ratio === 'original') payload.keepOriginalRatio = true;
            if (tileSize > 0) payload.tileSize = tileSize;
            if (targetFps !== null) payload.targetFps = targetFps;

            const typeLabel = isVideo ? (_t('tabVideo') || '视频') : (_t('tabImage') || '图片');
            const ratioLabel = ratio === 'scale' ? `${scale}x` : (ratio === 'original' ? quality : `${targetWidth||'?'}x${targetHeight||'?'}`);
            const label = `高清放大: ${typeLabel} ${ratioLabel}`;

            const queueInfo = await submitQueuedTask('upscale', endpoint, payload, label);
            addLog(_fmt('queueSubmitLog', { id: queueInfo.task_id, n: Math.max(0, (queueInfo.position || 1) - 1) }));
            if (queueInfo.task_id) {
                _currentQueueTaskId = queueInfo.task_id;
            }
            _awaitingQueueStart = true;
            _isGeneratingFlag = true;
            _updateGenButtons();
        } catch (e) {
            addLog((_t('upscaleFailed') || '放大失败') + ': ' + e.message);
        } finally {
            _isUpscaling = false;
            if (btn && !_isGeneratingFlag) { btn.disabled = false; btn.textContent = _t('upscaleGenBtn') || '🔍 开始高清放大'; }
        }
    }

    function _updateTtsSubModeUI() {
        const mode = _ttsSubMode;
        const hasRef = !!_ttsRefB64;
        const isTtsGen = mode !== 'asr';

        const textSec = document.getElementById('tts-text-section');
        const audioSec = document.getElementById('tts-audio-section');
        const ultSec = document.getElementById('tts-ultimate-section');
        const asrResultSec = document.getElementById('tts-asr-result-section');
        const paramsSec = document.getElementById('tts-params-section');
        const resultSec = document.getElementById('tts-result-section');
        const audioLabel = document.getElementById('tts-audio-label');
        const genBtn = document.getElementById('tts-top-btn');

        if (textSec) textSec.style.display = isTtsGen ? 'block' : 'none';
        if (audioSec) audioSec.style.display = (mode === 'clone' || mode === 'ultimate_clone' || mode === 'asr') ? 'block' : 'none';
        if (ultSec) ultSec.style.display = mode === 'ultimate_clone' ? 'block' : 'none';
        if (asrResultSec) asrResultSec.style.display = mode === 'asr' ? 'block' : 'none';
        if (paramsSec) paramsSec.style.display = isTtsGen ? 'block' : 'none';
        if (resultSec) resultSec.style.display = (isTtsGen && resultSec.style.display === 'block') ? 'block' : 'none';

        if (audioLabel) {
            if (mode === 'asr') {
                audioLabel.textContent = _t('asrUploadTitle') || '上传音频 / Upload Audio';
            } else {
                audioLabel.textContent = _t('ttsRefLabel') || '📎 参考音频（Reference）';
            }
        }

        if (genBtn) {
            if (mode === 'asr') {
                genBtn.textContent = _t('asrRunBtn') || '🔍 开始识别';
                genBtn.onclick = runAsr;
            } else {
                genBtn.textContent = _t('ttsGenBtn') || '🎙️ 开始生成语音';
                genBtn.onclick = runTts;
            }
        }

        const cloneBtn = document.getElementById('tts-clone-transcribe-btn');
        const ultBtn = document.getElementById('tts-ultimate-transcribe-btn');
        if (cloneBtn) cloneBtn.style.display = (hasRef && (mode === 'clone' || mode === 'ultimate_clone')) ? 'block' : 'none';
        if (ultBtn) ultBtn.style.display = (hasRef && mode === 'ultimate_clone') ? 'inline-block' : 'none';

        document.querySelectorAll('.tts-sub-tab').forEach(btn => {
            const subId = btn.id.replace('tts-subtab-', '');
            btn.classList.toggle('active', subId === _ttsSubMode);
        });
    }

async function transcribeTtsRef(target) {
        if (!_ttsRefB64) { addLog('❌ 请先上传参考音频'); return; }
        const cloneBtn = document.getElementById('tts-clone-transcribe-btn');
        const ultBtn = document.getElementById('tts-ultimate-transcribe-btn');
        const activeBtn = target === 'ultimate' ? ultBtn : cloneBtn;
        if (activeBtn) { activeBtn.disabled = true; activeBtn.textContent = _t('ttsTranscribing') || '⏳ 识别中...'; }
        try {
            const res = await fetch(`${BASE}/api/tts/transcribe`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ audio: _ttsRefB64 })
            });
            const data = await res.json();
            if (data.error) {
                addLog(`❌ ${_t('ttsTranscribeFail')}: ${data.error}`);
                return;
            }
            const text = data.text || '';
            if (!text) { addLog('⚠️ ' + (_t('ttsTranscribeEmpty') || '识别结果为空')); return; }
            if (target === 'ultimate') {
                const el = document.getElementById('tts-prompt-text');
                if (el) el.value = text;
            } else {
                const el = document.getElementById('tts-text');
                if (el) el.value = text;
            }
            const langInfo = data.language ? ` (${data.language})` : '';
            addLog(`✅ ${_t('ttsTranscribeOk')}${langInfo}: ${text.slice(0, 80)}${text.length > 80 ? '...' : ''}`);
        } catch (e) {
            addLog(`❌ ${_t('ttsTranscribeFail')}: ${e.message}`);
        } finally {
            if (activeBtn) { activeBtn.disabled = false; activeBtn.textContent = _t('ttsTranscribeBtn') || '🔍 识别为文字'; }
        }
    
    }
    window.transcribeTtsRef = transcribeTtsRef;

async function handleTtsRefUpload(file) {
        if (!file) return;
        const placeholder = document.getElementById('tts-ref-placeholder');
        const statusEl = document.getElementById('tts-ref-status');
        const clearBtn = document.getElementById('tts-ref-clear');
        addLog(`正在读取参考音频: ${file.name}...`);
        const reader = new FileReader();
        reader.onload = async (e) => {
            const b64 = e.target.result;
            _ttsRefB64 = b64.includes(',') ? b64.split(',')[1] : b64;
            if (placeholder) placeholder.style.display = 'none';
            if (statusEl) { statusEl.style.display = 'block'; statusEl.textContent = '✅ ' + file.name; }
            if (clearBtn) clearBtn.style.display = 'flex';
            _updateTtsSubModeUI();
            addLog(`✅ 参考音频已加载: ${file.name}`);
        
    }
    window.handleTtsRefUpload = handleTtsRefUpload;
        reader.readAsDataURL(file);
    };

function clearTtsRef() {
        _ttsRefB64 = null;
        const fi = document.getElementById('tts-ref-input');
        if (fi) fi.value = '';
        const placeholder = document.getElementById('tts-ref-placeholder');
        const statusEl = document.getElementById('tts-ref-status');
        const clearBtn = document.getElementById('tts-ref-clear');
        if (placeholder) placeholder.style.display = 'block';
        if (statusEl) { statusEl.style.display = 'none'; statusEl.textContent = ''; }
        if (clearBtn) clearBtn.style.display = 'none';
        _updateTtsSubModeUI();
        addLog('🧹 已清除参考音频');
    
    }
    window.clearTtsRef = clearTtsRef;

    let _asrLastResult = '';

async function runAsr() {
        if (!_ttsRefB64) { addLog('❌ 请先上传音频文件'); return; }
        const btn = document.getElementById('tts-top-btn');
        const resultEl = document.getElementById('asr-result');
        const copyBtn = document.getElementById('asr-copy-btn');
        if (btn) { btn.disabled = true; btn.textContent = _t('asrRunBusy') || '⏳ 识别中...'; }
        if (resultEl) resultEl.textContent = _t('asrRunBusy') || '⏳ 识别中...';
        try {
            const res = await fetch(`${BASE}/api/tts/transcribe`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ audio: _ttsRefB64 })
            });
            const data = await res.json();
            if (data.error) {
                if (resultEl) resultEl.textContent = '❌ ' + data.error;
                addLog(`❌ ${_t('asrRunFail')}: ${data.error}`);
                return;
            }
            const text = data.text || '';
            _asrLastResult = text;
            const langInfo = data.language ? ` [${data.language}]` : '';
            if (resultEl) resultEl.textContent = text || (_t('asrResultEmpty') || '（识别结果为空）');
            if (copyBtn) copyBtn.style.display = text ? 'inline-block' : 'none';
            addLog(`✅ ${_t('asrRunOk')}${langInfo}: ${text.slice(0, 80)}${text.length > 80 ? '...' : ''}`);
        } catch (e) {
            if (resultEl) resultEl.textContent = '❌ ' + e.message;
            addLog(`❌ ${_t('asrRunFail')}: ${e.message}`);
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = _t('asrRunBtn') || '🔍 开始识别'; }
        }
    
    }
    window.runAsr = runAsr;

function copyAsrResult() {
        if (!_asrLastResult) return;
        navigator.clipboard.writeText(_asrLastResult).then(() => {
            addLog('✅ 已复制到剪贴板');
        }).catch(() => {
            addLog('❌ 复制失败');
        });
    
    }
    window.copyAsrResult = copyAsrResult;

function runTtsOrAsr() {
        if (_ttsSubMode === 'asr') {
            runAsr();
        } else {
            runTts();
        }
    
    }
    window.runTtsOrAsr = runTtsOrAsr;

    async function checkTtsStatus() {
        const bar = document.getElementById('tts-status-bar');
        if (!bar) return;
        try {
            const [ttsRes, asrRes] = await Promise.all([
                fetch(`${BASE}/api/tts/status`).then(r => r.json()).catch(() => null),
                fetch(`${BASE}/api/tts/asr-status`).then(r => r.json()).catch(() => null)
            ]);
            const parts = [];
            let asrAvailable = false;
            if (ttsRes) {
                if (ttsRes.available) {
                    parts.push('✅ VoxCPM2');
                } else {
                    parts.push('❌ VoxCPM2 ' + (!ttsRes.voxcpm_installed ? _t('ttsStatusNoPkq') : _t('ttsStatusNoDir') + (ttsRes.expected_model_dir || '')));
                }
            }
            if (asrRes) {
                if (asrRes.available) {
                    parts.push('✅ faster-whisper');
                    asrAvailable = true;
                } else {
                    parts.push('⚠️ faster-whisper 未安装');
                }
            }
            if (parts.length === 0) {
                bar.style.color = '#f87171';
                bar.textContent = _t('ttsStatusConnErr') + '无法连接';
            } else if (parts.some(p => p.startsWith('✅'))) {
                bar.style.color = 'var(--accent)';
                bar.style.borderColor = 'var(--accent)';
                bar.textContent = parts.join('  |  ');
            } else {
                bar.style.color = '#f87171';
                bar.textContent = parts.join('  |  ');
            }
            if (!asrAvailable) {
                const installBtn = document.createElement('button');
                installBtn.style.cssText = 'margin-left:8px;font-size:10px;padding:2px 8px;border-radius:4px;cursor:pointer;font-weight:600;border:1px solid rgba(25,118,210,0.5);background:rgba(21,101,192,0.2);color:#42A5F5;white-space:nowrap;';
                installBtn.textContent = _t('envInstall');
                installBtn.onclick = async () => {
                    installBtn.textContent = _t('envFixing');
                    installBtn.disabled = true;
                    installBtn.style.opacity = '0.6';
                    try {
                        const res = await fetch(`${BASE}/api/system/env-fix`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ component: 'faster_whisper' }),
                        });
                        const data = await res.json();
                        if (data.status === 'success') {
                            installBtn.textContent = _t('envFixDone');
                            installBtn.style.color = '#4CAF50';
                            installBtn.style.borderColor = 'rgba(76,175,80,0.5)';
                            installBtn.style.background = 'rgba(76,175,80,0.2)';
                            setTimeout(() => checkTtsStatus(), 1000);
                        } else {
                            installBtn.textContent = _t('envFixFailed');
                            installBtn.style.color = '#f44336';
                            addLog((_t('upscaleInstallFailed') || '安装失败') + ': ' + (data.error || 'unknown'));
                        }
                    } catch (e) {
                        installBtn.textContent = _t('envFixFailed');
                        installBtn.style.color = '#f44336';
                    }
                };
                bar.textContent = '';
                bar.appendChild(document.createTextNode(parts.join('  |  ')));
                bar.appendChild(installBtn);
            }
        } catch (e) {
            bar.style.color = '#f87171';
            bar.textContent = _t('ttsStatusConnErr') + e.message;
        }
    }

function playTtsFromQueue(audioUrl) {
        if (!audioUrl) return;
        const fullUrl = audioUrl.startsWith('http') ? audioUrl : `${BASE}${audioUrl}`;
        const player = document.getElementById('tts-audio-player');
        const dlLink = document.getElementById('tts-download-link');
        const resultSec = document.getElementById('tts-result-section');
        if (player) { player.src = fullUrl; player.load(); player.play().catch(() => {}); }
        if (dlLink) {
            dlLink.href = fullUrl;
            dlLink.download = 'tts_output.wav';
        }
        if (resultSec) resultSec.style.display = 'block';
    
    }
    window.playTtsFromQueue = playTtsFromQueue;

async function runTts() {
        const text = (document.getElementById('tts-text')?.value || '').trim();
        if (!text) { addLog(_t('ttsErrNoText')); return; }

        const mode = _ttsSubMode;
        const cfg = parseFloat(document.getElementById('tts-cfg')?.value || 2.0);
        const steps = parseInt(document.getElementById('tts-steps')?.value || 10);
        const promptText = document.getElementById('tts-prompt-text')?.value || '';

        if ((mode === 'clone' || mode === 'ultimate_clone') && !_ttsRefB64) {
            addLog(_t('ttsErrNoRef'));
            return;
        }

        const btn = document.getElementById('tts-top-btn');
        if (btn) { btn.disabled = true; btn.textContent = _t('ttsGenBusy'); }
        const resultSec = document.getElementById('tts-result-section');
        if (resultSec) resultSec.style.display = 'none';

        addLog(`🎙️ TTS: Mode=${mode}, Length=${text.length}`);

        try {
            const payload = {
                text,
                mode,
                cfg_value: cfg,
                inference_timesteps: steps,
                reference_wav: _ttsRefB64 || null,
                prompt_wav: (mode === 'ultimate_clone' && _ttsRefB64) ? _ttsRefB64 : null,
                prompt_text: promptText || null,
            
    }
    window.runTts = runTts;

            const label = text.length > 40 ? text.slice(0, 40) + '...' : text;
            const queueRes = await submitQueuedTask('tts', '/api/tts/generate', payload, `🎙️ ${label}`);

            _pendingTtsTaskId = queueRes.task_id;
            _isGeneratingFlag = true;
            _awaitingQueueStart = true;
            _updateGenButtons();

        } catch (e) {
            addLog(`❌ TTS Error: ${e.message}`);
            if (btn) { btn.disabled = false; btn.textContent = _t('ttsGenBtn'); }
            _pendingTtsTaskId = null;
        }
    };
    // ──────────────────────────────────────────────────────────────────────────

    function showGeneratingView() {
        if (!_isGeneratingFlag) return;
        const resImg = document.getElementById('res-img');
        const videoWrapper = document.getElementById('video-wrapper');
        const audioWrapper = document.getElementById('audio-wrapper');
        if (resImg) resImg.style.display = "none";
        if (videoWrapper) videoWrapper.style.display = "none";
        if (audioWrapper) audioWrapper.style.display = "none";
        if (player) {
            try { player.pause(); } catch(_) {}
        } else {
            const vid = document.getElementById('res-video');
            if (vid) { vid.pause(); vid.removeAttribute('src'); vid.load(); }
        }
        const audio = document.getElementById('res-audio');
        if (audioPlayer) { try { audioPlayer.stop(); } catch(_) {} }
        if (audio) { audio.pause(); audio.removeAttribute('src'); audio.load(); }
        const loadingTxt = document.getElementById('loading-txt');
        if (loadingTxt) loadingTxt.style.display = "flex";
    }

    function removeCurrentLoadingCard() {
        const loadingCard = document.getElementById('current-loading-card');
        if (loadingCard) loadingCard.remove();
    }

    async function submitQueuedTask(mode, endpoint, payload, label) {
        payload = ensureReplaySeed(payload);
        console.log('[QUEUE] Submitting:', { mode, endpoint, label: label?.slice(0, 60) });
        const res = await fetch(`${BASE}/api/queue/submit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode, endpoint, payload, label })
        });
        const data = await res.json().catch(() => ({}));
        console.log('[QUEUE] Response:', { status: res.status, ok: res.ok, data });
        if (!res.ok) {
            const errMsg = data.error || data.detail || '队列提交失败';
            throw new Error(typeof errMsg === 'string' ? errMsg : JSON.stringify(errMsg));
        }
        startQueuePolling();
        return data;
    }

    function makeReplaySeed() {
        const arr = new Uint32Array(1);
        if (window.crypto && window.crypto.getRandomValues) {
            window.crypto.getRandomValues(arr);
            return (arr[0] % 2147483646) + 1;
        }
        return (Math.floor(Date.now() + Math.random() * 1000000) % 2147483646) + 1;
    }

    function refreshRandomSeedValue() {
        const el = document.getElementById('seed-value');
        if (el) el.value = String(makeReplaySeed());
    }

    function ensureReplaySeed(payload) {
        if (!payload || typeof payload !== 'object') return payload;
        const n = Number(payload.seed);
        if (!Number.isInteger(n) || n <= 0) {
            payload.seed = getActiveSeedForSubmit();
        }
        return payload;
    }

    function getSeedMode() {
        return document.querySelector('input[name="seed-mode"]:checked')?.value || 'random';
    }

    function getFixedSeedValue() {
        const el = document.getElementById('seed-value');
        let seed = parseInt(el?.value || '42', 10);
        if (!Number.isInteger(seed) || seed <= 0) seed = 42;
        seed = Math.min(seed, 2147483647);
        if (el) el.value = String(seed);
        return seed;
    }

    function getActiveSeedForSubmit() {
        return getFixedSeedValue();
    }

    function setFixedSeed(seed) {
        let seedNum = parseInt(seed, 10);
        if (!Number.isInteger(seedNum) || seedNum <= 0) {
            addLog(_t('previewNoReplaySeed'));
            return false;
        }
        seedNum = Math.min(seedNum, 2147483647);
        const fixed = document.querySelector('input[name="seed-mode"][value="fixed"]');
        const input = document.getElementById('seed-value');
        if (fixed) fixed.checked = true;
        if (input) input.value = String(seedNum);
        updateSeedModeUI();
        return true;
    }

    function updateSeedModeUI() {                                              // 2026-06-09: 改 function 声明 + window 暴露,修复 hoisting
        const input = document.getElementById('seed-value');
        const fixed = getSeedMode() === 'fixed';
        const shell = input ? input.closest('.seed-input-shell') : null;
        document.querySelectorAll('.seed-mode-option').forEach((label) => {
            const checked = !!label.querySelector('input')?.checked;
            label.classList.toggle('is-active', checked);
        });
        if (input) {
            if (!input.value || Number(input.value) <= 0) refreshRandomSeedValue();
            input.disabled = !fixed;
            input.title = fixed ? _t('seedFixed') : _t('seedRandom');
        }
        if (shell) shell.classList.toggle('is-random', !fixed);
    }
    window.updateSeedModeUI = updateSeedModeUI;

    const replayStore = new Map();
    let replayStoreSeq = 0;
    const pendingReplaySeedByMode = {};

    /**
     * ★ 2026-06-17 优化: 把 replay 改为按需加载
     *   旧逻辑: 列表 API 返回完整 replay(5.7KB / 5项),前端立即存到 store
     *   新逻辑: 列表只返回 replay_available + fullpath,replayRecordById 时按需拉
     *   收益: 列表响应体减半(10.8KB → 5KB),首屏网络传输时间省 30-50%
     */

    function makeReplayRecord(source) {
        const payload = source?.payload || {};
        const endpoint = source?.endpoint || (source?.mode === 'image' ? '/api/generate-image' : '/api/generate');
        let mode = source?.mode || 'i2v';
        if (mode === 'video') mode = 'i2v';
        if (endpoint === '/api/generate-image') mode = 'image';
        else if (endpoint === '/api/ic-lora/generate') mode = 'motion';
        else if (endpoint === '/api/generate-batch') mode = 'batch';
        return {
            mode,
            endpoint,
            payload: JSON.parse(JSON.stringify(payload)),
            label: source?.label || `${mode}: ${(payload.prompt || '').slice(0, 48) || 'replay'}`
        };
    }

    function storeReplayRecord(source) {
        const id = `replay-${++replayStoreSeq}`;
        replayStore.set(id, makeReplayRecord(source));
        return id;
    }

    /**
     * ★ 2026-06-17: 把 fullpath 注册成一个"懒加载"replay id
     *   数据结构:{ fullpath } - 按需拉取时调后端
     */
    function storeLazyReplay(fullpath) {
        const id = `replay-${++replayStoreSeq}`;
        replayStore.set(id, { lazy: true, fullpath });
        return id;
    }

    async function replayRecordById(id) {
        const record = replayStore.get(id);
        if (!record) {
            addLog(_t('replayMissing'));
            return;
        }
        // ★ 2026-06-17: 懒加载 replay
        if (record.lazy) {
            try {
                const r = await fetch(`${BASE}/api/system/replay?path=${encodeURIComponent(record.fullpath)}`);
                if (!r.ok) {
                    addLog(_t('replayMissing'));
                    return;
                }
                const data = await r.json();
                if (!data.replay) {
                    addLog(_t('replayMissing'));
                    return;
                }
                // 替换 store 里的 lazy 记录为完整 record
                const real = makeReplayRecord(data.replay);
                replayStore.set(id, real);
                replayStoreByPath.set(record.fullpath, real);
                Object.assign(record, real);
            } catch (e) {
                addLog(`❌ ${_t('replayFailed')}: ${e.message || e}`);
                return;
            }
        }
        const real = replayStore.get(id);
        if (!real || !real.endpoint || !real.payload) {
            addLog(_t('replayMissing'));
            return;
        }
        try {
            const info = await submitQueuedTask(
                real.mode,
                real.endpoint,
                real.payload,
                `${_t('replayLabel')}: ${real.label || real.mode}`
            );
            addLog(_fmt('replayQueuedLog', { id: info.task_id }));
        } catch (e) {
            addLog(`❌ ${_t('replayFailed')}: ${e.message || e}`);
        }
    }
    // ★ 按 fullpath 缓存,避免重复拉取
    const replayStoreByPath = new Map();

    function pathFileName(path) {
        return String(path || '').split(/[\\/]/).pop() || '';
    }

    function mediaUrlForPath(path) {
        return `${BASE}/api/system/file?path=${encodeURIComponent(path)}`;
    }

    /**
     * ★ 2026-06-17: 预加载资源(图片/视频/音频)
     *   在 <head> 注入 <link rel="preload" as="image|video|audio"> 让浏览器后台下载
     *   等用户真正需要时(displayHistoryOutput 设 src),浏览器直接从缓存读取
     *   关键路径: 预加载是 HTTP 缓存的"warm",displayHistoryOutput 触发时 0 网络等待
     *   重复调用去重: 同一 URL 只注入一次
     */
    const _preloadedUrls = new Set();
    function _preloadAsset(path, kind) {
        const url = mediaUrlForPath(path);
        if (_preloadedUrls.has(url)) return;
        _preloadedUrls.add(url);
        const link = document.createElement('link');
        link.rel = 'preload';
        link.as = kind;  // 'image' | 'video' | 'audio'
        link.href = url;
        // imagesrcset/imagesizes 可选;Range 不加,让服务端决定
        try { document.head.appendChild(link); } catch (_) {}
    }

    function setElValue(id, value) {
        const el = document.getElementById(id);
        if (el && value !== undefined && value !== null) el.value = value;
    }

    function setFramePath(frameType, path) {
        if (!path) return;
        setElValue(`${frameType}-frame-path`, path);
        const preview = document.getElementById(`${frameType}-frame-preview`);
        const placeholder = document.getElementById(`${frameType}-frame-placeholder`);
        const clearOverlay = document.getElementById(`clear-${frameType}-frame-overlay`);
        if (preview) {
            preview.src = mediaUrlForPath(path);
            preview.style.display = 'block';
        }
        if (placeholder) placeholder.style.display = 'none';
        if (clearOverlay) clearOverlay.style.display = 'flex';
    }

    function setNamedUpload(path, hiddenId, placeholderId, statusId, nameId, clearId) {
        if (!path) return;
        setElValue(hiddenId, path);
        const placeholder = document.getElementById(placeholderId);
        const status = document.getElementById(statusId);
        const name = document.getElementById(nameId);
        const clear = document.getElementById(clearId);
        if (placeholder) placeholder.style.display = 'none';
        if (status) status.style.display = 'block';
        if (name) name.textContent = pathFileName(path);
        if (clear) clear.style.display = 'flex';
    }

    function setMotionImagePath(path) {
        if (!path) return;
        setElValue('motion-image-path', path);
        const preview = document.getElementById('motion-image-preview');
        const placeholder = document.getElementById('motion-image-placeholder');
        const clear = document.getElementById('clear-motion-image-overlay');
        if (preview) {
            preview.src = mediaUrlForPath(path);
            preview.style.display = 'block';
        }
        if (placeholder) placeholder.style.display = 'none';
        if (clear) clear.style.display = 'flex';
    }

    function applyLoraReplay(containerId, paths, strengths) {
        const container = document.getElementById(containerId);
        if (!container) return;
        container.innerHTML = '';
        const list = Array.isArray(paths) && paths.length ? paths : [];
        if (!list.length) {
            window.addLoraSelection(containerId);
            return;
        }
        list.forEach((path, index) => {
            window.addLoraSelection(containerId);
            const entry = container.lastElementChild;
            if (!entry) return;
            const select = entry.querySelector('.lora-select');
            const strength = entry.querySelector('.lora-strength');
            const strengthVal = entry.querySelector('.lora-strength-val');
            const strengthBox = entry.querySelector('.lora-strength-container');
            if (select) {
                if (![...select.options].some(opt => opt.value === path)) {
                    const opt = document.createElement('option');
                    opt.value = path;
                    opt.textContent = pathFileName(path);
                    select.appendChild(opt);
                }
                select.value = path;
            }
            const s = Array.isArray(strengths) ? strengths[index] : 1.0;
            const safeStrength = Math.min(2.0, Math.max(0.1, Number(s || 1.0)));
            if (strength) strength.value = String(safeStrength);
            if (strengthVal) strengthVal.textContent = String(safeStrength);
            if (strengthBox) strengthBox.style.display = 'flex';
            const infoBox = entry.querySelector('.lora-info-box');
            if (infoBox && path) {
                const lora = availableLoras.find(l => l.path === path);
                if (lora && (lora.description || (lora.trigger_words && lora.trigger_words.length > 0) || lora.base_model)) {
                    let html = '';
                    if (lora.description) html += `<div style="margin-bottom:2px">${escapeHtmlAttr(lora.description)}</div>`;
                    if (lora.trigger_words && lora.trigger_words.length > 0) html += `<div>触发词: <span style="color:var(--accent)">${escapeHtmlAttr(lora.trigger_words.join(', '))}</span></div>`;
                    if (lora.base_model) html += `<div>基模: ${escapeHtmlAttr(lora.base_model)}</div>`;
                    infoBox.innerHTML = html;
                    infoBox.style.display = html ? 'block' : 'none';
                }
            }
        });
    }

    function applyReplayPayloadById(id) {
        const record = replayStore.get(id);
        if (!record || !record.payload) {
            addLog(_t('replayMissing'));
            return;
        }
        const payload = record.payload;
        switchMode(record.mode);
        if (payload.seed) pendingReplaySeedByMode[record.mode] = payload.seed;
        if (payload.seed) setFixedSeed(payload.seed);
        setElValue('prompt', payload.prompt || '');

        if (record.mode === 'image') {
            setElValue('img-w', payload.width);
            setElValue('img-h', payload.height);
            setElValue('img-steps', payload.numSteps);
            const stepsVal = document.getElementById('stepsVal');
            if (stepsVal && payload.numSteps) stepsVal.innerText = payload.numSteps;
            updateImgResPreview();
        } else if (record.mode === 'i2v' || record.mode === 'video') {
            const q = String(payload.resolution || '').replace('p', '');
            if (q) setElValue('vid-quality', q);
            setElValue('vid-ratio', (payload.aspectRatio === 'ref' && payload.customWidth && payload.customHeight) ? 'custom' : payload.aspectRatio);
            setElValue('vid-custom-w', payload.customWidth);
            setElValue('vid-custom-h', payload.customHeight);
            setElValue('vid-duration', payload.duration);
            setElValue('vid-fps', payload.fps);
            setElValue('vid-motion', payload.cameraMotion);
            const audio = document.getElementById('vid-audio');
            if (audio && payload.audio !== undefined) audio.checked = String(payload.audio) === 'true';
            setFramePath('start', payload.startFramePath || payload.imagePath);
            setFramePath('end', payload.endFramePath);
            setNamedUpload(payload.audioPath, 'uploaded-audio-path', 'audio-upload-placeholder', 'audio-upload-status', 'audio-filename-status', 'clear-audio-overlay');
            applyLoraReplay('loras-container', payload.loraPaths || (payload.loraPath ? [payload.loraPath] : []), payload.loraStrengths || [payload.loraStrength]);
            updateResPreview();
        } else if (record.mode === 'motion') {
            const mode = payload.conditioning_type === 'video'
                ? ((Array.isArray(payload.images) && payload.images.length) ? 'camera' : 'repaint')
                : 'action';
            window.setVideoTransferMode(mode);
            setNamedUpload(payload.video_path, 'motion-video-path', 'motion-video-placeholder', 'motion-video-status', 'motion-video-name', 'clear-motion-video-overlay');
            const motionVidPreview = document.getElementById('motion-video-preview');
            if (motionVidPreview && payload.video_path) {
                motionVidPreview.poster = `${BASE}/api/system/video-thumbnail?path=${encodeURIComponent(payload.video_path)}&_t=${Date.now()}`;
                motionVidPreview.src = mediaUrlForPath(payload.video_path) + '&_t=' + Date.now();
            }
            const imgPath = Array.isArray(payload.images) && payload.images[0] ? payload.images[0].path : '';
            setMotionImagePath(imgPath);
            setElValue('motion-conditioning-type', payload.conditioning_type);
            setElValue('motion-fps', payload.fps);
            setElValue('motion-duration', payload.duration);
            setElValue('motion-strength', payload.conditioning_strength);
            setElValue('motion-quality', payload.quality || '720');
            updateMotionResPreview();
            applyLoraReplay('motion-loras-container', payload.loraPaths || [], payload.loraStrengths || []);
            setElValue('motion-seed', payload.seed || '');
            const strengthVal = document.getElementById('motion-strength-val');
            if (strengthVal && payload.conditioning_strength !== undefined) strengthVal.textContent = String(payload.conditioning_strength);
            setElValue('motion-attention-strength', payload.attention_strength ?? 1.0);
            const attentionVal = document.getElementById('motion-attention-val');
            if (attentionVal && payload.attention_strength !== undefined) attentionVal.textContent = String(payload.attention_strength);
        } else if (record.mode === 'batch') {
            const q = String(payload.resolution || '').replace('p', '');
            if (q) setElValue('batch-quality', q);
            setElValue('batch-ratio', (payload.aspectRatio === 'ref' && payload.customWidth && payload.customHeight) ? 'custom' : payload.aspectRatio);
            setElValue('batch-custom-w', payload.customWidth);
            setElValue('batch-custom-h', payload.customHeight);
            setElValue('vid-fps', payload.fps);
            setElValue('vid-motion', payload.cameraMotion);
            applyLoraReplay('batch-loras-container', payload.loraPaths || (payload.loraPath ? [payload.loraPath] : []), payload.loraStrengths || [payload.loraStrength]);
            const segments = Array.isArray(payload.segments) ? payload.segments : [];
            const keyframes = Array.isArray(payload.keyframePaths) ? payload.keyframePaths : [];
            if (keyframes.length) {
                const singleMode = document.querySelector('input[name="batch-workflow"][value="single"]');
                if (singleMode) singleMode.checked = true;
                batchImages = keyframes.map(path => ({ path, preview: mediaUrlForPath(path) }));
                renderBatchImages();
                updateBatchSegments();
                const strengths = Array.isArray(payload.keyframeStrengths) ? payload.keyframeStrengths : [];
                strengths.forEach((s, idx) => setElValue(`batch-kf-strength-${idx}`, s));
                const times = Array.isArray(payload.keyframeTimes) ? payload.keyframeTimes.map(Number) : [];
                if (times.length) {
                    const total = Number(payload.duration || 0);
                    for (let idx = 0; idx < keyframes.length; idx++) {
                        const next = idx < times.length - 1 ? times[idx + 1] : total;
                        const cur = times[idx] || 0;
                        const dur = Math.max(0.1, (Number(next) || cur + 1) - cur);
                        setElValue(`batch-kf-seg-dur-${idx}`, dur.toFixed(1));
                    }
                    updateBatchKfTimelineDerivedUI();
                }
            } else if (segments.length) {
                const segmentMode = document.querySelector('input[name="batch-workflow"][value="segments"]');
                if (segmentMode) segmentMode.checked = true;
                batchImages = [];
                segments.forEach((seg, idx) => {
                    if (idx === 0 && seg.startImage) batchImages.push({ path: seg.startImage, preview: mediaUrlForPath(seg.startImage) });
                    if (seg.endImage) batchImages.push({ path: seg.endImage, preview: mediaUrlForPath(seg.endImage) });
                });
                renderBatchImages();
                updateBatchSegments();
                segments.forEach((seg, idx) => {
                    setElValue(`batch-segment-duration-${idx}`, seg.duration);
                    setElValue(`batch-segment-prompt-${idx}`, seg.prompt);
                });
            }
            setElValue('batch-background-audio-path', payload.backgroundAudioPath);
            updateBatchResPreview();
        }
        addLog(_t('replayLoadedLog'));
    }

function loadCurrentPreviewSeed() {
        const record = currentPreviewReplayId ? replayStore.get(currentPreviewReplayId) : null;
        const seed = record?.payload?.seed;
        if (!seed) {
            addLog(_t('previewNoReplaySeed'));
            return;
        }
        if (setFixedSeed(seed)) {
            addLog(_fmt('previewSeedLoadedLog', { seed }));
        }
    
    }
    window.loadCurrentPreviewSeed = loadCurrentPreviewSeed;

function loadCurrentPreviewParams() {
        if (!currentPreviewReplayId || !replayStore.has(currentPreviewReplayId)) {
            addLog(_t('previewNoReplayParams'));
            return;
        }
        applyReplayPayloadById(currentPreviewReplayId);
    
    }
    window.loadCurrentPreviewParams = loadCurrentPreviewParams;

    async function refreshPreviewReplayFromFile(fileOrPath, fileName) {
        if (currentPreviewReplayId) return;
        try {
            const hasPath = String(fileOrPath || '').includes('\\') || String(fileOrPath || '').includes('/');
            const qs = hasPath
                ? `path=${encodeURIComponent(fileOrPath)}`
                : `filename=${encodeURIComponent(fileName || fileOrPath)}`;
            const res = await fetch(`${BASE}/api/system/replay?${qs}`);
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.replay) return;
            currentPreviewReplayId = storeReplayRecord(data.replay);
            const actions = document.getElementById('preview-replay-actions');
            if (actions) {
                actions.style.display = 'inline-flex';
                actions.classList.remove('is-unavailable');
            }
        } catch (_) {}
    }

    window.replayRecordById = replayRecordById;
    window.applyReplayPayloadById = applyReplayPayloadById;

    async function run() {
        console.log('[RUN] run() called, currentMode=', currentMode, '_isGeneratingFlag=', _isGeneratingFlag);
        const useQueue = ['i2v', 'motion', 'batch', 'image'].includes(currentMode);
        if (_isGeneratingFlag) {
            addLog(_t('warnGenerating'));
            return;
        }

        const btn = document.getElementById('mainBtn');
        const promptEl = document.getElementById('prompt');
        const rawPrompt = promptEl ? promptEl.value.trim() : '';

        // 自动注入 LoRA 触发词到 prompt 前面
        function collectLoraPathsForTrigger() {
            const paths = [];
            const containerIds = ['loras-container', 'img-loras-container'];
            for (const cid of containerIds) {
                const container = document.getElementById(cid);
                if (!container) continue;
                container.querySelectorAll('.lora-entry').forEach(entry => {
                    const sel = entry.querySelector('.lora-select');
                    if (sel && sel.value) paths.push(sel.value);
                });
            }
            return [...new Set(paths)];
        }

        function injectLoraTriggers(basePrompt, loraPaths) {
            if (!loraPaths || loraPaths.length === 0) return basePrompt;
            const triggers = [];
            for (const p of loraPaths) {
                const lora = availableLoras.find(l => l.path === p);
                if (lora && lora.trigger_words && lora.trigger_words.length > 0) {
                    for (const tw of lora.trigger_words) {
                        if (tw && !basePrompt.includes(tw)) {
                            triggers.push(tw);
                        }
                    }
                }
            }
            if (triggers.length === 0) return basePrompt;
            return triggers.join(', ') + ', ' + basePrompt;
        }

        const loraPathsForTrigger = collectLoraPathsForTrigger();
        const prompt = injectLoraTriggers(rawPrompt, loraPathsForTrigger);

        function batchHasUsablePrompt() {
            if (prompt) return true;
            if (typeof batchWorkflowIsSingle === 'function' && batchWorkflowIsSingle()) {
                return false;
            }
            if (batchImages.length < 2) return false;
            for (let i = 0; i < batchImages.length - 1; i++) {
                if (document.getElementById(`batch-segment-prompt-${i}`)?.value?.trim()) return true;
            }
            return false;
        }

        if (currentMode === 'batch') {
            if (!batchHasUsablePrompt()) {
                addLog(_t('warnBatchPrompt'));
                return;
            }
        } else if (currentMode !== 'motion' && !prompt) {
            addLog(_t('warnNeedPrompt'));
            return;
        }

        if (!btn) {
            console.error('mainBtn not found');
            return;
        }

        // 先设置标志 + 禁用按钮，然后用顶层 try/finally 保证一定能解锁
        _isGeneratingFlag = true;
        let _queueSubmitOk = false;
        _updateGenButtons();

        try {
            if (!useQueue) {
                const loader = document.getElementById('loading-txt');
                const resImg = document.getElementById('res-img');
                const resVideo = document.getElementById('res-video');

                if (loader) {
                    loader.style.display = "flex";
                    loader.style.flexDirection = "column";
                    loader.style.alignItems = "center";
                    loader.style.gap = "12px";
                    loader.innerHTML = `
                        <div class="spinner" style="width:48px;height:48px;border-width:4px;color:var(--accent);"></div>
                        <div id="loader-step-text" style="font-size:13px;font-weight:700;color:var(--text-sub);">${escapeHtmlAttr(_t('loaderGpuAlloc'))}</div>
                    `;
                }
                if (resImg) resImg.style.display = "none";
                const videoWrapper = document.getElementById('video-wrapper');
                if (videoWrapper) videoWrapper.style.display = "none";
                const audioWrapper = document.getElementById('audio-wrapper');
                if (audioWrapper) audioWrapper.style.display = "none";
                if (player) { try { player.pause(); } catch(_) {} }
                else if (resVideo) { resVideo.pause?.(); resVideo.removeAttribute?.('src'); resVideo.load?.(); }
                if (audioPlayer) { try { audioPlayer.stop(); } catch(_) {} }

                checkStatus();
                try { await fetch(`${BASE}/api/system/reset-state`, { method: 'POST' }); } catch(_) {}
                startProgressPolling();

                const historyContainer = document.getElementById('history-container');
                if (historyContainer) {
                    const old = document.getElementById('current-loading-card');
                    if (old) old.remove();
                    const loadingCard = document.createElement('div');
                    loadingCard.className = 'history-card loading-card';
                    loadingCard.id = 'current-loading-card';
                    loadingCard.onclick = showGeneratingView;
                    loadingCard.innerHTML = `
                        <div class="spinner"></div>
                        <div id="loading-card-step" style="font-size:10px;color:var(--text-dim);margin-top:4px;">等待中...</div>
                    `;
                    historyContainer.prepend(loadingCard);
                }
            }

            // ---- 构建请求 ----
            let endpoint, payload;
            if (currentMode === 'image') {
                const w = parseInt(document.getElementById('img-w').value);
                const h = parseInt(document.getElementById('img-h').value);
                endpoint = '/api/generate-image';
                payload = {
                    prompt, width: w, height: h,
                    numSteps: parseInt(document.getElementById('img-steps').value),
                    numImages: 1,
                    modelPath: getSelectedImgModelPath() || null,
                };
                const imgLoras = collectLoras('img-loras-container');
                if (imgLoras.length > 0) {
                    payload.loraPaths = imgLoras.map(l => l.path);
                    payload.loraStrengths = imgLoras.map(l => l.strength);
                }
                addLog(`正在发起图像渲染: ${w}x${h}, Steps: ${payload.numSteps}`);

            } else if (currentMode === 'i2v') {
                const res = updateResPreview();
                const genSize = getGenerationSize('video');
                const durEl = document.getElementById('vid-duration');
                const dur = durEl ? parseFloat(durEl.value) : 5;
                const fpsEl = document.getElementById('vid-fps');
                const fps = fpsEl ? fpsEl.value : '24';
                if (dur > 20) addLog(_t('warnVideoLong').replace('{n}', String(dur)));

                const audioEl = document.getElementById('vid-audio');
                const audio = audioEl ? (audioEl.checked ? "true" : "false") : "false";
                const audioPathEl = document.getElementById('uploaded-audio-path');
                const audioPath = audioPathEl ? audioPathEl.value : '';
                const startFrameEl = document.getElementById('start-frame-path');
                const startFramePathValue = startFrameEl ? startFrameEl.value : '';
                const endFrameEl = document.getElementById('end-frame-path');
                const endFramePathValue = endFrameEl ? endFrameEl.value : '';
                const vidModelSelect = document.getElementById('vid-model-select');
                const modelPath = vidModelSelect ? vidModelSelect.value : getSelectedModelCheckpointPath();

                let finalImagePath = null, finalStartFramePath = null, finalEndFramePath = null;
                if (startFramePathValue && endFramePathValue) {
                    finalStartFramePath = startFramePathValue;
                    finalEndFramePath = endFramePathValue;
                } else if (startFramePathValue) {
                    finalImagePath = startFramePathValue;
                }

                endpoint = '/api/generate';
                const loraPaths = [];
                const loraStrengths = [];
                document.querySelectorAll('#loras-container .lora-entry').forEach(entry => {
                    const sel = entry.querySelector('.lora-select');
                    const strIn = entry.querySelector('.lora-strength');
                    if (sel && sel.value) {
                        loraPaths.push(sel.value);
                        loraStrengths.push(strIn ? parseFloat(strIn.value) : 1.0);
                    }
                });

                const effectiveLoraPaths = loraPaths;
                const effectiveLoraStrengths = loraStrengths;
                const loraPath = effectiveLoraPaths[0] || null;
                const loraStrength = effectiveLoraStrengths[0] || 1.0;

                const isDistilled = !isVidModelDev();
                const vidStepsEl = document.getElementById('vid-steps');
                const vidSteps = vidStepsEl ? parseInt(vidStepsEl.value, 10) : 30;

                const vidMotionSpeed = parseFloat(document.getElementById('vid-motion-speed')?.value) || 1.0;
                payload = {
                    prompt, resolution: res, model: "ltx-2",
                    cameraMotion: document.getElementById('vid-motion')?.value || 'none',
                    negativePrompt: "low quality, blurry, noisy, static noise, distorted",
                    duration: String(dur), fps, audio,
                    imagePath: finalImagePath,
                    audioPath: audioPath || null,
                    startFramePath: finalStartFramePath,
                    endFramePath: finalEndFramePath,
                    aspectRatio: document.getElementById('vid-ratio')?.value || '16:9',
                    customWidth: genSize.width,
                    customHeight: genSize.height,
                    loraPath: loraPath,
                    loraStrength: loraStrength,
                    loraPaths: effectiveLoraPaths.length > 0 ? effectiveLoraPaths : null,
                    loraStrengths: effectiveLoraStrengths.length > 0 ? effectiveLoraStrengths : null,
                    modelPath: modelPath || null,
                    distilled: isDistilled,
                    numInferenceSteps: isDistilled ? null : vidSteps,
                    motionSpeed: vidMotionSpeed,
                };
                
                let loraLog = _t('loraNoneLabel');
                if (effectiveLoraPaths.length > 0) {
                    loraLog = effectiveLoraPaths.map(p => p.split(/[/\\]/).pop()).join(', ');
                }
                addLog(`正在发起视频渲染: ${res}, 时长: ${dur}s, FPS: ${fps}, 运动速度: ${vidMotionSpeed}x, 步数: ${isDistilled ? '8+3(蒸馏)' : `${vidSteps}(标准)`}, LoRA: ${loraLog}`);

            } else if (currentMode === 'motion') {
                const videoPath = document.getElementById('motion-video-path')?.value || '';
                const imagePath = document.getElementById('motion-image-path')?.value || '';
                if (!videoPath) {
                    throw new Error(_t('motionErrNeedVideo'));
                }
                const transferMode = window._videoTransferMode || 'action';
                if (transferMode !== 'repaint' && !imagePath) {
                    throw new Error(_t('motionErrNeedImage'));
                }
                const conditioningType = document.getElementById('motion-conditioning-type')?.value || 'canny';
                const motionFpsRaw = document.getElementById('motion-fps')?.value || '';
                const motionFps = motionFpsRaw ? parseInt(motionFpsRaw, 10) : null;
                const motionDurationRaw = document.getElementById('motion-duration')?.value || '';
                const motionDuration = motionDurationRaw ? parseFloat(motionDurationRaw) : null;
                let strength = parseFloat(document.getElementById('motion-strength')?.value || '0.5');
                if (!Number.isFinite(strength)) strength = 1.0;
                strength = Math.max(0, Math.min(2.0, strength));
                let attentionStrength = parseFloat(document.getElementById('motion-attention-strength')?.value || '1.0');
                if (!Number.isFinite(attentionStrength)) attentionStrength = 1.0;
                attentionStrength = Math.max(0, Math.min(1.0, attentionStrength));
                const motionPrompt = prompt || 'A high quality video of the target subject following the reference motion, coherent movement, stable identity, clean details';
                const motionQuality = document.getElementById('motion-quality')?.value || '720';
                const motionLoraPaths = [];
                const motionLoraStrengths = [];
                document.querySelectorAll('#motion-loras-container .lora-entry').forEach(entry => {
                    const sel = entry.querySelector('.lora-select');
                    const sld = entry.querySelector('.lora-strength');
                    if (sel && sel.value) {
                        motionLoraPaths.push(sel.value);
                        motionLoraStrengths.push(parseFloat(sld?.value || '1.0'));
                    }
                });
                const motionSeedRaw = document.getElementById('motion-seed')?.value;
                const motionSeed = motionSeedRaw ? parseInt(motionSeedRaw, 10) : null;
                const motionMotionSpeed = parseFloat(document.getElementById('motion-motion-speed')?.value) || 1.0;
                endpoint = '/api/ic-lora/generate';
                payload = {
                    video_path: videoPath,
                    conditioning_type: conditioningType,
                    prompt: motionPrompt,
                    conditioning_strength: strength,
                    attention_strength: attentionStrength,
                    fps: motionFps,
                    duration: motionDuration,
                    cfg_guidance_scale: 1.0,
                    negative_prompt: "low quality, blurry, noisy, static noise, distorted",
                    images: transferMode === 'repaint' ? [] : [{ path: imagePath, frame: 0, strength: 1.0 }],
                    modelPath: getSelectedModelCheckpointPath() || null,
                    quality: motionQuality,
                    seed: motionSeed,
                    loraPaths: motionLoraPaths.length > 0 ? motionLoraPaths : undefined,
                    loraStrengths: motionLoraStrengths.length > 0 ? motionLoraStrengths : undefined,
                    motionSpeed: motionMotionSpeed,
                };
                if (!prompt) addLog(_t('motionDefaultPromptNotice'));
                addLog(`${_fmt('motionStartLog', { type: conditioningType, strength })}, ${_fmt('motionStartMeta', { fps: motionFps || 'auto', duration: motionDuration || 'auto' })}, 运动速度: ${motionMotionSpeed}x`);

            } else if (currentMode === 'batch') {
                const res = updateBatchResPreview();
                const batchGenSize = getGenerationSize('batch');
                const loraPaths = [];
                const loraStrengths = [];
                document.querySelectorAll('#batch-loras-container .lora-entry').forEach(entry => {
                    const sel = entry.querySelector('.lora-select');
                    const strIn = entry.querySelector('.lora-strength');
                    if (sel && sel.value) {
                        loraPaths.push(sel.value);
                        loraStrengths.push(strIn ? parseFloat(strIn.value) : 1.0);
                    }
                });
                
                const effectiveLoraPaths = loraPaths;
                const effectiveLoraStrengths = loraStrengths;
                const loraPath = effectiveLoraPaths[0] || null;
                const loraStrength = effectiveLoraStrengths[0] || 1.0;
                const modelPath = getSelectedModelCheckpointPath();
                
                if (batchImages.length < 2) {
                    throw new Error(_t('errBatchMinImages'));
                }

                if (batchWorkflowIsSingle()) {
                    captureBatchKfTimelineFromDom();
                    const fps = document.getElementById('vid-fps').value;
                    const nKf = batchImages.length;
                    const minSeg = 0.1;
                    const frameDurs = [];
                    for (let j = 0; j < nKf; j++) {
                        let v = parseFloat(document.getElementById(`batch-kf-seg-dur-${j}`)?.value);
                        if (!Number.isFinite(v) || v < minSeg) v = minSeg;
                        frameDurs.push(v);
                    }
                    const sumSec = frameDurs.reduce((a, b) => a + b, 0);
                    const dur = Math.max(2, Math.ceil(sumSec - 1e-9));
                    const times = [0];
                    let acc = 0;
                    for (let j = 0; j < nKf - 1; j++) {
                        acc += frameDurs[j];
                        times.push(acc);
                    }
                    const combinedPrompt = prompt.trim();
                    if (!combinedPrompt) {
                        throw new Error(_t('errSingleKfPrompt'));
                    }
                    const strengths = [];
                    for (let i = 0; i < nKf; i++) {
                        const sEl = document.getElementById(`batch-kf-strength-${i}`);
                        let sv = parseFloat(sEl?.value);
                        if (!Number.isFinite(sv)) {
                            sv = parseFloat(defaultKeyframeStrengthForIndex(i, nKf));
                        }
                        if (!Number.isFinite(sv)) sv = 1;
                        sv = Math.max(0.1, Math.min(1.0, sv));
                        strengths.push(sv);
                    }
                    endpoint = '/api/generate';
                    const batchMotionSpeed = parseFloat(document.getElementById('batch-motion-speed')?.value) || 1.0;
                    payload = {
                        prompt: combinedPrompt,
                        resolution: res,
                        model: "ltx-2",
                        cameraMotion: document.getElementById('vid-motion')?.value || 'none',
                        negativePrompt: "low quality, blurry, noisy, static noise, distorted",
                        duration: String(dur),
                        fps,
                        audio: "false",
                        imagePath: null,
                        audioPath: null,
                        startFramePath: null,
                        endFramePath: null,
                        keyframePaths: batchImages.map((b) => b.path),
                        keyframeStrengths: strengths,
                        keyframeTimes: times,
                        aspectRatio: document.getElementById('batch-ratio')?.value || '16:9',
                        customWidth: batchGenSize.width,
                        customHeight: batchGenSize.height,
                        loraPath: loraPath,
                        loraStrength: loraStrength,
                        loraPaths: effectiveLoraPaths.length > 0 ? effectiveLoraPaths : null,
                        loraStrengths: effectiveLoraStrengths.length > 0 ? effectiveLoraStrengths : null,
                        modelPath: modelPath || null,
                        motionSpeed: batchMotionSpeed,
                    };
                    addLog(
                        `单次多关键帧: ${nKf} 帧时长合计 ${sumSec.toFixed(1)}s → 请求时长 ${dur}s, ${res}, FPS ${fps}, 运动速度: ${batchMotionSpeed}x`
                    );
                } else {
                    const segments = [];
                    for (let i = 0; i < batchImages.length - 1; i++) {
                        const duration = parseFloat(document.getElementById(`batch-segment-duration-${i}`)?.value || 5);
                        const segmentPrompt = document.getElementById(`batch-segment-prompt-${i}`)?.value || '';
                        const segParts = [prompt.trim(), segmentPrompt.trim()].filter(Boolean);
                        const combinedSegPrompt = segParts.join(', ');
                        segments.push({
                            startImage: batchImages[i].path,
                            endImage: batchImages[i + 1].path,
                            duration: duration,
                            prompt: combinedSegPrompt
                        });
                    }

                    endpoint = '/api/generate-batch';
                    const bgAudioEl = document.getElementById('batch-background-audio-path');
                    const backgroundAudioPath = (bgAudioEl && bgAudioEl.value) ? bgAudioEl.value.trim() : null;
                    const batchSegMotionSpeed = parseFloat(document.getElementById('batch-motion-speed')?.value) || 1.0;
                    payload = {
                        segments: segments,
                        resolution: res,
                        model: "ltx-2",
                        aspectRatio: document.getElementById('batch-ratio')?.value || '16:9',
                        customWidth: batchGenSize.width,
                        customHeight: batchGenSize.height,
                        loraPath: loraPath,
                        loraStrength: loraStrength,
                        loraPaths: effectiveLoraPaths.length > 0 ? effectiveLoraPaths : null,
                        loraStrengths: effectiveLoraStrengths.length > 0 ? effectiveLoraStrengths : null,
                        negativePrompt: "low quality, blurry, noisy, static noise, distorted",
                        backgroundAudioPath: backgroundAudioPath || null,
                        modelPath: modelPath || null,
                        motionSpeed: batchSegMotionSpeed,
                    };
                    addLog(`分段拼接: ${segments.length} 段, ${res}, 运动速度: ${batchSegMotionSpeed}x${backgroundAudioPath ? '，含统一配乐' : ''}`);
                }
            }

            if (useQueue) {
                _awaitingQueueStart = true;
                if (pendingReplaySeedByMode[currentMode]) {
                    payload.seed = Number(pendingReplaySeedByMode[currentMode]);
                    delete pendingReplaySeedByMode[currentMode];
                }
                console.log('[RUN] Submitting to queue:', { mode: currentMode, endpoint, payloadKeys: Object.keys(payload || {}) });
                const queueInfo = await submitQueuedTask(
                    currentMode,
                    endpoint,
                    payload,
                    `${currentMode}: ${(payload.prompt || prompt || '').slice(0, 48) || 'untitled'}`
                );
                console.log('[RUN] Queue submit result:', queueInfo);
                addLog(_fmt('queueSubmitLog', { id: queueInfo.task_id, n: Math.max(0, (queueInfo.position || 1) - 1) }));
                if (getSeedMode() === 'random') refreshRandomSeedValue();
                if (queueInfo.task_id) {
                    _currentQueueTaskId = queueInfo.task_id;
                }
                _queueSubmitOk = true;
            } else {
                payload = ensureReplaySeed(payload);
                const res = await fetch(BASE + endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                let data;
                try { data = await res.json(); } catch(_) { data = {}; }
                if (!res.ok) {
                    if (res.status === 503 || data.status === 'offline') {
                        throw new Error('后端服务未启动，请先启动后端服务再渲染');
                    }
                    if (res.status === 504 || data.status === 'timeout') {
                        throw new Error('后端服务响应超时，请检查后端状态');
                    }
                    const errMsg = data.error || data.detail || "API 拒绝了请求";
                    throw new Error(typeof errMsg === 'string' ? errMsg : JSON.stringify(errMsg));
                }

                const rawPath = data.image_paths ? data.image_paths[0] : data.video_path;
                if (rawPath) {
                    try { displayOutput(rawPath); } catch (dispErr) { addLog(`⚠️ 播放器显示异常: ${dispErr.message}`); }
                }

                setTimeout(() => {
                    isLoadingHistory = false;
                    if (typeof fetchHistory === 'function') fetchHistory(1);
                }, 500);
                if (getSeedMode() === 'random') refreshRandomSeedValue();
            }

        } catch (e) {
            console.error('[RUN] Generation failed:', e);
            removeCurrentLoadingCard();
            const errText = e && e.message ? e.message : String(e);
            addLog(`❌ 渲染中断: ${errText}`);
            const loader = document.getElementById('loading-txt');
            if (loader) {
                loader.style.display = 'flex';
                loader.textContent = '';
                const span = document.createElement('span');
                span.style.cssText = 'color:var(--text-sub);font-size:13px;padding:12px;text-align:center;';
                span.textContent = `渲染失败：${errText}`;
                loader.appendChild(span);
            }

        } finally {
            if (useQueue && _queueSubmitOk) {
                checkStatus();
            } else {
                _isGeneratingFlag = false;
                _awaitingQueueStart = false;
                _currentQueueTaskId = null;
                _updateGenButtons();
                if (!useQueue) {
                    removeCurrentLoadingCard();
                    stopProgressPolling();
                }
                checkStatus();
                if (!useQueue) {
                    setTimeout(() => { clearGpu(); }, 500);
                }
            }
        }
    }

    window.run = run;
    window.switchMode = switchMode;

    let _feDebugEnabled = false;
    let _feDebugBuffer = [];
    let _feDebugSendTimer = null;
    const _FE_DEBUG_SEND_INTERVAL = 2000;
    const _FE_DEBUG_MAX_BUFFER = 50;

    function toggleFeDebug() {
        _feDebugEnabled = !_feDebugEnabled;
        localStorage.setItem('yunji_fe_debug', _feDebugEnabled ? '1' : '0');
        _updateFeDebugBtn();
        if (_feDebugEnabled) {
            addLog('🐛 前端调试已开启，JS错误将实时上报到运行日志');
            _feDebugBuffer = [];
            _startFeDebugSendLoop();
        } else {
            addLog('🐛 前端调试已关闭');
            if (_feDebugSendTimer) { clearInterval(_feDebugSendTimer); _feDebugSendTimer = null; }
            if (_feDebugBuffer.length > 0) { _flushFeDebugBuffer(); }
        }
    }

    function _updateFeDebugBtn() {
        const btn = document.getElementById('feDebugBtn');
        if (btn) {
            btn.textContent = _feDebugEnabled ? _t('feDebugOn') : _t('feDebugOff');
            btn.style.opacity = _feDebugEnabled ? '1' : '0.7';
            btn.style.borderColor = _feDebugEnabled ? 'var(--accent)' : '';
            btn.style.color = _feDebugEnabled ? 'var(--accent)' : '';
        }
    }

    function _restoreFeDebugState() {
        const saved = localStorage.getItem('yunji_fe_debug');
        if (saved === '1') {
            _feDebugEnabled = true;
            _updateFeDebugBtn();
            _feDebugBuffer = [];
            _startFeDebugSendLoop();
        }
    }

    function _feDebugCapture(level, msg, source, line, col) {
        if (!_feDebugEnabled) return;
        const entry = {
            ts: new Date().toISOString(),
            level: level || 'error',
            msg: String(msg || '').slice(0, 500),
            src: source ? String(source).split('/').pop().split('\\').pop() : '',
            line: line || 0,
            col: col || 0,
        };
        _feDebugBuffer.push(entry);
        if (_feDebugBuffer.length > _FE_DEBUG_MAX_BUFFER) {
            _feDebugBuffer.shift();
        }
    }

    function _startFeDebugSendLoop() {
        if (_feDebugSendTimer) return;
        _feDebugSendTimer = setInterval(() => {
            if (_feDebugBuffer.length > 0) _flushFeDebugBuffer();
        }, _FE_DEBUG_SEND_INTERVAL);
    }

    async function _flushFeDebugBuffer() {
        if (!_feDebugBuffer.length) return;
        const batch = _feDebugBuffer.splice(0, _feDebugBuffer.length);
        try {
            await fetch(`${BASE}/api/system/frontend-log`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ entries: batch }),
            });
        } catch (_) {}
    }

    window.addEventListener('error', function(e) {
        _feDebugCapture('error', e.message, e.filename, e.lineno, e.colno);
    });
    window.addEventListener('unhandledrejection', function(e) {
        const reason = e.reason;
        const msg = reason instanceof Error ? `${reason.message}\n${reason.stack || ''}` : String(reason);
        _feDebugCapture('error', msg, '', 0, 0);
    });

    const _origConsoleError = console.error;
    console.error = function(...args) {
        _feDebugCapture('error', args.map(a => {
            try { return typeof a === 'object' ? JSON.stringify(a) : String(a); } catch(_) { return String(a); }
        }).join(' '), '', 0, 0);
        _origConsoleError.apply(console, args);
    };

    function togglePanel(panelId) {
        const panels = ['env-settings', 'sys-settings'];
        const btnMap = { 'env-settings': 'button[onclick*="env-settings"]', 'sys-settings': 'button[onclick*="sys-settings"]' };
        const target = document.getElementById(panelId);
        if (!target) return;
        const isCurrentlyOpen = target.style.display !== 'none';
        panels.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.style.display = 'none';
            const btn = document.querySelector(btnMap[id]);
            if (btn) btn.classList.remove('active');
        });
        if (!isCurrentlyOpen) {
            target.style.display = 'block';
            const btn = document.querySelector(btnMap[panelId]);
            if (btn) btn.classList.add('active');
        }
    }

    async function clearGpu() {
        const btn = document.getElementById('clearGpuBtn');
        if (!btn) return;
        btn.disabled = true;
        btn.innerText = _t('clearingVram');
        try {
            const res = await fetch(`${BASE}/api/system/clear-gpu`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            const data = await res.json();
            if (res.ok) {
                addLog(`🧹 显存清理成功: ${data.message}`);
                checkStatus();
                setTimeout(checkStatus, 1000); 
            } else {
                const errMsg = data.error || data.detail || "后端未实现此接口 (404)";
                throw new Error(errMsg);
            }
        } catch(e) {
            addLog(`❌ 清理显存失败: ${e.message}`);
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerText = _t('clearVram');
            }
        }
    }

    async function listGpus() {
        try {
            const res = await fetch(`${BASE}/api/system/list-gpus`);
            const data = await res.json();
            if (res.ok && data.gpus) {
                const selector = document.getElementById('gpu-selector');
                if (selector) selector.innerHTML = data.gpus.map(g => 
                    `<option value="${g.id}" ${g.active ? 'selected' : ''}>GPU ${g.id}: ${g.name} (${g.vram})</option>`
                ).join('');
                
                const activeGpu = data.gpus.find(g => g.active);
                const gpuNameEl = document.getElementById('gpu-name');
                if (activeGpu && gpuNameEl) gpuNameEl.innerText = activeGpu.name;
            }
        } catch (e) {
            console.error("Failed to list GPUs", e);
        }
    }

    async function switchGpu(id) {
        if (!id) return;
        addLog(`🔄 正在切换到 GPU ${id}...`);
        try {
            const res = await fetch(`${BASE}/api/system/switch-gpu`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ gpu_id: parseInt(id) })
            });
            const data = await res.json();
            if (res.ok) {
                addLog(`✅ 已成功切换到 GPU ${id}，模型将重新加载。`);
                listGpus(); // 重新获取列表以同步状态
                setTimeout(checkStatus, 1000);
            } else {
                throw new Error(data.error || "切换失败");
            }
        } catch (e) {
            addLog(`❌ GPU 切换失败: ${e.message}`);
        }
    }

    function startProgressPolling() {
        if (pollInterval) clearInterval(pollInterval);
        let offlineCount = 0;
        pollInterval = setInterval(async () => {
            try {
                const res = await fetch(`${BASE}/api/generation/progress`);
                if (res.status === 503 || res.status === 504) {
                    offlineCount++;
                    if (offlineCount >= 3) {
                        stopProgressPolling();
                        addLog('❌ 后端服务不可用，已停止进度轮询');
                    }
                    return;
                }
                offlineCount = 0;
                const d = await res.json();
                if (d.progress > 0) {
                    const logMsg = d.log_message ?? d.logMessage ?? '';

                    const pf = document.getElementById('progress-fill');
                    if (pf) pf.style.width = d.progress + "%";

                    const progressText = `${d.progress}%`;
                    const statusText = logMsg || '';

                    const loaderStep = document.getElementById('loader-step-text');
                    if (loaderStep) {
                        loaderStep.innerHTML = `<span style="font-size:18px;font-weight:800;color:var(--text-main);">${progressText}</span>${statusText ? `<span style="font-size:12px;font-weight:600;color:var(--text-dim);margin-left:8px;">${statusText}</span>` : ''}`;
                    } else {
                        const loadingTxt = document.getElementById('loading-txt');
                        if (loadingTxt) loadingTxt.textContent = `${progressText} ${statusText}`;
                    }

                    const cardStep = document.getElementById('loading-card-step');
                    if (cardStep) cardStep.textContent = `${progressText} ${statusText}`;

                    const qMeta = document.querySelector('[data-qmeta]');
                    if (qMeta) qMeta.textContent = `${progressText} ${statusText}`;
                    const qPct = document.querySelector('[data-qpct]');
                    if (qPct) qPct.textContent = progressText;
                    const qBar = document.querySelector('[data-qbar]');
                    if (qBar) qBar.style.width = Math.min(100, d.progress) + '%';
                }
            } catch(e) {}
        }, 1000);
    }

    function stopProgressPolling() {
        clearInterval(pollInterval);
        pollInterval = null;
        const pf = document.getElementById('progress-fill');
        if (pf) pf.style.width = "0%";
        const lc = document.getElementById('current-loading-card');
        if (lc) lc.remove();
    }

    function getAudioMime(filename) {
        const ext = String(filename || '').split('.').pop().toLowerCase();
        const map = { wav: 'audio/wav', mp3: 'audio/mpeg', flac: 'audio/flac', ogg: 'audio/ogg', m4a: 'audio/mp4', aac: 'audio/aac' };
        return map[ext] || 'audio/wav';
    }

function toggleAudioPreviewPlayback() {
        const audio = document.getElementById('res-audio');
        if (audioPlayer) {
            if (audioPlayer.playing) {
                audioPlayer.pause();
                return;
            }
            const playPromise = audioPlayer.play();
            if (playPromise && typeof playPromise.catch === 'function') {
                playPromise.catch(() => {});
            }
            return;
        }
        if (!audio) return;
        if (audio.paused) {
            const playPromise = audio.play();
            if (playPromise && typeof playPromise.catch === 'function') {
                playPromise.catch(() => {});
            }
        } else {
            audio.pause();
        }
    
    }
    window.toggleAudioPreviewPlayback = toggleAudioPreviewPlayback;

    function initAudioPreviewToggle() {
        const art = document.getElementById('audio-preview-art');
        if (!art || art.dataset.toggleBound === '1') return;
        art.dataset.toggleBound = '1';
        art.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            window.toggleAudioPreviewPlayback();
        });
        art.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            e.preventDefault();
            window.toggleAudioPreviewPlayback();
        });
    }

    // ========== 历史预览代次 + per-file 稳定 cache buster ==========
    // 1) _previewReqId 单调递增,每次 displayOutput 自增,过期回调直接短路,
    //    避免用户快速点击 A → B → C 时,A/B 的旧 setTimeout / loadeddata / error
    //    覆盖到 C 的最终态(导致"显示的不是选中的那张")
    // 2) _fileBusterMap 同文件重复点击复用同一个 v=,浏览器命中缓存秒开;
    //    fetchHistory 列表刷新时调用 __invalidateAllFileBusters() 清除全部,
    //    新生成的文件能立即拿到新 URL(不被旧缓存挡掉)
    let _previewReqId = 0;
    const _fileBusterMap = new Map();
    function _isPreviewStale(myReqId) { return myReqId !== _previewReqId; }
    function _getFileBuster(fileKey) {
        let b = _fileBusterMap.get(fileKey);
        if (!b) { b = String(Date.now()); _fileBusterMap.set(fileKey, b); }
        return b;
    }
function __invalidateFileBuster(fileKey) {
        if (fileKey) _fileBusterMap.delete(fileKey);
    
    }
    window.__invalidateFileBuster = __invalidateFileBuster;
function __invalidateAllFileBusters() {
        _fileBusterMap.clear();
    
    }
    window.__invalidateAllFileBusters = __invalidateAllFileBusters;

    function displayOutput(fileOrPath, outputType = null, options = {}) {
        // ★ 抢占代次,本次调用从此刻起就是"最新"
        const myReqId = ++_previewReqId;
        const img = document.getElementById('res-img');
        const vid = document.getElementById('res-video');
        const audio = document.getElementById('res-audio');
        const audioWrapper = document.getElementById('audio-wrapper');
        const audioTitle = document.getElementById('audio-preview-title');
        const loader = document.getElementById('loading-txt');
        const videoWrapper = document.getElementById('video-wrapper');
        const previewDownloadBtn = document.getElementById('preview-download-btn');
        const previewReplayActions = document.getElementById('preview-replay-actions');
        const effectiveType = outputType || currentMode;
        const silentLog = !!options.silentLog;

        currentPreviewAssetUrl = '';
        currentPreviewAssetName = '';
        currentPreviewReplayId = options.replay ? storeReplayRecord(options.replay) : (options.replayId || '');
        if (previewDownloadBtn) previewDownloadBtn.style.display = 'none';
        if (previewReplayActions) {
            previewReplayActions.style.display = 'none';
            previewReplayActions.classList.toggle('is-unavailable', !currentPreviewReplayId);
        }

        if (img) img.style.display = "none";
        if (videoWrapper) videoWrapper.style.display = "none";
        if (audioWrapper) audioWrapper.style.display = "none";

        if (player) {
            try { player.pause(); } catch(_) {}
        }
        if (vid) {
            vid.pause();
            // 清 src 触发浏览器中止上一个请求(避免 A/B 抢带宽)
            vid.removeAttribute('src');
            try { vid.load(); } catch(_) {}
        }
        if (audio) {
            audio.pause();
            audio.removeAttribute('src');
            try { audio.load(); } catch(_) {}
        }
        if (audioPlayer) {
            try { audioPlayer.stop(); } catch(_) {}
        }

        // ★ per-file 稳定 cache buster:同文件复用,新生成时才换
        const fileKey = `${effectiveType}::${fileOrPath}`;
        const buster = _getFileBuster(fileKey);

        let url = "";
        let fileName = fileOrPath;
        if (fileOrPath.indexOf('\\') !== -1 || fileOrPath.indexOf('/') !== -1) {
            url = `${BASE}/api/system/file?path=${encodeURIComponent(fileOrPath)}&v=${buster}`;
            fileName = fileOrPath.split(/[\\/]/).pop();
        } else {
            const outInput = document.getElementById('global-out-dir');
            const globalDir = outInput ? outInput.value.replace(/\\/g, '/').replace(/\/$/, '') : "";
            if (globalDir && globalDir !== "") {
                url = `${BASE}/api/system/file?path=${encodeURIComponent(globalDir + '/' + fileOrPath)}&v=${buster}`;
            } else {
                // ★ 对 filename 做 URL 编码,避免含空格/中文等特殊字符的文件名导致 404
                url = `${BASE}/outputs/${encodeURIComponent(fileOrPath)}?v=${buster}`;
            }
        }

        console.log("[DEBUG][displayOutput] reqId:", myReqId, "fileOrPath:", fileOrPath);
        console.log("[DEBUG][displayOutput] effectiveType:", effectiveType);
        console.log("[DEBUG][displayOutput] Constructed URL:", url);

        if (loader) loader.style.display = "none";
        if (effectiveType === 'audio') {
            currentPreviewAssetUrl = url;
            currentPreviewAssetName = fileName || 'preview.wav';
            if (previewDownloadBtn) previewDownloadBtn.style.display = 'inline-flex';
            if (audioTitle) audioTitle.textContent = fileName;
            if (audioWrapper) audioWrapper.style.display = "flex";
            if (audioPlayer) {
                audioPlayer.source = {
                    type: 'audio',
                    sources: [{ src: url, type: getAudioMime(fileName) }]
                };
                const playPromise = audioPlayer.play();
                if (playPromise && typeof playPromise.catch === 'function') {
                    playPromise.catch(() => {});
                }
            } else if (audio) {
                audio.src = url;
                try { audio.load(); } catch(_) {}
                const playPromise = audio.play();
                if (playPromise && typeof playPromise.catch === 'function') {
                    playPromise.catch(() => {});
                }
            }
            if (!silentLog) addLog(`✅ 音频加载成功: ${fileName}`);
        } else if (effectiveType === 'image') {
            currentPreviewAssetUrl = url;
            currentPreviewAssetName = fileName || 'preview.png';
            if (previewDownloadBtn) previewDownloadBtn.style.display = 'inline-flex';
            if (previewReplayActions) previewReplayActions.style.display = 'inline-flex';
            refreshPreviewReplayFromFile(fileOrPath, fileName);
            // ★ 旧 onload/onerror 清理,避免上一张图的回调在切到新图后触发
            img.onload = null;
            img.onerror = null;
            img.onload = () => {
                if (_isPreviewStale(myReqId)) return;
                img.style.display = "block";
            };
            img.onerror = () => {
                if (_isPreviewStale(myReqId)) return;
                console.warn('[IMG] failed to load:', url);
            };
            img.src = url;
            if (!silentLog) addLog(`✅ 图像渲染成功: ${fileName}`);
        } else {
            if (videoWrapper) videoWrapper.style.display = "flex";
            currentPreviewAssetUrl = url;
            currentPreviewAssetName = fileName || 'preview.mp4';
            if (previewDownloadBtn) previewDownloadBtn.style.display = 'inline-flex';
            if (previewReplayActions) previewReplayActions.style.display = 'inline-flex';
            refreshPreviewReplayFromFile(fileOrPath, fileName);

            let _videoRetryCount = 0;
            const _videoMaxRetries = 2;
            const _altUrls = [];
            // alt url 也用同一个 buster(同文件不同表达路径)
            if (url.includes('/api/system/file')) {
                _altUrls.push(url.replace('/api/system/file?path=', '/outputs/').split('&v=')[0].replace(/%2F/g, '/').replace(/%5C/g, '/') + '?v=' + buster);
            } else if (url.includes('/outputs/')) {
                const outInput2 = document.getElementById('global-out-dir');
                const globalDir2 = outInput2 ? outInput2.value.replace(/\\/g, '/').replace(/\/$/, '') : "";
                if (globalDir2) {
                    const fname2 = url.split('/outputs/')[1].split('?')[0];
                    _altUrls.push(`${BASE}/api/system/file?path=${encodeURIComponent(globalDir2 + '/' + fname2)}&v=${buster}`);
                }
            }

            function _onVideoError(err) {
                if (_isPreviewStale(myReqId)) return;   // ★ 过期短路
                console.error("[VIDEO-PROTECT] Video error:", err);
                if (vid && vid.error) {
                    console.error("[VIDEO-PROTECT] Error code:", vid.error.code, "message:", vid.error.message);
                }
                if (_videoRetryCount < _videoMaxRetries && _altUrls.length > 0) {
                    _videoRetryCount++;
                    const retryUrl = _altUrls[0] + (_altUrls[0].includes('?') ? '&' : '?') + 'retry=' + _videoRetryCount + '&v=' + buster;
                    console.log("[VIDEO-PROTECT] Retrying with alt URL, attempt:", _videoRetryCount);
                    if (!silentLog) addLog(`⚠️ 视频加载失败，尝试备用路径 (${_videoRetryCount}/${_videoMaxRetries})...`);
                    _tryPlayVideo(retryUrl);
                } else if (player) {
                    console.log("[VIDEO-PROTECT] Plyr failed, falling back to native video");
                    if (!silentLog) addLog(`⚠️ Plyr播放器失败，尝试原生播放器...`);
                    try { player.destroy(); } catch(_) {}
                    player = null;
                    _tryPlayVideoNative(url);
                } else {
                    if (!silentLog) addLog(`❌ 视频无法播放，请尝试下载后本地查看`);
                }
            }

            function _tryPlayVideoNative(videoUrl) {
                if (!vid) return;
                if (_isPreviewStale(myReqId)) return;  // ★ 过期短路
                vid.removeEventListener('error', _onVideoError);
                vid.addEventListener('error', _onVideoError);
                vid.preload = 'auto';
                vid.src = videoUrl;
                try { vid.load(); } catch(_) {}
                // ★ 等 canplay 事件再 play,避免 readyState 不足导致播放失败
                const _onCanPlay = () => {
                    vid.removeEventListener('canplay', _onCanPlay);
                    if (_isPreviewStale(myReqId)) return;
                    try {
                        const p = vid.play();
                        if (p && typeof p.catch === 'function') p.catch(() => {});
                    } catch(_) {}
                };
                if (vid.readyState >= 3) {
                    _onCanPlay();
                } else {
                    vid.addEventListener('canplay', _onCanPlay);
                }
            }

            function _tryPlayVideo(videoUrl) {
                if (!vid) return;
                if (_isPreviewStale(myReqId)) return;  // ★ 过期短路
                vid.removeEventListener('error', _onVideoError);
                vid.addEventListener('error', _onVideoError);
                if (player) {
                    try {
                        player.source = {
                            type: 'video',
                            sources: [{ src: videoUrl, type: 'video/mp4' }]
                        };
                    } catch(e) {
                        console.error("[VIDEO-PROTECT] player.source setter failed:", e);
                        try { player.destroy(); } catch(_) {}
                        player = null;
                        _tryPlayVideoNative(videoUrl);
                        return;
                    }
                    const _onLoaded = () => {
                        if (_isPreviewStale(myReqId)) {  // ★ 过期短路
                            vid.removeEventListener('loadeddata', _onLoaded);
                            vid.removeEventListener('error', _onLoadError);
                            return;
                        }
                        vid.removeEventListener('loadeddata', _onLoaded);
                        vid.removeEventListener('error', _onLoadError);
                        try {
                            const p = player.play();
                            if (p && typeof p.catch === 'function') p.catch(() => {});
                        } catch(_) {}
                    };
                    const _onLoadError = () => {
                        if (_isPreviewStale(myReqId)) return;  // ★
                        vid.removeEventListener('loadeddata', _onLoaded);
                        vid.removeEventListener('error', _onLoadError);
                    };
                    vid.addEventListener('loadeddata', _onLoaded);
                    vid.addEventListener('error', _onLoadError);
                    setTimeout(() => {
                        if (_isPreviewStale(myReqId)) {  // ★ 3000ms 兜底也检查代次
                            vid.removeEventListener('loadeddata', _onLoaded);
                            vid.removeEventListener('error', _onLoadError);
                            return;
                        }
                        vid.removeEventListener('loadeddata', _onLoaded);
                        vid.removeEventListener('error', _onLoadError);
                        if (vid.readyState >= 2) {
                            try {
                                const p = player.play();
                                if (p && typeof p.catch === 'function') p.catch(() => {});
                            } catch(_) {}
                        }
                    }, 3000);
                } else {
                    _tryPlayVideoNative(videoUrl);
                }
            }

            _tryPlayVideo(url);
            if (!silentLog) addLog(`✅ 视频加载中: ${fileName}`);
        }
    }

function downloadCurrentPreviewAsset() {
        if (!currentPreviewAssetUrl) {
            addLog(_t('previewNoDownload'));
            return;
        }
        const a = document.createElement('a');
        a.href = currentPreviewAssetUrl;
        a.download = currentPreviewAssetName || 'preview.bin';
        document.body.appendChild(a);
        a.click();
        a.remove();
    
    }
    window.downloadCurrentPreviewAsset = downloadCurrentPreviewAsset;



    function addLog(msg) {
        const log = document.getElementById('log');
        if (!log) {
            console.log('[LTX]', msg);
            return;
        }
        const time = new Date().toLocaleTimeString();
        log.innerHTML += `<div style="margin-bottom:5px"> <span style="color:var(--text-dim)">[${time}]</span> ${msg}</div>`;
        log.scrollTop = log.scrollHeight;
    }


    function _onDomReady() {
        try {
        switchMode('video');
        refreshRandomSeedValue();
        updateSeedModeUI();
        initPresetChips();
        } catch(e) { console.error('[INIT] _onDomReady error:', e); }
        fetch(`${BASE}/api/app-info`)
            .then(r => r.json())
            .then(data => {
                const nameEl = document.getElementById('app-name');
                if (nameEl && data.app_name) nameEl.textContent = data.app_name;
                if (data.version) window.__appVersion = data.version;
            })
            .catch(() => {});
        // ★ 2026-06-17: 初始化"我的作品 → 输入框"拖拽支持
        try { setupHistoryDragDrop(); } catch (e) { console.warn('[dragdrop] init failed:', e); }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _onDomReady);
    } else {
        _onDomReady();
    }

    /**
     * 预设下拉(帧率/时长)与数字输入双向联动
     * - select 有预设值 + "自定义" 选项
     * - input 是数字输入,始终可见,代表"真实值"
     * - 联动规则:
     *   ① select 选预设 → 写入 input.value
     *   ② select 选"自定义" → 聚焦 input 不改值
     *   ③ input 改值 → 如果新值在预设列表里,select 跟着切到对应项;否则切到"自定义"
     * - 初始化:input.value 已是推荐值,select 同步;不在预设里则 select 显示"自定义"
     * - 持久化:沿用现有 PERSIST_INPUTS 机制(已包含 vid-fps / vid-duration),无需额外改动
     *   select 的值由 input 推导,不需要单独存
     */
    function initPresetChips() {
        // 一组配置: [inputId, selectId, presetValues]
        const groups = [
            { input: 'vid-fps',         select: 'vid-fps-select',         presets: ['8','12','16','24','25','30','48','60'] },
            { input: 'vid-duration',    select: 'vid-duration-select',    presets: ['3','5','8','10','12','15','20','30'] },
            { input: 'motion-fps',      select: 'motion-fps-select',      presets: ['8','12','16','24','25','30','48','60'] },
            { input: 'motion-duration', select: 'motion-duration-select', presets: ['3','5','8','10','12','15','20','30'] },
        ];
        groups.forEach(({ input, select, presets }) => {
            const inputEl = document.getElementById(input);
            const selectEl = document.getElementById(select);
            if (!inputEl || !selectEl) return;

            // 1) 初始同步 select ↔ input
            syncPresetSelect(inputEl, selectEl, presets);

            // 2) select 改变 → 更新 input(选"自定义"则不更,聚焦 input)
            selectEl.addEventListener('change', () => {
                const v = selectEl.value;
                if (v === '__custom__') {
                    inputEl.focus();
                    inputEl.select();
                    return;
                }
                if (String(inputEl.value) !== v) {
                    inputEl.value = v;
                    inputEl.dispatchEvent(new Event('input', { bubbles: true }));
                    inputEl.dispatchEvent(new Event('change', { bubbles: true }));
                }
            });

            // 3) input 改变 → 反向同步 select(匹配预设或切到"自定义")
            inputEl.addEventListener('input', () => syncPresetSelect(inputEl, selectEl, presets));
        });
    }
    function syncPresetSelect(inputEl, selectEl, presets) {
        const cur = String(inputEl.value);
        // 预设列表里能匹配上 → 选中那一项;否则切到"自定义"
        if (presets.indexOf(cur) >= 0) {
            selectEl.value = cur;
        } else {
            selectEl.value = '__custom__';
        }
    }


    
    
    
    
    
    
    
    
    
    
    let currentHistoryPage = 1;          // 当前已加载到的页码(从 1 开始)
    let isLoadingHistory = false;
    // ★ 2026-06-16 v2: 首次进入"我的作品"只拉 5 个,首屏 5 张卡片秒开
    //   5 个 × (replay 775B + gen_info 679B) ≈ 7 KB,后端响应 gzip 后 < 5 KB
    //   适合"快速进入随手点作品"的体验
    //   滚动到底部时由 IntersectionObserver 自动再拉 5 个
    const HISTORY_PAGE_LIMIT = 5;        // 首次加载数量
    const HISTORY_PAGE_MORE = 5;         // 每次"加载更多"自动追加数量
    const HISTORY_THUMB_CONCURRENCY = 8; // ★ 图片缩略图并发度,后端API返回图片无需前端解码
    let _historyListFingerprint = '';
    let _historyHasMore = true;          // 后端是否还有更多页(total_pages > current_page)
    let _historyTotalPages = 0;          // ★ 2026-06-16 v3: 用于 IO 防御性检查,避免越过末页
    let _historyHasRendered = false;
    const _historyRenderedKeys = new Set();
    let historyThumbLoaderBusy = false;
    let historyThumbLoadQueued = false;
    let historyThumbLoaderToken = 0;
    let _historyLayout = localStorage.getItem('historyLayout') || 'list';
    let _historySentinelObserver = null;  // ★ 2026-06-16 v2: 触底加载的 IO
    let _historySentinel = null;          // 滚动到容器底部时显示的"加载中/已加载完"哨兵元素
    // ★ 2026-06-16 v4 提速: 首页 5 个作品的 localStorage 缓存
    //   F5 瞬间显示,不等网络回来(网络是 304 也好、200 也好,用户先看到东西)
    //   缓存时间: 24h(超过 24h 直接当过期,等网络)
    //   key: 包含 limit(=5) 让"加载更多"不会命中这个缓存
    const _HISTORY_LOCAL_CACHE_KEY = '__yunji_history_p1_v4';
    const _HISTORY_LOCAL_CACHE_TTL = 24 * 60 * 60 * 1000;  // 24h
    function _loadHistoryFromLocal(page, limit) {
        if (page !== 1 || limit !== HISTORY_PAGE_LIMIT) return null;
        try {
            const raw = localStorage.getItem(_HISTORY_LOCAL_CACHE_KEY);
            if (!raw) return null;
            const wrap = JSON.parse(raw);
            if (!wrap || !wrap.savedAt || !wrap.data) return null;
            if (Date.now() - wrap.savedAt > _HISTORY_LOCAL_CACHE_TTL) return null;
            return wrap.data;
        } catch (_) { return null; }
    }
    function _saveHistoryToLocal(page, limit, data) {
        if (page !== 1 || limit !== HISTORY_PAGE_LIMIT) return;
        try {
            localStorage.setItem(_HISTORY_LOCAL_CACHE_KEY, JSON.stringify({
                savedAt: Date.now(),
                data: data,
            }));
        } catch (_) {}
    }

    function setHistoryLayout(layout) {
        _historyLayout = layout;
        localStorage.setItem('historyLayout', layout);
        document.querySelectorAll('.layout-btn').forEach(b => b.classList.toggle('active', b.dataset.layout === layout));
        const container = document.getElementById('history-container');
        if (container) container.classList.toggle('layout-list', layout === 'list');
        resetHistoryAndReload();
    }

    function formatFileSize(bytes) {
        if (!bytes || bytes <= 0) return '-';
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
    }

    function formatDateTime(mtime) {
        if (!mtime) return '-';
        try {
            const d = new Date(mtime * 1000);
            if (isNaN(d.getTime())) return '-';
            const pad = n => String(n).padStart(2, '0');
            return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
        } catch { return '-'; }
    }

    function extractReplayInfo(item) {
        const gi = item.gen_info;
        if (gi && Object.keys(gi).length > 0) {
            return {
                prompt: gi.prompt || '',
                seed: gi.seed || '',
                width: gi.width || '',
                height: gi.height || '',
                fps: gi.fps || '',
                duration: gi.duration ? gi.duration + 's' : '',
                steps: gi.steps || '',
                cfg: gi.cfg || '',
                aspect_ratio: gi.aspect_ratio || '',
                camera_motion: gi.camera_motion || '',
                gen_method: gi.gen_method || '',
                loras: gi.loras || [],
                lora_details: gi.lora_details || [],
                elapsed: gi.elapsed || '',
                upscale_scale: gi.upscale_scale || '',
                upscale_actual_scale: gi.upscale_actual_scale || '',
                upscale_model: gi.upscale_model || '',
                upscale_engine: gi.upscale_engine || '',
                upscale_resize_mode: gi.upscale_resize_mode || '',
                upscale_original_size: gi.upscale_original_size || '',
                upscale_output_size: gi.upscale_output_size || '',
                upscale_mode: gi.upscale_mode || '',
                upscale_keep_ratio: gi.upscale_keep_ratio || false,
                upscale_frames: gi.upscale_frames || '',
                upscale_elapsed: gi.upscale_elapsed || '',
                upscale_original_fps: gi.upscale_original_fps || '',
                upscale_output_fps: gi.upscale_output_fps || '',
                upscale_duration: gi.upscale_duration || '',
            };
        }
        const replay = item.replay;
        if (!replay) return {};
        const payload = replay.payload || {};
        const loraPaths = payload.loraPaths || (payload.loraPath ? [payload.loraPath] : []);
        const loraStrengths = payload.loraStrengths || (payload.loraStrength ? [payload.loraStrength] : []);
        const loraDetails = loraPaths.map((p, i) => {
            const name = p ? p.split(/[/\\]/).pop().replace(/\.[^.]+$/, '') : '';
            return { name, strength: loraStrengths[i] || 1.0, path: p || '' };
        });
        const segments = Array.isArray(payload.segments) ? payload.segments : [];
        const segTotalDur = segments.reduce((s, seg) => s + (seg.duration || 0), 0);
        return {
            prompt: payload.prompt || (segments.length ? segments.map(s => s.prompt).filter(Boolean).join('; ') : ''),
            seed: payload.seed || '',
            width: payload.width || payload.image_width || payload.customWidth || '',
            height: payload.height || payload.image_height || payload.customHeight || '',
            fps: payload.fps || '',
            duration: payload.duration ? payload.duration + 's' : (segTotalDur ? segTotalDur + 's' : (payload.num_frames ? (payload.num_frames / (payload.fps || 24)).toFixed(1) + 's' : '')),
            steps: payload.num_inference_steps || payload.steps || '',
            cfg: payload.guidance_scale || payload.cfg || '',
            loras: loraDetails.map(l => l.strength !== 1.0 ? `${l.name}(${l.strength})` : l.name),
            lora_details: loraDetails,
        };
    }

    function showCopyright() {
        const modal = document.getElementById('copyright-modal');
        const verEl = document.getElementById('copyright-version');
        if (verEl) {
            const appVer = document.getElementById('app-version');
        verEl.textContent = appVer ? appVer.textContent : (window.__appVersion ? 'v' + window.__appVersion : '-');
        }
        if (modal) modal.style.display = 'flex';
    }

    function closeCopyright() {
        const modal = document.getElementById('copyright-modal');
        if (modal) modal.style.display = 'none';
    }

    function switchLibTab(tab) {
        const logContainer = document.getElementById('log-container');
        if (logContainer) logContainer.style.display = tab === 'log' ? 'flex' : 'none';
        const hw = document.getElementById('history-wrapper');
        if (hw) hw.style.display = tab === 'history' ? 'block' : 'none';
        
        const tabLog = document.getElementById('tab-log');
        if (tabLog) {
            tabLog.style.color = tab === 'log' ? 'var(--accent)' : 'var(--text-dim)';
            tabLog.style.borderColor = tab === 'log' ? 'var(--accent)' : 'transparent';
        }
        
        const tabHistory = document.getElementById('tab-history');
        if (tabHistory) {
            tabHistory.style.color = tab === 'history' ? 'var(--accent)' : 'var(--text-dim)';
            tabHistory.style.borderColor = tab === 'history' ? 'var(--accent)' : 'transparent';
        }
        
        if (tab === 'history') {
            // ★ 2026-06-16 v2: 切回 history tab 时重置回第一页 + 5 个
            //   (避免"上次滚到第3页,切走再切回还显示第3页"的不一致体验)
            return resetHistoryAndReload();
        }
        return Promise.resolve();
    }

    function historyItemKey(item) {
        return `${item?.type || ''}|${item?.filename || ''}`;
    }

    function historyMediaUrl(item, globalDir) {
        return (globalDir && globalDir !== "")
            ? `${BASE}/api/system/file?path=${encodeURIComponent(globalDir + '/' + item.filename)}`
            : `${BASE}/outputs/${item.filename}`;
    }

    function historyThumbUrl(item, globalDir) {
        // ★ 2026-06-17 提速 v3: 视频/图片都走专门的缩略图端点(磁盘缓存 + ETag 304)
        //   原: 图片用 historyMediaUrl → 直接返回原图(5-20MB),5 个图卡几十秒
        //   新: 用 /api/system/{video,image}-thumbnail → 缩到 360px 宽(几十 KB)
        const rawPath = item.fullpath || (globalDir ? globalDir + '/' + item.filename : item.filename);
        if (item.type === 'video') {
            return `${BASE}/api/system/video-thumbnail?path=${encodeURIComponent(rawPath)}&w=360&mtime=${encodeURIComponent(item.mtime || '')}&size=${encodeURIComponent(item.size || '')}`;
        }
        if (item.type === 'image') {
            return `${BASE}/api/system/image-thumbnail?path=${encodeURIComponent(rawPath)}&w=360&mtime=${encodeURIComponent(item.mtime || '')}&size=${encodeURIComponent(item.size || '')}`;
        }
        // audio / 其他类型:保持原 URL
        return historyMediaUrl(item, globalDir);
    }

    function renderHistoryCardHtml(item, globalDir) {
        const url = historyMediaUrl(item, globalDir);
        const thumbUrl = historyThumbUrl(item, globalDir);
        // safeFilename: JS字符串转义,仅用于 onclick 内的 JS 字符串字面量
        const safeFilename = item.filename.replace(/'/g, "\\'").replace(/"/g, '\\"');
        const safeGlobalDir = (globalDir || '').replace(/'/g, "\\'").replace(/"/g, '\\"');
        // htmlFilename/htmlFullpath/htmlGlobalDir: HTML属性转义,用于 data-* 属性
        const htmlFilename = escapeHtmlAttr(item.filename);
        const htmlFullpath = escapeHtmlAttr(item.fullpath || '');
        const htmlGlobalDir = escapeHtmlAttr(globalDir || '');
        const key = escapeHtmlAttr(historyItemKey(item));
        // ★ 2026-06-17: 改为懒加载 - 列表不再内嵌完整 replay
        //   旧: const replayId = item.replay_available && item.replay ? storeReplayRecord(item.replay) : '';
        const replayId = item.replay_available && item.fullpath ? storeLazyReplay(item.fullpath) : '';
        const typeBadge = item.type === 'video' ? '🎬 VID' : item.type === 'audio' ? '♪ AUD' : '🎨 IMG';
        const info = extractReplayInfo(item);
        const isUpscale = info.gen_method === '高清放大';
        const upscaleScaleText = isUpscale
            ? (info.upscale_actual_scale
                ? (info.upscale_actual_scale + 'x')
                : (info.upscale_scale ? info.upscale_scale + 'x' : '—'))
            : '';
        const upscaleModeText = isUpscale
            ? (info.upscale_mode
                ? (info.upscale_mode === '原始比例' ? '原始比例' : info.upscale_mode === '目标分辨率' ? '目标分辨率' : '按倍数')
                : (info.upscale_keep_ratio ? '原始比例' : info.upscale_output_size ? '目标分辨率' : '按倍数'))
            : '';

        if (_historyLayout === 'list') {
            const media = item.type === 'audio'
                ? `<div class="history-audio-thumb"><div class="history-audio-icon">♪</div></div>`
                : `<img data-src="${item.type === 'video' ? thumbUrl : url}" class="lazy-load history-thumb-media" alt="" style="object-fit: contain; max-width: 100%; max-height: 100%;">`;

            const pv = (v) => v || '—';
            let loraCell = pv('');
            if (info.loras && info.loras.length > 0) {
                const loraStr = info.loras.map(l => escapeHtmlAttr(l)).join(', ');
                let loraTitle = loraStr;
                let loraDescHtml = '';
                if (info.lora_details && info.lora_details.length > 0) {
                    const descParts = [];
                    info.lora_details.forEach(d => {
                        const meta = d.path ? availableLoras.find(l => l.path === d.path) : null;
                        const titleParts = [d.name + (d.strength !== 1.0 ? `(${d.strength})` : '')];
                        if (meta && meta.description) titleParts.push(meta.description);
                        if (meta && meta.trigger_words && meta.trigger_words.length > 0) titleParts.push('触发词: ' + meta.trigger_words.join(', '));
                        descParts.push(titleParts.join('\n'));
                        if (meta && meta.description) {
                            loraDescHtml += `<div class="lora-desc-line" style="color:var(--text-dim);font-size:10px;margin-top:1px">${escapeHtmlAttr(meta.description.slice(0, 80))}</div>`;
                        }
                        if (meta && meta.trigger_words && meta.trigger_words.length > 0) {
                            loraDescHtml += `<div class="lora-trigger-line" style="color:var(--accent);font-size:10px">触发词: ${escapeHtmlAttr(meta.trigger_words.join(', '))}</div>`;
                        }
                    });
                    loraTitle = descParts.join('\n');
                }
                loraCell = `<span title="${escapeHtmlAttr(loraTitle)}">${loraStr}</span>${loraDescHtml}`;
            }

            const hasReplay = !!(info.prompt || info.gen_method || info.width || info.seed);

            const promptText = info.prompt ? escapeHtmlAttr(info.prompt.slice(0, 120)) : '';
            const resolution = (info.width && info.height) ? `${info.width}×${info.height}` : '';
            const duration = info.duration || '';
            const fps = info.fps ? `${info.fps}fps` : '';
            const method = info.gen_method || '';
            const seed = info.seed ? `Seed:${info.seed}` : '';
            const steps = info.steps ? `${info.steps}步` : '';
            const cfg = info.cfg ? `CFG${info.cfg}` : '';
            const lora = (info.loras && info.loras.length > 0) ? loraCell : '';
            const motion = info.camera_motion || '';
            const elapsed = info.elapsed || '';
            const aspect = info.aspect_ratio || '';
            const codec = info.codec || '';
            const bitrate = info.bitrate || '';

            const dateTime = formatDateTime(item.mtime);

            const upscaleScaleText = info.upscale_actual_scale
                ? (info.upscale_actual_scale + 'x')
                : (info.upscale_scale ? info.upscale_scale + 'x' : '—');
            const upscaleModeText = info.upscale_mode
                ? (info.upscale_mode === '原始比例' ? '原始比例' : info.upscale_mode === '目标分辨率' ? '目标分辨率' : '按倍数')
                : (info.upscale_keep_ratio ? '原始比例' : info.upscale_output_size ? '目标分辨率' : '按倍数');

            const _fpsDisplay = info.upscale_original_fps
                ? (info.upscale_output_fps && info.upscale_output_fps !== info.upscale_original_fps
                    ? `${info.upscale_original_fps}→${info.upscale_output_fps}fps`
                    : `${info.upscale_original_fps}fps`)
                : (fps || '—');
            const _durDisplay = info.upscale_duration
                ? (info.upscale_duration >= 60
                    ? `${Math.floor(info.upscale_duration/60)}m${Math.round(info.upscale_duration%60)}s`
                    : `${info.upscale_duration}s`)
                : (duration || '—');

            const upscaleLine = isUpscale ? `
                <div class="list-param-line">
                    <span class="lpl-key">模式</span><span class="lpl-val">${upscaleModeText}</span>
                    <span class="lpl-key">放大</span><span class="lpl-val">${upscaleScaleText}</span>
                    <span class="lpl-key">模型</span><span class="lpl-val">${info.upscale_model || '—'}</span>
                    <span class="lpl-key">原始</span><span class="lpl-val">${info.upscale_original_size || '—'}</span>
                    <span class="lpl-key">输出</span><span class="lpl-val">${info.upscale_output_size || resolution || '—'}</span>
                    <span class="lpl-key">帧率</span><span class="lpl-val">${_fpsDisplay}</span>
                    <span class="lpl-key">时长</span><span class="lpl-val">${_durDisplay}</span>
                    <span class="lpl-key">帧数</span><span class="lpl-val">${info.upscale_frames || '—'}</span>
                    <span class="lpl-key">缩放</span><span class="lpl-val">${info.upscale_resize_mode || '—'}</span>
                    <span class="lpl-key">日期</span><span class="lpl-val">${dateTime || '—'}</span>
                </div>
                <div class="list-param-line">
                    ${info.upscale_elapsed ? `<span class="lpl-key">耗时</span><span class="lpl-val">${info.upscale_elapsed}s</span>` : `<span class="lpl-key">耗时</span><span class="lpl-val">${elapsed || '—'}</span>`}
                    <span class="lpl-key">大小</span><span class="lpl-val">${formatFileSize(item.size)}</span>
                    <span class="lpl-key">文件名</span><span class="lpl-val lpl-filename">${escapeHtmlAttr(item.filename)}</span>
                </div>` : '';

            const paramHtml = `
                ${promptText ? `<div class="list-param-line lpl-prompt-line" onclick="this.classList.toggle('lpl-expanded')"><span class="lpl-key">提示词</span><span class="lpl-val lpl-prompt">${promptText}</span></div>` : ''}
                <div class="list-param-line">
                    <span class="lpl-key">方式</span><span class="lpl-val lpl-method">${method || '—'}</span>
                    <span class="lpl-key">分辨率</span><span class="lpl-val">${resolution || '—'}</span>
                    ${!isUpscale ? `<span class="lpl-key">帧率</span><span class="lpl-val">${fps || '—'}</span>` : ''}
                    ${!isUpscale ? `<span class="lpl-key">时长</span><span class="lpl-val">${duration || '—'}</span>` : ''}
                    ${!isUpscale ? `<span class="lpl-key">种子</span><span class="lpl-val">${seed || '—'}</span>` : ''}
                    <span class="lpl-key">日期</span><span class="lpl-val">${dateTime || '—'}</span>
                </div>
                ${isUpscale ? upscaleLine : `<div class="list-param-line">
                    <span class="lpl-key">步数</span><span class="lpl-val">${steps || '—'}</span>
                    <span class="lpl-key">CFG</span><span class="lpl-val">${cfg || '—'}</span>
                    <span class="lpl-key">运镜</span><span class="lpl-val">${motion || '—'}</span>
                    <span class="lpl-key">LoRA</span><span class="lpl-val lpl-lora">${lora || '—'}</span>
                    <span class="lpl-key">耗时</span><span class="lpl-val">${elapsed || '—'}</span>
                    <span class="lpl-key">大小</span><span class="lpl-val">${formatFileSize(item.size)}</span>
                    <span class="lpl-key">文件名</span><span class="lpl-val lpl-filename">${escapeHtmlAttr(item.filename)}</span>
                </div>`}`;

            return `<div class="history-card-list" draggable="true" data-history-key="${key}" data-filename="${htmlFilename}" data-fullpath="${htmlFullpath}" data-type="${item.type}" data-replayid="${replayId}" data-globaldir="${htmlGlobalDir}" data-mtime="${escapeHtmlAttr(String(item.mtime || ''))}" data-size="${escapeHtmlAttr(String(item.size || ''))}">
                        <div class="list-thumb">
                            <div class="history-type-badge">${typeBadge}</div>
                            <button class="history-delete-btn" onclick="event.stopPropagation(); deleteHistoryItem('${safeFilename}', '${item.type}', this)">✕</button>
                            <button class="history-save-btn" onclick="event.stopPropagation(); downloadHistoryItem('${safeFilename}', '${safeGlobalDir}')">⬇</button>
                            ${media}
                        </div>
                        <div class="list-info">
                            ${paramHtml}
                        </div>
                    </div>`;
        }

        const media = item.type === 'audio'
            ? `<div class="history-audio-thumb"><div class="history-audio-icon">♪</div><div>${escapeHtmlAttr(item.filename)}</div></div>`
            : `<img data-src="${item.type === 'video' ? thumbUrl : url}" class="lazy-load history-thumb-media" alt="" style="object-fit: contain; max-width: 100%; max-height: 100%;">`;

        const gridTags = [];
        if (info.gen_method) gridTags.push(`<span class="ptag ptag-method">${info.gen_method}</span>`);
        if (isUpscale) {
            if (upscaleModeText) gridTags.push(`<span class="ptag ptag-method">${upscaleModeText}</span>`);
            if (upscaleScaleText && upscaleScaleText !== '—') gridTags.push(`<span class="ptag">${upscaleScaleText}</span>`);
            if (info.upscale_original_size && info.upscale_output_size) gridTags.push(`<span class="ptag">${info.upscale_original_size}→${info.upscale_output_size}</span>`);
            if (info.upscale_model) gridTags.push(`<span class="ptag">${info.upscale_model}</span>`);
            if (info.upscale_original_fps) {
                gridTags.push(info.upscale_output_fps && info.upscale_output_fps !== info.upscale_original_fps
                    ? `<span class="ptag">${info.upscale_original_fps}→${info.upscale_output_fps}fps</span>`
                    : `<span class="ptag">${info.upscale_original_fps}fps</span>`);
            }
            if (info.upscale_duration) {
                gridTags.push(info.upscale_duration >= 60
                    ? `<span class="ptag">${Math.floor(info.upscale_duration/60)}m${Math.round(info.upscale_duration%60)}s</span>`
                    : `<span class="ptag">${info.upscale_duration}s</span>`);
            }
            if (info.upscale_frames) gridTags.push(`<span class="ptag">${info.upscale_frames}帧</span>`);
            if (info.upscale_elapsed) gridTags.push(`<span class="ptag">${info.upscale_elapsed}s</span>`);
        } else {
            if (info.width && info.height) gridTags.push(`<span class="ptag">${info.width}×${info.height}</span>`);
            if (info.fps) gridTags.push(`<span class="ptag">${info.fps}fps</span>`);
            if (info.duration) gridTags.push(`<span class="ptag">${info.duration}</span>`);
            if (info.seed) gridTags.push(`<span class="ptag">Seed:${info.seed}</span>`);
        }
        if (info.loras && info.loras.length > 0) {
            const loraStr = info.loras.map(l => escapeHtmlAttr(l)).join(', ');
            gridTags.push(`<span class="ptag ptag-lora">${loraStr}</span>`);
        }
        if (info.elapsed) gridTags.push(`<span class="ptag ptag-elapsed">⏱${info.elapsed}</span>`);
        const gridDateTime = formatDateTime(item.mtime);
        if (gridDateTime && gridDateTime !== '-') gridTags.push(`<span class="ptag ptag-date">📅${gridDateTime}</span>`);

        const gridInfoHtml = `<div class="grid-info-bar">
                ${info.prompt ? `<div class="grid-info-prompt">${escapeHtmlAttr(info.prompt.slice(0, 120))}</div>` : ''}
                <div class="param-row">${gridTags.join('')}</div>
           </div>`;

        return `<div class="history-card" draggable="true" data-history-key="${key}" data-filename="${htmlFilename}" data-fullpath="${htmlFullpath}" data-type="${item.type}" data-replayid="${replayId}" data-globaldir="${htmlGlobalDir}">
                    <div class="grid-thumb-wrap">
                        <div class="history-type-badge">${typeBadge}</div>
                        <button class="history-delete-btn" onclick="event.stopPropagation(); deleteHistoryItem('${safeFilename}', '${item.type}', this)">✕</button>
                        <button class="history-save-btn" onclick="event.stopPropagation(); downloadHistoryItem('${safeFilename}', '${safeGlobalDir}')">⬇</button>
                        ${media}
                    </div>
                    ${gridInfoHtml}
                </div>`;
    }

    // ★ 2026-06-17: "我的作品" → 输入框拖拽支持
    //   原理: history 卡片(网格 + 列表两种)dragstart 时把 {fullpath, type, filename} 写入 dataTransfer
    //         各个 upload-zone 上注册 dragover/drop 监听,按 type 匹配后把 server 路径直接塞到 hidden input
    //         关键: history 文件本来就在 server 端,直接复用路径,无需重新上传
    const YUNJI_DRAG_MIME = 'application/x-yunji-history-item';

    function _getHistoryItemFromDataTransfer(dt) {
        // 1) 自定义 MIME(优先)
        const raw = dt.getData(YUNJI_DRAG_MIME) || dt.getData('text/plain') || '';
        if (!raw) return null;
        try {
            const obj = JSON.parse(raw);
            if (obj && obj.fullpath && obj.type) return obj;
        } catch (_) {}
        return null;
    }

    function _buildFileUrl(fullpath) {
        return `${BASE}/api/system/file?path=${encodeURIComponent(fullpath)}`;
    }

    // 拖入时把历史文件作为"伪 File"塞进 file input,触发已有的 onchange 逻辑
    //   - 这样可以复用 handleFrameUpload / handleUpscaleVideoUpload 等函数
    //   - DataTransfer.items 在 Chromium 中受限于 onchange 不能直接构造 file,需要走一个 trick
    //   - 这里采用更稳的做法: 直接调 handleXxxUpload 风格的"已上传"路径
    function _applyHistoryToDropZone(zoneCfg, item) {
        const fileUrl = _buildFileUrl(item.fullpath);
        const filename = item.filename || (item.fullpath.split(/[\\/]/).pop() || 'item');

        // 1) 隐藏的 path input
        if (zoneCfg.pathInputId) {
            const inp = document.getElementById(zoneCfg.pathInputId);
            if (inp) inp.value = item.fullpath;
        }
        // 2) 预览元素
        if (zoneCfg.previewId) {
            const preview = document.getElementById(zoneCfg.previewId);
            if (preview) {
                if (preview.tagName === 'VIDEO') {
                    preview.src = fileUrl;
                    preview.style.display = 'block';
                    preview.load();
                } else {
                    preview.src = fileUrl;
                    preview.style.display = 'block';
                }
            }
        }
        // 3) 隐藏 placeholder,显示 clear 按钮,显示文件名
        if (zoneCfg.placeholderId) {
            const p = document.getElementById(zoneCfg.placeholderId);
            if (p) p.style.display = 'none';
        }
        if (zoneCfg.clearOverlayId) {
            const c = document.getElementById(zoneCfg.clearOverlayId);
            if (c) c.style.display = 'flex';
        }
        if (zoneCfg.nameElId) {
            const n = document.getElementById(zoneCfg.nameElId);
            if (n) { n.textContent = filename; n.style.display = 'block'; }
        }
        if (zoneCfg.statusElId) {
            const s = document.getElementById(zoneCfg.statusElId);
            if (s) s.style.display = 'block';
        }
        if (zoneCfg.statusFilenameElId) {
            const f = document.getElementById(zoneCfg.statusFilenameElId);
            if (f) f.textContent = '✅ ' + filename;
        }
        // 4) 特殊:TTS 参考音频(用 _ttsRefB64 而非 hidden input)
        if (zoneCfg.onApply) {
            try { zoneCfg.onApply(item, filename, fileUrl); } catch (e) { console.warn('drop onApply fail:', e); }
        }
        // 5) 分辨率预览(帧上传场景需要刷新)
        try { if (typeof updateResPreview === 'function') updateResPreview(); } catch (_) {}
        // 6) 持久化
        try { if (typeof scheduleSave === 'function') scheduleSave(); } catch (_) {}
        addLog(`✅ 已应用: ${filename} → ${zoneCfg.label || '目标'}`);
    }

    // 所有支持拖拽的 drop zone 配置
    //   accepts: ['image'] / ['video'] / ['audio'] / ['image','video']
    //   isImage: 决定 <video> 还是 <img> 用于预览(已通过 previewId 自动识别 tagName)
    const HISTORY_DROP_ZONES = [
        // 高清放大
        {
            zoneId: 'upscale-video-drop-zone', accepts: ['video'],
            pathInputId: 'upscale-video-path', previewId: 'upscale-video-preview',
            placeholderId: 'upscale-video-placeholder', clearOverlayId: 'clear-upscale-video-overlay',
            nameElId: 'upscale-video-name', label: '输入视频(高清放大)',
        },
        {
            zoneId: 'upscale-image-drop-zone', accepts: ['image'],
            pathInputId: 'upscale-image-path', previewId: 'upscale-image-preview',
            placeholderId: 'upscale-image-placeholder', clearOverlayId: 'clear-upscale-image-overlay',
            label: '输入图片(高清放大)',
        },
        // 视频生成 - 首/尾帧
        {
            zoneId: 'start-frame-drop-zone', accepts: ['image'],
            pathInputId: 'start-frame-path', previewId: 'start-frame-preview',
            placeholderId: 'start-frame-placeholder', clearOverlayId: 'clear-start-frame-overlay',
            label: '起始帧',
            onApply: (item) => {
                if (typeof uploadedFrameDims === 'object') {
                    // 异步取真实尺寸(图片加载完成后在 _onFrameImageLoaded 风格函数里取)
                    const img = new Image();
                    img.onload = () => {
                        uploadedFrameDims.start = { w: img.naturalWidth, h: img.naturalHeight };
                        try { updateResPreview(); } catch (_) {}
                    };
                    img.src = _buildFileUrl(item.fullpath);
                }
            },
        },
        {
            zoneId: 'end-frame-drop-zone', accepts: ['image'],
            pathInputId: 'end-frame-path', previewId: 'end-frame-preview',
            placeholderId: 'end-frame-placeholder', clearOverlayId: 'clear-end-frame-overlay',
            label: '结束帧',
            onApply: (item) => {
                if (typeof uploadedFrameDims === 'object') {
                    const img = new Image();
                    img.onload = () => {
                        uploadedFrameDims.end = { w: img.naturalWidth, h: img.naturalHeight };
                        try { updateResPreview(); } catch (_) {}
                    };
                    img.src = _buildFileUrl(item.fullpath);
                }
            },
        },
        // 视频生成 - 音频
        {
            zoneId: 'audio-drop-zone', accepts: ['audio'],
            pathInputId: 'uploaded-audio-path',
            statusElId: 'audio-upload-status', statusFilenameElId: 'audio-filename-status',
            placeholderId: 'audio-upload-placeholder', label: '音频(视频生成)',
        },
        // 动作迁移
        {
            zoneId: 'motion-video-drop-zone', accepts: ['video'],
            pathInputId: 'motion-video-path', previewId: 'motion-video-preview',
            placeholderId: 'motion-video-placeholder', clearOverlayId: 'clear-motion-video-overlay',
            label: '输入视频(动作迁移)',
        },
        {
            zoneId: 'motion-image-drop-zone', accepts: ['image'],
            pathInputId: 'motion-image-path', previewId: 'motion-image-preview',
            placeholderId: 'motion-image-placeholder', clearOverlayId: 'clear-motion-image-overlay',
            label: '目标主体图(动作迁移)',
        },
        // TTS 参考音频
        {
            zoneId: 'tts-ref-drop', accepts: ['audio'],
            label: 'TTS 参考音频',
            onApply: (item, filename) => {
                // TTS 流程用的是 _ttsRefB64(base64),需要 fetch 一下转 base64
                fetch(_buildFileUrl(item.fullpath))
                    .then(r => r.blob())
                    .then(blob => {
                        const reader = new FileReader();
                        reader.onload = () => {
                            _ttsRefB64 = reader.result;
                            const placeholder = document.getElementById('tts-ref-placeholder');
                            const statusEl = document.getElementById('tts-ref-status');
                            const clearBtn = document.getElementById('tts-ref-clear');
                            if (placeholder) placeholder.style.display = 'none';
                            if (statusEl) { statusEl.style.display = 'block'; statusEl.textContent = '✅ ' + filename; }
                            if (clearBtn) clearBtn.style.display = 'flex';
                            if (typeof _updateTtsSubModeUI === 'function') _updateTtsSubModeUI();
                        };
                        reader.readAsDataURL(blob);
                    })
                    .catch(err => addLog('❌ 读取音频失败: ' + err.message));
            },
        },
    ];

    function _setupHistoryDropZone(zoneCfg) {
        const zone = document.getElementById(zoneCfg.zoneId);
        if (!zone) return;
        // dragover: preventDefault + 视觉反馈
        zone.addEventListener('dragover', (e) => {
            // 必须 preventDefault 才能触发 drop
            if (e.dataTransfer && Array.from(e.dataTransfer.types || []).some(t => t === YUNJI_DRAG_MIME || t === 'text/plain')) {
                e.preventDefault();
                e.stopPropagation();
                e.dataTransfer.dropEffect = 'copy';
                zone.classList.add('dragover');
            }
        });
        // dragleave: 清除视觉
        zone.addEventListener('dragleave', (e) => {
            // 只在离开整个 zone 时清除(子元素冒泡会触发)
            if (e.target === zone) zone.classList.remove('dragover');
        });
        // drop: 处理
        zone.addEventListener('drop', (e) => {
            e.preventDefault();
            e.stopPropagation();
            zone.classList.remove('dragover');
            const item = _getHistoryItemFromDataTransfer(e.dataTransfer);
            // ★ 2026-06-17 debug: 临时调试日志
            if (window.__yunjiDragDebug) {
                console.log('[dragdrop] drop on', zoneCfg.zoneId, 'item:', item, 'types:', e.dataTransfer && Array.from(e.dataTransfer.types || []));
            }
            if (!item) {
                // 不是从历史拖来的,什么都不做(让浏览器走文件上传)
                return;
            }
            if (!zoneCfg.accepts.includes(item.type)) {
                addLog(`❌ 类型不匹配:此目标只接受 ${zoneCfg.accepts.join('/')},但拖入的是 ${item.type}`);
                return;
            }
            _applyHistoryToDropZone(zoneCfg, item);
        });
    }

    function setupHistoryDragDrop() {
        // 1) 在 document 层级拦截 dragstart(事件委托,只针对历史卡片)
        document.addEventListener('dragstart', (e) => {
            const card = e.target.closest('.history-card, .history-card-list');
            if (!card) return;
            const item = {
                fullpath: card.dataset.fullpath || '',
                type: card.dataset.type || '',
                filename: card.dataset.filename || '',
            };
            if (!item.fullpath || !item.type) {
                e.preventDefault();
                return;
            }
            const payload = JSON.stringify(item);
            try { e.dataTransfer.setData(YUNJI_DRAG_MIME, payload); } catch (_) {}
            try { e.dataTransfer.setData('text/plain', payload); } catch (_) {}
            // ★ 2026-06-17 fix: 同时写 application/x-yunji-asset(已有 initDragAndDrop 系统的 MIME)
            //   - payload 字段保持兼容:{filename, type}
            //   - _fetchAssetAsFile 用 filename 拼 URL,但实际接受 path 参数,所以传 fullpath 也能解析
            //   - 这样 initDragAndDrop 的 drop handler 也能正确处理,双保险
            try { e.dataTransfer.setData('application/x-yunji-asset', JSON.stringify({ filename: item.fullpath, type: item.type })); } catch (_) {}
            e.dataTransfer.effectAllowed = 'copy';
            // 视觉:被拖的卡片加个半透明
            card.classList.add('is-dragging');
        });
        document.addEventListener('dragend', (e) => {
            const card = e.target.closest && e.target.closest('.history-card, .history-card-list');
            if (card) card.classList.remove('is-dragging');
        });
        // 2) 给所有 drop zone 装监听(只装一次,重复调用安全)
        if (!window.__yunjiDropZonesInstalled) {
            HISTORY_DROP_ZONES.forEach(_setupHistoryDropZone);
            window.__yunjiDropZonesInstalled = true;
        }
    }
    window.setupHistoryDragDrop = setupHistoryDragDrop;

    function resetHistoryAndReload() {
        _historyListFingerprint = '';
        _historyHasRendered = false;
        _historyRenderedKeys.clear();
        currentHistoryPage = 1;
        _historyHasMore = true;
        _historyTotalPages = 0;  // ★ 重置 total_pages,让哨兵在拿到新数据前不误判
        historyThumbLoaderToken++;
        // ★ 2026-06-16 v2: 切回第一页后,移除旧哨兵 + 重置观察者
        _destroyHistorySentinel();
        const container = document.getElementById('history-container');
        if (container) container.innerHTML = '';
        isLoadingHistory = false;
        return fetchHistory(true);
    }
    // ★ 2026-06-16 v2: 暴露到 window,供 HTML 内联 onclick="resetHistoryAndReload()" 使用
    window.resetHistoryAndReload = resetHistoryAndReload;

    // ★ 2026-06-16 v3 修复: 创建/获取底部"哨兵"元素,IntersectionObserver 触底自动加载下一页
    //   关键: 哨兵必须放在 #history-wrapper(滚动容器)内部,IO 的 root 也必须是这个 wrapper
    //   否则哨兵在文档流里直接可见,IO 立刻触发 → 一次加载全部 → 无限转圈
    function _ensureHistorySentinel() {
        const wrapper = document.getElementById('history-wrapper');
        if (!wrapper) return null;
        // ★ 哨兵要插到 wrapper 末尾(不是 wrapper 之后),跟随 wrapper 一起滚动
        let sentinel = document.getElementById('history-sentinel');
        if (sentinel && sentinel.parentNode === wrapper) {
            return sentinel;
        }
        // 清理可能存在的旧节点(防御性)
        if (sentinel && sentinel.parentNode) {
            sentinel.parentNode.removeChild(sentinel);
        }
        sentinel = document.createElement('div');
        sentinel.id = 'history-sentinel';
        sentinel.className = 'history-sentinel';
        sentinel.innerHTML = '<span class="hs-spinner"></span><span class="hs-text" data-i18n="loadingMore">加载中...</span>';
        wrapper.appendChild(sentinel);
        _historySentinel = sentinel;
        return sentinel;
    }

    function _destroyHistorySentinel() {
        if (_historySentinelObserver) {
            try { _historySentinelObserver.disconnect(); } catch (_) {}
            _historySentinelObserver = null;
        }
        if (_historySentinel && _historySentinel.parentNode) {
            _historySentinel.parentNode.removeChild(_historySentinel);
        }
        _historySentinel = null;
    }

    // 更新哨兵文字
    function _setSentinelState(state) {
        const sentinel = _historySentinel;
        if (!sentinel) return;
        const textEl = sentinel.querySelector('.hs-text');
        const t = (window.t || ((k) => k));  // i18n.js 暴露的全局 t()
        if (state === 'loading') {
            sentinel.classList.remove('hs-done', 'hs-end');
            sentinel.classList.add('hs-loading');
            if (textEl) textEl.textContent = t('loadingMore');
        } else if (state === 'end') {
            sentinel.classList.remove('hs-loading');
            sentinel.classList.add('hs-end');
            if (textEl) textEl.textContent = t('noMore');
        } else if (state === 'done') {
            sentinel.classList.remove('hs-loading', 'hs-end');
            if (textEl) textEl.textContent = '';
        }
    }

    // 启动哨兵观察: 哨兵进入 #history-wrapper 视口 → 加载下一页 5 个
    //   ★ 关键修复 v3:
    //   - root 用 history-wrapper(滚动容器),不是 window,否则哨兵不跟随滚动
    //   - rootMargin 400px: 哨兵离 wrapper 底部还有 400px 就触发,用户感觉不到延迟
    //   - 加载期间 disconnect,加载完重新 observe,防止连续触发
    function _startHistorySentinelObserver() {
        if (!('IntersectionObserver' in window)) return;
        if (_historySentinelObserver) return;
        const wrapper = document.getElementById('history-wrapper');
        const sentinel = _ensureHistorySentinel();
        if (!wrapper || !sentinel) return;
        const io = new IntersectionObserver((entries) => {
            for (const entry of entries) {
                if (!entry.isIntersecting) continue;
                if (isLoadingHistory || !_historyHasMore) continue;
                // ★ v3 修复: 防御性检查,万一后端 total_pages 字段缺失但已经超出范围
                //   也不要去发无意义的请求
                if (_historyTotalPages > 0 && currentHistoryPage >= _historyTotalPages) {
                    _historyHasMore = false;
                    _setSentinelState('end');
                    continue;
                }
                // ★ 立刻 disconnect,加载完成后再重新 observe(防止同一次进入视口触发多次)
                io.unobserve(entry.target);
                currentHistoryPage += 1;
                fetchHistory(false, true).finally(() => {
                    // 加载完后再 observe 哨兵(没更多页了就不 observe)
                    if (_historyHasMore && _historySentinel) {
                        try { io.observe(_historySentinel); } catch (_) {}
                    }
                });
            }
        }, { root: wrapper, rootMargin: '0px 0px 400px 0px', threshold: 0 });
        _historySentinelObserver = io;
        try { io.observe(sentinel); } catch (_) {}
    }

    async function fetchHistory(isFirstLoad = false, silent = false) {
        if (isLoadingHistory) return;
        isLoadingHistory = true;
        // ★ 加载期间显示"加载中"状态(只对追加页生效,首屏由浏览器渲染空容器)
        if (!isFirstLoad) _setSentinelState('loading');

        try {
            // ★ 2026-06-16 v2: 支持分页,首次 5 个,之后每次 +5
            //   silent 模式: 不清空 _historyListFingerprint 状态,允许"无变化则不重渲"
            const limit = isFirstLoad ? HISTORY_PAGE_LIMIT : HISTORY_PAGE_MORE;

            // ★ 2026-06-17: 优先用 HTML 解析阶段就发起的预取 promise
            //   - 场景: 用户首次打开页面 / F5 刷新
            //   - 浏览器在 HTML 解析时就已经开始下载 /api/system/history
            //   - 等 JS 跑完时,大概率已经 ready(等 50ms 兜底,避免预取太慢反而拖累)
            //   - F5 时(ETag 命中 304),0 字节响应,真正秒开
            if (isFirstLoad && !silent && window.__yunjiHistoryPrefetch) {
                const prefetched = await Promise.race([
                    window.__yunjiHistoryPrefetch,
                    new Promise(resolve => setTimeout(() => resolve(null), 50)),
                ]);
                if (prefetched) {
                    window.__yunjiHistoryPrefetch = null;
                    // 走 localStorage 缓存路径(后续 fetch 仍然会发,走 304 透明命中)
                    isLoadingHistory = false;
                    await _renderHistoryData(prefetched, true, false, limit);
                    isLoadingHistory = true;
                }
            }

            // ★ 2026-06-16 v4 提速: 首次加载时,先尝试从 localStorage 读缓存(0 阻塞渲染)
            //   - 如果命中 → 立刻渲染 → 用户先看到 5 个作品 → 再后台 fetch 最新数据
            //   - 视觉:F5 后 200-500ms 就能看到内容(本来要 1-2 秒)
            //   - 后台 fetch 回来后用 silent 路径,内容不变就啥都不做,变了就 diff
            if (isFirstLoad && !silent) {
                const cached = _loadHistoryFromLocal(currentHistoryPage, limit);
                if (cached) {
                    // ★ 标记当前正在"显示缓存",避免 200ms 后网络回来走 silent 时
                    //   把已经渲染好的卡片当成"未渲染",触发额外的 needsFullRender
                    isLoadingHistory = false;
                    await _renderHistoryData(cached, true, false, limit);
                    isLoadingHistory = true;
                }
            }
            // ★ 2026-06-16 v4 提速: cache:'default' 让浏览器自动处理 304
            //   - 后端带 ETag/Last-Modified,同参数二次请求会自动加 If-None-Match/If-Modified-Since
            //   - 后端返回 304 → 浏览器自动用本地缓存(0 字节响应)
            //   - 用户感受: F5 后 1-2 秒的网络请求变成 < 50ms
            const res = await fetch(`${BASE}/api/system/history?page=${currentHistoryPage}&limit=${limit}`, {
                cache: 'default',
                headers: { 'Accept': 'application/json' },
            });
            if (!res.ok) {
                // ★ 304 已被浏览器透明处理(res.ok=true 且 status=304 时浏览器自动解出来)
                //   这里只处理真正的 4xx/5xx
                isLoadingHistory = false;
                return;
            }
            // ★ 2026-06-16 v4: status 304 时 res.json() 会抛 — 浏览器其实早已自动从
            //   HTTP 缓存里取回上次的 body,fetch API 在多数浏览器里会把 304 重写为 200
            //   + 上次的 body。但为了兜底(老浏览器 / Service Worker 拦截),
            //   如果真的撞到 304,直接用 localStorage 缓存补上。
            if (res.status === 304) {
                const cached = _loadHistoryFromLocal(currentHistoryPage, limit);
                if (cached) {
                    await _renderHistoryData(cached, isFirstLoad, silent, limit);
                }
                return;
            }
            const data = await res.json();
            // ★ 2026-06-16 v4: 拉到新数据后写 localStorage,F5 才能秒开
            _saveHistoryToLocal(currentHistoryPage, limit, data);
            // ★ 2026-06-16 v4 修复: 如果已经从 localStorage 渲染过了,网络回来的
            //   数据要走 silent 模式,避免"刚渲染完又被清空重渲"
            const useSilent = isFirstLoad && _historyListFingerprint && _historyHasRendered;
            await _renderHistoryData(data, isFirstLoad, useSilent || silent, limit);
        } catch(e) {
            console.error("Failed to load history", e);
        } finally {
            isLoadingHistory = false;
        }
    }

    // ★ 2026-06-16 v4 提速: 把 fetchHistory 里的"拿到数据 → 渲染"逻辑抽出来
    //   1) 让 fetchHistory 主体只管"发请求 + 304 兜底",逻辑更清晰
    //   2) 让 304 路径(命中 localStorage)能直接复用渲染逻辑
    async function _renderHistoryData(data, isFirstLoad, silent, limit) {
        // ★ 2026-06-16 v3 修复: 只在首次加载/重置时清掉 cache buster 和预下载缓存
        //   旧逻辑: 每次 fetchHistory 都调 → 翻页时把第1页正在下载的缩略图全部作废,
        //          → 浏览器取消旧请求 + 拿新 buster 重新请求 → 缩略图永远加载不完
        //   新逻辑: 只有"用户主动刷新"才清,翻页追加不动现有缩略图
        // ★ 2026-06-16 v4 修复: 二次渲染(从 localStorage 渲染过了,网络又回来)
        //   不能再清 bust,否则刚显示的缓存卡片缩略图 URL 全部失效 → 视觉上"清空重渲"
        if (isFirstLoad && !silent) {
            if (typeof window.__invalidateAllFileBusters === 'function') {
                window.__invalidateAllFileBusters();
            }
            if (typeof window.__resetVideoPrefetchCache === 'function') {
                window.__resetVideoPrefetchCache();
            }
        }

            const validHistory = (data.history || []).filter(item => {
                if (!item || !item.filename) return false;
                const name = String(item.filename);
                if (name.startsWith('_') || name.toLowerCase().startsWith('tmp')) return false;
                const size = Number(item.size || 0);
                if (item.type === 'video' && size > 0 && size < 4096) return false;
                return true;
            });
            const fingerprint = validHistory.length === 0
                ? '__empty__'
                : validHistory.map(h => `${h.type}|${h.filename}|${h.size || 0}|${h.mtime || 0}`).join('\0');

            if (silent && fingerprint === _historyListFingerprint) {
                return;
            }

            const container = document.getElementById('history-container');
            if (!container) {
                return;
            }
            container.classList.toggle('layout-list', _historyLayout === 'list');

            let loadingCardHtml = "";
            const lc = document.getElementById('current-loading-card');
            if (lc && _isGeneratingFlag) {
                loadingCardHtml = lc.outerHTML;
            }

            // ★ 2026-06-16 v3 修复: 空响应不再清空容器
            //   旧逻辑: validHistory 为空就 container.innerHTML = loadingCardHtml
            //   翻页触底时 API 返回 [] → 容器被清空 → 用户看到"已加载的作品全没了"
            //   新逻辑: 只有"用户主动刷新"且确实没有数据时才清空
            if (validHistory.length === 0) {
                if (isFirstLoad || !_historyHasRendered) {
                    // 真正空目录(没有作品)才清空,显示 loading card
                    container.innerHTML = loadingCardHtml;
                    const newLcEmpty = document.getElementById('current-loading-card');
                    if (newLcEmpty) newLcEmpty.onclick = showGeneratingView;
                }
                // 翻页触底:什么都不做,保留已有卡片
                _historyListFingerprint = fingerprint;
                return;
            }

            const outInput = document.getElementById('global-out-dir');
            const globalDir = outInput ? outInput.value.replace(/\\/g, '/').replace(/\/$/, '') : "";

            const needsFullRender = !_historyHasRendered || container.querySelectorAll('.history-card[data-history-key], .history-card-list[data-history-key]').length === 0;
            // ★ 2026-06-16 v2: 分页追加模式 = (非首次) + (已有渲染过的卡片)
            //   这种情况直接 append 新卡片到底部,不重做 diff/merge(分页天然没有重复)
            const isAppending = !isFirstLoad && _historyHasRendered;
            if (isAppending) {
                // 过滤掉"已经渲染过"的卡(防御性,理论上分页不会重复)
                const newOnly = validHistory.filter((item) => !_historyRenderedKeys.has(historyItemKey(item)));
                if (newOnly.length > 0) {
                    const cardsHtml = newOnly.map((item) => renderHistoryCardHtml(item, globalDir)).join('');
                    container.insertAdjacentHTML('beforeend', cardsHtml);
                    newOnly.forEach((item) => _historyRenderedKeys.add(historyItemKey(item)));
                }
            } else if (needsFullRender) {
                const cardsHtml = validHistory.map((item) => renderHistoryCardHtml(item, globalDir)).join('');
                container.insertAdjacentHTML('beforeend', cardsHtml);
                _historyRenderedKeys.clear();
                validHistory.forEach((item) => _historyRenderedKeys.add(historyItemKey(item)));
                _historyHasRendered = true;
            } else {
                const existingCards = new Map(
                    Array.from(container.querySelectorAll('.history-card[data-history-key], .history-card-list[data-history-key]')).map((card) => [card.dataset.historyKey, card])
                );
                // ★ 2026-06-09 修复: 把"已渲染但 _historyRenderedKeys 漏掉"的卡补登记,
                //   并保存每张卡的 mtime/size,后续判断文件是否被覆盖重生成
                const existingMeta = new Map();
                existingCards.forEach((card, key) => {
                    if (!_historyRenderedKeys.has(key)) _historyRenderedKeys.add(key);
                    existingMeta.set(key, {
                        mtime: card.dataset.mtime || '',
                        size: card.dataset.size || '',
                    });
                });
                const freshKeys = new Set(validHistory.map((item) => historyItemKey(item)));
                existingCards.forEach((card, key) => {
                    if (!freshKeys.has(key)) {
                        card.remove();
                        _historyRenderedKeys.delete(key);
                        existingMeta.delete(key);
                    }
                });

                // ★ 找出"同 key 但 mtime/size 变了"的项 —— 文件被覆盖重生成
                //   旧卡上展示的是旧文件缩略图,不重新渲染就会"图不对版"错乱
                const staleUpdatable = [];
                const newItems = [];
                validHistory.forEach((item) => {
                    const key = historyItemKey(item);
                    if (!_historyRenderedKeys.has(key)) {
                        newItems.push(item);
                        return;
                    }
                    const old = existingMeta.get(key);
                    if (old && (old.mtime !== String(item.mtime || '') || old.size !== String(item.size || ''))) {
                        staleUpdatable.push(item);
                    }
                });

                if (newItems.length > 0) {
                    const cardsHtml = newItems.map((item) => renderHistoryCardHtml(item, globalDir)).join('');
                    const loadingCard = document.getElementById('current-loading-card');
                    if (loadingCard) {
                        loadingCard.insertAdjacentHTML('afterend', cardsHtml);
                    } else {
                        container.insertAdjacentHTML('afterbegin', cardsHtml);
                    }
                    newItems.forEach((item) => _historyRenderedKeys.add(historyItemKey(item)));
                }

                // ★ 把"被覆盖重生成"的卡原地替换成新内容(mtime 写进 data 属性,
                //   缩略图走 lazy-load 流程重新请求,buster 已被清掉所以拿到新文件)
                staleUpdatable.forEach((item) => {
                    const key = historyItemKey(item);
                    const card = existingCards.get(key);
                    if (!card) return;
                    const tmp = document.createElement('div');
                    tmp.innerHTML = renderHistoryCardHtml(item, globalDir).trim();
                    const fresh = tmp.firstElementChild;
                    if (!fresh) return;
                    fresh.dataset.mtime = String(item.mtime || '');
                    fresh.dataset.size = String(item.size || '');
                    // 取消可能正在飞的旧缩略图,避免新旧并存
                    try {
                        const oldImg = card.querySelector('img.history-thumb-media');
                        if (oldImg) {
                            oldImg.classList.remove('history-thumb-ready', 'history-thumb-loading');
                            oldImg.classList.add('lazy-load');
                            try { oldImg.removeAttribute('src'); } catch (_) {}
                        }
                        const oldVid = card.querySelector('video.history-thumb-media');
                        if (oldVid) {
                            try { oldVid.pause(); oldVid.removeAttribute('src'); oldVid.load(); } catch (_) {}
                        }
                    } catch (_) {}
                    card.replaceWith(fresh);
                    existingCards.set(key, fresh);
                });
            }

            // ★ 2026-06-09: 把新挂载的历史卡片挂到 IntersectionObserver 上,
            //              进入视口才会触发缩略图懒加载 + 视频文件预下载
            if (typeof window.__observeHistoryCard === 'function') {
                Array.from(container.querySelectorAll('.history-card:not([data-observed]), .history-card-list:not([data-observed])'))
                    .forEach((card) => {
                        card.dataset.observed = '1';
                        window.__observeHistoryCard(card);
                    });
            }

            // 重新绑定loading card点击事件
            const newLc = document.getElementById('current-loading-card');
            if (newLc) newLc.onclick = showGeneratingView;

            if (isFirstLoad || !_historyHasRendered) {
                await loadVisibleImages();
            } else {
                requestHistoryThumbLoad();
            }
            _historyListFingerprint = fingerprint;

            // ★ 2026-06-17: 默认激活最新作品并显示大图
            //   触发条件: 首次加载 + 列表非空 + 当前没有用户主动激活的卡
            //   作用: 切回 history tab 时自动展示最新;用户点过别的就不再覆盖
            try {
                if (isFirstLoad && validHistory.length > 0) {
                    const _hasActive = container.querySelector('.history-card.is-active, .history-card-list.is-active');
                    if (!_hasActive) {
                        const _firstCard = container.querySelector('.history-card[data-history-key], .history-card-list[data-history-key]');
                        if (_firstCard) {
                            // 标记活跃样式
                            container.querySelectorAll('.history-card.is-active, .history-card-list.is-active').forEach(c => c.classList.remove('is-active'));
                            _firstCard.classList.add('is-active');
                            // 优先用 fullpath,回退到 filename
                            const _fullpath = _firstCard.dataset.fullpath || '';
                            const _filename = _firstCard.dataset.filename || '';
                            const _type = _firstCard.dataset.type || 'image';
                            const _replayId = _firstCard.dataset.replayid || '';
                            const _fileToDisplay = _fullpath || _filename;
                            if (_fileToDisplay) {
                                // ★ 2026-06-17: 预加载大图(关键路径优化)
                                //   不等 displayHistoryOutput 触发 img.src 才下载
                                //   而是先在 <head> 注入 <link rel="preload" as="image"> 提前下载
                                //   浏览器会并发下载,等 displayHistoryOutput 设 src 时直接走浏览器缓存
                                //   收益: 大图加载从"等 displayHistoryOutput" 降到"和 history API 并行"
                                try {
                                    if (_type === 'image') {
                                        _preloadAsset(_fileToDisplay, 'image');
                                    } else if (_type === 'video') {
                                        _preloadAsset(_fileToDisplay, 'video');
                                    }
                                } catch (_ppErr) { /* preload 失败不影响主流程 */ }
                                // ★ 仅显示大图,不重复刷日志(用户还没主动操作)
                                displayHistoryOutput(_fileToDisplay, _type, _replayId);
                                console.log('[AUTO-ACTIVATE] 已默认激活最新作品:', _fileToDisplay);
                            }
                        }
                    }
                }
            } catch (_autoErr) {
                console.warn('[AUTO-ACTIVATE] 失败:', _autoErr);
            }

            // ★ 2026-06-16 v2: 决定"还有没有更多页",并启动底部哨兵观察者
            //   后端响应里有 total_pages / current_page,直接对位判断
            //   容错:服务端没给字段时,按"返回的卡片数 < limit"判定为最后一页
            const totalPages = Number(data.total_pages || 0);
            const currentPage = Number(data.current_page || currentHistoryPage);
            _historyTotalPages = totalPages;  // ★ v3: 记下来,IO 用它防御
            if (totalPages > 0) {
                _historyHasMore = currentPage < totalPages;
            } else {
                _historyHasMore = validHistory.length >= limit;
            }
            // 启动底部哨兵 IO(只在有更多时才有意义,但哨兵本身一直挂载以便统一管理)
            _startHistorySentinelObserver();
            if (!_historyHasMore) {
                _setSentinelState('end');
            } else {
                _setSentinelState('done');  // 不显示文字,只占位触发 IO
            }
    }

    async function deleteHistoryItem(filename, type, btn) {
        if (!confirm(`确定要删除 "${filename}" 吗？`)) return;
        
        try {
            const res = await fetch(`${BASE}/api/system/delete-file`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({filename: filename, type: type})
            });
            
            if (res.ok) {
                // 删除成功后移除元素
                const card = btn.closest('.history-card, .history-card-list');
                if (card) {
                    if (card.dataset.historyKey) _historyRenderedKeys.delete(card.dataset.historyKey);
                    card.remove();
                }
            } else {
                alert('删除失败');
            }
        } catch(e) {
            console.error('Delete failed', e);
            alert('删除失败');
        }
    }

    function downloadHistoryItem(filename, globalDir) {
        if (!filename) return;
        let url;
        if (globalDir) {
            url = `${BASE}/api/system/file?path=${encodeURIComponent(globalDir + '/' + filename)}`;
        } else {
            url = `${BASE}/outputs/${encodeURIComponent(filename)}`;
        }
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
    }

    function waitForMediaEvent(media, eventName, timeoutMs) {
        return new Promise((resolve) => {
            let done = false;
            const finish = (ok) => {
                if (done) return;
                done = true;
                clearTimeout(timer);
                media.removeEventListener(eventName, onEvent);
                media.removeEventListener('error', onError);
                resolve(ok);
            };
            const onEvent = () => finish(true);
            const onError = () => finish(false);
            const timer = setTimeout(() => finish(false), timeoutMs);
            media.addEventListener(eventName, onEvent, { once: true });
            media.addEventListener('error', onError, { once: true });
        });
    }

    function sleep(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }

    function withCacheBust(url, key) {
        const sep = String(url).includes('?') ? '&' : '?';
        return `${url}${sep}_thumb=${encodeURIComponent(key)}`;
    }

    function waitForVideoFrame(video, timeoutMs) {
        return new Promise((resolve) => {
            let done = false;
            const finish = (ok) => {
                if (done) return;
                done = true;
                clearTimeout(timer);
                resolve(ok);
            };
            const timer = setTimeout(() => finish(false), timeoutMs);
            if (typeof video.requestVideoFrameCallback === 'function') {
                video.requestVideoFrameCallback(() => finish(true));
            } else {
                setTimeout(() => finish(video.readyState >= 2), 80);
            }
        });
    }

    function canvasLooksBlack(canvas) {
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        if (!ctx) return false;
        const w = canvas.width;
        const h = canvas.height;
        if (!w || !h) return true;
        const sampleW = Math.min(48, w);
        const sampleH = Math.min(32, h);
        const data = ctx.getImageData(
            Math.floor((w - sampleW) / 2),
            Math.floor((h - sampleH) / 2),
            sampleW,
            sampleH
        ).data;
        let brightPixels = 0;
        let totalBrightness = 0;
        const pixels = data.length / 4;
        for (let i = 0; i < data.length; i += 4) {
            const brightness = data[i] + data[i + 1] + data[i + 2];
            totalBrightness += brightness;
            if (brightness > 30) brightPixels++;
        }
        return (totalBrightness / Math.max(1, pixels)) < 8 && brightPixels < Math.max(4, pixels * 0.01);
    }

    function captureVideoPoster(video) {
        const vw = video.videoWidth || 0;
        const vh = video.videoHeight || 0;
        if (!vw || !vh) return null;
        const maxW = 320;
        const scale = Math.min(1, maxW / vw);
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(vw * scale));
        canvas.height = Math.max(1, Math.round(vh * scale));
        const ctx = canvas.getContext('2d');
        if (!ctx) return null;
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        return { url: canvas.toDataURL('image/jpeg', 0.82), black: canvasLooksBlack(canvas) };
    }

    async function buildVideoPoster(video, src, token) {
        const durations = [];
        for (let attempt = 0; attempt < 3; attempt++) {
            if (token !== historyThumbLoaderToken) return null;
            video.src = withCacheBust(src, `${Date.now()}-${attempt}`);
            video.load();
            const gotMeta = await waitForMediaEvent(video, 'loadedmetadata', 6500);
            if (!gotMeta) {
                await sleep(300 + attempt * 500);
                continue;
            }

            const duration = Number.isFinite(video.duration) ? video.duration : 0;
            const candidates = duration > 0
                ? [duration * 0.2, duration * 0.5, duration * 0.8]
                : [0.2, 0.7, 1.2];
            durations.splice(0, durations.length, ...candidates);

            for (const rawTime of durations) {
                if (token !== historyThumbLoaderToken) return null;
                const targetTime = Math.max(0.08, Math.min(Math.max(0.08, duration - 0.08), rawTime));
                try {
                    video.currentTime = targetTime;
                    await waitForMediaEvent(video, 'seeked', 3500);
                    await waitForVideoFrame(video, 1200);
                    const poster = captureVideoPoster(video);
                    if (poster && !poster.black) return poster.url;
                } catch (_) {}
            }
            await sleep(450 + attempt * 600);
        }
        const fallback = captureVideoPoster(video);
        return fallback ? fallback.url : null;
    }

    async function loadOneHistoryThumb(media, token) {
        const src = media.dataset.src;
        if (!src || token !== historyThumbLoaderToken) return false;
        media.classList.remove('lazy-load');
        media.classList.add('history-thumb-loading');

        const reveal = () => {
            media.classList.remove('history-thumb-loading');
            media.classList.add('history-thumb-ready');
        };

        if (media.tagName === 'VIDEO') {
            media.muted = true;
            media.playsInline = true;
            media.preload = 'metadata';
            const posterUrl = await buildVideoPoster(media, src, token);
            if (token !== historyThumbLoaderToken) return false;
            if (posterUrl) {
                const img = document.createElement('img');
                img.src = posterUrl;
                img.alt = '';
                img.className = media.className;
                img.classList.remove('history-thumb-loading');
                img.classList.add('history-thumb-ready');
                img.style.cssText = media.style.cssText;
                media.replaceWith(img);
                try {
                    media.removeAttribute('src');
                    media.load();
                } catch (_) {}
                return true;
            }
            try { media.pause(); } catch (_) {}
            if (token === historyThumbLoaderToken) reveal();
            return true;
        }

        media.src = src;
        if (media.complete && media.naturalWidth > 0) {
            reveal();
            return true;
        }
        const loaded = await waitForMediaEvent(media, 'load', 5000);
        if (token !== historyThumbLoaderToken) return false;
        if (loaded && media.naturalWidth > 0) {
            reveal();
            return true;
        }
        media.classList.remove('history-thumb-loading');
        media.classList.add('lazy-load');
        media.dataset.nextThumbRetry = String(Date.now() + 1200);
        media.removeAttribute('src');
        requestHistoryThumbLoad(1200);
        return false;
    }

    function findNextLazyHistoryMedia(limit) {
        const now = Date.now();
        return Array.from(document.querySelectorAll('#history-container .lazy-load'))
            .filter((candidate) => !!candidate.dataset.src && Number(candidate.dataset.nextThumbRetry || 0) <= now)
            .slice(0, limit);
    }

    async function loadVisibleImages() {
        if (historyThumbLoaderBusy) {
            historyThumbLoadQueued = true;
            return;
        }
        const hw = document.getElementById('history-wrapper');
        if (!hw) return;

        historyThumbLoaderBusy = true;
        historyThumbLoadQueued = false;
        const token = historyThumbLoaderToken;
        try {
            while (token === historyThumbLoaderToken) {
                const medias = findNextLazyHistoryMedia(HISTORY_THUMB_CONCURRENCY);

                if (!medias.length) break;
                await Promise.all(medias.map((media) => loadOneHistoryThumb(media, token)));
                await new Promise((resolve) => setTimeout(resolve, 50));
            }
        } finally {
            historyThumbLoaderBusy = false;
            if (historyThumbLoadQueued && token === historyThumbLoaderToken) {
                historyThumbLoadQueued = false;
                requestHistoryThumbLoad(120);
            }
        }
    }

    function requestHistoryThumbLoad(delay = 0) {
        const run = () => {
            if ('requestAnimationFrame' in window) {
                window.requestAnimationFrame(() => loadVisibleImages());
            } else {
                loadVisibleImages();
            }
        };
        if (delay > 0) {
            setTimeout(run, delay);
        } else {
            run();
        }
    }

    // ★ 2026-06-09: 用 IntersectionObserver 替换原来的 scroll 节流监听
    // 视频文件较大(5-50MB),点击后 <video> 还要走一遍网络才能开始播放。
    // 这里在卡片进入视口(rootMargin 提前 300px)时,就后台 fetch 把完整视频文件
    // 灌进 HTTP 缓存;用户真去点时,<video>.src=url 命中本地磁盘秒开。
    // 同时仅触发进入视口的卡片缩略图,避免一上来 240 张全抢带宽。
    const _videoPrefetchedSet = new Set();     // 已经预下载过的 filename
    const _videoPrefetchActiveMap = new Map();  // 正在下载的 filename -> AbortController
    const _videoPrefetchQueue = [];            // 等候中的预下载任务
    const VIDEO_PREFETCH_MAX_CONCURRENT = 1;   // ★ 2026-06-16 v4: 改 1,给缩略图让出带宽
                                              // 浏览器同源最多 6 并发,缩略图 8 并发本身就要排队
                                              // 视频一个一个下,首屏缩略图秒现,视频预下载慢慢补
    function _cancelAllVideoPrefetch() {
        for (const ctrl of _videoPrefetchActiveMap.values()) {
            try { ctrl.abort(); } catch (_) {}
        }
        _videoPrefetchActiveMap.clear();
        _videoPrefetchQueue.length = 0;
    }
    window.__cancelAllVideoPrefetch = _cancelAllVideoPrefetch;

    // 清空"已预下载"记录:文件 buster 变更后,旧的预下载 URL 失效,
    // 下次进入视口会重新预下载(用新的 buster)
    function _resetVideoPrefetchCache() {
        _cancelAllVideoPrefetch();
        _videoPrefetchedSet.clear();
    }
    window.__resetVideoPrefetchCache = _resetVideoPrefetchCache;

    function _doStartPrefetch(task) {
        const { fname, url, fileKey } = task;
        if (_videoPrefetchedSet.has(fname) || _videoPrefetchActiveMap.has(fname)) return;
        const ctrl = new AbortController();
        _videoPrefetchActiveMap.set(fname, ctrl);
        // 静默后台 fetch,不读 body、只让浏览器把文件存进 HTTP 缓存
        // priority: 'low' 让浏览器在带宽紧张时优先让给用户点击的真实请求
        let fetchOpts = {
            method: 'GET',
            signal: ctrl.signal,
            cache: 'force-cache',
            credentials: 'same-origin',
        };
        try { fetchOpts.priority = 'low'; } catch (_) {}
        fetch(url, fetchOpts)
            .then((res) => {
                if (res && res.body && typeof res.body.cancel === 'function') {
                    try { res.body.cancel(); } catch (_) {}
                }
            })
            .catch(() => { /* 静默失败,点击时仍会正常下载 */ })
            .finally(() => {
                _videoPrefetchActiveMap.delete(fname);
                _videoPrefetchedSet.add(fname);
                // 队列里还有等候的任务,继续
                _drainPrefetchQueue();
            });
    }
    function _drainPrefetchQueue() {
        while (_videoPrefetchActiveMap.size < VIDEO_PREFETCH_MAX_CONCURRENT && _videoPrefetchQueue.length > 0) {
            const task = _videoPrefetchQueue.shift();
            _doStartPrefetch(task);
        }
    }
    function _preloadVideoFile(item, globalDir) {
        if (!item || item.type !== 'video') return;
        const fname = item.filename;
        if (!fname) return;
        if (_videoPrefetchedSet.has(fname) || _videoPrefetchActiveMap.has(fname)) return;

        // 构造与 displayOutput 完全一致的 URL,确保缓存命中
        const fileKey = `video::${globalDir ? globalDir + '/' + fname : fname}`;
        const buster = (typeof _getFileBuster === 'function') ? _getFileBuster(fileKey) : String(Date.now());
        const rawPath = item.fullpath || (globalDir ? globalDir + '/' + fname : fname);
        const url = `${BASE}/api/system/file?path=${encodeURIComponent(rawPath)}&v=${buster}`;

        const task = { fname, url, fileKey };
        // 还有空位就直接起,满了就排队
        if (_videoPrefetchActiveMap.size < VIDEO_PREFETCH_MAX_CONCURRENT) {
            _doStartPrefetch(task);
        } else {
            // 队列里同名任务去重,避免重复排队
            if (!_videoPrefetchQueue.some((t) => t.fname === fname)) {
                _videoPrefetchQueue.push(task);
            }
        }
    }

    function _onHistoryCardEnter(card, withPrefetch) {
        if (!card) return;
        // 1) 缩略图懒加载:有 .lazy-load 子元素就走正常流程
        if (card.querySelector('img.lazy-load')) requestHistoryThumbLoad(0);
        // 2) 视频文件预下载:仅"容器内可见"才预下载,避免窗口级 IO 把视口外的也卷进来
        if (!withPrefetch) return;
        const fname = card.dataset.filename;
        const type = card.dataset.type;
        const fullpath = card.dataset.fullpath || '';
        const globalDir = card.dataset.globaldir || '';
        if (type === 'video' && fname) {
            // ★ 传入 fullpath,确保预下载 URL 与 displayOutput 构建的 URL 一致,缓存才能命中
            _preloadVideoFile({ filename: fname, type: 'video', fullpath: fullpath }, globalDir);
        }
    }

    function initHistoryScrollListener() {
        const hw = document.getElementById('history-wrapper');
        if (!hw) return;
        if (hw.dataset.ioInited === '1') return;
        hw.dataset.ioInited = '1';

        if (!('IntersectionObserver' in window)) {
            // 兜底:老浏览器退化到 scroll 节流(不预下载,只看缩略图)
            let scrollTimeout;
            hw.addEventListener('scroll', () => {
                if (scrollTimeout) clearTimeout(scrollTimeout);
                scrollTimeout = setTimeout(() => requestHistoryThumbLoad(), 100);
            });
            return;
        }

        // 容器级 IO: 缩略图 + 视频预下载 都做(用户能看到的才值得预下载)
        const containerObs = new IntersectionObserver((entries) => {
            for (const entry of entries) {
                if (entry.isIntersecting) {
                    _onHistoryCardEnter(entry.target, true);
                    containerObs.unobserve(entry.target);
                }
            }
        }, { root: hw, rootMargin: '300px 0px', threshold: 0.01 });

        // 窗口级 IO: 只做缩略图(便宜,反正 5 并发,不影响用户点击)
        //   用于"卡片虽然不在容器可视区但已经在窗口可视区"的情况
        const winObs = new IntersectionObserver((entries) => {
            for (const entry of entries) {
                if (entry.isIntersecting) {
                    _onHistoryCardEnter(entry.target, false);
                    winObs.unobserve(entry.target);
                }
            }
        }, { root: null, rootMargin: '300px 0px', threshold: 0.01 });

        // 暴露给 renderHistoryList 用,新生成的卡片挂上观察者
        // ★ 2026-06-16 v4 提速: 用函数声明 + 单独赋值,V8 才能 lazy-parse body
        function __observeHistoryCard(card) {
            if (!card) return;
            try { containerObs.observe(card); } catch (_) {}
            try { winObs.observe(card); } catch (_) {}
        }
        window.__observeHistoryCard = __observeHistoryCard;
    }

    // ★ 2026-06-16 v4 修复: 直接同步注册 IO 监听器,不再白等 500ms
    //   旧逻辑: 担心 IO 提前触发所以拖 500ms,实测现代浏览器完全没这问题
    //   收益: 首屏缩略图加载提前 500ms
    if (typeof IntersectionObserver !== 'undefined' && document.getElementById('history-wrapper')) {
        initHistoryScrollListener();
    } else {
        document.addEventListener('DOMContentLoaded', initHistoryScrollListener, { once: true });
    }

    window.displayHistoryOutput = function displayHistoryOutput(file, type, replayId = '') {
        console.log("[DEBUG][displayHistoryOutput] file:", file, "type:", type, "replayId:", replayId);
        const resImg = document.getElementById('res-img');
        const videoWrapper = document.getElementById('video-wrapper');
        const audioWrapper = document.getElementById('audio-wrapper');
        const loader = document.getElementById('loading-txt');
        if (resImg) resImg.style.display = 'none';
        if (videoWrapper) videoWrapper.style.display = 'none';
        if (audioWrapper) audioWrapper.style.display = 'none';
        // ★ 立即显示加载提示,避免用户看到空白
        if (loader) {
            loader.style.display = 'flex';
            loader.style.alignItems = 'center';
            loader.style.justifyContent = 'center';
            loader.style.gap = '8px';
            loader.innerHTML = '<span style="color:var(--text-dim);font-size:12px;">⏳ 加载中...</span>';
        }
        displayOutput(file, type, { replayId });
    }
    
    // 使用事件委托处理历史卡片点击（避免浏览器缓存导致的onclick失效）
    document.addEventListener('click', (e) => {
        const card = e.target.closest('.history-card, .history-card-list');
        if (!card) return;
        if (e.target.closest('.history-delete-btn')) return;
        if (e.target.closest('.history-save-btn')) return;
        document.querySelectorAll('.history-card.is-active, .history-card-list.is-active').forEach(c => c.classList.remove('is-active'));
        card.classList.add('is-active');
        const filename = card.dataset.filename;
        const fullpath = card.dataset.fullpath || '';
        const type = card.dataset.type;
        const replayId = card.dataset.replayid || '';
        // ★ 优先使用 fullpath(完整绝对路径),避免依赖 global-out-dir 元素
        //   fullpath 不存在时回退到 filename,由 displayOutput 自行拼接路径
        const fileToDisplay = fullpath || filename;
        // ★ 2026-06-09: 用户开始做选择,立刻让所有后台"还在飞的"工作退出,
        //              腾出带宽/线程给新点击,避免 A→B→C 时旧加载还占着资源导致卡顿
        //              1) 缩略图懒加载的 token 自增,旧的 loadVisibleImages 循环会退出
        //              2) 视频预下载全部 abort,不再浪费带宽(用户已经明确选了一个)
        historyThumbLoaderToken++;
        if (typeof window.__cancelAllVideoPrefetch === 'function') {
            window.__cancelAllVideoPrefetch();
        }
        console.log("[DEBUG][card-click] Click on history card:", fileToDisplay, type, replayId);
        displayHistoryOutput(fileToDisplay, type, replayId);
    });
    
    function _onDomReady2() {
        try {
        document.querySelectorAll('.layout-btn').forEach(b => b.classList.toggle('active', b.dataset.layout === _historyLayout));
        const hc = document.getElementById('history-container');
        if (hc) hc.classList.toggle('layout-list', _historyLayout === 'list');
        initAudioPreviewToggle();
        // Initialize Plyr Custom Video Component
        try {
        if(window.Plyr) {
            try {
            player = new Plyr('#res-video', {
                controls: [
                    'play-large', 'play', 'progress', 'current-time', 
                    'mute', 'volume', 'fullscreen'
                ],
                settings: [],
                loop: { active: true },
                autoplay: true
            });
            } catch(plyrInitErr) {
                console.error("[VIDEO-PROTECT] Plyr init failed:", plyrInitErr);
                player = null;
            }
            
            const videoEl = document.getElementById('res-video');
            if (videoEl) {
                videoEl.addEventListener('error', (e) => {
                    console.error("[VIDEO-PROTECT] HTML5 video error:", videoEl.error ? `code=${videoEl.error.code} msg=${videoEl.error.message}` : 'unknown', "src:", videoEl.src);
                });
            }
            if (player) {
                player.on('error', (e) => {
                    console.error("[VIDEO-PROTECT] Plyr error:", e);
                });
            }
            
            try {
            audioPlayer = new Plyr('#res-audio', {
                controls: [
                    'play', 'progress', 'current-time',
                    'mute', 'volume'
                ],
                settings: []
            });
            } catch(plyrAudioErr) {
                console.error("[VIDEO-PROTECT] Plyr audio init failed:", plyrAudioErr);
                audioPlayer = null;
            }
        }
        } catch(plyrErr) {
            console.error("[VIDEO-PROTECT] Plyr setup exception:", plyrErr);
            player = null;
            audioPlayer = null;
        }

        const startupVideo = document.getElementById('res-video');
        const startupVideoWrapper = document.getElementById('video-wrapper');
        const startupImg = document.getElementById('res-img');
        const startupAudio = document.getElementById('res-audio');
        const startupAudioWrapper = document.getElementById('audio-wrapper');
        if (startupImg) {
            startupImg.style.display = 'none';
            startupImg.removeAttribute('src');
        }
        if (startupVideoWrapper) startupVideoWrapper.style.display = 'none';
        if (startupVideo) {
            try { startupVideo.pause(); } catch (_) {}
            startupVideo.removeAttribute('src');
            try { startupVideo.load(); } catch (_) {}
        }
        if (startupAudioWrapper) startupAudioWrapper.style.display = 'none';
        if (startupAudio) {
            try { startupAudio.pause(); } catch (_) {}
            startupAudio.removeAttribute('src');
            try { startupAudio.load(); } catch (_) {}
        }
        
        const loadOutputDir = fetch(`${BASE}/api/system/get-dir`)
            .then((res) => res.json())
            .then((data) => {
                if (data && data.directory) {
                    const outInput = document.getElementById('global-out-dir');
                    if (outInput) outInput.value = data.directory;
                }
            })
            .catch((e) => console.error(e));

        (async () => {
            await scanLoras();
            await loadOutputDir;
            await switchLibTab('history');
        })();

        // Load LoRA dir from settings
        loadLoraDir();
        loadModelCheckpoints();
        loadCoreModels();

        // ★ 2026-06-16 v4 提速: 自动刷新改成"首次 30s,后续 60s"
        //   旧: 每 15s 跑一次 → 哪怕用户没动,前端也频繁发请求 + 检查 fingerprint
        //   新: 首次 30s(让用户先消化首屏)→ 后续 60s(生成场景下够用,静止场景几乎无感)
        //   关键: 即便触发,fingerprint 一致就静默返回,不会清空 UI
        let historyRefreshInterval = null;
        function startHistoryAutoRefresh() {
            if (historyRefreshInterval) return;
            // ★ 首次延迟 30s,避开"刚加载完用户还没看清"就被打断的尴尬
            historyRefreshInterval = setTimeout(function _tick() {
                const hc = document.getElementById('history-container');
                // 容器存在 + 可见(非隐藏) + 没在生成中 + 没在加载中 → 才发请求
                if (hc && hc.offsetParent !== null && !_isGeneratingFlag && !isLoadingHistory) {
                    fetchHistory(false, true);
                }
                // ★ 后续每 60s 一次(原来是 15s,太快了)
                historyRefreshInterval = setTimeout(_tick, 60000);
            }, 30000);
        }
        startHistoryAutoRefresh();
        } catch(e) { console.error('[INIT] _onDomReady2 error:', e); }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _onDomReady2);
    } else {
        _onDomReady2();
    }


async function saveVramLimit() {
    const lim = document.getElementById("vram-limit-input").value;
    const status = document.getElementById("vram-limit-status");
    status.textContent = "保存中...";
    try {
        const res = await fetch(`${BASE}/api/vram-limit`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ vramLimit: lim })
        });
        const d = await res.json();
        if (d.status === 'ok') {
            status.textContent = "保存成功";
            status.style.color = '#4caf50';
        } else throw new Error(d.message || "Unknown error");
    } catch (e) {
        status.textContent = e.message;
        status.style.color = '#f44336';
    }
}
async function fetchVramLimit() {
    try {
        const res = await fetch(`${BASE}/api/vram-limit`);
        const d = await res.json();
        const input = document.getElementById("vram-limit-input");
        if (input) {
            input.value = (d.vramLimit !== undefined && d.vramLimit !== null && d.vramLimit !== '') ? d.vramLimit : '0';
        }
    } catch (e) {}
}
try { fetchVramLimit(); } catch(e) {}

// ── 硬件智能推荐 (动态联动版) ──────────────────────────────────
// 核心逻辑: VRAM 限制的是总帧数 (total_frames = fps × seconds)
// 调高 FPS → 推荐秒数自动缩短，反之亦然
let _hwProfile = null;

async function loadHardwareProfile() {
    try {
        const res = await fetch(`${BASE}/api/system/hardware-profile`);
        _hwProfile = await res.json();
        renderHardwareProfile(_hwProfile);
        renderRecommendationBadges(_hwProfile);
    } catch (e) {
        console.warn('loadHardwareProfile failed:', e);
    }
}

/** 根据当前清晰度 + FPS，找到对应的推荐方案 */
function _findRecForQuality(quality) {
    if (!_hwProfile || !_hwProfile.recommendations) return null;
    const widthMap = { '1080': 1920, '720': 1280, '540': 960, '480': 768, '360': 640 };
    const targetWidth = widthMap[quality];
    if (targetWidth) {
        const exact = _hwProfile.recommendations.find(r => r.width === targetWidth);
        if (exact) return exact;
    }
    const sorted = [..._hwProfile.recommendations].sort((a, b) => a.width - b.width);
    if (targetWidth) {
        const lower = sorted.filter(r => r.width < targetWidth);
        if (lower.length > 0) return lower[lower.length - 1];
    }
    return sorted[0] || null;
}

/** 清晰度值 → 推荐方案中的 label 匹配用 key */
function _qualityKey(width) {
    if (width >= 1500) return '1080';
    if (width >= 1000) return '720';
    if (width >= 800) return '540';
    if (width >= 600) return '480';
    return '360';
}

/** 动态联动推荐 — FPS/清晰度变化时自动更新推荐时长和帧预算提示 */
function updateDynamicRecommendation() {
    if (!_hwProfile) return;

    const quality = document.getElementById('vid-quality').value;
    const fps = parseInt(document.getElementById('vid-fps').value) || 24;
    const durationEl = document.getElementById('vid-duration');
    const currentDuration = parseInt(durationEl.value) || 5;

    const rec = _findRecForQuality(quality);
    if (!rec) return;

    // 核心联动: duration = total_frames / fps
    const maxDuration = Math.max(1, Math.floor(rec.max_total_frames / fps));
    const recDuration = Math.max(1, Math.floor(rec.recommended_total_frames / fps));

    // 更新时长徽章 — 显示帧预算和动态秒数
    const setBadge = (id, text) => {
        const el = document.getElementById(id);
        if (el) { el.textContent = text; el.style.display = 'inline'; }
    };

    setBadge('vid-duration-rec', `建议≤${recDuration}s@${fps}fps`);
    setBadge('vid-fps-rec', `推荐${(rec.recommended_fps || [24])[0]}fps`);
    const vidSize = getGenerationSize('video');
    setBadge('vid-quality-rec', `推荐${_qualityKey(rec.width)}P（${vidSize.width}×${vidSize.height}）`);
    const batchSize = getGenerationSize('batch');
    setBadge('batch-quality-rec', `推荐${_qualityKey(rec.width)}P（${batchSize.width}×${batchSize.height}）`);

    _updateDurationWarning(currentDuration, recDuration, maxDuration, rec);

    // 同步批量模式徽章
    const batchQuality = document.getElementById('batch-quality');
    if (batchQuality) {
        const bRec = _findRecForQuality(batchQuality.value);
        if (bRec) {
            setBadge('batch-quality-rec', `⭐ 帧预算${bRec.recommended_total_frames}帧`);
        }
    }

    // 更新硬件面板中的动态时长提示
    _updateProfileDurationHints(fps);
}

/** 时长输入框下方的警告/提示文字 */
function _updateDurationWarning(current, recDuration, maxDuration, rec) {
    let warnEl = document.getElementById('vid-duration-warn');
    if (!warnEl) {
        // 动态创建警告元素
        const durContainer = document.getElementById('vid-duration').parentElement;
        warnEl = document.createElement('div');
        warnEl.id = 'vid-duration-warn';
        warnEl.style.cssText = 'font-size:10px; margin-top:2px; line-height:1.3;';
        durContainer.appendChild(warnEl);
    }

    if (current > maxDuration) {
        warnEl.innerHTML = `<span style="color:#f44336;">⚠ 超出帧预算! 最多${maxDuration}s@${rec.max_total_frames}帧</span>`;
    } else if (current > recDuration) {
        warnEl.innerHTML = `<span style="color:#FF9800;">⚡ 超推荐值，可能OOM — 建议≤${recDuration}s</span>`;
    } else {
        warnEl.innerHTML = `<span style="color:var(--text-dim);">✓ 当前${current * parseInt(document.getElementById('vid-fps').value || 24)}帧，在安全范围内</span>`;
    }
}

/** 更新硬件面板中推荐方案的动态时长提示 */
function _updateProfileDurationHints(fps) {
    if (!_hwProfile) return;
    const recsEl = document.getElementById('hw-profile-recs');
    if (!recsEl) return;
    // 重新渲染推荐列表，加入当前FPS的时长计算
    const recs = _hwProfile.recommendations || [];
    recsEl.innerHTML = '';
    recs.forEach(r => {
        const isPrimary = r.label.includes('推荐');
        const maxD = Math.max(1, Math.floor(r.max_total_frames / fps));
        const recD = Math.max(1, Math.floor(r.recommended_total_frames / fps));
        const div = document.createElement('div');
        div.style.cssText = `display:flex; align-items:center; gap:6px; padding:4px 8px; margin-bottom:4px; border-radius:6px; font-size:11px; cursor:pointer; border:1px solid ${isPrimary ? 'var(--accent)' : 'rgba(255,255,255,0.08)'}; background:${isPrimary ? 'rgba(255,255,255,0.06)' : 'transparent'};`;
        div.innerHTML = `
            <span style="font-weight:700; color:${isPrimary ? 'var(--accent)' : 'var(--text-main)'}; min-width:70px;">${r.label}</span>
            <span style="color:var(--text-dim);">${r.width}×${r.height}</span>
            <span style="color:var(--text-dim);">| 帧预算${r.recommended_total_frames}帧</span>
            <span style="color:var(--accent); font-weight:600;">→ ≤${recD}s@${fps}fps</span>
            <span style="color:var(--text-dim); font-size:10px;">(极限${maxD}s)</span>
        `;
        div.onclick = () => applyRecommendation(r);
        recsEl.appendChild(div);
    });
}

function renderHardwareProfile(p) {
    const panel = document.getElementById('hw-profile-panel');
    if (!panel || !p) return;
    panel.style.display = 'block';

    const tierColors = { ultra: '#FFD700', high: '#4CAF50', medium: '#2196F3', low: '#FF9800', minimal: '#f44336' };
    const tierColor = tierColors[p.tier] || '#999';

    document.getElementById('hw-profile-gpu').textContent =
        (p.gpu_name || 'Unknown') + (p.gpu_vram_gb ? ` (${p.gpu_vram_gb}GB)` : '') +
        (p.system_ram_gb ? ` | RAM: ${p.system_ram_gb}GB` : '');

    document.getElementById('hw-profile-tier').innerHTML =
        `<span style="color:${tierColor};">▎</span> ${p.tier_name} — ${p.vram_range}`;

    // 推荐方案 (使用当前FPS动态计算时长)
    const currentFps = parseInt(document.getElementById('vid-fps')?.value) || 24;
    _updateProfileDurationHints(currentFps);

    // 提示
    const tipsEl = document.getElementById('hw-profile-tips');
    tipsEl.innerHTML = (p.tips || []).map(t => `💡 ${t}`).join('<br>');
}

/** 根据硬件档案，在表单字段旁渲染推荐徽章，标注 select 中的推荐项，并自动应用首选参数 */
function renderRecommendationBadges(profile) {
    if (!profile || !profile.recommendations) return;

    const primaryRec = profile.recommendations.find(r => r.label.includes('推荐'));
    if (!primaryRec) return;

    // 推荐清晰度
    const recQuality = _qualityKey(primaryRec.width);
    const recFps = primaryRec.recommended_fps ? primaryRec.recommended_fps[0] : 24;

    // 在 select 选项中标注推荐项 ⭐
    _markRecommendedOption('vid-quality', recQuality);
    _markRecommendedOption('batch-quality', recQuality);
    _markRecommendedOption('vid-fps', String(recFps));

    const hasPersisted = _hasPersistedSettings();

    if (hasPersisted) {
        updateDynamicRecommendation();
    } else {
        applyRecommendation(primaryRec);
        _showAutoApplyToast(profile);
        updateDynamicRecommendation();
    }
}

function _hasPersistedSettings() {
    try {
        const s = JSON.parse(localStorage.getItem(SETTINGS_KEY));
        if (!s || typeof s !== 'object') return false;
        return s['vid-quality'] !== undefined || s['vid-fps'] !== undefined || s['vid-duration'] !== undefined;
    } catch(_) {
        return false;
    }
}

/** 在 <select> 的推荐选项后追加 ⭐ 标记 */
function _markRecommendedOption(selectId, recValue) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    if (sel.tagName === 'SELECT') {
        for (const opt of sel.options) {
            const base = opt.text.replace(/ ⭐$/, '');
            opt.text = opt.value === recValue ? base + ' ⭐' : base;
        }
    }
}

/** 显示自动应用推荐的 toast 通知 */
function _showAutoApplyToast(profile) {
    document.querySelectorAll('.hw-auto-toast').forEach(el => el.remove());
    const toast = document.createElement('div');
    toast.className = 'hw-auto-toast';
    toast.style.cssText = 'position:fixed;bottom:80px;right:20px;background:rgba(76,175,80,0.95);color:#fff;padding:10px 16px;border-radius:8px;font-size:12px;z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,0.3);transition:opacity 0.5s;max-width:300px;line-height:1.4;';
    toast.textContent = `⭐ 已根据 ${profile.gpu_name || 'GPU'} (${profile.tier_name}) 自动推荐最佳参数`;
    document.body.appendChild(toast);
    setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 500); }, 3500);
}

function applyRecommendation(r) {
    // 根据推荐分辨率自动设置清晰度
    const qualityVal = _qualityKey(r.width);

    // 视频模式
    document.getElementById('vid-quality').value = qualityVal;
    updateResPreview();

    // 批量模式
    const bq = document.getElementById('batch-quality');
    if (bq) { bq.value = qualityVal; updateBatchResPreview(); }

    // 设置帧率
    const fps = document.getElementById('vid-fps');
    if (r.recommended_fps && r.recommended_fps.length > 0) {
        fps.value = String(r.recommended_fps[0]);
        // 触发 input 事件,让 initPresetChips 里挂的监听把 select 同步过来
        fps.dispatchEvent(new Event('input', { bubbles: true }));
    }

    // 设置时长 (动态计算: duration = recommended_total_frames / fps)
    const currentFps = parseInt(fps.value) || 24;
    const recDuration = Math.max(1, Math.floor(r.recommended_total_frames / currentFps));
    const dur = document.getElementById('vid-duration');
    dur.value = String(recDuration);
    // 同步 select
    dur.dispatchEvent(new Event('input', { bubbles: true }));

    // 触发动态推荐更新
    updateDynamicRecommendation();

    // 闪烁提示
    const panel = document.getElementById('hw-profile-panel');
    if (panel) {
        panel.style.borderColor = 'var(--accent)';
        setTimeout(() => { panel.style.borderColor = ''; }, 1000);
    }
}

async function applyHardwareProfile() {
    if (!_hwProfile) return;
    try {
        const res = await fetch(`${BASE}/api/system/apply-profile`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({tier: _hwProfile.tier}) });
        const d = await res.json();
        if (d.status === 'success') {
            const status = document.getElementById('vram-limit-status');
            status.textContent = `✓ 已应用 ${d.tier_name} (vram_limit=${d.vram_limit_gb}GB, offload=${d.offload_enabled})`;
            status.style.color = '#4CAF50';
            document.getElementById('vram-limit-input').value = d.vram_limit_gb || '0';
            // 自动应用推荐方案中第一个"推荐"项
            const primaryRec = (_hwProfile.recommendations || []).find(r => r.label.includes('推荐'));
            if (primaryRec) applyRecommendation(primaryRec);
        }
    } catch (e) {
        const status = document.getElementById('vram-limit-status');
        status.textContent = '应用失败: ' + e.message;
        status.style.color = '#f44336';
    }
}

async function refreshAndSync() {
    // 2026-06-10: 模型管理已下放给启动器,前端仅触发"同步HF元数据"以便刷出最新描述/大小
    try {
        const res = await fetch(`${BASE}/api/models/registry/refresh-hf`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({force: false}),
        });
        if (window._appendLog) window._appendLog('🤗 已触发 HuggingFace 元数据后台刷新', 'info');
    } catch (e) {
        if (window._appendLog) window._appendLog(`⚠️ 同步失败: ${e.message}`, 'warn');
    }
}
window.refreshAndSync = refreshAndSync;

// ── 动态联动事件绑定 ──────────────────────────────────────────
// FPS/清晰度/时长 变化时自动更新推荐

function _initDynamicRecommendation() {
    const qualityEl = document.getElementById('vid-quality');
    const fpsEl = document.getElementById('vid-fps');
    const durationEl = document.getElementById('vid-duration');

    if (qualityEl) {
        const origOnchange = qualityEl.onchange;
        qualityEl.onchange = function() {
            if (origOnchange) origOnchange.call(this);
            updateDynamicRecommendation();
        };
    }
    if (fpsEl) {
        fpsEl.addEventListener('change', () => updateDynamicRecommendation());
    }
    if (durationEl) {
        durationEl.addEventListener('input', () => updateDynamicRecommendation());
    }

    const motionDurationEl = document.getElementById('motion-duration');
    if (motionDurationEl) {
        motionDurationEl.addEventListener('input', () => _updateMotionParamWarnings());
    }
    const motionFpsEl = document.getElementById('motion-fps');
    if (motionFpsEl) {
        motionFpsEl.addEventListener('change', () => _updateMotionParamWarnings());
    }

    // 批量模式清晰度变化也触发
    const batchQualityEl = document.getElementById('batch-quality');
    if (batchQualityEl) {
        const origBatchOnchange = batchQualityEl.onchange;
        batchQualityEl.onchange = function() {
            if (origBatchOnchange) origBatchOnchange.call(this);
            const bRec = _findRecForQuality(batchQualityEl.value);
            const setBadge = (id, text) => {
                const el = document.getElementById(id);
                if (el) { el.textContent = text; el.style.display = 'inline'; }
            };
            if (bRec) setBadge('batch-quality-rec', `⭐ 帧预算${bRec.recommended_total_frames}帧`);
        };
    }
}

function _updateMotionParamWarnings() {
    const durationEl = document.getElementById('motion-duration');
    const fpsEl = document.getElementById('motion-fps');
    if (!durationEl) return;

    const duration = parseInt(durationEl.value) || 5;
    const fps = parseInt(fpsEl?.value) || 24;

    let warnEl = document.getElementById('motion-param-warn');
    if (!warnEl) {
        const container = durationEl.closest('.flex-row') || durationEl.parentElement;
        warnEl = document.createElement('div');
        warnEl.id = 'motion-param-warn';
        warnEl.style.cssText = 'font-size:10px; margin-top:4px; line-height:1.3;';
        if (container && container.parentElement) container.parentElement.appendChild(warnEl);
    }

    const totalFrames = duration * fps;
    const warnings = [];
    if (duration > 6) warnings.push(`<span style="color:#FF9800;">⚡ 时长${duration}s较长，可能OOM</span>`);
    if (totalFrames > 161) warnings.push(`<span style="color:#f44336;">⚠ 总帧数${totalFrames}超出上限(161帧)</span>`);

    if (warnings.length > 0) {
        warnEl.innerHTML = warnings.join('<br>');
    } else {
        warnEl.innerHTML = `<span style="color:var(--text-dim);">✓ ${totalFrames}帧 @ 8+3步蒸馏，在安全范围内</span>`;
    }
}

// ★ 2026-06-16 v4 修复: 延迟从 3s/3.5s/4s 缩到 0.5s/0.5s/requestIdleCallback
//   旧逻辑: 写"3秒等后端就绪"是因为早期后端启动慢,现在后端 100ms 就绪,白等 3 秒
//   env-check 改用 requestIdleCallback(浏览器空闲时跑,不抢首屏带宽)
const _idle = window.requestIdleCallback || function (cb) { return setTimeout(cb, 200); };
const _now = function (cb, delay) { setTimeout(cb, delay); };
_now(loadHardwareProfile, 500);
_now(_initDynamicRecommendation, 500);
_idle(() => {
    fetch(`${BASE}/api/system/env-check`).then(r => r.json()).then(d => { _envCheckData = d; }).catch(() => {});
});

// ── 环境检测面板 ──────────────────────────────────────────────────
let _envCheckData = null;

async function loadEnvCheck() {
    const panel = document.getElementById('env-settings');
    const list = document.getElementById('env-check-list');
    const badge = document.getElementById('env-check-badge');
    const btn = document.getElementById('env-detect-btn');
    if (!panel || !list) return;

    if (btn) {
        btn.textContent = _t('envScanning');
        btn.disabled = true;
        btn.style.opacity = '0.6';
    }
    list.innerHTML = `<div style="font-size:11px;color:var(--text-dim);text-align:center;padding:8px;">${_t('envScanning')}</div>`;
    if (panel.style.display === 'none') {
        panel.style.display = 'block';
        const btn = document.querySelector('button[onclick*="env-settings"]');
        if (btn) btn.classList.add('active');
    }

    try {
        const res = await fetch(`${BASE}/api/system/env-check`);
        _envCheckData = await res.json();
        renderEnvCheck(_envCheckData);
        loadEnvPreset();
    } catch (e) {
        list.innerHTML = `<div style="font-size:11px;color:#f44336;text-align:center;padding:8px;">${_t('envCheckFailed')}</div>`;
    } finally {
        if (btn) {
            btn.textContent = _t('envDetect');
            btn.disabled = false;
            btn.style.opacity = '1';
        }
    }
}

function renderEnvCheck(data) {
    const panel = document.getElementById('env-settings');
    const list = document.getElementById('env-check-list');
    const badge = document.getElementById('env-check-badge');
    if (!panel || !list || !data) return;

    if (panel.style.display === 'none') {
        panel.style.display = 'block';
        const btn = document.querySelector('button[onclick*="env-settings"]');
        if (btn) btn.classList.add('active');
    }

    if (data.has_issues) {
        badge.textContent = _t('envIssuesFound');
        badge.style.background = 'rgba(244,67,54,0.2)';
        badge.style.color = '#f44336';
        panel.style.borderColor = 'rgba(244,67,54,0.3)';
    } else {
        badge.textContent = _t('envAllOk');
        badge.style.background = 'rgba(76,175,80,0.2)';
        badge.style.color = '#4CAF50';
        panel.style.borderColor = 'rgba(76,175,80,0.2)';
    }

    list.innerHTML = '';

    for (const comp of (data.components || [])) {
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:6px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);';

        const statusIcon = _envStatusIcon(comp.status);
        const variantTag = _envVariantTag(comp);
        const versionText = comp.version || _t('envNotDetected');
        const recText = comp.recommended_version ? `${_t('envRecommended')}: ${comp.recommended_version}${comp.recommended_variant ? ` (${comp.recommended_variant})` : ''}` : '';

        let statusColor = 'var(--text-dim)';
        let versionColor = 'var(--text-main)';
        if (comp.status === 'ok') {
            statusColor = '#4CAF50';
            versionColor = 'var(--text-main)';
        } else if (comp.status === 'outdated') {
            statusColor = '#FF9800';
            versionColor = '#FF9800';
        } else if (comp.status === 'missing') {
            statusColor = '#f44336';
            versionColor = '#f44336';
        } else if (comp.status === 'wrong_variant_cpu') {
            statusColor = '#f44336';
            versionColor = '#f44336';
        } else if (comp.status === 'wrong_variant_gpu') {
            statusColor = '#FF9800';
            versionColor = '#FF9800';
        } else if (comp.status === 'mismatch') {
            statusColor = '#FF9800';
            versionColor = '#FF9800';
        }

        const leftDiv = document.createElement('div');
        leftDiv.style.cssText = 'flex:1;min-width:0;';
        leftDiv.innerHTML = `
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px;">
                <span style="font-size:11px;font-weight:700;color:var(--text-main);">${comp.name}</span>
                ${variantTag}
            </div>
            <div style="font-size:10px;color:${versionColor};font-variant-numeric:tabular-nums;">${versionText}</div>
            ${recText ? `<div style="font-size:9px;color:var(--text-dim);margin-top:1px;">${recText}</div>` : ''}
        `;

        const rightDiv = document.createElement('div');
        rightDiv.style.cssText = 'display:flex;align-items:center;gap:4px;flex-shrink:0;';

        rightDiv.appendChild(statusIcon);

        if (comp.status !== 'ok') {
            const fixBtn = document.createElement('button');
            fixBtn.style.cssText = 'font-size:9px;padding:3px 8px;border-radius:4px;cursor:pointer;font-weight:600;border:1px solid;white-space:nowrap;background:rgba(21,101,192,0.2);border-color:rgba(25,118,210,0.5);color:#42A5F5;';
            if (comp.status === 'wrong_variant_cpu' || comp.status === 'outdated') {
                fixBtn.textContent = _t('envFix');
            } else if (comp.status === 'missing') {
                fixBtn.textContent = _t('envInstall');
            } else if (comp.status === 'mismatch') {
                fixBtn.textContent = _t('envMismatch');
            } else {
                fixBtn.textContent = _t('envFix');
            }
            const compKey = comp.name.toLowerCase().replace(/ /g, '_').replace(/-/g, '_');
            fixBtn.onclick = () => fixEnvComponent(compKey, fixBtn);
            rightDiv.appendChild(fixBtn);
        }

        row.appendChild(leftDiv);
        row.appendChild(rightDiv);
        list.appendChild(row);
    }
}

function _envStatusIcon(status) {
    const span = document.createElement('span');
    span.style.cssText = 'font-size:12px;line-height:1;';
    switch (status) {
        case 'ok':
            span.textContent = '✓';
            span.style.color = '#4CAF50';
            break;
        case 'outdated':
            span.textContent = '⚠';
            span.style.color = '#FF9800';
            break;
        case 'missing':
            span.textContent = '✗';
            span.style.color = '#f44336';
            break;
        case 'wrong_variant_cpu':
            span.innerHTML = '<span style="font-size:9px;font-weight:800;color:#f44336;">CPU</span>';
            break;
        case 'wrong_variant_gpu':
            span.innerHTML = '<span style="font-size:9px;font-weight:800;color:#FF9800;">GPU</span>';
            break;
        case 'mismatch':
            span.textContent = '⚠';
            span.style.color = '#FF9800';
            break;
        default:
            span.textContent = '?';
            span.style.color = 'var(--text-dim)';
    }
    return span;
}

function _envVariantTag(comp) {
    if (!comp.variant) return '';
    const isGpu = comp.is_gpu;
    const tagColor = isGpu ? '#4CAF50' : '#FF9800';
    const tagText = isGpu ? 'GPU' : 'CPU';
    return `<span style="font-size:9px;padding:1px 4px;border-radius:3px;background:${tagColor}22;color:${tagColor};font-weight:700;border:1px solid ${tagColor}44;">${tagText}</span>`;
}

async function fixEnvComponent(component, btn) {
    const origText = btn.textContent;
    btn.textContent = _t('envFixing');
    btn.disabled = true;
    btn.style.opacity = '0.6';

    try {
        const res = await fetch(`${BASE}/api/system/env-fix`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ component }),
        });
        const d = await res.json();

        if (d.status === 'success') {
            btn.textContent = _t('envFixDone');
            btn.style.background = 'rgba(76,175,80,0.15)';
            btn.style.borderColor = 'rgba(76,175,80,0.4)';
            btn.style.color = '#4CAF50';
            setTimeout(() => loadEnvCheck(), 2000);
        } else {
            const errMsg = d.error || _t('envFixFailed');
            btn.textContent = _t('envFixFailed');
            btn.style.background = 'rgba(244,67,54,0.15)';
            btn.style.borderColor = 'rgba(244,67,54,0.4)';
            btn.style.color = '#f44336';

            _showEnvFixToast(errMsg, 'error');
            setTimeout(() => {
                btn.textContent = origText;
                btn.disabled = false;
                btn.style.opacity = '1';
            }, 3000);
        }
    } catch (e) {
        btn.textContent = _t('envFixFailed');
        setTimeout(() => {
            btn.textContent = origText;
            btn.disabled = false;
            btn.style.opacity = '1';
        }, 2000);
    }
}

function _showEnvFixToast(message, type) {
    document.querySelectorAll('.env-fix-toast').forEach(el => el.remove());
    const toast = document.createElement('div');
    toast.className = 'env-fix-toast';
    const bgColor = type === 'error' ? 'rgba(244,67,54,0.95)' : 'rgba(76,175,80,0.95)';
    toast.style.cssText = `position:fixed;bottom:80px;right:20px;background:${bgColor};color:#fff;padding:10px 16px;border-radius:8px;font-size:11px;z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,0.3);transition:opacity 0.5s;max-width:400px;line-height:1.4;word-break:break-word;`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 500); }, 5000);
}

async function loadEnvPreset() {
    const section = document.getElementById('env-preset-section');
    const details = document.getElementById('env-preset-details');
    const tierSpan = document.getElementById('env-preset-tier');
    if (!section || !details) return;

    try {
        const res = await fetch(`${BASE}/api/system/env-preset`);
        const data = await res.json();
        renderEnvPreset(data);
    } catch (e) {
        section.style.display = 'none';
    }
}

function renderEnvPreset(data) {
    const section = document.getElementById('env-preset-section');
    const details = document.getElementById('env-preset-details');
    const tierSpan = document.getElementById('env-preset-tier');
    if (!section || !details || !data || !data.preset) return;

    section.style.display = 'block';

    const p = data.preset;
    const tierNames = { ultra: '极致', high: '高性能', medium: '均衡', low: '节能', minimal: '极限' };
    if (tierSpan) {
        tierSpan.textContent = tierNames[data.tier] || data.tier;
    }

    const gpuFeat = data.gpu_features || {};
    const featTags = [];
    if (gpuFeat.fp8_native) featTags.push('<span style="color:#4CAF50;">FP8</span>');
    if (gpuFeat.sage_attention) featTags.push('<span style="color:#4CAF50;">SageAttn</span>');
    if (gpuFeat.bf16) featTags.push('<span style="color:#4CAF50;">BF16</span>');
    if (gpuFeat.tensor_cores) featTags.push('<span style="color:#4CAF50;">TensorCore</span>');

    const stack = p.stack || {};
    const model = p.model_recommendation || {};
    const inferCfg = p.inference_config || {};
    const sysReq = p.system_requirements || {};
    const perf = p.performance_estimate || {};

    let html = '';

    html += `<div style="margin-bottom:4px;"><b style="color:var(--text-main);">${_t('envPresetTarget')}:</b> ${p.target_hardware || ''}</div>`;

    if (featTags.length > 0) {
        html += `<div style="margin-bottom:4px;"><b style="color:var(--text-main);">GPU:</b> ${featTags.join(' ')} <span style="color:var(--text-dim);">(${gpuFeat.arch_name || ''})</span></div>`;
    }

    html += `<div style="margin-bottom:4px;"><b style="color:var(--text-main);">${_t('envPresetStack')}:</b> PyTorch ${stack.pytorch || ''} + Python ${stack.python || ''}</div>`;

    html += `<div style="margin-bottom:4px;"><b style="color:var(--text-main);">${_t('envPresetModel')}:</b> ${model.primary || ''}</div>`;

    const resKey = inferCfg.recommended_resolution || '';
    if (resKey) {
        html += `<div style="margin-bottom:4px;"><b style="color:var(--text-main);">${_t('envPresetRes')}:</b> ${resKey}</div>`;
    }

    if (sysReq.min_ram_gb) {
        html += `<div style="margin-bottom:4px;"><b style="color:var(--text-main);">${_t('envPresetRam')}:</b> >=${sysReq.min_ram_gb}GB${sysReq.recommended_ram_gb ? ` (${_t('envRecommended')}: ${sysReq.recommended_ram_gb}GB)` : ''}</div>`;
    }

    const perfEntries = Object.entries(perf).slice(0, 2);
    if (perfEntries.length > 0) {
        const perfStr = perfEntries.map(([k, v]) => `${k}: ${v}`).join(' | ');
        html += `<div style="margin-bottom:2px;"><b style="color:var(--text-main);">${_t('envPresetPerf')}:</b> ${perfStr}</div>`;
    }

    const tips = p.tips || [];
    if (tips.length > 0) {
        html += `<div style="margin-top:4px;font-size:9px;line-height:1.4;color:var(--text-dim);">`;
        for (const tip of tips.slice(0, 3)) {
            html += `💡 ${tip}<br>`;
        }
        html += `</div>`;
    }

    details.innerHTML = html;
}

// ★ 2026-06-16 v4: 删掉 4s 的 env-check 重复定时器(上面已经用 requestIdleCallback 启过了)

