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
    var text = $('#labelText').val() || '';
    var lines = text.split(/\r?\n/);
    if (lines.length === 0) lines = [''];

    // Default font settings from the current UI controls
    var currentFont = {
        font: $('#font option:selected').val() || DEFAULT_FONT,
        size: $('#fontSize').val(),
        inverted: $('#fontInverted').is(':checked'),
        todo: $('#fontCheckbox').is(':checked'),
        align: $('input[name=fontAlign]').parent('.active').find('input').val() || 'center',
        line_spacing: $('input[name=lineSpacing]').parent('.active').find('input').val() || '100',
        color: $('input[name=fontColor]').parent('.active').find('input').val() || 'black'
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
    const isSynced = $('#syncFontSettings').is(':checked');
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
        $('#fontSize').val(fs.size);
        // Set alignment
        $('input[name=fontAlign]').prop('checked', false).parent().removeClass('active');
        $('input[name=fontAlign][value="' + fs.align + '"]').prop('checked', true).parent().addClass('active');
        // Set line spacing
        $('input[name=lineSpacing]').prop('checked', false).parent().removeClass('active');
        $('input[name=lineSpacing][value="' + fs.line_spacing + '"]').prop('checked', true).parent().addClass('active');
        // Set font inversion
        $('#fontInverted').prop('checked', fs.inverted);
        // Set font color
        $('input[name=fontColor]').prop('checked', false).parent().removeClass('active');
        $('input[name=fontColor][value="' + fs.color + '"]').prop('checked', true).parent().addClass('active');
        // Set TODO item
        $('#fontCheckbox').prop('checked', fs.todo);
    });

    // When the user changes the caret/selection in the textarea, update #lineSelect and font controls
    $('#labelText').on('click keyup', function (e) {
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
        label_size: $('#labelSize').val(),
        orientation: $('input[name=orientation]:checked').val(),
        margin_top: $('#marginTop').val(),
        margin_bottom: $('#marginBottom').val(),
        margin_left: $('#marginLeft').val(),
        margin_right: $('#marginRight').val(),
        print_type: $('input[name=printType]:checked').val(),
        barcode_type: $('#barcodeType').val(),
        qrcode_size: $('#qrCodeSize').val(),
        qrcode_correction: $('#qrCodeCorrection option:selected').val(),
        image_bw_threshold: $('#imageBwThreshold').val(),
        image_mode: $('input[name=imageMode]:checked').val(),
        image_fit: $('#imageFitCheckbox').is(':checked') ? 1 : 0,
        print_count: $('#printCount').val(),
        log_level: $('#logLevel').val(),
        line_spacing: $('input[name=lineSpacing]:checked').val(),
        cut_once: cut_once ? 1 : 0,
        border_thickness: $('#borderThickness').val(),
        border_roundness: $('#borderRoundness').val(),
        border_distance_x: $('#borderDistanceX').val(),
        border_distance_y: $('#borderDistanceY').val(),
        high_res: $('#highResolutionCheckbox').is(':checked') ? 1 : 0
    }

    if (printer_status['red_support']) {
        data['print_color'] = $('input[name=printColor]:checked').val();
        data['border_color'] = $('input[name=borderColor]:checked').val();
    }

    return data;
}

function get_dpi() {
    return $('#highResolutionCheckbox').is(':checked') ? 600 : 300;
}

function updatePreview(data) {
    $('#previewImg').attr('src', 'data:image/png;base64,' + data);
    var img = $('#previewImg')[0];
    img.onload = function () {
        $('#labelWidth').html((img.naturalWidth / get_dpi() * 2.54).toFixed(1));
        $('#labelHeight').html((img.naturalHeight / get_dpi() * 2.54).toFixed(1));
    };
}

function gen_label(preview = true, cut_once = false) {
    // Check label against installed label in the printer
    updatePrinterStatus();

    // Update font settings for each line
    setFontSettingsPerLine();

    if (preview) {
        // Update preview image based on label size
        if ($('#labelSize option:selected').data('round') == 'True') {
            $('img#previewImg').addClass('roundPreviewImage');
        } else {
            $('img#previewImg').removeClass('roundPreviewImage');
        }

        // Disable irrelevant margin controls
        if ($('input[name=orientation]:checked').val() == 'standard') {
            $('.marginsTopBottom').prop('disabled', false).removeAttr('title');
            $('.marginsLeftRight').prop('disabled', true).prop('title', 'Only relevant if rotated orientation is selected.');
        } else {
            $('.marginsLeftRight').prop('disabled', false).removeAttr('title');
            $('.marginsTopBottom').prop('disabled', true).prop('title', 'Only relevant if standard orientation is selected.');
        }
    }

    // Show or hide image upload box
    if ($('input[name=printType]:checked').val() == 'image') {
        $('#groupLabelImage').show();
    } else {
        $('#groupLabelImage').hide();
    }

    // Update status box
    let type = preview ? 'preview' : 'printing';
    setStatus({ type: type, 'status': 'pending' });

    // Process image upload
    if ($('input[name=printType]:checked').val() == 'image') {
        dropZoneMode = preview ? 'preview' : 'printing';
        imageDropZone.processQueue();
        return;
    }

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
            iconClass = 'float-right fas fa-hourglass-half text-muted';
        } else if (status === 'success') {
            // Success for preview or printing
            if (type === 'preview') {
                html = `<div id="statusBox" class="alert alert-info" role="alert">
                            <i class="fas fa-eye"></i>
                            <span>Preview generated successfully.</span>
                        </div>`;
                iconClass = 'float-right fas fa-check text-success';
            } else {
                html = `<div id="statusBox" class="alert alert-success" role="alert">
                            <i class="fas fa-check"></i>
                            <span>Printing was successful.</span>
                        </div>`;
                iconClass = 'float-right fas fa-print text-success';
            }
        } else if (status === 'error') {
            // Error for preview or printing
            let action = type === 'preview' ? "Preview generation failed" : "Printing failed";
            html = `<div id="statusBox" class="alert alert-warning" role="alert">
                        <i class="fas fa-exclamation-triangle"></i>
                        <span>${action}${extra_info}</span>
                    </div>`;
            iconClass = 'float-right fas fa-exclamation-triangle text-danger';
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
            iconClass = 'float-right fas fa-exclamation-triangle text-danger';
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

var imageDropZone;
Dropzone.options.myAwesomeDropzone = {
    url: function () {
        if (dropZoneMode == 'preview') {
            return url_for_preview + "?return_format=base64";
        } else {
            return url_for_print;
        }
    },
    paramName: "image",
    acceptedFiles: 'image/png,image/jpeg,application/pdf',
    maxFiles: 1,
    addRemoveLinks: true,
    autoProcessQueue: false,
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
};


function toggleQrSettings() {
    var barcodeType = document.getElementById('barcodeType');
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
            const select = document.getElementById('barcodeType');
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
    const printerIcon = document.getElementById('printerIcon');
    const printerModel = document.getElementById('printerModel');
    if (printerModel) {
        printerModel.textContent = printer_status.model || 'Unknown';
    }
    const printerPath = document.getElementById('printerPath');
    if (printerPath) {
        printerPath.textContent = printer_status.path || 'Unknown';
    }
    if ($('#labelSize option:selected').val().includes('red')) {
        $(".red-support").show();
    } else {
        $('#print_color_black').prop('active', true);
        $(".red-support").hide();
    }

    if (printer_status.status_type === 'Offline') {
        printerModel.classList.add('text-muted');
        printerPath.classList.add('text-muted');
        printerIcon.classList.add('text-muted');
        // Append " (offline)" to printer path
        printerPath.textContent += " (offline)";
    } else {
        printerModel.classList.remove('text-muted');
        printerPath.classList.remove('text-muted');
        printerIcon.classList.remove('text-muted');
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
    const labelSizeSelect = document.getElementById('labelSize');
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
    printer_status = data;
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
        const key = this.id.length > 0 ? this.id : this.name;
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
    $('#undoSettingsBtn').find('.undo-counter').text(steps);
    $('#undoSettingsBtn').prop('disabled', steps === 0);
}

function restoreAllSettingsFromLocalStorage() {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return;

    let data;
    try { data = JSON.parse(raw); } catch { return; }
    current_restoring = true;
    $('input, select, textarea').each(function () {
        const key = this.id || this.name;
        if (!(key in data)) return;
        if (this.type === 'checkbox' || this.type === 'radio') {
            $(this).prop('checked', !!data[key]);
            if (this.type === 'radio') {
                if ($(this).val() == data[key]) {
                    $(this).prop('checked', true);
                    $(this).parent().addClass('active');
                } else {
                    $(this).prop('checked', false);
                    $(this).parent().removeClass('active');
                }
            }
        } else {
            if (data[key] !== undefined) {
                $(this).val(data[key]);
            }
        }
    });
    // Restore fontSettingsPerLine if available
    if (data['fontSettingsPerLine'] && window.fontSettingsPerLine) {
        try {
            window.fontSettingsPerLine = JSON.parse(data['fontSettingsPerLine']);
            $('#lineSelect').val(0);
        } catch { }
    }
    // Trigger preview after restore
    setTimeout(() => { preview(); current_restoring = false; }, 100);
}

function exportSettings() {
    const data = localStorage.getItem(LS_KEY) || '{}';
    const blob = new Blob([data], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'labeldesigner_settings.json';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 100);
}

function importSettings() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'application/json';
    input.onchange = function (e) {
        const file = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = function (evt) {
            try {
                localStorage.setItem(LS_KEY, evt.target.result);
                restoreAllSettingsFromLocalStorage();
            } catch { }
        };
        reader.readAsText(file);
    };
    input.click();
}

function resetSettings() {
    if (confirm('Really reset all label settings to default?')) {
        localStorage.removeItem(LS_KEY);
        // Reset font settings
        window.fontSettingsPerLine = {};
        set_all_inputs_default(true);
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
    // Export/Import/Reset buttons
    $('#exportSettings').on('click', exportSettings);
    $('#importSettings').on('click', importSettings);
    $('#resetSettings').on('click', resetSettings);

    // Undo button
    $('#undoSettingsBtn').on('click', undoSettings);
    updateUndoButton();
};
