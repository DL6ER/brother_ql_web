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
// Runs fn at once. Called while it runs, it runs once more afterwards with the
// newest arguments: a preview follows typing without requests piling up.
// keyOf tells what a run is for, e.g. its stage: a run for another one
// starts at once instead of waiting for the one before.
function latest(fn, keyOf = () => null) {
    let running = null, next = null;
    const run = async args => {
        const me = running = { key: keyOf() };
        try { await fn(...args); } catch (e) { console.error(e); }
        if (running !== me) return;
        running = null;
        if (next) { const a = next; next = null; run(a); }
    };
    return (...args) => {
        if (running && running.key === keyOf()) next = args;
        else { next = null; run(args); }
    };
}
// The date of the browser, not of UTC
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };

// What belongs to this instance of the label designer is kept apart from
// other instances below the same host, e.g. behind a reverse proxy below
// different paths: its draft, print history, printer and settings. The theme
// and the language of the demo labels belong to the person, they are shared.
const own = key => C.storage_prefix + key;

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
function toast(text, action, ms = 0) {
    const el = $('#toast');
    el.innerHTML = esc(text) + (action ? ` <button class="btn sm quiet" style="color:inherit;text-decoration:underline" type="button">${esc(action.label)}</button>` : '');
    el.hidden = false;
    if (action) el.querySelector('button').onclick = () => { el.hidden = true; action.run(); };
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, ms || (action ? 6000 : 3500));
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
// ZINK tape of the VC-500W, which prints in any color
const isColorTape = id => !!SIZES[id] && SIZES[id].palette === 'color';
// What the server's driver of a printer model offers, see Driver.capabilities()
const ANY_PRINTER = { qualities: ['fast', 'quality', 'high'], high_res: true, cutter: true, half_cut: false, chain: false, feed_waste: false, job_stages: false, colors: 'black', never_cut: '', leaves_turned: false, lead_mm: 0, vendor: '', manual_cutter: false };
const capsOf = model => (C.models || {})[model] || ANY_PRINTER;
const printerCaps = () => capsOf(S.printer && S.printer.model);
// The maker and the model of a printer, as the label designer names it
// Models other than Brother ones are named with their maker, e.g. PUQU/Q00
const modelOnly = model => (model || '').split('/').pop();
const printerName = p => [capsOf(p && p.model).vendor, p && modelOnly(p.model)].filter(Boolean).join(' ');

function sizeName(id) {
    const s = SIZES[id];
    if (!s) return id;
    if (isPtouch(id)) return s.name;
    if (isColorTape(id)) return `${s.tape[0]} mm color tape`;
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
// A font of a label that is not installed stays chosen until another one is
const missingFont = family => FONTS[family] ? '' : `<option value="${esc(family)}" selected>${esc(family)}, not installed</option>`;

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
        image_mode: def.image_mode, image_bw_threshold: String(def.image_bw_threshold), image_fit: '1', image_crop: '0', image_contrast: '0',
        image_scaling_factor: '100', image_rotation: '0', text_fit: 'shrink', print_color: 'black', high_res: '0', pdf_pages: '',
        // Empty: the server chooses the distance from the size of the text
        image_text_gap: '',
        // Empty: above the text across the tape, left of it along the tape
        image_position: '',
        // Empty: cut as set for the printer, else as saved with the label
        cut: '', half_cut: '', chain: '',
        // Empty: a Cat.7 cable
        cable_flag: '0', cable_diameter: '',
        // Empty: an endless label is as long as its content, else its
        // length in millimeters as cut
        label_length: '',
        // Where the content is on a label with room to spare
        vertical_align: 'center'
    };
}

// Layouts with an image: alone or with text, or with a code as well
const withImage = d => d.print_type === 'image' || d.print_type === 'image_qrcode_text';
// The files of a label, each prints as a label of its own. The editor
// previews one of them at a time.
const labelFiles = d => withImage(d) && d.image ? [d.image, ...(d.more_images || [])] : [];
const fileShown = E => Math.min(E.fileShown || 0, Math.max(0, labelFiles(E.design).length - 1));

// How the text is sized: as set, shrunk where it does not fit, or as large as
// fits. Labels kept from before have shrink_text only.
const TEXT_FITS = [['none', 'As set'], ['shrink', 'Shrink to fit'], ['fill', 'Fill the label']];
const textFit = d => d.text_fit || (d.shrink_text === '1' ? 'shrink' : 'none');
function setTextFit(d, fit) {
    d.text_fit = fit;
    delete d.shrink_text;
}

// Where the content is between the top and bottom margins, as the text
// reads. An endless label across the tape is as long as its content, unless
// it has a length.
const VERTICAL_ALIGNS = [['top', 'Top'], ['center', 'Middle'], ['bottom', 'Bottom']];
const verticalAlign = d => d.vertical_align || 'center';
const NO_ROOM = 'Endless labels end where the content ends, there is no room to place it in';
const hasRoom = d => !(SIZES[d.label_size] && isEndless(SIZES[d.label_size]) && !runsAlong(d) && !hasFixedLength(d));

// Labels as the API stores them (text as JSON, booleans as true/false)
function normalize(raw) {
    const d = Object.assign(baseDesign(raw.label_size || C.default_label_size), {});
    for (const k of Object.keys(d)) if (raw[k] !== undefined && raw[k] !== null) d[k] = raw[k];
    let text = raw.text;
    if (typeof text === 'string') { try { text = JSON.parse(text); } catch (e) { text = []; } }
    d.text = (Array.isArray(text) ? text : []).map(l => Object.assign(L('', 40), l, { size: String(l.size || 40) }));
    // Flags and the text size as the server reads them when it prints the
    // label. One it refuses stays as it is, the preview tells why, until
    // another is chosen.
    const given = v => v !== undefined && v !== null && v !== '';
    for (const [k, def] of [['image_fit', '1'], ['image_crop', '0']]) d[k] = given(raw[k]) ? String(raw[k]) : def;
    d.text_fit = given(raw.text_fit) ? String(raw.text_fit) : '';
    // Cutting saved through the API e.g. as half_cut: 0 or chain: true
    for (const k of CUT_KEYS) d[k] = !given(raw[k]) ? '' : raw[k] === true ? '1' : raw[k] === false ? '0' : String(raw[k]);
    d.cable_flag = !given(raw.cable_flag) ? '0' : raw.cable_flag === true ? '1' : raw.cable_flag === false ? '0' : String(raw.cable_flag);
    d.image_contrast = !given(raw.image_contrast) ? '0' : [true, 'true'].includes(raw.image_contrast) ? '1' : [false, 'false'].includes(raw.image_contrast) ? '0' : String(raw.image_contrast);
    if (!given(raw.text_fit) && given(raw.shrink_text)) d.shrink_text = String(raw.shrink_text);
    if (raw.image_data) d.image = { data: raw.image_data, mime: raw.image_mime || 'image/png', name: raw.image_name || 'image.png' };
    if (raw.fields && typeof raw.fields === 'object') d.fields = raw.fields;
    // Values of blanks saved with it through the API, also one left empty
    // on purpose: filled in where no other is given, kept when it is saved
    if (raw.blanks && typeof raw.blanks === 'object' && !Array.isArray(raw.blanks)) {
        d.blanks = Object.fromEntries(Object.entries(raw.blanks).map(([name, v]) => [name, String(v)]));
    }
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

// Templates as text fields show them: [Name] for a blank, [today], [counter]
// and [id]
const TEMPLATE_RE = /\{\{(?:blank:([^}]+)|(datetime):[^}]+|counter(:\d+)?|short-uuid)\}\}/g;
function shownAs(m) {
    if (m[1] !== undefined) return `[${m[1]}]`;
    if (m[2]) return '[today]';
    if (m[0].startsWith('{{counter')) return `[counter${m[3] || ''}]`;
    return '[id]';
}
// The text as its field shows it, and where each template is shown
function displayed(text) {
    text = String(text || '');
    const tokens = [];
    let out = '', last = 0;
    for (const m of text.matchAll(TEMPLATE_RE)) {
        out += text.slice(last, m.index);
        tokens.push({ at: out.length, shown: shownAs(m), template: m[0] });
        out += shownAs(m);
        last = m.index + m[0].length;
    }
    return { text: out + text.slice(last), tokens };
}
function toDisplay(text) {
    return displayed(text).text;
}
// The text of a field with its templates. The text before and after the
// change is as shown before, a template shown there stays as it was, e.g. a
// [today] keeps its format. One in the change is new, a [today] gets the
// format set in the printer panel. caret, where the change ends, e.g. the
// cursor after typing, tells which of two same ones was deleted. Typed anew
// as a whole (inOrder), e.g. in the wizard, each new [today] takes the
// format of the dates there were, in their order.
function fromDisplay(text, previous = '', caret = null, inOrder = false) {
    const old = displayed(previous), was = old.text, now = String(text || '');
    // What was put in ends at the caret: the same before it starts before it
    const maxPre = caret === null ? now.length : caret - Math.max(0, now.length - was.length), maxSuf = caret === null ? now.length : now.length - caret;
    let pre = 0, suf = 0;
    while (pre < was.length && pre < now.length && pre < maxPre && was[pre] === now[pre]) pre++;
    while (suf < was.length - pre && suf < now.length - pre && suf < maxSuf && was[was.length - 1 - suf] === now[now.length - 1 - suf]) suf++;
    // The templates kept where they were, each [today] of the others takes
    // the format of the next date that was not kept
    const kept = [...now.matchAll(/\[([^\]\n]+)\]/g)].map(({ 0: m, index: i }) => {
        const at = i + m.length <= pre ? i : i >= now.length - suf ? i - now.length + was.length : -1;
        return old.tokens.find(t => t.at === at && t.shown === m);
    });
    const formats = inOrder ? old.tokens.filter(t => !kept.includes(t) && t.template.startsWith('{{datetime:')).map(t => t.template.slice(11, -2)) : [];
    let seen = 0, k = 0;
    return now.replace(/\[([^\]\n]+)\]/g, (m, name) => {
        const own = kept[seen++];
        if (own) return own.template;
        const n = name.trim();
        if (n.toLowerCase() === 'today') return `{{datetime:${formats.length ? formats[Math.min(k++, formats.length - 1)] : dateFormat()}}}`;
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
const guessKind = name => /(^|\s)on$|\bdate\b/i.test(name) ? 'date' : /^(box|number|portions|no\.?|count)$/i.test(name) ? 'number' : 'text';

function fieldType(item, name) {
    return (blankFields(item)[name] || {}).type || guessKind(name);
}

// The blanks of a design as the server fills them in: the values given, and
// the settings of each blank. A date is written in its format, else as set
// in the printer panel.
function blankRequest(design, values) {
    const blanks = {}, fields = {};
    for (const name of blanksOf(design)) {
        const f = Object.assign({}, (design.fields || {})[name], { type: fieldType({ design }, name) });
        if (f.type === 'date' && !f.format) f.format = dateFormat();
        fields[name] = f;
        // Also one left empty: it is printed empty, without its default
        const v = values ? values[name] : undefined;
        if (v !== undefined) blanks[name] = String(v);
    }
    return { blanks, fields };
}

// --- Talking to the server -------------------------------------------------

function b64ToBlob(b64, mime) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Blob([bytes], { type: mime });
}

// 600 dpi, if chosen and the printer can print it
const highRes = () => S.settings.quality === 'high' && (!S.printer || printerCaps().high_res);
// The dots of a label size per inch as the server tells, e.g. 180 on TZe
// tape, 300 or 600 on DK rolls
const dpiOf = (labelSize, high = highRes()) => SIZES[labelSize] ? SIZES[labelSize].dpi[high ? 1 : 0] : 300 * (high ? 2 : 1);

// How the labels of a job are cut: after each label, after the last one or
// not at all, set for each printer. P-touch printers and the VC-500W also
// half cut between labels they do not cut, the backing stays whole. P-touch
// printers with chain printing leave the last label in the printer: the next
// print cuts it off, without the tape fed before it.
const CUTS = [['each', 'After each label'], ['last', 'After the last label'], ['none', 'Never']];
const CUT_KEYS = ['cut', 'half_cut', 'chain'];
// How a label size is cut: as the printer cuts it if the printer prints
// it, else as its first printer with a cutter would, if there is one
const sizeCaps = id => {
    const models = SIZES[id] ? SIZES[id].models : [], model = S.printer && S.printer.model;
    return capsOf(models.includes(model) ? model : models.find(m => !noCutter(capsOf(m))) || models[0]);
};
// A printer that does not leave labels uncut tells why, the server refuses it
const neverWhy = k => k.never_cut || '';
// Labels of a printer without a cutter are torn off or cut with its lever
const noCutter = k => k.cutter === false;
const noCutterWhy = k => k.manual_cutter ? 'This printer has no automatic cutter' : 'This printer has no cutter';
const usableCut = (c, k) => noCutter(k) ? Object.assign(c, { cut: 'none' })
    : neverWhy(k) && c.cut === 'none' ? Object.assign(c, { cut: 'last' }) : c;
// Printers without a setting of their own cut as set before for all
const printerCut = () => usableCut(Object.assign({ cut: S.settings.cut || 'each', half_cut: '1', chain: '0' }, (S.settings.cuts || {})[printerKey()]), printerCaps());
// P-touch printers cut after the last label unless chain printing, so not
// cutting at all is chain printing, as the server decides it too
const chainOf = (c, k) => k.chain && (c.chain === '1' || c.cut === 'none');
const halfOf = (c, k) => k.half_cut && c.half_cut === '1';
// A label saved with its own way of cutting keeps it
const cutOf = d => d && d.cut ? usableCut({ cut: d.cut, half_cut: d.half_cut || '1', chain: d.chain || '0' }, sizeCaps(d.label_size)) : printerCut();

function cutText(c, k) {
    if (noCutter(k)) return k.manual_cutter ? 'No automatic cutter, cut the labels off with the lever on the printer.' : 'No cutter, the labels are torn off.';
    const half = halfOf(c, k), chain = chainOf(c, k);
    const text = c.cut === 'each' ? 'Cut after each label'
        : (half ? 'Half cut between the labels, ' : '') + (c.cut === 'last' ? (half ? 'cut after the last' : 'Cut after the last label') : (half ? 'not cut' : 'Not cut'));
    return text + '.' + (chain ? ' The last label stays in the printer, the next print cuts it off.' : c.cut === 'none' ? ' The last label stays in the printer.' : '')
        + (k.feed_waste ? ' The tape fed before the first label is waste, short labels get some extra tape.' : '');
}

// The same in a few words, next to the preview
function cutShort(c, k) {
    if (noCutter(k)) return k.manual_cutter ? 'Cut by hand' : 'Torn off';
    const half = halfOf(c, k) && c.cut !== 'each';
    return { each: 'Cut after each label', last: 'Cut after the last', none: 'Not cut' }[c.cut] + (half ? ', half cuts' : '') + (k.chain && c.chain === '1' && c.cut !== 'none' ? ', chain' : '');
}

// Three copies leaving the printer, as the display of a P-touch printer
// shows them: the waste fed before the first label, half cut off with half
// cuts or a cut after each label (as a PT-E550W did it), cuts, half cuts and
// the last label left in the printer. Not to scale: a PT-E550W fed 22 mm
// before the first label, 11 mm after a chain print, and gave short labels
// some more tape.
function cutStrip(c, k, files = 1) {
    const half = halfOf(c, k);
    const between = c.cut === 'each' ? 'full' : half ? 'half' : 'none';
    // The last label stays in the printer with chain printing, and on QL
    // printers where it is not cut
    const end = noCutter(k) ? 'none' : chainOf(c, k) || c.cut === 'none' ? 'chain' : 'full';
    // Copies of one file, all A, of several as the setting orders them: the
    // first three printed, the first of them on the right
    const [third, second, first] = printOrder(files).slice(0, 3).reverse().map(i => `<span class="lab">${'ABC'[Math.min(i, 2)]}</span>`);
    const mark = k => `<span class="cm ${k}"></span>`;
    const text = esc(cutText(c, k));
    const waste = k.feed_waste ? `${mark(half || c.cut === 'each' ? 'half' : 'full')}<span class="waste"></span>` : '';
    // The printer on the left, as on its display: the tape leaves it to the
    // right, the waste first
    return `<span class="cut-strip" title="${text}" role="img" aria-label="${text}"><span class="out"></span>${mark(end)}${third}${mark(between)}${second}${mark(between)}${first}${waste}</span>`;
}

// The files of copies in the order they are printed: as sets, ABAB, or
// grouped, AABB, two copies of several files, three of one
const collated = () => S.settings.collate !== '0';
const printOrder = files => {
    const ids = [...Array(files).keys()], copies = files > 1 ? 2 : 3;
    return collated() ? Array(copies).fill(ids).flat() : ids.flatMap(i => Array(copies).fill(i));
};

// marks: a preview shows the blanks still to fill in. blanks: those of
// blankRequest(), e.g. as a cached preview was made for them.
function formFor(design, copies = 1, forPrint = false, values = null, marks = false, blanks = blankRequest(design, values)) {
    const fd = new FormData();
    for (const [k, v] of Object.entries(design)) {
        // The blanks are sent with their values below
        if (k === 'text' || k === 'image' || k === 'more_images' || k === 'fields' || k === 'blanks') continue;
        // Cutting does not change the label, it is sent for printing only
        if (CUT_KEYS.includes(k)) continue;
        fd.append(k, typeof v === 'boolean' ? (v ? '1' : '0') : String(v));
    }
    // Without red on the tape red parts print black, the preview shows that.
    // Other colors the server makes red or black on black and red tape.
    const red = redTape(design) || isColorTape(design.label_size);
    if (!red) {
        fd.set('border_color', 'black');
        fd.set('print_color', 'black');
        if (['red_and_black', 'colored'].includes(design.image_mode)) fd.set('image_mode', 'black_and_white');
    }
    fd.append('text', JSON.stringify(design.text.map(l => ({
        text: l.text, size: String(l.size), font: l.font, align: l.align, inverted: !!l.inverted,
        checkbox: !!l.checkbox, color: red ? l.color || 'black' : 'black', line_spacing: String(l.line_spacing || '100')
    }))));
    if (design.image && withImage(design)) {
        // The preview shows the first image, all of them are printed
        for (const im of [design.image].concat(forPrint ? design.more_images || [] : [])) fd.append('image', b64ToBlob(im.data, im.mime), im.name);
    }
    if (Object.keys(blanks.fields).length) {
        fd.append('blanks', JSON.stringify(blanks.blanks));
        fd.append('fields', JSON.stringify(blanks.fields));
        if (marks) fd.append('mark_blanks', '1');
    }
    fd.append('print_count', String(copies));
    // The quality is a setting of the printer, previews are made with its
    // resolution as well
    fd.set('high_res', highRes() ? '1' : '0');
    if (forPrint) {
        fd.set('fast_printing', S.settings.quality === 'fast' ? '1' : '0');
        fd.set('collate', collated() ? '1' : '0');
        const c = cutOf(design);
        fd.set('cut', c.cut);
        fd.set('half_cut', c.half_cut);
        fd.set('chain', c.chain);
    }
    if (S.printer && S.printer.path) fd.append('printer', S.printer.path);
    if (S.printer && S.printer.model) fd.append('model', S.printer.model);
    return fd;
}

async function post(path, body, signal = undefined) {
    // Without files as a plain form, the server reads it faster
    if (body instanceof FormData && ![...body.values()].some(v => v instanceof Blob)) body = new URLSearchParams(body);
    return fetch(API + path, { method: 'POST', body, signal, headers: { 'X-Requested-With': 'XMLHttpRequest' } });
}
// A preview that does not come gives way to the next one
let previewTimeout = 60000;
// AbortSignal.timeout, also in browsers without it, e.g. Safari before 16
function timeoutSignal(ms) {
    if (AbortSignal.timeout) return AbortSignal.timeout(ms);
    const control = new AbortController();
    const timer = setTimeout(() => control.abort(new DOMException('signal timed out', 'TimeoutError')), ms);
    // Ended once the preview is there
    control.signal.done = () => clearTimeout(timer);
    return control.signal;
}
// Why a preview failed on its way
const previewFailed = e => new Error(['TimeoutError', 'AbortError'].includes(e.name) ? 'The preview took too long' : 'The preview could not be made');

// Everything the server renders (previews and thumbnails) goes through one
// queue. The server says how many at a time: its CPU cores minus one, at
// most 16. Jobs that are no longer needed are dropped before they run.
const PARALLEL = Math.max(1, C.preview_parallel || 2);
const previewCache = new Map();
let running = 0;
// Previews looked at, e.g. of the editor, go before thumbnails
const waiting = [], waitingFirst = [];
function nextJob() { while (running < PARALLEL && (waitingFirst.length || waiting.length)) (waitingFirst.shift() || waiting.shift())(); }
// A request is shared by everyone who asks for the same. Dropped because its
// first caller no longer needs it, it is made again for a caller who does.
function shared(p, stale, again) {
    return p.catch(e => { if (e.message === 'stale' && !(stale && stale())) return again(); throw e; });
}
function limit(task, stale, first = false) {
    let queued = null;
    const p = new Promise((resolve, reject) => {
        const run = () => {
            // Started, the promise kept for others no longer holds the job
            // and what it was made for, e.g. a design
            queued = null;
            const job = task;
            task = null;
            if (stale && stale()) { reject(new Error('stale')); return; }
            running++;
            job().then(resolve, reject).finally(() => { running--; nextJob(); });
        };
        if (running < PARALLEL) run(); else { queued = run; (first ? waitingFirst : waiting).push(run); }
    });
    // Asked for again by a preview looked at, it goes before thumbnails
    p.first = () => {
        const i = waiting.indexOf(queued);
        if (i >= 0) { waiting.splice(i, 1); waitingFirst.push(queued); }
    };
    return p;
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
// Two hashes of 32 bits: an image's key also names it in the undo history
// and in the label kept for the next visit, two images must not share one
function fingerprint(s) {
    let h1 = 0x811c9dc5, h2 = 0x9e3779b9;
    for (let i = 0; i < s.length; i++) {
        const c = s.charCodeAt(i);
        h1 = Math.imul(h1 ^ c, 0x01000193);
        h2 = Math.imul(h2 ^ c, 0x5bd1e995);
        h2 ^= h2 >>> 15;
    }
    return (h1 >>> 0).toString(36) + (h2 >>> 0).toString(36) + s.length.toString(36);
}

// Images can be large: each one's fingerprint is made once
const imageKeys = new WeakMap();
function imageKey(im) {
    let k = imageKeys.get(im);
    if (!k) { k = im.mime + fingerprint(im.data); imageKeys.set(im, k); }
    return k;
}

// Previews that dropped out of the cache are released once no image on the
// page shows them any more. Those dropped just now may still be on their
// way onto the page.
const dropped = new Map();
function release(url) {
    if (url) dropped.set(url, Date.now());
    const shown = new Set($$('img').map(i => i.src));
    for (const [u, t] of dropped) if (Date.now() - t > 10000 && !shown.has(u)) { URL.revokeObjectURL(u); dropped.delete(u); }
    // Those still waiting are looked at again, also when nothing else drops out
    clearTimeout(release.timer);
    if (dropped.size) release.timer = setTimeout(release, 11000);
}

function preview(design, values, stale, first = false) {
    // In the resolution it is printed with: for 600 dpi the server makes the
    // label twice as large
    const asked = Object.assign({}, design, { high_res: highRes() ? '1' : '0' });
    // Dates the server fills in, e.g. of a [today], change with the time: a
    // preview kept from another minute is not used
    const blanks = blankRequest(asked, values || {});
    const shown = JSON.stringify(Object.assign({}, asked, { image: design.image ? imageKey(design.image) : null, more_images: undefined },
        Object.fromEntries(CUT_KEYS.map(k => [k, undefined]))));
    const dated = shown.includes('{{datetime:') || Object.entries(blanks.fields).some(([name, f]) => f.type === 'date' && !(name in blanks.blanks));
    const key = JSON.stringify([shown, blanks, dated ? Math.floor(Date.now() / 60000) : 0]);
    if (previewCache.has(key)) {
        // The most recently used stay longest
        const p = previewCache.get(key);
        previewCache.delete(key);
        previewCache.set(key, p);
        if (first && p.first) p.first();
        return shared(p, stale, () => preview(design, values, stale, first));
    }
    // As the label is now, it may change while the preview waits in line.
    // Images are replaced, never changed, they need no copy.
    const d = Object.assign(clone(Object.assign({}, asked, { image: undefined, more_images: undefined })),
        { image: design.image, more_images: design.more_images });
    values = clone(values || {});
    const p = limit(async () => {
        let r;
        const signal = timeoutSignal(previewTimeout);
        // The time limit is on the image as well, which may come later
        let blob;
        try {
            try {
                r = await post('/preview', formFor(d, 1, false, values, true, blanks), signal);
            } catch (e) {
                throw previewFailed(e);
            }
            if (!r.ok) {
                let msg = 'The preview could not be made';
                try { msg = (await r.json()).message || msg; } catch (e) { }
                // The server refuses the label, it cannot print it either
                throw Object.assign(new Error(msg), { refused: true });
            }
            try {
                blob = await r.blob();
            } catch (e) {
                throw previewFailed(e);
            }
        } finally {
            if (signal.done) signal.done();
        }
        return {
            url: URL.createObjectURL(blob), highRes: d.high_res === '1', warning: r.headers.get('X-Label-Warning') || '',
            auto: r.headers.get('X-Label-Orientation') === 'auto', gap: +(r.headers.get('X-Label-Image-Text-Gap') || 0),
            side: r.headers.get('X-Label-Image-Position') || '', faint: r.headers.get('X-Image-Faint') === '1'
        };
    }, stale, first);
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
        lazyWaiting.delete(e.target);
        const job = e.target._render;
        if (!job) continue;
        if (job.url) fetchImage(job.url).then(u => { e.target.src = u; }).catch(() => { });
        else if (job.item) typedThumb(e.target, job);
        else preview(job.design, job.values).then(p => { e.target.src = p.url; }).catch(() => { });
    }
}, { rootMargin: '200px' });
// Thumbnails waiting to come into view
const lazyWaiting = new Set();
function attachLazy(root) {
    // Those drawn over before they came into view are let go
    for (const img of lazyWaiting) if (!img.isConnected) { lazyObserver.unobserve(img); lazyWaiting.delete(img); }
    $$('img[data-lazy]', root).forEach(img => {
        const job = LAZY.get(img.dataset.lazy);
        if (job) { img._render = job; LAZY.delete(img.dataset.lazy); lazyObserver.observe(img); lazyWaiting.add(img); }
    });
    // Those of pages drawn over before they were shown. Ones made just now
    // may not be on the page yet.
    if (LAZY.size > 1000) {
        const onPage = new Set($$('img[data-lazy]').map(img => img.dataset.lazy));
        for (const [id, job] of [...LAZY]) if (Date.now() - job.made > 10000 && !onPage.has(id)) LAZY.delete(id);
    }
}
const LAZY = new Map();
let lazySeq = 0;
function lazyImg(job) {
    const id = 'l' + (++lazySeq);
    LAZY.set(id, Object.assign(job, { made: Date.now() }));
    return `<img data-lazy="${id}" alt="">`;
}
function lazyTag(design, values) {
    return lazyImg({ design, values });
}
function lazyUrlTag(url) {
    return lazyImg({ url });
}
// A template with the text typed into the wizard, made when it comes into view
function lazyTypedTag(item) {
    return lazyImg({ item, gen: S.typedGen });
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
    history: load(own('designer_history'), []),
    lang: load('designer_lang', (navigator.language || 'en').toLowerCase().startsWith('de') ? 'de' : 'en'),
    fitsOnly: true,
    libCatChosen: false, typedGen: 0,
    // tapes: the tape set by hand for each printer
    settings: Object.assign({ quality: 'quality', cut: 'each', tapes: {}, dateFmt: '', largePreview: false }, load(own('designer_settings'), {})),
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
        if (storeQuiet(own('designer_history'), S.history)) return;
    }
    store(own('designer_history'), S.history);
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
// Whether a label is a cable flag, read as the server reads it
function flagOn(d) {
    const v = String(d.cable_flag).trim().toLowerCase();
    return v === 'true' || (/^[+-]?\d+$/.test(v) && parseInt(v, 10) > 0);
}
// Another label size: die-cut labels start wide, on endless tape the text
// keeps its direction
function setSize(d, id) {
    const along = runsAlong(d);
    d.label_size = id;
    if (isDieCut(id)) d.cable_flag = '0';
    return isDieCut(id) ? setWide(d, true) : setRunsAlong(d, along);
}
// The same in the editor, which says when it turned a cable flag off
function changeSize(d, id) {
    const flag = flagOn(d);
    setSize(d, id);
    if (flag && !flagOn(d)) toast('Cable flag turned off, it needs continuous tape', { label: 'Undo', run: () => stepHistory(-1) });
}

function fitsTape(sizeId) {
    if (sizeId === S.tape) return true;
    const a = SIZES[sizeId], b = SIZES[S.tape];
    return !!(a && b && a.form === b.form && a.tape[0] === b.tape[0] && a.tape[1] === b.tape[1] && (!a.red || S.tapeRedUnknown));
}

// --- Printer status ----------------------------------------------------------

// look: also look for Bluetooth printers that did not answer before, which
// the status every 20 s leaves alone
async function refreshPrinters(look = false) {
    try {
        const r = await fetch(API + '/printer_status' + (look ? '?look=1' : ''));
        const data = await r.json();
        S.printers = Array.isArray(data.printers) ? data.printers : [data];
        const chosen = load(own('designer_printer'), null);
        const kept = S.printers.find(p => p.path === chosen);
        S.printer = kept || S.printers.find(p => p.path === data.selected) || S.printers[0] || null;
        // The chosen one comes back by itself when it is there again
        if (chosen && !kept && S.printer && S.printerGone !== chosen) {
            S.printerGone = chosen;
            toast(`The printer ${chosen.replace('file://', '')} is not connected, labels print on ${thePrinter(S.printer)} (${S.printer.path.replace('file://', '')})`);
        }
        if (kept) S.printerGone = null;
    } catch (e) {
        // A single failed poll keeps what is known, the next one corrects it
        if (!S.printers.length) S.printer = null;
    }
    detectTape();
    renderPrinterChip();
    // An open printer panel shows the new status, drawn again only when it
    // changed so that what is focused in it stays focused
    const box = $('.pp');
    if (box) {
        const fresh = document.createElement('div');
        fresh.innerHTML = printerPanelHTML();
        if (fresh.innerHTML !== box.innerHTML) { box.replaceChildren(...fresh.childNodes); bindPrinterPanel(); }
    }
    // The note of the editor on the tape and the tape list follow the tape the
    // printer reports
    const size = $('#edSize');
    if (size && S.edit) {
        const note = document.createElement('div');
        note.innerHTML = tapeNote(S.edit.design);
        const old = size.parentElement.querySelector('.tape-note');
        if ((old ? old.outerHTML : '') !== note.innerHTML) {
            if (old) old.replaceWith(...note.children);
            else size.before(...note.children);
            if ($('#edUseTape')) $('#edUseTape').onclick = () => { changeSize(S.edit.design, S.tape); renderEditor(); };
        }
        $$('option', size).forEach(o => { o.textContent = tapeName(o.value); });
    }
}

const printerKey = () => (S.printer && S.printer.path) || '';
// A printer is called by its name, given in PRINTERS, else by its model.
// Sentences put "the" before a model, not before a name.
const printerLabel = p => (p && (p.name || printerName(p))) || 'Printer';
const thePrinter = (p, start = false) => p && p.name ? p.name : `${start ? 'The' : 'the'} ${(p && printerName(p)) || 'printer'}`;
const manualTape = () => (S.settings.tapes || {})[printerKey()] || '';

// Whether the printer takes a label size, any size if its model is unknown
function printerTakes(s) {
    const model = S.printer && S.printer.model;
    const known = model && C.label_sizes.some(x => x.models.includes(model));
    return known ? s.models.includes(model) : s.models.length > 0;
}

function detectTape() {
    const p = S.printer;
    // What the printer reported says nothing about a tape set by hand
    S.tapeUnknown = '';
    S.tapeRedUnknown = false;
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
        // QL printers do not tell whether a roll is black and red
        S.tapeRedUnknown = !!found && !red && !p.text_color;
        if (!found) S.tapeUnknown = p.media_length ? `${p.media_width} x ${p.media_length} mm` : `${p.media_width} mm`;
    }
    // Assumed is the default size, or the first one the printer takes if it
    // does not take that, e.g. a PUQU Q00 no 62 mm endless tape
    const fallback = C.label_sizes.find(s => s.id === C.default_label_size && printerTakes(s)) || C.label_sizes.find(printerTakes);
    S.tape = found ? found.id : fallback ? fallback.id : C.default_label_size;
    S.tapeAssumed = !found;
}

// What a VC-500W does: the stage of the label it prints, the label of how
// many, how far along it is
function jobText(job) {
    return job.stage + (job.percent !== undefined ? ` ${job.percent} %` : '') + (job.labels > 1 ? `, label ${job.label} of ${job.labels}` : '');
}

// The tape left in the cassette of a VC-500W
function tapeLeft(p) {
    if (!p || !p.tape_remaining_mm || !p.tape_length_mm) return '';
    const m = mm => (mm / 1000).toFixed(1);
    return `${m(p.tape_remaining_mm)} m of ${m(p.tape_length_mm)} m left`;
}

function printerState(p) {
    if (!p) return { cls: 'off', text: 'No printer' };
    if (p.errors && p.errors.length) return { cls: 'bad', text: p.errors[0] };
    // Still being asked, e.g. a printer on Bluetooth that takes a few seconds
    if (p.status_type === 'Asking the printer') return { cls: 'off', text: 'Asking the printer…' };
    if (p.job) return { cls: '', text: jobText(p.job) };
    if (/simulat/i.test(p.status_type || p.phase_type || '')) return { cls: '', text: 'Ready (simulated)' };
    return { cls: '', text: 'Ready' };
}

function renderPrinterChip() {
    const chip = $('#printerChip');
    const st = printerState(S.printer);
    // Phones show the model only, a printer named in PRINTERS its name
    const vendor = S.printer && !S.printer.name && capsOf(S.printer.model).vendor;
    chip.innerHTML = `<span class="dot ${st.cls}"></span><b>${vendor ? `<span class="vendor">${esc(vendor)} </span>` : ''}${esc(S.printer ? (S.printer.name || modelOnly(S.printer.model)) : 'No printer')}</b><span class="tape">${esc(sizeName(S.tape))}</span>`;
    // A long name is cut short in the chip
    chip.title = S.printer && S.printer.name ? `${S.printer.name}: ${st.text}` : st.text;
    // The printer on the stages shows the same, they are drawn again only
    // with another preview
    $$('.stage .printer .led').forEach(led => { led.className = `led ${st.cls}`; led.title = st.text; });
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
        source: job.source || null, printer: S.printer ? S.printer.model : '', printerName: (S.printer && S.printer.name) || '', state: 'printing', message: ''
    };
    S.history.unshift(entry);
    saveHistory();
    updateCounts();
    toast(`Printing ${entry.copies} ${entry.copies === 1 ? 'label' : 'labels'}...`);
    // A VC-500W takes about 40 s for a label, what it does is shown meanwhile
    const followed = S.printer && printerCaps().job_stages ? S.printer.path : '';
    const follow = followed && setInterval(async () => {
        try {
            const data = await (await fetch(API + '/printer_status')).json();
            const p = (Array.isArray(data.printers) ? data.printers : [data]).find(x => x.path === followed);
            if (p && p.job && entry.state === 'printing') toast(jobText(p.job), null, 10000);
        } catch (e) { }
    }, 1500);
    try {
        const r = await post('/print', formFor(job.design, entry.copies, true, job.values));
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
    if (follow) clearInterval(follow);
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
                <p>${esc(entryName(entry))} on ${esc(entry.printerName || thePrinter({ model: entry.printer }))}</p>
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
        const flagged = flagOn(job.design) && isDieCut(S.tape);
        const canAdapt = SIZES[job.design.label_size] && SIZES[S.tape] && !flagged;
        sheet(`<div class="okc warn"><i class="fa-solid fa-ruler-combined"></i></div>
            <h2>This label needs ${esc(need)}</h2>
            <p>${esc(thePrinter(S.printer, true))} has ${esc(have)} loaded${S.tapeManual ? ' (set in the printer settings)' : S.tapeAssumed ? ' (assumed, the printer does not say)' : ''}.</p>
            ${flagged ? '<p>Cable flags need continuous tape.</p>' : ''}
            ${canAdapt ? `<button class="btn-print" data-r="adapt"><i class="fa-solid fa-maximize"></i>Print it on ${esc(have)}</button>` : ''}
            <button class="btn" data-r="anyway">Print anyway</button>
            <button class="btn quiet" data-r="cancel">Cancel</button>`, () => resolve('cancel'), () => resolve('cancel'));
        // The answer first, closing would settle it as canceled
        $$('[data-r]', $('#layer')).forEach(b => b.onclick = () => { resolve(b.dataset.r); closeLayer(true); });
    });
}

// --- Overlays ------------------------------------------------------------------

let onLayerClose = null, onLayerGone = null;
// onGone is called however the overlay goes: closed, also silently, or
// replaced by another one, e.g. to settle a question that waits for it
function layerGone() {
    const cb = onLayerGone;
    onLayerGone = null;
    if (cb) cb();
}
function openLayer(html, cls, onClose, onGone) {
    layerGone();
    const layer = $('#layer');
    layer.className = 'layer ' + (cls || '');
    layer.innerHTML = html;
    layer.hidden = false;
    onLayerClose = onClose || null;
    onLayerGone = onGone || null;
    layer.onclick = e => { if (e.target === layer || e.target.closest('[data-close]')) closeLayer(); };
}
function closeLayer(silent) {
    const layer = $('#layer');
    if (layer.hidden) return;
    layer.hidden = true;
    layer.innerHTML = '';
    layerGone();
    const cb = onLayerClose;
    onLayerClose = null;
    if (cb && !silent) cb();
    // A view to draw anew, e.g. for a tape changed in a panel this layer took
    // the place of. Closed for something else, right after that, unless that
    // drew it already.
    if (S.routeLater) {
        S.routeLater = false;
        if (!silent) route();
        else {
            const drawn = S.drawn;
            // A layer opened by what this one closed for, e.g. a question
            // before printing, is not closed by it: drawn once that closes
            setTimeout(() => {
                if (S.drawn !== drawn) return;
                if ($('#layer').hidden) route();
                else S.routeLater = true;
            });
        }
    }
}
function sheet(inner, onClose, onGone) {
    openLayer(`<div class="sheet"><div class="grab"></div>${inner}</div>`, 'sheet-layer', onClose, onGone);
}
// The label designer works without the internet, a link to a page there asks
// before it opens it
function askToLeave(url) {
    const host = new URL(url).host;
    sheet(`<div class="okc warn"><i class="fa-solid fa-globe"></i></div>
        <h2>Open ${esc(host)}?</h2>
        <p>The link opens a page on the internet in a new tab. The label designer itself works without the internet.</p>
        <button class="btn-print" type="button" data-open><i class="fa-solid fa-arrow-up-right-from-square"></i>Open ${esc(host)}</button>
        <button class="btn quiet" type="button" data-close>Cancel</button>`);
    $('#layer [data-open]').onclick = () => { closeLayer(true); window.open(url, '_blank', 'noopener'); };
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
        S.saved = (data.files || []).map(f => ({ name: f.name, title: f.title || f.name.replace(/\.json$/i, '').replace(/[-_]/g, ' '), size: f.label_size_id || C.default_label_size, mtime: f.mtime }));
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
    // when it is missing, "a.json" would replace a.json instead of a.json.json.
    // A new label replaces none saved under the name in the meantime.
    const body = Object.assign({}, design, { name, title, text: design.text, replace: !!overwrite });
    // Each blank keeps its kind, the server does not guess it, and a date
    // the format it is shown in, like a [today], also when the label is
    // printed through the API: the settings previews send
    body.fields = blankRequest(design, null).fields;
    if (!Object.keys(body.fields).length) delete body.fields;
    delete body.image;
    delete body.more_images;
    if (design.image) Object.assign(body, { image_data: design.image.data, image_mime: design.image.mime, image_name: design.image.name });
    const r = await fetch(API + '/repository/save', { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' } });
    const data = await r.json().catch(() => ({}));
    if (r.status === 409) {
        await loadSaved();
        throw Object.assign(new Error(data.message), { taken: true });
    }
    if (!r.ok || !data.success) throw new Error(data.message || 'The label could not be saved');
    forget(data.name);
    await loadSaved();
    return data.name;
}
// Asks for the name of a label saved for the first time, then calls
// save(title, name), name that of the saved label it is to replace. A saved
// label of the same name is only replaced when asked again.
function saveDialog(title, save) {
    openLayer(`<div class="dialog narrow" role="dialog" aria-label="Save to your labels">
        <div class="dialog-h"><div class="grow"><h2>Save to your labels</h2><p>It is listed under this name in the wizard and the search.</p></div>
            <button class="btn quiet icon" type="button" data-close title="Close"><i class="fa-solid fa-xmark"></i></button></div>
        <form class="fields" id="saveForm" novalidate>
            <label><span class="label-s">Name</span><input class="field" id="saveName" value="${esc(title)}" autocomplete="off"></label>
            <p class="err small" id="saveErr" hidden style="padding:0;text-align:left"></p>
            <div class="acts"><button class="btn-print" type="submit" id="saveGo">Save</button></div>
        </form></div>`);
    const name = $('#saveName'), go = $('#saveGo');
    let replacing = null;
    const say = text => { $('#saveErr').textContent = text; $('#saveErr').hidden = !text; };
    // Closed or replaced while saving, the dialog is gone
    const tell = text => name.isConnected ? say(text) : toast(text);
    name.addEventListener('input', () => { replacing = null; say(''); go.textContent = 'Save'; });
    name.select();
    name.focus();
    $('#saveForm').onsubmit = async e => {
        e.preventDefault();
        // Saved under the name in another browser in the meantime, the list
        // is loaded again and the name checked once more
        for (let tries = 0; tries < 2; tries++) {
            const t = name.value.trim();
            if (!t) return tell('Give the label a name.');
            const taken = S.saved.find(x => x.title.toLowerCase() === t.toLowerCase());
            if (taken && replacing !== taken.name) {
                replacing = taken.name;
                go.textContent = 'Replace it';
                return tell(`"${taken.title}" is one of your saved labels already. Replace it, or give this label another name.`);
            }
            go.disabled = true;
            try {
                await save(t, taken ? taken.name : null);
                if (name.isConnected) closeLayer(true);
                return;
            } catch (err) {
                if (!err.taken || tries) return tell(err.message);
            } finally {
                go.disabled = false;
            }
        }
    };
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
            typed.forEach((t, i) => { if (design.text[i]) design.text[i].text = fromDisplay(t, design.text[i].text, null, true); });
        }
    }
    return { design, values };
}

// What a blank starts with: the value saved with the label through the
// API, else its default. Blank settings show it, new settings replace it.
function blankStart(item, name) {
    const saved = (item.design && item.design.blanks) || {};
    return name in saved ? saved[name] : (blankFields(item)[name] || {}).default || '';
}

// What a blank starts with now: a date without a value saved or a default
// at today
function startOf(item, name) {
    const start = blankStart(item, name);
    return start || name in ((item.design && item.design.blanks) || {}) || fieldType(item, name) !== 'date' ? start : today();
}

function defaults(item) {
    return Object.fromEntries(blanksOf(item.design).map(name => [name, startOf(item, name)]));
}

// The values of the blanks, those still at what they started with (starts)
// as it is now, e.g. a date at today also past midnight
function valuesNow(values, starts, item) {
    const v = Object.assign({}, values);
    for (const [name, start] of Object.entries(starts || {})) if (v[name] === start) v[name] = startOf(item, name);
    return v;
}
const editItem = E => ({ kind: E.origin && E.origin.kind === 'saved' ? 'saved' : 'edit', id: E.origin && E.origin.id, design: E.design });

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
    S.routeLater = false;
    // How often the view was drawn, see closeLayer()
    S.drawn = (S.drawn || 0) + 1;
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
function stageHTML(id, zoom = false) {
    return `<div class="stage busy" id="${id}"${zoom ? ' data-zoom' : ''}>${SPIN}<div class="wait">Making the preview...</div></div>`;
}
// Shows the spinner at once, while an update still waits. The preview shown
// stays until a newer one is there.
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
    // A newer preview is shown already, or the stage was replaced meanwhile.
    // While typing, the preview of the text before shows until the newest
    // is there, so that the stage follows.
    const outdated = () => seq <= (el._shown || 0) || !el.isConnected;
    const shown = () => { el._shown = seq; if (seq !== el._seq) el.classList.add('busy'); };
    // Only the newest preview tells about the label as it is now: whether it
    // fits and can be printed. One of the text before that fails is not shown.
    const newest = () => seq === el._seq;
    el.classList.add('busy');
    let p;
    try {
        p = await preview(design, values, null, true);
    } catch (e) {
        if (outdated() || !newest()) return;
        el._view = null;
        el._fit = null;
        el.className = 'stage';
        el.innerHTML = `${SPIN}<div class="err"><i class="fa-solid fa-triangle-exclamation"></i> ${esc(e.message)}</div>`;
        shown();
        if (onWarning) onWarning(e.message, true, null, !!e.refused);
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
    // Some printers leave blank paper before each label, which was between
    // their printhead and cutter
    const lead = isEndless(size) ? sizeCaps(design.label_size).lead_mm : 0;
    // An endless label of a given length is as long as that when cut
    const lengthMm = !isEndless(size) ? size.tape[1] : hasLength(design) && +design.label_length > 0 ? +design.label_length : along + lead;
    // Labels that leave the printer top first are shown leaving it upwards,
    // the printer below them. Those that leave sideways have it to their left.
    const turned = !side && sizeCaps(design.label_size).leaves_turned;
    // The cut after the label, and how copies are cut
    const caps = sizeCaps(design.label_size), cutNow = cutOf(design);
    // Kept to fit the label again when the stage changes its size
    el._view = {
        url: p.url, w, h, side, turned, dpi, blank, color: isColorTape(design.label_size),
        lead, leadDots: lead / 25.4 * dpi,
        form: (isEndless(size) ? '' : ' diecut') + (size.form === 'round_die_cut' ? ' round' : ''),
        dims: [side ? lengthMm : size.tape[0], side ? size.tape[0] : lengthMm],
        cutEnd: chainOf(cutNow, caps) ? 'chain' : cutNow.cut === 'none' ? 'none' : 'full',
        cuts: `${cutStrip(cutNow, caps, Math.max(1, labelFiles(design).length))}<span>${esc(cutShort(cutNow, caps))}</span>`,
    };
    if ('zoom' in el.dataset && !el._watched) { el._watched = true; stageSizes.observe(el); }
    placeOnStage(el, true);
    el.classList.remove('busy');
    shown();
    if (onWarning && newest()) onWarning(p.warning, false, p);
}

// The large preview shows labels at most about four times their real size,
// small labels do not turn into large dots
const LARGE_PX_PER_MM = 16;
// Only the editor offers it, where the stage takes the height of the window
const largePreview = el => 'zoom' in el.dataset && !!S.settings.largePreview && matchMedia('(min-width: 761px)').matches;
// The large preview also follows the height of its stage, e.g. when fields to
// fill in show above it. Widths change with a scroll bar the label itself can
// bring, they follow the window only.
const stageSizes = new ResizeObserver(entries => entries.forEach(({ target: el, contentRect: r }) => {
    if (!el.isConnected) return stageSizes.unobserve(el);
    const height = Math.round(r.height);
    if (height === el._height) return;
    el._height = height;
    if (largePreview(el)) placeOnStage(el);
}));
window.addEventListener('resize', () => $$('.stage').forEach(el => placeOnStage(el)));

// Fits the label shown into its stage. Drawn again only when its size
// changes, or always with redraw.
function placeOnStage(el, redraw = false) {
    const v = el._view;
    if (!v || !el.isConnected) return;
    const { w, h, side, turned } = v, large = largePreview(el);
    const box = el.getBoundingClientRect();
    // The printer takes room on top, or on the left for labels that leave it sideways
    const maxW = Math.max(140, box.width - 110 - (side ? 70 : 0)), maxH = Math.max(110, Math.min(440, window.innerHeight * .5) - (side ? 0 : 60));
    // With the blank paper before it, at the end that leaves first
    const wide = w + (side ? v.leadDots : 0), tall = h + (side ? 0 : v.leadDots);
    let k = Math.min(0.85, maxW / wide, maxH / tall);
    if (large) {
        // The label fills the stage, next to the printer and with how copies
        // are cut below it, and is never smaller than otherwise
        const cs = getComputedStyle(el);
        const roomW = box.width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - (side ? 50 : 72);
        const roomH = box.height - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom) - (side ? 64 : 86);
        k = Math.max(k, Math.min(LARGE_PX_PER_MM * 25.4 / v.dpi, roomW / wide, roomH / tall));
    }
    const dw = Math.round(w * k), dh = Math.round(h * k);
    // Dots of black and red labels stay sharp where they are shown large
    const sharp = large && !v.color && k * (window.devicePixelRatio || 1) >= 2;
    const fit = [dw, dh, large, sharp].join();
    if (!redraw && fit === el._fit) return;
    el._fit = fit;
    // Short labels keep the arrow clear of the scissors at the cut
    const short = side ? dw : dh;
    const busy = el.classList.contains('busy');
    el.className = 'stage' + (side ? ' side' : '') + v.form + (turned ? ' turned' : '') + (large ? ' large' : '')
        + (short < 30 ? ' tiny' : short < 50 ? ' short' : '') + (busy ? ' busy' : '');
    // The front of the printer with its exit slot, its name and status light
    const st = printerState(S.printer);
    const led = `<span class="led ${st.cls}" title="${esc(st.text)}"></span>`;
    const model = `<span class="model">${esc(printerLabel(S.printer))}</span>`;
    const printer = side
        ? `<div class="printer side" style="height:${dh + 64}px" aria-hidden="true">${led}${model}<span class="mouth" style="height:${dh + 16}px"></span></div>`
        : `<div class="printer top" style="width:${dw + 72}px" aria-hidden="true">${led}${model}<span class="mouth" style="width:${dw + 16}px"></span></div>`;
    const flow = `<div class="flow" title="Printing direction"><i class="fa-solid fa-${side ? 'arrow-right-long' : turned ? 'arrow-up-long' : 'arrow-down-long'}"></i></div>`;
    const leadPx = Math.round(v.leadDots * k);
    const lead = leadPx ? `<div class="lead" style="${side ? 'width' : 'height'}:${leadPx}px" title="Blank paper, ${v.lead} mm"></div>` : '';
    const label = v.blank ? `<div class="blank-paper" style="aspect-ratio:${w} / ${h}"></div>` : `<img alt="Label preview" src="${v.url}"${sharp ? ' class="dots"' : ''}>`;
    const paper = side ? label + lead : lead + label;
    // How copies are cut: in the corner, or below the large label
    const cuts = `<div class="cuts">${v.cuts}</div>`;
    const zoom = 'zoom' in el.dataset ? `<button class="btn quiet icon zoom" type="button" aria-pressed="${large}" title="${large ? 'Smaller preview' : 'Larger preview'}">
        <i class="fa-solid fa-${large ? 'compress' : 'expand'}"></i><span class="sr-only">Large preview</span></button>` : '';
    el.innerHTML = `${SPIN}${zoom}${printer}<div class="feed" style="width:${dw + (side ? leadPx : 0)}px"><div class="paper">${paper}</div>${flow}
        <div class="cut"${v.cutEnd === 'full' ? '' : ' hidden'}><i class="fa-solid fa-scissors"></i></div>
        <div class="dim-x">${v.dims[0]} mm</div><div class="dim-y">${v.dims[1]} mm</div>${large ? cuts : ''}</div>
        ${large ? '' : cuts}`;
}

// Red parts on tape without red print black
function layoutProblem(d) {
    if (isColorTape(d.label_size)) return '';
    const colored = c => (c || 'black') !== 'black';
    const textColors = d.print_type !== 'qrcode' ? d.text.map(l => l.color) : [];
    const parts = textColors.concat(frameOf(d) !== 'none' ? [d.border_color] : [], d.print_type !== 'text' ? [d.print_color] : []).filter(colored);
    const redParts = parts.length || (withImage(d) && ['red_and_black', 'colored'].includes(d.image_mode));
    if (redParts && !redTape(d)) return 'This tape has no red, the red parts print black.';
    if (parts.some(c => c !== 'red')) return 'This tape prints black and red only, other colors print in one of them.';
    return '';
}

function fitsNote(el, warning, error, design, onShrink) {
    if (!el) return;
    if (!warning && !error && design) warning = layoutProblem(design);
    if (error) { el.className = 'fits warn'; el.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i> Fix the label to print it.'; return; }
    el.className = warning ? 'fits warn' : 'fits';
    el.innerHTML = warning ? `<i class="fa-solid fa-triangle-exclamation"></i> ${esc(warning)}` : '<i class="fa-solid fa-circle-check"></i> Everything fits.';
    if (warning && /text does not fit/i.test(warning) && design && textFit(design) === 'none' && onShrink) {
        el.insertAdjacentHTML('beforeend', ' <button class="btn sm" type="button">Shrink it to fit</button>');
        el.querySelector('button').onclick = () => { setTextFit(design, 'shrink'); onShrink(); };
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
    // Open at once, so that Escape closes it while the lists load
    openLayer('<div class="dialog narrow" role="dialog" aria-label="Insert a symbol"><p class="muted">Loading the symbols</p></div>');
    const loading = $('#layer .dialog');
    const [list, icons] = await Promise.all([loadSymbols(), loadIcons()]);
    if (!loading.isConnected) return;
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

// The codes of {{datetime:<format>}}, with examples made by the server
function showDateCodes() {
    const dc = C.datetime_codes || { example: '', codes: [] };
    const rows = dc.codes.map(([code, meaning, example]) => `<tr><td><code>${esc(code)}</code></td><td>${esc(meaning)}</td><td>${esc(example)}</td></tr>`).join('');
    openLayer(`<div class="dialog narrow" role="dialog" aria-label="Date and time codes">
        <div class="dialog-h"><div class="grow"><h2>Date and time codes</h2><p>For <code>{{datetime:&lt;format&gt;}}</code>, e.g. <code>{{datetime:%d.%m.%Y}}</code>. The examples are for ${esc(dc.example)}.</p></div>
            <button class="btn quiet icon" type="button" data-close title="Close"><i class="fa-solid fa-xmark"></i></button></div>
        <table class="codes"><thead><tr><th>Code</th><th>Stands for</th><th>Example</th></tr></thead><tbody>${rows}</tbody></table></div>`);
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
            <li><code>{{datetime:&lt;format&gt;}}</code> - the current date and time. The format uses <button type="button" class="link" data-dtcodes>date and time codes</button>, e.g. <code>%H:%M:%S %d.%m.%Y</code>.</li>
            <li><code>{{counter[:&lt;start&gt;]}}</code> - a number that counts up, starting at &lt;start&gt; (default 1). When you print several labels at once, each gets the next number.</li>
            <li><code>{{uuid}}</code> - a random UUID, <code>{{short-uuid}}</code> - a shorter random UUID.</li>
            <li><code>{{mdi:&lt;name&gt;}}</code> - the Material Design Icon &lt;name&gt;, e.g. <code>{{mdi:lightbulb}}</code>. The Symbol button finds the names.</li>
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
    // While typing the tiles stay, each shows its last preview until the next
    // one is there
    if ($('[data-look]', box) && box.dataset.tape === S.tape) {
        $$('[data-look]', box).forEach(x => x.classList.remove('sel'));
        showLooks();
        return;
    }
    box.dataset.tape = S.tape;
    box.innerHTML = `<div class="sec-h"><h2>Your text in six looks</h2><span class="muted">for the ${esc(sizeName(S.tape))} tape</span></div>
        <div class="tiles compact">${LOOKS.map((look, i) => `
            <div class="tile" data-look="${i}" role="button" tabindex="0">
                <div class="thumb"><div class="bed" id="flook${i}"></div></div>
                <div class="foot"><div class="grow"><b>${esc(look.name)}</b></div>
                    <button class="btn-print sm" type="button" data-plook="${i}" title="Print"><i class="fa-solid fa-print"></i></button></div>
            </div>`).join('')}</div>`;
    showLooks();
    $$('[data-plook]', box).forEach(b => b.onclick = () => printLook(+b.dataset.plook));
    $$('[data-look]', box).forEach(t => {
        t.onclick = e => { if (!e.target.closest('button')) chooseLook(+t.dataset.look); };
        t.onkeydown = e => {
            if (e.target === t && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); chooseLook(+t.dataset.look); }
        };
    });
}

// While typing, the looks of the newest text follow those on their way
const showLooks = latest(() => Promise.all(LOOKS.map((look, i) => {
    const bed = $('#flook' + i);
    if (!bed) return null;
    const gen = S.typedGen;
    return preview(lookDesign(i), {}, () => gen !== S.typedGen, true).then(async p => {
        const img = new Image();
        img.alt = '';
        img.src = p.url;
        try { await img.decode(); } catch (e) { }
        // A preview of earlier text that comes late does not replace a newer one
        if (gen < (bed._gen || 0)) return;
        bed._gen = gen;
        bed.replaceChildren(img);
    }).catch(() => { });
})), () => $('#flook0'));

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
    office: 'briefcase', storage: 'box', kitchen: 'utensils', garden: 'seedling', events: 'gift', school: 'graduation-cap', kids: 'car-side', letters: 'font'
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
            <p class="muted small" id="libOther" hidden></p>
            <div class="g4" id="libGrid"></div>
        </section>
        <aside class="detail" id="libDetail"></aside>
    </div>`;
    $$('.lib-nav button').forEach(b => b.onclick = () => { S.libCatChosen = true; history.replaceState(null, '', '#templates/' + b.dataset.cat); S.libCat = b.dataset.cat; $$('.lib-nav button').forEach(x => x.classList.toggle('on', x === b)); renderLibGrid(); });
    {
        const ta = $('#wzText');
        const grow = () => { ta.rows = Math.min(6, Math.max(1, ta.value.split('\n').length)); };
        grow();
        // The looks follow at once, the templates with the text after a pause
        const update = debounce(() => {
            renderLibGrid();
            if (S.libSel && S.libSel.kind !== 'look') showDetail(S.libSel);
        }, 250);
        ta.addEventListener('input', () => {
            S.wizardText = ta.value;
            grow();
            S.typedGen++;
            S.libCatChosen = false;
            $$('.lib-nav button').forEach(x => x.classList.remove('on'));
            renderLooks();
            update();
        });
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
    // What the dimmed ones are
    $('#libOther').hidden = items.slice(0, 240).every(it => fitsTape(it.size));
    $('#libOther').textContent = `Dimmed labels are made for another label size than ${sizeName(S.tape)}.`;
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
    // What the blanks started with, those still at it follow it when printed.
    // Printed before, they are as printed then.
    const starts = item.values ? item.starts : defaults(Object.assign({}, item, { design }));
    const work = { design, values };
    const fits = fitsTape(design.label_size);
    box.innerHTML = `
        <div class="row"><h2 class="grow">${esc(item.title)}</h2><button class="btn quiet icon close" type="button" title="Close"><i class="fa-solid fa-xmark"></i></button></div>
        ${stageHTML('libStage')}
        <div>${fits ? `<span class="badge ok"><i class="fa-solid fa-check"></i>Fits the tape in ${esc(thePrinter(S.printer))}</span>`
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
    const draw = () => { if (stage.isConnected) return showOnStage(stage, work.design, work.values, (w, err) => fitsNote(note, w, err, work.design, () => refresh())); };
    const refresh = busyThen(() => stage, latest(draw));
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
        const ok = await printJob({ title: item.title, design: work.design, values: valuesNow(work.values, starts, Object.assign({}, item, { design: work.design })), copies: copiesIn(box, '#libCopies'), source: { kind: item.kind, id: item.id } });
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
    if ($('#libKeep')) $('#libKeep').onclick = () => {
        $('#libMenu').hidden = true;
        saveDialog(item.title, async (title, replacing) => {
            const name = await saveLabel(title, work.design, replacing);
            toast(`"${title}" is now one of your saved labels`, { label: 'Open', run: () => { location.hash = 'fill/' + encodeURIComponent(name); } });
        });
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

// The content of a code, which may have several lines, e.g. a vCard: they
// are not wrapped, the field grows with them. The line break after the tag
// keeps one the code starts with.
const codeRows = text => Math.min(8, Math.max(1, String(text).split('\n').length));
const codeField = (id, code, attrs) =>
    `<textarea class="field code" id="${id}" rows="${codeRows(toDisplay(code))}" wrap="off" spellcheck="false"${attrs}>\n${esc(toDisplay(code))}</textarea>`;

// Labels with blanks get a field per blank. Labels without get a field
// per line, which changes the line itself.
function renderFields(box, item, work, refresh, onEnter) {
    const blanks = blanksOf(work.design);
    let html = '';
    if (blanks.length) {
        html = blanks.map((name, i) => fieldHTML(item, name, work.values[name], i)).join('');
    } else {
        html = work.design.text.map((l, i) => `<div><label class="label-s" for="ln${i}">Line ${i + 1}</label><input class="field" id="ln${i}" data-line="${i}" value="${esc(toDisplay(l.text))}"></div>`).join('');
        if (['qrcode_text', 'qrcode', 'image_qrcode_text'].includes(work.design.print_type)) {
            html += `<div><label class="label-s" for="lnCode">Code content</label>${codeField('lnCode', work.design.code_text, work.design.print_type !== 'qrcode' ? ' placeholder="Empty: the text of the label"' : '')}</div>`;
        }
        if (!work.design.text.length && work.design.print_type === 'image') html += '<p class="muted small">An image label without text. Change the design to replace the image.</p>';
    }
    box.innerHTML = html;
    $$('[data-blank]', box).forEach(inp => inp.addEventListener('input', () => { work.values[inp.dataset.blank] = inp.value; refresh(); }));
    $$('[data-line]', box).forEach(inp => inp.addEventListener('input', () => {
        const l = work.design.text[+inp.dataset.line];
        l.text = fromDisplay(inp.value, l.text, inp.selectionStart);
        refresh();
    }));
    if ($('#lnCode', box)) {
        $('#lnCode', box).addEventListener('input', e => { e.target.rows = codeRows(e.target.value); work.design.code_text = fromDisplay(e.target.value, work.design.code_text, e.target.selectionStart); refresh(); });
        // Enter starts a new line of the code
        $('#lnCode', box).addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); onEnter(); } });
    }
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
    item.starts = Object.assign({}, item.values);
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
    histImages.clear();
    histRefs.clear();
    editorFailed = false;
}

// The label in the editor is kept for the next visit. Its images are
// stored apart, and only when they change. Images too large for the storage
// are left out, the label is kept without them. They are tried again a
// minute later at the earliest, not with each pause in typing.
let storedImages = null, imagesTooLarge = null;
function storeEdit() {
    const d = S.edit.design;
    const images = [d.image, ...(d.more_images || [])].filter(Boolean);
    const keys = images.map(imageKey).join(' ');
    // before: the images stored before, read where they are replaced
    let before, without = false;
    if (keys !== storedImages) {
        if (imagesTooLarge && imagesTooLarge.keys === keys && Date.now() - imagesTooLarge.time < 60000) without = true;
        else {
            try { before = localStorage.getItem(own('designer_edit_images')); } catch (e) { before = null; }
            if (storeQuiet(own('designer_edit_images'), images)) { storedImages = keys; imagesTooLarge = null; }
            else { storedImages = null; imagesTooLarge = { keys, time: Date.now() }; without = true; }
        }
    }
    const ref = im => ({ ref: imageKey(im) });
    const plain = Object.assign({}, d, { image: undefined, more_images: undefined });
    const kept = without ? plain
        : Object.assign({}, d, { image: d.image ? ref(d.image) : undefined, more_images: d.more_images ? d.more_images.map(ref) : undefined });
    const forgetImages = () => { try { localStorage.removeItem(own('designer_edit_images')); } catch (e) { } };
    if (storeQuiet(own('designer_edit'), Object.assign({}, S.edit, { design: kept }))) {
        // Kept without its images, the images stored before belong to no
        // label kept
        if (without) forgetImages();
        return;
    }
    // Kept without its images, which are not tried again before a minute
    // has passed. Images stored before make room for the newest text. Where
    // it finds none even so, the label kept before keeps its images.
    if (before === undefined) try { before = localStorage.getItem(own('designer_edit_images')); } catch (e) { before = null; }
    forgetImages();
    storedImages = null;
    imagesTooLarge = { keys, time: Date.now() };
    if (!storeQuiet(own('designer_edit'), Object.assign({}, S.edit, { design: plain })) && before !== null) {
        try { localStorage.setItem(own('designer_edit_images'), before); } catch (e) { }
    }
}
// Another tab stored its images, they are stored again with the next change
addEventListener('storage', e => { if (e.key === own('designer_edit_images') || e.key === null) storedImages = null; });
function loadEdit() {
    const edit = load(own('designer_edit'), null);
    if (!edit || !edit.design) return edit;
    const images = new Map(load(own('designer_edit_images'), []).map(im => [imageKey(im), im]));
    // Stored before images were kept apart, the image is in the label
    const get = r => (r && r.ref ? images.get(r.ref) : r);
    const d = codeWithText(edit.design);
    if (d.image) d.image = get(d.image);
    if (!d.image) delete d.image;
    if (d.more_images) d.more_images = d.more_images.map(get).filter(Boolean);
    if (d.more_images && !d.more_images.length) delete d.more_images;
    // All found, they are stored as they are: the next change does not
    // store them again
    const kept = [d.image, ...(d.more_images || [])].filter(Boolean);
    if (kept.length === images.size) storedImages = kept.map(imageKey).join(' ');
    return edit;
}

// A code alone from before is a code with its text left empty, its lines
// were not printed. A code without content of its own took them, they stay.
function codeWithText(d) {
    if (d.print_type !== 'qrcode') return d;
    d.print_type = 'qrcode_text';
    if (String(d.code_text || '').trim()) d.text = [Object.assign(d.text[0] || L('', C.defaults.font_size, true), { text: '' })];
    return d;
}

function openEditor({ title, design, origin }) {
    setEdit({ title: title || 'New label', design: codeWithText(clone(design)), origin: origin || null });
    S.focusBlank = true;
    S.editSel = 0;
    if (location.hash === '#edit') renderEditor(); else location.hash = 'edit';
}

function layoutOf(d) {
    if (d.print_type === 'qrcode_text') return 'codetext';
    if (d.print_type === 'image') return 'image';
    if (d.print_type === 'image_qrcode_text') return 'imagecode';
    return 'text';
}

// Where the editor is scrolled to and what has the focus, kept while the
// same label is drawn anew for a change of a setting, e.g. along the tape
function editorPlace() {
    if (S.editShown !== S.edit || !$('#view .ed')) return null;
    const scrolls = ['#view', '#view .ed-panel', '#view .ed-canvas'].map(sel => [sel, $(sel) ? $(sel).scrollTop : 0]);
    const active = document.activeElement;
    let focus = null;
    if (active && active !== document.body && $('#view').contains(active)) {
        // The controls of all lines are alike: which of them it is is kept,
        // after a line was removed the one of the line now in its place
        const sel = active.id ? '#' + CSS.escape(active.id)
            : active.tagName.toLowerCase() + ([...active.attributes].filter(a => a.name.startsWith('data-')).map(a => `[${a.name}="${CSS.escape(a.value)}"]`).join('')
                || (active.classList.length ? '.' + CSS.escape(active.classList[0]) : ''));
        focus = { sel, nth: $$('#view ' + sel).indexOf(active), caret: null };
        // Where the caret is in a field of text, number fields have none
        try { if (typeof active.selectionStart === 'number') focus.caret = [active.selectionStart, active.selectionEnd]; } catch (e) { }
    }
    return { scrolls, focus };
}
function restoreEditorPlace(place) {
    for (const [sel, top] of place.scrolls) if ($(sel)) $(sel).scrollTop = top;
    if (!place.focus) return;
    const all = $$('#view ' + place.focus.sel);
    const el = all[Math.min(place.focus.nth, all.length - 1)];
    if (!el) return;
    el.focus({ preventScroll: true });
    if (place.focus.caret) try { el.setSelectionRange(...place.focus.caret); } catch (e) { }
}

function renderEditor() {
    if (!S.edit) S.edit = loadEdit() || newEdit();
    const place = editorPlace();
    S.editShown = S.edit;
    const E = S.edit, d = E.design;
    const lay = layoutOf(d);
    const hasCode = lay === 'codetext' || lay === 'imagecode';
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
                ${[['text', 'Text', '<i class="t"></i>'], ['image', 'Image', '<i class="q" style="border-radius:50%"></i><i class="t"></i>'], ['codetext', 'Code', '<i class="q"></i><i class="t"></i>'], ['imagecode', 'Image + Code', '<i class="q s" style="border-radius:50%"></i><i class="t"></i><i class="q s"></i>']]
                    .map(([id, name, sch]) => `<button class="lay${lay === id ? ' on' : ''}" type="button" data-lay="${id}"><span class="sch">${sch}</span>${name}</button>`).join('')}
            </div></div>
            ${hasCode ? `<div class="block"><div class="block-h"><i class="fa-solid fa-qrcode"></i>Code<select id="edBarcode" aria-label="Kind of code">${S.barcodes.map(b => `<option${b === d.barcode_type ? ' selected' : ''}>${esc(b)}</option>`).join('')}</select></div>
                <div class="block-b">${codeField('edCode', d.code_text, ' placeholder="Link or text, empty for the text of the label" aria-label="Content of the code"')}
                ${lay === 'imagecode' ? '<span class="muted small">Fitted to the label when the image is, see Size under Image.</span>'
                    : `<div><span class="label-s">Size</span><div class="seg">${fitButton(d)}<button type="button" data-fit="0" class="${fitted(d) ? '' : 'on'}">Fixed</button></div></div>`}
                ${((qr, fixed) => `<div class="pair">
                    ${qr ? `<label${fixed ? '' : ' title="The code fits the label"'}><span class="label-s${fixed ? '' : ' off'}">Size of a dot</span><input class="field" type="number" min="1" max="40" id="edQrSize" value="${esc(d.qrcode_size)}"${fixed ? '' : ' disabled'}></label>`
                        : lay === 'imagecode' ? '' : `<label${fixed ? '' : ' title="The code fits the label"'}><span class="label-s${fixed ? '' : ' off'}">Scale in %</span><input class="field" type="number" min="1" max="500" id="edCodeScale" value="${esc(d.image_scaling_factor)}"${fixed ? '' : ' disabled'}></label>`}
                    <label${qr ? '' : ' title="Only for QR codes"'}><span class="label-s${qr ? '' : ' off'}">Error correction</span><select class="field" id="edQrCorr"${qr ? '' : ' disabled'}>${[['L', 'Low, 7 %'], ['M', 'Medium, 15 %'], ['Q', 'Quartile, 25 %'], ['H', 'High, 30 %']].map(([v, n]) => `<option value="${v}"${d.qrcode_correction === v ? ' selected' : ''}>${n}</option>`).join('')}</select></label>
                </div>${qr ? '' : '<span class="muted small">Error correction exists for QR codes only.</span>'}`)(d.barcode_type === 'QR', !fitted(d))}
                ${isColorTape(d.label_size) ? `<div><span class="label-s">Color</span>${colorField('edCodeColor', d.print_color, 'Color of the code')}</div>` : ''}
                ${lay === 'codetext' ? sideField(d, 'code') + gapField(d) : ''}</div></div>` : ''}
            ${lay === 'image' || lay === 'imagecode' ? imageBlock(d) : ''}
            <div class="block"><div class="block-h"><i class="fa-solid fa-font"></i>Text<select id="edFamily" aria-label="Font of all lines" title="Font of all lines">${fam ? missingFont(fam) : '<option value="" selected>Several fonts</option>'}${families.map(f => `<option${f === fam ? ' selected' : ''}>${esc(f)}</option>`).join('')}</select></div>
                <div id="edLines">${d.text.map((l, i) => lineRow(l, i, mainFamily(d), families, redTape(d), isColorTape(d.label_size))).join('')}</div>
                <div class="addl">
                    <button class="pill" type="button" id="edAdd"><i class="fa-solid fa-plus"></i>Line</button>
                    <button class="pill" type="button" id="edBlank"><i class="fa-regular fa-square"></i>Blank</button>
                    <button class="pill" type="button" data-tok="[today]"><i class="fa-regular fa-calendar"></i>Date</button>
                    <button class="pill" type="button" data-tok="[counter]"><i class="fa-solid fa-arrow-up-1-9"></i>Counter</button>
                    <button class="pill" type="button" id="edSym"><i class="fa-solid fa-icons"></i>Symbol</button>
                    <button class="pill${S.tplInfo ? ' on' : ''}" type="button" id="tplInfoBtn" aria-expanded="${!!S.tplInfo}" aria-controls="tplInfo"><i class="fa-solid fa-circle-info"></i>Variables</button>
                </div>
                <p class="ed-note">Write <b>[Name]</b> for a blank that is filled in when printing. Variables explains all of them.</p>
                <div class="info-box" id="tplInfo" style="margin:0 .8rem .7rem"${S.tplInfo ? '' : ' hidden'}>${varsHelpHTML()}</div></div>
            <div class="block"><div class="block-h"><i class="fa-solid fa-ruler-combined"></i>Label</div>
                <div class="block-b">
                    <div class="tape-row"><label class="label-s" for="edSize">Tape</label>${lengthLabel(d)}${tapeNote(d)}<select class="field" id="edSize">${sizeOptions(d.label_size, s => esc(tapeName(s.id)))}</select>${lengthInput(d)}</div>
                    ${flagRow(d)}
                    <div><span class="label-s">Text runs</span><div class="seg"><button type="button" data-along="0" class="${runsAlong(d) ? '' : 'on'}">Across the tape</button><button type="button" data-along="1" class="${runsAlong(d) ? 'on' : ''}">Along</button></div></div>
                    <div><span class="label-s">Text size</span><div class="seg">${TEXT_FITS.map(([v, n]) => `<button type="button" data-tfit="${v}" class="${textFit(d) === v ? 'on' : ''}"${v === 'fill' ? ' title="As large as fits on the label, the lines keep the ratio of their sizes"' : ''}>${n}</button>`).join('')}</div></div>
                    ${(room => `<div title="${room ? 'Where the content is on the label, as the text reads' : NO_ROOM}"><span class="label-s${room ? '' : ' off'}">Vertical position</span><div class="seg">${VERTICAL_ALIGNS.map(([v, n]) => `<button type="button" data-valign="${v}" class="${verticalAlign(d) === v ? 'on' : ''}"${room ? '' : ' disabled'}>${n}</button>`).join('')}</div></div>`)(hasRoom(d))}
                    <div class="group">
                        <div><span class="label-s">Frame</span><div class="seg">${[['none', 'None'], ['square', 'Square'], ['rounded', 'Rounded']].map(([v, n]) => `<button type="button" data-frame="${v}" class="${frameOf(d) === v ? 'on' : ''}">${n}</button>`).join('')}</div></div>
                        ${frameOf(d) === 'none' ? '' : frameDetails(d)}
                    </div>
                    ${cutField(d)}
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
            <div class="file-step" id="edFileStep" hidden>
                <button class="btn quiet icon" type="button" data-file="-1" title="Previous file" aria-label="Previous file"><i class="fa-solid fa-chevron-left"></i></button>
                <span id="edFileOf" aria-live="polite"></span>
                <button class="btn quiet icon" type="button" data-file="1" title="Next file" aria-label="Next file"><i class="fa-solid fa-chevron-right"></i></button>
            </div>
            ${stageHTML('edStage', true)}
        </section>
    </div>`;
    bindEditor();
    redrawEditor();
    if (place) { restoreEditorPlace(place); return; }
    const first = $('[data-row="0"] .tx');
    if (first && !first.value && window.matchMedia('(min-width: 761px)').matches) first.focus();
}

// The label sizes the printer can use, grouped by their form. The chosen
// one is always offered, e.g. that of a label made for another printer.
function sizeOptions(chosen, name) {
    const offered = s => s.id === chosen || printerTakes(s);
    const groups = [['Endless', s => s.form === 'endless' && !isPtouch(s.id) && !isColorTape(s.id)], ['Die-cut', s => s.form === 'die_cut'],
        ['Round', s => s.form === 'round_die_cut'], ['P-touch tape', s => isPtouch(s.id)], ['Color tape', s => isColorTape(s.id)]];
    return groups.map(([group, inGroup]) => {
        const sizes = C.label_sizes.filter(s => inGroup(s) && offered(s));
        return sizes.length ? `<optgroup label="${group}">${sizes.map(s => `<option value="${esc(s.id)}"${s.id === chosen ? ' selected' : ''}>${name(s)}</option>`).join('')}</optgroup>` : '';
    }).join('');
}

// A cable flag prints the label twice, with the wrap around the cable between
function flagRow(d) {
    const on = flagOn(d), locked = isDieCut(d.label_size) && !on;
    return `<div class="row" id="edFlagRow"${locked ? ' title="Cable flags need continuous tape"' : ''}>
            <label class="check"><input type="checkbox" id="edFlag"${on ? ' checked' : ''}${locked ? ' disabled' : ''}><span${locked ? ' class="off"' : ''}>Cable flag</span></label>
            ${on ? `<input class="field rot" type="number" min="1" max="50" step="0.5" id="edCable" value="${esc(d.cable_diameter)}" placeholder="6.5" aria-label="Diameter of the cable in mm"><span>mm cable</span>` : ''}
        </div>${on ? '<span class="muted small" id="edFlagHint">Place the cable on the triangles, fold both ends together and press them firmly.</span>' : ''}`;
}

// A tape as the tape list names it
const tapeName = id => sizeName(id) + (id === S.tape && !S.tapeAssumed ? ' (in the printer)' : '');

function tapeNote(d) {
    const who = esc(thePrinter(S.printer, true));
    if (S.tapeUnknown) return `<span class="tape-note warn">${who} reports ${esc(S.tapeUnknown)} tape, which is not offered here.</span>`;
    // An assumed tape matches nothing in the printer
    if (d.label_size === S.tape) return S.tapeAssumed ? '' : `<span class="tape-note ok"><i class="fa-solid fa-check"></i> Matches the tape in the printer${tapeLeft(S.printer) ? ', ' + esc(tapeLeft(S.printer)) : ''}</span>`;
    if (fitsTape(d.label_size)) return '';
    const have = S.tapeAssumed ? `does not report its tape, ${esc(sizeName(S.tape))} is assumed` : `has ${esc(sizeName(S.tape))} loaded`;
    return `<span class="tape-note warn">${who} ${have}. <button type="button" class="link" id="edUseTape">Switch to it</button></span>`;
}

// The length of an endless label as cut, next to the tape, where the server
// offers one for the tape. Die-cut labels have their own, it shows as the hint.
const LENGTH_TITLE = 'The label as cut, with the margin the printer leaves before and after the print. Empty: as long as its content.';
const hasLength = d => !!SIZES[d.label_size] && !!SIZES[d.label_size].length && !flagOn(d);
// An endless label of a length typed is laid out as a die-cut one
const hasFixedLength = d => hasLength(d) && +d.label_length > 0;
const lengthWhy = d => hasLength(d) ? LENGTH_TITLE : isDieCut(d.label_size) ? 'Die-cut labels have their own length'
    : flagOn(d) ? 'Cable flags are as long as their content' : 'Labels on this tape are as long as their content';
const lengthLabel = d => `<label class="label-s${hasLength(d) ? '' : ' off'}" for="edLength" title="${lengthWhy(d)}">Length, mm</label>`;
function lengthInput(d) {
    const on = hasLength(d), s = SIZES[d.label_size];
    return `<input class="field" type="number"${on ? ` min="${s.length[0]}" max="${s.length[1]}"` : ''} step="0.5" id="edLength" value="${on ? esc(d.label_length) : ''}" placeholder="${on || !isDieCut(d.label_size) ? 'Automatic' : esc(s.tape[1])}"
        title="${lengthWhy(d)}"${on ? '' : ' disabled'}>`;
}

function imageBlock(d) {
    const red = redTape(d), color = isColorTape(d.label_size);
    const modes = [['black_and_white', 'Black and white'], ['grayscale', 'Grayscale'], ['red_and_black', 'Red and black'], ['colored', 'Full color']];
    const needsRed = v => (v === 'red_and_black' || v === 'colored') && !red && d.image_mode !== v;
    const bw = d.image_mode === 'black_and_white';
    const fit = fitted(d), crop = d.image_crop === '1';
    const pdf = d.image && /pdf/i.test(d.image.mime);
    return `<div class="block"><div class="block-h"><i class="fa-regular fa-image"></i>Image</div>
        <div class="block-b">
            <label class="drop" for="edFile"><i class="fa-solid fa-upload"></i> ${d.image ? esc(d.image.name) + (d.more_images ? ` and ${d.more_images.length} more` : '') + ', choose others' : 'Choose images or PDFs, drop or paste them'}</label><input type="file" id="edFile" accept="image/*,application/pdf" multiple hidden>
            ${d.more_images ? `<span class="muted small">${d.more_images.length + 1} files, each prints as a label of its own with these settings. Step through them above the preview, saving the label keeps only the first.</span>` : ''}
            <label title="${pdf ? '' : 'Only for PDF files'}"><span class="label-s${pdf ? '' : ' off'}">PDF pages</span><input class="field" id="edPages" placeholder="${pdf ? '1' : 'Only for PDF files'}" value="${esc(d.pdf_pages || '')}"${pdf ? '' : ' disabled'}>${pdf ? '<span class="muted small" id="edPagesHelp">For example 1-3, 5. Each page prints as a label of its own.</span>' : ''}</label>
            ${color ? `<div><span class="label-s">Colors</span><span class="muted small">Images print in full color on this tape.</span></div>
            <div class="row"><label class="check"><input type="checkbox" id="edContrast"${d.image_contrast === '1' ? ' checked' : ''}><span>Stronger contrast, for light drawings</span></label></div>
            <span class="muted small" id="edContrastHint" hidden>This image looks light and may print faint. <button type="button" class="link" id="edContrastOn">Use stronger contrast</button></span>` : `<div><span class="label-s">Colors</span><div class="seg wrap">${modes.map(([v, n]) => `<button type="button" data-imode="${v}" class="${d.image_mode === v ? 'on' : ''}"${needsRed(v) ? ' disabled title="Needs black and red tape"' : ''}>${n}</button>`).join('')}</div></div>`}
            ${color ? '' : `<label title="${bw ? '' : 'Only for black and white'}"><span class="label-s${bw ? '' : ' off'}">Pixels darker than <b id="edThrV">${esc(d.image_bw_threshold)}</b> of 255 print black${bw ? '' : ', only for black and white'}</span><input type="range" id="edThr" min="1" max="254" value="${esc(d.image_bw_threshold)}"${bw ? '' : ' disabled'}></label>`}
            <div class="row" style="flex-wrap:wrap;align-items:flex-end">
                <div><span class="label-s">Size</span><div class="seg">${fitButton(d)}<button type="button" data-fit="0" class="${fit ? '' : 'on'}">Scale</button></div></div>
                <label title="${fit ? 'Only without fitting to the label' : ''}"><span class="label-s${fit ? ' off' : ''}">Scale in %</span><input class="field" type="number" min="1" max="500" id="edScale" value="${esc(d.image_scaling_factor)}" style="width:6rem"${fit ? ' disabled' : ''}></label>
            </div>
            <div class="row" style="flex-wrap:wrap">
                <div><span class="label-s">Rotation in degrees</span><div class="row" style="gap:.35rem"><div class="seg">${[0, 90, 180, 270].map(r => `<button type="button" data-rot="${r}" class="${+d.image_rotation === r ? 'on' : ''}">${r}</button>`).join('')}</div>
                    <input class="field rot" type="number" min="0" max="359" id="edRot" value="${esc(d.image_rotation)}" aria-label="Any angle in degrees" title="Any angle in degrees"></div></div>
                <div><span class="label-s">Image processing</span><div class="seg"><button type="button" data-crop="0" class="${crop ? '' : 'on'}">None</button><button type="button" data-crop="1" class="${crop ? 'on' : ''}" title="Removes the empty border around the image">Auto crop</button></div></div>
            </div>
            ${sideField(d, 'image')}${gapField(d)}
        </div></div>`;
}

// Distance between the code or image and the text. Left empty, the server
// chooses it from the size of the text, the field then shows its choice.
const hasTextLines = d => d.text.some(l => String(l.text || '').trim());
// Whether the code or image has something next to it: text, or the code of
// an image label with a code as well
const paired = d => hasTextLines(d) || d.print_type === 'image_qrcode_text';
const GAP_TITLE = 'Leave it empty for a distance that suits the size of the text';
// Buttons for the cutting c, with data-<attr> naming the setting. Half cuts
// and chain printing only for P-touch printers.
function cutSettings(attr, c, k, withCut = true) {
    // Settings that do not apply are disabled, showing what is printed
    const seg = (k, name, opts, why = '', on = c[k]) => `<div${why ? ` title="${esc(why)}"` : ''}><span class="label-s${why ? ' off' : ''}">${name}</span>
        <div class="seg wrap">${opts.map(([v, n, off]) => `<button type="button" data-${attr}="${k}" data-v="${v}" class="${on === v ? 'on' : ''}"${off ? ` title="${esc(off)}"` : ''}${why || off ? ' disabled' : ''}>${n}</button>`).join('')}</div></div>`;
    const onOff = [['1', 'On'], ['0', 'Off']];
    const halfWhy = k.half_cut ? '' : 'This printer has no half cutter';
    const chainWhy = !k.chain ? 'Only for P-touch printers' : c.cut === 'none' ? 'Always on when not cutting, else the printer cuts after the last label' : '';
    const cutWhy = noCutter(k) ? noCutterWhy(k) : '';
    return (withCut ? seg('cut', 'Cutting', CUTS.map(([v, n]) => [v, n, v === 'none' ? neverWhy(k) : '']), cutWhy, cutWhy ? '' : c.cut) : '')
        + seg('half_cut', 'Half cut between labels', onOff, halfWhy, halfOf(c, k) ? '1' : halfWhy ? '0' : c.half_cut)
        + seg('chain', 'Chain printing, the last label stays in the printer', onOff, chainWhy, chainOf(c, k) ? '1' : chainWhy ? '0' : c.chain);
}

// A label can keep its own way of cutting, else it is cut as set for the printer
function cutField(d) {
    const pc = CUTS.find(x => x[0] === printerCut().cut)[1].toLowerCase();
    return `<div class="cutting"><label class="label-s" for="edCut">Cutting</label>
        <select class="field" id="edCut"${noCutter(sizeCaps(d.label_size)) ? ` disabled title="${noCutterWhy(sizeCaps(d.label_size))}"` : ''}><option value="">Like the printer: ${esc(pc)}</option>
        ${CUTS.map(([v, n]) => `<option value="${v}"${d.cut === v ? ' selected' : ''}${v === 'none' && neverWhy(sizeCaps(d.label_size)) ? ' disabled' : ''}>${n}, saved with the label</option>`).join('')}</select>
        ${d.cut ? cutSettings('lcut', cutOf(d), sizeCaps(d.label_size), false) : ''}</div>`;
}

function gapField(d) {
    const on = paired(d);
    return `<label title="${on ? GAP_TITLE : 'Only with text on the label'}"><span class="label-s${on ? '' : ' off'}">Distance to the text, mm</span>
        <input class="field" type="number" min="0" max="50" step="0.5" id="edGap" value="${d.image_text_gap === '' ? '' : dotsToMm(d.image_text_gap, dpiOf(d.label_size))}" placeholder="${on ? 'Automatic' : 'Only with text'}" style="width:10rem"${on ? '' : ' disabled'}></label>`;
}

function resetLook(d) {
    d.image_rotation = '0';
    d.image_crop = '0';
    d.image_scaling_factor = '100';
}

const fitted = d => d.image_fit === '1';
const fitButton = d => `<button type="button" data-fit="1" class="${fitted(d) ? 'on' : ''}">Fit to the label</button>`;

// Where the code or image is next to the text: as chosen, else where the
// server put it in the last preview
const SIDES = [['above', 'Above'], ['below', 'Below'], ['left', 'Left'], ['right', 'Right']];
const imageSide = d => d.image_position || (S.edit && S.edit.placedSide) || '';
function sideField(d, what) {
    const on = paired(d);
    const other = d.print_type === 'image_qrcode_text' ? ', the code is on the other side' : '';
    const why = `Where the ${what} is next to the text${other}`;
    return `<div id="edSide" data-why="${why}" title="${on ? why : 'Only with text on the label'}"><span class="label-s${on ? '' : ' off'}">Position</span><div class="seg">${SIDES.map(([v, n]) =>
        `<button type="button" data-side="${v}" class="${imageSide(d) === v ? 'on' : ''}"${on ? '' : ' disabled'}>${n}</button>`).join('')}</div></div>`;
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
        <div style="margin-top:.5rem"><span class="label-s">Color</span>${isColorTape(d.label_size) ? colorField('edFrameColor', d.border_color, 'Color of the frame')
            : `<div class="seg"><button type="button" data-bcolor="black" class="${d.border_color === 'red' ? '' : 'on'}">Black</button><button type="button" data-bcolor="red" class="${d.border_color === 'red' ? 'on' : ''}"${red || d.border_color === 'red' ? '' : ' disabled title="Needs black and red tape"'}>Red</button></div>`}</div>
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
// Colors are kept as black, red or #rrggbb
const hexOf = c => c === 'red' ? '#ff0000' : /^#[0-9a-f]{6}$/i.test(c || '') ? c.toLowerCase() : '#000000';
const colorOf = hex => hex === '#000000' ? 'black' : hex === '#ff0000' ? 'red' : hex;
const colorField = (id, color, title) => `<input type="color" class="color-in" id="${id}" value="${hexOf(color)}" title="${title}" aria-label="${title}">`;
const SPACINGS = [100, 150, 200, 250, 300];

// Two rows per line: the text on top, all settings of the line below it
function lineRow(l, i, main, families, red, color) {
    const al = { left: 'align-left', center: 'align-center', right: 'align-right' }[l.align] || 'align-center';
    const fam = familyOf(l.font);
    const noBold = wantsBold(l) && !hasBold(fam);
    const spacing = +(l.line_spacing || 100);
    return `<div class="lrow${wantsBold(l) && !noBold ? ' bold' : ''}${i === S.editSel ? ' sel' : ''}${l.color === 'red' ? ' red' : ''}" data-row="${i}">
        <span class="txw">${l.checkbox ? '<i class="fa-regular fa-square" title="To-do box"></i>' : ''}<input class="tx"${color && hexOf(l.color) !== '#000000' ? ` style="color:${hexOf(l.color)}"` : ''} value="${esc(toDisplay(l.text))}" placeholder="${i === 0 ? 'Type the label text' : 'Line ' + (i + 1)}" aria-label="Line ${i + 1}">
            <button class="mini quiet" type="button" data-act="del" title="Remove the line"><i class="fa-solid fa-xmark"></i></button></span>
        <div class="st">
            <select class="mini lf${fam !== main ? ' on' : ''}" title="Font of line ${i + 1}" aria-label="Font of line ${i + 1}">${missingFont(fam)}${families.map(f => `<option${f === fam ? ' selected' : ''}>${esc(f)}</option>`).join('')}</select>
            <input class="mini" type="number" min="1" value="${esc(l.size)}" title="Size" aria-label="Size of line ${i + 1}">
            <button class="mini${wantsBold(l) ? ' on' : ''}${noBold ? ' nobold' : ''}" type="button" data-act="bold" title="${noBold ? esc(fam) + ' has no bold style. The line prints regular and becomes bold again with a font that has one.' : 'Bold'}">B</button>
            <button class="mini" type="button" data-act="align" title="Alignment: ${esc(l.align || 'center')}"><i class="fa-solid fa-${al}"></i></button>
            <button class="mini${l.inverted ? ' on' : ''}" type="button" data-act="invert" title="White on black"><i class="fa-solid fa-circle-half-stroke"></i></button>
            <button class="mini${l.checkbox ? ' on' : ''}" type="button" data-act="todo" title="To-do box in front"><i class="fa-regular fa-square-check"></i></button>
            ${color ? `<label class="mini-wrap color-sw" title="Color of line ${i + 1}"><input type="color" class="lcol" value="${hexOf(l.color)}" aria-label="Color of line ${i + 1}"></label>` : `<button class="mini red-sw${l.color === 'red' ? ' on' : ''}${l.color === 'red' && !red ? ' nored' : ''}" type="button" data-act="red" title="${red ? 'Red text' : l.color === 'red' ? 'This tape has no red, the line prints black' : 'Red text needs black and red tape'}"${red || l.color === 'red' ? '' : ' disabled'}><span></span></button>`}
            <label class="mini-wrap${spacing !== 100 ? ' on' : ''}" title="Line spacing"><i class="fa-solid fa-text-height"></i><select class="mini lsp" aria-label="Line spacing of line ${i + 1}">${SPACINGS.concat(SPACINGS.includes(spacing) ? [] : [spacing]).map(v => `<option value="${v}"${v === spacing ? ' selected' : ''}>${v} %</option>`).join('')}</select></label>
        </div></div>`;
}

// The steps of the undo history refer to their images, each is kept once
const histImages = new Map();
// How many steps refer to each image. One no step refers to any more is let
// go, the steps are looked at only when they come and go.
const histRefs = new Map();
function countRefs(snap, by) {
    for (const m of snap.matchAll(/"ref":"([^"]+)"/g)) {
        const n = (histRefs.get(m[1]) || 0) + by;
        if (n > 0) histRefs.set(m[1], n);
        else { histRefs.delete(m[1]); histImages.delete(m[1]); }
    }
}
// images: where to put the images of the step, by their keys
function snapshot(E, images = null) {
    const keep = im => { const k = imageKey(im); if (images) images.set(k, im); return { ref: k }; };
    const d = Object.assign({}, E.design);
    if (d.image) d.image = keep(d.image);
    if (d.more_images) d.more_images = d.more_images.map(keep);
    return JSON.stringify({ t: E.title, d });
}
function fromSnapshot(snap) {
    const o = JSON.parse(snap);
    const get = r => (r && r.ref ? histImages.get(r.ref) : r);
    if (o.d.image) o.d.image = get(o.d.image);
    if (o.d.more_images) o.d.more_images = o.d.more_images.map(get);
    return o;
}
function recordHistory(E) {
    const images = new Map();
    const snap = snapshot(E, images);
    if (snap !== S.hist.last) {
        if (S.hist.last !== null) {
            S.hist.undo.push(S.hist.last);
            if (S.hist.undo.length > 60) countRefs(S.hist.undo.shift(), -1);
            S.hist.redo.forEach(st => countRefs(st, -1));
            S.hist.redo = [];
        }
        for (const [k, im] of images) histImages.set(k, im);
        countRefs(snap, 1);
        S.hist.last = snap;
    }
    if ($('#edUndo')) {
        $('#edUndo').disabled = !S.hist.undo.length;
        $('#edRedo').disabled = !S.hist.redo.length;
        $('#edUndoN').textContent = S.hist.undo.length;
        $('#edUndoN').hidden = !S.hist.undo.length;
    }
}
function stepHistory(dir) {
    // A change not yet kept is a step of its own
    if (S.edit) recordHistory(S.edit);
    const from = dir < 0 ? S.hist.undo : S.hist.redo, to = dir < 0 ? S.hist.redo : S.hist.undo;
    if (!from.length || !S.edit) return;
    to.push(S.hist.last);
    S.hist.last = from.pop();
    const o = fromSnapshot(S.hist.last);
    S.edit.title = o.t;
    S.edit.design = o.d;
    // Its rotation, cropping and scale belong to the layout brought back
    delete S.edit.lookOf;
    renderEditor();
}

// Each pause in typing is a step to undo, and the label is kept for the next
// visit. The first state right away, it is where undo leads back to.
const keepEditor = debounce(() => { if (S.edit) { recordHistory(S.edit); storeEdit(); } }, 250);
// The server refused the last preview of the editor: Print waits for one it
// does not refuse. What it cannot draw it cannot print either, nor an empty
// label. One that took too long may still print.
let editorFailed = false;
const printable = empty => { $('#edPrint').disabled = empty || editorFailed; };
// The fields and buttons follow each change at once, the preview after the
// one on its way
function drawEditor() {
    const E = S.edit;
    if (!E || !$('#edStage')) return;
    if (S.hist.last === null) recordHistory(E);
    keepEditor();
    // A change not yet kept can be undone as well
    if ($('#edUndo') && !S.hist.undo.length) $('#edUndo').disabled = snapshot(E) === S.hist.last;
    // A label with blanks shows fields to try values
    const blanks = blanksOf(E.design);
    E.values = E.values || {};
    const box = $('#edFields');
    const item = editItem(E);
    // Also a new day for a date at today
    const sig = JSON.stringify([blanks, blanks.map(b => [blankFields(item)[b] || null, startOf(item, b)]), !!S.blankSettings]);
    if (box.dataset.sig !== sig) {
        box.dataset.sig = sig;
        const vals = defaults(item);
        // Typed values stay, unless the kind of the blank changed or its new
        // default replaces the old one that was still in the field. Kept
        // with the label, the fields are made anew with the editor.
        const oldTypes = E.blankTypes || {}, oldDefs = E.blankDefs || {};
        for (const b of blanks) {
            const v = E.values[b], def = startOf(item, b);
            if (v === undefined || (oldTypes[b] && fieldType(item, b) !== oldTypes[b])) continue;
            if ((v === '' || v === (oldDefs[b] || '')) && def !== (oldDefs[b] || '')) continue;
            vals[b] = v;
        }
        E.blankTypes = Object.fromEntries(blanks.map(b => [b, fieldType(item, b)]));
        E.blankDefs = Object.fromEntries(blanks.map(b => [b, startOf(item, b)]));
        E.values = vals;
        const head = `<h3>Fill in the blanks <span class="badge warn open-count" hidden></span>
            <button class="btn sm quiet ml-auto${S.blankSettings ? ' on' : ''}" type="button" id="blankSet" aria-pressed="${!!S.blankSettings}"><i class="fa-solid fa-sliders"></i>Blank settings</button></h3>`;
        box.innerHTML = !blanks.length ? '' : S.blankSettings ? head + `<div class="bset">${blanks.map(b => {
            const k = fieldType(item, b);
            return `<div class="bset-row" data-bname="${esc(b)}"><b>[${esc(b)}]</b>
                <div class="seg">${BLANK_KINDS.map(([v, n]) => `<button type="button" data-kind="${v}" class="${k === v ? 'on' : ''}">${n}</button>`).join('')}</div>
                ${k !== 'date' ? `<input class="field" data-default="1" type="${k === 'number' ? 'number' : 'text'}" placeholder="Default, optional" value="${esc(blankStart(item, b))}">`
                    : blankStart(item, b) ? `<span class="muted small">Starts at ${esc(blankStart(item, b))}</span><button type="button" class="btn sm quiet" data-today="1">Start at today</button>`
                    : '<span class="muted small">Starts at today</span>'}</div>`;
        }).join('')}</div><p class="muted small" style="margin:0">Stored with the label. Rename a blank in its line, between the brackets.</p>`
            : head + `<div class="fill-grid">${blanks.map((n, i) => fieldHTML(item, n, vals[n], 100 + i)).join('')}</div>`;
        box.hidden = !blanks.length;
        if ($('#blankSet', box)) $('#blankSet', box).onclick = () => { S.blankSettings = !S.blankSettings; redrawEditor(); };
        const setField = (name, patch) => {
            E.design.fields = Object.assign({}, E.design.fields);
            E.design.fields[name] = Object.assign({}, blankFields(item)[name] || { type: guessKind(name) }, patch);
            if (E.design.fields[name].default === '') delete E.design.fields[name].default;
            // They replace a value saved with the label through the API
            if (E.design.blanks && name in E.design.blanks) {
                E.design.blanks = Object.assign({}, E.design.blanks);
                delete E.design.blanks[name];
                if (!Object.keys(E.design.blanks).length) delete E.design.blanks;
            }
            redrawEditor();
        };
        $$('.bset-row', box).forEach(r => {
            $$('[data-kind]', r).forEach(b => b.onclick = () => setField(r.dataset.bname, { type: b.dataset.kind, default: '' }));
            const dv = $('[data-default]', r);
            if (dv) dv.addEventListener('change', () => setField(r.dataset.bname, { default: dv.value.trim() }));
            const now = $('[data-today]', r);
            if (now) now.onclick = () => setField(r.dataset.bname, { default: '' });
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
    // An empty label is shown blank, as large as one line of text in the
    // first line's size makes it, and cannot be printed or saved yet
    const empty = isEmptyDesign(E.design);
    printable(empty);
    $('#edSave').disabled = empty;
    // Typing the first line of text, or deleting the last, changes whether
    // the position and the distance to the text apply
    const on = paired(E.design), gap = $('#edGap'), side = $('#edSide');
    if (gap) {
        gap.disabled = !on;
        gap.previousElementSibling.classList.toggle('off', !on);
        gap.parentElement.title = on ? GAP_TITLE : 'Only with text on the label';
        if (!on) gap.placeholder = 'Only with text';
    }
    if (side) {
        $$('[data-side]', side).forEach(b => b.disabled = !on);
        $('.label-s', side).classList.toggle('off', !on);
        side.title = on ? side.dataset.why : 'Only with text on the label';
    }
    const files = labelFiles(E.design);
    $('#edFileStep').hidden = files.length < 2;
    if (files.length > 1) $('#edFileOf').textContent = `File ${fileShown(E) + 1} of ${files.length}: ${files[fileShown(E)].name}`;
    drawStage();
}
// A stage drawn anew, e.g. for another layout, does not wait for the preview
// of the one before
const drawStage = latest(async () => {
    const E = S.edit, stage = $('#edStage');
    if (!E || !stage) return;
    const empty = isEmptyDesign(E.design);
    const file = fileShown(E);
    const shown = empty ? Object.assign(clone(E.design), { text: E.design.text.slice(0, 1).map(l => Object.assign({}, l, { text: 'Ag' })) })
        : file ? Object.assign({}, E.design, { image: labelFiles(E.design)[file] }) : E.design;
    const gap = $('#edGap');
    await showOnStage(stage, shown, E.values, (w, err, p, refused) => {
        // Where the server put the code or image, its setting shows that
        if (p && !empty && p.side !== (E.placedSide || '')) {
            E.placedSide = p.side;
            const side = imageSide(E.design);
            $$('[data-side]').forEach(b => b.classList.toggle('on', b.dataset.side === side));
        }
        if (p && p.auto && !empty) setTimeout(() => $('#edFits') && $('#edFits').insertAdjacentHTML('beforeend', ' <span class="muted">Turned automatically to fit the label best.</span>'));
        if (gap && p && paired(E.design)) gap.placeholder = p.gap ? `Automatic, ${dotsToMm(p.gap, dpiOf(E.design.label_size, p.highRes))}` : 'Automatic';
        // The server finds the image light, stronger contrast would help it
        if ($('#edContrastHint')) $('#edContrastHint').hidden = !(p && p.faint && !empty);
        if (empty) { const n = $('#edFits'); n.className = 'fits'; n.innerHTML = ''; }
        else fitsNote($('#edFits'), w, err, E.design, () => renderEditor());
        editorFailed = !!refused;
        printable(empty);
    }, empty);
}, () => $('#edStage'));
const redrawEditor = busyThen(() => $('#edStage'), drawEditor);

function bindEditor() {
    const E = S.edit, d = E.design;
    $('#edTitle').oninput = e => { E.title = e.target.value; storeEdit(); };
    $('#edStage').onclick = e => {
        if (!e.target.closest('.zoom')) return;
        S.settings.largePreview = !S.settings.largePreview;
        store(own('designer_settings'), S.settings);
        placeOnStage(e.currentTarget, true);
        $('#edStage .zoom').focus();
    };
    // From the last file on to the first, the buttons keep the focus
    const stepFile = by => {
        const n = labelFiles(E.design).length;
        E.fileShown = (fileShown(E) + by + n) % n;
        redrawEditor();
    };
    $$('#edFileStep [data-file]').forEach(b => b.onclick = () => stepFile(+b.dataset.file));
    $('#edFileStep').onkeydown = e => {
        if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
        e.preventDefault();
        stepFile(e.key === 'ArrowLeft' ? -1 : 1);
    };
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
        // Rotation, cropping and scale belong to the image or the code they
        // were set for, also after the text layout between: they would act on
        // the other one, a code does not show them
        const kindOf = t => t === 'qrcode_text' ? 'code' : t === 'image' || t === 'image_qrcode_text' ? 'image' : null;
        if (E.lookOf === undefined) E.lookOf = kindOf(d.print_type);
        const next = { text: 'text', codetext: 'qrcode_text', image: 'image', imagecode: 'image_qrcode_text' }[lay];
        const kind = kindOf(next);
        if (kind && kind !== E.lookOf && (E.lookOf || kind === 'code')) resetLook(d);
        if (kind) E.lookOf = kind;
        d.print_type = next;
        // Next to text, a code without content of its own holds the text
        if ((lay === 'codetext' || lay === 'imagecode') && !d.code_text && !hasTextLines(d)) d.code_text = 'https://example.com';
        if (lay !== 'text') { setWide(d, true); d.text.forEach(l => { if (l.align === 'center') l.align = 'left'; }); }
        if ((lay === 'codetext' || lay === 'imagecode') && d.qrcode_size === '10') d.qrcode_size = '14';
        if (!d.text.length) d.text.push(L('Text', 80, true, 'left'));
        renderEditor();
    });
    if ($('#edCode')) $('#edCode').oninput = e => { e.target.rows = codeRows(e.target.value); d.code_text = fromDisplay(e.target.value, d.code_text, e.target.selectionStart); redrawEditor(); };
    if ($('#edBarcode')) $('#edBarcode').onchange = e => {
        d.barcode_type = e.target.value;
        // A QR code has the size of a dot instead of a scale
        if (d.barcode_type === 'QR' && d.print_type !== 'image_qrcode_text') d.image_scaling_factor = '100';
        renderEditor();
    };
    if ($('#edCodeScale')) $('#edCodeScale').addEventListener('input', e => { const v = Math.round(+e.target.value); if (v > 0) { d.image_scaling_factor = String(v); redrawEditor(); } });
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
        tx.addEventListener('input', () => { l.text = fromDisplay(tx.value, l.text, tx.selectionStart); redrawEditor(); });
        tx.addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) { e.preventDefault(); $('#edAdd').click(); }
            if (e.key === 'Backspace' && !tx.value && d.text.length > 1) { e.preventDefault(); d.text.splice(i, 1); S.editSel = Math.max(0, i - 1); renderEditor(); const p = $(`[data-row="${S.editSel}"] .tx`); if (p) p.focus(); }
        });
        $('input.mini', row).addEventListener('input', e => { const v = Math.round(+e.target.value); if (v > 0) { l.size = String(v); redrawEditor(); } });
        $('select.lf', row).onchange = e => { l.bold = wantsBold(l); l.font = fontOf(e.target.value, l.bold); S.editSel = i; renderEditor(); };
        $('select.lsp', row).onchange = e => { l.line_spacing = e.target.value; S.editSel = i; renderEditor(); };
        if ($('input.lcol', row)) $('input.lcol', row).onchange = e => { l.color = colorOf(e.target.value); S.editSel = i; renderEditor(); };
        $$('button[data-act]', row).forEach(b => b.onclick = () => {
            const act = b.dataset.act;
            if (act === 'todo') l.checkbox = !l.checkbox;
            if (act === 'red') l.color = l.color === 'red' ? 'black' : 'red';
            if (act === 'bold') { l.bold = !wantsBold(l); if (FONTS[familyOf(l.font)]) l.font = fontOf(familyOf(l.font), l.bold); }
            if (act === 'align') l.align = { left: 'center', center: 'right', right: 'left' }[l.align] || 'left';
            if (act === 'invert') l.inverted = !l.inverted;
            if (act === 'del') d.text.splice(i, 1);
            S.editSel = i;
            renderEditor();
        });
    });
    if ($('#edUseTape')) $('#edUseTape').onclick = () => { changeSize(d, S.tape); renderEditor(); };
    $('#edSize').onchange = e => { changeSize(d, e.target.value); renderEditor(); };
    $('#edFlag').onchange = e => { d.cable_flag = e.target.checked ? '1' : '0'; renderEditor(); };
    if ($('#edContrast')) $('#edContrast').onchange = e => { d.image_contrast = e.target.checked ? '1' : '0'; renderEditor(); };
    if ($('#edContrastOn')) $('#edContrastOn').onclick = () => { d.image_contrast = '1'; renderEditor(); };
    // A diameter the server refuses shows why, one being typed waits
    if ($('#edCable')) $('#edCable').addEventListener('input', e => {
        if (e.target.validity.badInput) return;
        d.cable_diameter = e.target.value.trim();
        redrawEditor();
    });
    // Taken once typed, a length on the way to another may be too short
    $('#edLength').addEventListener('change', e => {
        const v = e.target.value.trim();
        // Drawn anew the field is replaced, which tells the length again
        if (v === d.label_length || v !== '' && !(+v > 0)) return;
        d.label_length = v;
        // Of a length the content has room to be placed in
        renderEditor();
    });
    $$('[data-along]').forEach(b => b.onclick = () => { setRunsAlong(d, b.dataset.along === '1'); renderEditor(); });
    $$('[data-frame]').forEach(b => b.onclick = () => { setFrame(d, b.dataset.frame); renderEditor(); });
    $$('[data-tfit]').forEach(b => b.onclick = () => { setTextFit(d, b.dataset.tfit); renderEditor(); });
    $('#edCut').onchange = e => {
        const c = cutOf(d);
        Object.assign(d, e.target.value ? { cut: e.target.value, half_cut: c.half_cut, chain: c.chain } : { cut: '', half_cut: '', chain: '' });
        renderEditor();
    };
    $$('[data-lcut]').forEach(b => b.onclick = () => { d[b.dataset.lcut] = b.dataset.v; renderEditor(); });
    $$('[data-valign]').forEach(b => b.onclick = () => { d.vertical_align = b.dataset.valign; renderEditor(); });
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
    $$('[data-side]').forEach(b => b.onclick = () => { d.image_position = b.dataset.side; renderEditor(); });
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
    if ($('#edFrameColor')) $('#edFrameColor').onchange = e => { d.border_color = colorOf(e.target.value); renderEditor(); };
    if ($('#edCodeColor')) $('#edCodeColor').onchange = e => { d.print_color = colorOf(e.target.value); renderEditor(); };
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
    $('#edPrint').onclick = () => printJob({ title: E.title, design: d, values: valuesNow(E.values, E.blankDefs, editItem(E)), copies: copiesIn(document, '#edCopies'), source: E.origin });
    $('#edSave').onclick = () => {
        const over = E.origin && E.origin.kind === 'saved' ? E.origin.id : null;
        const save = async (title, replacing) => {
            const name = await saveLabel(title, d, over || replacing);
            E.title = title;
            E.origin = { kind: 'saved', id: name };
            if (S.edit === E) storeEdit();
            // The editor may show something else by now
            if (S.editShown === E && $('#edTitle')) {
                $('#edTitle').value = title;
                $('#edSave').innerHTML = '<i class="fa-regular fa-bookmark"></i><span class="lbl">Save</span>';
                $('#edSave').title = 'Save';
            }
            toast(over ? `Saved "${title}"` : `"${title}" is now one of your saved labels`, { label: blanksOf(E.design).length ? 'Fill in and print' : 'Open in the wizard', run: () => { location.hash = 'fill/' + encodeURIComponent(name); } });
        };
        // A saved label is saved again, a new one is given its name first
        if (over) save(E.title, null).catch(e => toast(e.message));
        else saveDialog(E.title, save);
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
        if (!withImage(d)) d.print_type = 'image';
        S.edit.fileShown = 0;
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
    // Opened by a link, closed or replaced by another layer, it goes back to
    // the labels. A change of the tape shows once that layer is closed.
    const gone = () => {
        if (parseHash().view === 'printer') history.replaceState(null, '', '#wizard');
        if (tapeChanged) S.routeLater = true;
    };
    if (matchMedia('(max-width: 760px)').matches) sheet(inner, done, gone);
    else openLayer(`<div class="pop">${inner}</div>`, 'pop-layer', done, gone);
    bindPrinterPanel();
}

function printerPanelHTML() {
    const list = S.printers.map(p => {
        const st = printerState(p);
        const on = S.printer && p.path === S.printer.path;
        return `<button class="prow${on ? ' on' : ''}" type="button" data-printer="${esc(p.path)}"><span class="dot ${st.cls}"></span>
            <span class="grow"><b>${esc(printerLabel(p))}</b><span class="sm">${esc([p.name ? printerName(p) : '', st.text, (p.path || '').replace('file://', ''), tapeLeft(p)].filter(Boolean).join(', '))}</span>
                ${p.weak_connection ? '<span class="sm warn">The Wi-Fi connection seems weak, try moving the printer closer to the Wi-Fi router.</span>' : ''}
                ${p.battery_low ? '<span class="sm warn">The battery is low, charge the printer.</span>' : ''}</span>
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
    const saveSettings = () => { store(own('designer_settings'), S.settings); detectTape(); renderPrinterChip(); tapeChanged = true; redraw(); };
    $$('[data-printer]', box).forEach(b => b.onclick = () => {
        S.printer = S.printers.find(p => p.path === b.dataset.printer) || S.printer;
        store(own('designer_printer'), S.printer.path);
        detectTape();
        renderPrinterChip();
        tapeChanged = true;
        redraw();
    });
    $('#ppRefresh', box).onclick = async () => { await refreshPrinters(true); redraw(); };
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
    $$('[data-col]', box).forEach(b => b.onclick = () => { S.settings.collate = b.dataset.col; saveSettings(); });
    $$('[data-c]', box).forEach(b => b.onclick = () => {
        S.settings.cuts = Object.assign({}, S.settings.cuts, { [printerKey()]: Object.assign(printerCut(), { [b.dataset.c]: b.dataset.v }) });
        saveSettings();
    });
    $('a.link', box).onclick = () => closeLayer(true);
}

const listJoin = a => a.length < 2 ? a.join('') : a.slice(0, -1).join(', ') + ' and ' + a[a.length - 1];

function settingsCard() {
    const p = S.printer;
    const caps = printerCaps(), offersHigh = caps.qualities.includes('high'), noHigh = p && offersHigh && !caps.high_res;
    // Printers without 600 dpi, e.g. the VC-500W, print those labels in
    // quality, those with one quality, e.g. the PUQU Q00, all of them
    const oneQuality = !caps.qualities.includes('fast'), ONE = ' disabled title="This printer prints in one quality only"';
    const q = oneQuality || (!offersHigh && S.settings.quality === 'high') ? 'quality' : S.settings.quality, c = printerCut();
    const without600 = Object.keys(C.models).filter(m => C.models[m].qualities.includes('high') && !C.models[m].high_res).sort();
    // The assumed tape is named, it is the default only if the printer takes it
    const assumed = `${esc(sizeName(S.tape))} is assumed.`;
    const note = S.tapeManual ? 'Set by hand.' : S.tapeUnknown ? `The printer reports ${S.tapeUnknown} tape, which is not offered here. ${assumed}`
        : S.tapeAssumed ? `The printer does not report its tape, ${assumed}` : 'Read from the printer.';
    return `<div class="settings">
        <div><label class="label-s" for="setTape">Tape in the printer</label>
            <select class="field" id="setTape"><option value="">Automatic${S.tapeManual ? '' : ': ' + esc(sizeName(S.tape))}</option>
            ${sizeOptions(manualTape(), x => esc(sizeName(x.id)))}
            </select><span class="muted small">${note}</span></div>
        <div><div class="row" style="gap:.35rem;margin-bottom:.3rem"><span class="label-s" style="margin:0">Quality</span>
            <button type="button" class="info-btn" id="qInfoBtn" aria-expanded="${!!S.qualityInfo}" aria-controls="qInfo" title="About the quality settings"><i class="fa-solid fa-circle-info"></i><span class="sr-only">About the quality settings</span></button></div>
            <div class="seg">
            <button type="button" data-q="fast" class="${q === 'fast' ? 'on' : ''}"${oneQuality ? ONE : ''}>Fast</button>
            <button type="button" data-q="quality" class="${q === 'quality' ? 'on' : ''}"${oneQuality ? ONE : ''}>Quality</button>
            ${!offersHigh ? '' : `<button type="button" data-q="high" class="${q === 'high' ? 'on' : ''}"${noHigh ? ' disabled title="This printer cannot print at 600 dpi"' : ''}>600 dpi, slow</button>`}</div>
            ${noHigh ? `<span class="muted small">${esc(thePrinter(p, true))} cannot print at 600 dpi.</span>` : ''}
            <div class="info-box" id="qInfo"${S.qualityInfo ? '' : ' hidden'}>
                ${caps.colors === 'color' ? `<p>Fast printing is a few seconds quicker, colors come out lighter. Quality heats the paper longer for stronger colors.</p>
                <p>A label takes about 40 seconds, most of it to prepare and preheat. With "After each label" take every label out of the printer, the next one waits for that. "After the last label" keeps the labels together on a strip.</p>`
                : `<p>Fast printing is quicker, the print is a little less sharp.</p>
                <p>600 dpi prints twice as many lines along the label. Labels usually come out much darker, printing is slower, and the result is often worse than at 300 dpi. It is only confirmed for the QL-800. The ${esc(listJoin(without600))} cannot print at 600 dpi.</p>`}
            </div></div>
        <div><span class="label-s">Copies of several files</span>
            <div class="seg">
            <button type="button" data-col="1" class="${collated() ? 'on' : ''}">As sets: ABC ABC</button>
            <button type="button" data-col="0" class="${collated() ? '' : 'on'}">Grouped: AA BB CC</button></div>
            <span class="muted small">For several images or PDF pages printed more than once.</span></div>
        <div><label class="label-s" for="setDate">Dates on labels</label>
            <select class="field" id="setDate">${[['', 'Like the browser: ' + formatDate('2026-11-01', browserDateFormat())], ['%Y-%m-%d', '2026-11-01'], ['%d.%m.%Y', '01.11.2026'], ['%d/%m/%Y', '01/11/2026'], ['%m/%d/%Y', '11/01/2026']]
                .map(([v, n]) => `<option value="${esc(v)}"${(S.settings.dateFmt || '') === v ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select>
            <span class="muted small">For "Today's date" and date blanks. Labels that already contain a date keep its format.</span></div>
        <div class="cutting">${cutSettings('c', c, caps)}
            <div class="cut-show">${cutStrip(c, caps)}<span class="muted small">${esc(cutText(c, caps))}${noCutter(caps) ? '' : ' Labels saved with their own cutting keep it.'}</span></div></div>
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
// Every link to another site, also one opened with the middle button
const elsewhere = a => /^https?:$/.test(a.protocol) && a.origin !== location.origin;
['click', 'auxclick'].forEach(type => document.addEventListener(type, e => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a || !elsewhere(a) || (type === 'auxclick' && e.button !== 1)) return;
    e.preventDefault();
    askToLeave(a.href);
}, true));
// In the help on variables of the editor and the wizard
document.addEventListener('click', e => { if (e.target.closest && e.target.closest('[data-dtcodes]')) showDateCodes(); });
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
        d.print_type = 'qrcode_text';
        d.code_text = item.url;
    } else {
        d.text = item.text.split(/\r?\n/).filter(t => t.trim()).map((t, i) => L(t, i ? 44 : 70, i === 0));
    }
    const before = S.edit || loadEdit();
    setEdit(edit);
    S.editSel = 0;
    if (before && !isEmptyDesign(before.design)) toast('Started a new label with what was shared', { label: 'Undo', run: () => { setEdit(before); renderEditor(); } });
}

// A change the pause after it has not kept yet
addEventListener('pagehide', () => { if (S.edit) storeEdit(); });

(async function start() {
    renderPrinterChip();
    await Promise.all([refreshPrinters(true), loadDemos(), loadSaved(), loadBarcodes()]);
    await receiveShared();
    // Printing state from a page that was closed while printing is unknown
    S.history.forEach(h => { if (h.state === 'printing') { h.state = 'failed'; h.message = 'The page was closed while printing'; } });
    route();
    setInterval(refreshPrinters, 20000);
})();
