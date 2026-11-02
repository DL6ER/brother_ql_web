// The label designer: an editor, a wizard with the saved and the ready-made
// labels, and the printer panel. Labels are saved on the server, the print
// history and the settings of this browser in localStorage.
'use strict';

const C = DESIGNER_CONFIG;
const BASE = C.base;
const API = BASE + '/api';
const SIZES = Object.fromEntries(C.label_sizes.map(s => [s.id, s]));
const FONTS = Object.fromEntries(C.fonts.map(f => [f.family, f.styles]));

// --- Small helpers ---------------------------------------------------------

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const clone = o => JSON.parse(JSON.stringify(o));
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
// The date of the browser, not of UTC
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };

function load(key, fallback) {
    try {
        const v = localStorage.getItem(key);
        return v === null ? fallback : JSON.parse(v);
    } catch (e) { return fallback; }
}
function store(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { toast('This browser could not keep the change'); }
}

function storeQuiet(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch (e) { return false; }
}

let toastTimer;
function toast(text, action) {
    const el = $('#toast');
    el.innerHTML = esc(text) + (action ? ` <button class="btn sm quiet" style="color:inherit;text-decoration:underline" type="button">${esc(action.label)}</button>` : '');
    el.hidden = false;
    if (action) el.querySelector('button').onclick = () => { el.hidden = true; action.run(); };
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, action ? 6000 : 3500);
}

function fmtTime(ts) {
    const d = new Date(ts), now = new Date();
    const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    if (d.toDateString() === now.toDateString()) return hm;
    const y = new Date(now); y.setDate(now.getDate() - 1);
    if (d.toDateString() === y.toDateString()) return 'Yesterday ' + hm;
    return d.toLocaleDateString([], { day: 'numeric', month: 'short' });
}

// Endless rolls and P-touch tape grow with the label
const isEndless = s => s.form === 'endless' || s.form === 'ptouch_endless';
// TZe tape, only P-touch printers take it
const isPtouch = id => !!SIZES[id] && SIZES[id].models.length > 0 && SIZES[id].models.every(m => m.startsWith('PT-'));

function sizeName(id) {
    const s = SIZES[id];
    if (!s) return id;
    if (isPtouch(id)) return s.name;
    if (s.form === 'endless') return `${s.tape[0]} mm endless${s.red ? ', black and red' : ''}`;
    if (s.form === 'round_die_cut') return `${s.tape[0]} mm round`;
    return `${s.tape[0]} x ${s.tape[1]} mm`;
}

// --- Fonts and lines -------------------------------------------------------

// New lines use the configured default font
const DEFAULT_FAMILY = C.default_font.split(',')[0];
function styleFor(family, bold) {
    const styles = FONTS[family] || [];
    if (bold) return styles.find(s => s.toLowerCase() === 'bold') || styles.find(s => /bold/i.test(s)) || styles[0];
    return styles.find(s => /^(book|regular)$/i.test(s)) || styles[0];
}
const fontOf = (family, bold) => `${family},${styleFor(family, bold)}`;
const isBold = font => /bold/i.test(String(font || '').split(',')[1] || '');
// Bold is kept with the line as a wish, so that it comes back after a font
// without a bold style. The font name says what is printed.
const wantsBold = l => l.bold !== undefined ? !!l.bold : isBold(l.font);
const hasBold = family => (FONTS[family] || []).some(st => /bold/i.test(st));
const familyOf = font => String(font || C.default_font).split(',')[0];

function L(text, size, bold = false, align = 'center', extra = {}) {
    return Object.assign({
        text, size: String(size), font: fontOf(DEFAULT_FAMILY, bold), align,
        inverted: false, checkbox: false, color: 'black', line_spacing: String(C.defaults.line_spacing)
    }, extra);
}

// With the defaults of the server's configuration
function baseDesign(size) {
    const def = C.defaults;
    return {
        label_size: size, orientation: def.orientation, print_type: 'text', text: [],
        code_text: '', barcode_type: 'QR', qrcode_size: String(def.qrcode_size), qrcode_correction: 'M',
        margin_top: String(def.margin_top), margin_bottom: String(def.margin_bottom), margin_left: String(def.margin_left), margin_right: String(def.margin_right),
        border_thickness: '0', border_roundness: '0', border_distance_x: '0', border_distance_y: '0', border_color: 'black',
        image_mode: 'black_and_white', image_bw_threshold: '128', image_fit: '1', image_crop: '0',
        image_scaling_factor: '100', image_rotation: '0', shrink_text: '1', print_color: 'black', high_res: '0', pdf_pages: '',
        // Empty: the server chooses the distance from the size of the text
        image_text_gap: ''
    };
}

// Labels as the API stores them (text as JSON, booleans as true/false)
function normalize(raw) {
    const d = Object.assign(baseDesign(raw.label_size || C.default_label_size), {});
    for (const k of Object.keys(d)) if (raw[k] !== undefined && raw[k] !== null) d[k] = raw[k];
    let text = raw.text;
    if (typeof text === 'string') { try { text = JSON.parse(text); } catch (e) { text = []; } }
    d.text = (Array.isArray(text) ? text : []).map(l => Object.assign(L('', 40), l, { size: String(l.size || 40) }));
    // Missing ones as the server defaults them, so that the label looks the same
    for (const [k, def] of [['image_fit', '1'], ['image_crop', '0'], ['shrink_text', '0']]) {
        d[k] = raw[k] === undefined || raw[k] === null ? def : (raw[k] === true || raw[k] === 1 || raw[k] === '1') ? '1' : '0';
    }
    if (raw.image_data) d.image = { data: raw.image_data, mime: raw.image_mime || 'image/png', name: raw.image_name || 'image.png' };
    if (raw.fields && typeof raw.fields === 'object') d.fields = raw.fields;
    return d;
}

// --- Blanks and template fields --------------------------------------------
// Stored like the server's template fields: {{blank:What}}. In text fields
// they are shown as [What], dates as [today], counters as [counter].

const BLANK_RE = /\{\{blank:([^}]+)\}\}/g;

// Dates on labels follow the browser's language: its order and separators
// as a strftime format for the server, e.g. %d.%m.%Y in German
function browserDateFormat() {
    try {
        const parts = new Intl.DateTimeFormat(navigator.language, { year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(2026, 10, 1));
        let f = '';
        for (const part of parts) {
            if (part.type === 'day') f += '%d';
            else if (part.type === 'month') f += '%m';
            else if (part.type === 'year') f += '%Y';
            else if (part.type === 'literal' && /^[\s./-]+$/.test(part.value)) f += part.value.replace(/\s+/g, ' ');
            else return '%Y-%m-%d';
        }
        return ['%d', '%m', '%Y'].every(x => f.includes(x)) ? f : '%Y-%m-%d';
    } catch (e) { return '%Y-%m-%d'; }
}
const dateFormat = () => (S.settings && S.settings.dateFmt) || browserDateFormat();
// A date like 2026-11-01 written in that format
function formatDate(iso, fmt = dateFormat()) {
    const [y, m, d] = iso.split('-');
    return fmt.replace('%Y', y).replace('%m', m).replace('%d', d);
}

function toDisplay(text) {
    return String(text || '')
        .replace(BLANK_RE, '[$1]')
        .replace(/\{\{datetime:[^}]+\}\}/g, '[today]')
        .replace(/\{\{counter(:\d+)?\}\}/g, (m, start) => `[counter${start || ''}]`)
        .replace(/\{\{short-uuid\}\}/g, '[id]');
}
function fromDisplay(text, previous = '') {
    // Each [today] keeps the format of the date it was shown for
    const formats = [...String(previous).matchAll(/\{\{datetime:([^}]+)\}\}/g)].map(m => m[1]);
    let k = 0;
    return String(text || '').replace(/\[([^\]\n]+)\]/g, (m, name) => {
        const n = name.trim();
        if (n.toLowerCase() === 'today') return `{{datetime:${formats[Math.min(k++, formats.length - 1)] || dateFormat()}}}`;
        const counter = n.match(/^counter(:\d+)?$/i);
        if (counter) return `{{counter${counter[1] || ''}}}`;
        if (n.toLowerCase() === 'id') return '{{short-uuid}}';
        return `{{blank:${n}}}`;
    });
}

function blanksOf(design) {
    const names = [];
    const scan = s => { for (const m of String(s || '').matchAll(BLANK_RE)) if (!names.includes(m[1])) names.push(m[1]); };
    (design.text || []).forEach(l => scan(l.text));
    if (design.print_type !== 'text' && design.print_type !== 'image') scan(design.code_text);
    return names;
}

// Settings of the blanks, stored with the label
const blankFields = item => (item.design && item.design.fields) || {};

// Kinds of blanks and the kind a name suggests
const BLANK_KINDS = [['text', 'Text'], ['number', 'Number'], ['date', 'Date']];
const guessKind = name => /\b(on|date)\b/i.test(name) ? 'date' : /^(box|number|portions|no\.?|count)$/i.test(name) ? 'number' : 'text';

function fieldType(item, name) {
    return (blankFields(item)[name] || {}).type || guessKind(name);
}

function fillBlanks(design, values, forPreview) {
    const d = clone(design);
    const sub = s => String(s || '').replace(BLANK_RE, (m, name) => {
        let v = values ? values[name] : undefined;
        if (v === undefined || v === '') return forPreview ? name + '?' : '';
        // Dates are written as set in the printer panel, text stays as typed
        if (/^\d{4}-\d{2}-\d{2}$/.test(v) && fieldType({ design }, name) === 'date') return formatDate(v);
        return v;
    });
    d.text = d.text.map(l => Object.assign(l, { text: sub(l.text) }));
    d.code_text = sub(d.code_text);
    return d;
}

// --- Talking to the server -------------------------------------------------

function b64ToBlob(b64, mime) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Blob([bytes], { type: mime });
}

// 600 dpi, if chosen and the printer can print it
const highRes = () => S.settings.quality === 'high' && !(S.printer && C.models_without_600_dpi.includes(S.printer.model));
// P-touch printers print at 180 dpi (section 2.3.1 of Brother's raster
// command reference of the PT-E550W, PT-P750W and PT-P710BT), QL printers
// at 300 dpi, or 600
const dpiOf = (labelSize, high = highRes()) => (isPtouch(labelSize) ? 180 : 300) * (high ? 2 : 1);

function formFor(design, copies = 1, forPrint = false) {
    const fd = new FormData();
    for (const [k, v] of Object.entries(design)) {
        if (k === 'text' || k === 'image' || k === 'more_images' || k === 'fields') continue;
        fd.append(k, typeof v === 'boolean' ? (v ? '1' : '0') : String(v));
    }
    // Without red on the tape red parts print black, the preview shows that
    const red = redTape(design);
    if (!red) {
        fd.set('border_color', 'black');
        fd.set('print_color', 'black');
        if (['red_and_black', 'colored'].includes(design.image_mode)) fd.set('image_mode', 'black_and_white');
    }
    fd.append('text', JSON.stringify(design.text.map(l => ({
        text: l.text, size: String(l.size), font: l.font, align: l.align, inverted: !!l.inverted,
        checkbox: !!l.checkbox, color: red ? l.color || 'black' : 'black', line_spacing: String(l.line_spacing || '100')
    }))));
    if (design.image && design.print_type === 'image') {
        // The preview shows the first image, all of them are printed
        for (const im of [design.image].concat(forPrint ? design.more_images || [] : [])) fd.append('image', b64ToBlob(im.data, im.mime), im.name);
    }
    fd.append('print_count', String(copies));
    // The quality is a setting of the printer, previews are made with its
    // resolution as well
    fd.set('high_res', highRes() ? '1' : '0');
    if (forPrint) {
        fd.set('fast_printing', S.settings.quality === 'fast' ? '1' : '0');
        fd.set('cut_once', S.settings.cut === 'last' ? '1' : '0');
    }
    if (S.printer && S.printer.path) fd.append('printer', S.printer.path);
    if (S.printer && S.printer.model) fd.append('model', S.printer.model);
    return fd;
}

async function post(path, body) {
    return fetch(API + path, { method: 'POST', body, headers: { 'X-Requested-With': 'XMLHttpRequest' } });
}

// Everything the server renders (previews and thumbnails) goes through one
// queue. The server says how many at a time: its CPU cores minus one, at
// most 16. Jobs that are no longer needed are dropped before they run.
const PARALLEL = Math.max(1, C.preview_parallel || 2);
const previewCache = new Map();
let running = 0;
const waiting = [];
function nextJob() { while (running < PARALLEL && waiting.length) waiting.shift()(); }
// A request is shared by everyone who asks for the same. Dropped because its
// first caller no longer needs it, it is made again for a caller who does.
function shared(p, stale, again) {
    return p.catch(e => { if (e.message === 'stale' && !(stale && stale())) return again(); throw e; });
}
function limit(task, stale) {
    return new Promise((resolve, reject) => {
        const run = () => {
            if (stale && stale()) { reject(new Error('stale')); return; }
            running++;
            task().then(resolve, reject).finally(() => { running--; nextJob(); });
        };
        if (running < PARALLEL) run(); else waiting.push(run);
    });
}

const imageCache = new Map();
function fetchImage(url, stale) {
    if (imageCache.has(url)) return shared(imageCache.get(url), stale, () => fetchImage(url, stale));
    const p = limit(async () => {
        const r = await fetch(url);
        if (!r.ok) throw new Error('The thumbnail could not be made');
        return URL.createObjectURL(await r.blob());
    }, stale);
    imageCache.set(url, p);
    p.catch(() => imageCache.delete(url));
    return p;
}

// A short fingerprint of an image for the cache key (FNV-1a)
function fingerprint(s) {
    let h = 0x811c9dc5;
    for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 0x01000193);
    return (h >>> 0).toString(36) + s.length.toString(36);
}

// Previews that dropped out of the cache are released once no image on the
// page shows them any more. Those dropped just now may still be on their
// way onto the page.
const dropped = new Map();
function release(url) {
    dropped.set(url, Date.now());
    const shown = new Set($$('img').map(i => i.src));
    for (const [u, t] of dropped) if (Date.now() - t > 10000 && !shown.has(u)) { URL.revokeObjectURL(u); dropped.delete(u); }
}

function preview(design, values, stale) {
    // In the resolution it is printed with: for 600 dpi the server makes the
    // label twice as large
    const d = Object.assign(fillBlanks(design, values, true), { high_res: highRes() ? '1' : '0' });
    const key = JSON.stringify(Object.assign({}, d, { image: d.image ? d.image.mime + fingerprint(d.image.data) : null, more_images: undefined }));
    if (previewCache.has(key)) {
        // The most recently used stay longest
        const p = previewCache.get(key);
        previewCache.delete(key);
        previewCache.set(key, p);
        return shared(p, stale, () => preview(design, values, stale));
    }
    const p = limit(async () => {
        const r = await post('/preview', formFor(d));
        if (!r.ok) {
            let msg = 'The preview could not be made';
            try { msg = (await r.json()).message || msg; } catch (e) { }
            throw new Error(msg);
        }
        const blob = await r.blob();
        return {
            url: URL.createObjectURL(blob), highRes: d.high_res === '1', warning: r.headers.get('X-Label-Warning') || '',
            auto: r.headers.get('X-Label-Orientation') === 'auto', gap: +(r.headers.get('X-Label-Image-Text-Gap') || 0)
        };
    }, stale);
    previewCache.set(key, p);
    p.catch(() => { if (previewCache.get(key) === p) previewCache.delete(key); });
    if (previewCache.size > 150) {
        const [oldKey, old] = previewCache.entries().next().value;
        previewCache.delete(oldKey);
        old.then(r => release(r.url), () => { });
    }
    return p;
}

// Thumbnails of labels that only exist in this browser are rendered when
// they come into view
const lazyObserver = new IntersectionObserver(entries => {
    for (const e of entries) {
        if (!e.isIntersecting) continue;
        lazyObserver.unobserve(e.target);
        const job = e.target._render;
        if (!job) continue;
        if (job.url) fetchImage(job.url).then(u => { e.target.src = u; }).catch(() => { });
        else if (job.item) typedThumb(e.target, job);
        else preview(job.design, job.values).then(p => { e.target.src = p.url; }).catch(() => { });
    }
}, { rootMargin: '200px' });
function attachLazy(root) {
    $$('img[data-lazy]', root).forEach(img => {
        const job = LAZY.get(img.dataset.lazy);
        if (job) { img._render = job; lazyObserver.observe(img); }
    });
}
const LAZY = new Map();
let lazySeq = 0;
function lazyTag(design, values) {
    const id = 'l' + (++lazySeq);
    LAZY.set(id, { design, values });
    return `<img data-lazy="${id}" alt="">`;
}
function lazyUrlTag(url) {
    const id = 'l' + (++lazySeq);
    LAZY.set(id, { url });
    return `<img data-lazy="${id}" alt="">`;
}
// A template with the text typed into the wizard, made when it comes into view
function lazyTypedTag(item) {
    const id = 'l' + (++lazySeq);
    LAZY.set(id, { item, gen: S.typedGen });
    return `<img data-lazy="${id}" alt="">`;
}
const designCache = new Map();
function designOf(item) {
    if (item.design) return Promise.resolve(item.design);
    const key = item.kind + ':' + item.id;
    if (!designCache.has(key)) {
        const p = itemDesign(item);
        designCache.set(key, p);
        p.catch(() => designCache.delete(key));
    }
    return designCache.get(key);
}
async function typedThumb(img, job) {
    const stale = () => job.gen !== S.typedGen;
    try {
        const design = clone(await designOf(job.item));
        if (stale()) return;
        // The list shows the blanks of a saved label once it is loaded
        const blanks = blanksOf(design);
        const sub = img.closest('.thumb') && img.closest('.thumb').querySelector('.s');
        if (sub && blanks.length && job.item.kind === 'saved') sub.textContent = 'Fill in: ' + blanks.join(', ');
        const t = applyTyped(job.item, design);
        const p = await preview(t.design, t.values, stale);
        if (!stale()) img.src = p.url;
    } catch (e) { }
}

// --- State -------------------------------------------------------------------

const S = {
    printers: [], printer: null, tape: C.default_label_size, tapeAssumed: true,
    demos: null, demoList: [], saved: [], barcodes: ['QR'],
    history: load('designer_history', []),
    lang: load('designer_lang', (navigator.language || 'en').toLowerCase().startsWith('de') ? 'de' : 'en'),
    fitsOnly: true,
    libCatChosen: false, typedGen: 0,
    // tapes: the tape set by hand for each printer
    settings: Object.assign({ quality: 'quality', cut: 'each', tapes: {}, dateFmt: '' }, load('designer_settings', {})),
    wizardText: '',
    libCat: 'saved', libSel: null,
    fillValues: {},
    edit: null, editSel: 0,
    hist: { undo: [], redo: [], last: null },
};

const saveHistory = () => {
    // Images make the history big: the last few jobs keep them, fewer if the
    // browser has no room for them
    S.history = S.history.slice(0, 60);
    // Jobs without their image cannot be printed again
    for (const keep of [9, 3, 1, 0]) {
        S.history.forEach((h, i) => {
            if (i >= keep && (h.design.image || h.design.more_images)) { delete h.design.image; delete h.design.more_images; h.noImage = true; }
        });
        if (storeQuiet('designer_history', S.history)) return;
    }
    store('designer_history', S.history);
};

// Wide labels, e.g. for a code next to text: endless tape rotated, die-cut
// labels in the standard orientation, which the server makes landscape
const isDieCut = id => !!SIZES[id] && !isEndless(SIZES[id]);
function setWide(d, wide) {
    d.orientation = wide === isDieCut(d.label_size) ? 'standard' : 'rotated';
    return d;
}
// Whether the text runs along the tape, as the editor shows it: standard
// puts it along the longer side of the label, which is across the tape on
// endless tape and e.g. on 62 x 29 mm labels
const longAlongTape = id => !!SIZES[id] && SIZES[id].tape[1] > SIZES[id].tape[0];
const runsAlong = d => (d.orientation === 'standard') === longAlongTape(d.label_size);
function setRunsAlong(d, along) {
    d.orientation = along === longAlongTape(d.label_size) ? 'standard' : 'rotated';
    return d;
}
// Another label size: die-cut labels start wide, on endless tape the text
// keeps its direction
function setSize(d, id) {
    const along = runsAlong(d);
    d.label_size = id;
    return isDieCut(id) ? setWide(d, true) : setRunsAlong(d, along);
}

function fitsTape(sizeId) {
    if (sizeId === S.tape) return true;
    const a = SIZES[sizeId], b = SIZES[S.tape];
    return !!(a && b && a.form === b.form && a.tape[0] === b.tape[0] && a.tape[1] === b.tape[1] && !a.red);
}

// --- Printer status ----------------------------------------------------------

async function refreshPrinters() {
    try {
        const r = await fetch(API + '/printer_status');
        const data = await r.json();
        S.printers = Array.isArray(data.printers) ? data.printers : [data];
        const chosen = load('designer_printer', null);
        S.printer = S.printers.find(p => p.path === chosen) || S.printers.find(p => p.path === data.selected) || S.printers[0] || null;
    } catch (e) {
        // A single failed poll keeps what is known, the next one corrects it
        if (!S.printers.length) S.printer = null;
    }
    detectTape();
    renderPrinterChip();
}

const printerKey = () => (S.printer && S.printer.path) || '';
const manualTape = () => (S.settings.tapes || {})[printerKey()] || '';

// Whether the printer takes a label size, any size if its model is unknown
function printerTakes(s) {
    const model = S.printer && S.printer.model;
    const known = model && C.label_sizes.some(x => x.models.includes(model));
    return known ? s.models.includes(model) : s.models.length > 0;
}

function detectTape() {
    const p = S.printer;
    if (SIZES[manualTape()]) {
        S.tape = manualTape();
        S.tapeAssumed = false;
        S.tapeManual = true;
        return;
    }
    S.tapeManual = false;
    let found = null;
    if (p && p.media_width) {
        // The width and length of DK and TZe tape can be the same
        const red = /red/i.test(p.text_color || '') || /red/i.test(p.media_type || '');
        found = C.label_sizes.find(s => printerTakes(s) && s.tape[0] === p.media_width && s.tape[1] === (p.media_length || 0) && !!s.red === red);
    }
    S.tape = found ? found.id : C.default_label_size;
    S.tapeAssumed = !found;
}

function printerState(p) {
    if (!p) return { cls: 'off', text: 'No printer' };
    if (p.errors && p.errors.length) return { cls: 'bad', text: p.errors[0] };
    if (/simulat/i.test(p.status_type || p.phase_type || '')) return { cls: '', text: 'Ready (simulated)' };
    return { cls: '', text: 'Ready' };
}

function renderPrinterChip() {
    const chip = $('#printerChip');
    const st = printerState(S.printer);
    chip.innerHTML = `<span class="dot ${st.cls}"></span><b>${esc(S.printer ? S.printer.model : 'No printer')}</b><span class="tape">${esc(sizeName(S.tape))}</span>`;
    chip.title = st.text;
}

// --- Printing ------------------------------------------------------------------

async function printJob(job) {
    // job: { title, design, values, copies, source }
    // A copy: printing a job again passes its design, which the history may
    // strip of its image before the request is sent
    job = Object.assign({}, job, { design: clone(job.design) });
    if (!fitsTape(job.design.label_size)) {
        const choice = await tapeGuard(job);
        if (choice === 'cancel') return false;
        if (choice === 'adapt') {
            job = Object.assign({}, job, { design: setSize(clone(job.design), S.tape) });
        }
    }
    const entry = {
        id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), time: Date.now(), title: job.title,
        design: clone(job.design), values: Object.assign({}, job.values || {}), copies: job.copies || 1,
        source: job.source || null, printer: S.printer ? S.printer.model : '', state: 'printing', message: ''
    };
    S.history.unshift(entry);
    saveHistory();
    updateCounts();
    toast(`Printing ${entry.copies} ${entry.copies === 1 ? 'label' : 'labels'}...`);
    try {
        const r = await post('/print', formFor(fillBlanks(job.design, job.values, false), entry.copies, true));
        let data = {};
        try { data = await r.json(); } catch (e) { }
        if (r.ok && data.success !== false) {
            entry.state = 'printed';
        } else {
            entry.state = 'failed';
            entry.message = data.message || ('The printer answered ' + r.status);
        }
    } catch (e) {
        entry.state = 'failed';
        entry.message = 'The label designer could not be reached';
    }
    saveHistory();
    if ($('#libGrid')) {
        if (S.libCat === 'printed') renderLibGrid();
        if (!S.libSel) emptyDetail();
    }
    announce(entry);
    return entry.state === 'printed';
}

function announce(entry) {
    const n = entry.copies;
    const what = `${n} ${n === 1 ? 'label' : 'labels'}`;
    if (entry.state === 'printed') {
        if (window.matchMedia('(max-width: 760px)').matches) {
            sheet(`<div class="okc"><i class="fa-solid fa-check"></i></div><h2>${what} printed</h2>
                <p>${esc(entryName(entry))} on the ${esc(entry.printer || 'printer')}</p>
                <button class="btn-print" data-close><i class="fa-solid fa-check"></i>Done</button>`);
        } else {
            toast(`Printed ${what}: ${entryName(entry)}`, { label: 'Print again', run: () => again(entry) });
        }
    } else {
        sheet(`<div class="okc bad"><i class="fa-solid fa-xmark"></i></div><h2>Not printed</h2><p>${esc(entry.message)}</p>
            <button class="btn-print" id="sheetRetry"><i class="fa-solid fa-rotate-right"></i>Try again</button>
            <button class="btn" data-close>Close</button>`);
        $('#sheetRetry').onclick = () => { closeLayer(); again(entry); };
    }
}

function entryName(e) {
    const blanks = blanksOf(e.design);
    const first = blanks.length ? (e.values || {})[blanks[0]] : '';
    if (first) return first;
    if (!blanks.length && e.source && e.source.kind !== 'wizard' && e.title) return e.title;
    const line = (e.design.text || []).map(l => toDisplay(l.text)).find(t => t.trim());
    return line || e.title || 'Label';
}

const NO_IMAGE = 'Its image was not kept, open it in the editor to add it again';
function again(entry) {
    if (entry.noImage) { toast(NO_IMAGE); return; }
    printJob({ title: entry.title, design: entry.design, values: entry.values, copies: entry.copies, source: entry.source });
}

function tapeGuard(job) {
    return new Promise(resolve => {
        const need = sizeName(job.design.label_size);
        const have = sizeName(S.tape);
        const canAdapt = SIZES[job.design.label_size] && SIZES[S.tape];
        sheet(`<div class="okc warn"><i class="fa-solid fa-ruler-combined"></i></div>
            <h2>This label needs ${esc(need)}</h2>
            <p>The ${esc(S.printer ? S.printer.model : 'printer')} has ${esc(have)} loaded${S.tapeManual ? ' (set in the printer settings)' : S.tapeAssumed ? ' (assumed, the printer does not say)' : ''}.</p>
            ${canAdapt ? `<button class="btn-print" data-r="adapt"><i class="fa-solid fa-maximize"></i>Print it on ${esc(have)}</button>` : ''}
            <button class="btn" data-r="anyway">Print anyway</button>
            <button class="btn quiet" data-r="cancel">Cancel</button>`, () => resolve('cancel'));
        $$('[data-r]', $('#layer')).forEach(b => b.onclick = () => { const r = b.dataset.r; closeLayer(true); resolve(r); });
    });
}

// --- Overlays ------------------------------------------------------------------

let onLayerClose = null;
function openLayer(html, cls, onClose) {
    const layer = $('#layer');
    layer.className = 'layer ' + (cls || '');
    layer.innerHTML = html;
    layer.hidden = false;
    onLayerClose = onClose || null;
    layer.onclick = e => { if (e.target === layer || e.target.closest('[data-close]')) closeLayer(); };
}
function closeLayer(silent) {
    const layer = $('#layer');
    if (layer.hidden) return;
    layer.hidden = true;
    layer.innerHTML = '';
    const cb = onLayerClose;
    onLayerClose = null;
    if (cb && !silent) cb();
}
function sheet(inner, onClose) {
    openLayer(`<div class="sheet"><div class="grab"></div>${inner}</div>`, 'sheet-layer', onClose);
}

// --- Data loading --------------------------------------------------------------

async function loadDemos() {
    try {
        const r = await fetch(API + '/demos/list');
        const data = await r.json();
        S.demos = data.categories || [];
        S.demoList = [];
        for (const c of S.demos) for (const l of c.labels) S.demoList.push(Object.assign({ cat: c.id, catName: c.name }, l));
    } catch (e) { S.demos = []; }
}
async function loadSaved() {
    try {
        const r = await fetch(API + '/repository/list');
        const data = await r.json();
        S.saved = (data.files || []).map(f => ({ name: f.name, title: f.title || f.name.replace(/\.json$/i, '').replace(/[-_]/g, ' '), size: f.label_size_id, mtime: f.mtime }));
    } catch (e) { S.saved = []; }
    updateCounts();
}
// The numbers next to "Saved labels" and "Printed" in the wizard
function updateCounts() {
    for (const [cat, n] of [['saved', S.saved.length], ['printed', S.history.length]]) {
        const el = $(`.lib-nav [data-cat="${cat}"] .n`);
        if (el) el.textContent = n;
    }
}
// The file name the server gives a label (like werkzeug's secure_filename)
function serverName(title) {
    const base = String(title).normalize('NFKD').replace(/[^\x00-\x7F]/g, '').replace(/[^\w\s.-]/g, '').trim().replace(/\s+/g, '_').replace(/^[._]+|[._]+$/g, '');
    return (base || 'label') + '.json';
}
async function saveLabel(title, design, overwrite = null) {
    let name = overwrite;
    if (!name) {
        name = serverName(title);
        for (let n = 2; S.saved.some(x => x.name.toLowerCase() === name.toLowerCase()); n++) name = serverName(`${title} ${n}`);
    }
    // The name that was checked above, with ".json": the server only adds it
    // when it is missing, "a.json" would replace a.json instead of a.json.json
    const body = Object.assign({}, design, { name, title, text: design.text });
    if (design.fields) body.fields = Object.fromEntries(Object.entries(design.fields).filter(([k]) => blanksOf(design).includes(k)));
    delete body.image;
    delete body.more_images;
    if (design.image) Object.assign(body, { image_data: design.image.data, image_mime: design.image.mime, image_name: design.image.name });
    const r = await fetch(API + '/repository/save', { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' } });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.success) throw new Error(data.message || 'The label could not be saved');
    forget(data.name);
    await loadSaved();
    return data.name;
}
async function deleteLabel(name) {
    const r = await post('/repository/delete', (() => { const f = new FormData(); f.append('name', name); return f; })());
    if (!r.ok) throw new Error('The label could not be removed');
    forget(name);
    await loadSaved();
}
// A saved label that changed is loaded again where it is shown next
function forget(name) {
    designCache.delete('saved:' + name);
    if (S.libSel && S.libSel.kind === 'saved' && S.libSel.id === name) delete S.libSel.design;
}
async function loadBarcodes() {
    try {
        const r = await fetch(API + '/barcodes');
        const data = await r.json();
        if (Array.isArray(data.barcodes) && data.barcodes.length) S.barcodes = data.barcodes;
    } catch (e) { }
}

// An "item" is anything that can be printed: one of your labels, a saved
// label on the server, a ready-made label or a past print
async function itemDesign(item) {
    if (item.design) return item.design;
    let url;
    if (item.kind === 'demo') url = `${API}/demos/load?id=${encodeURIComponent(item.id)}`;
    else if (item.kind === 'saved') url = `${API}/repository/load?name=${encodeURIComponent(item.id)}`;
    const r = await fetch(url);
    if (!r.ok) throw new Error('The label could not be loaded');
    item.design = normalize(await r.json());
    return item.design;
}
const demoItem = d => ({ kind: 'demo', id: d.id, title: d.title, size: d.label_size_id, sub: d.catName });
const savedItem = s => ({ kind: 'saved', id: s.name, title: s.title, size: s.size, sub: 'Saved label' });
const histItem = h => ({ kind: 'printed', id: h.id, title: entryName(h), design: h.design, values: h.values, size: h.design.label_size, sub: h.state === 'failed' ? `Failed: ${h.message}` : `${h.copies} x, ${fmtTime(h.time)}`, entry: h });

function thumbSrc(item) {
    if (item.kind === 'demo') return lazyUrlTag(`${API}/demos/preview?id=${encodeURIComponent(item.id)}`);
    // Saved labels may have blanks, they are drawn here with the blank's name
    if (item.kind === 'saved') return lazyTypedTag(item);
    return lazyTag(item.design, item.values || defaults(item));
}

// The text typed into the wizard goes into the blanks of a template, or
// into its lines if it has no blanks
function applyTyped(item, design, values) {
    values = values || defaults(Object.assign({}, item, { design }));
    const typed = S.wizardText.split('\n').map(t => t.trim()).filter(Boolean);
    if (typed.length && ['demo', 'saved'].includes(item.kind)) {
        const blanks = blanksOf(design);
        if (blanks.length) {
            let k = 0;
            for (const b of blanks) if (fieldType(item, b) === 'text' && k < typed.length) values[b] = typed[k++];
        } else {
            typed.forEach((t, i) => { if (design.text[i]) design.text[i].text = fromDisplay(t, design.text[i].text); });
        }
    }
    return { design, values };
}

function defaults(item) {
    const v = {};
    for (const name of blanksOf(item.design)) {
        const t = fieldType(item, name);
        const f = blankFields(item)[name] || {};
        v[name] = t === 'date' ? today() : (f.default || '');
    }
    return v;
}

// --- Rows that scroll sideways ---------------------------------------------------
// They fade out where more follows, and peek once after loading, so that
// nobody misses the hidden part

const hinted = new Set();
function hintScrollers(root) {
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    for (const el of $$('.lib-nav', root)) {
        const cs = getComputedStyle(el);
        if (!/(auto|scroll)/.test(cs.overflowX) || el.scrollWidth <= el.clientWidth + 4) continue;
        const edges = () => {
            el.classList.toggle('more-left', el.scrollLeft > 4);
            el.classList.toggle('more-right', el.scrollLeft + el.clientWidth < el.scrollWidth - 4);
        };
        edges();
        el.addEventListener('scroll', edges, { passive: true });
        const key = el.className.split(' ')[0];
        if (reduced || hinted.has(key) || el.scrollLeft > 4) continue;
        hinted.add(key);
        setTimeout(() => {
            el.scrollTo({ left: Math.min(72, el.scrollWidth - el.clientWidth), behavior: 'smooth' });
            setTimeout(() => el.scrollTo({ left: 0, behavior: 'smooth' }), 550);
        }, 700);
    }
}

// --- Routing -------------------------------------------------------------------

function parseHash() {
    const raw = location.hash.replace(/^#/, '') || 'edit';
    const [path, query] = raw.split('?');
    const parts = path.split('/');
    const params = new URLSearchParams(query || '');
    return { view: parts[0], arg: parts.slice(1).join('/'), params };
}
const currentView = () => parseHash().view;

function route() {
    closeLayer(true);
    const { view, arg, params } = parseHash();
    const isWizard = ['wizard', 'templates', 'labels', 'fill', 'printer'].includes(view);
    $$('#tabs a').forEach(a => a.classList.toggle('on', a.dataset.tab === (isWizard ? 'wizard' : 'edit')));
    const main = $('#view');
    main.scrollTop = 0;
    if (view === 'fill') openFill(decodeURIComponent(arg), params);
    else if (view === 'printer') { renderLabels(''); openPrinterPanel(); }
    else if (isWizard) renderLabels(arg);
    else renderEditor();
    requestAnimationFrame(() => hintScrollers(main));
}

// --- Stage -----------------------------------------------------------------------

// A small spinner in the corner of the stage while a new preview is coming
const SPIN = '<span class="spin" aria-hidden="true"><i class="fa-solid fa-circle-notch"></i></span>';
function stageHTML(id) {
    return `<div class="stage busy" id="${id}">${SPIN}<div class="wait">Making the preview...</div></div>`;
}
// Shows the spinner at once, while a debounced update still waits. A preview
// still on its way is not shown any more, it is out of date.
function busyThen(getEl, fn) {
    return (...args) => {
        const el = getEl();
        if (el) { el._seq = (el._seq || 0) + 1; el.classList.add('busy'); }
        fn(...args);
    };
}

async function showOnStage(el, design, values, onWarning, blank = false) {
    if (!el) return;
    el._seq = (el._seq || 0) + 1;
    const seq = el._seq;
    // A newer preview was asked for, or the stage was replaced meanwhile
    const outdated = () => seq !== el._seq || !el.isConnected;
    el.classList.add('busy');
    let p;
    try {
        p = await preview(design, values);
    } catch (e) {
        if (outdated()) return;
        el.className = 'stage';
        el.innerHTML = `${SPIN}<div class="err"><i class="fa-solid fa-triangle-exclamation"></i> ${esc(e.message)}</div>`;
        if (onWarning) onWarning(e.message, true);
        return;
    }
    if (outdated()) return;
    const img = new Image();
    img.src = p.url;
    try { await img.decode(); } catch (e) { }
    if (outdated()) return;
    const size = SIZES[design.label_size] || { tape: [62, 0], dots: [696, 0], form: 'endless' };
    // At 600 dpi the label has twice as many dots each way
    const scale = p.highRes ? 2 : 1, dpi = dpiOf(design.label_size, p.highRes);
    const w = img.naturalWidth, h = img.naturalHeight, span = size.dots[0] * scale;
    const side = Math.abs(w - span) > 4 * scale && Math.abs(h - span) <= 4 * scale;
    const along = Math.round((side ? w : h) / dpi * 25.4);
    const lengthMm = isEndless(size) ? along : size.tape[1];
    // Fit the label into the stage
    const box = el.getBoundingClientRect();
    // The printer takes room on top, or on the left for labels that leave it sideways
    const maxW = Math.max(140, box.width - 110 - (side ? 70 : 0)), maxH = Math.max(110, Math.min(440, window.innerHeight * .5) - (side ? 0 : 60));
    const k = Math.min(0.85, maxW / w, maxH / h);
    const dw = Math.round(w * k), dh = Math.round(h * k);
    // Short labels keep the arrow clear of the scissors at the cut
    const short = side ? dw : dh;
    el.className = 'stage' + (side ? ' side' : '') + (isEndless(size) ? '' : ' diecut') + (size.form === 'round_die_cut' ? ' round' : '')
        + (short < 30 ? ' tiny' : short < 50 ? ' short' : '');
    // The front of the printer with its exit slot, its name and status light
    const st = printerState(S.printer);
    const led = `<span class="led ${st.cls}" title="${esc(st.text)}"></span>`;
    const model = `<span class="model">${esc(S.printer ? S.printer.model : 'Printer')}</span>`;
    const printer = side
        ? `<div class="printer side" style="height:${dh + 64}px" aria-hidden="true">${led}${model}<span class="mouth" style="height:${dh + 16}px"></span></div>`
        : `<div class="printer top" style="width:${dw + 72}px" aria-hidden="true">${led}${model}<span class="mouth" style="width:${dw + 16}px"></span></div>`;
    const flow = `<div class="flow" title="Printing direction"><i class="fa-solid fa-${side ? 'arrow-right-long' : 'arrow-down-long'}"></i></div>`;
    const paper = blank ? `<div class="blank-paper" style="aspect-ratio:${w} / ${h}"></div>` : `<img alt="Label preview" src="${p.url}">`;
    el.innerHTML = `${SPIN}${printer}<div class="feed" style="width:${dw}px"><div class="paper">${paper}</div>${flow}
        <div class="cut"><i class="fa-solid fa-scissors"></i></div>
        <div class="dim-x">${side ? lengthMm : size.tape[0]} mm</div><div class="dim-y">${side ? size.tape[0] : lengthMm} mm</div></div>`;
    if (onWarning) onWarning(p.warning, false, p);
}

// The engine stacks code and text on die-cut labels, small ones leave the
// text no room and it disappears without a warning
function layoutProblem(d) {
    const size = SIZES[d.label_size];
    if (d && size && !isEndless(size) && d.print_type === 'qrcode_text' && Math.min(size.dots[0], size.dots[1]) < 600) {
        return 'On this die-cut label the text gets no room next to the code. Use endless tape or a larger label.';
    }
    const redParts = (d.print_type !== 'qrcode' && d.text.some(l => l.color === 'red')) || (d.border_color === 'red' && frameOf(d) !== 'none')
        || (d.print_type === 'image' && ['red_and_black', 'colored'].includes(d.image_mode));
    if (redParts && !redTape(d)) return 'This tape has no red, the red parts print black.';
    return '';
}

function fitsNote(el, warning, error, design, onShrink) {
    if (!el) return;
    if (!warning && !error && design) warning = layoutProblem(design);
    if (error) { el.className = 'fits warn'; el.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i> Fix the label to print it.'; return; }
    el.className = warning ? 'fits warn' : 'fits';
    el.innerHTML = warning ? `<i class="fa-solid fa-triangle-exclamation"></i> ${esc(warning)}` : '<i class="fa-solid fa-circle-check"></i> Everything fits.';
    if (warning && /text does not fit/i.test(warning) && design && design.shrink_text !== '1' && onShrink) {
        el.insertAdjacentHTML('beforeend', ' <button class="btn sm" type="button">Shrink it to fit</button>');
        el.querySelector('button').onclick = () => { design.shrink_text = '1'; onShrink(); };
    }
}

// --- Wizard ----------------------------------------------------------------------

const LOOKS = [
    {
        name: 'Big and simple', make: (lines, d) => { d.text = lines.map((t, i) => L(t, i ? 44 : 84, i === 0)); }
    },
    {
        name: 'With date', make: (lines, d) => { d.text = lines.map((t, i) => L(t, i ? 40 : 70, i === 0)).concat([L(`{{datetime:${dateFormat()}}}`, 34)]); }
    },
    {
        name: 'White on black', make: (lines, d) => {
            d.text = lines.length === 1 ? [L(` ${lines[0]} `, 76, true, 'center', { inverted: true })]
                : [L(` ${lines[0]} `, 44, true, 'center', { inverted: true })].concat(lines.slice(1).map(t => L(t, 64, true)));
        }
    },
    {
        name: 'Framed', make: (lines, d) => {
            d.text = lines.map((t, i) => L(t, i ? 36 : 62, i === 0));
            Object.assign(d, { border_thickness: '5', border_roundness: '24', border_distance_x: '20', border_distance_y: '14', margin_top: '40', margin_bottom: '40', margin_left: '50', margin_right: '50' });
        }
    },
    {
        name: 'With details', make: (lines, d) => {
            d.text = lines.map((t, i) => L(t, i ? 34 : 64, i === 0, 'left'));
            if (lines.length === 1) d.text.push(L(`{{datetime:${dateFormat()}}}`, 34, false, 'left'));
        }
    },
    {
        name: 'With QR code', make: (lines, d) => {
            // Without content of its own the code holds the printed text,
            // with the same date, counter and ID
            Object.assign(d, { print_type: 'qrcode_text', code_text: '', qrcode_size: '14', margin_left: '30', margin_right: '30' });
            setWide(d, true);
            d.text = lines.map((t, i) => L(t, i ? 60 : 100, i === 0, 'left'));
        }
    }
];

function wizardLines() {
    return S.wizardText.split('\n').map(t => t.trim()).filter(Boolean).map(t => fromDisplay(t));
}
function lookDesign(i) {
    const d = baseDesign(S.tape);
    LOOKS[i].make(wizardLines(), d);
    return d;
}

// The symbols of the symbol dialog
const STATIC = ($('link[href*="designer/designer.css"]') || { getAttribute: () => '/static/designer/designer.css' }).getAttribute('href').replace(/designer\/designer\.css.*$/, '');
let symbolList = null;
async function loadSymbols() {
    if (symbolList) return symbolList;
    try {
        symbolList = await (await fetch(STATIC + 'designer/symbols.json')).json();
    } catch (e) { return []; }
    return symbolList;
}
// Material Design Icons for {{mdi:<name>}}: their names and characters
let iconList = null;
async function loadIcons() {
    if (iconList) return iconList;
    try {
        const data = await (await fetch(STATIC + 'mdi/icons.json')).json();
        iconList = Object.entries(data.icons).map(([name, code]) => ({ name, char: String.fromCodePoint(parseInt(code, 16)) }));
    } catch (e) { return []; }
    return iconList;
}
// Icons whose names contain all words of the search, exact and leading
// matches first. Without a search all icons, by name.
function findIcons(list, query) {
    const words = query.toLowerCase().replace(/^mdi:/, '').split(/[\s:]+/).filter(Boolean);
    if (!words.length) return list;
    const rank = i => i.name === words.join('-') ? 0 : i.name.startsWith(words[0]) ? 1 : 2;
    return list.filter(i => words.every(w => i.name.includes(w)))
        .sort((a, b) => rank(a) - rank(b) || a.name.length - b.name.length || a.name.localeCompare(b.name));
}

async function openSymbols(input) {
    if (!input) return;
    const [list, icons] = await Promise.all([loadSymbols(), loadIcons()]);
    openLayer(`<div class="dialog narrow${icons.length ? ' symbols' : ''}" role="dialog" aria-label="Insert a symbol">
        <div class="dialog-h"><div class="grow"><h2>Insert a symbol</h2><p>Not every font has every symbol, the preview shows what prints.</p></div>
            <button class="btn quiet icon" type="button" data-close title="Close"><i class="fa-solid fa-xmark"></i></button></div>
        <div class="sym-grid">${list.map((x, i) => `<button type="button" class="sym" data-sym="${i}">${esc(x)}</button>`).join('')}</div>
        ${icons.length ? `<div class="sym-icons"><h3>Icons <span class="muted small">Material Design Icons, in every font</span></h3>
            <input class="field" type="search" id="icoQ" placeholder="Search, e.g. power or lightbulb" autocomplete="off" aria-label="Search the icons">
            <div class="sym-grid" id="icoGrid"></div>
            <p class="muted small" id="icoHint" style="margin:0"></p>
            <button class="btn sm" type="button" id="icoAll" style="align-self:flex-start" hidden></button></div>` : ''}</div>`);
    $$('[data-sym]').forEach(b => b.onclick = () => { closeLayer(true); insertAt(input, list[+b.dataset.sym], false); });
    if (!icons.length) return;
    // The first ones, all of them on request
    const draw = all => {
        const found = findIcons(icons, $('#icoQ').value);
        const shown = all ? found : found.slice(0, 120);
        $('#icoGrid').innerHTML = shown.map(i => `<button type="button" class="sym ico" data-ico="${esc(i.name)}" title="mdi:${esc(i.name)}">${i.char}</button>`).join('');
        $('#icoHint').innerHTML = found.length ? 'Inserts the name, e.g. <code>{{mdi:power}}</code>. Point at an icon to see its name.' : 'No icon found.';
        $('#icoAll').hidden = shown.length >= found.length;
        $('#icoAll').textContent = `Show all ${found.length} icons`;
    };
    draw(false);
    $('#icoQ').addEventListener('input', () => draw(false));
    $('#icoAll').onclick = () => draw(true);
    $('#icoGrid').onclick = e => {
        const b = e.target.closest('[data-ico]');
        if (b) { closeLayer(true); insertAt(input, `{{mdi:${b.dataset.ico}}}`, false); }
    };
}

function varsHelpHTML() {
    return `<p>Variables put content into the label when it is printed. Type them into a line, or insert them with the buttons.</p>
        <ul>
            <li><code>[Name]</code> - a blank: whoever prints the label fills it in, in the editor or in the wizard. A name ending in "on" or containing "date" (like <code>[Frozen on]</code>) asks for a date, <code>[Box]</code>, <code>[Number]</code>, <code>[Portions]</code> and <code>[Count]</code> ask for a number, every other name for text.</li>
            <li><code>[today]</code> - today's date, in the date format chosen in the printer settings (like the browser by default).</li>
            <li><code>[counter]</code> - a number that counts up, each copy gets the next one.</li>
            <li><code>[id]</code> - a short random ID.</li>
        </ul>
        <p>The variables of the server work as well:</p>
        <ul>
            <li><code>{{datetime:&lt;format&gt;}}</code> - the current date and time. The format uses <a href="https://strftime.org/" target="_blank" rel="noopener">strftime</a> codes, e.g. <code>%H:%M:%S %d.%m.%Y</code>.</li>
            <li><code>{{counter[:&lt;start&gt;]}}</code> - a number that counts up, starting at &lt;start&gt; (default 1). When you print several labels at once, each gets the next number.</li>
            <li><code>{{uuid}}</code> - a random UUID, <code>{{short-uuid}}</code> - a shorter random UUID.</li>
            <li><code>{{mdi:&lt;name&gt;}}</code> - the <a href="https://pictogrammers.com/library/mdi/" target="_blank" rel="noopener">Material Design Icon</a> &lt;name&gt;, e.g. <code>{{mdi:lightbulb}}</code>. The Symbol button finds the names.</li>
            <li><code>{{env:&lt;var&gt;}}</code> - the environment variable &lt;var&gt;, if it is listed in <code>LABEL_TEMPLATE_ENV_VARS</code>.</li>
            <li><code>{{random[:&lt;len&gt;][:shift]}}</code> - a random string of &lt;len&gt; characters (default 64, at most 10000). <code>shift</code> shifts it around to fill gaps.</li>
        </ul>`;
}
function toggleVars(btn, box) {
    S.tplInfo = !S.tplInfo;
    box.hidden = !S.tplInfo;
    btn.setAttribute('aria-expanded', String(S.tplInfo));
    btn.classList.toggle('on', S.tplInfo);
}

function wizardChips() {
    return `<div class="adds">
        <button class="pill" type="button" data-ins="[today]"><i class="fa-regular fa-calendar"></i>Today's date</button>
        <button class="pill" type="button" data-ins="[counter]"><i class="fa-solid fa-arrow-up-1-9"></i>Counter</button>
        <button class="pill" type="button" id="wzCode"><i class="fa-solid fa-qrcode"></i>QR code or barcode</button>
        <button class="pill" type="button" id="wzImage"><i class="fa-regular fa-image"></i>Image or PDF</button>
        <button class="pill" type="button" id="wzSym"><i class="fa-solid fa-icons"></i>Symbol</button>
        <button class="pill${S.tplInfo ? ' on' : ''}" type="button" id="wzVars" aria-expanded="${!!S.tplInfo}" aria-controls="wzVarsBox"><i class="fa-solid fa-circle-info"></i>Variables</button>
    </div>
    <div class="info-box" id="wzVarsBox"${S.tplInfo ? '' : ' hidden'}>${varsHelpHTML()}</div>`;
}
function bindWizardChips(ta) {
    $$('[data-ins]').forEach(b => b.onclick = () => insertAt(ta, b.dataset.ins));
    $('#wzSym').onclick = () => openSymbols(ta);
    $('#wzVars').onclick = () => toggleVars($('#wzVars'), $('#wzVarsBox'));
    $('#wzCode').onclick = () => openEditor({ title: 'New label', design: Object.assign(lookDesign(5), { text: wizardLines().length ? lookDesign(5).text : [L('Text', 100, true, 'left')], code_text: wizardLines().length ? '' : 'https://example.com' }) });
    $('#wzImage').onclick = () => openEditor({ title: 'New label', design: setWide(Object.assign(baseDesign(S.tape), { print_type: 'image', text: wizardLines().map((t, i) => L(t, i ? 60 : 90, i === 0, 'left')) }), true) });
}

function insertAt(input, text, padded = true) {
    const s = input.selectionStart ?? input.value.length, e = input.selectionEnd ?? s;
    const before = input.value.slice(0, s);
    const pad = padded && before && !/\s$/.test(before) ? ' ' : '';
    input.value = before + pad + text + input.value.slice(e);
    input.focus();
    const pos = (before + pad + text).length;
    input.setSelectionRange(pos, pos);
    input.dispatchEvent(new Event('input'));
}

function renderLooks() {
    const box = $('#fzLooks');
    if (!box) return;
    if (!wizardLines().length) { box.innerHTML = ''; return; }
    box.innerHTML = `<div class="sec-h"><h2>Your text in six looks</h2><span class="muted">for the ${esc(sizeName(S.tape))} tape</span></div>
        <div class="tiles compact">${LOOKS.map((look, i) => `
            <div class="tile" data-look="${i}" role="button" tabindex="0">
                <div class="thumb"><div class="bed" id="flook${i}"></div></div>
                <div class="foot"><div class="grow"><b>${esc(look.name)}</b></div>
                    <button class="btn-print sm" type="button" data-plook="${i}" title="Print"><i class="fa-solid fa-print"></i></button></div>
            </div>`).join('')}</div>`;
    LOOKS.forEach((look, i) => {
        const bed = $('#flook' + i);
        const gen = S.typedGen;
        preview(lookDesign(i), {}, () => gen !== S.typedGen).then(p => { bed.innerHTML = `<img alt="" src="${p.url}">`; }).catch(() => { });
    });
    $$('[data-plook]', box).forEach(b => b.onclick = () => printLook(+b.dataset.plook));
    $$('[data-look]', box).forEach(t => {
        t.onclick = e => { if (!e.target.closest('button')) chooseLook(+t.dataset.look); };
        t.onkeydown = e => {
            if (e.target === t && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); chooseLook(+t.dataset.look); }
        };
    });
}

// A look is shown next to the list, with fields for its blanks
function chooseLook(i) {
    $$('#fzLooks [data-look]').forEach(x => x.classList.toggle('sel', +x.dataset.look === i));
    $$('#libGrid .thumb').forEach(x => x.classList.remove('sel'));
    return showDetail({ kind: 'look', id: 'look' + i, title: LOOKS[i].name, design: lookDesign(i), size: S.tape });
}

function printLook(i) {
    const lines = wizardLines();
    const design = lookDesign(i);
    // Blanks are filled in first
    if (blanksOf(design).length) {
        chooseLook(i).then(() => { const f = $('#libFields input.need'); if (f) f.focus(); });
        return;
    }
    printJob({ title: toDisplay(lines[0] || 'Label'), design, values: {}, copies: 1, source: { kind: 'wizard', look: LOOKS[i].name } });
}

function againCard(n) {
    const rows = S.history.slice(0, n);
    return `<div class="card hist"><h2>Print again</h2>${rows.length ? rows.map(h => `
        <div class="hr"><div class="mini-bed">${lazyTag(h.design, h.values)}</div>
            <div><b>${esc(entryName(h))}</b><span>${h.state === 'failed' ? 'Failed ' + esc(fmtTime(h.time)) : `${h.copies} ${h.copies === 1 ? 'copy' : 'copies'}, ${esc(fmtTime(h.time))}`}</span></div>
            <button class="btn quiet icon" type="button" data-again="${h.id}" title="${h.noImage ? NO_IMAGE : 'Print again'}"${h.noImage ? ' disabled' : h.state === 'failed' ? ' style="color:var(--bad)"' : ''}><i class="fa-solid fa-rotate-right"></i></button></div>`).join('')
        : '<p class="empty small">Labels you print show up here, ready to print again.</p>'}</div>`;
}
function bindAgain(root) {
    $$('[data-again]', root).forEach(b => b.onclick = () => {
        const h = S.history.find(x => x.id === b.dataset.again);
        if (h) again(h);
    });
}

// --- Labels ------------------------------------------------------------------------

const CAT_ICONS = {
    address: 'truck', mail: 'envelope', signs: 'triangle-exclamation', doors: 'door-closed', visitors: 'id-badge',
    names: 'user', prices: 'tag', hazards: 'skull-crossbones', lab: 'vial', health: 'kit-medical', workshop: 'screwdriver-wrench',
    office: 'briefcase', storage: 'box', kitchen: 'utensils', garden: 'seedling', events: 'gift', school: 'graduation-cap', letters: 'font'
};

function libItems(cat) {
    let items;
    const demos = S.demoList.filter(d => d.language === S.lang);
    if (cat === 'saved') items = S.saved.map(savedItem);
    else if (cat === 'printed') items = S.history.slice(0, 40).map(histItem);
    else if (cat === 'all') items = demos.map(demoItem);
    else items = demos.filter(d => d.cat === cat).map(demoItem);
    if (S.fitsOnly && !['printed', 'saved'].includes(cat)) {
        // Labels for other tapes come last, dimmed
        items = items.filter(i => fitsTape(i.size)).concat(items.filter(i => !fitsTape(i.size)));
    }
    return items;
}

function renderLabels(arg) {
    if (arg) S.libCat = arg;
    S.libCatChosen = !!arg;
    const cats = S.demos || [];
    const count = id => S.demoList.filter(d => d.language === S.lang && d.cat === id && (!S.fitsOnly || fitsTape(d.label_size_id))).length;
    $('#view').innerHTML = `<div class="lib">
        <nav class="lib-nav" aria-label="Label groups">
            <div class="h">Yours</div>
            ${navBtn('saved', 'server', 'Saved labels', S.saved.length)}
            ${navBtn('printed', 'clock-rotate-left', 'Printed', S.history.length)}
            <div class="h">Ready-made</div>
            ${navBtn('all', 'swatchbook', 'All', S.demoList.filter(d => d.language === S.lang && (!S.fitsOnly || fitsTape(d.label_size_id))).length)}
            ${cats.map(c => navBtn(c.id, CAT_ICONS[c.id] || 'tag', c.name, count(c.id))).join('')}
        </nav>
        <section class="lib-list">
            <h1>What should the label say?</h1>
                <textarea id="wzText" class="field typer" rows="1" placeholder="Type, then pick a look or a template" aria-label="Label text">${esc(S.wizardText)}</textarea>
                ${wizardChips()}
                <div id="fzLooks"></div>
            <div class="lib-bar">
                <button class="pill${S.fitsOnly ? ' on' : ''}" type="button" id="libFits"><i class="fa-solid fa-check"></i>Fits ${esc(sizeName(S.tape))}</button>
                <select id="libLang" aria-label="Language of the ready-made labels"><option value="en"${S.lang === 'en' ? ' selected' : ''}>English</option><option value="de"${S.lang === 'de' ? ' selected' : ''}>Deutsch</option></select>
            </div>
            <h1 id="libTitle"></h1>
            <div class="g4" id="libGrid"></div>
        </section>
        <aside class="detail" id="libDetail"></aside>
    </div>`;
    $$('.lib-nav button').forEach(b => b.onclick = () => { S.libCatChosen = true; history.replaceState(null, '', '#templates/' + b.dataset.cat); S.libCat = b.dataset.cat; $$('.lib-nav button').forEach(x => x.classList.toggle('on', x === b)); renderLibGrid(); });
    {
        const ta = $('#wzText');
        const grow = () => { ta.rows = Math.min(6, Math.max(1, ta.value.split('\n').length)); };
        grow();
        const update = debounce(() => {
            S.typedGen++;
            S.libCatChosen = false;
            $$('.lib-nav button').forEach(x => x.classList.remove('on'));
            renderLooks();
            renderLibGrid();
            if (S.libSel && S.libSel.kind !== 'look') showDetail(S.libSel);
        }, 400);
        ta.addEventListener('input', () => { S.wizardText = ta.value; grow(); update(); });
        ta.addEventListener('keydown', e => {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); if (wizardLines().length) printLook(0); }
        });
        bindWizardChips(ta);
        renderLooks();
        if (!S.libCatChosen && window.matchMedia('(min-width: 761px)').matches) ta.focus();
    }
    $('#libFits').onclick = () => { S.fitsOnly = !S.fitsOnly; renderLabels(); };
    $('#libLang').onchange = e => { S.lang = e.target.value; store('designer_lang', S.lang); renderLabels(); };
    renderLibGrid();
    if (S.libSel) showDetail(S.libSel); else emptyDetail();
}

// Without a chosen template the panel offers the last prints
function emptyDetail() {
    const box = $('#libDetail');
    if (!box) return;
    box.classList.remove('open');
    box.innerHTML = `<p class="muted">Choose a template to fill in and print it, or to change its design.</p>${againCard(6)}`;
    attachLazy(box);
    bindAgain(box);
}

function navBtn(id, icon, name, n) {
    return `<button type="button" data-cat="${esc(id)}" class="${S.libCat === id ? 'on' : ''}"><i class="fa-solid fa-${icon}"></i><span>${esc(name)}</span><span class="n">${n}</span></button>`;
}

function textMatches() {
    const words = wizardLines().join(' ').replace(/\{\{[^}]+\}\}/g, ' ').toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(w => w.length >= 3);
    if (!words.length) return [];
    const scored = [];
    const pool = [].concat(S.saved.map(savedItem), S.demoList.filter(d => d.language === S.lang).map(d => Object.assign(demoItem(d), { _cat: d.catName })));
    for (const it of pool) {
        const hay = (it.title + ' ' + (it._cat || '') + ' ' + (it.design ? blanksOf(it.design).join(' ') : '')).toLowerCase();
        let score = 0;
        for (const w of words) if (hay.includes(w) || hay.includes(w.slice(0, Math.max(4, w.length - 2)))) score++;
        if (score) scored.push([score + (fitsTape(it.size) ? .5 : 0) + (it.kind === 'saved' ? .3 : 0), it]);
    }
    scored.sort((a, b) => b[0] - a[0]);
    return scored.slice(0, 24).map(x => x[1]);
}

function renderLibGrid() {
    const grid = $('#libGrid');
    if (!grid) return;
    const typed = !S.libCatChosen && wizardLines().length;
    const typedGrid = wizardLines().length > 0;
    const matches = typed ? textMatches() : [];
    const items = matches.length ? matches : libItems(S.libCat);
    const cat = (S.demos || []).find(c => c.id === S.libCat);
    $('#libTitle').textContent = matches.length ? `Templates for "${toDisplay(wizardLines()[0])}"` :
        ({ saved: 'Saved labels', printed: 'Printed', all: 'All ready-made templates' }[S.libCat] || (cat ? cat.name : 'Templates'));
    if (!items.length) {
        grid.innerHTML = `<p class="empty">${S.libCat === 'printed' ? 'Nothing printed yet.' : 'No labels here.'}</p>`;
        return;
    }
    grid.innerHTML = items.slice(0, 240).map((it, i) => {
        const fits = fitsTape(it.size);
        const blanks = it.design ? blanksOf(it.design) : [];
        const sub = it.kind === 'saved' ? (blanks.length ? 'Fill in: ' + blanks.join(', ') : (fits ? 'Saved label' : 'Needs ' + sizeName(it.size))) : (fits ? (it.sub || '') : 'Needs ' + sizeName(it.size));
        return `<button class="thumb${fits ? '' : ' other'}${S.libSel && S.libSel.kind === it.kind && S.libSel.id === it.id ? ' sel' : ''}" type="button" data-i="${i}">
            <div class="bed">${typedGrid && it.kind !== 'printed' ? lazyTypedTag(it) : thumbSrc(it)}</div><div class="t">${esc(it.title)}</div><div class="s">${esc(sub)}</div></button>`;
    }).join('');
    attachLazy(grid);
    $$('.thumb', grid).forEach(b => b.onclick = () => {
        $$('.thumb', grid).forEach(x => x.classList.toggle('sel', x === b));
        showDetail(items[+b.dataset.i]);
    });
}

async function showDetail(item) {
    S.libSel = item;
    const box = $('#libDetail');
    if (!box) return;
    box.innerHTML = `<div class="row"><h2 class="grow">${esc(item.title)}</h2><button class="btn quiet icon close" type="button" title="Close"><i class="fa-solid fa-xmark"></i></button></div><p class="muted">Loading...</p>`;
    box.classList.add('open');
    $('.close', box).onclick = () => { box.innerHTML = ''; S.libSel = null; };
    let design;
    try { design = clone(await itemDesign(item)); } catch (e) { box.innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
    if (S.libSel !== item) return;
    const blanks = blanksOf(design);
    const { values } = applyTyped(item, design, item.values ? Object.assign({}, item.values) : null);
    const work = { design, values };
    const fits = fitsTape(design.label_size);
    box.innerHTML = `
        <div class="row"><h2 class="grow">${esc(item.title)}</h2><button class="btn quiet icon close" type="button" title="Close"><i class="fa-solid fa-xmark"></i></button></div>
        ${stageHTML('libStage')}
        <div>${fits ? `<span class="badge ok"><i class="fa-solid fa-check"></i>Fits the tape in the ${esc(S.printer ? S.printer.model : 'printer')}</span>`
            : `<span class="badge warn"><i class="fa-solid fa-ruler-combined"></i>Made for ${esc(sizeName(design.label_size))}</span>`}</div>
        <div class="fields" id="libFields"></div>
        <div class="fits" id="libFitsNote"></div>
        <div class="acts"><div class="copies"><button type="button" data-step="-1" aria-label="One less"><i class="fa-solid fa-minus"></i></button><input id="libCopies" type="number" min="1" max="100" value="${item.entry ? item.entry.copies : 1}" aria-label="Copies"><button type="button" data-step="1" aria-label="One more"><i class="fa-solid fa-plus"></i></button></div>
            <button class="btn-print" type="button" id="libPrint"${item.entry && item.entry.noImage ? ` disabled title="${NO_IMAGE}"` : ''}><i class="fa-solid fa-print"></i>Print</button></div>
        ${item.kind === 'saved' && blanks.length ? '<div class="hint"><span class="kbd">Enter</span>prints and empties the first field for the next one</div>' : ''}
        <div class="acts">
            <button class="btn" type="button" id="libEdit"><i class="fa-solid fa-pen-ruler"></i>Change the design</button>
            <div class="more-wrap"><button class="btn" type="button" id="libMore" aria-expanded="false"><i class="fa-solid fa-ellipsis"></i>More</button>
                <div class="more-menu" id="libMenu" hidden>
                    ${item.kind === 'saved' ? `<button type="button" id="libLinks"><i class="fa-solid fa-link"></i>Use from anywhere</button>
                        <button type="button" id="libDelete" class="danger"><i class="fa-regular fa-trash-can"></i>Remove from the saved labels</button>`
                        : `<button type="button" id="libKeep"><i class="fa-regular fa-bookmark"></i>Save to your labels</button>`}
                </div></div>
        </div>
        ${item.kind === 'saved' ? '<div class="hist-mini" id="libHist"></div>' : ''}`;
    $('.close', box).onclick = () => { S.libSel = null; emptyDetail(); $$('#libGrid .thumb').forEach(x => x.classList.remove('sel')); };
    // The stage of this label: a delayed refresh after another label was
    // chosen finds it gone and does nothing
    const stage = $('#libStage', box), note = $('#libFitsNote', box);
    const draw = () => { if (stage.isConnected) showOnStage(stage, work.design, work.values, (w, err) => fitsNote(note, w, err, work.design, () => refresh())); };
    const refresh = busyThen(() => stage, debounce(draw, 250));
    const drawHist = () => {
        const hb = $('#libHist');
        if (!hb) return;
        const printed = S.history.filter(h => h.source && h.source.kind === 'saved' && h.source.id === item.id).slice(0, 3);
        hb.innerHTML = printed.length ? `<h3>Printed with this template</h3>` + printed.map(h => `
            <div class="row"><div class="grow"><b>${esc(entryName(h))}</b> <span class="muted small">${h.copies} x, ${esc(fmtTime(h.time))}</span></div>
            <button class="btn quiet sm" type="button" data-again="${h.id}"${h.noImage ? ` disabled title="${NO_IMAGE}"` : ''}><i class="fa-solid fa-rotate-right"></i>Again</button></div>`).join('') : '';
        bindAgain(hb);
    };
    drawHist();
    const doPrint = async () => {
        if (item.entry && item.entry.noImage) { toast(NO_IMAGE); return; }
        const ok = await printJob({ title: item.title, design: work.design, values: Object.assign({}, work.values), copies: copiesIn(box, '#libCopies'), source: { kind: item.kind, id: item.id } });
        drawHist();
        // Ready for the next one: the first blank is emptied
        if (ok && item.kind === 'saved' && blanks.length) {
            const first = $$('#libFields [data-blank]').find(i => fieldType(item, i.dataset.blank) === 'text');
            if (first) { first.value = ''; work.values[first.dataset.blank] = ''; first.focus(); refresh(); }
        }
    };
    renderFields($('#libFields'), item, work, refresh, doPrint);
    markBlanks(box);
    bindCopies(box);
    draw();
    $('#libPrint').onclick = doPrint;
    $('#libMore').onclick = () => { const m = $('#libMenu'); m.hidden = !m.hidden; $('#libMore').setAttribute('aria-expanded', String(!m.hidden)); };
    $('#libEdit').onclick = () => openEditor({ title: item.title, design: work.design, origin: item.kind === 'saved' ? { kind: 'saved', id: item.id } : null });
    if ($('#libKeep')) $('#libKeep').onclick = async () => {
        $('#libMenu').hidden = true;
        try {
            const name = await saveLabel(item.title, work.design);
            toast(`"${item.title}" is now one of your saved labels`, { label: 'Open', run: () => { location.hash = 'fill/' + encodeURIComponent(name); } });
        } catch (e) { toast(e.message); }
    };
    if ($('#libLinks')) $('#libLinks').onclick = () => { $('#libMenu').hidden = true; linksDialog({ id: item.id, title: item.title, design: work.design }); };
    if ($('#libDelete')) $('#libDelete').onclick = async () => {
        const kept = clone(item.design);
        try { await deleteLabel(item.id); } catch (e) { toast(e.message); return; }
        S.libSel = null;
        renderLabels();
        toast(`Removed "${item.title}"`, { label: 'Undo', run: async () => { await saveLabel(item.title, kept, item.id); renderLabels(); } });
    };
}

// Labels with blanks get a field per blank. Labels without get a field
// per line, which changes the line itself.
function renderFields(box, item, work, refresh, onEnter) {
    const blanks = blanksOf(work.design);
    let html = '';
    if (blanks.length) {
        html = blanks.map((name, i) => fieldHTML(item, name, work.values[name], i)).join('');
    } else {
        html = work.design.text.map((l, i) => `<div><label class="label-s" for="ln${i}">Line ${i + 1}</label><input class="field" id="ln${i}" data-line="${i}" value="${esc(toDisplay(l.text))}"></div>`).join('');
        if (work.design.print_type === 'qrcode_text' || work.design.print_type === 'qrcode') {
            html += `<div><label class="label-s" for="lnCode">Code content</label><input class="field" id="lnCode" value="${esc(toDisplay(work.design.code_text))}"${work.design.print_type === 'qrcode_text' ? ' placeholder="Empty: the text of the label"' : ''}></div>`;
        }
        if (!work.design.text.length && work.design.print_type === 'image') html += '<p class="muted small">An image label without text. Change the design to replace the image.</p>';
    }
    box.innerHTML = html;
    $$('[data-blank]', box).forEach(inp => inp.addEventListener('input', () => { work.values[inp.dataset.blank] = inp.value; refresh(); }));
    $$('[data-line]', box).forEach(inp => inp.addEventListener('input', () => {
        const l = work.design.text[+inp.dataset.line];
        l.text = fromDisplay(inp.value, l.text);
        refresh();
    }));
    if ($('#lnCode', box)) $('#lnCode', box).addEventListener('input', e => { work.design.code_text = fromDisplay(e.target.value, work.design.code_text); refresh(); });
    $$('[data-stepf]', box).forEach(b => b.onclick = () => {
        const inp = $('#' + b.dataset.for, box);
        inp.value = Math.max(0, (parseInt(inp.value, 10) || 0) + +b.dataset.stepf);
        inp.dispatchEvent(new Event('input'));
    });
    $$('input', box).forEach(inp => inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); onEnter(); } }));
    $$('[data-reuse]', box).forEach(p => p.onclick = () => {
        const inp = $(`[data-blank="${CSS.escape(p.dataset.reuse)}"]`, box);
        inp.value = p.textContent;
        inp.dispatchEvent(new Event('input'));
        inp.focus();
    });
}

function fieldHTML(item, name, value, i) {
    const t = fieldType(item, name);
    const id = 'f' + i;
    if (t === 'number') {
        return `<div><label class="label-s" for="${id}">${esc(name)}</label><div class="stepper-f"><button type="button" data-stepf="-1" data-for="${id}" aria-label="Less"><i class="fa-solid fa-minus"></i></button><input id="${id}" type="number" data-blank="${esc(name)}" value="${esc(value)}"><button type="button" data-stepf="1" data-for="${id}" aria-label="More"><i class="fa-solid fa-plus"></i></button></div></div>`;
    }
    if (t === 'date') {
        return `<div><label class="label-s" for="${id}">${esc(name)}</label><input class="field" id="${id}" type="date" data-blank="${esc(name)}" value="${esc(value || today())}"></div>`;
    }
    const used = [];
    if (item.kind === 'saved') {
        for (const h of S.history) {
            if (!h.source || h.source.id !== item.id) continue;
            const v = (h.values || {})[name];
            if (v && !used.includes(v)) used.push(v);
            if (used.length === 4) break;
        }
    }
    return `<div><label class="label-s" for="${id}">${esc(name)}</label><input class="field${i === 0 ? ' big' : ''}${String(value || '').trim() ? '' : ' need'}" id="${id}" data-blank="${esc(name)}" value="${esc(value)}" placeholder="Fill in ${esc(name)}" autocomplete="off">
        ${used.length ? `<div class="reuse">${used.map(v => `<button class="pill" type="button" data-reuse="${esc(name)}">${esc(v)}</button>`).join('')}</div>` : ''}</div>`;
}

function bindCopies(root) {
    $$('[data-step]', root).forEach(b => b.onclick = () => {
        const inp = b.parentElement.querySelector('input');
        inp.value = Math.min(100, Math.max(1, (parseInt(inp.value, 10) || 1) + +b.dataset.step));
        inp.dispatchEvent(new Event('input', { bubbles: true }));
    });
}
const copiesIn = (root, sel) => Math.min(100, Math.max(1, parseInt($(sel, root).value, 10) || 1));

// --- Links to a label ------------------------------------------------------------------
// A link (or the QR sticker) opens the wizard with the label chosen and its
// blanks filled in from the address

async function openFill(id, params) {
    const s = S.saved.find(x => x.name === id);
    if (!s) { toast('This label is not saved on this server'); location.hash = 'wizard'; return; }
    const item = savedItem(s);
    try { item.design = await itemDesign(item); } catch (e) { toast(e.message); return; }
    item.values = defaults(item);
    for (const [k, v] of params.entries()) if (blanksOf(item.design).includes(k)) item.values[k] = v;
    S.libSel = item;
    renderLabels('saved');
}

// --- Links ---------------------------------------------------------------------------

function linksDialog(m) {
    if (!m) return;
    const blanks = blanksOf(m.design);
    const url = `${location.origin}${location.pathname}#fill/${encodeURIComponent(m.id)}`;
    const example = blanks.length ? `${url}?${encodeURIComponent(blanks[0])}=${encodeURIComponent(blanks[0] === 'What' ? 'Chili' : 'Example')}` : url;
    const sticker = setWide(Object.assign(baseDesign(S.tape), {
        print_type: 'qrcode_text', code_text: url, qrcode_size: '12', margin_left: '30', margin_right: '30',
        text: [L('Scan to print a', 56, false, 'left'), L(m.title + ' label', 84, true, 'left')]
    }), true);
    openLayer(`<div class="dialog">
        <div class="dialog-h"><div class="grow"><h2>Use "${esc(m.title)}" from anywhere</h2><p>Every label has its own address. Open it, fill it in, print it.</p></div>
            <button class="btn quiet icon" type="button" data-close title="Close"><i class="fa-solid fa-xmark"></i></button></div>
        <div class="links">
            <div class="opt"><h3><i class="fa-solid fa-qrcode"></i>A sticker where you need it</h3>
                <div class="sticker" id="stickerBox"><span class="muted small">Making the sticker...</span></div>
                <p>Scan it with the phone camera and the form for this label opens.</p>
                <button class="btn-print sm" type="button" id="printSticker" style="align-self:flex-start"><i class="fa-solid fa-print"></i>Print the sticker</button></div>
            <div style="display:flex;flex-direction:column;gap:1rem;min-width:0">
                <div class="opt"><h3><i class="fa-solid fa-link"></i>Link</h3>
                    <div class="row"><input class="field" id="linkUrl" readonly value="${esc(example)}"><button class="btn" type="button" id="copyLink"><i class="fa-regular fa-copy"></i>Copy</button></div>
                    <p>${blanks.length ? `Add <b>?${esc(blanks[0])}=...</b> to fill in blanks in advance. ` : ''}Works for bookmarks, NFC tags and Stream Deck buttons.</p></div>
                <div class="opt"><h3><i class="fa-solid fa-mobile-screen"></i>On the home screen</h3>
                    <p>Open the link on the phone and choose "Add to Home screen". It opens straight into this label.</p></div>
            </div>
        </div></div>`);
    preview(sticker, {}).then(p => { const box = $('#stickerBox'); if (box) box.innerHTML = `<img alt="Sticker preview" src="${p.url}">`; }).catch(() => { });
    $('#printSticker').onclick = () => { closeLayer(true); printJob({ title: m.title + ' sticker', design: sticker, values: {}, copies: 1, source: { kind: 'sticker', id: m.id } }); };
    $('#copyLink').onclick = async () => {
        try { await navigator.clipboard.writeText($('#linkUrl').value); toast('Link copied'); }
        catch (e) { $('#linkUrl').select(); toast('Press Ctrl C to copy'); }
    };
}

// --- Editor ----------------------------------------------------------------------------

function newEdit() {
    return { title: 'New label', design: Object.assign(baseDesign(S.tape), { text: [L('', C.defaults.font_size, true)] }), origin: null };
}
const isEmptyDesign = d => d.print_type === 'text' && !d.text.some(l => String(l.text || '').trim());

// The label in the editor. Another one starts a new undo history, so that
// undo never mixes two labels.
function setEdit(edit) {
    S.edit = edit;
    S.hist = { undo: [], redo: [], last: null };
}

function openEditor({ title, design, origin }) {
    setEdit({ title: title || 'New label', design: clone(design), origin: origin || null });
    S.focusBlank = true;
    S.editSel = 0;
    if (location.hash === '#edit') renderEditor(); else location.hash = 'edit';
}

function layoutOf(d) {
    if (d.print_type === 'qrcode_text') return 'codetext';
    if (d.print_type === 'qrcode') return 'code';
    if (d.print_type === 'image') return 'image';
    return 'text';
}

function renderEditor() {
    if (!S.edit) S.edit = load('designer_edit', null) || newEdit();
    const E = S.edit, d = E.design;
    const lay = layoutOf(d);
    const hasCode = lay === 'code' || lay === 'codetext';
    const hasText = lay !== 'code';
    const families = Object.keys(FONTS).sort((a, b) => a.localeCompare(b));
    // The font list in the header sets all lines, each line can have its own
    const used = [...new Set(d.text.map(l => familyOf(l.font)))];
    const fam = used.length === 1 ? used[0] : '';
    $('#view').innerHTML = `<div class="ed">
        <aside class="ed-panel">
            <div class="ed-head"><input id="edTitle" value="${esc(E.title)}" aria-label="Name of the label">
                <button class="btn sm" type="button" id="edNew" title="Start a new, empty label"><i class="fa-solid fa-plus"></i>New</button>
                <a class="btn sm" href="#templates" title="Start from a template"><i class="fa-solid fa-swatchbook"></i>Templates</a></div>
            <div><h3 style="margin-bottom:.55rem">Layout</h3><div class="layouts">
                ${[['text', 'Text', '<i class="t"></i>'], ['codetext', 'Code, text', '<i class="q"></i><i class="t"></i>'], ['image', 'Image, text', '<i class="q" style="border-radius:50%"></i><i class="t"></i>'], ['code', 'Code', '<i class="q" style="margin:0 auto"></i>']]
                    .map(([id, name, sch]) => `<button class="lay${lay === id ? ' on' : ''}" type="button" data-lay="${id}"><span class="sch">${sch}</span>${name}</button>`).join('')}
            </div></div>
            ${hasCode ? `<div class="block"><div class="block-h"><i class="fa-solid fa-qrcode"></i>Code<select id="edBarcode" aria-label="Kind of code">${S.barcodes.map(b => `<option${b === d.barcode_type ? ' selected' : ''}>${esc(b)}</option>`).join('')}</select></div>
                <div class="block-b"><input class="field" id="edCode" value="${esc(toDisplay(d.code_text))}" placeholder="${lay === 'codetext' ? 'Link or text, empty for the text of the label' : 'Link or text in the code'}" aria-label="Content of the code">
                <div><span class="label-s">Size</span><div class="seg"><button type="button" data-fit="1" class="${d.image_fit === '1' ? 'on' : ''}">Fit to the label</button><button type="button" data-fit="0" class="${d.image_fit === '1' ? '' : 'on'}">Fixed</button></div></div>
                ${((qr, dots) => `<div class="pair"${qr ? '' : ' title="Only for QR codes"'}>
                    <label${qr && !dots ? ' title="The code fits the label"' : ''}><span class="label-s${dots ? '' : ' off'}">Size of a dot</span><input class="field" type="number" min="1" max="40" id="edQrSize" value="${esc(d.qrcode_size)}"${dots ? '' : ' disabled'}></label>
                    <label><span class="label-s${qr ? '' : ' off'}">Error correction</span><select class="field" id="edQrCorr"${qr ? '' : ' disabled'}>${[['L', 'Low, 7 %'], ['M', 'Medium, 15 %'], ['Q', 'Quartile, 25 %'], ['H', 'High, 30 %']].map(([v, n]) => `<option value="${v}"${d.qrcode_correction === v ? ' selected' : ''}>${n}</option>`).join('')}</select></label>
                </div>${qr ? '' : '<span class="muted small">Size and error correction exist for QR codes only.</span>'}`)(d.barcode_type === 'QR', d.barcode_type === 'QR' && d.image_fit !== '1')}
                ${lay === 'codetext' ? gapField(d) : ''}</div></div>` : ''}
            ${lay === 'image' ? imageBlock(d) : ''}
            ${hasText ? `<div class="block"><div class="block-h"><i class="fa-solid fa-font"></i>Text<select id="edFamily" aria-label="Font of all lines" title="Font of all lines">${fam ? '' : '<option value="" selected>Several fonts</option>'}${families.map(f => `<option${f === fam ? ' selected' : ''}>${esc(f)}</option>`).join('')}</select></div>
                <div id="edLines">${d.text.map((l, i) => lineRow(l, i, mainFamily(d), families, redTape(d))).join('')}</div>
                <div class="addl">
                    <button class="pill" type="button" id="edAdd"><i class="fa-solid fa-plus"></i>Line</button>
                    <button class="pill" type="button" id="edBlank"><i class="fa-regular fa-square"></i>Blank</button>
                    <button class="pill" type="button" data-tok="[today]"><i class="fa-regular fa-calendar"></i>Date</button>
                    <button class="pill" type="button" data-tok="[counter]"><i class="fa-solid fa-arrow-up-1-9"></i>Counter</button>
                    <button class="pill" type="button" id="edSym"><i class="fa-solid fa-icons"></i>Symbol</button>
                    <button class="pill${S.tplInfo ? ' on' : ''}" type="button" id="tplInfoBtn" aria-expanded="${!!S.tplInfo}" aria-controls="tplInfo"><i class="fa-solid fa-circle-info"></i>Variables</button>
                </div>
                <p class="ed-note">Write <b>[Name]</b> for a blank that is filled in when printing. Variables explains all of them.</p>
                <div class="info-box" id="tplInfo" style="margin:0 .8rem .7rem"${S.tplInfo ? '' : ' hidden'}>${varsHelpHTML()}</div></div>` : ''}
            <div class="block"><div class="block-h"><i class="fa-solid fa-ruler-combined"></i>Label</div>
                <div class="block-b">
                    <div><label class="label-s" for="edSize">Tape</label>${tapeNote(d)}<select class="field" id="edSize">${sizeOptions(d.label_size, s => `${esc(sizeName(s.id))}${s.id === S.tape ? ' (in the printer)' : ''}`)}</select></div>
                    <div><span class="label-s">${hasText ? 'Text' : 'Code'} runs</span><div class="seg"><button type="button" data-along="0" class="${runsAlong(d) ? '' : 'on'}">Across the tape</button><button type="button" data-along="1" class="${runsAlong(d) ? 'on' : ''}">Along</button></div></div>
                    <div${hasText ? '' : ' title="Only with text"'}><span class="label-s${hasText ? '' : ' off'}">Text that does not fit</span><div class="seg">${[['1', 'Shrink it'], ['0', 'Keep the size']].map(([v, n]) => `<button type="button" data-shrink="${v}" class="${(d.shrink_text === '1') === (v === '1') ? 'on' : ''}"${hasText ? '' : ' disabled'}>${n}</button>`).join('')}</div></div>
                    <div class="group">
                        <div><span class="label-s">Frame</span><div class="seg">${[['none', 'None'], ['square', 'Square'], ['rounded', 'Rounded']].map(([v, n]) => `<button type="button" data-frame="${v}" class="${frameOf(d) === v ? 'on' : ''}">${n}</button>`).join('')}</div></div>
                        ${frameOf(d) === 'none' ? '' : frameDetails(d)}
                    </div>
                    <div id="edMargins"><span class="label-s">Margins, empty space around the content</span>
                        <div class="margins">${['top', 'bottom', 'left', 'right'].map(side => `
                        <label><span>${side[0].toUpperCase() + side.slice(1)}, mm</span><input class="field" type="number" min="0" max="20" step="0.5" data-margin="${side}" value="${dotsToMm(d['margin_' + side], dpiOf(d.label_size))}"></label>`).join('')}</div>
</div>
                </div></div>
        </aside>
        <section class="ed-canvas">
            <div class="ed-top">
                <div class="fits" id="edFits"></div>
                <div class="row ml-auto">
                    <div class="undo-group">
                        <button class="btn" type="button" id="edUndo" title="Undo the last change (Ctrl Z)"${S.hist.undo.length ? '' : ' disabled'}><i class="fa-solid fa-rotate-left"></i><span class="lbl">Undo</span><span class="count" id="edUndoN"${S.hist.undo.length ? '' : ' hidden'}>${S.hist.undo.length}</span></button>
                        <button class="btn icon" type="button" id="edRedo" title="Redo (Ctrl Y)" aria-label="Redo"${S.hist.redo.length ? '' : ' disabled'}><i class="fa-solid fa-rotate-right"></i></button>
                    </div>
                    <button class="btn" type="button" id="edSave" title="${E.origin ? 'Save' : 'Save to your labels'}"><i class="fa-regular fa-bookmark"></i><span class="lbl">${E.origin ? 'Save' : 'Save to your labels'}</span></button>
                    <span class="break" aria-hidden="true"></span>
                    <div class="copies"><button type="button" data-step="-1" aria-label="One less"><i class="fa-solid fa-minus"></i></button><input id="edCopies" type="number" min="1" max="100" value="${E.copies || 1}" aria-label="Copies"><button type="button" data-step="1" aria-label="One more"><i class="fa-solid fa-plus"></i></button></div>
                    <button class="btn-print" type="button" id="edPrint"><i class="fa-solid fa-print"></i>Print</button>
                </div>
            </div>
            <div class="fill-in" id="edFields"></div>
            ${stageHTML('edStage')}
        </section>
    </div>`;
    bindEditor();
    redrawEditor();
    const first = $('[data-row="0"] .tx');
    if (first && !first.value && window.matchMedia('(min-width: 761px)').matches) first.focus();
}

// The label sizes the printer can use, grouped by their form. The chosen
// one is always offered, e.g. that of a label made for another printer.
function sizeOptions(chosen, name) {
    const offered = s => s.id === chosen || printerTakes(s);
    const groups = [['Endless', s => s.form === 'endless' && !isPtouch(s.id)], ['Die-cut', s => s.form === 'die_cut'],
        ['Round', s => s.form === 'round_die_cut'], ['P-touch tape', s => isPtouch(s.id)]];
    return groups.map(([group, inGroup]) => {
        const sizes = C.label_sizes.filter(s => inGroup(s) && offered(s));
        return sizes.length ? `<optgroup label="${group}">${sizes.map(s => `<option value="${esc(s.id)}"${s.id === chosen ? ' selected' : ''}>${name(s)}</option>`).join('')}</optgroup>` : '';
    }).join('');
}

function tapeNote(d) {
    if (d.label_size === S.tape) return '<span class="tape-note ok"><i class="fa-solid fa-check"></i> Matches the tape in the printer</span>';
    if (fitsTape(d.label_size)) return '';
    return `<span class="tape-note warn">The ${esc(S.printer ? S.printer.model : 'printer')} has ${esc(sizeName(S.tape))} loaded. <button type="button" class="link" id="edUseTape">Switch to it</button></span>`;
}

function imageBlock(d) {
    const red = redTape(d);
    const modes = [['black_and_white', 'Black and white'], ['grayscale', 'Grayscale'], ['red_and_black', 'Red and black'], ['colored', 'Full color']];
    const needsRed = v => (v === 'red_and_black' || v === 'colored') && !red && d.image_mode !== v;
    const bw = d.image_mode === 'black_and_white';
    const fit = d.image_fit === '1', crop = d.image_crop === '1';
    const pdf = d.image && /pdf/i.test(d.image.mime);
    return `<div class="block"><div class="block-h"><i class="fa-regular fa-image"></i>Image</div>
        <div class="block-b">
            <label class="drop" for="edFile"><i class="fa-solid fa-upload"></i> ${d.image ? esc(d.image.name) + (d.more_images ? ` and ${d.more_images.length} more` : '') + ', choose others' : 'Choose images or PDFs, drop or paste them'}</label><input type="file" id="edFile" accept="image/*,application/pdf" multiple hidden>
            ${d.more_images ? `<span class="muted small">${d.more_images.length + 1} files, each prints as a label of its own. The preview shows the first, saving the label keeps only the first.</span>` : ''}
            <label title="${pdf ? '' : 'Only for PDF files'}"><span class="label-s${pdf ? '' : ' off'}">PDF pages</span><input class="field" id="edPages" placeholder="${pdf ? '1' : 'Only for PDF files'}" value="${esc(d.pdf_pages || '')}"${pdf ? '' : ' disabled'}>${pdf ? '<span class="muted small" id="edPagesHelp">For example 1-3, 5. Each page prints as a label of its own.</span>' : ''}</label>
            <div><span class="label-s">Colors</span><div class="seg wrap">${modes.map(([v, n]) => `<button type="button" data-imode="${v}" class="${d.image_mode === v ? 'on' : ''}"${needsRed(v) ? ' disabled title="Needs black and red tape"' : ''}>${n}</button>`).join('')}</div></div>
            <label title="${bw ? '' : 'Only for black and white'}"><span class="label-s${bw ? '' : ' off'}">Pixels darker than <b id="edThrV">${esc(d.image_bw_threshold)}</b> of 255 print black${bw ? '' : ', only for black and white'}</span><input type="range" id="edThr" min="1" max="254" value="${esc(d.image_bw_threshold)}"${bw ? '' : ' disabled'}></label>
            <div class="row" style="flex-wrap:wrap;align-items:flex-end">
                <div><span class="label-s">Size</span><div class="seg"><button type="button" data-fit="1" class="${fit ? 'on' : ''}">Fit to the label</button><button type="button" data-fit="0" class="${fit ? '' : 'on'}">Scale</button></div></div>
                <label title="${fit ? 'Only without fitting to the label' : ''}"><span class="label-s${fit ? ' off' : ''}">Scale in %</span><input class="field" type="number" min="1" max="500" id="edScale" value="${esc(d.image_scaling_factor)}" style="width:6rem"${fit ? ' disabled' : ''}></label>
            </div>
            <div class="row" style="flex-wrap:wrap">
                <div><span class="label-s">Rotation in degrees</span><div class="row" style="gap:.35rem"><div class="seg">${[0, 90, 180, 270].map(r => `<button type="button" data-rot="${r}" class="${+d.image_rotation === r ? 'on' : ''}">${r}</button>`).join('')}</div>
                    <input class="field rot" type="number" min="0" max="359" id="edRot" value="${esc(d.image_rotation)}" aria-label="Any angle in degrees" title="Any angle in degrees"></div></div>
                <div><span class="label-s">Image processing</span><div class="seg"><button type="button" data-crop="0" class="${crop ? '' : 'on'}">None</button><button type="button" data-crop="1" class="${crop ? 'on' : ''}" title="Removes the empty border around the image">Auto crop</button></div></div>
            </div>
            ${gapField(d)}
        </div></div>`;
}

// Distance between the code or image and the text. Left empty, the server
// chooses it from the size of the text, the field then shows its choice.
const hasTextLines = d => d.text.some(l => String(l.text || '').trim());
const GAP_TITLE = 'Leave it empty for a distance that suits the size of the text';
function gapField(d) {
    const on = hasTextLines(d);
    return `<label title="${on ? GAP_TITLE : 'Only with text on the label'}"><span class="label-s${on ? '' : ' off'}">Distance to the text, mm</span>
        <input class="field" type="number" min="0" max="50" step="0.5" id="edGap" value="${d.image_text_gap === '' ? '' : dotsToMm(d.image_text_gap, dpiOf(d.label_size))}" placeholder="${on ? 'Automatic' : 'Only with text'}" style="width:10rem"${on ? '' : ' disabled'}></label>`;
}

function frameDetails(d) {
    const red = redTape(d), dpi = dpiOf(d.label_size);
    // Shown whenever there is a frame, it is what the frame consists of
    return `<div class="frame-details" id="edFrameMore">
        <div class="margins">
            <label><span>Line, mm</span><input class="field" type="number" min="0.1" max="5" step="0.1" data-frame-k="border_thickness" value="${dotsToMm(d.border_thickness, dpi)}"></label>
            <label><span>Sides, mm</span><input class="field" type="number" min="0" max="20" step="0.5" data-frame-k="border_distance_x" value="${dotsToMm(d.border_distance_x, dpi)}"></label>
            <label><span>Top, bottom</span><input class="field" type="number" min="0" max="20" step="0.5" data-frame-k="border_distance_y" value="${dotsToMm(d.border_distance_y, dpi)}"></label>
            ${(rd => `<label title="${rd ? '' : 'Only for rounded frames'}"><span class="${rd ? '' : 'off'}">Corners, mm</span><input class="field" type="number" min="0" max="20" step="0.5" data-frame-k="border_roundness" value="${dotsToMm(d.border_roundness, dpi)}"${rd ? '' : ' disabled'}></label>`)(frameOf(d) === 'rounded')}
        </div>
        <p class="muted small" style="margin:.4rem 0 0">Sides, top and bottom: the distance from the edge of the label. The margins grow so that the text stays inside.</p>
        <div style="margin-top:.5rem"><span class="label-s">Color</span><div class="seg"><button type="button" data-bcolor="black" class="${d.border_color === 'red' ? '' : 'on'}">Black</button><button type="button" data-bcolor="red" class="${d.border_color === 'red' ? 'on' : ''}"${red || d.border_color === 'red' ? '' : ' disabled title="Needs black and red tape"'}>Red</button></div></div>
    </div>`;
}

// The font most lines use
function mainFamily(d) {
    const count = {};
    d.text.forEach(l => { const f = familyOf(l.font); count[f] = (count[f] || 0) + 1; });
    return Object.keys(count).sort((a, b) => count[b] - count[a])[0] || DEFAULT_FAMILY;
}

// Margins, frames and distances are kept in dots, the editor shows them in
// millimeters at the resolution the label is printed with
const dotsToMm = (dots, dpi) => Math.round((+dots || 0) / dpi * 25.4 * 10) / 10;
const mmToDots = (mm, dpi) => Math.round(mm * dpi / 25.4);

// The frame is drawn at a distance from the edge of the label, independent
// of the text, so the margins grow with it to keep the text inside
const frameOf = d => d.border_thickness === '0' ? 'none' : d.border_roundness === '0' ? 'square' : 'rounded';
function setFrame(d, kind) {
    const base = baseDesign(d.label_size);
    if (kind === 'none') {
        Object.assign(d, { border_thickness: '0', border_roundness: '0', border_distance_x: '0', border_distance_y: '0' });
        for (const k of ['margin_top', 'margin_bottom', 'margin_left', 'margin_right']) d[k] = base[k];
        return;
    }
    const was = frameOf(d);
    Object.assign(d, { border_roundness: kind === 'rounded' ? (+d.border_roundness ? d.border_roundness : '24') : '0' });
    if (was === 'none') Object.assign(d, { border_thickness: '5', border_distance_x: '20', border_distance_y: '14' });
    fitMarginsToFrame(d);
}
function fitMarginsToFrame(d) {
    const x = +d.border_distance_x + +d.border_thickness + 25, y = +d.border_distance_y + +d.border_thickness + 21;
    d.margin_left = d.margin_right = String(Math.max(x, +d.margin_left || 0, +d.margin_right || 0));
    d.margin_top = d.margin_bottom = String(Math.max(y, +d.margin_top || 0, +d.margin_bottom || 0));
}

const redTape = d => !!(SIZES[d.label_size] && SIZES[d.label_size].red);
const SPACINGS = [100, 150, 200, 250, 300];

// Two rows per line: the text on top, all settings of the line below it
function lineRow(l, i, main, families, red) {
    const al = { left: 'align-left', center: 'align-center', right: 'align-right' }[l.align] || 'align-center';
    const fam = familyOf(l.font);
    const noBold = wantsBold(l) && !hasBold(fam);
    const spacing = +(l.line_spacing || 100);
    return `<div class="lrow${wantsBold(l) && !noBold ? ' bold' : ''}${i === S.editSel ? ' sel' : ''}${l.color === 'red' ? ' red' : ''}" data-row="${i}">
        <span class="txw">${l.checkbox ? '<i class="fa-regular fa-square" title="To-do box"></i>' : ''}<input class="tx" value="${esc(toDisplay(l.text))}" placeholder="${i === 0 ? 'Type the label text' : 'Line ' + (i + 1)}" aria-label="Line ${i + 1}">
            <button class="mini quiet" type="button" data-act="del" title="Remove the line"><i class="fa-solid fa-xmark"></i></button></span>
        <div class="st">
            <select class="mini lf${fam !== main ? ' on' : ''}" title="Font of line ${i + 1}" aria-label="Font of line ${i + 1}">${families.map(f => `<option${f === fam ? ' selected' : ''}>${esc(f)}</option>`).join('')}</select>
            <input class="mini" type="number" min="1" value="${esc(l.size)}" title="Size" aria-label="Size of line ${i + 1}">
            <button class="mini${wantsBold(l) ? ' on' : ''}${noBold ? ' nobold' : ''}" type="button" data-act="bold" title="${noBold ? esc(fam) + ' has no bold style. The line prints regular and becomes bold again with a font that has one.' : 'Bold'}">B</button>
            <button class="mini" type="button" data-act="align" title="Alignment: ${esc(l.align || 'center')}"><i class="fa-solid fa-${al}"></i></button>
            <button class="mini${l.inverted ? ' on' : ''}" type="button" data-act="invert" title="White on black"><i class="fa-solid fa-circle-half-stroke"></i></button>
            <button class="mini${l.checkbox ? ' on' : ''}" type="button" data-act="todo" title="To-do box in front"><i class="fa-regular fa-square-check"></i></button>
            <button class="mini red-sw${l.color === 'red' ? ' on' : ''}${l.color === 'red' && !red ? ' nored' : ''}" type="button" data-act="red" title="${red ? 'Red text' : l.color === 'red' ? 'This tape has no red, the line prints black' : 'Red text needs black and red tape'}"${red || l.color === 'red' ? '' : ' disabled'}><span></span></button>
            <label class="mini-wrap${spacing !== 100 ? ' on' : ''}" title="Line spacing"><i class="fa-solid fa-text-height"></i><select class="mini lsp" aria-label="Line spacing of line ${i + 1}">${SPACINGS.concat(SPACINGS.includes(spacing) ? [] : [spacing]).map(v => `<option value="${v}"${v === spacing ? ' selected' : ''}>${v} %</option>`).join('')}</select></label>
        </div></div>`;
}

function recordHistory(E) {
    const snap = JSON.stringify({ t: E.title, d: E.design });
    if (S.hist.last !== null && snap !== S.hist.last) {
        S.hist.undo.push(S.hist.last);
        if (S.hist.undo.length > 60) S.hist.undo.shift();
        S.hist.redo = [];
    }
    S.hist.last = snap;
    if ($('#edUndo')) {
        $('#edUndo').disabled = !S.hist.undo.length;
        $('#edRedo').disabled = !S.hist.redo.length;
        $('#edUndoN').textContent = S.hist.undo.length;
        $('#edUndoN').hidden = !S.hist.undo.length;
    }
}
function stepHistory(dir) {
    const from = dir < 0 ? S.hist.undo : S.hist.redo, to = dir < 0 ? S.hist.redo : S.hist.undo;
    if (!from.length || !S.edit) return;
    to.push(S.hist.last);
    S.hist.last = from.pop();
    const o = JSON.parse(S.hist.last);
    S.edit.title = o.t;
    S.edit.design = o.d;
    renderEditor();
}

const drawEditor = debounce(() => {
    const E = S.edit;
    if (!E || !$('#edStage')) return;
    recordHistory(E);
    // A label with blanks shows fields to try values
    const blanks = blanksOf(E.design);
    E.values = E.values || {};
    const box = $('#edFields');
    const item = { kind: E.origin && E.origin.kind === 'saved' ? 'saved' : 'edit', id: E.origin && E.origin.id, design: E.design };
    const sig = JSON.stringify([blanks, blanks.map(b => blankFields(item)[b] || null), !!S.blankSettings]);
    if (box.dataset.sig !== sig) {
        box.dataset.sig = sig;
        const vals = defaults(item);
        // Typed values stay, unless the kind of the blank changed or its new
        // default replaces the old one that was still in the field. Kept
        // with the label, the fields are made anew with the editor.
        const oldTypes = E.blankTypes || {}, oldDefs = E.blankDefs || {};
        for (const b of blanks) {
            const v = E.values[b], def = (blankFields(item)[b] || {}).default || '';
            if (v === undefined || (oldTypes[b] && fieldType(item, b) !== oldTypes[b])) continue;
            if ((v === '' || v === (oldDefs[b] || '')) && def !== (oldDefs[b] || '')) continue;
            vals[b] = v;
        }
        E.blankTypes = Object.fromEntries(blanks.map(b => [b, fieldType(item, b)]));
        E.blankDefs = Object.fromEntries(blanks.map(b => [b, (blankFields(item)[b] || {}).default || '']));
        E.values = vals;
        const head = `<h3>Fill in the blanks <span class="badge warn open-count" hidden></span>
            <button class="btn sm quiet ml-auto${S.blankSettings ? ' on' : ''}" type="button" id="blankSet" aria-pressed="${!!S.blankSettings}"><i class="fa-solid fa-sliders"></i>Blank settings</button></h3>`;
        box.innerHTML = !blanks.length ? '' : S.blankSettings ? head + `<div class="bset">${blanks.map(b => {
            const k = fieldType(item, b), f = blankFields(item)[b] || {};
            return `<div class="bset-row" data-bname="${esc(b)}"><b>[${esc(b)}]</b>
                <div class="seg">${BLANK_KINDS.map(([v, n]) => `<button type="button" data-kind="${v}" class="${k === v ? 'on' : ''}">${n}</button>`).join('')}</div>
                ${k === 'date' ? '<span class="muted small">Starts at today</span>' : `<input class="field" data-default="1" type="${k === 'number' ? 'number' : 'text'}" placeholder="Default, optional" value="${esc(f.default || '')}">`}</div>`;
        }).join('')}</div><p class="muted small" style="margin:0">Stored with the label. Rename a blank in its line, between the brackets.</p>`
            : head + `<div class="fill-grid">${blanks.map((n, i) => fieldHTML(item, n, vals[n], 100 + i)).join('')}</div>`;
        box.hidden = !blanks.length;
        if ($('#blankSet', box)) $('#blankSet', box).onclick = () => { S.blankSettings = !S.blankSettings; redrawEditor(); };
        const setField = (name, patch) => {
            E.design.fields = Object.assign({}, E.design.fields);
            E.design.fields[name] = Object.assign({}, blankFields(item)[name] || { type: guessKind(name) }, patch);
            if (E.design.fields[name].default === '') delete E.design.fields[name].default;
            redrawEditor();
        };
        $$('.bset-row', box).forEach(r => {
            $$('[data-kind]', r).forEach(b => b.onclick = () => setField(r.dataset.bname, { type: b.dataset.kind, default: '' }));
            const dv = $('[data-default]', r);
            if (dv) dv.addEventListener('change', () => setField(r.dataset.bname, { default: dv.value.trim() }));
        });
        $$('[data-reuse]', box).forEach(pl => pl.onclick = () => {
            const inp = $(`[data-blank="${CSS.escape(pl.dataset.reuse)}"]`, box);
            inp.value = pl.textContent;
            inp.dispatchEvent(new Event('input', { bubbles: true }));
        });
        markBlanks(box.parentElement);
        if (S.focusBlank && window.matchMedia('(min-width: 761px)').matches) { const f = $('input.need', box); if (f) f.focus(); }
        S.focusBlank = false;
        $$('[data-blank]', box).forEach(inp => inp.addEventListener('input', () => { E.values[inp.dataset.blank] = inp.value; redrawEditor(); }));
        $$('[data-blank]', box).forEach(inp => inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); $('#edPrint').click(); } }));
        $$('[data-stepf]', box).forEach(b => b.onclick = () => { const inp = $('#' + b.dataset.for, box); inp.value = Math.max(0, (parseInt(inp.value, 10) || 0) + +b.dataset.stepf); inp.dispatchEvent(new Event('input')); });
    }
    storeQuiet('designer_edit', S.edit);
    // An empty label is shown blank, as large as one line of text in the
    // first line's size makes it, and cannot be printed or saved yet
    const empty = isEmptyDesign(E.design);
    $('#edPrint').disabled = empty;
    $('#edSave').disabled = empty;
    const shown = empty ? Object.assign(clone(E.design), { text: E.design.text.slice(0, 1).map(l => Object.assign({}, l, { text: 'Ag' })) }) : E.design;
    const gap = $('#edGap');
    if (gap) {
        const on = hasTextLines(E.design);
        gap.disabled = !on;
        gap.previousElementSibling.classList.toggle('off', !on);
        gap.parentElement.title = on ? GAP_TITLE : 'Only with text on the label';
        if (!on) gap.placeholder = 'Only with text';
    }
    showOnStage($('#edStage'), shown, E.values, (w, err, p) => {
        if (p && p.auto && !empty) setTimeout(() => $('#edFits') && $('#edFits').insertAdjacentHTML('beforeend', ' <span class="muted">Turned automatically to fit the label best.</span>'));
        if (gap && p && hasTextLines(E.design)) gap.placeholder = p.gap ? `Automatic, ${dotsToMm(p.gap, dpiOf(E.design.label_size, p.highRes))}` : 'Automatic';
        if (empty) { const n = $('#edFits'); n.className = 'fits'; n.innerHTML = ''; }
        else fitsNote($('#edFits'), w, err, E.design, () => renderEditor());
    }, empty);
}, 250);
const redrawEditor = busyThen(() => $('#edStage'), drawEditor);

function bindEditor() {
    const E = S.edit, d = E.design;
    $('#edTitle').oninput = e => { E.title = e.target.value; storeQuiet('designer_edit', S.edit); };
    $('#edNew').onclick = () => {
        const before = S.edit;
        setEdit(newEdit());
        S.editSel = 0;
        renderEditor();
        $('[data-row="0"] .tx').focus();
        if (!isEmptyDesign(before.design)) toast('Started a new label', { label: 'Undo', run: () => { setEdit(before); renderEditor(); } });
    };
    $$('[data-lay]').forEach(b => b.onclick = () => {
        const lay = b.dataset.lay;
        d.print_type = { text: 'text', codetext: 'qrcode_text', image: 'image', code: 'qrcode' }[lay];
        // Next to text, a code without content of its own holds the text
        if (lay === 'code' && !d.code_text) d.code_text = (d.text[0] && d.text[0].text) || 'https://example.com';
        if (lay === 'codetext' && !d.code_text && !hasTextLines(d)) d.code_text = 'https://example.com';
        if (lay === 'codetext' || lay === 'image') { setWide(d, true); d.text.forEach(l => { if (l.align === 'center') l.align = 'left'; }); }
        if (lay === 'codetext' && d.qrcode_size === '10') d.qrcode_size = '14';
        if (lay !== 'code' && !d.text.length) d.text.push(L('Text', 80, true, 'left'));
        renderEditor();
    });
    if ($('#edCode')) $('#edCode').oninput = e => { d.code_text = fromDisplay(e.target.value, d.code_text); redrawEditor(); };
    if ($('#edBarcode')) $('#edBarcode').onchange = e => { d.barcode_type = e.target.value; renderEditor(); };
    if ($('#edFile')) $('#edFile').onchange = e => readImages(e.target.files);
    if ($('#edFamily')) $('#edFamily').onchange = e => {
        if (!e.target.value) return;
        d.text.forEach(l => { l.bold = wantsBold(l); l.font = fontOf(e.target.value, l.bold); });
        renderEditor();
    };

    if ($('#edAdd')) $('#edAdd').onclick = () => {
        const last = d.text[d.text.length - 1];
        d.text.push(L('', last ? Math.max(20, Math.round(last.size * .6)) : 50, false, last ? last.align : 'center', { font: fontOf(familyOf(last && last.font), false) }));
        S.editSel = d.text.length - 1;
        renderEditor();
        const inp = $(`[data-row="${S.editSel}"] .tx`);
        if (inp) inp.focus();
    };
    $$('[data-tok]').forEach(b => b.onclick = () => {
        const row = $(`[data-row="${S.editSel}"] .tx`) || $('[data-row] .tx');
        if (row) insertAt(row, b.dataset.tok);
    });
    // A blank goes where the cursor was last: a line or the code
    if ($('#edCode')) $('#edCode').addEventListener('focus', () => { S.blankTarget = 'code'; });
    $$('[data-row] .tx').forEach(tx => tx.addEventListener('focus', () => { S.blankTarget = 'line'; }));
    if ($('#edBlank')) $('#edBlank').onclick = () => openBlankDialog(d, () => S.blankTarget === 'code' && $('#edCode') ? $('#edCode') : ($(`[data-row="${S.editSel}"] .tx`) || $('[data-row] .tx')));
    $$('[data-row]').forEach(row => {
        const i = +row.dataset.row, l = d.text[i];
        const tx = $('.tx', row);
        tx.addEventListener('focus', () => { S.editSel = i; $$('[data-row]').forEach(r => r.classList.toggle('sel', r === row)); });
        tx.addEventListener('input', () => { l.text = fromDisplay(tx.value, l.text); redrawEditor(); });
        tx.addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) { e.preventDefault(); $('#edAdd').click(); }
            if (e.key === 'Backspace' && !tx.value && d.text.length > 1) { e.preventDefault(); d.text.splice(i, 1); S.editSel = Math.max(0, i - 1); renderEditor(); const p = $(`[data-row="${S.editSel}"] .tx`); if (p) p.focus(); }
        });
        $('input.mini', row).addEventListener('input', e => { const v = Math.round(+e.target.value); if (v > 0) { l.size = String(v); redrawEditor(); } });
        $('select.lf', row).onchange = e => { l.bold = wantsBold(l); l.font = fontOf(e.target.value, l.bold); S.editSel = i; renderEditor(); };
        $('select.lsp', row).onchange = e => { l.line_spacing = e.target.value; S.editSel = i; renderEditor(); };
        $$('button[data-act]', row).forEach(b => b.onclick = () => {
            const act = b.dataset.act;
            if (act === 'todo') l.checkbox = !l.checkbox;
            if (act === 'red') l.color = l.color === 'red' ? 'black' : 'red';
            if (act === 'bold') { l.bold = !wantsBold(l); l.font = fontOf(familyOf(l.font), l.bold); }
            if (act === 'align') l.align = { left: 'center', center: 'right', right: 'left' }[l.align] || 'left';
            if (act === 'invert') l.inverted = !l.inverted;
            if (act === 'del') d.text.splice(i, 1);
            S.editSel = i;
            renderEditor();
        });
    });
    if ($('#edUseTape')) $('#edUseTape').onclick = () => { setSize(d, S.tape); renderEditor(); };
    $('#edSize').onchange = e => { setSize(d, e.target.value); renderEditor(); };
    $$('[data-along]').forEach(b => b.onclick = () => { setRunsAlong(d, b.dataset.along === '1'); renderEditor(); });
    $$('[data-frame]').forEach(b => b.onclick = () => { setFrame(d, b.dataset.frame); renderEditor(); });
    $$('[data-shrink]').forEach(b => b.onclick = () => { d.shrink_text = b.dataset.shrink; renderEditor(); });
    // Code
    if ($('#edQrSize')) $('#edQrSize').addEventListener('input', e => { const v = Math.round(+e.target.value); if (v > 0) { d.qrcode_size = String(v); redrawEditor(); } });
    if ($('#edQrCorr')) $('#edQrCorr').onchange = e => { d.qrcode_correction = e.target.value; redrawEditor(); };
    if ($('#edGap')) $('#edGap').addEventListener('input', e => {
        const v = e.target.value.trim();
        if (v !== '' && !(+v >= 0)) return;
        d.image_text_gap = v === '' ? '' : String(mmToDots(+v, dpiOf(d.label_size)));
        redrawEditor();
    });
    // Image
    $$('[data-imode]').forEach(b => b.onclick = () => { d.image_mode = b.dataset.imode; renderEditor(); });
    if ($('#edThr')) $('#edThr').addEventListener('input', e => { d.image_bw_threshold = e.target.value; $('#edThrV').textContent = e.target.value; redrawEditor(); });
    $$('[data-fit]').forEach(b => b.onclick = () => { d.image_fit = b.dataset.fit; renderEditor(); });
    if ($('#edScale')) $('#edScale').addEventListener('input', e => { const v = Math.round(+e.target.value); if (v > 0) { d.image_scaling_factor = String(v); redrawEditor(); } });
    $$('[data-rot]').forEach(b => b.onclick = () => { d.image_rotation = b.dataset.rot; renderEditor(); });
    if ($('#edRot')) {
        $('#edRot').addEventListener('input', e => { const v = +e.target.value; if (e.target.value !== '' && v >= 0 && v < 360) { d.image_rotation = String(Math.round(v)); redrawEditor(); } });
        $('#edRot').addEventListener('change', () => renderEditor());
    }
    $$('[data-crop]').forEach(b => b.onclick = () => { d.image_crop = b.dataset.crop; renderEditor(); });
    if ($('#edPages')) $('#edPages').addEventListener('input', e => { d.pdf_pages = e.target.value.trim(); redrawEditor(); });

    // Frame details
    $$('[data-frame-k]').forEach(inp => inp.addEventListener('change', () => {
        const mm = parseFloat(inp.value);
        if (!(mm >= 0)) return;
        d[inp.dataset.frameK] = String(Math.max(inp.dataset.frameK === 'border_thickness' ? 1 : 0, mmToDots(mm, dpiOf(d.label_size))));
        fitMarginsToFrame(d);
        renderEditor();
    }));
    $$('[data-bcolor]').forEach(b => b.onclick = () => { d.border_color = b.dataset.bcolor; renderEditor(); });
    // Symbols, undo and redo
    if ($('#tplInfoBtn')) $('#tplInfoBtn').onclick = () => toggleVars($('#tplInfoBtn'), $('#tplInfo'));
    if ($('#edSym')) $('#edSym').onclick = () => openSymbols($(`[data-row="${S.editSel}"] .tx`) || $('[data-row] .tx'));
    $('#edUndo').onclick = () => stepHistory(-1);
    $('#edRedo').onclick = () => stepHistory(1);
    $$('[data-margin]').forEach(inp => inp.addEventListener('input', () => {
        const mm = parseFloat(inp.value);
        if (!(mm >= 0)) return;
        d['margin_' + inp.dataset.margin] = String(mmToDots(mm, dpiOf(d.label_size)));
        redrawEditor();
    }));
    bindCopies($('.ed-top'));
    // The count stays when the editor is drawn again, e.g. after Bold
    $('#edCopies').addEventListener('input', () => { E.copies = copiesIn(document, '#edCopies'); });
    $('#edPrint').onclick = () => printJob({ title: E.title, design: d, values: E.values || {}, copies: copiesIn(document, '#edCopies'), source: E.origin });
    $('#edSave').onclick = async () => {
        const over = E.origin && E.origin.kind === 'saved' ? E.origin.id : null;
        try {
            const name = await saveLabel(E.title, d, over);
            E.origin = { kind: 'saved', id: name };
            storeQuiet('designer_edit', S.edit);
            $('#edSave').innerHTML = '<i class="fa-regular fa-bookmark"></i><span class="lbl">Save</span>';
            $('#edSave').title = 'Save';
            toast(over ? `Saved "${E.title}"` : `"${E.title}" is now one of your saved labels`, { label: blanksOf(E.design).length ? 'Fill in and print' : 'Open in the wizard', run: () => { location.hash = 'fill/' + encodeURIComponent(name); } });
        } catch (e) { toast(e.message); }
    };
}

function openBlankDialog(d, target) {
    const used = blanksOf(d);
    const suggest = ['What', 'Name', 'Where', 'Number', 'Note'].find(n => !used.includes(n)) || 'Blank ' + (used.length + 1);
    let kind = guessKind(suggest), chosen = false;
    openLayer(`<div class="dialog narrow" role="dialog" aria-label="Add a blank">
        <div class="dialog-h"><div class="grow"><h2>Add a blank</h2><p>Whoever prints the label fills it in.</p></div>
            <button class="btn quiet icon" type="button" data-close title="Close"><i class="fa-solid fa-xmark"></i></button></div>
        <form class="fields" id="blankForm" novalidate>
            <label><span class="label-s">Name, shown next to the field</span><input class="field" id="bName" value="${esc(suggest)}" autocomplete="off"></label>
            <div><span class="label-s">Kind</span><div class="seg" id="bKinds">${BLANK_KINDS.map(([v, n]) => `<button type="button" data-bkind="${v}">${n}</button>`).join('')}</div></div>
            <label id="bDefWrap"><span class="label-s">Default value, optional</span><input class="field" id="bDef" autocomplete="off" placeholder="Empty"></label>
            <p class="muted small" id="bDateNote" hidden>Date blanks start at today's date.</p>
            <p class="err small" id="bErr" hidden style="padding:0;text-align:left"></p>
            <div class="acts"><button class="btn-print" type="submit" id="bInsert"></button></div>
        </form></div>`);
    const name = $('#bName'), def = $('#bDef');
    const sync = () => {
        if (!chosen) kind = guessKind(name.value.trim());
        $$('[data-bkind]').forEach(b => b.classList.toggle('on', b.dataset.bkind === kind));
        $('#bDefWrap').hidden = kind === 'date';
        $('#bDateNote').hidden = kind !== 'date';
        def.type = kind === 'number' ? 'number' : 'text';
        $('#bInsert').textContent = `Insert [${name.value.trim() || '...'}]`;
    };
    $$('[data-bkind]').forEach(b => b.onclick = () => { kind = b.dataset.bkind; chosen = true; sync(); });
    name.addEventListener('input', sync);
    sync();
    name.select();
    name.focus();
    $('#blankForm').onsubmit = e => {
        e.preventDefault();
        const n = name.value.trim();
        const err = !n ? 'Give the blank a name.' : /[\[\]{}\n]/.test(n) ? 'Brackets cannot be part of the name.'
            : ['today', 'counter', 'id'].includes(n.toLowerCase()) ? `[${n}] is a variable already, choose another name.` : '';
        if (err) { $('#bErr').textContent = err; $('#bErr').hidden = false; return; }
        d.fields = d.fields || {};
        d.fields[n] = Object.assign({ type: kind }, kind !== 'date' && def.value.trim() ? { default: def.value.trim() } : {});
        closeLayer(true);
        const input = target();
        if (input) insertAt(input, `[${n}]`);
    };
}

// Images and PDFs, chosen, dropped or pasted, replace those of the label.
// Several are printed one after the other, each as a label of its own.
const isImageFile = f => /^image\/|pdf$/.test(f.type);
function readImages(files) {
    const read = file => new Promise((resolve, reject) => {
        const fr = new FileReader();
        fr.onload = () => {
            const [head, data] = String(fr.result).split(',');
            resolve({ data, mime: (head.match(/data:([^;]+)/) || [])[1] || file.type, name: file.name || 'image' });
        };
        fr.onerror = () => reject(fr.error);
        fr.readAsDataURL(file);
    });
    files = Array.from(files).filter(isImageFile);
    if (!files.length) return;
    Promise.all(files.map(read)).then(([first, ...more]) => {
        const d = S.edit.design;
        d.image = first;
        if (more.length) d.more_images = more; else delete d.more_images;
        d.print_type = 'image';
        renderEditor();
    }).catch(() => toast('The file could not be read'));
}

// A blank stays marked until something is typed into it
function markBlanks(root) {
    for (const box of $$('.fill-in, #libFields', root)) {
        $$('input[data-blank]', box).forEach(inp => inp.classList.toggle('need', !inp.value.trim()));
        const open = $$('input.need', box).length;
        box.classList.toggle('pending', open > 0);
        const n = $('.open-count', box);
        if (n) { n.textContent = open === 1 ? '1 to fill in' : `${open} to fill in`; n.hidden = !open; }
    }
}
document.addEventListener('input', e => { if (e.target.matches && e.target.matches('input[data-blank]')) markBlanks(document); });

document.addEventListener('paste', e => {
    if (currentView() !== 'edit' || !S.edit) return;
    const files = Array.from(e.clipboardData ? e.clipboardData.files : []).filter(isImageFile);
    if (files.length) { e.preventDefault(); readImages(files); }
});
// Files dropped anywhere on the editor
document.addEventListener('dragover', e => {
    if (currentView() === 'edit' && S.edit && e.dataTransfer && e.dataTransfer.types.includes('Files')) e.preventDefault();
});
document.addEventListener('drop', e => {
    if (currentView() !== 'edit' || !S.edit || !e.dataTransfer || !e.dataTransfer.files.length) return;
    e.preventDefault();
    readImages(e.dataTransfer.files);
});

// --- Printer -------------------------------------------------------------------------

// The printer chip in the top bar opens this panel: printers, the tape,
// quality and cutting. Past prints are in the wizard under "Printed".
let tapeChanged = false;
function openPrinterPanel() {
    tapeChanged = false;
    const inner = `<div class="pp">${printerPanelHTML()}</div>`;
    const done = () => { if (tapeChanged) route(); };
    if (matchMedia('(max-width: 760px)').matches) sheet(inner, done);
    else openLayer(`<div class="pop">${inner}</div>`, 'pop-layer', done);
    bindPrinterPanel();
}

function printerPanelHTML() {
    const list = S.printers.map(p => {
        const st = printerState(p);
        const on = S.printer && p.path === S.printer.path;
        return `<button class="prow${on ? ' on' : ''}" type="button" data-printer="${esc(p.path)}"><span class="dot ${st.cls}"></span>
            <span class="grow"><b>${esc(p.model || 'Printer')}</b><span class="sm">${esc(st.text)}, ${esc((p.path || '').replace('file://', ''))}</span></span>
            ${on ? '<i class="fa-solid fa-check"></i>' : ''}</button>`;
    }).join('') || '<p class="empty">No printer answered.</p>';
    return `<div class="row"><h2 class="grow">Printer</h2><button class="btn quiet icon" type="button" data-close title="Close"><i class="fa-solid fa-xmark"></i></button></div>
        <div class="plist">${list}</div>
        <button class="btn sm" type="button" id="ppRefresh" style="align-self:flex-start"><i class="fa-solid fa-magnifying-glass"></i>Look for printers</button>
        ${settingsCard()}
        <a class="link" href="#wizard/printed"><i class="fa-solid fa-clock-rotate-left"></i> Printed labels</a>`;
}

function bindPrinterPanel() {
    const box = $('.pp');
    if (!box) return;
    const redraw = () => { box.innerHTML = printerPanelHTML(); bindPrinterPanel(); };
    const saveSettings = () => { store('designer_settings', S.settings); detectTape(); renderPrinterChip(); tapeChanged = true; redraw(); };
    $$('[data-printer]', box).forEach(b => b.onclick = () => {
        S.printer = S.printers.find(p => p.path === b.dataset.printer) || S.printer;
        store('designer_printer', S.printer.path);
        detectTape();
        renderPrinterChip();
        tapeChanged = true;
        redraw();
    });
    $('#ppRefresh', box).onclick = async () => { await refreshPrinters(); redraw(); };
    $('#setTape', box).onchange = e => {
        S.settings.tapes = Object.assign({}, S.settings.tapes, { [printerKey()]: e.target.value });
        if (!e.target.value) delete S.settings.tapes[printerKey()];
        saveSettings();
    };
    $('#setDate', box).onchange = e => { S.settings.dateFmt = e.target.value; saveSettings(); };
    $('#qInfoBtn', box).onclick = () => {
        S.qualityInfo = !S.qualityInfo;
        $('#qInfo', box).hidden = !S.qualityInfo;
        $('#qInfoBtn', box).setAttribute('aria-expanded', String(S.qualityInfo));
    };
    $$('[data-q]', box).forEach(b => b.onclick = () => { S.settings.quality = b.dataset.q; saveSettings(); });
    $$('[data-c]', box).forEach(b => b.onclick = () => { S.settings.cut = b.dataset.c; saveSettings(); });
    $('a.link', box).onclick = () => closeLayer(true);
}

const listJoin = a => a.length < 2 ? a.join('') : a.slice(0, -1).join(', ') + ' and ' + a[a.length - 1];

function settingsCard() {
    const p = S.printer;
    const noHigh = p && C.models_without_600_dpi.includes(p.model);
    const q = S.settings.quality, c = S.settings.cut;
    const note = S.tapeManual ? 'Set by hand.' : S.tapeAssumed ? 'The printer does not report its tape, the default is assumed.' : 'Read from the printer.';
    return `<div class="settings">
        <div><label class="label-s" for="setTape">Tape in the printer</label>
            <select class="field" id="setTape"><option value="">Automatic${S.tapeManual ? '' : ': ' + esc(sizeName(S.tape))}</option>
            ${sizeOptions(manualTape(), x => esc(sizeName(x.id)))}
            </select><span class="muted small">${note}</span></div>
        <div><div class="row" style="gap:.35rem;margin-bottom:.3rem"><span class="label-s" style="margin:0">Quality</span>
            <button type="button" class="info-btn" id="qInfoBtn" aria-expanded="${!!S.qualityInfo}" aria-controls="qInfo" title="About the quality settings"><i class="fa-solid fa-circle-info"></i><span class="sr-only">About the quality settings</span></button></div>
            <div class="seg">
            <button type="button" data-q="fast" class="${q === 'fast' ? 'on' : ''}">Fast</button>
            <button type="button" data-q="quality" class="${q === 'quality' ? 'on' : ''}">Quality</button>
            <button type="button" data-q="high" class="${q === 'high' ? 'on' : ''}"${noHigh ? ' disabled title="This printer cannot print at 600 dpi"' : ''}>600 dpi, slow</button></div>
            ${noHigh ? `<span class="muted small">The ${esc(p.model)} cannot print at 600 dpi.</span>` : ''}
            <div class="info-box" id="qInfo"${S.qualityInfo ? '' : ' hidden'}>
                <p>Fast printing is quicker, the print is a little less sharp.</p>
                <p>600 dpi prints twice as many lines along the label. Labels usually come out much darker, printing is slower, and the result is often worse than at 300 dpi. It is only confirmed for the QL-800. The ${esc(listJoin(C.models_without_600_dpi))} cannot print at 600 dpi.</p>
            </div></div>
        <div><label class="label-s" for="setDate">Dates on labels</label>
            <select class="field" id="setDate">${[['', 'Like the browser: ' + formatDate('2026-11-01', browserDateFormat())], ['%Y-%m-%d', '2026-11-01'], ['%d.%m.%Y', '01.11.2026'], ['%d/%m/%Y', '01/11/2026'], ['%m/%d/%Y', '11/01/2026']]
                .map(([v, n]) => `<option value="${esc(v)}"${(S.settings.dateFmt || '') === v ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select>
            <span class="muted small">For "Today's date" and date blanks. Labels that already contain a date keep its format.</span></div>
        <div><span class="label-s">Cutting</span><div class="seg">
            <button type="button" data-c="each" class="${c === 'each' ? 'on' : ''}">After each label</button>
            <button type="button" data-c="last" class="${c === 'last' ? 'on' : ''}">After the last label</button></div></div>
    </div>`;
}

// --- Search -----------------------------------------------------------------------------

const COMMANDS = [
    { name: 'New label', icon: 'plus', run: () => openEditor(newEdit()) },
    { name: 'Go to the editor', icon: 'pen-ruler', run: () => { location.hash = 'edit'; } },
    { name: 'Go to the wizard', icon: 'wand-magic-sparkles', run: () => { location.hash = 'wizard'; } },
    { name: 'Printer and settings', icon: 'print', run: () => openPrinterPanel() },
    { name: 'Printed labels', icon: 'clock-rotate-left', run: () => { location.hash = 'wizard/printed'; } },
    { name: 'Dark mode', icon: 'moon', run: () => setTheme('dark') },
    { name: 'Light mode', icon: 'sun', run: () => setTheme('light') },
    { name: 'Follow the system for dark mode', icon: 'circle-half-stroke', run: () => setTheme(null) },
];

function openSearch() {
    openLayer(`<div class="palette" role="dialog" aria-label="Search">
        <div class="q-in"><i class="fa-solid fa-magnifying-glass"></i><input id="palQ" placeholder="Search labels, or type > for commands" autocomplete="off"><span class="kbd">Esc</span></div>
        <div class="res-list" id="palRes"></div>
        <div class="foot"><span><span class="kbd">Up</span> <span class="kbd">Down</span> choose</span><span><span class="kbd">Enter</span> open</span><span>Type <b>&gt;</b> for commands</span></div>
    </div>`);
    const q = $('#palQ');
    let results = [], sel = 0;
    const draw = () => {
        const text = q.value.trim().toLowerCase();
        const groups = [];
        if (text.startsWith('>')) {
            const t = text.slice(1).trim();
            groups.push(['Commands', COMMANDS.filter(c => c.name.toLowerCase().includes(t)).map(c => ({ cmd: c }))]);
        } else {
            const m = s => !text || s.toLowerCase().includes(text);
            groups.push(['Saved labels', S.saved.filter(x => m(x.title)).slice(0, text ? 5 : 6).map(x => ({ item: savedItem(x) }))]);
            const seen = new Set();
            groups.push(['Printed before', S.history.filter(h => { const n = entryName(h); if (seen.has(n) || !m(n + ' ' + h.title)) return false; seen.add(n); return true; }).slice(0, text ? 3 : 2).map(h => ({ item: histItem(h) }))]);
            if (text) {
                groups.push(['Ready-made', S.demoList.filter(d => d.language === S.lang && m(d.title + ' ' + d.catName)).sort((a, b) => fitsTape(b.label_size_id) - fitsTape(a.label_size_id)).slice(0, 5).map(d => ({ item: demoItem(d) }))]);
            }
        }
        results = [];
        let html = '';
        for (const [name, list] of groups) {
            if (!list.length) continue;
            html += `<div class="grp-h">${esc(name)}</div>`;
            for (const r of list) {
                const i = results.push(r) - 1;
                if (r.cmd) html += `<button class="res" type="button" data-r="${i}"><span class="ico"><i class="fa-solid fa-${r.cmd.icon}"></i></span><span><b>${esc(r.cmd.name)}</b></span><span class="act">run</span></button>`;
                else {
                    const it = r.item;
                    const blanks = it.design ? blanksOf(it.design) : [];
                    const sub = (it.sub || '') + (fitsTape(it.size) ? '' : ', needs ' + sizeName(it.size));
                    html += `<button class="res" type="button" data-r="${i}"><div class="mini-bed">${thumbSrc(it)}</div><span><b>${esc(it.title)}</b><span class="sm">${esc(sub)}</span></span><span class="act">${it.kind === 'printed' ? 'print again' : it.kind === 'saved' ? 'open' : 'use'}</span></button>`;
                }
            }
        }
        $('#palRes').innerHTML = html || '<p class="empty" style="padding:.8rem">Nothing found. Try another word, or &gt; for commands.</p>';
        attachLazy($('#palRes'));
        sel = Math.min(sel, Math.max(0, results.length - 1));
        mark();
        $$('[data-r]', $('#palRes')).forEach(b => b.onclick = () => choose(+b.dataset.r));
    };
    const mark = () => $$('[data-r]', $('#palRes')).forEach(b => b.classList.toggle('on', +b.dataset.r === sel));
    const choose = i => {
        const r = results[i];
        if (!r) return;
        closeLayer(true);
        if (r.cmd) return r.cmd.run();
        const it = r.item;
        if (it.kind === 'printed') return again(it.entry);
        if (it.kind === 'saved') { location.hash = 'fill/' + encodeURIComponent(it.id); return; }
        S.libSel = it;
        if (it.kind === 'demo') { const d = S.demoList.find(x => x.id === it.id); if (d) S.libCat = d.cat; }
        if (location.hash === '#templates/' + S.libCat) renderLabels(); else location.hash = 'templates/' + S.libCat;
    };
    q.addEventListener('input', () => { sel = 0; draw(); });
    q.addEventListener('keydown', e => {
        if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(results.length - 1, sel + 1); mark(); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); mark(); }
        else if (e.key === 'Enter') { e.preventDefault(); choose(sel); }
    });
    draw();
    q.focus();
}

// --- Theme and start ----------------------------------------------------------------------

function setTheme(mode) {
    if (mode) document.documentElement.setAttribute('data-theme', mode);
    else document.documentElement.removeAttribute('data-theme');
    try { mode ? localStorage.setItem('designer_theme', mode) : localStorage.removeItem('designer_theme'); } catch (e) { }
}

document.addEventListener('keydown', e => {
    const mod = e.ctrlKey || e.metaKey;
    if (currentView() === 'edit' && $('#layer').hidden) {
        if (mod && e.key === 'Enter') { e.preventDefault(); const b = $('#edPrint'); if (b && !b.disabled) b.click(); return; }
        // In text fields the browser undoes the typing itself
        const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
        if (mod && !typing && e.key.toLowerCase() === 'z') { e.preventDefault(); stepHistory(e.shiftKey ? 1 : -1); return; }
        if (mod && !typing && e.key.toLowerCase() === 'y') { e.preventDefault(); stepHistory(1); return; }
    }
    if (mod && e.key.toLowerCase() === 'k') { e.preventDefault(); openSearch(); }
    else if (e.key === 'Escape' && !$('#layer').hidden) closeLayer();
});
$('#openSearch').onclick = openSearch;
$('#printerChip').onclick = openPrinterPanel;
$('#themeToggle').onclick = () => {
    const dark = document.documentElement.getAttribute('data-theme') === 'dark' ||
        (!document.documentElement.getAttribute('data-theme') && matchMedia('(prefers-color-scheme: dark)').matches);
    setTheme(dark ? 'light' : 'dark');
};
window.addEventListener('hashchange', route);

// Content shared with the installed app from the share menu of a phone
// (share_target in the manifest) arrives as ?shared=<token>: a file becomes
// the image of a new label, a link its QR code, a text its text
async function receiveShared() {
    const params = new URLSearchParams(location.search);
    const token = params.get('shared');
    if (!token) return;
    // Reloading the page must not fetch it again
    params.delete('shared');
    history.replaceState(null, '', location.pathname + (params.toString() ? '?' + params : '') + '#edit');
    let item;
    try {
        const r = await fetch(`${BASE}/share?token=${encodeURIComponent(token)}`);
        item = await r.json();
        if (!r.ok) throw new Error(item.message);
    } catch (e) {
        toast(e.message || 'What was shared could not be loaded, share it again.');
        return;
    }
    const edit = newEdit();
    const d = edit.design;
    if (item.data) {
        d.print_type = 'image';
        d.image = { data: item.data, mime: item.type, name: item.name };
        edit.title = item.name.replace(/\.[^.]+$/, '') || edit.title;
    } else if (item.url) {
        d.print_type = 'qrcode';
        d.code_text = item.url;
    } else {
        d.text = item.text.split(/\r?\n/).filter(t => t.trim()).map((t, i) => L(t, i ? 44 : 70, i === 0));
    }
    const before = S.edit || load('designer_edit', null);
    setEdit(edit);
    S.editSel = 0;
    if (before && !isEmptyDesign(before.design)) toast('Started a new label with what was shared', { label: 'Undo', run: () => { setEdit(before); renderEditor(); } });
}

(async function start() {
    renderPrinterChip();
    await Promise.all([refreshPrinters(), loadDemos(), loadSaved(), loadBarcodes()]);
    await receiveShared();
    // Printing state from a page that was closed while printing is unknown
    S.history.forEach(h => { if (h.state === 'printing') { h.state = 'failed'; h.message = 'The page was closed while printing'; } });
    route();
    setInterval(refreshPrinters, 20000);
})();
