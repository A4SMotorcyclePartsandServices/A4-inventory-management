(function () {
    const ERROR_CLASS = 'a4-field-error-msg';
    const ERROR_SELECTOR = `.${ERROR_CLASS}`;
    const INVALID_CLASS = 'a4-field-invalid';
    const LEGACY_ERROR_CLASS = 'field-error-msg';

    function resolveRoot(root) {
        if (!root) {
            return document;
        }
        if (typeof root === 'string') {
            return document.querySelector(root) || document;
        }
        return root;
    }

    function resolveErrorHost(field, options = {}) {
        if (options.errorHost) {
            if (typeof options.errorHost === 'string') {
                return document.querySelector(options.errorHost);
            }
            return options.errorHost;
        }
        if (!field) {
            return null;
        }
        return field.closest?.('[data-validation-field]') || field.parentElement || field;
    }

    function clear(root) {
        const scope = resolveRoot(root);
        scope.querySelectorAll?.(`${ERROR_SELECTOR}, .${LEGACY_ERROR_CLASS}`).forEach((el) => el.remove());
        scope.querySelectorAll?.(`.${INVALID_CLASS}, [data-a4-validation-invalid="1"]`).forEach((el) => {
            el.classList.remove(INVALID_CLASS, 'border-danger', 'is-invalid');
            el.style.boxShadow = '';
            delete el.dataset.a4ValidationInvalid;
            el.removeAttribute('aria-invalid');
        });
    }

    function addError(field, message, options = {}) {
        if (!field) {
            return null;
        }

        const text = String(message || 'This field is required.').trim();
        field.classList.add(INVALID_CLASS, 'border-danger');
        field.dataset.a4ValidationInvalid = '1';
        field.setAttribute('aria-invalid', 'true');
        if (options.highlight !== false) {
            field.style.boxShadow = '0 0 0 2px rgba(193,18,31,0.3)';
        }

        const host = resolveErrorHost(field, options);
        if (!host) {
            return null;
        }

        let err = host.querySelector(`:scope > ${ERROR_SELECTOR}, :scope > .${LEGACY_ERROR_CLASS}`);
        if (!err) {
            err = document.createElement('div');
            err.className = `${ERROR_CLASS} ${LEGACY_ERROR_CLASS}`;
            host.appendChild(err);
        }
        err.textContent = text;
        return err;
    }

    function hasErrors(root) {
        const scope = resolveRoot(root);
        return Boolean(scope.querySelector?.(`${ERROR_SELECTOR}, [data-a4-validation-invalid="1"]`));
    }

    function firstInvalid(root) {
        const scope = resolveRoot(root);
        return scope.querySelector?.('[data-a4-validation-invalid="1"], .border-danger, .is-invalid') || null;
    }

    function focusFirstError(root, options = {}) {
        const target = options.target || firstInvalid(root);
        if (!target) {
            return null;
        }

        target.scrollIntoView({
            behavior: options.behavior || 'smooth',
            block: options.block || 'center',
        });
        if (typeof target.focus === 'function') {
            target.focus({ preventScroll: true });
        }
        return target;
    }

    function showSummary(message, type = 'warning', options = {}) {
        const text = String(message || '').trim();
        if (!text) {
            return null;
        }
        if (window.A4Flash?.show) {
            return window.A4Flash.show(text, type, {
                replace: true,
                ...(options.flashOptions || {}),
            });
        }
        return null;
    }

    function fail(root, message, options = {}) {
        const target = focusFirstError(root, options);
        showSummary(message || 'Please complete the highlighted required fields.', options.type || 'warning', options);
        return target;
    }

    window.A4Validation = {
        clear,
        addError,
        hasErrors,
        firstInvalid,
        focusFirstError,
        showSummary,
        fail,
    };
})();
