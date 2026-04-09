(function () {
    function newIdempotencyKey() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID();
        }
        return 'idem-' + Date.now() + '-' + Math.random().toString(16).slice(2, 10);
    }

    function createButtonGuard(button, options) {
        const config = options || {};
        const idleHtml = Object.prototype.hasOwnProperty.call(config, 'idleHtml')
            ? config.idleHtml
            : button?.innerHTML || '';
        const loadingHtml = Object.prototype.hasOwnProperty.call(config, 'loadingHtml')
            ? config.loadingHtml
            : idleHtml;

        function isLocked() {
            return button?.dataset.submitting === 'true';
        }

        return {
            begin() {
                if (!button || isLocked()) {
                    return false;
                }

                button.dataset.submitting = 'true';
                button.dataset.idempotencyKey = button.dataset.idempotencyKey || newIdempotencyKey();
                button.disabled = true;
                button.innerHTML = loadingHtml;
                return true;
            },

            getIdempotencyKey() {
                if (!button) {
                    return newIdempotencyKey();
                }
                button.dataset.idempotencyKey = button.dataset.idempotencyKey || newIdempotencyKey();
                return button.dataset.idempotencyKey;
            },

            buildHeaders(extraHeaders) {
                const headers = Object.assign({}, extraHeaders || {});
                headers['X-Idempotency-Key'] = this.getIdempotencyKey();
                return headers;
            },

            succeed() {
                if (!button) {
                    return;
                }
                delete button.dataset.submitting;
                delete button.dataset.idempotencyKey;
                button.disabled = true;
            },

            reset() {
                if (!button) {
                    return;
                }
                delete button.dataset.submitting;
                delete button.dataset.idempotencyKey;
                button.disabled = false;
                button.innerHTML = idleHtml;
            },
        };
    }

    window.A4Idempotency = {
        newKey: newIdempotencyKey,
    };

    window.A4Submit = {
        createButtonGuard,
    };
})();
