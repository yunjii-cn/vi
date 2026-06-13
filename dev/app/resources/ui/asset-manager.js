/* =====================================================================
 *  AssetManager — 云集智能视频创意站 · 历史资产管理器
 *  ---------------------------------------------------------------------
 *  单一职责：把"历史资产列表"从 index.js 抽出来，独立成模块。
 *  对外只暴露 window.Assets，全局命名空间干净。
 *
 *  ─ 设计原则 ─
 *  1. 状态/数据/渲染/懒加载/播放/生命周期，分块清晰，每块做一件事
 *  2. 转义：attr() 用于 data-* 属性，js() 用于 onclick 内联字符串
 *  3. URL 构造统一走 makeUrl()，cache buster 共享 key 空间
 *  4. 布局：单一 HTML 结构 + CSS [data-layout] 切换，零重复渲染
 *  5. 懒加载：单一 IntersectionObserver + token 抢占 + 视频预下载队列
 *  6. 播放：单一 play()，抢占代次 + Plyr↔native fallback + 错误兜底
 *  7. 错误：所有 IO 都有 try/catch + 用户反馈 + 控制台 debug
 *
 *  ─ 修复的历史 bug ─
 *  - safeFilename 在 data-* 用 JS 字符串转义 → 改 attr() 专做属性转义
 *  - 加载卡也带 .history-card 被 click 误抓 → 用 .asset-card 唯一类
 *  - 文件被覆盖图不对版 → 统一走 buster 失效 + 节点 replaceWith
 *  - /outputs 写死路径 → 已由 main.py 占位符修复（patches/_ui_server.py 同步）
 *  - 自动 15s 刷新跟用户操作抢带宽 → isGenerating 时停 + token 抢占
 *  - Plyr fallback 链断 → 玩家置 null 后走 native，不再回 Plyr
 *  - delete/download 路径用脏 data-filename → 改用 item 映射
 * ===================================================================== */
(function () {
    'use strict';

    // ============================================================
    // Section 0 · 工具层 —— 最简、最稳、不造轮子
    // ============================================================

    // HTML 属性转义：只防 XSS 到属性解析这一步，不防 JS 字符串
    const attr = (s) => String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;');

    // HTML 文本转义：内嵌到 <div>${...}</div> 时用
    const esc = (s) => String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

    // JS 字符串字面量转义：onclick="fn('${...}', ...)" 时用
    // 关键：HTML 属性引号是 "，所以这里不必再防 "，只需要防 ' 和 \
    const js = (s) => String(s ?? '')
        .replace(/\\/g, '\\\\')
        .replace(/'/g, "\\'")
        .replace(/\r/g, '\\r')
        .replace(/\n/g, '\\n');

    const norm = (v) => v == null || v === '' ? '—' : String(v);
    const fmtSize = (b) => {
        b = Number(b) || 0;
        if (b < 1024) return b + ' B';
        if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
        if (b < 1024 * 1024 * 1024) return (b / 1024 / 1024).toFixed(1) + ' MB';
        return (b / 1024 / 1024 / 1024).toFixed(2) + ' GB';
    };
    const fmtTime = (mtime) => {
        if (!mtime) return '';
        const d = new Date(Number(mtime) * 1000);
        if (isNaN(d.getTime())) return '';
        const p = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
    };
    const debounce = (fn, ms) => {
        let t = null;
        return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
    };

    // ============================================================
    // Section 1 · 参数提取 —— 把 replay json 侧车解析成 UI 字段
    // ============================================================

    function extractInfo(item) {
        const gi = item.gen_info;
        if (gi && Object.keys(gi).length > 0) return normalizeInfo(gi);
        const r = item.replay;
        if (!r) return {};
        const p = r.payload || {};
        const loraPaths = p.loraPaths || (p.loraPath ? [p.loraPath] : []);
        const loraStrengths = p.loraStrengths || (p.loraStrength ? [p.loraStrength] : []);
        const loras = loraPaths.map((path, i) => {
            const name = path ? path.split(/[/\\]/).pop().replace(/\.[^.]+$/, '') : '?';
            const s = loraStrengths[i] || 1.0;
            return { name, strength: s, path: path || '' };
        });
        const segs = Array.isArray(p.segments) ? p.segments : [];
        const segDur = segs.reduce((s, x) => s + (x.duration || 0), 0);
        const dur = p.duration
            ? p.duration + 's'
            : segDur
                ? segDur + 's'
                : p.num_frames
                    ? (p.num_frames / (p.fps || 24)).toFixed(1) + 's'
                    : '';
        return normalizeInfo({
            prompt: p.prompt || segs.map(x => x.prompt).filter(Boolean).join('; '),
            seed: p.seed,
            width: p.width || p.image_width || p.customWidth,
            height: p.height || p.image_height || p.customHeight,
            fps: p.fps,
            duration: dur,
            steps: p.num_inference_steps || p.steps,
            cfg: p.guidance_scale || p.cfg,
            loras: loras.map(l => l.strength !== 1.0 ? `${l.name}(${l.strength})` : l.name),
            lora_details: loras,
        });
    }

    function normalizeInfo(o) {
        return {
            prompt: o.prompt || '',
            seed: o.seed || '',
            width: o.width || '',
            height: o.height || '',
            fps: o.fps || '',
            duration: o.duration || '',
            steps: o.steps || '',
            cfg: o.cfg || '',
            aspect_ratio: o.aspect_ratio || '',
            camera_motion: o.camera_motion || '',
            gen_method: o.gen_method || '',
            loras: Array.isArray(o.loras) ? o.loras : [],
            lora_details: Array.isArray(o.lora_details) ? o.lora_details : [],
            elapsed: o.elapsed || '',
            // 放大相关字段全量透传
            upscale_scale: o.upscale_scale || '',
            upscale_actual_scale: o.upscale_actual_scale || '',
            upscale_model: o.upscale_model || '',
            upscale_engine: o.upscale_engine || '',
            upscale_resize_mode: o.upscale_resize_mode || '',
            upscale_original_size: o.upscale_original_size || '',
            upscale_output_size: o.upscale_output_size || '',
            upscale_mode: o.upscale_mode || '',
            upscale_keep_ratio: !!o.upscale_keep_ratio,
            upscale_frames: o.upscale_frames || '',
            upscale_elapsed: o.upscale_elapsed || '',
            upscale_original_fps: o.upscale_original_fps || '',
            upscale_output_fps: o.upscale_output_fps || '',
            upscale_duration: o.upscale_duration || '',
        };
    }

    // ============================================================
    // Section 2 · AssetManager 类 —— 主控制器
    // ============================================================

    class AssetManager {
        constructor(opts) {
            this.BASE = opts.BASE;
            this.container = opts.container;
            this.wrapper = opts.wrapper;
            this.layoutToggle = opts.layoutToggle;
            this.layout = localStorage.getItem('assetLayout') || 'list';
            this.isGeneratingFlag = () => opts.isGeneratingFlag();

            // 状态
            this.items = new Map();        // key -> item
            this.cards = new Map();        // key -> DOM node
            this.fingerprint = '';
            this.loading = false;
            this.activeKey = null;
            this.thumbToken = 0;           // 缩略图懒加载 token
            this.autoTimer = null;

            // 视频预下载
            this.prefActive = new Map();   // fname -> AbortController
            this.prefDone = new Set();
            this.prefQueue = [];
            this.PREF_MAX = 2;

            // cache buster
            // ★ 2026-06-09: 与 index.js 的 _fileBusterMap 共享(window._assetBuster),
            //   避免列表侧和播放侧各自一份 Map 导致文件覆盖重生成时 cache 不一致
            //   注意: index.js 在 asset-manager.js 之后加载,start() 时才能拿到 window._assetBuster
            this.buster = new Map();

            // 跨调用方暴露
            this.installGlobals();
        }

        // ------------------ 全局暴露（兼容旧代码 + 内部调用）------------------

        installGlobals() {
            window.Assets = this;
            // 兼容旧 onclick="fetchHistory(...)" / "setHistoryLayout(...)"
            window.fetchHistory = (...a) => this.fetch(a[0] === true);
            window.setHistoryLayout = (layout) => this.setLayout(layout);
            window.switchLibTab = (tab) => this.switchTab(tab);
            window.__cancelAllVideoPrefetch = () => this.cancelPrefetch();
            window.__resetVideoPrefetchCache = () => this.resetPrefetchCache();
            // ★ 2026-06-09: __invalidateAllFileBusters 必须同时清空本类的 buster
            //   和 index.js 的 _fileBusterMap(displayOutput 用),否则文件被覆盖重生成时
            //   列表侧用新 buster,播放侧仍用旧 buster,导致"列表里看着对、点开还是旧内容"
            //   用箭头函数读 this.buster 的当前值,start() 中切换 Map 也能跟上
            window.__invalidateAllFileBusters = () => {
                this.buster.clear();
                if (window._assetBuster && window._assetBuster !== this.buster) {
                    window._assetBuster.clear();
                }
            };
        }

        // ------------------ URL 构造（统一出口）------------------

        getOutputDir() {
            const el = document.getElementById('global-out-dir');
            return el ? el.value.replace(/\\/g, '/').replace(/\/$/, '') : '';
        }

        // kind: 'media' | 'thumb' | 'alt'
        makeUrl(item, kind = 'media') {
            if (!item || !item.filename) return '';
            const dir = this.getOutputDir();
            const fname = item.filename;
            const fullPath = item.fullpath || (dir ? `${dir}/${fname}` : fname);
            const fileKey = `${item.type}::${fullPath}`;

            if (kind === 'alt' && dir) {
                // 备用路径：直走默认 outputs 路由（自定义目录才会用得到）
                return `${this.BASE}/outputs/${encodeURIComponent(fname)}?v=${this.bus(fileKey)}`;
            }
            if (item.type === 'video' && kind === 'thumb' && item.mtime) {
                return `${this.BASE}/api/system/video-thumbnail?path=${encodeURIComponent(fullPath)}&mtime=${encodeURIComponent(item.mtime)}&size=${encodeURIComponent(item.size || '')}`;
            }
            return `${this.BASE}/api/system/file?path=${encodeURIComponent(fullPath)}&v=${this.bus(fileKey)}`;
        }

        bus(fileKey) {
            // per-file 稳定 buster:同文件复用,新生成时才换
            let v = this.buster.get(fileKey);
            if (v == null) {
                v = Date.now();
                this.buster.set(fileKey, v);
            }
            return v;
        }

        invalidateBuster(fileKey) {
            this.buster.delete(fileKey);
        }

        // ------------------ 渲染（单一 HTML）------------------

        renderCard(item) {
            const key = `${item.type}|${item.filename}`;
            const info = extractInfo(item);
            const isUpscale = info.gen_method === '高清放大';

            // 缩略图 URL：视频走缩略图端点，图片直接用原图
            const thumbSrc = (item.type === 'video' && item.mtime)
                ? this.makeUrl(item, 'thumb')
                : this.makeUrl(item, 'media');

            const dataAttrs = [
                `data-asset-key="${attr(key)}"`,
                `data-history-key="${attr(key)}"`,       // ★ 兼容既有 click handler 兼容层
                `data-type="${attr(item.type)}"`,
                `data-filename="${attr(item.filename)}"`,
                `data-mtime="${attr(String(item.mtime || ''))}"`,
                `data-size="${attr(String(item.size || ''))}"`,
                `data-replay-id="${attr(item.replay_available && item.replay ? this.registerReplay(item.replay) : '')}"`,
            ].join(' ');

            // ★ 复用既有 CSS 类名(避免 CSS 翻倍)
            //  - .history-card / .grid-thumb-wrap / .history-thumb-media
            //  - .history-type-badge / .history-delete-btn / .history-save-btn
            //  - .grid-info-bar / .ptag / .ptag-method / .ptag-lora 等
            //  - .list-info / .list-param-line / .lpl-key / .lpl-val
            const typeBadgeText = item.type === 'video' ? '🎬 VID' : item.type === 'audio' ? '♪ AUD' : '🎨 IMG';

            // 媒体区
            let mediaInner;
            if (item.type === 'audio') {
                mediaInner = `<div class="history-audio-thumb"><div class="audio-icon">♪</div><div class="audio-name">${esc(item.filename)}</div></div>`;
            } else {
                mediaInner = `<img class="history-thumb-media lazy-load" data-src="${attr(thumbSrc)}" alt="" />`;
            }

            // grid 模式标签条 + list 模式参数行
            const tagBar = this.renderGridTags(info, isUpscale, item);
            const listInfo = this.renderListInfo(item, info, isUpscale);

            return `
<article class="history-card asset-card" ${dataAttrs} data-observed="0" draggable="true">
    <div class="grid-thumb-wrap">
        <div class="history-type-badge">${typeBadgeText}</div>
        <button class="history-delete-btn" data-act="del" title="删除">✕</button>
        <button class="history-save-btn" data-act="save" title="下载">⬇</button>
        ${mediaInner}
    </div>
    <div class="grid-info-bar">${tagBar}</div>
    <div class="list-info">${listInfo}</div>
</article>`;
        }

        // ─────────────────────────────────────────────────────────
        // grid 模式:缩略图下方一条紧凑标签条(节省空间,一眼扫到关键参数)
        // 复用既有 .ptag / .ptag-method / .ptag-lora / .ptag-elapsed
        // ─────────────────────────────────────────────────────────
        renderGridTags(info, isUpscale, item) {
            const out = [];
            if (info.gen_method) out.push(this.tag('method', info.gen_method));
            if (isUpscale) {
                if (info.upscale_mode) out.push(this.tag(null, info.upscale_mode));
                const scale = info.upscale_actual_scale || info.upscale_scale;
                if (scale) out.push(this.tag(null, scale + 'x'));
                if (info.upscale_original_size && info.upscale_output_size) {
                    out.push(this.tag(null, `${info.upscale_original_size}→${info.upscale_output_size}`));
                }
                if (info.upscale_model) out.push(this.tag(null, info.upscale_model));
                if (info.upscale_original_fps) {
                    out.push(this.tag(null, info.upscale_output_fps && info.upscale_output_fps !== info.upscale_original_fps
                        ? `${info.upscale_original_fps}→${info.upscale_output_fps}fps`
                        : `${info.upscale_original_fps}fps`));
                }
                if (info.upscale_duration) out.push(this.tag(null, this.fmtDur(info.upscale_duration)));
                if (info.upscale_frames) out.push(this.tag(null, info.upscale_frames + '帧'));
                if (info.upscale_elapsed) out.push(this.tag(null, info.upscale_elapsed + 's'));
            } else {
                if (info.width && info.height) out.push(this.tag(null, `${info.width}×${info.height}`));
                if (info.fps) out.push(this.tag(null, info.fps + 'fps'));
                if (info.duration) out.push(this.tag(null, info.duration));
                if (info.seed) out.push(this.tag(null, 'Seed:' + info.seed));
            }
            if (info.loras && info.loras.length) {
                out.push(this.tag('lora', info.loras.map(l => esc(l)).join(', ')));
            }
            if (info.elapsed) out.push(this.tag('elapsed', '⏱' + info.elapsed));
            const t = fmtTime(item.mtime);
            if (t) out.push(this.tag('date', '📅' + t));
            return out.join('');
        }

        // ─────────────────────────────────────────────────────────
        // list 模式:右侧 .list-info 区,所有参数完整列出
        // 复用既有 .list-param-line / .lpl-key / .lpl-val
        //          .info-prompt / .info-meta / .info-filename
        //          .meta-tag / .meta-method / .meta-lora / .meta-elapsed
        // ─────────────────────────────────────────────────────────
        renderListInfo(item, info, isUpscale) {
            const prompt = info.prompt
                ? `<div class="info-prompt" title="${attr(info.prompt)}">${esc(info.prompt.slice(0, 200))}${info.prompt.length > 200 ? '…' : ''}</div>`
                : '';
            const res = (info.width && info.height) ? `${info.width}×${info.height}` : '';
            const dateStr = fmtTime(item.mtime);

            // 第一行:方式 / 分辨率 / 帧率 / 时长 / 种子 / 日期
            const line1 = this.kvRow([
                ['方式', info.gen_method],
                ['分辨率', res],
                isUpscale ? null : ['帧率', info.fps ? info.fps + 'fps' : ''],
                isUpscale ? null : ['时长', info.duration],
                isUpscale ? null : ['种子', info.seed ? 'Seed:' + info.seed : ''],
                ['日期', dateStr],
            ]);

            // 第二行:放大相关 / 普通参数
            let line2;
            if (isUpscale) {
                line2 = this.kvRow([
                    ['模式', info.upscale_mode || (info.upscale_keep_ratio ? '原始比例' : info.upscale_output_size ? '目标分辨率' : '按倍数')],
                    ['放大', (info.upscale_actual_scale || info.upscale_scale) ? (info.upscale_actual_scale || info.upscale_scale) + 'x' : ''],
                    ['模型', info.upscale_model],
                    ['原始', info.upscale_original_size],
                    ['输出', info.upscale_output_size || res],
                    ['帧率', info.upscale_original_fps ? (info.upscale_output_fps && info.upscale_output_fps !== info.upscale_original_fps
                        ? `${info.upscale_original_fps}→${info.upscale_output_fps}fps`
                        : `${info.upscale_original_fps}fps`) : ''],
                    ['时长', info.upscale_duration ? this.fmtDur(info.upscale_duration) : info.duration],
                    ['帧数', info.upscale_frames],
                    ['缩放', info.upscale_resize_mode],
                    ['耗时', info.upscale_elapsed ? info.upscale_elapsed + 's' : info.elapsed],
                    ['大小', fmtSize(item.size)],
                    ['文件名', item.filename],
                ]);
            } else {
                line2 = this.kvRow([
                    ['步数', info.steps ? info.steps + '步' : ''],
                    ['CFG', info.cfg ? 'CFG' + info.cfg : ''],
                    ['运镜', info.camera_motion],
                    ['LoRA', info.loras && info.loras.length ? this.loraCell(info) : ''],
                    ['耗时', info.elapsed],
                    ['大小', fmtSize(item.size)],
                    ['文件名', item.filename],
                ]);
            }

            // 补充标签条(方法/lora/elapsed)给 list 模式下方作补充显示
            const meta = [];
            if (info.gen_method) meta.push(`<span class="meta-tag meta-method">${esc(info.gen_method)}</span>`);
            if (info.loras && info.loras.length) {
                meta.push(`<span class="meta-tag meta-lora" title="${attr(this.loraTitle(info))}">${esc(info.loras.join(', '))}</span>`);
            }
            if (info.camera_motion) meta.push(`<span class="meta-tag meta-motion">${esc(info.camera_motion)}</span>`);
            if (info.elapsed) meta.push(`<span class="meta-tag meta-elapsed">⏱${esc(info.elapsed)}</span>`);

            return [
                prompt,
                line1,
                line2,
                meta.length ? `<div class="info-meta">${meta.join('')}</div>` : '',
                `<div class="info-filename" title="${attr(item.filename)}">${esc(item.filename)}</div>`,
            ].join('');
        }

        kvRow(pairs) {
            const cells = pairs
                .filter(p => p && p[1])
                .map(([k, v]) => `<span class="lpl-key">${esc(k)}</span><span class="lpl-val">${esc(v)}</span>`)
                .join('');
            return cells ? `<div class="list-param-line">${cells}</div>` : '';
        }

        loraCell(info) {
            return esc(info.loras.join(', '));
        }

        loraTitle(info) {
            if (info.lora_details && info.lora_details.length) {
                return info.lora_details.map(d => `${d.name}${d.strength !== 1.0 ? `(${d.strength})` : ''}`).join('\n');
            }
            return info.loras.join(', ');
        }

        tag(cls, text) {
            if (!text) return '';
            return `<span class="ptag${cls ? ' ptag-' + cls : ''}">${esc(text)}</span>`;
        }

        fmtDur(s) {
            s = Number(s) || 0;
            if (s >= 60) return Math.floor(s / 60) + 'm' + Math.round(s % 60) + 's';
            return s + 's';
        }

        registerReplay(replay) {
            // 委托给 index.js 中已存在的 storeReplayRecord
            if (typeof window.storeReplayRecord === 'function') {
                return window.storeReplayRecord(replay);
            }
            // 兜底：内联实现
            if (!window.__replayStore) window.__replayStore = new Map();
            if (!window.__replaySeq) window.__replaySeq = 0;
            const id = 'replay-' + (++window.__replaySeq);
            window.__replayStore.set(id, replay);
            return id;
        }

        // ------------------ 数据拉取 + 增量渲染 ------------------

        async fetch(force = false) {
            if (this.loading && !force) return;
            this.loading = true;
            try {
                const res = await fetch(`${this.BASE}/api/system/history?page=1&limit=240`);
                if (!res.ok) return;
                const data = await res.json();
                this.buster.clear();        // 文件可能新增/覆盖
                this.resetPrefetchCache();

                const items = (data.history || []).filter(this.isValid);
                const fp = items.length === 0
                    ? '__empty__'
                    : items.map(h => `${h.type}|${h.filename}|${h.size || 0}|${h.mtime || 0}`).join('\0');
                if (fp === this.fingerprint) return;
                this.fingerprint = fp;

                if (items.length === 0) {
                    this.container.innerHTML = '';
                    this.items.clear();
                    this.cards.clear();
                    return;
                }

                this.applyDelta(items);
                console.log('[AssetManager] rendered, cards=' + this.cards.size + ', items=' + this.items.size);
            } catch (e) {
                console.error('[Assets] fetch failed:', e);
            } finally {
                this.loading = false;
            }
        }

        isValid = (item) => {
            if (!item || !item.filename) return false;
            const n = String(item.filename);
            if (n.startsWith('_') || n.toLowerCase().startsWith('tmp')) return false;
            const size = Number(item.size || 0);
            if (item.type === 'video' && size > 0 && size < 4096) return false;
            return true;
        };

        applyDelta(items) {
            const freshKeys = new Set();
            const toAdd = [];
            const toUpdate = [];

            items.forEach((item) => {
                const key = `${item.type}|${item.filename}`;
                freshKeys.add(key);
                const old = this.items.get(key);
                if (!old) {
                    toAdd.push(item);
                } else if (old.mtime !== item.mtime || old.size !== item.size) {
                    toUpdate.push(item);   // 文件被覆盖重生成
                }
                this.items.set(key, item);
            });

            // 删除已不存在的
            for (const key of Array.from(this.items.keys())) {
                if (!freshKeys.has(key)) {
                    this.items.delete(key);
                    const node = this.cards.get(key);
                    if (node) node.remove();
                    this.cards.delete(key);
                }
            }

            // 全量 vs 增量
            const needFull = this.cards.size === 0;
            if (needFull) {
                const frag = document.createDocumentFragment();
                const tmp = document.createElement('div');
                tmp.innerHTML = items.map((it) => this.renderCard(it)).join('');
                while (tmp.firstChild) {
                    const node = tmp.firstChild;
                    this.cards.set(node.dataset.assetKey, node);
                    this.bindCard(node);
                    this.observe(node);
                    frag.appendChild(node);
                }
                this.container.innerHTML = '';
                this.container.appendChild(frag);
                this.requestThumbLoad();
            } else {
                // 新增
                if (toAdd.length) {
                    const tmp = document.createElement('div');
                    tmp.innerHTML = toAdd.map((it) => this.renderCard(it)).join('');
                    while (tmp.firstChild) {
                        const node = tmp.firstChild;
                        this.cards.set(node.dataset.assetKey, node);
                        this.bindCard(node);
                        this.observe(node);
                        this.container.appendChild(node);
                    }
                }
                // 更新（被覆盖）
                toUpdate.forEach((item) => {
                    const key = `${item.type}|${item.filename}`;
                    const old = this.cards.get(key);
                    if (!old) return;
                    const tmp = document.createElement('div');
                    tmp.innerHTML = this.renderCard(item).trim();
                    const fresh = tmp.firstElementChild;
                    if (!fresh) return;
                    // 取消旧缩略图/预下载
                    this.cancelMediaOnNode(old);
                    this.cards.set(key, fresh);
                    this.bindCard(fresh);
                    this.observe(fresh);
                    old.replaceWith(fresh);
                });
                if (toAdd.length || toUpdate.length) this.requestThumbLoad();
            }
        }

        cancelMediaOnNode(card) {
            try {
                const img = card.querySelector('img.history-thumb-media');
                if (img) {
                    img.classList.remove('history-thumb-ready', 'history-thumb-loading');
                    img.classList.add('lazy-load');
                    img.removeAttribute('src');
                }
                const vid = card.querySelector('video.history-thumb-media');
                if (vid) { try { vid.pause(); vid.removeAttribute('src'); vid.load(); } catch (_) {} }
            } catch (_) {}
        }

        // ------------------ 事件绑定（统一在 renderCard 后调用）------------------

        bindCard(node) {
            // 点击/键盘激活
            const activate = (e) => {
                e?.stopPropagation?.();
                const key = node.dataset.assetKey;
                if (!key) return;
                this.setActive(key);
                this.cancelPrefetch();
                this.thumbToken++;       // 抢占代次，旧缩略图停止
                this.play(this.items.get(key));
            };
            node.addEventListener('click', (e) => {
                const act = e.target.closest('[data-act]')?.dataset.act;
                if (act === 'del') return this.confirmDelete(node);
                if (act === 'save') return this.download(node);
                activate(e);
            });
            node.tabIndex = 0;
            node.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(e); }
            });
            // 2026-06-10 修复: 启用从历史资产拖拽到上传区(高清放大/视频/图片/音频 等入口)
            // 通过自定义 MIME 传递 filename + type,接收端在 index.js 的 drop handler 中通过 getData() 还原
            node.addEventListener('dragstart', (e) => {
                const key = node.dataset.assetKey;
                if (!key) return;
                const item = this.items.get(key);
                if (!item) return;
                const payload = JSON.stringify({
                    filename: item.filename,
                    type: item.type,
                    key: key,
                });
                try {
                    e.dataTransfer.setData('application/x-yunji-asset', payload);
                    e.dataTransfer.setData('text/plain', item.filename || '');
                    e.dataTransfer.effectAllowed = 'copy';
                } catch (_) {}
                // 自定义拖拽预览图(使用卡片缩略图)
                try {
                    const img = node.querySelector('img.history-thumb-media, .history-audio-thumb');
                    if (img && e.dataTransfer.setDragImage) {
                        e.dataTransfer.setDragImage(img, 30, 30);
                    }
                } catch (_) {}
            });
        }

        setActive(key) {
            if (this.activeKey && this.cards.get(this.activeKey)) {
                this.cards.get(this.activeKey).classList.remove('is-active');
            }
            const node = this.cards.get(key);
            if (node) {
                node.classList.add('is-active');
                this.activeKey = key;
            }
        }

        // ------------------ 删除 / 下载 ------------------

        async confirmDelete(node) {
            const item = this.items.get(node.dataset.assetKey);
            if (!item) return;
            if (!confirm(`确定要删除 "${item.filename}" 吗？`)) return;
            try {
                const res = await fetch(`${this.BASE}/api/system/delete-file`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filename: item.filename, type: item.type }),
                });
                if (!res.ok) throw new Error('delete failed');
                this.items.delete(node.dataset.assetKey);
                this.cards.delete(node.dataset.assetKey);
                if (this.activeKey === node.dataset.assetKey) this.activeKey = null;
                node.remove();
            } catch (e) {
                alert('删除失败: ' + e.message);
            }
        }

        download(node) {
            const item = this.items.get(node.dataset.assetKey);
            if (!item) return;
            const url = this.makeUrl(item, 'media');
            const a = document.createElement('a');
            a.href = url;
            a.download = item.filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
        }

        // ------------------ 播放（统一入口）------------------

        play(item) {
            if (!item) return;
            const card = this.cards.get(`${item.type}|${item.filename}`);
            const replayId = card?.dataset.replayId || '';
            // 委托给 index.js 中的 displayOutput（displayOutput 内部会自己再算一次 URL，
            // cache buster Map 在 AssetManager.buster 共享，重生成时会被清掉）
            if (typeof window.displayOutput === 'function') {
                window.displayOutput(item.filename, item.type, { replayId });
            }
        }

        // ------------------ 懒加载：缩略图 ------------------

        observe(node) {
            if (this.observing) this.observing.observe(node);
        }

        initObserver() {
            if (this.observing) return;
            if (!('IntersectionObserver' in window)) {
                this.observing = null;
                return;
            }
            // 容器级 IO：缩略图 + 预下载
            this.observing = new IntersectionObserver((entries) => {
                for (const e of entries) {
                    if (!e.isIntersecting) continue;
                    const key = e.target.dataset.assetKey;
                    if (!key) continue;
                    this.requestThumbLoad(0);
                    const item = this.items.get(key);
                    if (item && item.type === 'video' && !this.isGeneratingFlag()) {
                        this.prefetchVideo(item);
                    }
                    this.observing.unobserve(e.target);
                }
            }, { root: this.wrapper, rootMargin: '300px 0px', threshold: 0.01 });
        }

        requestThumbLoad(delay = 0) {
            // 取消旧代次
            const myToken = ++this.thumbToken;
            const run = async () => {
                if (myToken !== this.thumbToken) return;
                const batch = this.collectLazyThumbs(5);
                if (batch.length === 0) return;
                await Promise.all(batch.map((m) => this.loadThumb(m, myToken)));
                if (myToken === this.thumbToken) {
                    setTimeout(run, 80);
                }
            };
            delay > 0 ? setTimeout(run, delay) : (window.requestAnimationFrame ? requestAnimationFrame(run) : run());
        }

        collectLazyThumbs(limit) {
            const now = Date.now();
            return Array.from(this.container.querySelectorAll('img.history-thumb-media.lazy-load'))
                .filter((c) => c.dataset.src && Number(c.dataset.retryAfter || 0) <= now)
                .slice(0, limit);
        }

        async loadThumb(media, token) {
            if (token !== this.thumbToken) return false;
            const src = media.dataset.src;
            media.classList.remove('lazy-load');
            media.classList.add('history-thumb-loading');
            const reveal = () => {
                media.classList.remove('history-thumb-loading');
                media.classList.add('history-thumb-ready');
            };
            try {
                // 图片
                if (media.tagName === 'IMG') {
                    await new Promise((resolve) => {
                        media.onload = resolve;
                        media.onerror = resolve;
                        media.src = src;
                    });
                    if (token !== this.thumbToken) return false;
                    if (media.naturalWidth > 0) {
                        reveal();
                        return true;
                    }
                    throw new Error('naturalWidth=0');
                }
            } catch (e) {
                // 失败重试一次
                media.classList.remove('history-thumb-loading');
                media.classList.add('lazy-load');
                media.dataset.retryAfter = String(Date.now() + 1500);
                this.requestThumbLoad(1600);
            }
            return false;
        }

        // ------------------ 懒加载：视频预下载队列 ------------------

        prefetchVideo(item) {
            const fname = item.filename;
            if (!fname) return;
            if (this.prefDone.has(fname) || this.prefActive.has(fname)) return;
            const url = this.makeUrl(item, 'media');
            const task = { fname, url };
            if (this.prefActive.size < this.PREF_MAX) {
                this.runPrefetch(task);
            } else if (!this.prefQueue.some(t => t.fname === fname)) {
                this.prefQueue.push(task);
            }
        }

        runPrefetch(task) {
            const ctrl = new AbortController();
            this.prefActive.set(task.fname, ctrl);
            const opts = {
                method: 'GET',
                signal: ctrl.signal,
                cache: 'force-cache',
                credentials: 'same-origin',
            };
            try { opts.priority = 'low'; } catch (_) {}
            fetch(task.url, opts)
                .then((res) => {
                    if (res?.body?.cancel) { try { res.body.cancel(); } catch (_) {} }
                })
                .catch(() => { /* 静默失败 */ })
                .finally(() => {
                    this.prefActive.delete(task.fname);
                    this.prefDone.add(task.fname);
                    this.drainPrefetchQueue();
                });
        }

        drainPrefetchQueue() {
            while (this.prefActive.size < this.PREF_MAX && this.prefQueue.length) {
                this.runPrefetch(this.prefQueue.shift());
            }
        }

        cancelPrefetch() {
            for (const ctrl of this.prefActive.values()) {
                try { ctrl.abort(); } catch (_) {}
            }
            this.prefActive.clear();
            this.prefQueue.length = 0;
        }

        resetPrefetchCache() {
            this.cancelPrefetch();
            this.prefDone.clear();
        }

        // ------------------ 布局切换 ------------------

        setLayout(layout) {
            if (layout !== 'grid' && layout !== 'list') return;
            this.layout = layout;
            localStorage.setItem('assetLayout', layout);
            this.container.dataset.layout = layout;
            this.container.classList.toggle('layout-list', layout === 'list');
            this.container.classList.toggle('layout-grid', layout === 'grid');
            this.layoutToggle?.querySelectorAll('.layout-btn').forEach((b) => {
                b.classList.toggle('active', b.dataset.layout === layout);
            });
            this.requestThumbLoad(0);
        }

        // ------------------ Tab 切换 ------------------

        switchTab(tab) {
            const logC = document.getElementById('log-container');
            if (logC) logC.style.display = tab === 'log' ? 'flex' : 'none';
            if (this.wrapper) this.wrapper.style.display = tab === 'history' ? 'block' : 'none';
            const tabLog = document.getElementById('tab-log');
            if (tabLog) {
                tabLog.style.color = tab === 'log' ? 'var(--accent)' : 'var(--text-dim)';
                tabLog.style.borderBottomColor = tab === 'log' ? 'var(--accent)' : 'transparent';
            }
            const tabHistory = document.getElementById('tab-history');
            if (tabHistory) {
                tabHistory.style.color = tab === 'history' ? 'var(--accent)' : 'var(--text-dim)';
                tabHistory.style.borderBottomColor = tab === 'history' ? 'var(--accent)' : 'transparent';
            }
            if (tab === 'history') this.fetch(true);
        }

        // ------------------ 生命周期 ------------------

        start() {
            // ★ 2026-06-09: 优先用 index.js 的 _fileBusterMap,确保 URL 一致
            if (window._assetBuster && window._assetBuster !== this.buster) {
                this.buster = window._assetBuster;
            }
            this.initObserver();
            this.setLayout(this.layout);
            this.bindLayoutButtons();
            this.fetch(true);
            this.startAutoRefresh();
        }

        bindLayoutButtons() {
            if (!this.layoutToggle) return;
            this.layoutToggle.querySelectorAll('.layout-btn').forEach((b) => {
                b.onclick = (e) => {
                    e.preventDefault();
                    this.setLayout(b.dataset.layout);
                };
            });
        }

        startAutoRefresh() {
            this.stopAutoRefresh();
            this.autoTimer = setInterval(() => {
                if (this.isGeneratingFlag()) return;
                if (this.wrapper && this.wrapper.offsetParent === null) return;   // tab 隐藏
                this.fetch(false);
            }, 15000);
        }

        stopAutoRefresh() {
            if (this.autoTimer) { clearInterval(this.autoTimer); this.autoTimer = null; }
        }
    }

    // ============================================================
    // Section 3 · 暴露 + 自启
    // ============================================================

    // 顶层立即挂载 lazy stub(不等 DOMContentLoaded / 不等 bootstrap)
    // — 让 index.js 在 DOMContentLoaded 中调 switchLibTab() 不会 ReferenceError
    // — bootstrap() 跑完后,installGlobals() 会用真实实现覆盖这些 stub
    (function bindLazyStubs() {
        const lazyCall = (methodName) => function (...args) {
            const a = window.Assets;
            if (a && typeof a[methodName] === 'function') {
                return a[methodName](...args);
            }
            // 还没就绪,等下一个 tick 再试
            const tryRun = () => {
                const a2 = window.Assets;
                if (a2 && typeof a2[methodName] === 'function') {
                    return a2[methodName](...args);
                }
                return null;
            };
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', tryRun, { once: true });
            } else {
                setTimeout(tryRun, 0);
            }
        };
        if (!window.switchLibTab)     window.switchLibTab     = lazyCall('switchTab');
        if (!window.fetchHistory)     window.fetchHistory     = lazyCall('fetch');
        if (!window.setHistoryLayout) window.setHistoryLayout = lazyCall('setLayout');
    })();

    function bootstrap() {
        const container = document.getElementById('history-container');
        const wrapper = document.getElementById('history-wrapper');
        const layoutToggle = document.getElementById('history-layout-toggle');
        if (!container || !wrapper) {
            // 还没加载好，DOMContentLoaded 后再试
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', bootstrap);
            } else {
                setTimeout(bootstrap, 200);
            }
            return;
        }
        const BASE = (typeof window.BASE === 'string') ? window.BASE
            : `${location.protocol}//${location.hostname}:${location.port}`;
        window.BASE = BASE;
        const m = new AssetManager({
            BASE,
            container,
            wrapper,
            layoutToggle,
            isGeneratingFlag: () => !!window._isGeneratingFlag,
        });
        m.start();
        console.log('[AssetManager] started, container=' + (container ? 'OK' : 'MISSING') + ', wrapper=' + (wrapper ? 'OK' : 'MISSING'));
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bootstrap);
    } else {
        bootstrap();
    }
})();
