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

    const SETTINGS_KEY = 'yunji_ui_settings';
    const PERSIST_SELECTS = [
        'gpu-selector', 'model-checkpoint-select', 'vid-quality', 'vid-ratio',
        'vid-fps', 'vid-motion', 'motion-conditioning-type', 'img-res-preset',
        'batch-quality', 'batch-ratio', 'tts-mode'
    ];
    const PERSIST_INPUTS = [
        'vram-limit-input', 'lora-dir-input', 'seed-value',
        'vid-custom-w', 'vid-custom-h', 'vid-duration',
        'img-w', 'img-h', 'img-steps',
        'batch-custom-w', 'batch-custom-h',
        'tts-cfg', 'tts-steps', 'global-out-dir',
        'start-frame-path', 'end-frame-path', 'uploaded-audio-path',
        'motion-video-path', 'motion-image-path', 'batch-background-audio-path'
    ];
    const PERSIST_TEXTAREAS = ['prompt', 'tts-text'];
    const PERSIST_CHECKBOXES = ['vid-audio'];
    const PERSIST_RADIOS = [
        { name: 'seed-mode', values: ['random', 'fixed'] },
        { name: 'batch-workflow', values: ['single', 'segments'] }
    ];
    const PERSIST_RANGES = ['motion-strength'];

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
        s._uploadNames = {
            startFrame: pathFileName(s['start-frame-path'] || ''),
            endFrame: pathFileName(s['end-frame-path'] || ''),
            audio: pathFileName(s['uploaded-audio-path'] || ''),
            motionVideo: pathFileName(s['motion-video-path'] || ''),
            motionImage: pathFileName(s['motion-image-path'] || ''),
            batchAudio: pathFileName(s['batch-background-audio-path'] || '')
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
            if (placeholder) placeholder.style.display = 'none';
            if (statusDiv) statusDiv.style.display = 'block';
            if (nameEl) nameEl.textContent = names.motionVideo || pathFileName(motionVideoPath);
            if (clearOverlay) clearOverlay.style.display = 'flex';
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
            const loraModels = models.filter(m => m.tags && m.tags.includes('lora') && m.downloaded && m.local_path);
            lorasFromRegistry = loraModels.map(m => ({ name: m.filename || m.name, path: m.local_path }));
            const localDirs = data.local_dirs || [];
            for (const dir of localDirs) {
                for (const m of (dir.models || [])) {
                    if (m.model_type === 'lora') {
                        lorasFromRegistry.push({ name: m.name, path: m.path });
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
                    merged.push({ ...l, description: meta.description || '', trigger_words: meta.trigger_words || [], base_model: meta.base_model || '' });
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

    window.addLoraSelection = function(containerId) {
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
        availableLoras.forEach(lora => {
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
        removeBtn.onclick = () => { wrapper.remove(); };

        row1.appendChild(select);
        row1.appendChild(removeBtn);

        const loraInfoBox = document.createElement('div');
        loraInfoBox.className = 'lora-info-box';
        loraInfoBox.style.display = 'none';
        loraInfoBox.style.fontSize = '11px';
        loraInfoBox.style.color = 'var(--text-dim)';
        loraInfoBox.style.padding = '2px 4px';
        loraInfoBox.style.lineHeight = '1.4';
        loraInfoBox.style.maxHeight = '60px';
        loraInfoBox.style.overflow = 'hidden';

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
            let html = '';
            if (lora.description) html += `<div style="margin-bottom:2px">${escapeHtmlAttr(lora.description)}</div>`;
            if (lora.trigger_words && lora.trigger_words.length > 0) html += `<div>触发词: <span style="color:var(--accent)">${escapeHtmlAttr(lora.trigger_words.join(', '))}</span></div>`;
            if (lora.base_model) html += `<div>基模: ${escapeHtmlAttr(lora.base_model)}</div>`;
            loraInfoBox.innerHTML = html;
            loraInfoBox.style.display = html ? 'block' : 'none';
        }

        select.onchange = () => {
            strengthContainer.style.display = select.value ? 'flex' : 'none';
            updateLoraInfo();
        };

        container.appendChild(wrapper);
    };

    function _fillLoraSelect(select) {
        const currentVal = select.value;
        select.innerHTML = '<option value="">' + _t('noLora') + '</option>';
        availableLoras.forEach(lora => {
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
        select.value = currentVal;
    }

    function updateLoraDropdown() {
        const selects = document.querySelectorAll('#loras-container .lora-select');
        selects.forEach(select => {
            _fillLoraSelect(select);
            const infoBox = select.closest('.lora-entry')?.querySelector('.lora-info-box');
            if (infoBox && select.value) {
                const lora = availableLoras.find(l => l.path === select.value);
                if (lora && (lora.description || (lora.trigger_words && lora.trigger_words.length > 0) || lora.base_model)) {
                    let html = '';
                    if (lora.description) html += `<div style="margin-bottom:2px">${escapeHtmlAttr(lora.description)}</div>`;
                    if (lora.trigger_words && lora.trigger_words.length > 0) html += `<div>触发词: <span style="color:var(--accent)">${escapeHtmlAttr(lora.trigger_words.join(', '))}</span></div>`;
                    if (lora.base_model) html += `<div>基模: ${escapeHtmlAttr(lora.base_model)}</div>`;
                    infoBox.innerHTML = html;
                    infoBox.style.display = html ? 'block' : 'none';
                } else {
                    infoBox.style.display = 'none';
                    infoBox.innerHTML = '';
                }
            }
        });
        if (document.getElementById('loras-container') && document.getElementById('loras-container').children.length === 0) {
            window.addLoraSelection('loras-container');
        }
    }

    window.updateLoraStrength = function() {};

    function updateBatchLoraDropdown() {
        const selects = document.querySelectorAll('#batch-loras-container .lora-select');
        selects.forEach(select => {
            _fillLoraSelect(select);
            const infoBox = select.closest('.lora-entry')?.querySelector('.lora-info-box');
            if (infoBox && select.value) {
                const lora = availableLoras.find(l => l.path === select.value);
                if (lora && (lora.description || (lora.trigger_words && lora.trigger_words.length > 0) || lora.base_model)) {
                    let html = '';
                    if (lora.description) html += `<div style="margin-bottom:2px">${escapeHtmlAttr(lora.description)}</div>`;
                    if (lora.trigger_words && lora.trigger_words.length > 0) html += `<div>触发词: <span style="color:var(--accent)">${escapeHtmlAttr(lora.trigger_words.join(', '))}</span></div>`;
                    if (lora.base_model) html += `<div>基模: ${escapeHtmlAttr(lora.base_model)}</div>`;
                    infoBox.innerHTML = html;
                    infoBox.style.display = html ? 'block' : 'none';
                } else {
                    infoBox.style.display = 'none';
                    infoBox.innerHTML = '';
                }
            }
        });
        if (document.getElementById('batch-loras-container') && document.getElementById('batch-loras-container').children.length === 0) {
            window.addLoraSelection('batch-loras-container');
        }
    }
    
    window.updateBatchLoraStrength = function() {};

    // 页面加载时更新批量模式的下拉框
    function initBatchDropdowns() {
        updateBatchLoraDropdown();
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

    function updateResPreview() {
        const vidQuality = document.getElementById('vid-quality');
        if (!vidQuality) return;
        const q = vidQuality.value;
        const size = getGenerationSize('video');
        const note = size.source === 'ref-missing' ? ` · ${_t('ratioRefMissing')}` : '';
        const recEl = document.getElementById('vid-quality-rec');
        if (recEl && recEl.style.display !== 'none' && recEl.textContent) {
            const base = recEl.textContent.replace(/（\d+×\d+）.*$/, '');
            recEl.textContent = `${base}（${size.width}×${size.height}）${note}`;
        }
        return resLabelFromQuality(q);
    }

    function updateImgResPreview() {
        const imgW = document.getElementById('img-w');
        const imgH = document.getElementById('img-h');
        const imgResPreview = document.getElementById('img-res-preview');
        if (!imgW || !imgH || !imgResPreview) return;
        imgResPreview.innerText = `${imgW.value}×${imgH.value}`;
    }

    function updateBatchResPreview() {
        const batchQuality = document.getElementById('batch-quality');
        if (!batchQuality) return;
        const q = batchQuality.value;
        const size = getGenerationSize('batch');
        const note = size.source === 'ref-missing' ? ` · ${_t('ratioRefMissing')}` : '';
        const recEl = document.getElementById('batch-quality-rec');
        if (recEl && recEl.style.display !== 'none' && recEl.textContent) {
            const base = recEl.textContent.replace(/（\d+×\d+）.*$/, '');
            recEl.textContent = `${base}（${size.width}×${size.height}）${note}`;
        }
        return resLabelFromQuality(q);
    }

    // 批量模式 LoRA 强度切换
    function updateBatchLoraStrength() {
        const select = document.getElementById('batch-lora');
        const container = document.getElementById('batch-lora-strength-container');
        if (select && container) {
            container.style.display = select.value ? 'flex' : 'none';
        }
    }

    // 切换图片预设分辨率
    function applyImgPreset(val) {
        if (val === "custom") {
            document.getElementById('img-custom-res').style.display = 'flex';
        } else {
            const [w, h] = val.split('x');
            document.getElementById('img-w').value = w;
            document.getElementById('img-h').value = h;
            updateImgResPreview();
            // 隐藏自定义区域或保持显示供微调
            // document.getElementById('img-custom-res').style.display = 'none';
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

    window.handleMotionVideoUpload = async function(file) {
        if (!file) return;
        const label = _t('motionRefVideoName');
        try {
            const path = await uploadBase64File(file, label);
            document.getElementById('motion-video-path').value = path;
            document.getElementById('motion-video-placeholder').style.display = 'none';
            document.getElementById('motion-video-status').style.display = 'block';
            document.getElementById('motion-video-name').textContent = file.name;
            document.getElementById('clear-motion-video-overlay').style.display = 'flex';
            addLog(_fmt('motionUploadOk', { label, name: file.name }));
            scheduleSave();
        } catch (e) {
            addLog(_fmt('motionUploadFail', { label, message: e.message }));
        }
    };

    window.clearMotionVideo = function() {
        document.getElementById('motion-video-input').value = "";
        document.getElementById('motion-video-path').value = "";
        document.getElementById('motion-video-placeholder').style.display = 'block';
        document.getElementById('motion-video-status').style.display = 'none';
        document.getElementById('motion-video-name').textContent = "";
        document.getElementById('clear-motion-video-overlay').style.display = 'none';
        addLog(_t('motionClearRefVideo'));
        scheduleSave();
    };

    window.handleMotionImageUpload = async function(file) {
        if (!file) return;
        const preview = document.getElementById('motion-image-preview');
        const placeholder = document.getElementById('motion-image-placeholder');
        const clearOverlay = document.getElementById('clear-motion-image-overlay');
        const reader = new FileReader();
        reader.onload = (e) => {
            preview.src = e.target.result;
            preview.style.display = 'block';
            placeholder.style.display = 'none';
            clearOverlay.style.display = 'flex';
        };
        reader.readAsDataURL(file);
        const label = _t('motionTargetImageName');
        try {
            const path = await uploadBase64File(file, label);
            document.getElementById('motion-image-path').value = path;
            addLog(_fmt('motionUploadOk', { label, name: file.name }));
            scheduleSave();
        } catch (e) {
            addLog(_fmt('motionUploadFail', { label, message: e.message }));
        }
    };

    window.clearMotionImage = function() {
        document.getElementById('motion-image-input').value = "";
        document.getElementById('motion-image-path').value = "";
        document.getElementById('motion-image-preview').style.display = 'none';
        document.getElementById('motion-image-preview').src = "";
        document.getElementById('motion-image-placeholder').style.display = 'block';
        document.getElementById('clear-motion-image-overlay').style.display = 'none';
        addLog(_t('motionClearTargetImage'));
        scheduleSave();
    };

    // 初始化拖拽上传逻辑
    function initDragAndDrop() {
        const audioDropZone = document.getElementById('audio-drop-zone');
        const startFrameDropZone = document.getElementById('start-frame-drop-zone');
        const endFrameDropZone = document.getElementById('end-frame-drop-zone');
        const batchImagesDropZone = document.getElementById('batch-images-drop-zone');
        const motionVideoDropZone = document.getElementById('motion-video-drop-zone');
        const motionImageDropZone = document.getElementById('motion-image-drop-zone');
        const ttsRefDropZone = document.getElementById('tts-ref-drop');
        
        const zones = [audioDropZone, startFrameDropZone, endFrameDropZone, batchImagesDropZone, motionVideoDropZone, motionImageDropZone, ttsRefDropZone].filter(z => z);

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

        if (audioDropZone) audioDropZone.addEventListener('drop', (e) => {
            const file = e.dataTransfer.files[0];
            if (file && file.type.startsWith('audio/')) handleAudioUpload(file);
        }, false);

        if (startFrameDropZone) startFrameDropZone.addEventListener('drop', (e) => {
            const file = e.dataTransfer.files[0];
            if (file && file.type.startsWith('image/')) handleFrameUpload(file, 'start');
        }, false);

        if (endFrameDropZone) endFrameDropZone.addEventListener('drop', (e) => {
            const file = e.dataTransfer.files[0];
            if (file && file.type.startsWith('image/')) handleFrameUpload(file, 'end');
        }, false);

        // 批量图片拖拽上传
        if (batchImagesDropZone) {
            batchImagesDropZone.addEventListener('drop', (e) => {
                e.preventDefault();
                e.stopPropagation();
                batchImagesDropZone.classList.remove('dragover');
                const files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/'));
                if (files.length > 0) handleBatchImagesUpload(files);
            }, false);
        }

        if (motionVideoDropZone) {
            motionVideoDropZone.addEventListener('drop', (e) => {
                const file = e.dataTransfer.files[0];
                if (file && (file.type.startsWith('video/') || /\.(mp4|mov|webm|mkv|avi)$/i.test(file.name))) {
                    window.handleMotionVideoUpload(file);
                }
            }, false);
        }

        if (motionImageDropZone) {
            motionImageDropZone.addEventListener('drop', (e) => {
                const file = e.dataTransfer.files[0];
                if (file && file.type.startsWith('image/')) {
                    window.handleMotionImageUpload(file);
                }
            }, false);
        }

        if (ttsRefDropZone) {
            ttsRefDropZone.addEventListener('drop', (e) => {
                const file = e.dataTransfer.files[0];
                if (file && (file.type.startsWith('audio/') || file.name.endsWith('.wav') || file.name.endsWith('.mp3'))) {
                    handleTtsRefUpload(file);
                }
            }, false);
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
    let queuePollInterval = null;
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
            cancelled: _t('queueCancelled')
        };
        return mapping[task.status] || task.status || '未知';
    }

    function queueModeLabel(task) {
        const mode = task?.mode || 'task';
        const mapping = {
            video: _t('queueTaskTypeVideo'),
            motion: _t('queueTaskTypeMotion'),
            batch: _t('queueTaskTypeBatch'),
            image: _t('queueTaskTypeImage')
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
            const resultPath = task.result?.video_path || task.result?.image_paths?.[0] || '';
            const openBtn = resultPath
                ? `<button onclick='displayOutput(${JSON.stringify(resultPath)})' style="margin-top:4px;font-size:10px;padding:3px 8px;border-radius:999px;border:1px solid rgba(255,255,255,0.12);background:rgba(255,255,255,0.04);color:var(--text-main);cursor:pointer;">${_t('queueViewResult')}</button>`
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
            }
            pollQueueStatus();
        } catch (e) {
            addLog(`❌ ${_t('queueCancelFail')}: ${e.message}`);
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
                if (!pollInterval) startProgressPolling();
                _isGeneratingFlag = true;
            } else if (!data?.current) {
                if (pollInterval) stopProgressPolling();
                _isGeneratingFlag = false;
                const mainBtn = document.getElementById('mainBtn');
                if (mainBtn) mainBtn.disabled = false;
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
                } else if (task.status === 'error') {
                    console.error('[QUEUE] Task error:', task.id, task.error);
                    addLog(_fmt('queueFailLog', { label: task.label || task.id, error: task.error || 'unknown' }));
                } else if (task.status === 'cancelled') {
                    addLog(_fmt('queueCancelLog', { label: task.label || task.id }));
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
        } catch (e) { console.error('[QUEUE] pollQueueStatus error:', e); }
        finally {
            queuePollInFlight = false;
        }
    }

    function startQueuePolling() {
        if (queuePollInterval) return;
        queuePollInterval = setInterval(pollQueueStatus, 1500);
        pollQueueStatus();
    }

    // 系统状态轮询
    async function checkStatus() {
        if (statusPollInFlight) return;
        statusPollInFlight = true;
        try {
            const h = await fetch(`${BASE}/health`).then(r => r.json()).catch(() => ({status: "error"}));
            const g = await fetch(`${BASE}/api/gpu-info`).then(r => r.json()).catch(() => ({gpu_info: {}}));
            const p = await fetch(`${BASE}/api/generation/progress`).then(r => r.json()).catch(() => ({progress: 0}));
            const sysGpus = await fetch(`${BASE}/api/system/list-gpus`).then(r => r.json()).catch(() => ({gpus: []}));
            
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
        } catch(e) { const ss = document.getElementById('sys-status'); if (ss) ss.innerText = _t('sysOffline'); }
        finally { statusPollInFlight = false; }
    }
    try {
    setInterval(checkStatus, 3000);
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

    window.onUiLanguageChanged = function () {
        updateResPreview();
        updateBatchResPreview();
        updateImgResPreview();
        refreshPromptPlaceholder();
        if (typeof currentMode !== 'undefined' && currentMode === 'batch') {
            updateBatchSegments();
        }
        if (typeof currentMode !== 'undefined' && currentMode === 'tts') {
            checkTtsStatus();
        }
        updateLoraDropdown();
        updateBatchLoraDropdown();
        pollQueueStatus();
    };

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
        return (select?.value || localStorage.getItem(MODEL_CHECKPOINT_STORAGE_KEY) || '').trim();
    }

    window.saveSelectedModelCheckpoint = function() {
        const select = document.getElementById('model-checkpoint-select');
        const status = document.getElementById('model-checkpoint-status');
        const value = (select?.value || '').trim();
        if (value) {
            localStorage.setItem(MODEL_CHECKPOINT_STORAGE_KEY, value);
        } else {
            localStorage.removeItem(MODEL_CHECKPOINT_STORAGE_KEY);
        }
        if (status) {
            status.textContent = value ? _t('modelCheckpointSaved') : _t('modelCheckpointHint');
            status.style.color = value ? '#4caf50' : 'var(--text-dim)';
            if (value) setTimeout(() => {
                status.textContent = _t('modelCheckpointHint');
                status.style.color = 'var(--text-dim)';
            }, 2500);
        }
    };

    window.loadModelCheckpoints = async function() {
        const select = document.getElementById('model-checkpoint-select');
        const status = document.getElementById('model-checkpoint-status');
        if (!select) return;
        const saved = localStorage.getItem(MODEL_CHECKPOINT_STORAGE_KEY) || '';
        try {
            if (status) {
                status.textContent = '...';
                status.style.color = 'var(--text-dim)';
            }
            const res = await fetch(`${BASE}/api/models`);
            const data = await res.json();
            const models = (data.models || []).filter(isSwitchableDistilledCheckpoint);
            select.innerHTML = '';
            const defOpt = document.createElement('option');
            defOpt.value = '';
            defOpt.textContent = _t('modelCheckpointDefault');
            select.appendChild(defOpt);
            models.forEach((model) => {
                const opt = document.createElement('option');
                opt.value = model.path;
                opt.textContent = model.name;
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
    };

    let _coreModelsData = null;

    window.loadCoreModels = async function() {
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
                    dlBtn.onclick = function() { startCoreModelDownload(m.id, dlBtn); };
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

    let _modelRegistryData = null;

    window.loadModelRegistry = async function() {
        const list = document.getElementById('model-registry-list');
        const dirs = document.getElementById('model-registry-dirs');
        if (!list) return;
        list.innerHTML = `<div style="font-size:10px;color:var(--text-dim);">${_t('modelRegLoading') || 'Loading...'}</div>`;
        try {
            const res = await fetch(`${BASE}/api/models/registry`);
            const data = await res.json();
            _modelRegistryData = data.models || [];
            if (dirs) {
                let dirsHtml = `<div style="font-size:9px;color:var(--text-dim);line-height:1.5;">`;
                if (data.default_models_dir) {
                    dirsHtml += `<div style="margin-bottom:2px;"><b style="color:var(--text-main);">${_t('modelRegDefaultDir') || 'Default'}:</b> ${data.default_models_dir}</div>`;
                }
                const customDirs = data.custom_models_dirs || [];
                if (customDirs.length > 0) {
                    dirsHtml += `<div style="margin-bottom:2px;"><b style="color:#FF9800;">${_t('modelRegCustomDir') || 'Custom'}:</b></div>`;
                    for (const cd of customDirs) {
                        dirsHtml += `<div style="display:flex;align-items:center;gap:4px;padding:1px 0;"><span style="flex:1;font-size:9px;word-break:break-all;">${cd}</span><button onclick="removeCustomModelsDir('${cd.replace(/\\/g, '\\\\')}')" style="font-size:8px;padding:1px 4px;background:rgba(244,67,54,0.15);border:1px solid rgba(244,67,54,0.3);color:#f44336;border-radius:3px;cursor:pointer;flex-shrink:0;">✕</button></div>`;
                    }
                }
                dirsHtml += `<div style="margin-top:4px;display:flex;gap:4px;align-items:center;">`;
                dirsHtml += `<input id="custom-models-dir-input" type="text" placeholder="${_t('modelRegCustomDirPh') || 'Custom models dir path'}" style="flex:1;height:22px;font-size:10px;padding:0 6px;box-sizing:border-box;border-radius:4px;border:1px solid rgba(255,255,255,0.1);background:rgba(0,0,0,0.2);color:var(--text-main);">`;
                dirsHtml += `<button onclick="addCustomModelsDir()" style="font-size:9px;padding:2px 8px;height:22px;background:var(--accent);border:1px solid var(--accent);color:#fff;border-radius:4px;cursor:pointer;font-weight:600;">＋</button>`;
                dirsHtml += `</div></div>`;
                dirs.innerHTML = dirsHtml;
            }
            renderModelRegistry(_modelRegistryData);
        } catch (e) {
            list.innerHTML = `<div style="font-size:10px;color:#f44336;">${_t('modelRegLoadFail') || 'Failed'}: ${e.message}</div>`;
        }
    };

    window.addCustomModelsDir = async function() {
        const input = document.getElementById('custom-models-dir-input');
        if (!input) return;
        const path = input.value.trim();
        if (!path) return;
        try {
            const res = await fetch(`${BASE}/api/models/registry/custom-dir`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({path: path}),
            });
            const data = await res.json();
            if (data.status === 'added') {
                input.value = '';
                loadModelRegistry();
            } else {
                alert(data.error || 'Failed');
            }
        } catch (e) {
            alert(e.message);
        }
    };

    window.removeCustomModelsDir = async function(path) {
        if (!path) return;
        try {
            const res = await fetch(`${BASE}/api/models/registry/custom-dir`, {
                method: 'DELETE',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({path: path}),
            });
            const data = await res.json();
            if (data.status === 'removed') {
                loadModelRegistry();
            } else {
                alert(data.error || 'Failed');
            }
        } catch (e) {
            alert(e.message);
        }
    };

    function renderModelRegistry(models) {
        const list = document.getElementById('model-registry-list');
        if (!list) return;
        list.innerHTML = '';

        const fastModels = models.filter(m => m.pipeline_mode === 'fast');
        const devModels = models.filter(m => m.pipeline_mode === 'dev');
        const upscalerModels = models.filter(m => m.pipeline_mode === 'upscaler');
        const loraModels = models.filter(m => m.tags && m.tags.includes('lora'));
        const otherModels = models.filter(m => !['fast','dev','upscaler'].includes(m.pipeline_mode) && !(m.tags && m.tags.includes('lora')));

        const toolbar = document.createElement('div');
        toolbar.style.cssText = 'display:flex;align-items:center;gap:6px;margin-bottom:6px;flex-wrap:wrap;';

        const selectAllCb = document.createElement('input');
        selectAllCb.type = 'checkbox';
        selectAllCb.id = 'model-select-all';
        selectAllCb.style.cssText = 'cursor:pointer;accent-color:#1565C0;width:14px;height:14px;';
        selectAllCb.onchange = function() {
            const checked = this.checked;
            list.querySelectorAll('.model-cb').forEach(cb => { cb.checked = checked; });
        };

        const selectAllLabel = document.createElement('label');
        selectAllLabel.htmlFor = 'model-select-all';
        selectAllLabel.style.cssText = 'font-size:10px;color:var(--text-dim);cursor:pointer;user-select:none;';
        selectAllLabel.textContent = _t('modelRegSelectAll') || '全选';

        const batchBtn = document.createElement('button');
        batchBtn.id = 'model-batch-download-btn';
        batchBtn.style.cssText = 'font-size:10px;padding:3px 10px;border-radius:4px;cursor:pointer;font-weight:600;border:1px solid;background:rgba(21,101,192,0.2);border-color:rgba(25,118,210,0.5);color:#42A5F5;';
        batchBtn.textContent = _t('modelRegBatchDownload') || '批量下载';
        batchBtn.onclick = function() {
            const selected = [];
            list.querySelectorAll('.model-cb:checked').forEach(cb => {
                selected.push(cb.dataset.modelId);
            });
            if (selected.length === 0) {
                alert(_t('modelRegNoSelection') || '请先选择要下载的模型');
                return;
            }
            selected.forEach(id => {
                const btn = document.querySelector(`[data-download-id="${id}"]`);
                if (btn && !btn.disabled) {
                    downloadRegistryModel(id, btn);
                }
            });
        };

        toolbar.appendChild(selectAllCb);
        toolbar.appendChild(selectAllLabel);
        toolbar.appendChild(batchBtn);
        list.appendChild(toolbar);

        function renderGroup(title, items) {
            if (items.length === 0) return;
            const header = document.createElement('div');
            header.style.cssText = 'font-size:10px;font-weight:700;color:var(--text-dim);margin-top:6px;margin-bottom:2px;padding-left:2px;';
            header.textContent = title;
            list.appendChild(header);

            for (const m of items) {
                const row = document.createElement('div');
                row.style.cssText = 'display:flex;align-items:center;gap:6px;padding:4px 6px;border-radius:5px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.05);margin-bottom:2px;';

                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.className = 'model-cb';
                cb.dataset.modelId = m.model_id;
                cb.style.cssText = 'cursor:pointer;accent-color:#1565C0;width:13px;height:13px;flex-shrink:0;';
                if (m.downloaded) {
                    cb.disabled = true;
                    cb.checked = true;
                    cb.style.opacity = '0.4';
                }

                const info = document.createElement('div');
                info.style.cssText = 'flex:1;min-width:0;';

                const nameRow = document.createElement('div');
                nameRow.style.cssText = 'display:flex;align-items:center;gap:4px;';

                const nameSpan = document.createElement('span');
                nameSpan.style.cssText = 'font-size:11px;font-weight:600;color:var(--text-main);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
                nameSpan.textContent = m.name;
                nameRow.appendChild(nameSpan);

                const quantTag = document.createElement('span');
                quantTag.style.cssText = 'font-size:8px;padding:1px 4px;border-radius:3px;font-weight:700;text-transform:uppercase;';
                if (m.quantization === 'fp8') {
                    quantTag.style.background = 'rgba(76,175,80,0.15)';
                    quantTag.style.color = '#4CAF50';
                    quantTag.textContent = 'FP8';
                } else if (m.quantization === 'bf16') {
                    quantTag.style.background = 'rgba(33,150,243,0.15)';
                    quantTag.style.color = '#2196F3';
                    quantTag.textContent = 'BF16';
                } else {
                    quantTag.style.background = 'rgba(255,255,255,0.06)';
                    quantTag.style.color = 'var(--text-dim)';
                    quantTag.textContent = m.quantization.toUpperCase();
                }
                nameRow.appendChild(quantTag);

                if (m.tags && m.tags.includes('recommended')) {
                    const recTag = document.createElement('span');
                    recTag.style.cssText = 'font-size:8px;padding:1px 4px;border-radius:3px;font-weight:600;background:rgba(255,152,0,0.15);color:#FF9800;';
                    recTag.textContent = _t('modelRegRecommended') || 'REC';
                    nameRow.appendChild(recTag);
                }

                info.appendChild(nameRow);

                const descRow = document.createElement('div');
                descRow.style.cssText = 'font-size:9px;color:var(--text-dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
                descRow.textContent = `${m.size_gb}GB · ${_t('modelRegMinVram') || 'Min VRAM'}: ${m.min_vram_gb}GB`;
                info.appendChild(descRow);

                row.appendChild(cb);
                row.appendChild(info);

                if (m.downloaded) {
                    const doneTag = document.createElement('span');
                    doneTag.style.cssText = 'font-size:9px;padding:2px 6px;border-radius:4px;background:rgba(76,175,80,0.15);color:#4CAF50;font-weight:600;white-space:nowrap;';
                    doneTag.textContent = '✓';
                    row.appendChild(doneTag);
                } else {
                    const dlBtn = document.createElement('button');
                    dlBtn.dataset.downloadId = m.model_id;
                    dlBtn.style.cssText = 'font-size:9px;padding:2px 8px;border-radius:4px;background:var(--accent);border:1px solid var(--accent);color:#fff;cursor:pointer;font-weight:600;white-space:nowrap;';
                    dlBtn.textContent = _t('modelRegDownload') || 'Download';
                    dlBtn.onclick = () => downloadRegistryModel(m.model_id, dlBtn);
                    row.appendChild(dlBtn);
                }

                list.appendChild(row);
            }
        }

        renderGroup(_t('modelRegCheckpoints') || 'Checkpoints', fastModels);
        renderGroup(_t('modelRegDev') || 'Dev', devModels);
        renderGroup(_t('modelRegUpscalers') || 'Upscalers', upscalerModels);
        renderGroup(_t('modelRegLora') || 'LoRA', loraModels);
        renderGroup(_t('modelRegOther') || '其他', otherModels);
    }

    async function downloadRegistryModel(modelId, btn) {
        const origText = btn.textContent;
        btn.textContent = _t('modelRegDownloading') || '...';
        btn.disabled = true;
        btn.style.opacity = '0.6';
        try {
            const res = await fetch(`${BASE}/api/models/registry/download`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({model_id: modelId}),
            });
            const data = await res.json();
            if (data.status === 'already_exists') {
                btn.textContent = '✓';
                btn.style.background = 'rgba(76,175,80,0.15)';
                btn.style.borderColor = 'rgba(76,175,80,0.3)';
                btn.style.color = '#4CAF50';
                return;
            }
            if (data.status === 'started') {
                btn.textContent = _t('modelRegDownloading') || 'Downloading...';
                pollModelDownload(modelId, btn);
                return;
            }
            btn.textContent = _t('modelRegFailed') || 'Failed';
            btn.style.background = 'rgba(244,67,54,0.15)';
            btn.style.borderColor = 'rgba(244,67,54,0.3)';
            btn.style.color = '#f44336';
        } catch (e) {
            btn.textContent = _t('modelRegFailed') || 'Failed';
            btn.style.background = 'rgba(244,67,54,0.15)';
            btn.style.borderColor = 'rgba(244,67,54,0.3)';
            btn.style.color = '#f44336';
        }
    }

    function pollModelDownload(modelId, btn) {
        const interval = setInterval(async () => {
            try {
                const res = await fetch(`${BASE}/api/models/registry/status`);
                const data = await res.json();
                if (!data.active && data.model_id === modelId) {
                    clearInterval(interval);
                    if (data.error) {
                        btn.textContent = _t('modelRegFailed') || 'Failed';
                        btn.style.background = 'rgba(244,67,54,0.15)';
                        btn.style.borderColor = 'rgba(244,67,54,0.3)';
                        btn.style.color = '#f44336';
                    } else {
                        btn.textContent = '✓';
                        btn.style.background = 'rgba(76,175,80,0.15)';
                        btn.style.borderColor = 'rgba(76,175,80,0.3)';
                        btn.style.color = '#4CAF50';
                        loadModelCheckpoints();
                    }
                } else if (data.active && data.model_id === modelId) {
                    const pct = Math.round(data.progress || 0);
                    btn.textContent = `${pct}%`;
                }
            } catch (e) {
                clearInterval(interval);
            }
        }, 3000);
    }

    function switchMode(m) {
        currentMode = m;
        scheduleSave();
        const tabImage = document.getElementById('tab-image');
        const tabVideo = document.getElementById('tab-video');
        const tabBatch = document.getElementById('tab-batch');
        if (tabImage) tabImage.classList.toggle('active', m === 'image');
        if (tabVideo) tabVideo.classList.toggle('active', m === 'video');
        if (tabBatch) tabBatch.classList.toggle('active', m === 'batch');
        const tabMotion = document.getElementById('tab-motion');
        if (tabMotion) tabMotion.classList.toggle('active', m === 'motion');
        const tabTts = document.getElementById('tab-tts');
        if (tabTts) tabTts.classList.toggle('active', m === 'tts');

        const imageOpts = document.getElementById('image-opts');
        const videoOpts = document.getElementById('video-opts');
        const batchOpts = document.getElementById('batch-opts');
        if (imageOpts) imageOpts.style.display = m === 'image' ? 'block' : 'none';
        if (videoOpts) videoOpts.style.display = m === 'video' ? 'block' : 'none';
        if (batchOpts) batchOpts.style.display = m === 'batch' ? 'block' : 'none';
        const motionOpts = document.getElementById('motion-opts');
        if (motionOpts) motionOpts.style.display = m === 'motion' ? 'block' : 'none';
        const ttsOpts = document.getElementById('tts-opts');
        if (ttsOpts) ttsOpts.style.display = m === 'tts' ? 'block' : 'none';

        // 主按钮：TTS 模式下隐藏（TTS 有自己的生成按钮）
        const mainBtn = document.getElementById('mainBtn');
        if (mainBtn) mainBtn.closest('div').style.display = m === 'tts' ? 'none' : '';

        // 视觉提示词：TTS 模式下隐藏（因为 TTS 有自己的输入框）
        const pc = document.getElementById('prompt-container');
        if (pc) pc.style.display = m === 'tts' ? 'none' : '';

        // 移除 TTS 模式下的上方分割线
        const mainTabsSection = document.getElementById('main-tabs-section');
        if (mainTabsSection) {
            mainTabsSection.style.borderBottom = m === 'tts' ? 'none' : '';
            mainTabsSection.style.paddingBottom = m === 'tts' ? '0' : '';
        }

        if (m === 'batch') updateBatchSegments();
        if (m === 'tts') checkTtsStatus();
        refreshPromptPlaceholder();
    }

    window.setVideoTransferMode = function(mode) {
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
        };
        activeStyle(actionBtn, isAction);
        activeStyle(cameraBtn, window._videoTransferMode === 'camera');
        activeStyle(repaintBtn, isRepaint);
    };

    function refreshPromptPlaceholder() {
        const pe = document.getElementById('prompt');
        if (!pe) return;
        pe.placeholder =
            currentMode === 'tts' ? '切换到 TTS 模式时此框不参与生成' :
            _t('promptPlaceholder');
    }

    // ─── TTS 语音合成 ──────────────────────────────────────────────────────────
    let _ttsRefB64 = null; // 存放参考音频的 base64 内容

    window.onTtsModeChange = function() {
        const mode = document.getElementById('tts-mode').value;
        const refSec = document.getElementById('tts-ref-section');
        const ultSec = document.getElementById('tts-ultimate-section');
        if (refSec) refSec.style.display = (mode === 'clone' || mode === 'ultimate_clone') ? 'block' : 'none';
        if (ultSec) ultSec.style.display = mode === 'ultimate_clone' ? 'block' : 'none';
    };

    window.handleTtsRefUpload = async function(file) {
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
            addLog(`✅ 参考音频已加载: ${file.name}`);
        };
        reader.readAsDataURL(file);
    };

    window.clearTtsRef = function() {
        _ttsRefB64 = null;
        const fi = document.getElementById('tts-ref-input');
        if (fi) fi.value = '';
        const placeholder = document.getElementById('tts-ref-placeholder');
        const statusEl = document.getElementById('tts-ref-status');
        const clearBtn = document.getElementById('tts-ref-clear');
        if (placeholder) placeholder.style.display = 'block';
        if (statusEl) { statusEl.style.display = 'none'; statusEl.textContent = ''; }
        if (clearBtn) clearBtn.style.display = 'none';
        addLog('🧹 已清除参考音频');
    };

    async function checkTtsStatus() {
        const bar = document.getElementById('tts-status-bar');
        if (!bar) return;
        try {
            const res = await fetch(`${BASE}/api/tts/status`);
            const data = await res.json();
            if (data.available) {
                bar.style.color = 'var(--accent)';
                bar.style.borderColor = 'var(--accent)';
                bar.textContent = _t('ttsStatusReady') + data.model_dir;
            } else if (!data.voxcpm_installed) {
                bar.style.color = '#f87171';
                bar.textContent = _t('ttsStatusNoPkq');
            } else if (!data.model_dir_exists) {
                bar.style.color = '#f87171';
                bar.textContent = _t('ttsStatusNoDir') + (data.expected_model_dir || data.model_dir || 'models\\VoxCPM2');
            } else {
                bar.style.color = 'var(--text-dim)';
                bar.textContent = _t('ttsStatusNotAvail');
            }
        } catch (e) {
            bar.style.color = '#f87171';
            bar.textContent = _t('ttsStatusConnErr') + e.message;
        }
    }

    window.runTts = async function() {
        const text = (document.getElementById('tts-text')?.value || '').trim();
        if (!text) { addLog(_t('ttsErrNoText')); return; }

        const mode = document.getElementById('tts-mode')?.value || 'text_only';
        const cfg = parseFloat(document.getElementById('tts-cfg')?.value || 2.0);
        const steps = parseInt(document.getElementById('tts-steps')?.value || 10);
        const promptText = document.getElementById('tts-prompt-text')?.value || '';

        if ((mode === 'clone' || mode === 'ultimate_clone') && !_ttsRefB64) {
            addLog(_t('ttsErrNoRef'));
            return;
        }

        const btn = document.getElementById('tts-gen-btn');
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
            };

            const res = await fetch(`${BASE}/api/tts/generate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();

            if (!res.ok || data.status !== 'complete') {
                throw new Error(data.error || 'Generation failed');
            }

            const audioUrl = `${BASE}${data.audio_url}`;
            const player = document.getElementById('tts-audio-player');
            const dlLink = document.getElementById('tts-download-link');

            // 播放音频
            if (player) { player.src = audioUrl; player.load(); }
            // 直接下载音频，不跳转页面
            if (dlLink) {
                try {
                    const resp = await fetch(audioUrl);
                    const blob = await resp.blob();
                    const blobUrl = URL.createObjectURL(blob);
                    dlLink.href = blobUrl;
                    dlLink.download = data.audio_path || 'tts_output.wav';
                    // 不再自动触发点击下载
                    // dlLink.click();
                } catch (e) {
                    // 若下载失败，回退到直接链接方式
                    dlLink.href = audioUrl;
                    dlLink.download = data.audio_path || 'tts_output.wav';
                }
            }
            if (resultSec) resultSec.style.display = 'block';

            addLog(`✅ TTS: ${data.audio_path} (${data.sample_rate} Hz)`);

            // 刷新历史记录
            setTimeout(() => { isLoadingHistory = false; if (typeof fetchHistory === 'function') fetchHistory(1); }, 500);

        } catch (e) {
            addLog(`❌ TTS Error: ${e.message}`);
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = _t('ttsGenBtn'); }
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
            try { player.source = { type: 'video', sources: [] }; } catch(_) {}
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

    window.updateSeedModeUI = function() {
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
    };

    const replayStore = new Map();
    let replayStoreSeq = 0;
    const pendingReplaySeedByMode = {};

    function makeReplayRecord(source) {
        const payload = source?.payload || {};
        const endpoint = source?.endpoint || (source?.mode === 'image' ? '/api/generate-image' : '/api/generate');
        let mode = source?.mode || 'video';
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

    async function replayRecordById(id) {
        const record = replayStore.get(id);
        if (!record || !record.endpoint || !record.payload) {
            addLog(_t('replayMissing'));
            return;
        }
        try {
            const info = await submitQueuedTask(
                record.mode,
                record.endpoint,
                record.payload,
                `${_t('replayLabel')}: ${record.label || record.mode}`
            );
            addLog(_fmt('replayQueuedLog', { id: info.task_id }));
        } catch (e) {
            addLog(`❌ ${_t('replayFailed')}: ${e.message || e}`);
        }
    }

    function pathFileName(path) {
        return String(path || '').split(/[\\/]/).pop() || '';
    }

    function mediaUrlForPath(path) {
        return `${BASE}/api/system/file?path=${encodeURIComponent(path)}`;
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
        } else if (record.mode === 'video') {
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
            const imgPath = Array.isArray(payload.images) && payload.images[0] ? payload.images[0].path : '';
            setMotionImagePath(imgPath);
            setElValue('motion-conditioning-type', payload.conditioning_type);
            setElValue('motion-fps', payload.fps);
            setElValue('motion-duration', payload.duration);
            setElValue('motion-strength', payload.conditioning_strength);
            const strengthVal = document.getElementById('motion-strength-val');
            if (strengthVal && payload.conditioning_strength !== undefined) strengthVal.textContent = String(payload.conditioning_strength);
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

    window.loadCurrentPreviewSeed = function() {
        const record = currentPreviewReplayId ? replayStore.get(currentPreviewReplayId) : null;
        const seed = record?.payload?.seed;
        if (!seed) {
            addLog(_t('previewNoReplaySeed'));
            return;
        }
        if (setFixedSeed(seed)) {
            addLog(_fmt('previewSeedLoadedLog', { seed }));
        }
    };

    window.loadCurrentPreviewParams = function() {
        if (!currentPreviewReplayId || !replayStore.has(currentPreviewReplayId)) {
            addLog(_t('previewNoReplayParams'));
            return;
        }
        applyReplayPayloadById(currentPreviewReplayId);
    };

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
        const useQueue = ['video', 'motion', 'batch', 'image'].includes(currentMode);
        // 防止重复点击（_isGeneratingFlag 比 btn.disabled 更可靠）
        if (_isGeneratingFlag) {
            addLog(_t('warnGenerating'));
            return;
        }

        const btn = document.getElementById('mainBtn');
        const promptEl = document.getElementById('prompt');
        const prompt = promptEl ? promptEl.value.trim() : '';

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
        btn.disabled = true;

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
                if (player) { try { player.pause(); } catch(_) {} try { player.source = { type: 'video', sources: [] }; } catch(_) {} }
                else if (resVideo) { resVideo.pause?.(); resVideo.removeAttribute?.('src'); }
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
                    numImages: 1
                };
                addLog(`正在发起图像渲染: ${w}x${h}, Steps: ${payload.numSteps}`);

            } else if (currentMode === 'video') {
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
                const modelPath = getSelectedModelCheckpointPath();

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
                };
                
                let loraLog = _t('loraNoneLabel');
                if (effectiveLoraPaths.length > 0) {
                    loraLog = effectiveLoraPaths.map(p => p.split(/[/\\]/).pop()).join(', ');
                }
                addLog(`正在发起视频渲染: ${res}, 时长: ${dur}s, FPS: ${fps}, LoRA: ${loraLog}`);

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
                const motionPrompt = prompt || 'A high quality video of the target subject following the reference motion, coherent movement, stable identity, clean details';
                endpoint = '/api/ic-lora/generate';
                payload = {
                    video_path: videoPath,
                    conditioning_type: conditioningType,
                    prompt: motionPrompt,
                    conditioning_strength: strength,
                    fps: motionFps,
                    duration: motionDuration,
                    num_inference_steps: 30,
                    cfg_guidance_scale: 1.0,
                    negative_prompt: "low quality, blurry, noisy, static noise, distorted",
                    images: transferMode === 'repaint' ? [] : [{ path: imagePath, frame: 0, strength: 1.0 }]
                };
                if (!prompt) addLog(_t('motionDefaultPromptNotice'));
                addLog(`${_fmt('motionStartLog', { type: conditioningType, strength })}, ${_fmt('motionStartMeta', { fps: motionFps || 'auto', duration: motionDuration || 'auto' })}`);

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
                    };
                    addLog(
                        `单次多关键帧: ${nKf} 帧时长合计 ${sumSec.toFixed(1)}s → 请求时长 ${dur}s, ${res}, FPS ${fps}`
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
                        modelPath: modelPath || null
                    };
                    addLog(`分段拼接: ${segments.length} 段, ${res}${backgroundAudioPath ? '，含统一配乐' : ''}`);
                }
            }

            if (useQueue) {
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
            _isGeneratingFlag = false;
            if (btn) btn.disabled = false;
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
                console.log('[PROGRESS]', JSON.stringify(d));
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

    window.toggleAudioPreviewPlayback = function() {
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
    };

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

    function displayOutput(fileOrPath, outputType = null, options = {}) {
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
        
        if (vid) {
            vid.pause();
            vid.removeAttribute('src');
            vid.load();
        }
        if (player) {
            try { player.pause(); } catch(_) {}
            try {
                player.source = { type: 'video', sources: [] };
            } catch(_) {}
        }
        if (audio) {
            audio.pause();
            audio.removeAttribute('src');
            audio.load();
        }
        if (audioPlayer) {
            try { audioPlayer.stop(); } catch(_) {}
        }
        
        let url = "";
        let fileName = fileOrPath;
        if (fileOrPath.indexOf('\\') !== -1 || fileOrPath.indexOf('/') !== -1) {
            url = `${BASE}/api/system/file?path=${encodeURIComponent(fileOrPath)}&t=${Date.now()}`;
            fileName = fileOrPath.split(/[\\/]/).pop();
        } else {
            const outInput = document.getElementById('global-out-dir');
            const globalDir = outInput ? outInput.value.replace(/\\/g, '/').replace(/\/$/, '') : "";
            if (globalDir && globalDir !== "") {
                url = `${BASE}/api/system/file?path=${encodeURIComponent(globalDir + '/' + fileOrPath)}&t=${Date.now()}`;
            } else {
                url = `${BASE}/outputs/${fileOrPath}?t=${Date.now()}`;
            }
        }

        console.log("[DEBUG][displayOutput] fileOrPath:", fileOrPath);
        console.log("[DEBUG][displayOutput] effectiveType:", effectiveType);
        console.log("[DEBUG][displayOutput] Constructed URL:", url);
        console.log("[DEBUG][displayOutput] BASE:", BASE);
        console.log("[DEBUG][displayOutput] Has player:", !!player);

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
                audio.load();
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
            img.src = url;
            img.style.display = "block";
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
            if (url.includes('/api/system/file')) {
                _altUrls.push(url.replace('/api/system/file?path=', '/outputs/').split('&t=')[0].replace(/%2F/g, '/').replace(/%5C/g, '/'));
            } else if (url.includes('/outputs/')) {
                const outInput2 = document.getElementById('global-out-dir');
                const globalDir2 = outInput2 ? outInput2.value.replace(/\\/g, '/').replace(/\/$/, '') : "";
                if (globalDir2) {
                    const fname2 = url.split('/outputs/')[1].split('?')[0];
                    _altUrls.push(`${BASE}/api/system/file?path=${encodeURIComponent(globalDir2 + '/' + fname2)}&t=${Date.now()}`);
                }
            }

            function _onVideoError(err) {
                console.error("[VIDEO-PROTECT] Video error:", err);
                if (vid && vid.error) {
                    console.error("[VIDEO-PROTECT] Error code:", vid.error.code, "message:", vid.error.message);
                }
                if (_videoRetryCount < _videoMaxRetries && _altUrls.length > 0) {
                    _videoRetryCount++;
                    const retryUrl = _altUrls[0] + (_altUrls[0].includes('?') ? '&' : '?') + 'retry=' + _videoRetryCount + '&t=' + Date.now();
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
                    if (vid) {
                        vid.removeEventListener('error', _onVideoError);
                    }
                }
            }

            function _tryPlayVideoNative(videoUrl) {
                if (!vid) return;
                vid.removeEventListener('error', _onVideoError);
                vid.addEventListener('error', _onVideoError);
                vid.src = videoUrl;
                vid.load();
                try {
                    const p = vid.play();
                    if (p && typeof p.catch === 'function') p.catch(() => {});
                } catch(_) {}
            }

            function _tryPlayVideo(videoUrl) {
                if (!vid) return;
                vid.removeEventListener('error', _onVideoError);
                vid.addEventListener('error', _onVideoError);
                if (player) {
                    try {
                        vid.src = videoUrl;
                        vid.load();
                    } catch(_) {}
                    try {
                        player.source = {
                            type: 'video',
                            sources: [{ src: videoUrl, type: 'video/mp4' }]
                        };
                    } catch(_) {}
                    setTimeout(() => {
                        try {
                            const p = player.play();
                            if (p && typeof p.catch === 'function') p.catch(() => {});
                        } catch(_) {}
                    }, 150);
                } else {
                    _tryPlayVideoNative(videoUrl);
                }
            }

            _tryPlayVideo(url);
            if (!silentLog) addLog(`✅ 视频加载中: ${fileName}`);
        }
    }

    window.downloadCurrentPreviewAsset = function() {
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
    };



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
        } catch(e) { console.error('[INIT] _onDomReady error:', e); }
        fetch(`${BASE}/api/app-info`)
            .then(r => r.json())
            .then(data => {
                const nameEl = document.getElementById('app-name');
                if (nameEl && data.app_name) nameEl.textContent = data.app_name;
                if (data.version) window.__appVersion = data.version;
            })
            .catch(() => {});
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _onDomReady);
    } else {
        _onDomReady();
    }


    
    
    
    
    
    
    
    
    
    
    let currentHistoryPage = 1;
    let isLoadingHistory = false;
    const HISTORY_PAGE_LIMIT = 240;
    const HISTORY_THUMB_CONCURRENCY = 10;
    let _historyListFingerprint = '';
    let _historyHasRendered = false;
    const _historyRenderedKeys = new Set();
    let historyThumbLoaderBusy = false;
    let historyThumbLoadQueued = false;
    let historyThumbLoaderToken = 0;
    let _historyLayout = localStorage.getItem('historyLayout') || 'list';

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
            return fetchHistory(true);
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
        if (item.type !== 'video') return historyMediaUrl(item, globalDir);
        const rawPath = item.fullpath || (globalDir ? globalDir + '/' + item.filename : item.filename);
        return `${BASE}/api/system/video-thumbnail?path=${encodeURIComponent(rawPath)}&mtime=${encodeURIComponent(item.mtime || '')}&size=${encodeURIComponent(item.size || '')}`;
    }

    function renderHistoryCardHtml(item, globalDir) {
        const url = historyMediaUrl(item, globalDir);
        const thumbUrl = historyThumbUrl(item, globalDir);
        const safeFilename = item.filename.replace(/'/g, "\\'").replace(/"/g, '\\"');
        const key = escapeHtmlAttr(historyItemKey(item));
        const safeGlobalDir = (globalDir || '').replace(/'/g, "\\'").replace(/"/g, '\\"');
        const replayId = item.replay_available && item.replay ? storeReplayRecord(item.replay) : '';
        const typeBadge = item.type === 'video' ? '🎬 VID' : item.type === 'audio' ? '♪ AUD' : '🎨 IMG';
        const info = extractReplayInfo(item);

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

            const paramHtml = `
                ${promptText ? `<div class="list-param-line lpl-prompt-line" onclick="this.classList.toggle('lpl-expanded')"><span class="lpl-key">提示词</span><span class="lpl-val lpl-prompt">${promptText}</span></div>` : ''}
                <div class="list-param-line">
                    <span class="lpl-key">方式</span><span class="lpl-val lpl-method">${method || '—'}</span>
                    <span class="lpl-key">分辨率</span><span class="lpl-val">${resolution || '—'}</span>
                    <span class="lpl-key">帧率</span><span class="lpl-val">${fps || '—'}</span>
                    <span class="lpl-key">时长</span><span class="lpl-val">${duration || '—'}</span>
                    <span class="lpl-key">种子</span><span class="lpl-val">${seed || '—'}</span>
                </div>
                <div class="list-param-line">
                    <span class="lpl-key">步数</span><span class="lpl-val">${steps || '—'}</span>
                    <span class="lpl-key">CFG</span><span class="lpl-val">${cfg || '—'}</span>
                    <span class="lpl-key">运镜</span><span class="lpl-val">${motion || '—'}</span>
                    <span class="lpl-key">LoRA</span><span class="lpl-val lpl-lora">${lora || '—'}</span>
                    <span class="lpl-key">耗时</span><span class="lpl-val">${elapsed || '—'}</span>
                    <span class="lpl-key">大小</span><span class="lpl-val">${formatFileSize(item.size)}</span>
                    <span class="lpl-key">文件名</span><span class="lpl-val lpl-filename">${escapeHtmlAttr(item.filename)}</span>
                </div>`;

            return `<div class="history-card-list" data-history-key="${key}" data-filename="${safeFilename}" data-type="${item.type}" data-replayid="${replayId}" data-globaldir="${safeGlobalDir}">
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
        if (info.width && info.height) gridTags.push(`<span class="ptag">${info.width}×${info.height}</span>`);
        if (info.fps) gridTags.push(`<span class="ptag">${info.fps}fps</span>`);
        if (info.duration) gridTags.push(`<span class="ptag">${info.duration}</span>`);
        if (info.seed) gridTags.push(`<span class="ptag">Seed:${info.seed}</span>`);
        if (info.loras && info.loras.length > 0) {
            const loraStr = info.loras.map(l => escapeHtmlAttr(l)).join(', ');
            gridTags.push(`<span class="ptag ptag-lora">${loraStr}</span>`);
        }
        if (info.elapsed) gridTags.push(`<span class="ptag ptag-elapsed">⏱${info.elapsed}</span>`);

        const gridInfoHtml = `<div class="grid-info-bar">
                ${info.prompt ? `<div class="grid-info-prompt">${escapeHtmlAttr(info.prompt.slice(0, 120))}</div>` : ''}
                <div class="param-row">${gridTags.join('')}</div>
           </div>`;

        return `<div class="history-card" data-history-key="${key}" data-filename="${safeFilename}" data-type="${item.type}" data-replayid="${replayId}" data-globaldir="${safeGlobalDir}">
                    <div class="grid-thumb-wrap">
                        <div class="history-type-badge">${typeBadge}</div>
                        <button class="history-delete-btn" onclick="event.stopPropagation(); deleteHistoryItem('${safeFilename}', '${item.type}', this)">✕</button>
                        <button class="history-save-btn" onclick="event.stopPropagation(); downloadHistoryItem('${safeFilename}', '${safeGlobalDir}')">⬇</button>
                        ${media}
                    </div>
                    ${gridInfoHtml}
                </div>`;
    }

    function resetHistoryAndReload() {
        _historyListFingerprint = '';
        _historyHasRendered = false;
        _historyRenderedKeys.clear();
        historyThumbLoaderToken++;
        const container = document.getElementById('history-container');
        if (container) container.innerHTML = '';
        isLoadingHistory = false;
        return fetchHistory(true);
    }

    async function fetchHistory(isFirstLoad = false, silent = false) {
        if (isLoadingHistory) return;
        isLoadingHistory = true;
        
        try {
            // 只加载最近一批历史，避免输出目录视频过多时拖慢缩略图初始化
            const res = await fetch(`${BASE}/api/system/history?page=1&limit=${HISTORY_PAGE_LIMIT}`);
            if (!res.ok) {
                isLoadingHistory = false;
                return;
            }
            const data = await res.json();

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

            if (validHistory.length === 0) {
                container.innerHTML = loadingCardHtml;
                const newLcEmpty = document.getElementById('current-loading-card');
                if (newLcEmpty) newLcEmpty.onclick = showGeneratingView;
                _historyListFingerprint = fingerprint;
                return;
            }

            const outInput = document.getElementById('global-out-dir');
            const globalDir = outInput ? outInput.value.replace(/\\/g, '/').replace(/\/$/, '') : "";

            const needsFullRender = !_historyHasRendered || container.querySelectorAll('.history-card[data-history-key], .history-card-list[data-history-key]').length === 0;
            if (needsFullRender) {
                const cardsHtml = validHistory.map((item) => renderHistoryCardHtml(item, globalDir)).join('');
                container.insertAdjacentHTML('beforeend', cardsHtml);
                _historyRenderedKeys.clear();
                validHistory.forEach((item) => _historyRenderedKeys.add(historyItemKey(item)));
                _historyHasRendered = true;
            } else {
                const existingCards = new Map(
                    Array.from(container.querySelectorAll('.history-card[data-history-key], .history-card-list[data-history-key]')).map((card) => [card.dataset.historyKey, card])
                );
                existingCards.forEach((card, key) => {
                    if (!_historyRenderedKeys.has(key)) _historyRenderedKeys.add(key);
                });
                const freshKeys = new Set(validHistory.map((item) => historyItemKey(item)));
                existingCards.forEach((card, key) => {
                    if (!freshKeys.has(key)) {
                        card.remove();
                        _historyRenderedKeys.delete(key);
                    }
                });

                const newItems = validHistory.filter((item) => !_historyRenderedKeys.has(historyItemKey(item)));
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
        } catch(e) {
            console.error("Failed to load history", e);
        } finally {
            isLoadingHistory = false;
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
            url = `${BASE}/outputs/${filename}`;
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
                await new Promise((resolve) => setTimeout(resolve, 120));
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

    // 监听history-wrapper的滚动事件来懒加载
    function initHistoryScrollListener() {
        const hw = document.getElementById('history-wrapper');
        if (!hw) return;
        
        let scrollTimeout;
        hw.addEventListener('scroll', () => {
            if (scrollTimeout) clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => {
                requestHistoryThumbLoad();
            }, 100);
        });
    }

    // 页面加载时初始化滚动监听
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => setTimeout(initHistoryScrollListener, 500));
    } else {
        setTimeout(initHistoryScrollListener, 500);
    }

    window.displayHistoryOutput = function displayHistoryOutput(file, type, replayId = '') {
        console.log("[DEBUG][displayHistoryOutput] file:", file, "type:", type, "replayId:", replayId);
        const resImg = document.getElementById('res-img');
        const videoWrapper = document.getElementById('video-wrapper');
        if (resImg) resImg.style.display = 'none';
        if (videoWrapper) videoWrapper.style.display = 'none';
        const audioWrapper = document.getElementById('audio-wrapper');
        if (audioWrapper) audioWrapper.style.display = 'none';
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
        const type = card.dataset.type;
        const replayId = card.dataset.replayid || '';
        console.log("[DEBUG][card-click] Click on history card:", filename, type, replayId);
        displayHistoryOutput(filename, type, replayId);
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
        loadModelRegistry();
        loadCoreModels();

        let historyRefreshInterval = null;
        function startHistoryAutoRefresh() {
            if (historyRefreshInterval) return;
            historyRefreshInterval = setInterval(() => {
                const hc = document.getElementById('history-container');
                if (hc && hc.offsetParent !== null && !_isGeneratingFlag) {
                    fetchHistory(1, true);
                }
            }, 5000);
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
    const vidNote = vidSize.source === 'ref-missing' ? ` · ${_t('ratioRefMissing')}` : '';
    setBadge('vid-quality-rec', `推荐${_qualityKey(rec.width)}P（${vidSize.width}×${vidSize.height}）${vidNote}`);
    const batchSize = getGenerationSize('batch');
    const batchNote = batchSize.source === 'ref-missing' ? ` · ${_t('ratioRefMissing')}` : '';
    setBadge('batch-quality-rec', `推荐${_qualityKey(rec.width)}P（${batchSize.width}×${batchSize.height}）${batchNote}`);

    // 更新时长输入框的 max 属性
    durationEl.max = String(maxDuration);

    // 如果当前时长超过上限，自动钳位
    if (currentDuration > maxDuration) {
        durationEl.value = String(maxDuration);
    }

    // 更新时长警告提示
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

    // 自动应用推荐参数到表单
    applyRecommendation(primaryRec);

    // 显示自动应用通知
    _showAutoApplyToast(profile);

    // 初始动态推荐
    updateDynamicRecommendation();
}

/** 在 <select> 的推荐选项后追加 ⭐ 标记 */
function _markRecommendedOption(selectId, recValue) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    for (const opt of sel.options) {
        const base = opt.text.replace(/ ⭐$/, '');
        opt.text = opt.value === recValue ? base + ' ⭐' : base;
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
    }

    // 设置时长 (动态计算: duration = recommended_total_frames / fps)
    const currentFps = parseInt(fps.value) || 24;
    const recDuration = Math.max(1, Math.floor(r.recommended_total_frames / currentFps));
    const maxDuration = Math.max(1, Math.floor(r.max_total_frames / currentFps));
    const dur = document.getElementById('vid-duration');
    dur.max = String(maxDuration);
    dur.value = String(Math.min(recDuration, parseInt(dur.value) || recDuration));

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
    try {
        const syncRes = await fetch(`${BASE}/api/models/registry/sync`, { method: 'POST' });
        const syncData = await syncRes.json().catch(() => ({}));
        if (window.loadModelRegistry) await window.loadModelRegistry();
        if (typeof loadModelCheckpoints === 'function') await loadModelCheckpoints();
        if (typeof scanLoras === 'function') await scanLoras();
        if (syncData.success) {
            const msg = syncData.added > 0 || syncData.updated > 0
                ? `同步完成：新增 ${syncData.added} 个，更新 ${syncData.updated} 个模型`
                : '已同步，暂无新模型';
            addLog(`✅ ${msg}`);
        } else if (syncData.error) {
            addLog(`⚠️ 同步失败: ${syncData.error}，已刷新本地列表`);
        } else {
            addLog('✅ 已刷新模型列表');
        }
    } catch(e) {
        addLog(`⚠️ 同步异常: ${e.message}，已刷新本地列表`);
        if (window.loadModelRegistry) await window.loadModelRegistry();
    }
}

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

// 延迟加载硬件档案（等后端就绪）
setTimeout(loadHardwareProfile, 3000);
// 延迟绑定动态事件（等DOM就绪）
setTimeout(_initDynamicRecommendation, 3500);

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

setTimeout(() => {
    fetch(`${BASE}/api/system/env-check`).then(r => r.json()).then(d => { _envCheckData = d; }).catch(() => {});
}, 4000);

