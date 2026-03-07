// Auto-fetch coordinates in admin
(function($) {
    $(document).ready(function() {
        console.log('🌍 Serviceable Location Auto-Fetch loaded');

        // Get form fields
        const pincodeField = $('#id_pincode');
        const areaField = $('#id_area_name');
        const cityField = $('#id_city');
        const stateField = $('#id_state');
        const latField = $('#id_latitude');
        const lngField = $('#id_longitude');

        if (!pincodeField.length || !areaField.length) {
            console.log('⚠️ Required fields not found');
            return;
        }

        // Add fetch button next to latitude field
        const fetchBtn = $('<button/>', {
            type: 'button',
            class: 'button',
            text: '🌍 Fetch Coordinates',
            css: {
                'margin-left': '10px',
                'background': '#667EEA',
                'color': 'white',
                'border': 'none',
                'padding': '8px 16px',
                'border-radius': '4px',
                'cursor': 'pointer'
            }
        });

        latField.parent().append(fetchBtn);

        // Add status message div
        const statusDiv = $('<div/>', {
            id: 'coordinate-status',
            css: {
                'margin-top': '10px',
                'padding': '10px',
                'border-radius': '4px',
                'display': 'none'
            }
        });

        fetchBtn.after(statusDiv);

        // Fetch coordinates function
        function fetchCoordinates() {
            const pincode = pincodeField.val();
            const area = areaField.val();
            const city = cityField.val();
            const state = stateField.val();

            if (!pincode || !area) {
                showStatus('⚠️ Please enter both pincode and area name', 'warning');
                return;
            }

            showStatus('🔄 Fetching coordinates...', 'info');
            fetchBtn.prop('disabled', true);

            // Use Nominatim API
            const query = `${area}, ${city}, ${state}, ${pincode}, India`;
            const url = `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(query)}&limit=1`;

            $.ajax({
                url: url,
                method: 'GET',
                success: function(data) {
                    if (data && data.length > 0) {
                        const lat = parseFloat(data[0].lat).toFixed(6);
                        const lng = parseFloat(data[0].lon).toFixed(6);

                        latField.val(lat);
                        lngField.val(lng);

                        showStatus(`✅ Coordinates found: ${lat}, ${lng}`, 'success');
                    } else {
                        showStatus('❌ No coordinates found. Try entering manually.', 'error');
                    }
                },
                error: function() {
                    showStatus('❌ Failed to fetch coordinates. Please try again.', 'error');
                },
                complete: function() {
                    fetchBtn.prop('disabled', false);
                }
            });
        }

        function showStatus(message, type) {
            const colors = {
                'success': '#E8F5E9',
                'error': '#FFEBEE',
                'warning': '#FFF3E0',
                'info': '#E3F2FD'
            };

            statusDiv.css({
                'background': colors[type] || colors.info,
                'display': 'block'
            }).text(message);

            if (type === 'success') {
                setTimeout(() => statusDiv.fadeOut(), 5000);
            }
        }

        // Bind click event
        fetchBtn.on('click', fetchCoordinates);

        // Auto-fetch on pincode/area change (optional)
        let fetchTimeout;
        pincodeField.add(areaField).on('input', function() {
            clearTimeout(fetchTimeout);
            fetchTimeout = setTimeout(() => {
                if (pincodeField.val() && areaField.val() &&
                    !latField.val() && !lngField.val()) {
                    showStatus('💡 Tip: Click "Fetch Coordinates" button or save to auto-fetch', 'info');
                }
            }, 1000);
        });
    });
})(django.jQuery);