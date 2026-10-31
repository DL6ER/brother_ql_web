// Global printer status object to be populated from the API
var printer_status = {
    'errors': [],
    'model': 'Unknown',
    'media_width': 62,
    'media_length': 0,
    'phase_type': 'Unknown',
    'red_support': false
};

// The configured default font (LABEL_DEFAULT_FONT_FAMILY and _STYLE)
const DEFAULT_FONT = $('#font').data('default');

// Returns an array of font settings for each line of label text.
// Each new line inherits the font settings of the previous line.
let fontSettingsPerLine = [];

// Helper: parse a data:...;base64,<b64> data URL into mime and base64 parts
function parseDataUrl(dataUrl) {
    const comma = dataUrl.indexOf(',');
    if (comma === -1) return { mime: null, b64: null };
    const header = dataUrl.substring(5, comma); // skip leading 'data:'
    const mime = header.split(';')[0];
    const b64 = dataUrl.substring(comma + 1);
    return { mime, b64 };
}
// IndexedDB helpers for storing image base64 data (so we avoid large localStorage entries)
function _openImageDB() {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open('brother_ql_images', 1);
        req.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains('images')) db.createObjectStore('images');
        };
        req.onsuccess = (e) => resolve(e.target.result);
        req.onerror = (e) => reject(e.target.error);
    });
}

function _saveImageToDB(id, mime, b64) {
    return _openImageDB().then(db => new Promise((resolve, reject) => {
        const tx = db.transaction('images', 'readwrite');
        const store = tx.objectStore('images');
        // Use add() so we don't overwrite an existing entry with the same id.
        const req = store.add({ mime, b64 }, id);
        req.onsuccess = () => { db.close(); resolve(true); };
        req.onerror = (e) => {
            const err = e && e.target && e.target.error;
            // If the key already exists, add() will throw a ConstraintError —
            // in that case we resolve(false) to indicate we did not change the DB.
            if (err && err.name === 'ConstraintError') {
                db.close();
                resolve(false);
            } else {
                db.close();
                reject(err);
            }
        };
    })).catch(() => null);
}

function _getImageFromDB(id) {
    return _openImageDB().then(db => new Promise((resolve, reject) => {
        const tx = db.transaction('images', 'readonly');
        const store = tx.objectStore('images');
        const req = store.get(id);
        req.onsuccess = (e) => { db.close(); resolve(e.target.result || null); };
        req.onerror = (e) => { db.close(); reject(e.target.error); };
    })).catch(() => null);
}

function _deleteImagesFromDB(id = null) {
    return _openImageDB().then(db => new Promise((resolve, reject) => {
        const tx = db.transaction('images', 'readwrite');
        const store = tx.objectStore('images');
        const req = id === null ? store.clear() : store.delete(id);
        req.onsuccess = () => { db.close(); resolve(true); };
        req.onerror = (e) => { db.close(); reject(e.target.error); };
    })).catch(() => null);
}
function setFontSettingsPerLine() {
    var text = $('#label_text').val() || '';
    var lines = text.split(/\r?\n/);
    if (lines.length === 0) lines = [''];

    // Default font settings from the current UI controls
    var currentFont = {
        font: $('#font option:selected').val() || DEFAULT_FONT,
        size: $('#font_size').val(),
        inverted: $('#font_inverted').is(':checked'),
        checkbox: $('#font_checkbox').is(':checked'),
        align: $('input[name=font_align]:checked').val() || 'center',
        line_spacing: $('input[name=line_spacing]:checked').val() || '100',
        color: $('input[name=print_color]:checked').val() || 'black'
    };

    // Create lines in the <option> with id #lineSelect
    var lineSelect = $('#lineSelect');
    // Get currently selected line number
    var selectedLine = lineSelect.val();
    // Recreate options with possibly updated text
    lineSelect.empty();
    $.each(lines, function (index, line) {
        lineSelect.append($("<option></option>")
            .attr("value", index).text(lines[index] || '(line ' + (index + 1) + ' is empty)'));
    });
    if (selectedLine !== null) {
        // Select the previously active line
        lineSelect.val(selectedLine);
    } else {
        // If no line is selected, we select the first one
        lineSelect.val(0);
    }

    // Should we use the same font settings for all lines?
    const isSynced = $('#sync_font_settings').is(':checked');
    if (isSynced) {
        fontSettingsPerLine = [];
        for (var i = 0; i < lines.length; i++) {
            fontSettingsPerLine[i] = Object.assign({}, currentFont);
            fontSettingsPerLine[i]['text'] = lines[i];
        }
        return;
    }

    // We may need to initialize new lines with current font settings
    if (fontSettingsPerLine.length < lines.length) {
        for (var i = fontSettingsPerLine.length; i < lines.length; i++) {
            if (i === selectedLine || selectedLine === null) {
                // Initialize with default
                fontSettingsPerLine.push(Object.assign({}, currentFont));
            } else {
                // Inherit from previous line
                fontSettingsPerLine.push(Object.assign({}, fontSettingsPerLine[i - 1]));
            }
        }
    }

    // If we have more font settings, remove the excess
    while (fontSettingsPerLine.length > lines.length) {
        fontSettingsPerLine.pop();
    }

    // Update the current line's font settings
    if (fontSettingsPerLine[selectedLine]) {
        fontSettingsPerLine[selectedLine] = Object.assign({}, currentFont);
    }

    // Set text
    for (var i = 0; i < lines.length; i++) {
        fontSettingsPerLine[i]['text'] = lines[i];
    }
}

// Select a font in the font dropdown. A font that is not installed here, e.g.
// in a label saved on another installation, gets an entry of its own, so the
// line keeps it and every preview names the font used instead.
function selectFont(font) {
    const select = document.getElementById('font');
    if (!Array.from(select.options).some(option => option.value === font)) {
        const [family, style] = font.split(',');
        $('<option>').val(font).text(`${family} (${style || ''}), not installed`).prependTo(select);
    }
    select.value = font;
}

// Update font controls when a line is selected
$(document).ready(function () {
    $('#lineSelect').on('change', function () {
        var idx = parseInt($(this).val(), 10);
        if (isNaN(idx) || !fontSettingsPerLine || !fontSettingsPerLine[idx]) return;
        var fs = fontSettingsPerLine[idx];
        // Set font
        selectFont(fs.font || DEFAULT_FONT);
        // Set font size
        $('#font_size').val(fs.size);
        // Set alignment
        $('input[name=font_align]').prop('checked', false);
        $('input[name=font_align][value="' + fs.align + '"]').prop('checked', true).trigger("change");
        // Set line spacing
        $('input[name=line_spacing]').prop('checked', false);
        $('input[name=line_spacing][value="' + fs.line_spacing + '"]').prop('checked', true).trigger("change");
        // Set font inversion
        $('#font_inverted').prop('checked', fs.inverted);
        // Set font color
        $('input[name=print_color]').prop('checked', false);
        $('input[name=print_color][value="' + fs.color + '"]').prop('checked', true).trigger("change");
        // Set checkbox item
        $('#font_checkbox').prop('checked', fs.checkbox);
    });

    // When the user changes the caret/selection in the textarea, update #lineSelect and font controls
    $('#label_text').on('click keyup', function (e) {
        var textarea = this;
        var caret = textarea.selectionStart;
        var lines = textarea.value.split(/\r?\n/);
        var charCount = 0;
        var lineIdx = 0;
        for (var i = 0; i < lines.length; i++) {
            var nextCount = charCount + (lines[i] ? lines[i].length : 0) + 1; // +1 for newline
            if (caret < nextCount) {
                lineIdx = i;
                break;
            }
            charCount = nextCount;
        }
        $('#lineSelect').val(lineIdx).trigger('change');
    });
});

function getModelFromSelectedPrinter(printerSelect) {
    if (printerSelect && printerSelect.value && window.available_printers) {
        const p = window.available_printers.find(p => p.path === printerSelect.value);
        if (p && p.model && p.model !== 'Unknown') return p.model;
    }
    if (printer_status && printer_status.model && printer_status.model !== 'Unknown') {
        return printer_status.model;
    }
    return null;
}

function formData(cut_once = false) {
    data = {
        text: JSON.stringify(fontSettingsPerLine),
        label_size: $('#label_size').val(),
        orientation: $('input[name=orientation]:checked').val(),
        margin_top: parseInt($('#margin_top').val(), 10) || 0,
        margin_bottom: parseInt($('#margin_bottom').val(), 10) || 0,
        margin_left: parseInt($('#margin_left').val(), 10) || 0,
        margin_right: parseInt($('#margin_right').val(), 10) || 0,
        print_type: $('input[name=print_type]:checked').val(),
        barcode_type: $('#barcode_type').val(),
        qrcode_size: parseInt($('#qrcode_size').val(), 10) || 0,
        qrcode_correction: $('#qrcode_correction option:selected').val(),
        image_bw_threshold: parseInt($('#image_bw_threshold').val(), 10) || 0,
        image_mode: $('input[name=image_mode]:checked').val(),
        image_fit: $('#image_fit').is(':checked') ? 1 : 0,
        image_crop: $('#image_crop').is(':checked') ? 1 : 0,
        print_count: parseInt($('#print_count').val(), 10) || 0,
        cut_once: cut_once ? 1 : 0,
        border_thickness: parseInt($('#border_thickness').val(), 10) || 0,
        border_roundness: parseInt($('#border_roundness').val(), 10) || 0,
        border_distance_x: parseInt($('#border_distance_x').val(), 10) || 0,
        border_distance_y: parseInt($('#border_distance_y').val(), 10) || 0,
        high_res: $('#high_res').is(':checked:enabled') ? 1 : 0,
        shrink_text: $('#shrink_text').is(':checked') ? 1 : 0,
        pdf_pages: $('#pdf_pages').val() || '',
        image_scaling_factor: parseInt($('#image_scaling_factor').val(), 10) || 0,
        image_rotation: parseInt($('#image_rotation').val(), 10) || 0,
        sync_font_settings: $('#sync_font_settings').is(':checked') ? 1 : 0
    }

    if (printer_status['red_support']) {
        data['print_color'] = $('input[name=print_color]:checked').val();
        data['border_color'] = $('input[name=border_color]:checked').val();
    }
    data['code_text'] = $('#code_text').val() || '';

    // Include selected printer if available
    const printerSelect = document.getElementById('printer');
    if (printerSelect && printerSelect.value) {
        data['printer'] = printerSelect.value;
        const modelFromPrinter = getModelFromSelectedPrinter(printerSelect);
        if (modelFromPrinter) {
            data['model'] = modelFromPrinter;
        }
    }

    return data;
}

function get_dpi() {
    return $('#high_res').is(':checked:enabled') ? 600 : 300;
}

// With auto-fit, an image without text on a die-cut label is turned to the
// orientation in which it comes out largest. The orientation buttons have no
// effect then.
function showAutoOrientation(auto) {
    $('#autoOrientation').prop('hidden', !auto);
    $('input[name=orientation]').prop('disabled', auto);
}

function updatePreview(data) {
    $('#previewImg').attr('src', 'data:image/png;base64,' + data);
    var img = $('#previewImg')[0];
    img.onload = function () {
        $('#labelWidth').text(Math.round(img.naturalWidth / get_dpi() * 25.4));
        $('#labelHeight').text(Math.round(img.naturalHeight / get_dpi() * 25.4));
    };
}

// Nothing to print yet: no text, no code content and no image
function isLabelEmpty() {
    if ($('input[name=print_type]:checked').val() == 'image') {
        return !imageDropZone || imageDropZone.files.length === 0;
    }
    return ($('#label_text').val() || '').trim() === '' && ($('#code_text').val() || '').trim() === '';
}

// Show the label on the stage like the roll in the printer: endless, die-cut
// or round, with a hint while there is nothing to print
function updateLabelStage() {
    const option = $('#label_size option:selected');
    let form = 'endless';
    if (option.data('round') == 'True') {
        form = 'round';
    } else if (parseInt(option.data('y'), 10) > 0) {
        form = 'die-cut';
    }
    const empty = isLabelEmpty();
    $('#labelStage').attr('data-form', form).toggleClass('is-empty', empty);
    $('#labelEmpty').text($('input[name=print_type]:checked').val() == 'image'
        ? 'Choose an image to see the label.' : 'Type a text to see the label.');
    // Codes like DK-22205 stay on one line. Non-breaking hyphens would do
    // the same, but many fonts do not have them.
    const parts = option.text().trim().split(/(\S*-\S*)/);
    $('#previewLabelName').empty().append(parts.map(part =>
        part.includes('-') ? $('<span class="text-nowrap">').text(part) : document.createTextNode(part)));
}

var lastPreviewData = null;
// Previews may answer out of order, only the newest one is shown
let previewSeq = 0;
function gen_label(preview = true, cut_once = false) {
    // Check label against installed label in the printer
    updatePrinterStatus();

    // Update font settings for each line
    setFontSettingsPerLine();

    // Show or hide image upload box
    if ($('input[name=print_type]:checked').val() == 'image') {
        $('#groupLabelImage').show();
    } else {
        $('#groupLabelImage').hide();
    }

    if (preview) {
        updateLabelStage();
        updateMarginHints();
    }

    let type = preview ? 'preview' : 'printing';

    const data = formData(cut_once);
    let body = data;
    const imageLabel = $('input[name=print_type]:checked').val() == 'image';
    if (imageLabel) {
        // Without an image there is nothing to send
        if (imageDropZone.files.length === 0) {
            setStatus({ type: type, 'status': '' });
            return;
        }
        // The preview shows the first image, printing takes all of them,
        // each as a label of its own
        body = new FormData();
        $.each(data, (key, value) => body.append(key, value));
        const files = preview ? imageDropZone.files.slice(0, 1) : imageDropZone.files;
        files.forEach(file => body.append('image', file, file.name));
        lastPreviewData = null;
    } else {
        // Compare to the last preview, return if nothing has changed
        const dataJson = JSON.stringify(data);
        if (preview && dataJson === lastPreviewData) {
            console.debug("No changes detected, not generating new preview.");
            return;
        }
        lastPreviewData = dataJson;
    }
    setStatus({ type: type, 'status': 'pending' });

    // Send printing request
    const url = preview ? (url_for_preview + '?return_format=base64') : url_for_print;
    const seq = preview ? ++previewSeq : null;
    $.ajax({
        type: 'POST',
        url: url,
        data: body,
        // Image labels send the files as multipart/form-data
        contentType: imageLabel ? false : 'application/x-www-form-urlencoded; charset=UTF-8',
        processData: !imageLabel,
        success: function (data, _textStatus, xhr) {
            if (preview && seq !== previewSeq) return;
            // Check if response is JSON and has a key "success"
            const status = typeof data === "object" &&
                data !== null &&
                "success" in data &&
                data.success === false ?
                'error' : 'success';
            setStatus({ type: type, 'status': status, warning: xhr.getResponseHeader('X-Label-Warning') });
            if (!preview) return;
            updatePreview(data);
            showAutoOrientation(xhr.getResponseHeader('X-Label-Orientation') === 'auto');
            // Pages can be chosen of a PDF with several
            const pages = parseInt(xhr.getResponseHeader('X-PDF-Pages') || '', 10) || 0;
            $('#pdfPageCount').text(pages);
            $('#pdfPagesGroup').prop('hidden', pages < 2);
        },
        error: function (xhr, _status, error) {
            if (preview && seq !== previewSeq) return;
            message = xhr.responseJSON ? xhr.responseJSON.message : error;
            const text = preview ? 'Preview generation failed' : 'Printing failed';
            setStatus({ type: type, 'status': 'error', 'message': message }, text);
        }
    });
}

function print(cut_once = false) {
    gen_label(false, cut_once);
}

function preview() {
    gen_label(true);
}

function setStatus(data, what = null) {
    let type = data.type || '';
    let status = data.status || '';
    let message = data.message || '';
    let errors = data?.errors || [];
    let extra_info = message ? ': ' + escapeHtml(message) : '';
    if (errors.length > 0) {
        extra_info += '<br />' + errors.map(escapeHtml).join('<br />');
    }

    let html = '';
    // State of the printer dot in the navbar: idle, busy, success or error
    let state = '';

    if (type === 'preview' || type === 'printing') {
        if (status === 'pending') {
            const action = type === 'printing' ? 'Printing' : 'Updating preview';
            html = `<span class="spinner-border spinner-border-sm" aria-hidden="true"></span><span>${action}</span>`;
            state = type === 'printing' ? 'busy' : '';
        } else if (status === 'success') {
            if (type === 'preview' && data.warning) {
                // e.g. text that does not fit on the label
                html = `<span class="fas fa-exclamation-triangle text-warning" aria-hidden="true"></span><span>${escapeHtml(data.warning)}</span>`;
            } else if (type === 'preview') {
                html = `<span class="fas fa-check text-success" aria-hidden="true"></span><span>Preview updated</span>`;
            } else {
                html = `<span class="fas fa-check-circle text-success" aria-hidden="true"></span><span>Printed</span>`;
                state = 'success';
            }
        } else if (status === 'error') {
            const action = type === 'preview' ? 'The preview failed' : 'Printing failed';
            html = `<div class="alert alert-danger d-flex gap-2 mb-0" role="alert">
                        <span class="fas fa-exclamation-triangle mt-1" aria-hidden="true"></span>
                        <div>${action}${extra_info}</div>
                    </div>`;
            state = 'error';
        }
    } else if (type === 'status' && status === 'error') {
        html = `<div class="alert alert-warning d-flex gap-2 mb-0" role="alert">
                    <span class="fas fa-exclamation-triangle mt-1" aria-hidden="true"></span>
                    <div>${escapeHtml(data.title || 'The printer reports an error')}${extra_info}</div>
                </div>`;
        state = 'error';
    }

    const elem = type === 'status' ? $('#printerStatusPanel') : $('#statusPanel');
    elem.html(html);
    elem.toggle(html.length > 0);

    // Printer errors keep the dot red until they are gone
    const dot = $('#printerIcon');
    if (type === 'status') {
        dot.toggleClass('has-error', state === 'error');
    } else if (state !== '' || status === 'pending') {
        dot.removeClass('is-busy is-success is-error');
        if (state) dot.addClass('is-' + state);
    }
    $('#printButton').prop('disabled', false);
    $('#dropdownPrintButton').prop('disabled', false);
}

// Collects the images, gen_label() sends them. Several files are printed
// one after the other, each as a label of its own.
let imageDropZone = null;
new Dropzone("#image-dropzone", {
    // Required by Dropzone, it never uploads itself
    url: url_for_preview,
    dictDefaultMessage: 'Drop images or PDFs here, click to choose them, or paste one with Ctrl+V',
    dictRemoveFile: 'Remove',
    acceptedFiles: 'image/*,application/pdf',
    addRemoveLinks: true,
    autoProcessQueue: false,
    thumbnailMethod: 'contain',
    init: function () {
        imageDropZone = this;

        this.on("addedfile", updateImageFilesNote);
        this.on("removedfile", function () {
            if (this.files.length === 0) $('#pdfPagesGroup').prop('hidden', true);
            updateImageFilesNote();
        });
    },

    accept: function (file, done) {
        // If a valid file was added, perform the preview
        done();
        preview();
    },

    removedfile: function (file) {
        file.previewElement.remove();
        showAutoOrientation(false);
        preview();
        // Insert a dummy image
        updatePreview('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=');
    }
});


function toggleQrSettings() {
    var barcodeType = document.getElementById('barcode_type');
    var qrCodeSize = document.getElementById('qrCodeSizeContainer');
    var qrCodeCorrection = document.getElementById('qrCodeCorrectionContainer');
    if (barcodeType) {
        qrCodeSize.style.display = (barcodeType.value === 'QR') ? '' : 'none';
        qrCodeCorrection.style.display = (barcodeType.value === 'QR') ? '' : 'none';
    }
}

function get_barcode_types() {
    // Populate barcode select menu from /api/barcodes
    fetch(url_for_get_barcodes)
        .then(response => response.json())
        .then(data => {
            const select = document.getElementById('barcode_type');
            barcodes = data['barcodes'];
            if (select && Array.isArray(barcodes) && barcodes.length > 0) {
                barcodes.forEach((barcode, idx) => {
                    const opt = document.createElement('option');
                    opt.value = barcode;
                    opt.textContent = barcode;
                    if (idx === 0) {
                        opt.selected = true;
                        // Set data-default="1" for first element
                        opt.setAttribute("data-default", "1");
                    }
                    select.appendChild(opt);
                });
                toggleQrSettings();
                select.addEventListener('change', toggleQrSettings);

                // Continue initializing page...
                init2();
            }
        });
}

// Show only the label sizes the printer can use (data-models, computed by
// label_printers() on the server)
function filterLabelSizes(model) {
    const select = document.getElementById('label_size');
    if (!select) return;
    const known = model && model !== 'Unknown';
    for (const option of select.options) {
        option.hidden = known && !option.dataset.models.split(',').includes(model);
    }
    // Never hide the selected size, e.g. one restored from the settings
    if (select.selectedOptions[0]) select.selectedOptions[0].hidden = false;
}

// The label the printer reports as loaded: the selected label size if it
// matches, otherwise the first offered one that does (black only before
// black/red, they have the same size). Labels the dropdown does not offer get
// a name from their size and no value.
function loadedLabel() {
    const width = printer_status.media_width;
    if (width === null || width === undefined) return null;
    const length = printer_status.media_length || 0;
    const fits = option => parseInt(option.dataset.x, 10) === width && parseInt(option.dataset.y, 10) === length;
    const select = document.getElementById('label_size');
    const selected = select.selectedOptions[0];
    if (selected && fits(selected)) {
        return { value: selected.value, name: selected.text.trim(), selected: true };
    }
    const offered = Array.from(select.options).filter(option => !option.hidden && fits(option));
    const option = offered.find(o => !o.value.includes('red')) || offered[0];
    if (option) {
        return { value: option.value, name: option.text.trim(), selected: false };
    }
    return { value: null, name: length > 0 ? `${width} x ${length} mm` : `${width} mm endless`, selected: false };
}

// The QL-1100 series prints at 300 dpi only
function update600Dpi() {
    const unsupported = models_without_600_dpi.includes(printer_status.model);
    $('#highResUnsupported').prop('hidden', !unsupported);
    $('#highResModel').text(printer_status.model);
    const changed = $('#high_res').prop('disabled') !== unsupported;
    $('#high_res').prop('disabled', unsupported);
    if (changed && $('#high_res').is(':checked')) {
        updateMarginHints();
        preview();
    }
}

function updatePrinterStatus() {
    filterLabelSizes(printer_status.model);
    update600Dpi();
    if ($('#label_size option:selected').val().includes('red')) {
        $(".red-support").show();
    } else {
        $('#print_color_black').prop('active', true);
        $(".red-support").hide();
    }

    // Name the label the printer reports and offer it when another one is
    // selected. The simulator and network printers report none.
    const loaded = loadedLabel();
    const mismatch = loaded !== null && !loaded.selected;
    $('#detectedLabel').prop('hidden', !(loaded && loaded.selected));
    $('#detectedLabelName').text(loaded ? loaded.name : '');
    $('#labelMismatch, #labelMismatchIcon').toggle(mismatch);
    $('#useDetectedLabel').prop('hidden', !(mismatch && loaded.value));

    if (missingPrinter) {
        const used = $('#printer option:selected').text() || 'another printer';
        const message = 'The printer ' + missingPrinter.replace(/^file:\/\//, '') + ' is not available. Labels are sent to ' + used + ' instead.';
        setStatus({ type: 'status', status: 'error', title: 'Printer not available', errors: [message].concat(printer_status.errors || []) });
    }
    else if (printer_status.errors && printer_status.errors.length > 0) {
        setStatus({ type: 'status', status: 'error', errors: printer_status.errors });
    }
    else {
        // Clear printer errors
        setStatus({ type: 'status', status: 'success' });
    }
}

// The printer chosen by the user or restored from the settings. If it is not
// available, another printer is used and a warning is shown until it is back.
var preferredPrinter = null;
var missingPrinter = null;

async function getPrinterStatus() {
    const response = await fetch(url_for_get_printer_status);
    const data = await response.json();
    // If server returned multiple printers, populate the select and pick the chosen one
    if (data && Array.isArray(data.printers)) {
        const select = document.getElementById('printer');
        if (select) {
            // remember current selection
            const cur = preferredPrinter || select.value;
            select.innerHTML = '';
            data.printers.forEach((p) => {
                const opt = document.createElement('option');
                opt.value = p.path || '';
                const displayPath = p.path? p.path.replace(/file:\/\//g, '' ) : '';
                opt.textContent = (p.model || 'Unknown') + ' @ ' + displayPath;
                select.appendChild(opt);
            });
            // choose previously selected or server selected or first
            if (cur && Array.from(select.options).some(o => o.value === cur)) {
                select.value = cur;
            } else if (data.selected) {
                select.value = data.selected;
            } else if (data.printers[0]) {
                select.value = data.printers[0].path;
            }
            missingPrinter = cur && select.value !== cur ? cur : null;
        }
        // Choose active printer status based on the select value
        const chosenPath = (document.getElementById('printer') || {}).value || (data.selected || (data.printers[0] && data.printers[0].path));
        let chosen = data.printers.find(p => p.path === chosenPath) || data.printers[0] || {};
        printer_status = chosen;
        // keep a list of available printers globally
        window.available_printers = data.printers;
    } else {
        printer_status = data;
    }
    updatePrinterStatus();
}

// --- Local Storage Save/Restore/Export/Import/Reset ---
const MAX_HISTORY = 40;
const LS_KEY = 'labeldesigner_settings_v1';
const LS_HISTORY_KEY = 'labeldesigner_settings_history_v1';
var current_restoring = false;
function saveAllSettingsToLocalStorage() {
    const data = {};
    // Save all input/select/textarea values
    $('input, select, textarea').each(function () {
        // Skip the value of #lineSelect
        if (this.id === 'lineSelect') return;
        // Prefer name over id for correct handling of radio buttons
        const key = this.type === 'radio' && this.name.length > 0 ? this.name : this.id;
        if (key.length == 0) return;
        if (this.type === 'checkbox') {
            data[key] = $(this).is(':checked');
        }
        else if (this.type === 'radio') {
            if ($(this).is(':checked') || $(this).parent().hasClass('active')) {
                data[key] = $(this).val();
            }
        } else {
            data[key] = $(this).val();
        }
    });
    // Save the font settings of each line. fontSettingsPerLine is declared
    // with let, so it is not available as window.fontSettingsPerLine.
    if (fontSettingsPerLine) {
        data['fontSettingsPerLine'] = JSON.stringify(fontSettingsPerLine);
    }
    // Store image metadata and persist base64 into IndexedDB (avoid large localStorage entries)
    try {
        if (imageDropZone && imageDropZone.files && imageDropZone.files.length > 0) {
            const f = imageDropZone.files[0];
            const dataUrl = imageDropZone.files[0].dataURL;
            const parsed = parseDataUrl(dataUrl);
            if (parsed.b64) {
                const imageHash = generateHash(parsed.b64);
                _saveImageToDB(imageHash, parsed.mime, parsed.b64).catch(e => console.debug('Failed saving image to IndexedDB', e));
                data['image_ref'] = imageHash;
                data['image_mime'] = parsed.mime;
                data['image_name'] = f.name || 'image';
            }
        }
    } catch (e) {
        console.debug('No image to save to localStorage', e);
    }
    const this_settings = JSON.stringify(data);
    localStorage.setItem(LS_KEY, this_settings);

    // --- History logic ---
    let history = [];
    try {
        history = JSON.parse(localStorage.getItem(LS_HISTORY_KEY)) || [];
    } catch { history = []; }
    // Only push if different from last
    if (history.length === 0 || JSON.stringify(history[history.length - 1]) !== this_settings) {
        // Log difference between the current and the previous state when saving history
        console.debug(compareObjects(history[history.length - 1], data));
        history.push(data);
        if (history.length > MAX_HISTORY) history = history.slice(history.length - MAX_HISTORY);
        localStorage.setItem(LS_HISTORY_KEY, JSON.stringify(history));
    }
    updateUndoButton();
}

function undoSettings() {
    let history = [];
    try {
        history = JSON.parse(localStorage.getItem(LS_HISTORY_KEY)) || [];
    } catch { history = []; }
    if (history.length < 2) return; // nothing to undo
    // Log difference between the current and the previous state when undoing
    console.debug(compareObjects(history[history.length - 1], history[history.length - 2]));
    // Remove current state
    history.pop();
    const prev = history[history.length - 1];
    localStorage.setItem(LS_HISTORY_KEY, JSON.stringify(history));
    localStorage.setItem(LS_KEY, JSON.stringify(prev));
    restoreAllSettingsFromLocalStorage();
    updateUndoButton();
}

function updateUndoButton() {
    let history = [];
    try {
        history = JSON.parse(localStorage.getItem(LS_HISTORY_KEY)) || [];
    } catch { history = []; }
    const steps = Math.max(0, history.length - 1);
    $('#undoCounter').text(steps).prop('hidden', steps === 0);
    $('#undoSettingsBtn').prop('disabled', steps === 0);
}

function restoreAllSettingsFromLocalStorage() {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return;

    let data;
    try { data = JSON.parse(raw); } catch { return; }
    current_restoring = true;
    $('input, select, textarea').each(function () {
        const key = this.type === 'radio' && this.name.length > 0 ? this.name : this.id;
        if (!(key in data)) return;
        if (this.id === 'printer') {
            // The printer list arrives with the next status query, the stored
            // printer is chosen then if it is available
            preferredPrinter = data[key] || null;
            if (Array.from(this.options).some(o => o.value === data[key])) this.value = data[key];
            return;
        }
        if (this.tagName === 'SELECT' && !Array.from(this.options).some(o => o.value == data[key])) {
            // e.g. a font or label size that is not available here
            return;
        }
        if (this.type === 'checkbox') {
            $(this).prop('checked', !!data[key]);
        } else if (this.type === 'radio') {
            if ($(this).val() == data[key]) {
                this.checked = true;
                $(`label[for="${this.id}"]`).addClass('active');
            } else {
                this.checked = false;
                $(`label[for="${this.id}"]`).removeClass('active');
            }
        } else if (this.type === 'number' && $(this).data('default') !== undefined && String(data[key] ?? '').trim() === '') {
            // An emptied number field, e.g. the font size, would make every
            // preview fail after reloading, it keeps its default instead
            return;
        } else {
            this.value = data[key];
            console.log(key + ": " + data[key]);
        }
    });

    // Restore the font settings of each line and show those of the first
    // line in the font controls, otherwise the next preview would copy the
    // controls into the first line
    if (data['fontSettingsPerLine']) {
        try {
            const restored = JSON.parse(data['fontSettingsPerLine']);
            if (Array.isArray(restored) && restored.length > 0) {
                // Lines with an emptied font size get the default size
                restored.forEach(line => {
                    if (String(line.size ?? '').trim() === '') line.size = String($('#font_size').data('default'));
                });
                fontSettingsPerLine = restored;
                const lineSelect = $('#lineSelect').empty();
                restored.forEach((line, index) => {
                    lineSelect.append($('<option></option>').attr('value', index).text(line.text || '(line ' + (index + 1) + ' is empty)'));
                });
                lineSelect.val(0).trigger('change');
            }
        } catch { }
    }

    // If image data is available (embedded) populate Dropzone, otherwise try IndexedDB by image_ref
    let imageRestorePromise = Promise.resolve();
    try {
        if (data.image_ref) {
            // try to retrieve from IndexedDB
            imageRestorePromise = _getImageFromDB(data.image_ref).then(record => {
                if (record && record.b64) {
                    const dataUrl = 'data:' + (record.mime || 'image/png') + ';base64,' + record.b64;
                    return fetch(dataUrl)
                        .then(res => res.blob())
                        .then(blob => {
                            const file = new File([blob], data.image_name || 'image', { type: record.mime || 'image/png' });
                            try { imageDropZone.removeAllFiles(true); } catch (e) { }
                            try { imageDropZone.addFile(file); } catch (e) { }
                        }).catch(e => {
                            console.debug('Failed to fetch blob from IndexedDB dataUrl', e);
                        });
                }
            }).catch(e => {
                console.debug('Failed to read image from IndexedDB', e);
            });
        }
    } catch (e) {
        console.debug('No image to restore from storage', e);
    }
    // Trigger preview after restore (after image restoration promise settles)
    imageRestorePromise.finally(() => {
        preview();
        current_restoring = false;
    });
}

// --- Repository UI functions ---------------------------------------------------------
function openRepositoryModal() {
    // show modal and load list
    const modalEl = document.getElementById('repoModal');
    if (!modalEl) return;
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
    loadRepositoryList();
}

// Bootstrap ignores hide() while the dialog is still opening, e.g. when a
// label is loaded right after the dialog was opened
let repoModalOpening = false;
$('#repoModal').on('show.bs.modal', () => { repoModalOpening = true; });
$('#repoModal').on('shown.bs.modal', () => { repoModalOpening = false; });

function closeRepositoryModal() {
    const modalEl = document.getElementById('repoModal');
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    if (repoModalOpening) {
        modalEl.addEventListener('shown.bs.modal', () => modal.hide(), { once: true });
    } else {
        modal.hide();
    }
}

function loadRepositoryList() {
    const body = $('#repoListBody');
    body.html('<p class="text-muted">Loading...</p>');
    fetch(url_for_repo_list)
        .then(r => r.json())
        .then(data => {
            renderRepoList(data.files || []);
        })
        .catch(e => {
            body.html('<div class="text-danger">Failed to load repository list.</div>');
            console.error(e);
        });
}

// Name of a saved label without the file ending
function repoDisplayName(name) {
    return String(name).replace(/\.json$/i, '');
}

function renderRepoList(files) {
    const body = $('#repoListBody');
    if (!files || files.length === 0) {
        body.html('<p class="text-body-secondary mb-0">No saved labels yet. Enter a name above to save the current label.</p>');
        return;
    }
    const list = $('<div class="list-group repo-list"></div>');
    files.forEach(f => {
        const name = escapeHtml(f.name);
        const item = $(
            `<div class="list-group-item repo-item" data-search="${escapeHtml(repoDisplayName(f.name).toLowerCase())}">
                <img class="repo-thumb" alt="" src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAAAQCAYAAAB49c9kAAAAE0lEQVR42mNgGAWjYBSMglEwCQAAP1gF8bQqT9kAAAAASUVORK5CYII=" />
                <div class="repo-info">
                    <div class="repo-name">${escapeHtml(repoDisplayName(f.name))}</div>
                    <div class="small text-body-secondary">${f.label_size ? escapeHtml(f.label_size) : 'Unknown label size'}</div>
                    <div class="small text-body-secondary">Saved ${new Date(f.mtime * 1000).toLocaleString()}</div>
                </div>
                <div class="repo-actions">
                    <button class="btn btn-sm btn-primary repo-load" data-name="${name}">Load</button>
                    <button class="btn btn-sm btn-outline-secondary repo-print" data-name="${name}"><span class="fas fa-print me-1" aria-hidden="true"></span>Print</button>
                    <button class="btn btn-sm btn-outline-danger repo-delete" data-name="${name}" title="Delete" aria-label="Delete ${escapeHtml(repoDisplayName(f.name))}"><span class="fas fa-trash" aria-hidden="true"></span></button>
                </div>
            </div>`
        );
        list.append(item);
        // fetch thumbnail asynchronously (do not override main preview)
        const img = item.find('.repo-thumb')[0];
        if (img) {
            repoFetchThumbnail(f.name, img);
        }
    });
    body.html(list);
    filterRepoList();

    // Attach handlers
    $('.repo-preview').off('click').on('click', function () {
        const name = $(this).data('name');
        repoPreview(name);
    });
    $('.repo-load').off('click').on('click', function () {
        const name = $(this).data('name');
        repoLoad(name);
    });
    $('.repo-delete').off('click').on('click', function () {
        const name = $(this).data('name');
        if (!confirm('Delete the saved label "' + repoDisplayName(name) + '"?')) return;
        repoDelete(name);
    });
    $('.repo-print').off('click').on('click', function () {
        const name = $(this).data('name');
        repoPrint(name);
    });
}

function repoSaveCurrent() {
    const name = $('#repoSaveName').val();
    if (!name) {
        alert('Please provide a name to save.');
        return;
    }
    // Ensure settings are saved to localStorage
    try { saveAllSettingsToLocalStorage(); } catch (e) { }
    let payload = {};
    try { payload = JSON.parse(localStorage.getItem(LS_KEY) || '{}'); } catch (e) { payload = {}; }
    payload['name'] = name;

    // Save text properties
    try {
        if (!payload.hasOwnProperty('text')) {
            if (payload.hasOwnProperty('fontSettingsPerLine')) {
                payload['text'] = payload['fontSettingsPerLine'];
            } else if (typeof fontSettingsPerLine !== 'undefined') {
                payload['text'] = fontSettingsPerLine;
            } else {
                payload['text'] = '[]';
            }
        }
    } catch (e) { /* ignore */ }

    // If an image is present in Dropzone, encode it as base64 and include
    // it in the JSON payload so the server can accept pure JSON saves.
    function sendJsonPayload(p) {
        fetch(url_for_repo_save, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
            body: JSON.stringify(p)
        }).then(r => r.json())
            .then(resp => {
                if (resp && (resp.success || resp.name)) {
                    try { $('#repoSaveName').val(''); } catch (e) {}
                    loadRepositoryList();
                } else {
                    alert('Save failed: ' + (resp && resp.message ? resp.message : 'Unknown'));
                }
            }).catch(e => {
                console.error(e);
                alert('Save failed');
            });
    }

    try {
        if (imageDropZone && imageDropZone.files && imageDropZone.files.length > 0) {
            const f = imageDropZone.files[0];
            // Use FileReader to read the file as data URL
            const reader = new FileReader();
            reader.onload = function (e) {
                try {
                    const dataUrl = e.target.result;
                    const parsed = parseDataUrl(dataUrl);
                    if (parsed.b64) {
                        payload['image_data'] = parsed.b64;
                        payload['image_mime'] = parsed.mime;
                        payload['image_name'] = f.name || 'image';
                    }
                } catch (err) {
                    console.warn('Failed to extract base64 from FileReader result', err);
                }
                sendJsonPayload(payload);
            };
            reader.onerror = function (err) {
                console.warn('Failed to read image for repo save', err);
                // fallback: send JSON without image
                sendJsonPayload(payload);
            };
            reader.readAsDataURL(f);
            return;
        }
    } catch (e) {
        console.warn('No image to attach to repo save', e);
    }

    // No image — send JSON payload directly
    sendJsonPayload(payload);
}

function repoLoad(name) {
    fetch(url_for_repo_load + '?name=' + encodeURIComponent(name))
        .then(r => {
            if (!r.ok) throw new Error('Load failed');
            return r.json();
        })
        .then(data => {
            try {
                data['fontSettingsPerLine'] = data['text'] || '[]';
                // A saved label does not change the printer
                data['printer'] = $('#printer').val() || null;
                localStorage.setItem(LS_KEY, JSON.stringify(data));
            } catch (e) { }
            restoreAllSettingsFromLocalStorage();
            // If the repository entry includes an embedded image, populate Dropzone
            try {
                const img_b64 = data.image_data;
                const img_mime = data.image_mime || 'image/png';
                const img_name = data.image_name || data.image || 'image';
                if (img_b64) {
                    const dataUrl = 'data:' + img_mime + ';base64,' + img_b64;
                    fetch(dataUrl)
                        .then(res => res.blob())
                        .then(blob => {
                            const file = new File([blob], img_name, { type: img_mime });
                            try { imageDropZone.removeAllFiles(true); } catch (e) {}
                            // Use Dropzone's API to add the file so it is
                            // processed identically to a user upload.
                            try {
                                imageDropZone.addFile(file);
                            } catch (e) {
                                console.warn('Failed to populate Dropzone with repository image', e);
                            }
                            preview();
                        }).catch(e => console.warn('Failed to load image blob', e));
                }
            } catch (e) { console.warn(e); }
            closeRepositoryModal();
        }).catch(e => {
            console.error(e);
            alert('Failed to load label');
        });
}

function repoDelete(name) {
    fetch(url_for_repo_delete + '?name=' + encodeURIComponent(name), { method: 'POST', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(r => r.json())
        .then(resp => {
            if (resp && resp.success) {
                loadRepositoryList();
            } else {
                alert('Delete failed: ' + (resp && resp.message ? resp.message : 'Unknown'));
            }
        }).catch(e => {
            console.error(e);
            alert('Delete failed');
        });
}

function repoPreview(name) {
    // request base64 preview
    fetch(url_for_repo_preview + '?name=' + encodeURIComponent(name) + '&return_format=base64')
        .then(r => {
            if (!r.ok) throw new Error('Preview failed');
            return r.text();
        })
        .then(b64 => {
            updatePreview(b64);
        }).catch(e => {
            console.error(e);
            alert('Preview failed');
        });
}

function repoPrint(name) {
    // Ask server to print a repository template by name
    const printerSelect = document.getElementById('printer');
    const body = new URLSearchParams();
    body.append('name', name);
    if (printerSelect && printerSelect.value) body.append('printer', printerSelect.value);
    const modelVal = getModelFromSelectedPrinter(printerSelect);
    if (modelVal) body.append('model', modelVal);

    fetch(url_for_repo_print, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest' }, body: body })
        .then(r => r.json())
        .then(resp => {
            if (resp && resp.success) {
                setStatus({ type: 'printing', status: 'success' });
                console.log('Print job queued successfully');
            } else {
                const msg = resp && resp.message ? resp.message : 'Print failed';
                setStatus({ type: 'printing', status: 'error', message: msg });
                console.error(msg);
            }
        }).catch(e => {
            console.error(e);
            console.error('Print failed');
        });
}

function repoFetchThumbnail(name, imgEl) {
    // include selected printer if present so preview matches selected device
    const printerSelect = document.getElementById('printer');
    const printer = printerSelect && printerSelect.value ? printerSelect.value : null;
    let url = url_for_repo_preview + '?name=' + encodeURIComponent(name) + '&return_format=base64';
    if (printer) url += '&printer=' + encodeURIComponent(printer);
    fetch(url)
        .then(r => {
            if (!r.ok) throw new Error('Preview fetch failed');
            return r.text();
        })
        .then(b64 => {
            imgEl.src = 'data:image/png;base64,' + b64;
        })
        .catch(e => {
            console.debug('Thumbnail fetch failed for', name, e);
            imgEl.style.opacity = 0.4;
        });
}

// Show only the saved labels whose name contains the search text
function filterRepoList() {
    const query = ($('.repo-search').val() || '').trim().toLowerCase();
    $('#repoListBody .repo-item').each(function () {
        this.hidden = query !== '' && !this.dataset.search.includes(query);
    });
}

// Show the margins in mm, the unit of the label sizes
function updateMarginHints() {
    const dpi = get_dpi();
    $('[data-mm-for]').each(function () {
        const px = parseInt($('#' + this.dataset.mmFor).val(), 10);
        this.textContent = isNaN(px) ? '' : (px / dpi * 25.4).toFixed(1) + ' mm';
    });
}

// Wire modal button on DOM ready
$(document).ready(function () {
    $('#openRepoBtn').off('click').on('click', openRepositoryModal);
    $('#printer').on('change', function () {
        preferredPrinter = this.value;
        missingPrinter = null;
    });
    $('#repoSaveBtn').off('click').on('click', repoSaveCurrent);
    $('.repo-search').on('input', filterRepoList);

    // Copies: buttons next to the number field
    $('#printCountDown, #printCountUp').on('click', function () {
        const input = $('#print_count');
        const step = this.id === 'printCountUp' ? 1 : -1;
        const min = parseInt(input.attr('min'), 10) || 1;
        const max = parseInt(input.attr('max'), 10) || 100;
        const value = (parseInt(input.val(), 10) || min) + step;
        input.val(Math.min(max, Math.max(min, value))).trigger('change');
    });

    $('#margin_top, #margin_bottom, #margin_left, #margin_right, #high_res').on('input change', updateMarginHints);
});

function resetSettings() {
    if (confirm('Really reset all label settings to default?')) {
        localStorage.removeItem(LS_KEY);
        // Reset font settings
        fontSettingsPerLine = [];
        set_all_inputs_default(true);
        saveAllSettingsToLocalStorage();
        location.reload();
    }
}

function set_all_inputs_default(force = false) {
    // Iterate over those <input> that have a data-default propery and set the value if empty
    $('input[data-default], select[data-default], textarea[data-default]').each(function () {
        if (this.type === 'checkbox' || this.type === 'radio') {
            $(this).prop('checked', $(this).data('default') == 1 || $(this).data('default') == true);
        }
        else if (this.type === 'select-one' || this.type === 'number') {
            $(this).val($(this).data('default'));

        }
        else if (!$(this).val() || force) {
            $(this).val($(this).data('default'));
        }
    });
}

window.onload = async function () {
    // Get supported barcodes
    get_barcode_types();

    // Get printer status once ...
    getPrinterStatus();
    // ... and update it every 5 seconds
    setInterval(getPrinterStatus, 5000);

    $('#useDetectedLabel').on('click', function () {
        const loaded = loadedLabel();
        if (loaded && loaded.value) {
            $('#label_size').val(loaded.value).trigger('change');
        }
    });

    // Ctrl+Enter (Cmd+Enter on macOS) prints from anywhere on the page
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' || !(e.ctrlKey || e.metaKey) || e.repeat) return;
        if ($('#printButton').prop('disabled') || $('.modal.show').length > 0) return;
        e.preventDefault();
        print();
    });

    // An image or PDF pasted from the clipboard, e.g. a screenshot, becomes
    // the image of the label. Text pasted into a field stays text.
    document.addEventListener('paste', function (e) {
        const clipboard = e.clipboardData;
        if (!clipboard || !imageDropZone) return;
        if ($(e.target).is('input, textarea') && clipboard.types.includes('text/plain')) return;
        const file = Array.from(clipboard.files).find(f => f.type.startsWith('image/') || f.type === 'application/pdf');
        if (!file) return;
        e.preventDefault();
        useImageFile(file);
    });
}

// Make a file the image of the label, e.g. one pasted or shared. It
// replaces the images there are, dropped and chosen files are added.
function useImageFile(file) {
    if ($('input[name=print_type]:checked').val() !== 'image') {
        $('#printTypeImage').prop('checked', true).trigger('change');
    }
    imageDropZone.removeAllFiles(true);
    imageDropZone.addFile(file);
}

// Several images are printed one after the other
function updateImageFilesNote() {
    const count = imageDropZone ? imageDropZone.files.length : 0;
    $('#imageFilesNote').prop('hidden', count < 2)
        .text(`${count} files, each is printed as a label of its own. The preview shows the first.`);
}

// Content shared with the installed app from the share menu of a phone
// (share_target in the manifest) arrives as ?shared=<token>: a file becomes
// the image, a link a QR code, a text the label text
async function receiveSharedContent() {
    const params = new URLSearchParams(location.search);
    const token = params.get('shared');
    if (!token) return;
    // Reloading the page must not fetch it again
    params.delete('shared');
    history.replaceState(null, '', location.pathname + (params.toString() ? '?' + params : ''));
    const response = await fetch(url_for_shared + '?token=' + encodeURIComponent(token));
    const shared = await response.json();
    if (!response.ok) {
        setStatus({ type: 'preview', status: 'error', message: shared.message });
        return;
    }
    if (shared.data) {
        const bytes = Uint8Array.from(atob(shared.data), c => c.charCodeAt(0));
        useImageFile(new File([bytes], shared.name, { type: shared.type }));
        return;
    }
    $('#label_text').val(shared.url || shared.text).trigger('input');
    if (shared.url) {
        $('#barcode_type').val('QR');
        $('#printTypeQrCode').prop('checked', true).trigger('change');
    } else {
        $('#printTypeText').prop('checked', true).trigger('change');
    }
}

function init2() {
    // Restore settings on load
    set_all_inputs_default();
    restoreAllSettingsFromLocalStorage();

    // Save on change
    $(document).on('change input', 'input, select, textarea', function () {
        // Skip when this was caused by the #lineSelect <select>
        if ($(this).is('#lineSelect')) return;
        setFontSettingsPerLine();
        saveAllSettingsToLocalStorage();
    });

    // Add event handler to update active class on manual click for btn-check radios
    $('input.btn-check[type="radio"]').off('change.btnCheckActive').on('change.btnCheckActive', function () {
        const name = $(this).attr('name');
        $(`input[name="${name}"]`).each(function () {
            $(`label[for="${this.id}"]`).removeClass('active');
        });
        $(`label[for="${this.id}"]`).addClass('active');
    });
    // Reset button
    $('#resetSettings').on('click', resetSettings);

    // Undo button
    $('#undoSettingsBtn').on('click', undoSettings);
    updateUndoButton();

    // Trigger initial preview
    preview();

    receiveSharedContent();
};

// Simple hash function to generate a hash from a string
// Uses a 64-bit FNV-1a style hash implemented with BigInt to reduce collision probability
const generateHash = (string) => {
    const FNV_OFFSET_BASIS = 14695981039346656037n; // 64-bit offset basis
    const FNV_PRIME = 1099511628211n;               // 64-bit FNV prime
    let hash = FNV_OFFSET_BASIS;

    for (let i = 0; i < string.length; i++) {
        hash ^= BigInt(string.charCodeAt(i));
        hash *= FNV_PRIME;
        // Constrain to 64 bits
        hash &= (1n << 64n) - 1n;
    }
    // Return as base36 string
    return hash.toString(36);
};
