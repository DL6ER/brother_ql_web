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
    .symbol-btn { width:34px; height:34px; border:1px solid #ddd; border-radius:4px; display:inline-flex; align-items:center; justify-content:center; cursor:pointer; font-size:18px; background:#fff; }
    .symbol-btn:hover { background:#f1f1f1; }
    `;
    document.head.appendChild(style);

    // modal markup (Bootstrap 5)
    const modal = document.createElement('div');
    modal.className = 'modal fade';
    modal.id = 'symbolPickerModal';
    modal.tabIndex = -1;
    modal.setAttribute('aria-hidden', 'true');
        modal.innerHTML = `
        <div class="modal-dialog modal-sm modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">Insert symbol</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                <div id="symbolGrid" class="symbol-grid"></div>
                <p class="small text-muted mb-2">Note: Not all symbols may be available in every font; appearance may vary depending on the selected font.</p>
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

function openSymbolPicker() {
    _ensureSymbolPickerExists();
    const modalEl = document.getElementById('symbolPickerModal');
    const modal = new bootstrap.Modal(modalEl);
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
