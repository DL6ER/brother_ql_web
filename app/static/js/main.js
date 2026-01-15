// Global printer status object to be populated from the API
var printer_status = {
    'errors': [],
    'model': 'Unknown',
    'media_width': 62,
    'media_length': 0,
    'phase_type': 'Unknown',
    'red_support': false
};

const DEFAULT_FONT = 'Droid Serif,Regular';

// Returns an array of font settings for each line of label text.
// Each new line inherits the font settings of the previous line.
var fontSettingsPerLine = [];
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

// Update font controls when a line is selected
$(document).ready(function () {
    $('#lineSelect').on('change', function () {
        var idx = parseInt($(this).val(), 10);
        if (isNaN(idx) || !fontSettingsPerLine || !fontSettingsPerLine[idx]) return;
        var fs = fontSettingsPerLine[idx];
        // Set font
        $('#font').val(fs.font || DEFAULT_FONT);
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
        print_count: parseInt($('#print_count').val(), 10) || 0,
        log_level: $('#log_level').val(),
        cut_once: cut_once ? 1 : 0,
        border_thickness: parseInt($('#border_thickness').val(), 10) || 0,
        border_roundness: parseInt($('#border_roundness').val(), 10) || 0,
        border_distance_x: parseInt($('#border_distance_x').val(), 10) || 0,
        border_distance_y: parseInt($('#border_distance_y').val(), 10) || 0,
        high_res: $('#high_res').is(':checked') ? 1 : 0,
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
    }

    return data;
}

function get_dpi() {
    return $('#high_res').is(':checked') ? 600 : 300;
}

function updatePreview(data) {
    $('#previewImg').attr('src', 'data:image/png;base64,' + data);
    var img = $('#previewImg')[0];
    img.onload = function () {
        $('#labelWidth').html((img.naturalWidth / get_dpi() * 2.54).toFixed(1));
        $('#labelHeight').html((img.naturalHeight / get_dpi() * 2.54).toFixed(1));
    };
}

var lastPreviewData = null;
function gen_label(preview = true, cut_once = false) {
    // Check label against installed label in the printer
    updatePrinterStatus();

    // Update font settings for each line
    setFontSettingsPerLine();

    if (preview) {
        // Update preview image based on label size
        if ($('#label_size option:selected').data('round') == 'True') {
            $('img#previewImg').addClass('roundPreviewImage');
        } else {
            $('img#previewImg').removeClass('roundPreviewImage');
        }
    }

    // Show or hide image upload box
    if ($('input[name=print_type]:checked').val() == 'image') {
        $('#groupLabelImage').show();
    } else {
        $('#groupLabelImage').hide();
    }

    // Update status box
    let type = preview ? 'preview' : 'printing';
    setStatus({ type: type, 'status': 'pending' });

    // Process image upload
    if ($('input[name=print_type]:checked').val() == 'image') {
        dropZoneMode = preview ? 'preview' : 'printing';
        imageDropZone.processQueue();
        return;
    }

    // Get data and compare to the last preview generation, return if nothing
    // has changed
    const data = formData(cut_once);
    const dataJson = JSON.stringify(data);
    if (preview && dataJson === lastPreviewData) {
        console.debug("No changes detected, not generating new preview.");
        return;
    }
    // Update lastPreviewData
    lastPreviewData = dataJson;

    // Send printing request
    const url = preview ? (url_for_preview + '?return_format=base64') : url_for_print;
    $.ajax({
        type: 'POST',
        url: url,
        contentType: 'application/x-www-form-urlencoded; charset=UTF-8',
        data: formData(cut_once),
        success: function (data) {
            // Check if response is JSON and has a key "success"
            const status = typeof data === "object" &&
                data !== null &&
                "success" in data &&
                data.success === false ?
                'error' : 'success';
            setStatus({ type: type, 'status': status });
            updatePreview(data);
        },
        error: function (xhr, _status, error) {
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
    let extra_info = message ? ':<br />' + message : '';
    if (errors.length > 0) {
        extra_info += '<br />' + errors.join('<br />');
    }

    // Default: clear status
    let html = '';
    let iconClass = '';

    if (type === 'preview' || type === 'printing') {
        if (status === 'pending') {
            // Busy preparing preview or printing
            let action = type === 'printing' ? "Printing" : "Generating preview";
            html = `<div id="statusBox" class="alert alert-info" role="alert">
                        <i class="fas fa-hourglass-half"></i>
                        <span>${action}...</span>
                    </div>`;
            iconClass = 'float-end fas fa-hourglass-half text-muted';
        } else if (status === 'success') {
            // Success for preview or printing
            if (type === 'preview') {
                html = `<div id="statusBox" class="alert alert-info" role="alert">
                            <i class="fas fa-eye"></i>
                            <span>Preview generated successfully.</span>
                        </div>`;
                iconClass = 'float-end fas fa-check text-success';
            } else {
                html = `<div id="statusBox" class="alert alert-success" role="alert">
                            <i class="fas fa-check"></i>
                            <span>Printing was successful.</span>
                        </div>`;
                iconClass = 'float-end fas fa-print text-success';
            }
        } else if (status === 'error') {
            // Error for preview or printing
            let action = type === 'preview' ? "Preview generation failed" : "Printing failed";
            html = `<div id="statusBox" class="alert alert-warning" role="alert">
                        <i class="fas fa-exclamation-triangle"></i>
                        <span>${action}${extra_info}</span>
                    </div>`;
            iconClass = 'float-end fas fa-exclamation-triangle text-danger';
        } else {
            // Unknown status, clear
            html = "";
            iconClass = "";
        }
    } else if (type === 'status') {
        if (status === 'error') {
            let action = "Error";
            html = `<div id="statusBox" class="alert alert-warning" role="alert">
                        <i class="fas fa-exclamation-triangle"></i>
                        <span>${action}${extra_info}</span>
                    </div>`;
            iconClass = 'float-end fas fa-exclamation-triangle text-danger';
        } else {
            html = "";
            iconClass = "";
        }
    } else {
        html = "";
        iconClass = "";
    }

    let elem = null;
    if (type === 'status') {
        elem = $('#printerStatusPanel');
    } else {
        elem = $('#statusPanel');
    }
    elem.html(html);
    if (html.length > 0)
        elem.show();
    else
        elem.hide();

    $('#statusIcon').removeClass().addClass(iconClass);
    $('#printButton').prop('disabled', false);
    $('#dropdownPrintButton').prop('disabled', false);
}

let myDropzone = new Dropzone("#image-dropzone", {
    url: function () {
        if (dropZoneMode == 'preview') {
            return url_for_preview + "?return_format=base64";
        } else {
            return url_for_print;
        }
    },
    paramName: "image", // The name that will be used to transfer the file
    acceptedFiles: 'image/*,application/pdf',
    maxFiles: 1,
    addRemoveLinks: true,
    autoProcessQueue: false,
    thumbnailMethod: 'contain',
    init: function () {
        imageDropZone = this;

        this.on("addedfile", function () {
            if (this.files[1] != null) {
                this.removeFile(this.files[0]);
            }
        });
    },

    sending: function (file, xhr, data) {
        // append all parameters to the request
        let fd = formData(false);

        $.each(fd, function (key, value) {
            data.append(key, value);
        });
    },

    success: function (file, response) {
        // If preview or print was successfull update the previewpane or print status
        // Check if response is JSON and has a key "success"
        const status = typeof response === "object" &&
            response !== null &&
            "success" in response &&
            response.success === false ?
            'error' : 'success';
        setStatus({ type: dropZoneMode, status: status });
        if (dropZoneMode == 'preview') {
            updatePreview(response);
        }
        file.status = Dropzone.QUEUED;
    },

    accept: function (file, done) {
        // If a valid file was added, perform the preview
        done();
        preview();
    },

    removedfile: function (file) {
        file.previewElement.remove();
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

function updatePrinterStatus() {
    if ($('#label_size option:selected').val().includes('red')) {
        $(".red-support").show();
    } else {
        $('#print_color_black').prop('active', true);
        $(".red-support").hide();
    }

    const labelSizeX = document.getElementById('label-width');
    const labelSizeY = document.getElementById('label-height');
    if (labelSizeX && labelSizeY) {
        labelSizeX.textContent = printer_status.media_width ?? "???" + " mm";
        if (printer_status.media_length > 0) {
            labelSizeY.textContent = printer_status.media_length + " mm";
        }
        else if (printer_status.media_type === 'Continuous length tape') {
            labelSizeY.textContent = "endless";
        }
        else {
            labelSizeY.textContent = "???";
        }
    }

    // Check for label size mismatch compared to data-x property of select
    const labelSizeSelect = document.getElementById('label_size');
    if (labelSizeSelect) {
        const selectedOption = labelSizeSelect.options[labelSizeSelect.selectedIndex];
        const dataX = selectedOption.getAttribute('data-x');
        const dataY = selectedOption.getAttribute('data-y');
        if (printer_status.media_width !== null && (printer_status.media_width !== parseInt(dataX) || printer_status.media_length !== parseInt(dataY))) {
            labelMismatch.style.display = '';
            labelMismatchIcon.style.display = '';
        } else {
            labelMismatch.style.display = 'none';
            labelMismatchIcon.style.display = 'none';
        }
    }

    if (printer_status.errors && printer_status.errors.length > 0) {
        setStatus({ type: 'status', status: 'error', errors: printer_status.errors });
    }
    else {
        // Clear printer errors
        setStatus({ type: 'status', status: 'success' });
    }
}

async function getPrinterStatus() {
    const response = await fetch(url_for_get_printer_status);
    const data = await response.json();
    // If server returned multiple printers, populate the select and pick the chosen one
    if (data && Array.isArray(data.printers)) {
        const select = document.getElementById('printer');
        if (select) {
            // remember current selection
            const cur = select.value;
            select.innerHTML = '';
            data.printers.forEach((p) => {
                const opt = document.createElement('option');
                opt.value = p.path || p.path;
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
    // Save fontSettingsPerLine if available
    if (window.fontSettingsPerLine) {
        data['fontSettingsPerLine'] = JSON.stringify(window.fontSettingsPerLine);
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
    $('#undoCounter').text(steps);
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
        } else {
            this.value = data[key];
            console.log(key + ": " + data[key]);
        }
    });

    // Restore fontSettingsPerLine if available
    if (data['fontSettingsPerLine'] && window.fontSettingsPerLine) {
        try {
            window.fontSettingsPerLine = JSON.parse(data['fontSettingsPerLine']);
            console.log(window.fontSettingsPerLine);
            $('#lineSelect').val(0);
            preview();
        } catch { }
    }
    // Trigger preview after restore
    setTimeout(() => { preview(); current_restoring = false; }, 100);
}

// --- Repository UI functions ---------------------------------------------------------
function openRepositoryModal() {
    // show modal and load list
    const modalEl = document.getElementById('repoModal');
    if (!modalEl) return;
    const modal = new bootstrap.Modal(modalEl);
    modal.show();
    loadRepositoryList();
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

function renderRepoList(files) {
    const body = $('#repoListBody');
    if (!files || files.length === 0) {
        body.html('<p class="text-muted">No labels in repository.</p>');
        return;
    }
    const table = $('<div class="list-group"></div>');
    files.forEach(f => {
        const item = $(
            ` <div class="list-group-item d-flex justify-content-between align-items-center">
                    <div class="d-flex gap-3 align-items-center">
                        <img class="repo-thumb" src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAAAQCAYAAAB49c9kAAAAE0lEQVR42mNgGAWjYBSMglEwCQAAP1gF8bQqT9kAAAAASUVORK5CYII=" />
                        <div>
                            <strong class="repo-name">${f.name}</strong><br/>
                            <small class="text-muted">${new Date(f.mtime * 1000).toLocaleString()} — ${f.size} bytes</small><br/>
                            <small class="text-muted">Label size: ${f.label_size ? f.label_size : 'unknown'}</small>
                        </div>
                    </div>
                    <div class="btn-group">
                        <button class="btn btn-sm btn-outline-success repo-load" data-name="${f.name}">Load</button>
                        <button class="btn btn-sm btn-outline-primary repo-print" data-name="${f.name}">Print</button>
                        <button class="btn btn-sm btn-outline-danger repo-delete" data-name="${f.name}">Delete</button>
                    </div>
                </div>`
        );
        table.append(item);
        // fetch thumbnail asynchronously (do not override main preview)
        const img = item.find('.repo-thumb')[0];
        if (img) {
            repoFetchThumbnail(f.name, img);
        }
    });
    body.html(table);

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
        if (!confirm('Delete ' + name + '?')) return;
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

    // If an image is present in Dropzone, encode it as base64 and include
    // it in the JSON payload so the server can accept pure JSON saves.
    function sendJsonPayload(p) {
        fetch(url_for_repo_save, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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
                    const comma = dataUrl.indexOf(',');
                    const header = dataUrl.substring(5, comma); // e.g. image/jpeg;base64
                    const mime = header.split(';')[0];
                    const b64 = dataUrl.substring(comma + 1);
                    payload['image_data'] = b64;
                    payload['image_mime'] = mime;
                    payload['image_name'] = f.name || 'image';
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
            // close modal
            const modalEl = document.getElementById('repoModal');
            bootstrap.Modal.getInstance(modalEl).hide();
        }).catch(e => {
            console.error(e);
            alert('Failed to load label');
        });
}

function repoDelete(name) {
    fetch(url_for_repo_delete + '?name=' + encodeURIComponent(name), { method: 'POST' })
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

    fetch(url_for_repo_print, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' }, body: body })
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

// Wire modal button on DOM ready
$(document).ready(function () {
    $('#openRepoBtn').off('click').on('click', openRepositoryModal);
    $('#repoSaveBtn').off('click').on('click', repoSaveCurrent);
});

function resetSettings() {
    if (confirm('Really reset all label settings to default?')) {
        localStorage.removeItem(LS_KEY);
        // Reset font settings
        window.fontSettingsPerLine = {};
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
};
