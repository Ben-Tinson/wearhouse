document.addEventListener('DOMContentLoaded', function () {
    const lookupBlocks = document.querySelectorAll('.sneaker-lookup');
    if (!lookupBlocks.length) return;

    function looksLikeSku(value) {
        if (!value) return false;
        if (value.includes(' ')) return false;
        if (!/[0-9]/.test(value)) return false;
        return /^[A-Za-z0-9-]+$/.test(value) && value.length >= 4;
    }

    function applySneakerToForm(form, sneaker) {
        if (!form || !sneaker) return;

        // Field hooks: name attributes align with WTForms fields.
        const brandInput = form.querySelector('[name="brand"]');
        const modelInput = form.querySelector('[name="model"]');
        const skuInput = form.querySelector('[name="sku"]');
        const colorwayInput = form.querySelector('[name="colorway"]');
        const imageUrlInput = form.querySelector('[name="sneaker_image_url"]');

        if (brandInput && sneaker.brand) brandInput.value = sneaker.brand;
        if (modelInput && sneaker.model_name) modelInput.value = sneaker.model_name;
        if (skuInput && sneaker.sku) skuInput.value = sneaker.sku;
        if (colorwayInput && sneaker.colorway) colorwayInput.value = sneaker.colorway;
        if (imageUrlInput && sneaker.image_url) imageUrlInput.value = sneaker.image_url;

        const urlRadio = form.querySelector('[name="image_option"][value="url"]');
        if (urlRadio) {
            urlRadio.checked = true;
            urlRadio.dispatchEvent(new Event('change', { bubbles: true }));
        }

        const previewArea = document.getElementById('newImagePreviewArea');
        const previewImage = document.getElementById('newSneakerImagePreview');
        if (previewArea && previewImage && sneaker.image_url) {
            previewImage.src = sneaker.image_url;
            previewArea.style.display = 'block';
        }
    }

    function renderCandidates(container, form, candidates) {
        container.innerHTML = '';
        if (!candidates.length) {
            container.innerHTML = '<p class="text-muted small mb-0">No matches found.</p>';
            return;
        }

        const list = document.createElement('div');
        list.className = 'list-group';

        candidates.forEach(candidate => {
            const item = document.createElement('div');
            item.className = 'list-group-item d-flex align-items-center gap-3';

            const thumb = document.createElement('img');
            thumb.alt = candidate.model_name || candidate.sku || 'Sneaker';
            thumb.src = candidate.image_url || '';
            thumb.style.width = '48px';
            thumb.style.height = '48px';
            thumb.style.objectFit = 'contain';
            thumb.style.backgroundColor = '#f8f9fa';
            if (!candidate.image_url) {
                thumb.style.display = 'none';
            }

            const details = document.createElement('div');
            details.className = 'flex-grow-1';

            const title = document.createElement('div');
            title.className = 'fw-semibold';
            title.textContent = `${candidate.brand || ''} ${candidate.model_name || ''}`.trim();

            const meta = document.createElement('div');
            meta.className = 'small text-muted';
            meta.textContent = [candidate.sku, candidate.colorway].filter(Boolean).join(' • ');

            details.appendChild(title);
            if (meta.textContent) details.appendChild(meta);

            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-sm btn-outline-primary';
            button.textContent = 'Use this';
            button.addEventListener('click', () => {
                applySneakerToForm(form, candidate);
                container.innerHTML = '';
            });

            item.appendChild(thumb);
            item.appendChild(details);
            item.appendChild(button);
            list.appendChild(item);
        });

        container.appendChild(list);
    }

    lookupBlocks.forEach(block => {
        const input = block.querySelector('.sneaker-lookup-input');
        const button = block.querySelector('.sneaker-lookup-btn');
        const results = block.querySelector('.sneaker-lookup-results');
        const targetFormSelector = block.getAttribute('data-target-form');
        const form = targetFormSelector ? document.querySelector(targetFormSelector) : null;
        let debounceTimer = null;
        let activeController = null;

        if (!input || !button || !results || !form) return;

        const runLookup = (force = false) => {
            const query = input.value.trim();
            if (!query) {
                results.innerHTML = '<p class="text-muted small mb-0">Enter a sneaker name or SKU.</p>';
                return;
            }
            if (query.length < 3) {
                results.innerHTML = '<p class="text-muted small mb-0">Enter at least 3 characters.</p>';
                return;
            }

            results.innerHTML = '<div class="spinner-border spinner-border-sm"></div>';

            if (activeController) {
                activeController.abort();
            }
            activeController = new AbortController();

            const forceRefresh = force ? '&force_refresh=1' : '';
            fetch(`/api/sneaker-lookup?q=${encodeURIComponent(query)}&limit=5${forceRefresh}`, {
                signal: activeController.signal
            })
                .then(response => response.json().then(data => ({ ok: response.ok, data })))
                .then(({ ok, data }) => {
                    if (!ok) {
                        results.innerHTML = `<p class="text-danger small mb-0">${data.message || 'Lookup failed.'}</p>`;
                        return;
                    }
                    if (data.mode === 'single' && data.sneaker) {
                        applySneakerToForm(form, data.sneaker);
                        results.innerHTML = '<p class="text-success small mb-0">Autofilled from lookup.</p>';
                        return;
                    }
                    if (data.mode === 'pick') {
                        renderCandidates(results, form, data.candidates || []);
                        return;
                    }
                    results.innerHTML = '<p class="text-muted small mb-0">No matches found.</p>';
                })
                .catch(error => {
                    if (error.name === 'AbortError') return;
                    results.innerHTML = '<p class="text-danger small mb-0">Lookup failed. Please try again.</p>';
                });
        };

        button.addEventListener('click', runLookup);
        input.addEventListener('keydown', event => {
            if (event.key === 'Enter') {
                event.preventDefault();
                runLookup();
            }
        });
        input.addEventListener('input', () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(runLookup, 500);
        });
    });
});
