// --- UTF-8 Symbol Picker ---------------------------------------------------------
// A simple Bootstrap modal that shows a grid of UTF-8 symbols and inserts the
// selected symbol into the `#label_text` textarea at the caret position.
const SYMBOL_LIST = [
    '★','☆','✓','✔','✕','✖','☐','☑','☒','←','↑','↓','→','↖','↗','↘','↙','↩','↪','•','…','—','–','€','£','¥','¢','§','©','®','±','µ','°','ª','º','¼','½','¾','÷','×','≠','≈','∞','∑','∏','√','∫','∆','π','Ω','σ','λ','Σ','Θ',
    '♠','♣','♥','♦','♪','♫','☼','☀','☂','⛅','☃','☁','⚑','☯','☸','☻','☺','😀','😁','😂','😃','😄','😅','😆','😉','😊','😋','😎','😍','😘','🤔','🤗','🤩','☎','☏','☕','☘','☠','☢','☣','☤','⚐','⚑','⚠'
];

function _ensureSymbolPickerExists() {
    if (document.getElementById('symbolPickerModal')) return;
    // styles
    const style = document.createElement('style');
    style.innerHTML = `
    .symbol-grid { display: grid; grid-template-columns: repeat(auto-fill, 36px); gap:6px; }
    .symbol-btn { width:34px; height:34px; border:1px solid var(--bs-border-color); border-radius:4px; display:inline-flex; align-items:center; justify-content:center; cursor:pointer; font-size:18px; background:var(--bs-body-bg); color:var(--bs-body-color); }
    .symbol-btn:hover { background:var(--bs-tertiary-bg); }
    .icon-btn { font-family: 'Material Design Icons'; font-size: 22px; }
    ${typeof url_for_mdi === 'undefined' ? '' : `@font-face { font-family: 'Material Design Icons'; src: url('${url_for_mdi}materialdesignicons-webfont.ttf') format('truetype'); }`}
    `;
    document.head.appendChild(style);

    // modal markup (Bootstrap 5)
    const modal = document.createElement('div');
    modal.className = 'modal fade';
    modal.id = 'symbolPickerModal';
    modal.tabIndex = -1;
    modal.setAttribute('aria-hidden', 'true');
        modal.innerHTML = `
        <div class="modal-dialog modal-dialog-centered modal-dialog-scrollable">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">Insert symbol</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                <div id="symbolGrid" class="symbol-grid"></div>
                <p class="small text-muted mb-2">Note: Not all symbols may be available in every font; appearance may vary depending on the selected font.</p>
                <div id="iconPicker" ${typeof url_for_mdi === 'undefined' ? 'hidden' : ''}>
                    <label for="iconSearch" class="form-label mt-2">Icons <span class="small text-muted">(Material Design Icons)</span></label>
                    <input type="search" id="iconSearch" class="form-control form-control-sm mb-2" placeholder="Search, e.g. power or lightbulb" autocomplete="off">
                    <div id="iconGrid" class="symbol-grid"></div>
                    <p id="iconHint" class="small text-muted mt-2 mb-0"></p>
                    <button type="button" id="iconShowAll" class="btn btn-outline-secondary btn-sm mt-2" hidden></button>
                </div>
                </div>
            </div>
        </div>`;
    document.body.appendChild(modal);

    // Populate grid
    const grid = modal.querySelector('#symbolGrid');
    SYMBOL_LIST.forEach(sym => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'symbol-btn';
        btn.textContent = sym;
        btn.title = sym;
        btn.addEventListener('click', function (e) {
            e.preventDefault();
            insertSymbolAtCaret(sym);
        });
        grid.appendChild(btn);
    });
}

// Material Design Icons: names and code points, loaded when the picker opens
const MAX_ICON_RESULTS = 120;
let mdiIcons = null;

function _loadIcons() {
    if (mdiIcons !== null || typeof url_for_mdi === 'undefined') return;
    mdiIcons = [];
    fetch(url_for_mdi + 'icons.json')
        .then(response => response.json())
        .then(data => {
            mdiIcons = Object.entries(data.icons).map(([name, code]) => ({ name, char: String.fromCodePoint(parseInt(code, 16)) }));
            _showIcons();
        })
        .catch(() => { mdiIcons = null; });
}

// Icons whose names contain all words of the search, exact and leading
// matches first. Without a search all icons, by name.
function _findIcons(query) {
    const words = query.toLowerCase().replace(/^mdi:/, '').split(/[\s:]+/).filter(w => w);
    if (!mdiIcons) return [];
    if (!words.length) return mdiIcons;
    const rank = icon => (icon.name === words.join('-') ? 0 : icon.name.startsWith(words[0]) ? 1 : 2);
    return mdiIcons
        .filter(icon => words.every(word => icon.name.includes(word)))
        .sort((a, b) => rank(a) - rank(b) || a.name.length - b.name.length || a.name.localeCompare(b.name));
}

function _showIcons(all = false) {
    const grid = document.getElementById('iconGrid');
    const input = document.getElementById('iconSearch');
    if (!grid || !input) return;
    const found = _findIcons(input.value);
    const shown = all === true ? found : found.slice(0, MAX_ICON_RESULTS);
    grid.replaceChildren(...shown.map(icon => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'symbol-btn icon-btn';
        btn.textContent = icon.char;
        btn.title = 'mdi:' + icon.name;
        btn.addEventListener('click', function (e) {
            e.preventDefault();
            insertSymbolAtCaret('{{mdi:' + icon.name + '}}');
        });
        return btn;
    }));
    const hint = document.getElementById('iconHint');
    if (input.value.trim() && !found.length && mdiIcons && mdiIcons.length) {
        hint.textContent = 'No icon found.';
    } else {
        hint.innerHTML = 'A click inserts the name, e.g. <code>{{mdi:power}}</code>. Hover over an icon to see its name.';
    }
    const showAll = document.getElementById('iconShowAll');
    showAll.hidden = shown.length >= found.length;
    showAll.textContent = `Show all ${found.length} icons`;
}

function openSymbolPicker() {
    _ensureSymbolPickerExists();
    const modalEl = document.getElementById('symbolPickerModal');
    const modal = new bootstrap.Modal(modalEl);
    _loadIcons();
    const search = document.getElementById('iconSearch');
    if (search && !search.dataset.bound) {
        search.dataset.bound = '1';
        search.addEventListener('input', () => _showIcons());
        document.getElementById('iconShowAll').addEventListener('click', () => _showIcons(true));
    }
    modal.show();
}

function insertSymbolAtCaret(symbol) {
    const ta = document.getElementById('label_text');
    if (!ta) return;
    const start = ta.selectionStart || 0;
    const end = ta.selectionEnd || 0;
    const before = ta.value.slice(0, start);
    const after = ta.value.slice(end);
    ta.value = before + symbol + after;
    const newPos = start + symbol.length;
    ta.selectionStart = ta.selectionEnd = newPos;
    ta.focus();
    // Trigger change handlers and save
    try { $(ta).trigger('input'); } catch (e) { }
}

// Add picker button next to textarea when the DOM is ready / init runs
$(document).ready(function () {
    const ta = document.getElementById('label_text');
    if (!ta) return;
    // If an HTML button already exists, attach handler; otherwise create it
    let btn = document.getElementById('symbolPickerBtn');
    if (!btn) {
        btn = document.createElement('button');
        btn.type = 'button';
        btn.id = 'symbolPickerBtn';
        btn.className = 'btn btn-outline-secondary btn-sm';
        btn.style.marginLeft = '6px';
        btn.innerHTML = '<i class="fas fa-star"></i> ▾';
        btn.title = 'Insert symbol';
        try {
            const parent = ta.parentElement;
            parent.appendChild(btn);
        } catch (e) {
            ta.insertAdjacentElement('afterend', btn);
        }
    }
    // Ensure we only attach one handler
    btn.removeEventListener && btn.removeEventListener('click', openSymbolPicker);
    btn.addEventListener('click', function (e) {
        e.preventDefault();
        openSymbolPicker();
    });
});
