(function () {
    function newIdempotencyKey() {
        if (window.A4Idempotency && typeof window.A4Idempotency.newKey === 'function') {
            return window.A4Idempotency.newKey();
        }
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID();
        }
        return 'idem-' + Date.now() + '-' + Math.random().toString(16).slice(2, 10);
    }

    function ensureFormKeyInput(form) {
        if (!form) {
            return null;
        }

        let input = form.querySelector('input[name="idempotency_key"]');
        if (!input) {
            input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'idempotency_key';
            form.appendChild(input);
        }
        return input;
    }

    function createFormGuard(form, options) {
        const config = options || {};
        const submitButton = config.submitButton || form?.querySelector('button[type="submit"]') || null;
        const idleHtml = Object.prototype.hasOwnProperty.call(config, 'idleHtml')
            ? config.idleHtml
            : submitButton?.innerHTML || '';
        const loadingHtml = Object.prototype.hasOwnProperty.call(config, 'loadingHtml')
            ? config.loadingHtml
            : idleHtml;

        function isLocked() {
            return form?.dataset.submitting === 'true';
        }

        return {
            begin() {
                if (!form || isLocked()) {
                    return false;
                }

                form.dataset.submitting = 'true';
                const keyInput = ensureFormKeyInput(form);
                if (keyInput) {
                    keyInput.value = keyInput.value || newIdempotencyKey();
                }
                if (submitButton) {
                    submitButton.disabled = true;
                    submitButton.innerHTML = loadingHtml;
                }
                return true;
            },

            reset() {
                if (!form) {
                    return;
                }

                delete form.dataset.submitting;
                const keyInput = ensureFormKeyInput(form);
                if (keyInput) {
                    keyInput.value = '';
                }
                if (submitButton) {
                    submitButton.disabled = false;
                    submitButton.innerHTML = idleHtml;
                }
            },
        };
    }

    window.A4Submit = Object.assign({}, window.A4Submit || {}, {
        createFormGuard,
    });
})();
