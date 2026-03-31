
function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

let initialServicesHTML = "";
document.addEventListener('DOMContentLoaded', () => {
    const servicesBody = document.getElementById('servicesBody');
    if (servicesBody) {
        initialServicesHTML = servicesBody.innerHTML;
        syncCategoryFields();
        initializeBundleForm();
    }
});

const categorySelect = document.getElementById('categorySelect');
const newCategoryInput = document.getElementById('newCategoryInput');

function syncCategoryFields() {
    const categorySelect = document.getElementById('categorySelect');
    const newCategoryInput = document.getElementById('newCategoryInput');
    if (!categorySelect || !newCategoryInput) return;

    if (categorySelect.value === "__OTHER__") {
        // ENABLED STATE
        newCategoryInput.disabled = false;
        newCategoryInput.required = true; // Force validation
        newCategoryInput.placeholder = "Type new category name...";
        newCategoryInput.focus(); // Quality of Life: auto-focus for the user
    } else {
        // DISABLED STATE
        newCategoryInput.value = ""; 
        newCategoryInput.disabled = true;
        newCategoryInput.required = false; // Remove validation
        newCategoryInput.placeholder = "Select '-- New Category --' to type";
    }
}

if (categorySelect) {
    categorySelect.addEventListener('change', syncCategoryFields);
}

let searchTimeout;
function filterServices() {
    const query = document.getElementById('serviceSearch').value.trim();
    const tbody = document.getElementById('servicesBody');

    // FIX: If the search box is empty, restore the original 20 items and stop
    if (query.length === 0) {
        tbody.innerHTML = initialServicesHTML;
        return;
    }

    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
        // Optional: don't search for just 1 character to save server resources
        if (query.length < 1) return; 

        fetch(`/api/search/services?q=${encodeURIComponent(query)}&include_inactive=1`)
            .then(response => response.json())
            .then(data => {
                tbody.innerHTML = ''; 

                if (data.services.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="3" class="text-center text-muted">No services found.</td></tr>';
                    return;
                }

                data.services.forEach(svc => {
                    const row = document.createElement('tr');
                    row.className = 'service-row';

                    const nameCell = document.createElement('td');
                    nameCell.className = 'fw-bold service-name';
                    nameCell.textContent = svc.name || '';

                    const categoryCell = document.createElement('td');
                    const categoryBadge = document.createElement('span');
                    categoryBadge.className = 'badge bg-dark border border-secondary text-info';
                    categoryBadge.textContent = svc.category || '';
                    categoryCell.appendChild(categoryBadge);

                    const toggleCell = document.createElement('td');
                    toggleCell.className = 'text-center';
                    const form = document.createElement('form');
                    form.action = `/services/toggle/${svc.id}`;
                    form.method = 'POST';
                    const button = document.createElement('button');
                    button.type = 'submit';
                    button.className = `toggle-btn ${svc.is_active === 1 ? 'text-success' : 'text-muted'}`;
                    button.innerHTML = svc.is_active === 1
                        ? '<i class="bi bi-toggle-on fs-3"></i><small class="d-block">ACTIVE</small>'
                        : '<i class="bi bi-toggle-off fs-3"></i><small class="d-block">DISABLED</small>';
                    form.appendChild(button);
                    toggleCell.appendChild(form);

                    row.appendChild(nameCell);
                    row.appendChild(categoryCell);
                    row.appendChild(toggleCell);
                    tbody.appendChild(row);
                });
            });
    }, 300);
}

function initializeBundleForm() {
    if (!document.getElementById('bundle-create-form')) return;

    document.getElementById('add-bundle-variant-btn').addEventListener('click', () => addBundleVariantRow());
    document.getElementById('add-bundle-service-btn').addEventListener('click', () => addBundleServiceRow());
    document.getElementById('add-bundle-item-btn').addEventListener('click', () => addBundleItemRow());
    document.getElementById('bundle-create-form').addEventListener('submit', prepareBundleSubmission);
    document.getElementById('edit-add-bundle-variant-btn')?.addEventListener('click', () => addEditBundleVariantRow());
    document.getElementById('edit-add-bundle-service-btn')?.addEventListener('click', () => addEditBundleServiceRow());
    document.getElementById('edit-add-bundle-item-btn')?.addEventListener('click', () => addEditBundleItemRow());
    document.getElementById('edit-bundle-form')?.addEventListener('submit', prepareEditBundleSubmission);

    addBundleVariantRow({ variant_name: '', sale_price: '' });
    addBundleServiceRow();
    addBundleItemRow();
}

function resetEditBundleDynamicRows() {
    const variantsWrap = document.getElementById('edit-bundle-variants-wrap');
    const servicesWrap = document.getElementById('edit-bundle-services-wrap');
    const itemsWrap = document.getElementById('edit-bundle-items-wrap');
    if (variantsWrap) variantsWrap.innerHTML = '';
    if (servicesWrap) servicesWrap.innerHTML = '';
    if (itemsWrap) itemsWrap.innerHTML = '';
}

function addBundleVariantRowTo(wrapId, initial = {}) {
    const wrap = document.getElementById(wrapId);
    if (!wrap) return;

    const row = document.createElement('div');
    row.className = 'bundle-variant-row border border-secondary rounded p-2 mb-2';
    row.innerHTML = `
        <div class="bundle-create-variant-stack">
            <div>
                <label class="form-label text-white-50 mb-1" style="font-size:0.72rem;">Subcategory Name</label>
                <input type="text" class="form-control bg-dark text-white border-secondary bundle-variant-name"
                    placeholder="e.g. 110-115 cc, 125 cc, 150-155 cc" value="${escapeHtml(initial.variant_name || '')}">
            </div>
            <div class="bundle-create-line-item">
                <div>
                    <label class="form-label text-white-50 mb-1" style="font-size:0.72rem;">Item Value</label>
                    <div class="input-group">
                        <span class="input-group-text bg-dark border-secondary text-warning">P</span>
                        <input type="number" min="0" step="0.01" class="form-control bg-dark text-white border-secondary bundle-item-value-input"
                            placeholder="0.00" value="${escapeHtml(String(initial.item_value_reference || ''))}" readonly>
                    </div>
                </div>
                <div>
                    <label class="form-label text-white-50 mb-1" style="font-size:0.72rem;">Shop Share</label>
                    <div class="input-group">
                        <span class="input-group-text bg-dark border-secondary text-warning">P</span>
                        <input type="number" min="0" step="0.01" class="form-control bg-dark text-white border-secondary bundle-shop-share-input"
                            placeholder="0.00" value="${escapeHtml(String(initial.shop_share || ''))}">
                    </div>
                </div>
            </div>
            <div>
                <label class="form-label text-white-50 mb-1" style="font-size:0.72rem;">Mechanic Share</label>
                <div class="input-group">
                    <span class="input-group-text bg-dark border-secondary text-warning">P</span>
                    <input type="number" min="0" step="0.01" class="form-control bg-dark text-white border-secondary bundle-mechanic-share-input"
                        placeholder="0.00" value="${escapeHtml(String(initial.mechanic_share || ''))}">
                </div>
            </div>
            <div class="bundle-create-total-row">
                <div>
                <label class="form-label text-white-50 mb-1" style="font-size:0.72rem;">Price for This Subcategory</label>
                <div class="input-group">
                    <span class="input-group-text bg-dark border-secondary text-info">P</span>
                    <input type="text" class="form-control bg-dark text-white border-secondary bundle-variant-total-price"
                        value="${escapeHtml(String(initial.sale_price || '0.00'))}" readonly>
                </div>
                </div>
                <button type="button" class="btn btn-sm btn-outline-danger bundle-remove-btn-inline" onclick="removeBundleRow(this)">
                    <i class="bi bi-x-lg"></i>
                </button>
            </div>
        </div>
    `;
    wrap.appendChild(row);
    recalculateBundleItemValues();
}

function addBundleVariantRow(initial = {}) {
    addBundleVariantRowTo('bundle-variants-wrap', initial);
}

function addEditBundleVariantRow(initial = {}) {
    const wrap = document.getElementById('edit-bundle-variants-wrap');
    if (!wrap) return;

    const row = document.createElement('div');
    row.className = 'bundle-variant-row';
    row.innerHTML = `
        <div class="d-grid gap-3">
            <div>
                <div class="bundle-row-label">Subcategory</div>
                <input type="text" class="form-control bg-dark text-white border-secondary bundle-variant-name"
                    placeholder="e.g. 110-115 cc, 125 cc, 150-155 cc" value="${escapeHtml(initial.variant_name || '')}">
            </div>
            <div class="bundle-row-grid variant-grid">
                <div>
                    <div class="bundle-row-label">Item Value</div>
                    <div class="input-group">
                        <span class="input-group-text bg-dark border-secondary text-warning">P</span>
                        <input type="number" min="0" step="0.01" class="form-control bg-dark text-white border-secondary bundle-item-value-input"
                            placeholder="0.00" value="${escapeHtml(String(initial.item_value_reference || ''))}" readonly>
                    </div>
                </div>
                <div>
                    <div class="bundle-row-label">Shop Share</div>
                    <div class="input-group">
                        <span class="input-group-text bg-dark border-secondary text-warning">P</span>
                        <input type="number" min="0" step="0.01" class="form-control bg-dark text-white border-secondary bundle-shop-share-input"
                            placeholder="0.00" value="${escapeHtml(String(initial.shop_share || ''))}">
                    </div>
                </div>
                <div>
                    <div class="bundle-row-label">Mechanic Share</div>
                    <div class="input-group">
                        <span class="input-group-text bg-dark border-secondary text-warning">P</span>
                        <input type="number" min="0" step="0.01" class="form-control bg-dark text-white border-secondary bundle-mechanic-share-input"
                            placeholder="0.00" value="${escapeHtml(String(initial.mechanic_share || ''))}">
                    </div>
                </div>
                <div>
                    <div class="bundle-row-label">Total Price</div>
                    <div class="input-group">
                        <span class="input-group-text bg-dark border-secondary text-info">P</span>
                        <input type="text" class="form-control bg-dark text-white border-secondary bundle-variant-total-price"
                            value="${escapeHtml(String(initial.sale_price || '0.00'))}" readonly>
                    </div>
                </div>
                <button type="button" class="btn btn-outline-danger bundle-remove-btn" onclick="removeBundleRow(this)" title="Remove subcategory">
                    <i class="bi bi-x-lg"></i>
                </button>
            </div>
        </div>
    `;
    wrap.appendChild(row);
    recalculateBundleItemValues();
}

function addBundleServiceRowTo(wrapId, initial = {}) {
    const wrap = document.getElementById(wrapId);
    if (!wrap) return;

    const row = document.createElement('div');
    row.className = 'bundle-service-row border border-secondary rounded p-2 mb-2';
    row.innerHTML = `
        <div class="position-relative">
            <div class="input-group">
                <input type="text" class="form-control bg-dark text-white border-secondary bundle-service-search"
                    placeholder="Search service..." autocomplete="off" value="${escapeHtml(initial.name || '')}">
                <input type="hidden" class="bundle-service-id" value="${escapeHtml(String(initial.service_id || ''))}">
                <button type="button" class="btn btn-outline-danger" onclick="removeBundleRow(this)">
                    <i class="bi bi-x-lg"></i>
                </button>
            </div>
            <div class="list-group position-absolute w-100 bundle-service-suggestions"
                style="display:none; z-index:9999; background:#1a1a1a; border:1px solid #444; border-radius:0 0 8px 8px; max-height:180px; overflow-y:auto;">
            </div>
        </div>
    `;
    wrap.appendChild(row);
    recalculateBundleItemValues();
}

function addBundleServiceRow(initial = {}) {
    addBundleServiceRowTo('bundle-services-wrap', initial);
}

function addEditBundleServiceRow(initial = {}) {
    const wrap = document.getElementById('edit-bundle-services-wrap');
    if (!wrap) return;

    const row = document.createElement('div');
    row.className = 'bundle-service-row';
    row.innerHTML = `
        <div class="position-relative">
            <div class="bundle-row-grid service-grid">
                <div>
                    <div class="bundle-row-label">Service</div>
                    <input type="text" class="form-control bg-dark text-white border-secondary bundle-service-search"
                        placeholder="Search service..." autocomplete="off" value="${escapeHtml(initial.name || '')}">
                    <input type="hidden" class="bundle-service-id" value="${escapeHtml(String(initial.service_id || ''))}">
                </div>
                <button type="button" class="btn btn-outline-danger bundle-remove-btn" onclick="removeBundleRow(this)" title="Remove service">
                    <i class="bi bi-x-lg"></i>
                </button>
            </div>
            <div class="list-group position-absolute w-100 bundle-service-suggestions"
                style="display:none; z-index:9999; background:#1a1a1a; border:1px solid #444; border-radius:0 0 8px 8px; max-height:180px; overflow-y:auto;">
            </div>
        </div>
    `;
    wrap.appendChild(row);
    recalculateBundleItemValues();
}

function addBundleItemRowTo(wrapId, initial = {}) {
    const wrap = document.getElementById(wrapId);
    if (!wrap) return;

    const row = document.createElement('div');
    row.className = 'bundle-item-row border border-secondary rounded p-2 mb-2';
    row.innerHTML = `
        <div class="position-relative">
            <div class="row g-2 align-items-center">
                <div class="col-8">
                    <input type="text" class="form-control bg-dark text-white border-secondary bundle-item-search"
                        placeholder="Search item..." autocomplete="off" value="${escapeHtml(initial.name || '')}">
                    <input type="hidden" class="bundle-item-id" value="${escapeHtml(String(initial.item_id || ''))}">
                    <input type="hidden" class="bundle-item-selling-price" value="${escapeHtml(String(initial.selling_price || '0'))}">
                </div>
                <div class="col-3">
                    <input type="number" min="1" step="1" class="form-control bg-dark text-white border-secondary bundle-item-qty"
                        placeholder="Qty" value="${escapeHtml(String(initial.quantity || 1))}">
                </div>
                <div class="col-1 text-end">
                    <button type="button" class="btn btn-sm btn-outline-danger" onclick="removeBundleRow(this)">
                        <i class="bi bi-x-lg"></i>
                    </button>
                </div>
            </div>
            <div class="list-group position-absolute w-100 bundle-item-suggestions"
                style="display:none; z-index:9999; background:#1a1a1a; border:1px solid #444; border-radius:0 0 8px 8px; max-height:180px; overflow-y:auto;">
            </div>
        </div>
    `;
    wrap.appendChild(row);
}

function addBundleItemRow(initial = {}) {
    addBundleItemRowTo('bundle-items-wrap', initial);
}

function addEditBundleItemRow(initial = {}) {
    const wrap = document.getElementById('edit-bundle-items-wrap');
    if (!wrap) return;

    const row = document.createElement('div');
    row.className = 'bundle-item-row';
    row.innerHTML = `
        <div class="position-relative">
            <div class="bundle-row-grid item-grid">
                <div>
                    <div class="bundle-row-label">Item</div>
                    <input type="text" class="form-control bg-dark text-white border-secondary bundle-item-search"
                        placeholder="Search item..." autocomplete="off" value="${escapeHtml(initial.name || '')}">
                    <input type="hidden" class="bundle-item-id" value="${escapeHtml(String(initial.item_id || ''))}">
                    <input type="hidden" class="bundle-item-selling-price" value="${escapeHtml(String(initial.selling_price || '0'))}">
                </div>
                <div>
                    <div class="bundle-row-label">Qty</div>
                    <input type="number" min="1" step="1" class="form-control bg-dark text-white border-secondary bundle-item-qty"
                        placeholder="Qty" value="${escapeHtml(String(initial.quantity || 1))}">
                </div>
                <button type="button" class="btn btn-outline-danger bundle-remove-btn" onclick="removeBundleRow(this)" title="Remove item">
                    <i class="bi bi-x-lg"></i>
                </button>
            </div>
            <div class="list-group position-absolute w-100 bundle-item-suggestions"
                style="display:none; z-index:9999; background:#1a1a1a; border:1px solid #444; border-radius:0 0 8px 8px; max-height:180px; overflow-y:auto;">
            </div>
        </div>
    `;
    wrap.appendChild(row);
}

function removeBundleRow(button) {
    const row = button.closest('.bundle-variant-row, .bundle-service-row, .bundle-item-row');
    if (!row) return;
    const parent = row.parentElement;
    row.remove();

    if (parent && parent.id === 'bundle-variants-wrap' && parent.children.length === 0) addBundleVariantRow();
    if (parent && parent.id === 'bundle-services-wrap' && parent.children.length === 0) addBundleServiceRow();
    if (parent && parent.id === 'bundle-items-wrap' && parent.children.length === 0) addBundleItemRow();
    if (parent && parent.id === 'edit-bundle-variants-wrap' && parent.children.length === 0) addEditBundleVariantRow();
    if (parent && parent.id === 'edit-bundle-services-wrap' && parent.children.length === 0) addEditBundleServiceRow();
    if (parent && parent.id === 'edit-bundle-items-wrap' && parent.children.length === 0) addEditBundleItemRow();
    recalculateBundleItemValues();
}

function recalculateBundleVariantRow(row) {
    if (!row) return;
    const itemValue = parseFloat(row.querySelector('.bundle-item-value-input')?.value || 0) || 0;
    const shopShare = parseFloat(row.querySelector('.bundle-shop-share-input')?.value || 0) || 0;
    const mechanicShare = parseFloat(row.querySelector('.bundle-mechanic-share-input')?.value || 0) || 0;
    const total = itemValue + shopShare + mechanicShare;
    const totalInput = row.querySelector('.bundle-variant-total-price');
    if (totalInput) {
        totalInput.value = total.toFixed(2);
    }
}

function getBundleComputedItemValue(itemsWrapSelector) {
    let computedTotal = 0;
    document.querySelectorAll(`${itemsWrapSelector} .bundle-item-row`).forEach(row => {
        const itemId = row.querySelector('.bundle-item-id')?.value?.trim();
        const quantity = parseInt(row.querySelector('.bundle-item-qty')?.value || 0, 10) || 0;
        const sellingPrice = parseFloat(row.querySelector('.bundle-item-selling-price')?.value || 0) || 0;
        if (!itemId || quantity <= 0) return;
        computedTotal += quantity * sellingPrice;
    });
    return Number(computedTotal.toFixed(2));
}

function recalculateBundleItemValues() {
    const scopes = [
        {
            itemsWrapSelector: '#bundle-items-wrap',
            variantRowSelector: '#bundle-variants-wrap .bundle-variant-row',
        },
        {
            itemsWrapSelector: '#edit-bundle-items-wrap',
            variantRowSelector: '#edit-bundle-variants-wrap .bundle-variant-row',
        },
    ];

    scopes.forEach(scope => {
        const computedValue = getBundleComputedItemValue(scope.itemsWrapSelector);
        document.querySelectorAll(scope.variantRowSelector).forEach(row => {
            const itemValueInput = row.querySelector('.bundle-item-value-input');
            if (itemValueInput) {
                itemValueInput.value = computedValue.toFixed(2);
            }
            recalculateBundleVariantRow(row);
        });
    });
}

async function bundleAutocompleteSearch(input, type) {
    const query = input.value.trim();
    const row = input.closest(type === 'service' ? '.bundle-service-row' : '.bundle-item-row');
    if (!row) return;

    const hiddenId = row.querySelector(type === 'service' ? '.bundle-service-id' : '.bundle-item-id');
    const sellingPriceInput = type === 'item' ? row.querySelector('.bundle-item-selling-price') : null;
    const suggestions = row.querySelector(type === 'service' ? '.bundle-service-suggestions' : '.bundle-item-suggestions');

    if (!query) {
        hiddenId.value = '';
        if (sellingPriceInput) {
            sellingPriceInput.value = '0';
            recalculateBundleItemValues();
        }
        suggestions.style.display = 'none';
        suggestions.innerHTML = '';
        return;
    }

    const endpoint = type === 'service' ? '/api/search/services' : '/api/search/items';
    const response = await fetch(`${endpoint}?q=${encodeURIComponent(query)}`);
    const data = await response.json();
    const list = type === 'service' ? (data.services || []) : (data.items || []);

    suggestions.innerHTML = '';
    if (!list.length) {
        suggestions.style.display = 'none';
        return;
    }

    list.forEach(entry => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'list-group-item list-group-item-action bg-dark text-white border-secondary';
        const secondary = type === 'service'
            ? (entry.category || 'Uncategorized')
            : `${entry.category || 'Uncategorized'}${entry.a4s_selling_price !== undefined ? ` • ₱${parseFloat(entry.a4s_selling_price || 0).toFixed(2)}` : ''}`;
        const nameStrong = document.createElement('strong');
        nameStrong.textContent = entry.name || '';
        const secondaryDiv = document.createElement('div');
        secondaryDiv.className = 'small text-info';
        secondaryDiv.textContent = secondary;
        button.appendChild(nameStrong);
        button.appendChild(secondaryDiv);
        button.addEventListener('click', () => {
            input.value = entry.name || '';
            hiddenId.value = entry.id || '';
            if (sellingPriceInput) {
                sellingPriceInput.value = String(parseFloat(entry.a4s_selling_price || 0) || 0);
            }
            suggestions.style.display = 'none';
            suggestions.innerHTML = '';
            recalculateBundleItemValues();
        });
        suggestions.appendChild(button);
    });

    suggestions.style.display = 'block';
}

function collectBundleFormData(scope, options = {}) {
    const {
        variantRowSelector,
        serviceRowSelector,
        itemRowSelector,
        variantButtonSelector,
        variantJsonId,
        servicesJsonId,
        itemsJsonId,
    } = scope;

    function showBundleValidation(message, targetSelector = null) {
        if (window.A4Flash?.show) {
            window.A4Flash.show(message, 'warning', {
                replace: true,
                clearExisting: true,
                targetSelector,
            });
        }
    }

    const variants = [];
    const services = [];
    const items = [];

    for (const row of document.querySelectorAll(variantRowSelector)) {
        const variantName = row.querySelector('.bundle-variant-name').value.trim();
        const itemValue = row.querySelector('.bundle-item-value-input').value.trim();
        const shopShare = row.querySelector('.bundle-shop-share-input').value.trim();
        const mechanicShare = row.querySelector('.bundle-mechanic-share-input').value.trim();
        if (!variantName && !itemValue && !shopShare && !mechanicShare) continue;
        if (!variantName) {
            showBundleValidation('Each bundle variant needs a name.', options.variantTargetSelector || '.bundle-variant-name');
            row.querySelector('.bundle-variant-name').focus();
            return null;
        }
        if (itemValue === '' || shopShare === '' || mechanicShare === '') {
            showBundleValidation(`Please complete the pricing breakdown for "${variantName}".`, options.variantPriceTargetSelector || '.bundle-item-value-input');
            (row.querySelector('.bundle-item-value-input') || row.querySelector('.bundle-shop-share-input') || row.querySelector('.bundle-mechanic-share-input'))?.focus();
            return null;
        }
        variants.push({
            variant_name: variantName,
            item_value_reference: itemValue,
            shop_share: shopShare,
            mechanic_share: mechanicShare,
            sale_price: row.querySelector('.bundle-variant-total-price')?.value || '0.00',
        });
    }

    if (variants.length === 0) {
        showBundleValidation('Please add at least one bundle variant.', variantButtonSelector);
        return null;
    }

    for (const row of document.querySelectorAll(serviceRowSelector)) {
        const serviceName = row.querySelector('.bundle-service-search').value.trim();
        const serviceId = row.querySelector('.bundle-service-id').value.trim();
        if (!serviceName && !serviceId) continue;
        if (!serviceId) {
            showBundleValidation(`Please select "${serviceName}" from the service search list.`, options.serviceTargetSelector || '.bundle-service-search');
            row.querySelector('.bundle-service-search').focus();
            return null;
        }
        services.push(parseInt(serviceId, 10));
    }

    for (const row of document.querySelectorAll(itemRowSelector)) {
        const itemName = row.querySelector('.bundle-item-search').value.trim();
        const itemId = row.querySelector('.bundle-item-id').value.trim();
        const quantity = row.querySelector('.bundle-item-qty').value.trim();
        if (!itemName && !itemId && (!quantity || quantity === '1')) continue;
        if (!itemId) {
            showBundleValidation(`Please select "${itemName}" from the item search list.`, options.itemTargetSelector || '.bundle-item-search');
            row.querySelector('.bundle-item-search').focus();
            return null;
        }
        if (!quantity || parseInt(quantity, 10) <= 0) {
            showBundleValidation('Bundle item quantity must be at least 1.', options.itemQtyTargetSelector || '.bundle-item-qty');
            row.querySelector('.bundle-item-qty').focus();
            return null;
        }
        items.push({ item_id: parseInt(itemId, 10), quantity: parseInt(quantity, 10) });
    }

    document.getElementById(variantJsonId).value = JSON.stringify(variants);
    document.getElementById(servicesJsonId).value = JSON.stringify(services);
    document.getElementById(itemsJsonId).value = JSON.stringify(items);
    return { variants, services, items };
}

async function prepareBundleSubmission(event) {
    const result = collectBundleFormData({
        variantRowSelector: '#bundle-variants-wrap .bundle-variant-row',
        serviceRowSelector: '#bundle-services-wrap .bundle-service-row',
        itemRowSelector: '#bundle-items-wrap .bundle-item-row',
        variantButtonSelector: '#add-bundle-variant-btn',
        variantJsonId: 'bundle-variants-json',
        servicesJsonId: 'bundle-services-json',
        itemsJsonId: 'bundle-items-json',
    });
    if (!result) {
        event.preventDefault();
    }
}

async function prepareEditBundleSubmission(event) {
    const result = collectBundleFormData({
        variantRowSelector: '#edit-bundle-variants-wrap .bundle-variant-row',
        serviceRowSelector: '#edit-bundle-services-wrap .bundle-service-row',
        itemRowSelector: '#edit-bundle-items-wrap .bundle-item-row',
        variantButtonSelector: '#edit-add-bundle-variant-btn',
        variantJsonId: 'edit-bundle-variants-json',
        servicesJsonId: 'edit-bundle-services-json',
        itemsJsonId: 'edit-bundle-items-json',
    });
    if (!result) {
        event.preventDefault();
    }
}

async function fetchBundlePayload(bundleId) {
    const response = await fetch(`/api/bundles/${bundleId}`);
    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.error || 'Failed to load bundle.');
    }
    return data;
}

async function openBundleDetails(bundleId) {
    const body = document.getElementById('bundleDetailsBody');
    const modalEl = document.getElementById('bundleDetailsModal');
    if (!body || !modalEl) return;
    body.innerHTML = '<div class="text-center text-muted py-3"><i class="bi bi-hourglass-split me-1"></i> Loading bundle...</div>';
    new bootstrap.Modal(modalEl).show();

    try {
        const data = await fetchBundlePayload(bundleId);
        body.replaceChildren();

        const shell = document.createElement('div');
        shell.className = 'bundle-details-shell';

        const topCard = document.createElement('div');
        topCard.className = 'bundle-editor-card';
        topCard.innerHTML = '<div class="bundle-editor-kicker">Bundle Snapshot</div>';

        const detailsGrid = document.createElement('div');
        detailsGrid.className = 'bundle-details-grid';
        [
            ['Bundle', data.name || ''],
            ['Vehicle Category', data.vehicle_category || ''],
            ['Current Version', `Version ${data.version_no || 1}`],
        ].forEach(([label, value]) => {
            const pill = document.createElement('div');
            pill.className = 'bundle-details-pill';
            const pillTitle = document.createElement('div');
            pillTitle.className = 'bundle-details-pill-title';
            pillTitle.textContent = label;
            const pillValue = document.createElement('div');
            pillValue.className = 'bundle-details-pill-value';
            pillValue.textContent = value;
            pill.appendChild(pillTitle);
            pill.appendChild(pillValue);
            detailsGrid.appendChild(pill);
        });
        topCard.appendChild(detailsGrid);
        shell.appendChild(topCard);

        const sections = document.createElement('div');
        sections.className = 'bundle-editor-sections';

        const variantsCard = document.createElement('div');
        variantsCard.className = 'bundle-editor-card';
        variantsCard.innerHTML = `
            <div class="bundle-editor-section-title">
                <strong class="text-warning">Subcategories</strong>
                <span class="small text-white-50">${(data.variants || []).length} total</span>
            </div>`;
        const tableWrap = document.createElement('div');
        tableWrap.className = 'bundle-details-table-wrap';
        const table = document.createElement('table');
        table.className = 'table table-dark table-hover align-middle bundle-details-table mb-0';
        table.innerHTML = `
            <thead>
                <tr>
                    <th>Subcategory</th>
                    <th class="text-end">Item Value</th>
                    <th class="text-end">Shop Share</th>
                    <th class="text-end">Mechanic Share</th>
                    <th class="text-end">Price</th>
                </tr>
            </thead>`;
        const tbody = document.createElement('tbody');
        if ((data.variants || []).length) {
            (data.variants || []).forEach((variant) => {
                const row = document.createElement('tr');
                [variant.variant_name, formatPesoText(variant.item_value_reference), formatPesoText(variant.shop_share), formatPesoText(variant.mechanic_share), formatPesoText(variant.sale_price)]
                    .forEach((value, index) => {
                        const cell = document.createElement('td');
                        if (index > 0) cell.className = 'text-end';
                        cell.textContent = value;
                        row.appendChild(cell);
                    });
                tbody.appendChild(row);
            });
        } else {
            const row = document.createElement('tr');
            const cell = document.createElement('td');
            cell.colSpan = 5;
            cell.className = 'text-center text-muted';
            cell.textContent = 'No subcategories saved.';
            row.appendChild(cell);
            tbody.appendChild(row);
        }
        table.appendChild(tbody);
        tableWrap.appendChild(table);
        variantsCard.appendChild(tableWrap);
        sections.appendChild(variantsCard);

        const buildListCard = (titleClass, titleText, count, rows, emptyText, itemBuilder) => {
            const card = document.createElement('div');
            card.className = 'bundle-editor-card';
            card.innerHTML = `
                <div class="bundle-editor-section-title">
                    <strong class="${titleClass}">${titleText}</strong>
                    <span class="small text-white-50">${count} total</span>
                </div>`;
            const list = document.createElement('ul');
            list.className = 'bundle-details-list';
            if (rows.length) {
                rows.forEach((rowData) => list.appendChild(itemBuilder(rowData)));
            } else {
                const empty = document.createElement('li');
                empty.className = 'text-muted';
                empty.textContent = emptyText;
                list.appendChild(empty);
            }
            card.appendChild(list);
            return card;
        };

        sections.appendChild(buildListCard(
            'text-info',
            'Included Services',
            (data.services || []).length,
            data.services || [],
            'No services saved.',
            (service) => {
                const item = document.createElement('li');
                item.className = 'mb-1';
                const name = document.createElement('span');
                name.className = 'text-info';
                name.textContent = service.name || '';
                const category = document.createElement('span');
                category.className = 'text-white-50';
                category.textContent = ` (${service.category || 'Uncategorized'})`;
                item.appendChild(name);
                item.appendChild(category);
                return item;
            }
        ));

        sections.appendChild(buildListCard(
            'text-success',
            'Included Items',
            (data.items || []).length,
            data.items || [],
            'No items saved.',
            (itemData) => {
                const item = document.createElement('li');
                item.className = 'mb-1';
                const name = document.createElement('span');
                name.className = 'text-success';
                name.textContent = itemData.name || '';
                const qty = document.createElement('span');
                qty.className = 'text-white-50';
                qty.textContent = ` x${itemData.quantity}`;
                item.appendChild(name);
                item.appendChild(qty);
                return item;
            }
        ));

        shell.appendChild(sections);

        const noteCard = document.createElement('div');
        noteCard.className = 'bundle-editor-card';
        noteCard.innerHTML = '<div class="bundle-editor-kicker">Version Note</div>';
        const notePill = document.createElement('div');
        notePill.className = 'bundle-details-pill';
        const noteValue = document.createElement('div');
        noteValue.className = 'bundle-details-pill-value';
        noteValue.style.fontSize = '0.92rem';
        noteValue.style.fontWeight = '600';
        noteValue.textContent = data.change_notes || 'No change note saved.';
        notePill.appendChild(noteValue);
        noteCard.appendChild(notePill);
        shell.appendChild(noteCard);

        body.appendChild(shell);
    } catch (error) {
        renderTextMessage(body, error.message || 'Failed to load bundle.', 'text-danger text-center py-3');
    }
}

async function openBundleEditModal(bundleId) {
    const form = document.getElementById('edit-bundle-form');
    const modalEl = document.getElementById('editBundleModal');
    if (!form || !modalEl) return;

    try {
        const data = await fetchBundlePayload(bundleId);
        form.action = `/bundles/${bundleId}/edit`;
        document.getElementById('edit-bundle-name').value = data.name || '';
        document.getElementById('edit-bundle-vehicle-category').value = data.vehicle_category || '';
        document.getElementById('edit-bundle-version-display').value = `Version ${data.version_no || 1}`;
        document.getElementById('edit-bundle-change-notes').value = '';
        resetEditBundleDynamicRows();

        (data.variants || []).forEach(variant => addEditBundleVariantRow(variant));
        if (!(data.variants || []).length) addEditBundleVariantRow();

        (data.services || []).forEach(service => addEditBundleServiceRow(service));
        if (!(data.services || []).length) addEditBundleServiceRow();

        (data.items || []).forEach(item => addEditBundleItemRow(item));
        if (!(data.items || []).length) addEditBundleItemRow();

        recalculateBundleItemValues();

        new bootstrap.Modal(modalEl).show();
    } catch (error) {
        if (window.A4Flash?.show) {
            window.A4Flash.show(error.message || 'Failed to load bundle.', 'danger', {
                replace: true,
                clearExisting: true,
            });
        }
    }
}

document.addEventListener('input', (event) => {
    if (
        event.target.classList.contains('bundle-shop-share-input')
        || event.target.classList.contains('bundle-mechanic-share-input')
    ) {
        recalculateBundleVariantRow(event.target.closest('.bundle-variant-row'));
    }

    if (event.target.classList.contains('bundle-service-search')) {
        const hiddenId = event.target.closest('.bundle-service-row').querySelector('.bundle-service-id');
        hiddenId.value = '';
        bundleAutocompleteSearch(event.target, 'service');
    }

    if (event.target.classList.contains('bundle-item-search')) {
        const hiddenId = event.target.closest('.bundle-item-row').querySelector('.bundle-item-id');
        const priceInput = event.target.closest('.bundle-item-row').querySelector('.bundle-item-selling-price');
        hiddenId.value = '';
        if (priceInput) priceInput.value = '0';
        recalculateBundleItemValues();
        bundleAutocompleteSearch(event.target, 'item');
    }

    if (event.target.classList.contains('bundle-item-qty')) {
        recalculateBundleItemValues();
    }
});

document.addEventListener('click', (event) => {
    if (!event.target.closest('.bundle-service-row')) {
        document.querySelectorAll('.bundle-service-suggestions').forEach(el => {
            el.style.display = 'none';
        });
    }
    if (!event.target.closest('.bundle-item-row')) {
        document.querySelectorAll('.bundle-item-suggestions').forEach(el => {
            el.style.display = 'none';
        });
    }
});

function legacyViewSaleDetailsDeprecated(saleID, displayNum) {
    document.getElementById('modalSaleID').innerText = displayNum || saleID;
    const list = document.getElementById('saleItemsList');
    list.innerHTML = '<tr><td colspan="3" class="text-center">Loading...</td></tr>';
    
    const myModal = new bootstrap.Modal(document.getElementById('saleDetailsModal'));
    myModal.show();

    fetch(`/sales/details/${saleID}`)
        .then(response => response.json())
        .then(data => {
            list.replaceChildren();
            
            // 1. Show Physical Items
            data.items.forEach(item => {
                const hasDisc = item.discount_amount && item.discount_amount > 0;
                const row = document.createElement('tr');
                const itemCell = document.createElement('td');
                const priceCell = document.createElement('td');
                priceCell.className = 'text-center align-middle';
                const qtyCell = document.createElement('td');
                qtyCell.className = 'text-center align-middle fw-bold text-warning';

                const nameDiv = document.createElement('div');
                nameDiv.textContent = item.name || '';
                itemCell.appendChild(nameDiv);
                if (hasDisc) {
                    const discount = document.createElement('small');
                    discount.className = 'text-success';
                    discount.textContent = `Disc: -${formatPesoText(item.discount_amount)}`;
                    itemCell.appendChild(discount);
                }

                const originalPrice = document.createElement('div');
                originalPrice.textContent = formatPesoText(parseFloat(item.original_price).toFixed(2));
                priceCell.appendChild(originalPrice);
                if (hasDisc) {
                    const finalPrice = document.createElement('div');
                    finalPrice.className = 'text-success fw-bold';
                    finalPrice.textContent = formatPesoText(parseFloat(item.final_unit_price).toFixed(2));
                    priceCell.appendChild(finalPrice);
                }

                qtyCell.textContent = String(item.quantity ?? '');
                row.appendChild(itemCell);
                row.appendChild(priceCell);
                row.appendChild(qtyCell);
                list.appendChild(row);
            });

            // 2. Show Services/Labor
            if(data.services && data.services.length > 0) {
                const sectionRow = document.createElement('tr');
                sectionRow.className = 'table-dark';
                const sectionCell = document.createElement('td');
                sectionCell.colSpan = 3;
                sectionCell.className = 'text-info fw-bold border-top border-secondary';
                sectionCell.textContent = data.mechanic ? `Labor & Services (by ${data.mechanic})` : 'Labor & Services';
                sectionRow.appendChild(sectionCell);
                list.appendChild(sectionRow);
                data.services.forEach(svc => {
                    const row = document.createElement('tr');
                    const nameCell = document.createElement('td');
                    nameCell.textContent = svc.name || '';
                    const priceCell = document.createElement('td');
                    priceCell.className = 'text-center';
                    priceCell.textContent = formatPesoText(parseFloat(svc.price).toFixed(2));
                    const qtyCell = document.createElement('td');
                    qtyCell.className = 'text-center text-muted';
                    qtyCell.textContent = '1';
                    row.appendChild(nameCell);
                    row.appendChild(priceCell);
                    row.appendChild(qtyCell);
                    list.appendChild(row);
                });
            }

            // 3. The Golden Total Row with Payment Badge
            const badgeMap = {
            'Cash': 'bg-success',
            'Utang': 'bg-warning'
            };
            const badgeClass = badgeMap[data.payment_method] || 'bg-primary';
            const totalRow = document.createElement('tr');
            totalRow.className = 'border-top border-danger';
            const labelCell = document.createElement('td');
            labelCell.className = 'fw-bold pt-3 text-danger';
            labelCell.appendChild(document.createTextNode('GRAND TOTAL '));
            const badge = document.createElement('span');
            badge.className = `badge ${badgeClass} ms-2`;
            badge.style.fontSize = '0.65rem';
            badge.style.verticalAlign = 'middle';
            badge.textContent = data.payment_method || '';
            labelCell.appendChild(badge);
            const amountCell = document.createElement('td');
            amountCell.className = 'pt-3 fw-bold fs-5 text-danger text-center';
            amountCell.textContent = formatPesoText(data.total_amount);
            totalRow.appendChild(labelCell);
            totalRow.appendChild(amountCell);
            totalRow.appendChild(document.createElement('td'));
            list.appendChild(totalRow);
        });
}

function formatPeso(value) {
    return `&#8369;${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatPesoText(value) {
    return `₱${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function renderTextMessage(container, message, className = 'text-muted text-center py-2') {
    if (!container) return;
    container.replaceChildren();
    const messageEl = document.createElement('div');
    messageEl.className = className;
    messageEl.textContent = message;
    container.appendChild(messageEl);
}

function renderTableMessage(tbody, colspan, message, className = 'text-center py-4') {
    if (!tbody) return;
    tbody.replaceChildren();
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = colspan;
    cell.className = className;
    cell.textContent = message;
    row.appendChild(cell);
    tbody.appendChild(row);
}

function renderRefundHistory(data) {
    const historyEl = document.getElementById('saleRefundHistory');
    const refunds = data.refund_history || [];
    historyEl.replaceChildren();

    const title = document.createElement('div');
    title.className = 'sale-refund-title';
    title.innerHTML = '<i class="bi bi-arrow-counterclockwise"></i><span>Refund History</span>';
    historyEl.appendChild(title);

    if (!refunds.length) {
        const emptyNote = document.createElement('div');
        emptyNote.className = 'sale-refund-note';
        emptyNote.textContent = 'No refunds recorded for this sale.';
        historyEl.appendChild(emptyNote);
        return;
    }

    const wrapper = document.createElement('div');

    refunds.forEach((refund) => {
        const card = document.createElement('div');
        card.className = 'sale-refund-card';

        const head = document.createElement('div');
        head.className = 'sale-refund-head';

        const details = document.createElement('div');

        const refundNumber = document.createElement('div');
        refundNumber.className = 'sale-refund-number';
        refundNumber.textContent = refund.refund_number || 'Refund';

        const refundMeta = document.createElement('div');
        refundMeta.className = 'sale-refund-meta';
        refundMeta.textContent = `${refund.refund_date_display || ''} - ${refund.refunded_by_username || 'System'}`;

        const refundReason = document.createElement('div');
        refundReason.className = 'sale-refund-note';
        refundReason.textContent = refund.reason || '';

        details.appendChild(refundNumber);
        details.appendChild(refundMeta);
        details.appendChild(refundReason);

        if (refund.notes) {
            const refundNotes = document.createElement('div');
            refundNotes.className = 'sale-refund-note';
            refundNotes.style.color = '#1d4ed8';
            refundNotes.textContent = refund.notes;
            details.appendChild(refundNotes);
        }

        const refundAmount = document.createElement('div');
        refundAmount.className = 'sale-refund-amount';
        refundAmount.textContent = formatPesoText(refund.refund_amount);

        head.appendChild(details);
        head.appendChild(refundAmount);
        card.appendChild(head);
        wrapper.appendChild(card);
    });

    historyEl.appendChild(wrapper);
}

function populateSaleDetails(data, saleID, displayNum) {
    document.getElementById('modalSaleID').innerText = displayNum || data.sales_number || saleID;

    const meta = document.getElementById('saleDetailsMeta');
    const summary = document.getElementById('saleDetailsSummary');
    const paymentPill = document.getElementById('saleDetailsPaymentPill');
    const paymentMethod = data.payment_method_name || 'N/A';
    const paymentToneClass = /cash/i.test(paymentMethod)
        ? 'sale-payment-cash'
        : /(utang|debt)/i.test(paymentMethod)
            ? 'sale-payment-credit'
            : 'sale-payment-other';

    paymentPill.className = `sale-payment-pill ${paymentToneClass}`;
    paymentPill.textContent = paymentMethod;

    meta.replaceChildren();
    [
        ['Customer', data.customer_name || 'Walk-in'],
        ['Date', data.transaction_date_display || ''],
        ['Status', data.status || 'Completed'],
    ].forEach(([label, value]) => {
        const card = document.createElement('div');
        card.className = 'sale-receipt-meta-card';

        const cardLabel = document.createElement('div');
        cardLabel.className = 'sale-receipt-meta-label';
        cardLabel.textContent = label;

        const cardValue = document.createElement('div');
        cardValue.className = 'sale-receipt-meta-value';
        cardValue.textContent = value;

        card.appendChild(cardLabel);
        card.appendChild(cardValue);
        meta.appendChild(card);
    });

    summary.replaceChildren();
    const summaryChips = [
        { icon: 'bi bi-calculator', label: `Total ${formatPesoText(data.total_amount)}`, tone: 'total' },
    ];
    if (Number(data.total_refunded || 0) > 0) {
        summaryChips.push(
            { icon: 'bi bi-arrow-return-left', label: `Refunded ${formatPesoText(data.total_refunded)}`, tone: 'refund' },
            { icon: 'bi bi-wallet2', label: `Net ${formatPesoText(data.net_amount)}`, tone: 'net' },
        );
    }

    summaryChips.forEach((chipData) => {
        const chip = document.createElement('div');
        chip.className = `sale-summary-chip ${chipData.tone}`;
        const chipIcon = document.createElement('i');
        chipIcon.className = chipData.icon;
        const chipText = document.createElement('span');
        chipText.textContent = chipData.label;
        chip.appendChild(chipIcon);
        chip.appendChild(chipText);
        summary.appendChild(chip);
    });

    const list = document.getElementById('saleItemsList');
    list.replaceChildren();

    (data.items || []).forEach(item => {
        const hasDisc = Number(item.discount_amount || 0) > 0;
        const row = document.createElement('tr');
        const itemCell = document.createElement('td');
        const priceCell = document.createElement('td');
        const qtyCell = document.createElement('td');

        const itemName = document.createElement('div');
        itemName.className = 'sale-line-name';
        itemName.textContent = item.name || '';
        itemCell.appendChild(itemName);

        if (hasDisc) {
            const discountNote = document.createElement('div');
            discountNote.className = 'sale-line-note discount';
            discountNote.textContent = `Discount applied: -${formatPesoText(item.discount_amount)}`;
            itemCell.appendChild(discountNote);
        }
        if (Number(item.refunded_quantity || 0) > 0) {
            const refundNote = document.createElement('div');
            refundNote.className = 'sale-line-note refund';
            refundNote.textContent = `Refunded: ${item.refunded_quantity} / Remaining: ${item.refundable_quantity}`;
            itemCell.appendChild(refundNote);
        }

        const originalPrice = document.createElement('div');
        originalPrice.className = 'sale-line-name';
        originalPrice.textContent = formatPesoText(item.original_price);
        priceCell.appendChild(originalPrice);

        if (hasDisc) {
            const finalPrice = document.createElement('div');
            finalPrice.className = 'sale-line-note discount';
            finalPrice.textContent = `Final: ${formatPesoText(item.final_unit_price)}`;
            priceCell.appendChild(finalPrice);
        }

        priceCell.className = 'text-center align-middle';
        qtyCell.className = 'text-center align-middle fw-bold';
        qtyCell.textContent = String(item.sold_quantity ?? '');

        row.appendChild(itemCell);
        row.appendChild(priceCell);
        row.appendChild(qtyCell);
        list.appendChild(row);
    });

    if (data.services && data.services.length > 0) {
        const sectionRow = document.createElement('tr');
        sectionRow.className = 'sale-section-row';
        const sectionCell = document.createElement('td');
        sectionCell.colSpan = 3;
        sectionCell.textContent = data.mechanic_name
            ? `Labor & Services - ${data.mechanic_name}`
            : 'Labor & Services';
        sectionRow.appendChild(sectionCell);
        list.appendChild(sectionRow);

        data.services.forEach(svc => {
            const row = document.createElement('tr');
            const serviceCell = document.createElement('td');
            const serviceName = document.createElement('div');
            serviceName.className = 'sale-line-name';
            serviceName.textContent = svc.name || '';
            serviceCell.appendChild(serviceName);

            const priceCell = document.createElement('td');
            priceCell.className = 'text-center';
            const priceName = document.createElement('div');
            priceName.className = 'sale-line-name';
            priceName.textContent = formatPesoText(svc.price);
            priceCell.appendChild(priceName);

            const qtyCell = document.createElement('td');
            qtyCell.className = 'text-center';
            qtyCell.textContent = '1';

            row.appendChild(serviceCell);
            row.appendChild(priceCell);
            row.appendChild(qtyCell);
            list.appendChild(row);
        });
    }

    const totalRow = document.createElement('tr');
    totalRow.className = 'sale-total-row';

    const totalLabelCell = document.createElement('td');
    totalLabelCell.className = 'sale-total-label';
    totalLabelCell.textContent = 'Grand Total';

    const totalAmountCell = document.createElement('td');
    totalAmountCell.className = 'sale-total-amount text-center';
    totalAmountCell.textContent = formatPesoText(data.total_amount);

    totalRow.appendChild(totalLabelCell);
    totalRow.appendChild(totalAmountCell);
    totalRow.appendChild(document.createElement('td'));
    list.appendChild(totalRow);

    renderRefundHistory(data);
}

function viewSaleDetails(saleID, displayNum) {
    document.getElementById('modalSaleID').innerText = displayNum || saleID;
    renderTableMessage(document.getElementById('saleItemsList'), 3, 'Loading...', 'text-center');
    document.getElementById('saleDetailsMeta').innerHTML = '';
    document.getElementById('saleDetailsSummary').innerHTML = '';
    document.getElementById('saleDetailsPaymentPill').className = 'sale-payment-pill sale-payment-other';
    document.getElementById('saleDetailsPaymentPill').textContent = 'Loading';
    document.getElementById('saleRefundHistory').innerHTML = '';
    document.getElementById('saleDetailsPrintBtn').href = `/reports/sales-receipt/${encodeURIComponent(saleID)}`;

    const myModal = new bootstrap.Modal(document.getElementById('saleDetailsModal'));
    myModal.show();

    fetch(`/sales/details/${saleID}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                renderTableMessage(document.getElementById('saleItemsList'), 3, data.error, 'text-center text-danger py-3');
                return;
            }
            populateSaleDetails(data, saleID, displayNum);
        })
        .catch(() => {
            renderTableMessage(document.getElementById('saleItemsList'), 3, 'Failed to load sale details.', 'text-center text-danger py-3');
        });
}

function viewManualInDetails(auditGroupId) {
    const body = document.getElementById('manualInDetailsBody');
    body.innerHTML = '<div class="text-center text-muted py-3"><i class="bi bi-hourglass-split me-1"></i> Loading...</div>';

    const modal = new bootstrap.Modal(document.getElementById('manualInDetailsModal'));
    modal.show();

    fetch(`/audit/manual-in/${auditGroupId}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                renderTextMessage(body, data.error, 'text-danger text-center py-3');
                return;
            }

            const walkin = data.walkin_purchase;
            const costUpdate = data.cost_update;
            body.replaceChildren();

            const header = document.createElement('div');
            header.className = 'mb-3';

            const title = document.createElement('div');
            title.className = 'text-white fw-bold fs-5';
            title.textContent = data.item_name || 'Unknown Item';

            const meta = document.createElement('div');
            meta.className = 'text-white-50 small mt-1';
            meta.innerHTML = '<i class="bi bi-clock me-1"></i><span class="manual-in-date"></span><span class="ms-3"><i class="bi bi-person me-1"></i><span class="manual-in-user"></span></span>';
            meta.querySelector('.manual-in-date').textContent = data.transaction_date || '-';
            meta.querySelector('.manual-in-user').textContent = data.user_name || 'System';

            header.appendChild(title);
            header.appendChild(meta);
            body.appendChild(header);

            if (walkin) {
                const section = document.createElement('div');
                section.className = 'border border-secondary rounded p-3 mb-3';
                section.innerHTML = '<div class="text-success fw-bold mb-2"><i class="bi bi-box-arrow-in-down me-1"></i> Walk-in Purchase</div>';

                const qtyLabel = document.createElement('div');
                qtyLabel.className = 'small text-white-50 mb-1';
                qtyLabel.textContent = 'Quantity Received';
                const qtyValue = document.createElement('div');
                qtyValue.className = 'fw-bold text-warning mb-2';
                qtyValue.textContent = String(walkin.quantity ?? '');

                const costLabel = document.createElement('div');
                costLabel.className = 'small text-white-50 mb-1';
                costLabel.textContent = 'Unit Cost';
                const costValue = document.createElement('div');
                costValue.className = 'fw-bold text-info mb-2';
                costValue.textContent = formatPesoText(Number(walkin.unit_cost || 0).toFixed(2));

                const notesLabel = document.createElement('div');
                notesLabel.className = 'small text-white-50 mb-1';
                notesLabel.textContent = 'Notes';
                const notesValue = document.createElement('div');
                notesValue.className = 'text-info';
                notesValue.textContent = walkin.notes || '-';

                section.appendChild(qtyLabel);
                section.appendChild(qtyValue);
                section.appendChild(costLabel);
                section.appendChild(costValue);
                section.appendChild(notesLabel);
                section.appendChild(notesValue);
                body.appendChild(section);
            }

            if (costUpdate) {
                const section = document.createElement('div');
                section.className = 'border border-secondary rounded p-3';
                section.innerHTML = '<div class="text-warning fw-bold mb-2"><i class="bi bi-arrow-repeat me-1"></i> Master Cost Update</div>';

                const costLabel = document.createElement('div');
                costLabel.className = 'small text-white-50 mb-1';

                const costValue = document.createElement('div');
                costValue.className = 'fw-bold text-warning mb-2';

                if (costUpdate.previous_cost != null && costUpdate.updated_cost != null) {
                    costLabel.textContent = 'Cost Per Piece';
                    costValue.textContent = `${formatPesoText(costUpdate.previous_cost)} -> ${formatPesoText(costUpdate.updated_cost)}`;
                } else {
                    costLabel.textContent = 'Updated Cost';
                    costValue.textContent = formatPesoText(costUpdate.unit_cost || 0);
                }

                const noteLabel = document.createElement('div');
                noteLabel.className = 'small text-white-50 mb-1';
                noteLabel.textContent = 'Audit Note';
                const noteValue = document.createElement('div');
                noteValue.className = 'text-info';
                noteValue.textContent = costUpdate.notes || '-';

                section.appendChild(costLabel);
                section.appendChild(costValue);
                section.appendChild(noteLabel);
                section.appendChild(noteValue);
                body.appendChild(section);
            } else {
                const empty = document.createElement('div');
                empty.className = 'text-muted small';
                empty.textContent = 'No cost-per-piece update was recorded for this manual stock-in.';
                body.appendChild(empty);
            }
        })
        .catch(() => {
            body.innerHTML = '<div class="text-danger text-center py-3">Failed to load manual stock-in details.</div>';
        });
}

function openPasswordResetReviewModal(button) {
    const requestId = button.getAttribute('data-request-id');
    const username = button.getAttribute('data-username') || 'Staff user';
    const requestedAt = button.getAttribute('data-requested-at') || '-';
    const lastRequestedAt = button.getAttribute('data-last-requested-at') || '';
    const repeatCount = Number(button.getAttribute('data-repeat-count') || '0');
    const totalCount = Number(button.getAttribute('data-total-count') || '1');
    const requestIp = button.getAttribute('data-request-ip') || 'Not captured';
    const requestNote = button.getAttribute('data-request-note') || 'No note provided.';
    const status = button.getAttribute('data-status') || 'PENDING';
    const handledBy = button.getAttribute('data-handled-by') || 'Pending';
    const handledAt = button.getAttribute('data-handled-at') || '';
    const adminNote = button.getAttribute('data-admin-note') || 'No admin note yet.';
    const canComplete = button.getAttribute('data-can-complete') === '1';
    const canReject = button.getAttribute('data-can-reject') === '1';
    const hasMatch = button.getAttribute('data-has-match') === '1';

    document.getElementById('passwordResetReviewUsername').textContent = username;
    document.getElementById('passwordResetReviewRequestedAt').textContent = requestedAt;
    document.getElementById('passwordResetReviewRequestNote').textContent = requestNote;
    document.getElementById('passwordResetReviewIp').textContent = requestIp;
    document.getElementById('passwordResetReviewAdminNote').textContent = adminNote;
    document.getElementById('passwordResetReviewHandledBy').textContent = handledAt ? `${handledBy} • ${handledAt}` : handledBy;

    const activityEl = document.getElementById('passwordResetReviewActivity');
    if (repeatCount > 0) {
        activityEl.textContent = `Submitted ${totalCount} times. Latest repeat: ${lastRequestedAt || requestedAt}`;
    } else {
        activityEl.textContent = 'Single submission';
    }

    const statusEl = document.getElementById('passwordResetReviewStatus');
    statusEl.textContent = status;
    statusEl.className = 'password-reset-status-pill';
    if (status === 'PENDING') {
        statusEl.classList.add('pending');
    } else if (status === 'COMPLETED') {
        statusEl.classList.add('completed');
    } else if (status === 'REJECTED') {
        statusEl.classList.add('rejected');
    }

    const subtitleEl = document.getElementById('passwordResetReviewSubtitle');
    if (status === 'PENDING' && hasMatch) {
        subtitleEl.textContent = 'Review the request details and choose the next action.';
    } else if (status === 'PENDING' && !hasMatch) {
        subtitleEl.textContent = 'The request is pending, but no eligible staff account is available for admin action.';
    } else {
        subtitleEl.textContent = 'This request has already been reviewed by an administrator.';
    }

    const actionsEl = document.getElementById('passwordResetReviewActions');
    const readOnlyEl = document.getElementById('passwordResetReviewReadOnly');
    const noMatchEl = document.getElementById('passwordResetReviewNoMatch');
    actionsEl.classList.toggle('d-none', !(canComplete || canReject));
    readOnlyEl.classList.toggle('d-none', status === 'PENDING');
    noMatchEl.classList.toggle('d-none', hasMatch || status !== 'PENDING');

    const completeForm = document.getElementById('passwordResetCompleteForm');
    const rejectForm = document.getElementById('passwordResetRejectForm');
    completeForm.action = `/password-resets/${encodeURIComponent(requestId)}/complete`;
    rejectForm.action = `/password-resets/${encodeURIComponent(requestId)}/reject`;
    document.getElementById('passwordResetTempPassword').value = '';
    document.getElementById('passwordResetCompleteNote').value = '';
    document.getElementById('passwordResetRejectNote').value = '';

    const modal = new bootstrap.Modal(document.getElementById('passwordResetReviewModal'));
    modal.show();
}

function viewOrder(poId, snapshotAt = '', changeReason = '', transactionType = '') {
    const params = new URLSearchParams();
    if (snapshotAt) params.set('snapshot_at', snapshotAt);
    if (changeReason) params.set('change_reason', changeReason);
    if (transactionType) params.set('transaction_type', transactionType);
    const query = params.toString();

    const formatPeso = (value) => `&#8369;${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    const renderMovementDetails = (details) => {
        const container = document.getElementById('po-movement-details');
        if (!details || !Array.isArray(details.entries) || details.entries.length === 0) {
            container.className = 'd-none';
            container.innerHTML = '';
            return;
        }

        const accent = escapeHtml(details.accent || 'info');
        const title = escapeHtml(details.title || 'Movement Details');
        const summary = escapeHtml(details.summary || '');
        const contextNote = details.context_note
            ? `<div class="small text-white-50 mt-2">${escapeHtml(details.context_note)}</div>`
            : '';
        const collapseId = 'po-movement-details-body';

        const entriesHtml = details.entries.map(entry => {
            const hasCostDelta = entry.previous_cost != null && entry.updated_cost != null;
            const qtyLine = entry.quantity != null
                ? `<div class="small text-white-50">Qty</div><div class="fw-bold text-white mb-2">${escapeHtml(String(entry.quantity))}</div>`
                : '';
            const unitCostLine = entry.unit_cost != null
                ? `<div class="small text-white-50">Unit Cost</div><div class="fw-bold text-info mb-2">${formatPeso(entry.unit_cost)}</div>`
                : '';
            const subtotalLine = entry.subtotal != null
                ? `<div class="small text-white-50">Subtotal</div><div class="fw-bold text-info mb-2">${formatPeso(entry.subtotal)}</div>`
                : '';
            const costDeltaLine = hasCostDelta
                ? `<div class="small text-white-50">Cost Per Piece</div><div class="fw-bold text-warning mb-2">${formatPeso(entry.previous_cost)} -> ${formatPeso(entry.updated_cost)}</div>`
                : '';
            const notesLine = entry.notes
                ? `<div class="small text-white-50">Notes</div><div class="text-info">${escapeHtml(entry.notes)}</div>`
                : '';

            return `
                <div class="po-movement-entry">
                    <div class="fw-bold text-white mb-2">${escapeHtml(entry.item_name || 'Unknown Item')}</div>
                    <div class="row g-3">
                        <div class="col-sm-3">${qtyLine}</div>
                        <div class="col-sm-3">${unitCostLine}</div>
                        <div class="col-sm-3">${subtotalLine}</div>
                        <div class="col-sm-3">${costDeltaLine}</div>
                    </div>
                    ${notesLine}
                </div>
            `;
        }).join('');

        container.className = `po-movement-card po-accent-${accent}`;
        container.innerHTML = `
            <button class="po-movement-toggle" type="button" data-bs-toggle="collapse" data-bs-target="#${collapseId}" aria-expanded="true" aria-controls="${collapseId}">
                <div class="d-flex align-items-center justify-content-between gap-3 flex-wrap">
                    <div>
                        <div class="small text-white-50 text-uppercase fw-bold mb-1">Audit Row Context</div>
                        <div class="fw-bold text-white">${title}</div>
                    </div>
                    <div class="d-flex align-items-center gap-2 text-white-50 small">
                        <span>Hide details</span>
                        <i class="bi bi-chevron-down po-movement-chevron"></i>
                    </div>
                </div>
            </button>
            <div id="${collapseId}" class="collapse show">
                ${summary ? `<div class="small text-white-50 mt-2">${summary}</div>` : ''}
                ${contextNote}
                ${entriesHtml}
            </div>
        `;
    };

    fetch(`/purchase-order/details/${poId}${query ? `?${query}` : ''}`)
        .then(response => response.json())
        .then(data => {
            const titleEl = document.getElementById('poModalLabel');
            const orderDateEl = document.getElementById('modal-po-order-date');
            const receivedDateEl = document.getElementById('modal-po-received-date');
            const statusEl = document.getElementById('modal-po-status');
            const qtyLabelEl = document.querySelector('#poDetailsModal #modal-po-qty-label');

            // Title stays generic
            titleEl.innerText = data.modal_title || "Purchase Order Details";

            // Dates
            orderDateEl.innerText = data.created_at || '-';
            receivedDateEl.innerText = data.received_at || '-';

            // Status badge
            statusEl.innerText = data.status;
            statusEl.className = `badge ${data.status_class}`;

            if ((data.mode || '').toUpperCase() === 'IN') {
                qtyLabelEl.innerText = 'Received Qty';
            } else if ((data.mode || '').toUpperCase() === 'ORDER') {
                qtyLabelEl.innerText = 'Ordered Qty';
            } else {
                qtyLabelEl.innerText = 'Qty';
            }

            // Other header fields
            document.getElementById('modal-po-number').innerText = data.po_number;
            document.getElementById('modal-po-vendor').innerText = data.vendor_name;
            document.getElementById('modal-po-total').innerHTML = formatPeso(data.total_amount || 0);
            renderMovementDetails(data.movement_details);

            // Itemized list
            const list = document.getElementById('po-items-list');
            list.innerHTML = data.items.map(item => `
                <tr>
                    <td>${escapeHtml(item.name)}</td>
                    <td class="text-center">${item.quantity_ordered}</td>
                    <td class="text-end">${formatPeso(item.unit_price)}</td>
                    <td class="text-end text-info">${formatPeso(item.subtotal)}</td>
                </tr>
            `).join('');

            new bootstrap.Modal(document.getElementById('poDetailsModal')).show();
        })
        .catch(err => {
            console.error("Error fetching PO details:", err);
            alert("Failed to load order details. Please try again.");
        });
}

// --- DEBT TAB STATE ---
let debtAllCustomers = [];       // full server response, never mutated
let debtActiveFilter = 'unpaid'; // default view is unpaid only
let debtFlatpickr = null; 

document.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('debt-date-range')) return;

    // Init Flatpickr in range mode on the debt tab input
    debtFlatpickr = flatpickr('#debt-date-range', {
        mode: 'range',
        dateFormat: 'Y-m-d',        // what gets sent to the server
        altInput: true,
        altFormat: 'M j, Y',        // what the user sees
        allowInput: false,
        disableMobile: false,
        onClose: function(selectedDates) {
            // Auto-apply when user finishes picking both dates
            if (selectedDates.length === 2) {
                applyDebtDateFilter();
            }
        }
    });
});

document.querySelector('[data-bs-target="#debt-audit-tab"]')?.addEventListener('shown.bs.tab', function () {
    // Only fetch if not already loaded (no date filter applied yet)
    if (debtAllCustomers.length === 0) {
        loadDebtSummary();
    }
});

function loadDebtSummary(startDate = null, endDate = null) {
    const tbody = document.getElementById('debt-audit-body');
    tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-4"><i class="bi bi-hourglass-split me-1"></i> Loading...</td></tr>';

    let url = '/api/debt/summary';
    const params = [];
    if (startDate) params.push(`start_date=${startDate}`);
    if (endDate)   params.push(`end_date=${endDate}`);
    if (params.length) url += '?' + params.join('&');

    fetch(url)
        .then(r => r.json())
        .then(data => {
            debtAllCustomers = data.sales || [];
            renderDebtTable();
        })
        .catch(() => {
            tbody.innerHTML = '<tr><td colspan="8" class="text-center text-danger py-4">Failed to load data.</td></tr>';
        });
}

function setDebtFilter(filter, btn) {
    debtActiveFilter = filter;

    // Update button active state
    document.querySelectorAll('#debt-status-toggle .btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    renderDebtTable();
}

function applyDebtDateFilter() {
    if (!debtFlatpickr) return;

    const selected = debtFlatpickr.selectedDates;

    // Need both dates selected before fetching
    if (selected.length < 2) return;

    const fmt = d => {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
    };
    const start = fmt(selected[0]);
    const end   = fmt(selected[1]);

    debtAllCustomers = [];
    loadDebtSummary(start, end);
}

function clearDebtDateFilter() {
    if (debtFlatpickr) debtFlatpickr.clear();
    debtAllCustomers = [];
    loadDebtSummary();
}

// --- AUDIT TRAIL STATE ---
let auditPage        = 1;
let auditType        = null;   // null = all
let auditFlatpickr   = null;
let auditStartDate   = null;
let auditEndDate     = null;
let auditHasDiscount = false;

document.querySelector('[data-bs-target="#audit-tab"]')?.addEventListener('shown.bs.tab', function () {
    if (auditPage === 1 && document.getElementById('audit-trail-body').textContent.includes('Loading')) {
        loadAuditTrail();
    }
});

document.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('audit-date-range')) return;

    auditFlatpickr = flatpickr('#audit-date-range', {
        mode: 'range',
        dateFormat: 'Y-m-d',
        altInput: true,
        altFormat: 'M j, Y',
        allowInput: false,
        onClose: function(selectedDates) {
            if (selectedDates.length === 2) {
                const fmt = d => {
                    const y   = d.getFullYear();
                    const m   = String(d.getMonth() + 1).padStart(2, '0');
                    const day = String(d.getDate()).padStart(2, '0');
                    return `${y}-${m}-${day}`;
                };
                auditStartDate = fmt(selectedDates[0]);
                auditEndDate   = fmt(selectedDates[1]);
                auditPage = 1;
                loadAuditTrail();
            }
        }
    });

    document.getElementById('audit-discount-filter').addEventListener('change', function () {
        auditHasDiscount = this.checked;
        auditPage = 1;
        loadAuditTrail();
    });
});

function setAuditType(type, btn) {
    auditType = type;
    auditPage = 1;
    document.querySelectorAll('#audit-type-toggle .btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    loadAuditTrail();
}

function clearAuditDateFilter() {
    if (auditFlatpickr) auditFlatpickr.clear();
    document.getElementById('audit-discount-filter').checked = false;
    auditHasDiscount = false;
    auditStartDate = null;
    auditEndDate   = null;
    auditPage      = 1;
    loadAuditTrail();
}

function auditChangePage(direction) {
    auditPage += direction;
    loadAuditTrail();
}

function loadAuditTrail() {
    const tbody = document.getElementById('audit-trail-body');
    tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-4"><i class="bi bi-hourglass-split me-1"></i> Loading...</td></tr>';

    const params = [`page=${auditPage}`];
    if (auditStartDate) params.push(`start_date=${auditStartDate}`);
    if (auditEndDate)   params.push(`end_date=${auditEndDate}`);
    if (auditType)      params.push(`type=${auditType}`);
    if (auditHasDiscount) params.push('has_discount=1');

    fetch(`/api/audit/trail?${params.join('&')}`)
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                renderTableMessage(tbody, 8, data.error, 'text-center text-danger py-4');
                return;
            }
            renderAuditTrail(data);
        })
        .catch(() => {
            renderTableMessage(tbody, 8, 'Failed to load data.', 'text-center text-danger py-4');
        });
}

function renderAuditTrail(data) {
    const tbody      = document.getElementById('audit-trail-body');
    const summaryEl  = document.getElementById('audit-summary');
    const pageLabel  = document.getElementById('audit-page-label');
    const paginationRow = document.getElementById('audit-pagination-row');
    const prevBtn    = document.getElementById('audit-prev-btn');
    const nextBtn    = document.getElementById('audit-next-btn');

    const auditSummary = `${data.total.toLocaleString()} total entries`;
    summaryEl.replaceChildren(document.createTextNode(auditSummary));
    if (auditHasDiscount) {
        summaryEl.appendChild(document.createTextNode(' '));
        const chip = document.createElement('span');
        chip.className = 'filter-chip filter-chip-audit';
        chip.textContent = 'Discounted sales only';
        summaryEl.appendChild(chip);
    }

    // Pagination controls
    if (data.total_pages > 1) {
        paginationRow.style.display = 'flex';
        pageLabel.className = 'page-indicator small';
        pageLabel.textContent = `Page ${data.page} of ${data.total_pages}`;
        prevBtn.disabled = data.page <= 1;
        nextBtn.disabled = data.page >= data.total_pages;
    } else {
        paginationRow.style.display = 'none';
    }

    if (!data.rows || data.rows.length === 0) {
        renderTableMessage(tbody, 8, 'No records found.', 'text-center py-4');
        return;
    }

    const typeBadge = {
        'OUT':    ['badge rounded-pill bg-danger audit-badge', 'bi bi-box-arrow-up me-1', 'OUT'],
        'IN':     ['badge rounded-pill bg-success audit-badge', 'bi bi-box-arrow-in-down me-1', 'IN'],
        'ORDER':  ['badge rounded-pill bg-info text-dark audit-badge', 'bi bi-clipboard-check-fill me-1', 'ORDER'],
    };

    tbody.replaceChildren();

    data.rows.forEach((log) => {
        const row = document.createElement('tr');
        const type = (log.transaction_type || '').toUpperCase();
        const badgeInfo = typeBadge[type] || ['badge rounded-pill bg-secondary audit-badge', '', log.transaction_type || '-'];
        const reasonText = log.change_reason
            ? (log.change_reason === 'WALKIN_PURCHASE' ? 'WALK IN PURCHASE' : log.change_reason.replace(/_/g, ' '))
            : '-';
        const summary = log.items_summary
            ? (log.items_summary.length > 40 ? log.items_summary.slice(0, 40) + '...' : log.items_summary)
            : '-';

        const dateCell = document.createElement('td');
        dateCell.className = 'text-white-50 small';
        dateCell.textContent = log.transaction_date || '';

        const summaryCell = document.createElement('td');
        summaryCell.className = 'audit-summary-cell';
        const summaryText = document.createElement('div');
        summaryText.className = 'fw-bold text-white summary-text';
        summaryText.textContent = summary;
        summaryCell.appendChild(summaryText);

        const typeCell = document.createElement('td');
        typeCell.className = 'text-center';
        const typePill = document.createElement('span');
        typePill.className = badgeInfo[0];
        if (badgeInfo[1]) {
            const icon = document.createElement('i');
            icon.className = badgeInfo[1];
            typePill.appendChild(icon);
            typePill.appendChild(document.createTextNode(` ${badgeInfo[2]}`));
        } else {
            typePill.textContent = badgeInfo[2];
        }
        typeCell.appendChild(typePill);

        const reasonCell = document.createElement('td');
        reasonCell.className = 'audit-reason-cell';
        const reasonSpan = document.createElement('span');
        reasonSpan.className = 'text-white-50 small fst-italic fw-bold';
        reasonSpan.textContent = reasonText;
        reasonCell.appendChild(reasonSpan);

        const qtyCell = document.createElement('td');
        qtyCell.className = 'text-center fw-bold';
        qtyCell.textContent = String(log.total_qty ?? '');

        const refCell = document.createElement('td');
        refCell.className = 'audit-reference-cell';
        if (log.reference_type === 'MANUAL_ADJUSTMENT' && log.audit_group_id) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'btn btn-sm btn-outline-success border-0';
            btn.style.maxWidth = '100%';
            btn.style.overflow = 'hidden';
            btn.style.textOverflow = 'ellipsis';
            btn.style.whiteSpace = 'nowrap';
            const icon = document.createElement('i');
            icon.className = 'bi bi-box-arrow-in-down me-1';
            btn.appendChild(icon);
            btn.appendChild(document.createTextNode('Manual IN'));
            btn.addEventListener('click', () => viewManualInDetails(log.audit_group_id));
            refCell.appendChild(btn);
        } else if (log.reference_id) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.style.maxWidth = '100%';
            btn.style.overflow = 'hidden';
            btn.style.textOverflow = 'ellipsis';
            btn.style.whiteSpace = 'nowrap';
            if (log.reference_type === 'PURCHASE_ORDER') {
                btn.className = 'btn btn-sm btn-outline-warning border-0';
                const icon = document.createElement('i');
                icon.className = 'bi bi-box-seam me-1';
                btn.appendChild(icon);
                btn.appendChild(document.createTextNode(log.po_number || log.reference_id));
                btn.addEventListener('click', () => viewOrder(log.reference_id, log.transaction_date_raw || '', log.change_reason || '', log.transaction_type || ''));
            } else if (log.reference_type === 'ITEM_CATALOG') {
                btn.className = 'btn btn-sm btn-outline-success border-0';
                const icon = document.createElement('i');
                icon.className = 'bi bi-box-seam me-1';
                btn.appendChild(icon);
                btn.appendChild(document.createTextNode(' New Item'));
                btn.addEventListener('click', () => viewItemCreated(log.reference_id));
            } else {
                btn.className = 'btn btn-sm btn-outline-danger border-0';
                const icon = document.createElement('i');
                icon.className = 'bi bi-receipt-cutoff me-1';
                btn.appendChild(icon);
                btn.appendChild(document.createTextNode(log.sales_number || log.reference_id));
                btn.addEventListener('click', () => viewSaleDetails(log.reference_id, log.sales_number || log.reference_id));
            }
            refCell.appendChild(btn);
        } else {
            const muted = document.createElement('span');
            muted.className = 'text-muted';
            muted.textContent = '---';
            refCell.appendChild(muted);
        }

        const notesCell = document.createElement('td');
        notesCell.className = 'text-white-50 small audit-notes-cell';
        if (log.notes) {
            const noteSpan = document.createElement('span');
            noteSpan.className = 'text-info';
            noteSpan.style.display = 'block';
            noteSpan.style.overflow = 'hidden';
            noteSpan.style.textOverflow = 'ellipsis';
            noteSpan.style.whiteSpace = 'nowrap';
            noteSpan.title = log.notes;
            noteSpan.innerHTML = '<i class="bi bi-chat-left-text me-1"></i>';
            noteSpan.appendChild(document.createTextNode(log.notes));
            notesCell.appendChild(noteSpan);
        } else {
            const muted = document.createElement('span');
            muted.className = 'text-muted';
            muted.textContent = '-';
            notesCell.appendChild(muted);
        }

        const staffCell = document.createElement('td');
        staffCell.className = 'text-white-50 small audit-staff-cell';
        staffCell.innerHTML = '<i class="bi bi-person me-1"></i>';
        staffCell.appendChild(document.createTextNode(log.user_name || '-'));

        row.appendChild(dateCell);
        row.appendChild(summaryCell);
        row.appendChild(typeCell);
        row.appendChild(reasonCell);
        row.appendChild(qtyCell);
        row.appendChild(refCell);
        row.appendChild(notesCell);
        row.appendChild(staffCell);
        tbody.appendChild(row);
    });
}

// --- PAYABLES AUDIT TAB STATE ---
let payablesAuditPage = 1;
let payablesAuditStartDate = null;
let payablesAuditEndDate = null;
let payablesAuditEventType = '';
let payablesAuditSourceType = '';
let payablesAuditPayeeSearch = '';
let payablesAuditChequeNoSearch = '';
let payablesAuditSearchTimeout = null;
let payablesAuditFlatpickr = null;
let payablesAuditRowsById = {};

document.querySelector('[data-bs-target="#payables-audit-tab"]')?.addEventListener('shown.bs.tab', function () {
    if (document.getElementById('payables-audit-body').textContent.includes('Loading')) {
        loadPayablesAudit();
    }
});

document.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('payables-audit-date-range')) return;

    payablesAuditFlatpickr = flatpickr('#payables-audit-date-range', {
        mode: 'range',
        dateFormat: 'Y-m-d',
        altInput: true,
        altFormat: 'M j, Y',
        allowInput: false,
        onClose: function(selectedDates) {
            if (selectedDates.length === 2) {
                const fmt = d => {
                    const y = d.getFullYear();
                    const m = String(d.getMonth() + 1).padStart(2, '0');
                    const day = String(d.getDate()).padStart(2, '0');
                    return `${y}-${m}-${day}`;
                };
                payablesAuditStartDate = fmt(selectedDates[0]);
                payablesAuditEndDate = fmt(selectedDates[1]);
                payablesAuditPage = 1;
                loadPayablesAudit();
            }
        }
    });

    document.getElementById('payables-audit-event-filter')?.addEventListener('change', function () {
        payablesAuditEventType = this.value || '';
        payablesAuditPage = 1;
        loadPayablesAudit();
    });

    document.getElementById('payables-audit-source-filter')?.addEventListener('change', function () {
        payablesAuditSourceType = this.value || '';
        payablesAuditPage = 1;
        loadPayablesAudit();
    });

    document.getElementById('payables-audit-payee-search')?.addEventListener('input', function () {
        clearTimeout(payablesAuditSearchTimeout);
        payablesAuditSearchTimeout = setTimeout(() => {
            payablesAuditPayeeSearch = this.value.trim();
            payablesAuditPage = 1;
            loadPayablesAudit();
        }, 400);
    });

    document.getElementById('payables-audit-cheque-search')?.addEventListener('input', function () {
        clearTimeout(payablesAuditSearchTimeout);
        payablesAuditSearchTimeout = setTimeout(() => {
            payablesAuditChequeNoSearch = this.value.trim();
            payablesAuditPage = 1;
            loadPayablesAudit();
        }, 400);
    });

    if (document.getElementById('payables-audit-tab')?.classList.contains('show')) {
        loadPayablesAudit();
    }
});

function clearPayablesAuditFilters() {
    if (payablesAuditFlatpickr) payablesAuditFlatpickr.clear();
    payablesAuditStartDate = null;
    payablesAuditEndDate = null;
    payablesAuditEventType = '';
    payablesAuditSourceType = '';
    payablesAuditPayeeSearch = '';
    payablesAuditChequeNoSearch = '';
    payablesAuditPage = 1;

    document.getElementById('payables-audit-event-filter').value = '';
    document.getElementById('payables-audit-source-filter').value = '';
    document.getElementById('payables-audit-payee-search').value = '';
    document.getElementById('payables-audit-cheque-search').value = '';

    loadPayablesAudit();
}

function payablesAuditChangePage(direction) {
    payablesAuditPage += direction;
    loadPayablesAudit();
}

function loadPayablesAudit() {
    const tbody = document.getElementById('payables-audit-body');
    tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-4"><i class="bi bi-hourglass-split me-1"></i> Loading...</td></tr>';

    const params = [`page=${payablesAuditPage}`];
    if (payablesAuditStartDate) params.push(`start_date=${encodeURIComponent(payablesAuditStartDate)}`);
    if (payablesAuditEndDate) params.push(`end_date=${encodeURIComponent(payablesAuditEndDate)}`);
    if (payablesAuditEventType) params.push(`event_type=${encodeURIComponent(payablesAuditEventType)}`);
    if (payablesAuditSourceType) params.push(`source_type=${encodeURIComponent(payablesAuditSourceType)}`);
    if (payablesAuditPayeeSearch) params.push(`payee_search=${encodeURIComponent(payablesAuditPayeeSearch)}`);
    if (payablesAuditChequeNoSearch) params.push(`cheque_no_search=${encodeURIComponent(payablesAuditChequeNoSearch)}`);

    fetch(`/api/payables/audit?${params.join('&')}`)
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                renderTableMessage(tbody, 6, data.error, 'text-center text-danger py-4');
                return;
            }
            renderPayablesAudit(data);
        })
        .catch(() => {
            renderTableMessage(tbody, 6, 'Failed to load data.', 'text-center text-danger py-4');
        });
}

function renderPayablesAudit(data) {
    const tbody = document.getElementById('payables-audit-body');
    const summaryEl = document.getElementById('payables-audit-summary');
    const pageLabel = document.getElementById('payables-audit-page-label');
    const paginationRow = document.getElementById('payables-audit-pagination-row');
    const prevBtn = document.getElementById('payables-audit-prev-btn');
    const nextBtn = document.getElementById('payables-audit-next-btn');

    summaryEl.textContent = `${Number(data.total || 0).toLocaleString()} total entries`;

    if (Number(data.total_pages || 0) > 1) {
        paginationRow.style.display = 'flex';
        pageLabel.className = 'page-indicator small';
        pageLabel.textContent = `Page ${data.page} of ${data.total_pages}`;
        prevBtn.disabled = data.page <= 1;
        nextBtn.disabled = data.page >= data.total_pages;
    } else {
        paginationRow.style.display = 'none';
    }

    if (!data.rows || data.rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="text-center py-4">No records found.</td></tr>';
        return;
    }

    const sourceBadge = {
        'PO_DELIVERY': ['badge bg-info text-dark', 'PO Delivery'],
        'MANUAL': ['badge bg-secondary', 'Manual'],
    };

    const eventBadge = {
        'PO_PAYABLE_CREATED': ['badge bg-primary', 'Payable Created'],
        'MANUAL_PAYABLE_CREATED': ['badge bg-primary', 'Payable Created'],
        'CHEQUE_ISSUED': ['badge bg-success', 'Cheque Issued'],
        'CHEQUE_STATUS_UPDATED': ['badge bg-warning text-dark', 'Status Updated'],
    };
    payablesAuditRowsById = {};
    (data.rows || []).forEach((row) => {
        payablesAuditRowsById[String(row.id)] = row;
    });

    tbody.replaceChildren();

    data.rows.forEach((log) => {
        const row = document.createElement('tr');

        const createdAtCell = document.createElement('td');
        createdAtCell.className = 'text-white-50 small';
        createdAtCell.textContent = log.created_at || '-';

        const payeeCell = document.createElement('td');
        const payeeDiv = document.createElement('div');
        payeeDiv.className = 'fw-bold text-white';
        payeeDiv.textContent = log.payee_name_snapshot || '-';
        payeeCell.appendChild(payeeDiv);

        const sourceCell = document.createElement('td');
        const sourceInfo = sourceBadge[log.source_type] || ['badge bg-dark border border-secondary', log.source_label || log.source_type || '-'];
        const sourceSpan = document.createElement('span');
        sourceSpan.className = sourceInfo[0];
        sourceSpan.textContent = sourceInfo[1];
        sourceCell.appendChild(sourceSpan);

        const eventCell = document.createElement('td');
        const eventInfo = eventBadge[log.event_type] || ['badge bg-dark border border-secondary', log.event_label || log.event_type || '-'];
        const eventSpan = document.createElement('span');
        eventSpan.className = eventInfo[0];
        eventSpan.textContent = eventInfo[1];
        eventCell.appendChild(eventSpan);

        const refCell = document.createElement('td');
        const refButton = document.createElement('button');
        refButton.type = 'button';
        refButton.className = 'btn btn-sm btn-outline-warning border-0';
        refButton.style.maxWidth = '100%';
        refButton.style.overflow = 'hidden';
        refButton.style.textOverflow = 'ellipsis';
        refButton.style.whiteSpace = 'nowrap';
        refButton.innerHTML = '<i class="bi bi-search me-1"></i>';
        refButton.appendChild(document.createTextNode(
            log.cheque_no_snapshot || log.po_number_snapshot || 'View Details'
        ));
        refButton.addEventListener('click', () => openPayablesAuditDetails(log.id));
        refCell.appendChild(refButton);

        const amountCell = document.createElement('td');
        amountCell.className = 'text-end fw-bold';
        if (log.amount_snapshot > 0) {
            amountCell.textContent = `₱${Number(log.amount_snapshot).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        } else {
            const muted = document.createElement('span');
            muted.className = 'text-muted';
            muted.textContent = '-';
            amountCell.appendChild(muted);
        }

        const createdByCell = document.createElement('td');
        createdByCell.className = 'text-white-50 small';
        createdByCell.innerHTML = '<i class="bi bi-person me-1"></i>';
        createdByCell.appendChild(document.createTextNode(log.created_by_username || 'System'));

        row.appendChild(createdAtCell);
        row.appendChild(payeeCell);
        row.appendChild(sourceCell);
        row.appendChild(eventCell);
        row.appendChild(refCell);
        row.appendChild(amountCell);
        row.appendChild(createdByCell);
        tbody.appendChild(row);
    });
}

function viewPayablesAuditDetails(auditId) {
    const data = payablesAuditRowsById[String(auditId)];
    const body = document.getElementById('payablesAuditDetailsBody');
    if (!body || !data) {
        return;
    }

    const statusText = data.old_status && data.new_status
        ? `${escapeHtml(data.old_status)} -> ${escapeHtml(data.new_status)}`
        : (data.new_status ? escapeHtml(data.new_status) : '-');
    body.replaceChildren();

    const row = document.createElement('div');
    row.className = 'row g-3';
    const fields = [
        ['Date & Time', data.created_at || '-', 'col-md-6', 'fw-bold'],
        ['Staff', data.created_by_username || 'System', 'col-md-6', 'fw-bold'],
        ['Payee / Vendor', data.payee_name_snapshot || '-', 'col-md-6', 'fw-bold text-warning'],
        ['Source', data.source_label || '-', 'col-md-6', 'fw-bold'],
        ['Event', data.event_label || '-', 'col-md-6', 'fw-bold'],
        ['Amount', data.amount_snapshot > 0 ? formatPesoText(data.amount_snapshot) : '-', 'col-md-6', 'fw-bold text-info'],
        ['PO Number', data.po_number_snapshot || '-', 'col-md-6', 'fw-bold'],
        ['Cheque Number', data.cheque_no_snapshot || '-', 'col-md-6', 'fw-bold'],
        ['Status', statusText, 'col-12', 'fw-bold'],
        ['Notes', data.notes || '-', 'col-12', 'text-info'],
    ];

    fields.forEach(([label, value, colClass, valueClass]) => {
        const col = document.createElement('div');
        col.className = colClass;
        const labelEl = document.createElement('div');
        labelEl.className = 'small text-white-50 mb-1';
        labelEl.textContent = label;
        const valueEl = document.createElement('div');
        valueEl.className = valueClass;
        valueEl.textContent = value;
        col.appendChild(labelEl);
        col.appendChild(valueEl);
        row.appendChild(col);
    });

    body.appendChild(row);

    new bootstrap.Modal(document.getElementById('payablesAuditDetailsModal')).show();
}

function openPayablesAuditDetails(auditId) {
    const data = payablesAuditRowsById[String(auditId)];
    const body = document.getElementById('payablesAuditDetailsBody');
    if (!body || !data) {
        return;
    }

    const statusText = data.old_status && data.new_status
        ? `${escapeHtml(data.old_status)} -> ${escapeHtml(data.new_status)}`
        : (data.new_status ? escapeHtml(data.new_status) : '-');
    body.replaceChildren();

    const header = document.createElement('div');
    header.className = 'mb-3';
    const title = document.createElement('div');
    title.className = 'text-white fw-bold fs-5';
    title.textContent = data.event_label || 'Payables Audit Event';
    const meta = document.createElement('div');
    meta.className = 'text-white-50 small mt-1';
    meta.innerHTML = '<i class="bi bi-clock me-1"></i><span class="audit-date"></span><span class="ms-3"><i class="bi bi-person me-1"></i><span class="audit-user"></span></span>';
    meta.querySelector('.audit-date').textContent = data.created_at || '-';
    meta.querySelector('.audit-user').textContent = data.created_by_username || 'System';
    header.appendChild(title);
    header.appendChild(meta);
    body.appendChild(header);

    const buildSection = (titleClass, iconClass, titleText, rows) => {
        const section = document.createElement('div');
        section.className = 'border border-secondary rounded p-3 mb-3';
        const sectionTitle = document.createElement('div');
        sectionTitle.className = `${titleClass} fw-bold mb-2`;
        const icon = document.createElement('i');
        icon.className = `${iconClass} me-1`;
        sectionTitle.appendChild(icon);
        sectionTitle.appendChild(document.createTextNode(titleText));
        section.appendChild(sectionTitle);
        rows.forEach(([label, value, valueClass]) => {
            const labelEl = document.createElement('div');
            labelEl.className = 'small text-white-50 mb-1';
            labelEl.textContent = label;
            const valueEl = document.createElement('div');
            valueEl.className = valueClass;
            valueEl.textContent = value;
            section.appendChild(labelEl);
            section.appendChild(valueEl);
        });
        return section;
    };

    body.appendChild(buildSection('text-warning', 'bi bi-building', 'Payable Reference', [
        ['Payee / Vendor', data.payee_name_snapshot || '-', 'fw-bold text-warning mb-2'],
        ['Source', data.source_label || '-', 'fw-bold mb-2'],
        ['Amount', data.amount_snapshot > 0 ? formatPesoText(data.amount_snapshot) : '-', 'fw-bold text-info mb-0'],
    ]));

    body.appendChild(buildSection('text-info', 'bi bi-link-45deg', 'Linked Reference', [
        ['PO Number', data.po_number_snapshot || '-', 'fw-bold mb-2'],
        ['Cheque Number', data.cheque_no_snapshot || '-', 'fw-bold text-info mb-0'],
    ]));

    const changeSection = document.createElement('div');
    changeSection.className = 'border border-secondary rounded p-3';
    const changeTitle = document.createElement('div');
    changeTitle.className = 'text-success fw-bold mb-2';
    changeTitle.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i> Change Details';
    changeSection.appendChild(changeTitle);
    [
        ['Status', statusText, 'fw-bold mb-2'],
        ['Notes', data.notes || '-', 'text-info'],
    ].forEach(([label, value, cls]) => {
        const labelEl = document.createElement('div');
        labelEl.className = 'small text-white-50 mb-1';
        labelEl.textContent = label;
        const valueEl = document.createElement('div');
        valueEl.className = cls;
        valueEl.textContent = value;
        changeSection.appendChild(labelEl);
        changeSection.appendChild(valueEl);
    });
    body.appendChild(changeSection);

    new bootstrap.Modal(document.getElementById('payablesAuditDetailsModal')).show();
}

// --- SALES TAB STATE ---
let salesPage      = 1;
let salesStartDate = null;
let salesEndDate   = null;
let salesSearch    = '';
let salesHasDiscount = false;
let salesPaymentStatus = null;
let salesSearchTimeout;
let salesFlatpickr = null;

document.querySelector('[data-bs-target="#sales-tab"]')?.addEventListener('shown.bs.tab', function () {
    if (salesPage === 1 && document.getElementById('sales-admin-body').textContent.includes('Loading')) {
        loadSalesAdmin();
    }
});

document.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('sales-date-range')) return;

    salesFlatpickr = flatpickr('#sales-date-range', {
        mode: 'range',
        dateFormat: 'Y-m-d',
        altInput: true,
        altFormat: 'M j, Y',
        allowInput: false,
        onClose: function(selectedDates) {
            if (selectedDates.length === 2) {
                const fmt = d => {
                    const y   = d.getFullYear();
                    const m   = String(d.getMonth() + 1).padStart(2, '0');
                    const day = String(d.getDate()).padStart(2, '0');
                    return `${y}-${m}-${day}`;
                };
                salesStartDate = fmt(selectedDates[0]);
                salesEndDate   = fmt(selectedDates[1]);
                salesPage = 1;
                loadSalesAdmin();
            }
        }
    });

    document.getElementById('sales-search-input').addEventListener('input', function() {
        clearTimeout(salesSearchTimeout);
        salesSearchTimeout = setTimeout(() => {
            salesSearch = this.value.trim();
            salesPage = 1;
            loadSalesAdmin();
        }, 500);
    });

    document.getElementById('sales-discount-filter').addEventListener('change', function () {
        salesHasDiscount = this.checked;
        salesPage = 1;
        loadSalesAdmin();
    });
});

function clearSalesFilters() {
    if (salesFlatpickr) salesFlatpickr.clear();
    document.getElementById('sales-search-input').value = '';
    document.getElementById('sales-discount-filter').checked = false;
    document.querySelectorAll('#sales-status-toggle .btn').forEach(b => b.classList.remove('active'));
    document.querySelector('#sales-status-toggle .btn').classList.add('active');
    salesStartDate = null;
    salesEndDate   = null;
    salesSearch    = '';
    salesHasDiscount = false;
    salesPaymentStatus = null;
    salesPage      = 1;
    loadSalesAdmin();
}

function setSalesStatus(status, btn) {
    salesPaymentStatus = status;
    salesPage = 1;
    document.querySelectorAll('#sales-status-toggle .btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    loadSalesAdmin();
}

function salesChangePage(direction) {
    salesPage += direction;
    loadSalesAdmin();
}

function loadSalesAdmin() {
    const tbody = document.getElementById('sales-admin-body');
    tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-4"><i class="bi bi-hourglass-split me-1"></i> Loading...</td></tr>';

    const params = [`page=${salesPage}`];
    if (salesStartDate) params.push(`start_date=${salesStartDate}`);
    if (salesEndDate)   params.push(`end_date=${salesEndDate}`);
    if (salesSearch)    params.push(`search=${encodeURIComponent(salesSearch)}`);
    if (salesHasDiscount) params.push('has_discount=1');
    if (salesPaymentStatus) params.push(`payment_status=${encodeURIComponent(salesPaymentStatus)}`);

    fetch(`/api/admin/sales?${params.join('&')}`)
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                renderTableMessage(tbody, 7, data.error, 'text-center text-danger py-4');
                return;
            }
            renderSalesAdmin(data);
        })
        .catch(() => {
            renderTableMessage(tbody, 7, 'Failed to load data.', 'text-center text-danger py-4');
        });
}

function renderSalesAdmin(data) {
    const tbody         = document.getElementById('sales-admin-body');
    const summaryEl     = document.getElementById('sales-summary');
    const pageLabel     = document.getElementById('sales-page-label');
    const paginationRow = document.getElementById('sales-pagination-row');
    const prevBtn       = document.getElementById('sales-prev-btn');
    const nextBtn       = document.getElementById('sales-next-btn');

    const salesSummary = `${data.total.toLocaleString()} total sales`;
    const salesStatusChipClass = salesPaymentStatus === 'Paid'
        ? 'filter-chip-sales-paid'
        : salesPaymentStatus === 'Partial'
            ? 'filter-chip-sales-partial'
            : salesPaymentStatus === 'Unresolved'
                ? 'filter-chip-sales-unpaid'
                : '';
    summaryEl.replaceChildren(document.createTextNode(salesSummary));
    if (salesPaymentStatus) {
        summaryEl.appendChild(document.createTextNode(' '));
        const statusChip = document.createElement('span');
        statusChip.className = `filter-chip ${salesStatusChipClass}`.trim();
        statusChip.textContent = salesPaymentStatus === 'Unresolved' ? 'Unpaid only' : `${salesPaymentStatus} only`;
        summaryEl.appendChild(statusChip);
    }
    if (salesHasDiscount) {
        summaryEl.appendChild(document.createTextNode(' '));
        const discountChip = document.createElement('span');
        discountChip.className = 'filter-chip';
        discountChip.textContent = 'Discounted sales only';
        summaryEl.appendChild(discountChip);
    }

    if (data.total_pages > 1) {
        paginationRow.style.display = 'flex';
        pageLabel.className = 'page-indicator small';
        pageLabel.textContent = `Page ${data.page} of ${data.total_pages}`;
        prevBtn.disabled = data.page <= 1;
        nextBtn.disabled = data.page >= data.total_pages;
    } else {
        paginationRow.style.display = 'none';
    }

    if (!data.rows || data.rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center py-4">No records found.</td></tr>';
        return;
    }

    const statusBadge = {
        'Paid':       ['badge bg-success', 'Paid'],
        'Partial':    ['badge bg-warning text-dark', 'Partial'],
        'Unresolved': ['badge bg-danger', 'Unpaid'],
    };

    tbody.replaceChildren();

    data.rows.forEach((sale) => {
        const row = document.createElement('tr');

        const dateCell = document.createElement('td');
        dateCell.className = 'text-white-50 small';
        dateCell.textContent = sale.transaction_date || '';

        const salesNumberCell = document.createElement('td');
        salesNumberCell.className = 'fw-bold text-warning';
        salesNumberCell.style.overflow = 'hidden';
        salesNumberCell.style.textOverflow = 'ellipsis';
        salesNumberCell.style.whiteSpace = 'nowrap';
        salesNumberCell.textContent = sale.sales_number || '-';

        const customerCell = document.createElement('td');
        customerCell.style.overflow = 'hidden';
        customerCell.style.textOverflow = 'ellipsis';
        customerCell.style.whiteSpace = 'nowrap';
        customerCell.textContent = sale.customer_name || 'Walk-in';

        const paymentCell = document.createElement('td');
        const paymentBadge = document.createElement('span');
        paymentBadge.className = 'badge bg-dark border border-secondary text-info';
        paymentBadge.textContent = sale.payment_method_name || '-';
        paymentCell.appendChild(paymentBadge);

        const statusCell = document.createElement('td');
        const badgeInfo = statusBadge[sale.status] || ['badge bg-secondary', sale.status || '-'];
        const statusPill = document.createElement('span');
        statusPill.className = badgeInfo[0];
        statusPill.textContent = badgeInfo[1];
        statusCell.appendChild(statusPill);
        if (sale.has_refund) {
            const refundState = document.createElement('div');
            refundState.className = 'small text-danger mt-1 fw-bold';
            refundState.textContent = sale.refund_state || 'Refunded';
            statusCell.appendChild(refundState);
        }

        const amountCell = document.createElement('td');
        amountCell.className = 'fw-bold text-danger';
        amountCell.textContent = formatPesoText(parseFloat(sale.total_amount));
        if (sale.has_refund) {
            const netLine = document.createElement('div');
            netLine.className = 'small text-warning fw-semibold';
            netLine.textContent = `Net: ${formatPesoText(parseFloat(sale.net_amount))}`;
            const refundedLine = document.createElement('div');
            refundedLine.className = 'small text-danger';
            refundedLine.textContent = `Refunded: ${formatPesoText(parseFloat(sale.refunded_amount))}`;
            amountCell.appendChild(netLine);
            amountCell.appendChild(refundedLine);
        }

        const actionsCell = document.createElement('td');
        actionsCell.className = 'text-center actions-cell';
        const btnGroup = document.createElement('div');
        btnGroup.className = 'btn-group';

        const viewBtn = document.createElement('button');
        viewBtn.type = 'button';
        viewBtn.className = 'btn btn-sm btn-outline-info me-1';
        viewBtn.innerHTML = '<i class="bi bi-eye"></i>';
        viewBtn.addEventListener('click', () => viewSaleDetails(sale.id, sale.sales_number));

        const printLink = document.createElement('a');
        printLink.href = `/reports/sales-receipt/${sale.id}`;
        printLink.target = '_blank';
        printLink.className = 'btn btn-sm btn-outline-warning';
        printLink.innerHTML = '<i class="bi bi-printer"></i>';

        btnGroup.appendChild(viewBtn);
        btnGroup.appendChild(printLink);
        actionsCell.appendChild(btnGroup);

        row.appendChild(dateCell);
        row.appendChild(salesNumberCell);
        row.appendChild(customerCell);
        row.appendChild(paymentCell);
        row.appendChild(statusCell);
        row.appendChild(amountCell);
        row.appendChild(actionsCell);
        tbody.appendChild(row);
    });
}

function viewItemCreated(itemId) {
    const body = document.getElementById('itemCreatedBody');
    body.innerHTML = '<div class="text-center text-muted py-3"><i class="bi bi-hourglass-split me-1"></i> Loading...</div>';
    new bootstrap.Modal(document.getElementById('itemCreatedModal')).show();

    fetch(`/api/item/${itemId}`)
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                renderTextMessage(body, data.error, 'text-danger text-center py-2');
                return;
            }

            const markup = data.markup ? `${(parseFloat(data.markup) * 100).toFixed(2)}%` : '-';
            const rows = [
                ['Category', data.category || '-'],
                ['Description', data.description || '-'],
                ['Pack Size', data.pack_size || '-'],
                ['Vendor Price', data.vendor_price != null ? formatPesoText(data.vendor_price) : '-'],
                ['Cost per Piece', data.cost_per_piece != null ? formatPesoText(data.cost_per_piece) : '-'],
                ['Selling Price', data.a4s_selling_price != null ? formatPesoText(data.a4s_selling_price) : '-'],
                ['Markup', markup],
                ['Vendor', data.vendor || '-'],
            ];

            body.replaceChildren();

            const title = document.createElement('h6');
            title.className = 'text-success mb-3';
            title.innerHTML = '<i class="bi bi-box me-2"></i>';
            title.appendChild(document.createTextNode(data.name || 'Unknown Item'));

            const table = document.createElement('table');
            table.className = 'table table-dark table-sm mb-0';
            const tbody = document.createElement('tbody');

            rows.forEach(([label, value]) => {
                const row = document.createElement('tr');
                const labelCell = document.createElement('td');
                labelCell.className = 'text-white-50 small';
                labelCell.style.width = '40%';
                labelCell.textContent = label;
                const valueCell = document.createElement('td');
                valueCell.className = 'fw-bold';
                valueCell.textContent = value;
                row.appendChild(labelCell);
                row.appendChild(valueCell);
                tbody.appendChild(row);
            });

            table.appendChild(tbody);
            body.appendChild(title);
            body.appendChild(table);
        })
        .catch(() => {
            body.innerHTML = '<div class="text-danger text-center py-2">Failed to load item details.</div>';
        });
}

function renderDebtTable() {
    const tbody = document.getElementById('debt-audit-body');
    const summaryEl = document.getElementById('debt-audit-summary');

    const filtered = debtActiveFilter === 'all'
        ? debtAllCustomers
        : debtActiveFilter === 'paid'
            ? debtAllCustomers.filter(customer => customer.status === 'paid')
            : debtAllCustomers.filter(customer => customer.status !== 'paid');

    const totalCount  = debtAllCustomers.length;
    const openCount   = debtAllCustomers.filter(customer => customer.status !== 'paid').length;
    summaryEl.textContent = `${totalCount} customers - ${openCount} open`;

    if (filtered.length === 0) {
        renderTableMessage(tbody, 8, 'No records found.');
        return;
    }

    tbody.replaceChildren();

    filtered.forEach(sale => {
        const debtRecordLabel = sale.receipt_count > 1
            ? `${sale.receipt_count} receipts`
            : (sale.latest_sales_number || sale.sale_id || '1 receipt');
        const customerName = sale.customer_name || 'Walk-in';

        const statusBadge = {
            'paid':    ['badge bg-success', 'PAID IN FULL'],
            'partial': ['badge bg-warning text-dark', 'PARTIAL'],
            'unpaid':  ['badge bg-danger', 'UNPAID'],
        }[sale.status] || ['badge bg-secondary', sale.status || 'UNKNOWN'];

        const row = document.createElement('tr');
        row.className = `debt-parent-row ${sale.status === 'paid' ? 'opacity-75' : ''}`.trim();

        const customerCell = document.createElement('td');
        customerCell.className = 'fw-bold';
        customerCell.textContent = customerName;

        const receiptCell = document.createElement('td');
        receiptCell.className = 'text-warning';
        receiptCell.textContent = debtRecordLabel;

        const dateCell = document.createElement('td');
        dateCell.className = 'text-white-50 small';
        dateCell.textContent = sale.date || '';

        const totalCell = document.createElement('td');
        totalCell.className = 'text-end';
        totalCell.textContent = formatPesoText(sale.total_amount);

        const paidCell = document.createElement('td');
        paidCell.className = 'text-end text-success';
        paidCell.textContent = formatPesoText(sale.total_paid);

        const remainingCell = document.createElement('td');
        remainingCell.className = `text-end ${sale.remaining > 0 ? 'text-danger fw-bold' : 'text-muted'}`;
        remainingCell.textContent = formatPesoText(sale.remaining);

        const statusCell = document.createElement('td');
        statusCell.className = 'text-center';
        const badge = document.createElement('span');
        badge.className = statusBadge[0];
        badge.textContent = statusBadge[1];
        statusCell.appendChild(badge);

        const actionsCell = document.createElement('td');
        actionsCell.className = 'text-center actions-cell';
        const cluster = document.createElement('div');
        cluster.className = 'action-cluster';

        const viewBtn = document.createElement('button');
        viewBtn.type = 'button';
        viewBtn.className = 'btn btn-sm btn-outline-info view-debt-payments-btn';
        viewBtn.dataset.customerId = sale.customer_id || '';
        viewBtn.dataset.saleId = sale.sale_id;
        viewBtn.dataset.saleReceipt = sale.latest_sales_number || '';
        viewBtn.dataset.receiptCount = sale.receipt_count || 1;
        viewBtn.title = 'View payment history';
        viewBtn.innerHTML = '<i class="bi bi-eye"></i>';

        const printLink = document.createElement('a');
        printLink.href = sale.customer_id ? `/debt/statement/customer/${sale.customer_id}` : `/debt/statement/${sale.sale_id}`;
        printLink.target = '_blank';
        printLink.className = 'btn btn-sm btn-outline-warning';
        printLink.title = 'Print customer statement';
        printLink.innerHTML = '<i class="bi bi-printer"></i>';

        cluster.appendChild(viewBtn);
        cluster.appendChild(printLink);
        actionsCell.appendChild(cluster);

        row.appendChild(customerCell);
        row.appendChild(receiptCell);
        row.appendChild(dateCell);
        row.appendChild(totalCell);
        row.appendChild(paidCell);
        row.appendChild(remainingCell);
        row.appendChild(statusCell);
        row.appendChild(actionsCell);
        tbody.appendChild(row);
    });
}

document.addEventListener('click', (e) => {
    const viewDebtBtn = (e.target instanceof Element) ? e.target.closest('.view-debt-payments-btn') : null;
    if (!viewDebtBtn) return;
    const customerId = viewDebtBtn.dataset.customerId;
    const saleId = viewDebtBtn.dataset.saleId;
    const saleReceipt = viewDebtBtn.dataset.saleReceipt || '';
    const receiptCount = parseInt(viewDebtBtn.dataset.receiptCount || '1', 10);
    if (customerId) {
        showDebtPaymentsModalForCustomer(parseInt(customerId, 10), saleReceipt, receiptCount);
        return;
    }
    if (!saleId) return;

    showDebtPaymentsModal(parseInt(saleId, 10), saleReceipt);
});

function showDebtPaymentsModalForCustomer(customerId, saleReceipt = '', receiptCount = 1) {
    const body = document.getElementById('debt-payments-body');
    body.innerHTML = '<div class="text-center text-muted py-3"><i class="bi bi-hourglass-split me-1"></i> Loading payment history...</div>';
    new bootstrap.Modal(document.getElementById('debtPaymentsModal')).show();

    fetch(`/api/debt/customer/${customerId}/payments`)
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                renderTextMessage(body, data.error, 'text-danger text-center py-2');
                return;
            }

            const payments = data.payments || [];
            const customerName = data.customer?.customer_name || 'Customer';
            const remaining = parseFloat(data.summary?.remaining || 0);
            const receiptLabel = receiptCount > 1 ? `${receiptCount} active receipts` : (saleReceipt || '1 active receipt');
            body.replaceChildren();

            const header = document.createElement('div');
            header.className = 'mb-3';
            const customerRow = document.createElement('div');
            customerRow.appendChild(document.createTextNode('Customer '));
            const customerSpan = document.createElement('span');
            customerSpan.className = 'fw-semibold text-warning';
            customerSpan.textContent = customerName;
            customerRow.appendChild(customerSpan);
            const balanceRow = document.createElement('div');
            balanceRow.className = 'text-white-50 small';
            balanceRow.textContent = `${receiptLabel} - Running balance ${formatPesoText(remaining)}`;
            header.appendChild(customerRow);
            header.appendChild(balanceRow);

            if (!payments.length) {
                body.appendChild(header);
                const empty = document.createElement('div');
                empty.className = 'text-muted text-center py-2';
                empty.textContent = 'No payment entries recorded yet.';
                body.appendChild(empty);
                return;
            }

            body.appendChild(header);

            const tableWrap = document.createElement('div');
            tableWrap.className = 'table-responsive';
            const table = document.createElement('table');
            table.className = 'table table-dark table-sm table-hover mb-0 debt-payments-table';
            const thead = document.createElement('thead');
            thead.style.fontSize = '0.72rem';
            const headRow = document.createElement('tr');
            headRow.className = 'text-muted';
            [
                { text: 'Date Paid', className: 'date-paid-col' },
                { text: 'Receipt' },
                { text: 'Amount' },
                { text: 'Method', style: 'width: 18%;' },
                { text: 'Reference', style: 'width: 14%;' },
                { text: 'Staff', style: 'width: 15%;' },
                { text: 'Notes' },
            ].forEach((cellConfig) => {
                const th = document.createElement('th');
                th.textContent = cellConfig.text;
                if (cellConfig.className) th.className = cellConfig.className;
                if (cellConfig.style) th.style.cssText = cellConfig.style;
                headRow.appendChild(th);
            });
            thead.appendChild(headRow);

            const tbody = document.createElement('tbody');
            payments.forEach((p) => {
                const row = document.createElement('tr');
                row.style.fontSize = '0.83rem';

                const dateCell = document.createElement('td');
                dateCell.className = 'text-white-50';
                dateCell.textContent = p.paid_at_display || p.paid_at || '-';

                const receiptCell = document.createElement('td');
                receiptCell.className = 'text-warning';
                receiptCell.textContent = p.sales_number || `Sale #${p.sale_id}`;

                const amountCell = document.createElement('td');
                amountCell.className = 'fw-bold text-info';
                amountCell.textContent = formatPesoText(parseFloat(p.amount_paid || 0));

                const methodCell = document.createElement('td');
                const methodBadge = document.createElement('span');
                methodBadge.className = 'badge bg-dark border border-secondary text-info me-2';
                methodBadge.textContent = p.payment_method || '-';
                methodCell.appendChild(methodBadge);

                const referenceCell = document.createElement('td');
                referenceCell.className = 'text-white-50';
                referenceCell.textContent = p.reference_no || '-';

                const staffCell = document.createElement('td');
                staffCell.className = 'text-white-50';
                const staffIcon = document.createElement('i');
                staffIcon.className = 'bi bi-person me-1';
                staffCell.appendChild(staffIcon);
                staffCell.appendChild(document.createTextNode(p.paid_by || 'Staff'));

                const notesCell = document.createElement('td');
                notesCell.className = 'text-white-50 small fst-italic';
                notesCell.textContent = p.notes || '';

                row.appendChild(dateCell);
                row.appendChild(receiptCell);
                row.appendChild(amountCell);
                row.appendChild(methodCell);
                row.appendChild(referenceCell);
                row.appendChild(staffCell);
                row.appendChild(notesCell);
                tbody.appendChild(row);
            });

            table.appendChild(thead);
            table.appendChild(tbody);
            tableWrap.appendChild(table);
            body.appendChild(tableWrap);
        })
        .catch(() => {
            renderTextMessage(body, 'Failed to load payment history.', 'text-danger text-center py-2');
        });
}

function showDebtPaymentsModal(saleId, saleReceipt = '') {
    const body = document.getElementById('debt-payments-body');
    body.innerHTML = '<div class="text-center text-muted py-3"><i class="bi bi-hourglass-split me-1"></i> Loading payment history...</div>';
    new bootstrap.Modal(document.getElementById('debtPaymentsModal')).show();

    fetch(`/api/debt/payments/${saleId}`)
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                renderTextMessage(body, data.error, 'text-danger text-center py-2');
                return;
            }

            const payments = data.payments || [];
            const receiptNo = data.sales_number || saleReceipt || '-';
            body.replaceChildren();

            const header = document.createElement('div');
            header.className = 'mb-3';
            const title = document.createElement('div');
            title.appendChild(document.createTextNode('Receipt # '));
            const receiptSpan = document.createElement('span');
            receiptSpan.className = 'fw-semibold text-warning';
            receiptSpan.textContent = receiptNo;
            title.appendChild(receiptSpan);
            header.appendChild(title);

            if (!payments.length) {
                body.appendChild(header);
                const empty = document.createElement('div');
                empty.className = 'text-muted text-center py-2';
                empty.textContent = 'No payment entries recorded yet.';
                body.appendChild(empty);
                return;
            }

            body.appendChild(header);

            const tableWrap = document.createElement('div');
            tableWrap.className = 'table-responsive';
            const table = document.createElement('table');
            table.className = 'table table-dark table-sm table-hover mb-0 debt-payments-table';
            const thead = document.createElement('thead');
            thead.style.fontSize = '0.72rem';
            const headRow = document.createElement('tr');
            headRow.className = 'text-muted';
            [
                { text: 'Date Paid', className: 'date-paid-col' },
                { text: 'Amount' },
                { text: 'Method', style: 'width: 18%;' },
                { text: 'Reference', style: 'width: 14%;' },
                { text: 'Staff', style: 'width: 15%;' },
                { text: 'Notes' },
            ].forEach((cellConfig) => {
                const th = document.createElement('th');
                th.textContent = cellConfig.text;
                if (cellConfig.className) th.className = cellConfig.className;
                if (cellConfig.style) th.style.cssText = cellConfig.style;
                headRow.appendChild(th);
            });
            thead.appendChild(headRow);

            const tbody = document.createElement('tbody');
            payments.forEach((p) => {
                const row = document.createElement('tr');
                row.style.fontSize = '0.83rem';

                const dateCell = document.createElement('td');
                dateCell.className = 'text-white-50';
                dateCell.textContent = p.paid_at || '-';

                const amountCell = document.createElement('td');
                amountCell.className = 'fw-bold text-info';
                amountCell.textContent = formatPesoText(parseFloat(p.amount_paid || 0));

                const methodCell = document.createElement('td');
                const methodBadge = document.createElement('span');
                methodBadge.className = 'badge bg-dark border border-secondary text-info me-2';
                methodBadge.textContent = p.payment_method || '-';
                methodCell.appendChild(methodBadge);

                const referenceCell = document.createElement('td');
                referenceCell.className = 'text-white-50';
                referenceCell.textContent = p.reference_no || '-';

                const staffCell = document.createElement('td');
                staffCell.className = 'text-white-50';
                const staffIcon = document.createElement('i');
                staffIcon.className = 'bi bi-person me-1';
                staffCell.appendChild(staffIcon);
                staffCell.appendChild(document.createTextNode(p.paid_by || 'Staff'));

                const notesCell = document.createElement('td');
                notesCell.className = 'text-white-50 small fst-italic';
                notesCell.textContent = p.notes || '';

                row.appendChild(dateCell);
                row.appendChild(amountCell);
                row.appendChild(methodCell);
                row.appendChild(referenceCell);
                row.appendChild(staffCell);
                row.appendChild(notesCell);
                tbody.appendChild(row);
            });

            table.appendChild(thead);
            table.appendChild(tbody);
            tableWrap.appendChild(table);
            body.appendChild(tableWrap);
        })
        .catch(() => {
            renderTextMessage(body, 'Failed to load payment history.', 'text-danger text-center py-2');
        });
}

/* =========================================================
LOYALTY PROGRAMS ADMIN TAB
========================================================= */

// Load programs when tab is clicked
document.querySelector('[data-bs-target="#loyalty-tab"]')?.addEventListener('shown.bs.tab', function () {
    loadLoyaltyPrograms();
});

function showLoyaltyFlash(message, type = 'success') {
    if (window.A4Flash?.show) {
        return window.A4Flash.show(message, type, { replace: true });
    }
}

let loyaltyProgramsCache = [];

function formatLoyaltyDate(value) {
    if (!value) return '-';
    const raw = String(value).trim();
    const parsed = new Date(raw);
    if (!Number.isNaN(parsed.getTime())) {
        return parsed.toLocaleDateString(undefined, {
            year: 'numeric',
            month: 'short',
            day: '2-digit'
        });
    }

    const isoMatch = raw.match(/^(\d{4}-\d{2}-\d{2})/);
    if (isoMatch) return isoMatch[1];
    return raw.replace(/\s+\d{2}:\d{2}:\d{2}(?:\s+GMT)?$/i, '');
}

function loyaltyDateInputValue(value) {
    if (!value) return '';
    const raw = String(value).trim();
    const isoMatch = raw.match(/^(\d{4}-\d{2}-\d{2})/);
    if (isoMatch) return isoMatch[1];

    const parsed = new Date(raw);
    if (!Number.isNaN(parsed.getTime())) {
        return parsed.toISOString().slice(0, 10);
    }
    return '';
}

function getLoyaltyRewardLabel(program) {
    return {
        NONE: 'Earn-only campaign',
        FREE_SERVICE: 'Free Service',
        FREE_ITEM: 'Free Item',
        DISCOUNT_PERCENT: `${program.reward_value}% off`,
        DISCOUNT_AMOUNT: `P${parseFloat(program.reward_value).toFixed(2)} off`,
        RAFFLE_ENTRY: `${parseFloat(program.reward_value || 1).toFixed(0)} raffle entr${parseFloat(program.reward_value || 1).toFixed(0) === '1' ? 'y' : 'ies'}`,
    }[program.reward_type] || program.reward_type;
}

function getLoyaltyEarningBadges(program) {
    const stampEnabled = Number(program.stamp_enabled ?? 1) === 1;
    const pointsEnabled = Number(program.points_enabled ?? 0) === 1;
    const ruleCount = Array.isArray(program.point_rules) ? program.point_rules.length : 0;
    const isEarnOnly = String(program.program_mode || 'REDEEMABLE') === 'EARN_ONLY';

    return [
        stampEnabled ? `<span class="badge bg-dark border border-success text-success">Stamps ${program.threshold || 0}</span>` : '',
        pointsEnabled ? `<span class="badge bg-dark border border-info text-info">Points ${ruleCount} rule${ruleCount === 1 ? '' : 's'}</span>` : '',
        pointsEnabled && Number(program.points_threshold || 0) > 0
            ? `<span class="badge bg-dark border border-primary text-primary">Pts Target ${program.points_threshold}</span>`
            : '',
        `<span class="badge bg-dark border border-secondary text-secondary">${String(program.reward_basis || 'STAMPS').replaceAll('_', ' ')}</span>`,
        `<span class="badge ${isEarnOnly ? 'bg-warning text-dark' : 'bg-dark border border-secondary text-secondary'}">${isEarnOnly ? 'Earn Only' : 'Redeemable'}</span>`
    ].filter(Boolean).join(' ');
}

function findLoyaltyProgram(programId) {
    return loyaltyProgramsCache.find(program => Number(program.id) === Number(programId)) || null;
}

async function loadLoyaltyPrograms() {
    const tbody = document.getElementById('loyalty-programs-admin-body');
    tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-3"><i class="bi bi-hourglass-split me-1"></i> Loading...</td></tr>';

    try {
        const res  = await fetch('/api/loyalty/programs');
        const data = await res.json();
        const programs = data.programs || [];
        loyaltyProgramsCache = programs;

        if (!programs.length) {
            tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-3">No programs yet. Create one above.</td></tr>';
            return;
        }

        tbody.innerHTML = programs.map(p => {
            const formatLoyaltyDate = (value) => {
                if (!value) return '-';
                const raw = String(value).trim();

                // Flask/JSON date strings can look like:
                // "Sun, 01 Mar 2026 00:00:00 GMT"
                const parsed = new Date(raw);
                if (!Number.isNaN(parsed.getTime())) {
                    return parsed.toLocaleDateString(undefined, {
                        year: 'numeric',
                        month: 'short',
                        day: '2-digit'
                    });
                }

                // Fallback for ISO-like values: keep YYYY-MM-DD only
                const isoMatch = raw.match(/^(\d{4}-\d{2}-\d{2})/);
                if (isoMatch) return isoMatch[1];

                return raw.replace(/\s+\d{2}:\d{2}:\d{2}(?:\s+GMT)?$/i, '');
            };

            const periodStartDisplay = formatLoyaltyDate(p.period_start);
            const periodEndDisplay   = formatLoyaltyDate(p.period_end);

            const typeBadge = p.program_type === 'SERVICE'
                ? `<span class="badge bg-dark border border-info text-info">Service</span>`
                : `<span class="badge bg-dark border border-warning text-warning">Item</span>`;

            const rewardLabel = {
                NONE:             'Earn-only campaign',
                FREE_SERVICE:     'Free Service',
                FREE_ITEM:        'Free Item',
                DISCOUNT_PERCENT: `${p.reward_value}% off`,
                DISCOUNT_AMOUNT:  `&#8369;${parseFloat(p.reward_value).toFixed(2)} off`,
                RAFFLE_ENTRY:     `${parseFloat(p.reward_value || 1).toFixed(0)} raffle entr${parseFloat(p.reward_value || 1).toFixed(0) === '1' ? 'y' : 'ies'}`,
            }[p.reward_type] || p.reward_type;

            const isEarnOnly = String(p.program_mode || 'REDEEMABLE') === 'EARN_ONLY';
            const rewardDisplay = p.reward_description
                ? `<span title="${rewardLabel}">${p.reward_description}</span>`
                : `<span class="text-muted">${isEarnOnly ? 'Earn-only (no direct redemption)' : rewardLabel}</span>`;

            const stampEnabled = Number(p.stamp_enabled ?? 1) === 1;
            const pointsEnabled = Number(p.points_enabled ?? 0) === 1;
            const ruleCount = Array.isArray(p.point_rules) ? p.point_rules.length : 0;
            const earningBadges = [
                stampEnabled ? `<span class="badge bg-dark border border-success text-success me-1">Stamps ${p.threshold || 0}</span>` : '',
                pointsEnabled ? `<span class="badge bg-dark border border-info text-info me-1">Points ${ruleCount} rule${ruleCount === 1 ? '' : 's'}</span>` : '',
                pointsEnabled && Number(p.points_threshold || 0) > 0
                    ? `<span class="badge bg-dark border border-primary text-primary me-1">Pts Target ${p.points_threshold}</span>`
                    : '',
                `<span class="badge bg-dark border border-secondary text-secondary me-1">${String(p.reward_basis || 'STAMPS').replaceAll('_', ' ')}</span>`,
                `<span class="badge ${isEarnOnly ? 'bg-warning text-dark' : 'bg-dark border border-secondary text-secondary'}">${isEarnOnly ? 'Earn Only' : 'Redeemable'}</span>`
            ].join('');

            const isExpired = Number(p.is_expired || 0) === 1;
            const extendButton = isExpired
                ? `<button class="btn btn-sm btn-outline-secondary text-muted" disabled title="Expired programs can no longer be extended">
                        <i class="bi bi-wrench"></i>
                    </button>`
                : `<button class="btn btn-sm btn-outline-warning" onclick="event.stopPropagation(); openExtendLoyaltyProgramModal(${p.id})" title="Extend program period">
                        <i class="bi bi-wrench"></i>
                    </button>`;
            const actionsHtml = `
                <div class="btn-group btn-group-sm" role="group" aria-label="Loyalty actions">
                    <button class="btn btn-sm btn-outline-info me-1" onclick="event.stopPropagation(); openLoyaltyProgramDetails(${p.id})" title="View details">
                        <i class="bi bi-eye"></i>
                    </button>
                    ${extendButton}
                </div>
            `;
            const statusBtn = isExpired
                ? `<div class="text-warning" title="This program's active period has ended.">
                    <i class="bi bi-clock-history fs-4"></i>
                    <small class="d-block fw-bold" style="font-size:0.65rem;">EXPIRED</small>
                </div>`
                : p.is_active
                    ? `<button class="toggle-btn text-success" onclick="event.stopPropagation(); toggleLoyaltyProgram(${p.id}, false)" title="Click to deactivate">
                        <i class="bi bi-toggle-on fs-3"></i>
                        <small class="d-block fw-bold" style="font-size:0.65rem;">ACTIVE</small>
                    </button>`
                    : `<button class="toggle-btn text-muted" onclick="event.stopPropagation(); toggleLoyaltyProgram(${p.id}, true)" title="Click to activate">
                        <i class="bi bi-toggle-off fs-3"></i>
                        <small class="d-block fw-bold" style="font-size:0.65rem;">INACTIVE</small>
                    </button>`;

            // qualifying_name is joined in by the API (see note below)
            const qualifyingDisplay = p.qualifying_name
                ? p.qualifying_name
                : `<span class="text-muted">ID: ${p.qualifying_id}</span>`;

            return `
                <tr>
                    <td class="fw-bold">${p.name}</td>
                    <td>${typeBadge}</td>
                    <td>${qualifyingDisplay}</td>
                    <td class="text-center">
                        ${earningBadges || '<span class="text-muted">None</span>'}
                    </td>
                    <td style="max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                        ${rewardDisplay}
                    </td>
                    <td class="text-white-50 small">
                        ${periodStartDisplay}<br>
                        <span class="text-muted">to</span> ${periodEndDisplay}
                    </td>
                    <td class="text-center">${actionsHtml}</td>
                    <td class="text-center">${statusBtn}</td>
                </tr>`;
        }).join('');

    } catch (err) {
        console.error('Loyalty programs load failed:', err);
        tbody.innerHTML = '<tr><td colspan="8" class="text-center text-danger py-3">Failed to load programs.</td></tr>';
    }
}

function openLoyaltyProgramDetails(programId) {
    const program = findLoyaltyProgram(programId);
    if (!program) {
        showLoyaltyFlash('Could not load loyalty program details.', 'danger');
        return;
    }

    const typeBadge = program.program_type === 'SERVICE'
        ? `<span class="badge bg-dark border border-info text-info">Service</span>`
        : `<span class="badge bg-dark border border-warning text-warning">Item</span>`;
    const isExpired = Number(program.is_expired || 0) === 1;
    const statusHtml = isExpired
        ? '<span class="badge bg-danger">Expired</span>'
        : Number(program.is_active || 0) === 1
            ? '<span class="badge bg-success">Active</span>'
            : '<span class="badge bg-secondary">Inactive</span>';
    const rewardLabel = getLoyaltyRewardLabel(program);
    const rewardDisplay = program.reward_description
        ? `${program.reward_description} <span class="text-white-50">(${rewardLabel})</span>`
        : rewardLabel;
    const qualifyingDisplay = program.qualifying_name || `ID: ${program.qualifying_id}`;
    const rules = Array.isArray(program.point_rules) ? program.point_rules : [];

    document.getElementById('loyaltyDetailName').textContent = program.name || '';
    document.getElementById('loyaltyDetailType').innerHTML = typeBadge;
    document.getElementById('loyaltyDetailStatus').innerHTML = statusHtml;
    document.getElementById('loyaltyDetailQualifying').textContent = qualifyingDisplay;
    document.getElementById('loyaltyDetailPeriod').textContent = `${formatLoyaltyDate(program.period_start)} to ${formatLoyaltyDate(program.period_end)}`;
    document.getElementById('loyaltyDetailEarning').innerHTML = getLoyaltyEarningBadges(program) || '<span class="text-muted">None</span>';
    document.getElementById('loyaltyDetailReward').innerHTML = rewardDisplay;
    document.getElementById('loyaltyDetailRules').innerHTML = rules.length
        ? rules.map(rule => {
            const conditions = [];
            if (Number(rule.requires_any_service || 0) === 1) conditions.push('Any service');
            if (Number(rule.requires_any_item || 0) === 1) conditions.push('Any item');
            if (rule.service_name) {
                conditions.push(`Service: ${rule.service_name}`);
            } else if (rule.service_id) {
                conditions.push(`Service #${rule.service_id}`);
            }
            if (rule.item_name) {
                conditions.push(`Item: ${rule.item_name}`);
            } else if (rule.item_id) {
                conditions.push(`Item #${rule.item_id}`);
            }
            const conditionText = conditions.length ? conditions.join(' + ') : 'Applies without extra conditions';
            return `
                <div class="border rounded border-secondary p-2 mb-2">
                    <div class="fw-bold text-info">${rule.rule_name || 'Rule'}</div>
                    <div class="text-white-50">+${rule.points} points</div>
                    <div class="text-white-50">${conditionText}</div>
                </div>
            `;
        }).join('')
        : '<div class="text-muted">No point rules configured for this program.</div>';

    new bootstrap.Modal(document.getElementById('loyaltyProgramDetailsModal')).show();
}

function openExtendLoyaltyProgramModal(programId) {
    const program = findLoyaltyProgram(programId);
    if (!program) {
        showLoyaltyFlash('Could not load loyalty program for extension.', 'danger');
        return;
    }

    if (Number(program.is_expired || 0) === 1) {
        showLoyaltyFlash('Expired loyalty programs can no longer be extended.', 'warning');
        return;
    }

    const currentEnd = loyaltyDateInputValue(program.period_end);
    document.getElementById('extend-loyalty-program-id').value = program.id;
    document.getElementById('extend-loyalty-program-name').value = program.name || '';
    document.getElementById('extend-loyalty-period-start').value = loyaltyDateInputValue(program.period_start);
    document.getElementById('extend-loyalty-current-end').value = currentEnd;
    document.getElementById('extend-loyalty-new-end').value = currentEnd;
    document.getElementById('extend-loyalty-new-end').min = currentEnd;
    document.getElementById('extend-loyalty-error').textContent = '';

    new bootstrap.Modal(document.getElementById('extendLoyaltyProgramModal')).show();
}

async function submitLoyaltyProgramExtension() {
    const programId = document.getElementById('extend-loyalty-program-id').value;
    const newEnd = document.getElementById('extend-loyalty-new-end').value;
    const errorEl = document.getElementById('extend-loyalty-error');
    errorEl.textContent = '';

    if (!programId || !newEnd) {
        errorEl.textContent = 'Choose a new end date first.';
        return;
    }

    try {
        const res = await fetch(`/api/loyalty/programs/${programId}/extend`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ period_end: newEnd })
        });
        const data = await res.json();

        if (!res.ok) {
            errorEl.textContent = data.message || 'Failed to extend loyalty program.';
            return;
        }

        bootstrap.Modal.getInstance(document.getElementById('extendLoyaltyProgramModal')).hide();
        showLoyaltyFlash(data.message || 'Loyalty program extended successfully.', 'success');
        loadLoyaltyPrograms();
    } catch (err) {
        console.error('Loyalty extension failed:', err);
        errorEl.textContent = 'Network error while extending loyalty program.';
    }
}

async function toggleLoyaltyProgram(programId, activate) {
    try {
        const res = await fetch(`/api/loyalty/programs/${programId}/toggle`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ is_active: activate })
        });
        const data = await res.json();

        if (!res.ok) {
            showLoyaltyFlash(data.message || 'Failed to update program status.', 'danger');
            return;
        }

        showLoyaltyFlash(
            data.message || `Program successfully ${activate ? 'activated' : 'deactivated'}.`,
            activate ? 'success' : 'danger'
        );
        loadLoyaltyPrograms();
    } catch (err) {
        console.error('Toggle failed:', err);
        showLoyaltyFlash('Network error while updating program status.', 'danger');
    }
}

// -- Qualifying search (SERVICE or ITEM) -------------------------
let lfSearchTimer;
let lfRuleCounter = 0;

function loyaltyTypeChanged() {
    const type = document.getElementById('lf-type').value;
    const label = document.getElementById('lf-qualifying-label');
    const input = document.getElementById('lf-qualifying-search');

    label.innerHTML = type === 'SERVICE'
        ? 'Qualifying Service <span class="text-danger">*</span>'
        : 'Qualifying Item <span class="text-danger">*</span>';
    input.placeholder = type === 'SERVICE' ? 'Search service...' : 'Search item...';

    // Clear selection on type change
    input.value = '';
    document.getElementById('lf-qualifying-id').value = '';
    document.getElementById('lf-qualifying-suggestions').style.display = 'none';
}

function loyaltyModesChanged() {
    const programMode = document.getElementById('lf-program-mode').value;
    const isEarnOnly = programMode === 'EARN_ONLY';
    const stampEnabled = document.getElementById('lf-stamp-enabled').checked;
    const pointsEnabled = document.getElementById('lf-points-enabled').checked;
    const threshold = document.getElementById('lf-threshold');
    const thresholdGroup = document.getElementById('lf-threshold-group');
    const thresholdRequired = document.getElementById('lf-threshold-required');
    const pointsThreshold = document.getElementById('lf-points-threshold');
    const pointsThresholdGroup = document.getElementById('lf-points-threshold-group');
    const pointsThresholdRequired = document.getElementById('lf-points-threshold-required');
    const rewardBasisSelect = document.getElementById('lf-reward-basis');
    const pointsCard = document.getElementById('lf-points-rules-card');
    const rewardTypeGroup = document.getElementById('lf-reward-type-group');
    const rewardValueGroup = document.getElementById('lf-reward-value-group');
    const rewardDescGroup = document.getElementById('lf-reward-desc-group');
    const rewardTypeInput = document.getElementById('lf-reward-type');
    const rewardValueInput = document.getElementById('lf-reward-value');
    const basisUsesStamps = (basis) => basis === 'STAMPS' || basis === 'STAMPS_OR_POINTS';
    const basisUsesPoints = (basis) => basis === 'POINTS' || basis === 'STAMPS_OR_POINTS';

    rewardTypeGroup.style.display = isEarnOnly ? 'none' : '';
    rewardValueGroup.style.display = isEarnOnly ? 'none' : '';
    rewardDescGroup.style.display = isEarnOnly ? 'none' : '';
    rewardTypeInput.required = !isEarnOnly;
    rewardValueInput.required = !isEarnOnly;

    if (isEarnOnly) {
        rewardTypeInput.value = 'RAFFLE_ENTRY';
        rewardValueInput.value = '0';
    }

    Array.from(rewardBasisSelect.options).forEach(opt => {
        if (opt.value === 'STAMPS') opt.disabled = !stampEnabled;
        if (opt.value === 'POINTS') opt.disabled = !pointsEnabled;
        if (opt.value === 'STAMPS_OR_POINTS') opt.disabled = !(stampEnabled && pointsEnabled);
    });

    if ((!stampEnabled && rewardBasisSelect.value === 'STAMPS') ||
        (!pointsEnabled && rewardBasisSelect.value === 'POINTS') ||
        (!(stampEnabled && pointsEnabled) && rewardBasisSelect.value === 'STAMPS_OR_POINTS')) {
        rewardBasisSelect.value = stampEnabled ? 'STAMPS' : (pointsEnabled ? 'POINTS' : 'STAMPS');
    }

    const rewardBasis = rewardBasisSelect.value;
    const needsStampThreshold = !isEarnOnly && stampEnabled && basisUsesStamps(rewardBasis);
    const needsPointsThreshold = !isEarnOnly && pointsEnabled && basisUsesPoints(rewardBasis);

    thresholdGroup.style.display = needsStampThreshold ? '' : 'none';
    threshold.required = needsStampThreshold;
    thresholdRequired.style.display = needsStampThreshold ? '' : 'none';
    threshold.disabled = !needsStampThreshold;

    if (!needsStampThreshold) {
        threshold.value = '0';
    } else if (parseInt(threshold.value || '0', 10) < 1) {
        threshold.value = '10';
    }

    pointsThresholdGroup.style.display = needsPointsThreshold ? '' : 'none';
    pointsThreshold.required = needsPointsThreshold;
    pointsThresholdRequired.style.display = needsPointsThreshold ? '' : 'none';
    pointsThreshold.disabled = !needsPointsThreshold;
    if (!needsPointsThreshold) {
        pointsThreshold.value = '0';
    } else if (parseInt(pointsThreshold.value || '0', 10) < 1) {
        pointsThreshold.value = '100';
    }

    pointsCard.style.display = pointsEnabled ? '' : 'none';
    if (pointsEnabled && !document.querySelector('.lf-point-rule-row')) {
        loyaltyAddRuleRow();
    }
}

function openServicesTabWithPrefill(serviceName) {
    const tabBtn = document.querySelector('[data-bs-target="#manage-services-tab"]');
    const serviceInput = document.getElementById('add-service-name-input');
    if (!tabBtn || !serviceInput) return;

    const tab = bootstrap.Tab.getOrCreateInstance(tabBtn);
    tab.show();

    // Wait for tab transition before focusing so the caret lands correctly.
    window.setTimeout(() => {
        serviceInput.value = serviceName || '';
        serviceInput.focus();
        serviceInput.setSelectionRange(serviceInput.value.length, serviceInput.value.length);
    }, 120);
}

function openItemsPageWithPrefill(itemName) {
    const q = encodeURIComponent(itemName || '');
    window.location.href = `/transaction/items?return_to=in&prefill_name=${q}`;
}

function loyaltyBuildRuleRow(id) {
    return `
        <div class="row g-2 align-items-end mb-2 lf-point-rule-row" data-rule-id="${id}" style="border-bottom:1px dashed #333; padding-bottom:10px;">
            <div class="col-md-2">
                <label class="form-label text-white-50" style="font-size:0.7rem;">Rule Name</label>
                <input type="text" class="form-control bg-dark text-white border-secondary lf-rule-name" placeholder="Optional">
            </div>
            <div class="col-md-2">
                <label class="form-label text-white-50" style="font-size:0.7rem;">Service Condition</label>
                <div class="position-relative">
                    <input type="text" class="form-control bg-dark text-white border-secondary lf-rule-service-search"
                        placeholder="Search service..." autocomplete="off" oninput="loyaltyRuleServiceSearch(this)">
                    <div class="list-group position-absolute w-100 lf-rule-service-suggestions"
                        style="display:none; z-index:9999; background:#1a1a1a; border:1px solid #444; border-radius:0 0 8px 8px; max-height:160px; overflow-y:auto;"></div>
                    <input type="hidden" class="lf-rule-service-id">
                </div>
            </div>
            <div class="col-md-1">
                <label class="form-label text-white-50" style="font-size:0.7rem;">Points</label>
                <input type="number" min="1" value="10" class="form-control bg-dark text-white border-secondary lf-rule-points">
            </div>
            <div class="col-md-1">
                <label class="form-label text-white-50" style="font-size:0.7rem;">Priority</label>
                <input type="number" min="1" value="${id * 10}" class="form-control bg-dark text-white border-secondary lf-rule-priority">
            </div>
            <div class="col-md-4">
                <label class="form-label text-white-50 d-block" style="font-size:0.7rem;">Extra Conditions</label>
                <div class="d-flex gap-3 flex-wrap">
                    <label class="form-check-label text-white-50" style="font-size:0.73rem;">
                        <input class="form-check-input me-1 lf-rule-any-item" type="checkbox"> requires any item
                    </label>
                    <label class="form-check-label text-white-50" style="font-size:0.73rem;">
                        <input class="form-check-input me-1 lf-rule-stop-on-match" type="checkbox"> stop on match
                    </label>
                </div>
            </div>
            <div class="col-md-2 text-end">
                <button type="button" class="btn btn-sm btn-outline-danger" onclick="this.closest('.lf-point-rule-row').remove()">
                    <i class="bi bi-trash"></i>
                </button>
            </div>
        </div>
    `;
}

function loyaltyAddRuleRow() {
    lfRuleCounter += 1;
    const container = document.getElementById('lf-point-rules');
    container.insertAdjacentHTML('beforeend', loyaltyBuildRuleRow(lfRuleCounter));
}

function loyaltyCollectRuleRows() {
    return Array.from(document.querySelectorAll('.lf-point-rule-row')).map((row, idx) => {
        const serviceId = row.querySelector('.lf-rule-service-id').value;
        return {
            rule_name: row.querySelector('.lf-rule-name').value.trim(),
            service_id: serviceId ? parseInt(serviceId, 10) : null,
            points: parseInt(row.querySelector('.lf-rule-points').value || '0', 10) || 0,
            priority: parseInt(row.querySelector('.lf-rule-priority').value || String((idx + 1) * 10), 10),
            requires_any_item: row.querySelector('.lf-rule-any-item').checked,
            stop_on_match: row.querySelector('.lf-rule-stop-on-match').checked,
        };
    });
}

async function loyaltyRuleServiceSearch(inputEl) {
    const query = inputEl.value.trim();
    const row = inputEl.closest('.lf-point-rule-row');
    const hiddenId = row.querySelector('.lf-rule-service-id');
    const suggestions = row.querySelector('.lf-rule-service-suggestions');

    hiddenId.value = '';

    if (query.length < 2) {
        suggestions.style.display = 'none';
        return;
    }

    try {
        const res = await fetch(`/api/search/services?q=${encodeURIComponent(query)}`);
        const data = await res.json();
        const list = data.services || [];
        suggestions.innerHTML = '';

        if (!list.length) {
            suggestions.style.display = 'none';
            return;
        }

        list.slice(0, 8).forEach(item => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'list-group-item list-group-item-action';
            btn.style.cssText = 'background:#1a1a1a; color:#e0e6f0; font-size:0.83rem; padding:8px 12px;';
            btn.textContent = item.name;
            btn.onclick = () => {
                inputEl.value = item.name;
                hiddenId.value = item.id;
                suggestions.style.display = 'none';
            };
            suggestions.appendChild(btn);
        });

        suggestions.style.display = 'block';
    } catch (err) {
        console.error('Rule service search error:', err);
        suggestions.style.display = 'none';
    }
}

function loyaltyQualifyingSearch() {
    clearTimeout(lfSearchTimer);
    lfSearchTimer = setTimeout(async () => {
        const query       = document.getElementById('lf-qualifying-search').value.trim();
        const type        = document.getElementById('lf-type').value;
        const suggestions = document.getElementById('lf-qualifying-suggestions');

        // Clear hidden id on new typing
        document.getElementById('lf-qualifying-id').value = '';

        if (query.length < 2) { suggestions.style.display = 'none'; return; }

        try {
            const url = type === 'SERVICE'
                ? `/api/search/services?q=${encodeURIComponent(query)}`
                : `/api/search?q=${encodeURIComponent(query)}`;

            const res  = await fetch(url);
            const data = await res.json();
            const list = type === 'SERVICE' ? (data.services || []) : (data.items || []);
            const normalizedQuery = query.toLowerCase();

            suggestions.innerHTML = '';
            if (!list.length && type !== 'SERVICE' && type !== 'ITEM') {
                suggestions.style.display = 'none';
                return;
            }

            list.slice(0, 8).forEach(item => {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'list-group-item list-group-item-action';
                btn.style.cssText = 'background:#1a1a1a; color:#e0e6f0; font-size:0.83rem; padding:8px 12px;';
                btn.textContent = item.name;
                btn.addEventListener('mouseover', () => btn.style.background = '#2a2a2a');
                btn.addEventListener('mouseout',  () => btn.style.background = '#1a1a1a');
                btn.onclick = () => {
                    document.getElementById('lf-qualifying-search').value = item.name;
                    document.getElementById('lf-qualifying-id').value     = item.id;
                    suggestions.style.display = 'none';
                };
                suggestions.appendChild(btn);
            });

            if (type === 'SERVICE') {
                const hasExactMatch = list.some(item => String(item.name || '').trim().toLowerCase() === normalizedQuery);
                if (!hasExactMatch) {
                    const addBtn = document.createElement('button');
                    addBtn.type = 'button';
                    addBtn.className = 'list-group-item list-group-item-action';
                    addBtn.style.cssText = 'background:#1f2a1f; color:#9be28f; font-size:0.83rem; padding:8px 12px; border-top:1px dashed #3b4b3b;';
                    const addIcon = document.createElement('i');
                    addIcon.className = 'bi bi-plus-circle me-2';
                    const addLabel = document.createTextNode('Add service: ');
                    const addStrong = document.createElement('strong');
                    addStrong.textContent = query;
                    addBtn.appendChild(addIcon);
                    addBtn.appendChild(addLabel);
                    addBtn.appendChild(addStrong);
                    addBtn.onclick = () => {
                        suggestions.style.display = 'none';
                        openServicesTabWithPrefill(query);
                    };
                    suggestions.appendChild(addBtn);
                }
            }
            if (type === 'ITEM') {
                const hasExactMatch = list.some(item => String(item.name || '').trim().toLowerCase() === normalizedQuery);
                if (!hasExactMatch) {
                    const addItemBtn = document.createElement('button');
                    addItemBtn.type = 'button';
                    addItemBtn.className = 'list-group-item list-group-item-action';
                    addItemBtn.style.cssText = 'background:#1f2a1f; color:#9be28f; font-size:0.83rem; padding:8px 12px; border-top:1px dashed #3b4b3b;';
                    const addIcon = document.createElement('i');
                    addIcon.className = 'bi bi-plus-circle me-2';
                    const addLabel = document.createTextNode('Add item: ');
                    const addStrong = document.createElement('strong');
                    addStrong.textContent = query;
                    addItemBtn.appendChild(addIcon);
                    addItemBtn.appendChild(addLabel);
                    addItemBtn.appendChild(addStrong);
                    addItemBtn.onclick = () => {
                        suggestions.style.display = 'none';
                        openItemsPageWithPrefill(query);
                    };
                    suggestions.appendChild(addItemBtn);
                }
            }

            suggestions.style.display = 'block';
        } catch (err) {
            console.error('Qualifying search error:', err);
        }
    }, 250);
}

// Hide suggestions on outside click
document.addEventListener('click', (e) => {
    if (!e.target.closest('#lf-qualifying-search') && !e.target.closest('#lf-qualifying-suggestions')) {
        document.getElementById('lf-qualifying-suggestions').style.display = 'none';
    }
    if (!e.target.closest('.lf-rule-service-search') && !e.target.closest('.lf-rule-service-suggestions')) {
        document.querySelectorAll('.lf-rule-service-suggestions').forEach(el => {
            el.style.display = 'none';
        });
    }
});

function loyaltyRewardTypeChanged() {
    const type  = document.getElementById('lf-reward-type').value;
    const hint  = document.getElementById('lf-reward-value-hint');
    const label = document.getElementById('lf-reward-value-label');

    const hints = {
        FREE_SERVICE:     '(leave 0 - reward is the service itself)',
        FREE_ITEM:        '(leave 0 - reward is the item itself)',
        DISCOUNT_PERCENT: '(e.g. 10 = 10% off)',
        DISCOUNT_AMOUNT:  '(peso amount off, e.g. 50)',
        RAFFLE_ENTRY:     '(number of raffle entries to grant, e.g. 1)',
    };
    hint.textContent = hints[type] || '';
}

// -- Form submission --------------------------------------------
async function loyaltySubmitProgram() {
    const errEl = document.getElementById('lf-error');
    const statusEl = document.getElementById('lf-status');
    errEl.textContent = '';
    statusEl.textContent = '';

    const name = document.getElementById('lf-name').value.trim();
    const programType = document.getElementById('lf-type').value;
    const programMode = document.getElementById('lf-program-mode').value;
    const qualifyingId = document.getElementById('lf-qualifying-id').value;
    const qualifyingName = document.getElementById('lf-qualifying-search').value.trim();
    const threshold = parseInt(document.getElementById('lf-threshold').value || '0', 10) || 0;
    const pointsThreshold = parseInt(document.getElementById('lf-points-threshold').value || '0', 10) || 0;
    const stampEnabled = document.getElementById('lf-stamp-enabled').checked;
    const pointsEnabled = document.getElementById('lf-points-enabled').checked;
    const rewardBasis = document.getElementById('lf-reward-basis').value;
    const rewardType = document.getElementById('lf-reward-type').value;
    const rewardValue = parseFloat(document.getElementById('lf-reward-value').value) || 0;
    const rewardDesc = document.getElementById('lf-reward-desc').value.trim();
    const periodStart = document.getElementById('lf-period-start').value;
    const periodEnd = document.getElementById('lf-period-end').value;
    const pointRules = loyaltyCollectRuleRows().filter(r => r.points > 0);

    if (!name) { errEl.textContent = 'Program name is required.'; return; }
    if (!qualifyingId) { errEl.textContent = `Select a qualifying ${programType === 'SERVICE' ? 'service' : 'item'} from the search.`; return; }
    if (programType === 'SERVICE' && !qualifyingName) {
        errEl.textContent = 'Qualifying service is required.';
        return;
    }
    if (!/^\d+$/.test(String(qualifyingId))) {
        errEl.textContent = 'Select a valid qualifying service/item from the search list.';
        return;
    }
    if (!stampEnabled && !pointsEnabled) { errEl.textContent = 'Enable stamps and/or points.'; return; }
    if (programMode === 'REDEEMABLE') {
        if (rewardBasis === 'STAMPS' && !stampEnabled) { errEl.textContent = 'Stamps reward basis requires Stamps mode.'; return; }
        if (rewardBasis === 'POINTS' && !pointsEnabled) { errEl.textContent = 'Points reward basis requires Points mode.'; return; }
        if (rewardBasis === 'STAMPS_OR_POINTS' && (!stampEnabled || !pointsEnabled)) {
            errEl.textContent = 'Stamps OR Points basis requires both earning modes.';
            return;
        }
        if ((rewardBasis === 'STAMPS' || rewardBasis === 'STAMPS_OR_POINTS') && threshold < 1) {
            errEl.textContent = 'Stamps needed must be at least 1 for this reward basis.';
            return;
        }
        if ((rewardBasis === 'POINTS' || rewardBasis === 'STAMPS_OR_POINTS') && pointsThreshold < 1) {
            errEl.textContent = 'Points needed must be at least 1 for this reward basis.';
            return;
        }
    }
    if (pointsEnabled && pointRules.length < 1) { errEl.textContent = 'Add at least one valid point rule.'; return; }
    if (pointsEnabled) {
        const invalidRule = pointRules.find(r => !r.service_id && !r.requires_any_item);
        if (invalidRule) {
            errEl.textContent = 'Each point rule needs a service condition or "requires any item".';
            return;
        }
        const invalidRuleService = pointRules.find(r => r.service_id !== null && !Number.isInteger(r.service_id));
        if (invalidRuleService) {
            errEl.textContent = 'Each point rule service must be selected from existing services.';
            return;
        }
    }
    if (!periodStart) { errEl.textContent = 'Period start is required.'; return; }
    if (!periodEnd) { errEl.textContent = 'Period end is required.'; return; }
    if (periodStart >= periodEnd) { errEl.textContent = 'Period end must be after period start.'; return; }

    try {
        const res = await fetch('/api/loyalty/programs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name,
                program_type: programType,
                program_mode: programMode,
                qualifying_id: parseInt(qualifyingId, 10),
                threshold,
                points_threshold: pointsThreshold,
                reward_basis: rewardBasis,
                stamp_enabled: stampEnabled,
                points_enabled: pointsEnabled,
                point_rules: pointRules,
                reward_type: programMode === 'EARN_ONLY' ? 'NONE' : rewardType,
                reward_value: programMode === 'EARN_ONLY' ? 0 : rewardValue,
                reward_description: programMode === 'EARN_ONLY' ? (rewardDesc || 'Earn-only campaign') : rewardDesc,
                period_start: periodStart,
                period_end: periodEnd,
                branch_id: null,
            })
        });

        const data = await res.json();

        if (!res.ok) {
            errEl.textContent = data.message || 'Failed to create program.';
            return;
        }

        ['lf-name', 'lf-qualifying-search', 'lf-reward-desc', 'lf-period-start', 'lf-period-end'].forEach(id => {
            document.getElementById(id).value = '';
        });
        document.getElementById('lf-qualifying-id').value = '';
        document.getElementById('lf-threshold').value = '10';
        document.getElementById('lf-points-threshold').value = '100';
        document.getElementById('lf-reward-value').value = '0';
        document.getElementById('lf-program-mode').value = 'REDEEMABLE';
        document.getElementById('lf-reward-basis').value = 'STAMPS';
        document.getElementById('lf-stamp-enabled').checked = true;
        document.getElementById('lf-points-enabled').checked = false;
        document.getElementById('lf-point-rules').innerHTML = '';
        lfRuleCounter = 0;
        loyaltyModesChanged();

        loadLoyaltyPrograms();
        showLoyaltyFlash(data.message || `Program "${name}" created successfully.`, 'success');

    } catch (err) {
        errEl.textContent = 'Network error. Try again.';
        showLoyaltyFlash(errEl.textContent, 'danger');
    }
}
if (document.getElementById('lf-program-mode')) {
    loyaltyModesChanged();
}

const addMechanicForm = document.getElementById('addMechanicForm');
if (addMechanicForm) {
    const mechanicFields = Array.from(addMechanicForm.querySelectorAll('input[name], select[name]'));

    const markMechanicFieldValidity = (field) => {
        const value = String(field.value || '').trim();
        const isValid = value !== '';
        field.classList.toggle('is-invalid', !isValid);
        return isValid;
    };

    mechanicFields.forEach(field => {
        field.addEventListener('input', () => markMechanicFieldValidity(field));
        field.addEventListener('change', () => markMechanicFieldValidity(field));
    });

    addMechanicForm.addEventListener('submit', (event) => {
        const invalidFields = mechanicFields.filter(field => !markMechanicFieldValidity(field));
        if (invalidFields.length) {
            event.preventDefault();
            invalidFields[0].focus();
        }
    });
}

