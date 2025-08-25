
// Returns an array of font settings for each line of label text.
// Each new line inherits the font settings of the previous line.
var fontSettingsPerLine = [];
function setFontSettingsPerLine() {
    var text = $('#labelText').val() || '';
    var lines = text.split(/\r?\n/);
    if (lines.length === 0) lines = [''];

    // Default font settings from the current UI controls
    var currentFont = {
        font_family:  $('#fontFamily option:selected').text(),
        font_style:   $('#fontStyle option:selected').text(),
        font_size:    $('#fontSize').val(),
        align:        $('input[name=fontAlign]:checked').val(),
        line_spacing: $('input[name=lineSpacing]:checked').val()
    };

    // Create lines in the <option> with id #lineSelect
    var lineSelect = $('#lineSelect');
    // Get currently selected line number
    var selectedLine = lineSelect.val();
    // Recreate options with possibly updated text
    lineSelect.empty();
    $.each(lines, function(index, line) {
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
                fontSettingsPerLine.push(Object.assign({}, fontSettingsPerLine[i-1]));
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
$(document).ready(function() {
    $('#lineSelect').on('change', function() {
        var idx = parseInt($(this).val(), 10);
        if (isNaN(idx) || !fontSettingsPerLine || !fontSettingsPerLine[idx]) return;
        var fs = fontSettingsPerLine[idx];
        // Set font family
        $('#fontFamily').val(fs.font_family);
        // Set font style
        updateStyles(fs.font_style);
        // Set font size
        $('#fontSize').val(fs.font_size);
        // Set alignment
        $('input[name=fontAlign][value="' + fs.align + '"]').prop('checked', true).parent().addClass('active').siblings().removeClass('active');
        // Set line spacing
        $('input[name=lineSpacing][value="' + fs.line_spacing + '"]').prop('checked', true).parent().addClass('active').siblings().removeClass('active');
    });

    // When the user changes the caret/selection in the textarea, update #lineSelect and font controls
    $('#labelText').on('click keyup', function(e) {
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

function formData(cut_once) {
    data = {
        text:               fontSettingsPerLine,
        orientation:        $('input[name=orientation]:checked').val(),
        margin_top:         $('#marginTop').val(),
        margin_bottom:      $('#marginBottom').val(),
        margin_left:        $('#marginLeft').val(),
        margin_right:       $('#marginRight').val(),
        print_type:         $('input[name=printType]:checked').val(),
        qrcode_size:        $('#qrCodeSize').val(),
        qrcode_correction:  $('#qrCodeCorrection option:selected').val(),
        image_bw_threshold: $('#imageBwThreshold').val(),
        image_mode:         $('input[name=imageMode]:checked').val(),
        image_fit:          $('#imageFitCheckbox').is(':checked') ? 1 : 0,
        print_count:        $('#printCount').val(),
        log_level:          $('#logLevel').val(),
        line_spacing:       $('input[name=lineSpacing]:checked').val(),
        cut_once:           cut_once ? 1 : 0,
        border_thickness:   $('#borderThickness').val(),
        border_roundness:   $('#borderRoundness').val(),
        border_distance_x:  $('#borderDistanceX').val(),
        border_distance_y:  $('#borderDistanceY').val(),
    }

    if (red_support) {
        data['print_color'] = $('input[name=printColor]:checked').val();
        data['border_color'] = $('input[name=borderColor]:checked').val();
    }

    return data;
}

function updatePreview(data) {
    $('#previewImg').attr('src', 'data:image/png;base64,' + data);
    var img = $('#previewImg')[0];
    img.onload = function() {
        $('#labelWidth').html( (img.naturalWidth /default_dpi*2.54).toFixed(1));
        $('#labelHeight').html((img.naturalHeight/default_dpi*2.54).toFixed(1));
    };
}

function updateStyles(style = null) {
    font_familiy = $('#fontFamily option:selected').text()

    $.ajax({
        type:        'POST',
        url:         url_for_get_font_styles,
        contentType: 'application/x-www-form-urlencoded; charset=UTF-8',
        data:        {font: font_familiy},
        success: function( data ) {
            var styleSelect = $('#fontStyle');
            styleSelect.empty();
            $.each(data, function (key, value) {
                styleSelect.append($("<option></option>")
                    .attr("value", key).text(key));
                if (style) {
                    styleSelect.val(style);
                } else if ('Book,Regular'.includes(key)) {
                    styleSelect.val(key);
                }
            });
            styleSelect.trigger("change");
        }
    });
}

function preview() {
    if ($('#labelSize option:selected').data('round') == 'True') {
        $('img#previewImg').addClass('roundPreviewImage');
    } else {
        $('img#previewImg').removeClass('roundPreviewImage');
    }

    if ($('input[name=orientation]:checked').val() == 'standard') {
        $('.marginsTopBottom').prop('disabled', false).removeAttr('title');
        $('.marginsLeftRight').prop('disabled', true).prop('title', 'Only relevant if rotated orientation is selected.');
    } else {
        $('.marginsTopBottom').prop('disabled', true).prop('title', 'Only relevant if standard orientation is selected.');
        $('.marginsLeftRight').prop('disabled', false).removeAttr('title');
    }

    if (red_support) {
        if ($('#labelSize option:selected').val().includes('red')) {
            $('#print_color_black').removeClass('disabled');
            $('#print_color_red').removeClass('disabled');
            $('#image_mode_red_and_black').removeClass('disabled');
            $('#image_mode_colored').removeClass('disabled');
        } else {
            $('#print_color_black').addClass('disabled').prop('active', true);
            $('#print_color_red').addClass('disabled');
            $('#image_mode_red_and_black').addClass('disabled');
            $('#image_mode_colored').addClass('disabled');
        }
    }

    if($('input[name=printType]:checked').val() == 'image') {
        $('#groupLabelText').hide();
        $('#groupLabelImage').show()
    } else {
        $('#groupLabelText').show();
        $('#groupLabelImage').hide();
    }

    if($('input[name=printType]:checked').val() == 'image') {
        dropZoneMode = 'preview';
        imageDropZone.processQueue();
        return;
    }

    setFontSettingsPerLine();

    $.ajax({
        type:        'POST',
        url:         url_for_get_preview + '?return_format=base64',
        contentType: 'application/x-www-form-urlencoded; charset=UTF-8',
        data:        formData(),
        success: function( data ) {
            updatePreview(data);
        }
    });
}

function setStatus(data) {
    if (data['success']) {
        $('#statusPanel').html('<div id="statusBox" class="alert alert-success" role="alert"><i class="fas fa-check"></i><span>Printing was successful.</span></div>');
    } else {
        $('#statusPanel').html('<div id="statusBox" class="alert alert-warning" role="alert"><i class="fas fa-exclamation-triangle"></i><span>Printing was unsuccessful:<br />'+data['message']+'</span></div>');
    }
    $('#printButton').prop('disabled', false);
    $('#dropdownPrintButton').prop('disabled', false);
}

function print(cut_once = false) {
    $('#printButton').prop('disabled', true);
    $('#dropdownPrintButton').prop('disabled', true);
    $('#statusPanel').html('<div id="statusBox" class="alert alert-info" role="alert"><i class="fas fa-hourglass-half"></i><span>Processing print request...</span></div>');

    if($('input[name=printType]:checked').val() == 'image') {
        dropZoneMode = 'print';
        imageDropZone.processQueue();
        return;
    }

    $.ajax({
        type:     'POST',
        dataType: 'json',
        data:     formData(cut_once),
        url:      url_for_print_text,
        success:  setStatus,
        error:    setStatus
    });
}


var imageDropZone;
Dropzone.options.myAwesomeDropzone = {
    url: function() {
        if (dropZoneMode == 'preview') {
            return url_for_get_preview + "?return_format=base64";
        } else {
            return url_for_print_text;
        }
    },
    paramName: "image",
    acceptedFiles: 'image/png,image/jpeg,application/pdf',
    maxFiles: 1,
    addRemoveLinks: true,
    autoProcessQueue: false,
    init: function() {
        imageDropZone = this;

        this.on("addedfile", function() {
            if (this.files[1] != null) {
                this.removeFile(this.files[0]);
            }
        });
    },

    sending: function(file, xhr, data) {
        // append all parameters to the request
        fd = formData(false);

        $.each(fd, function(key, value){
            data.append(key, value);
        });
    },

    success: function(file, response) {
        // If preview or print was successfull update the previewpane or print status
        if (dropZoneMode == 'preview') {
            updatePreview(response);
        } else {
            setStatus(response);
        }
        file.status = Dropzone.QUEUED;
    },

    accept: function(file, done) {
        // If a valid file was added, perform the preview
        done();
        preview();
    },

    removedfile: function(file) {
        file.previewElement.remove();
        preview();
        // Insert a dummy image
        updatePreview('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=');
    }
};

window.onload = function() {
    updateStyles(); // this also triggers preview()
};
