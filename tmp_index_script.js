
let lastSuccessfulSearch = "";
const inventorySearchInput = document.getElementById('inventorySearch');
const inventoryTableBody = document.querySelector('table tbody');
const itemCountBadge = document.getElementById('itemCountBadge');
const initialTableMarkup = inventoryTableBody ? inventoryTableBody.innerHTML : "";
const initialBadgeText = itemCountBadge ? itemCountBadge.textContent : "";
function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function formatMarkupPercent(markup) {
    const numericMarkup = Number(markup || 0) * 100;
    const truncatedMarkup = Math.trunc(numericMarkup * 100) / 100;
    return truncatedMarkup.toFixed(2);
}

/* ---- Row Builder ---- */
function createRowHtml(item) {
    const isAdmin = "{{ session.get('role') }}" === "admin";
    const isLow = Boolean(item.should_restock);
    return `
        <tr class="inventory-row" data-item-id="${item.id}">
            <td class="fw-bold item-cell" data-original="${escapeHtml(item.name || '')}">${escapeHtml(item.name || '')}</td>
            <td class="description-cell text-center" data-original="${escapeHtml(item.description || '')}">${escapeHtml(item.description || '')}</td>
            <td class="price-text">₱${item.vendor_price || 0}</td>
            <td class="price-text">₱${item.cost_per_piece || 0}</td>
            <td class="price-text selling-price">₱${item.a4s_selling_price || 0}</td>
            ${isAdmin ? `<td><span class="markup-badge">${formatMarkupPercent(item.markup)}%</span></td>` : ''}
            <td><div class="stock-badge ${isLow ? 'low' : ''}">${item.current_stock}</div></td>
        </tr>
    `;
}

/* ---- Highlight ---- */
function escapeRegExp(text) {
    return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function highlightText(element, tokens) {
    if (!element || !element.dataset.original) return;
    const original = element.dataset.original;
    let highlighted = escapeHtml(original);
    tokens.forEach(token => {
        if (!token) return;
        const regex = new RegExp(`(${escapeRegExp(token)})`, "gi");
        highlighted = highlighted.replace(regex, `<span class="highlight">$1</span>`);
    });
    element.innerHTML = highlighted;
}

function bindInventoryRowLinks(scope = document) {
    scope.querySelectorAll('.inventory-row[data-item-id]').forEach(row => {
        if (row.dataset.linkBound === '1') return;
        row.dataset.linkBound = '1';
        row.addEventListener('click', () => {
            const itemId = row.dataset.itemId;
            if (!itemId) return;
            window.location.href = `/transaction/items/edit/${encodeURIComponent(itemId)}`;
        });
    });
}

/* ---- Debounce ---- */
function debounce(func, delay) {
    let id;
    return function (...args) {
        clearTimeout(id);
        id = setTimeout(() => func.apply(this, args), delay);
    };
}

/* ---- Search ---- */
const performSearch = async () => {
    const rawInput = inventorySearchInput.value.trim();
    const tbody = inventoryTableBody;
    const badge = itemCountBadge;

    if (rawInput.length === 0) {
        if (tbody) tbody.innerHTML = initialTableMarkup;
        if (badge) badge.textContent = initialBadgeText;
        if (tbody) bindInventoryRowLinks(tbody);
        inventorySearchInput.focus();
        return;
    }
    if (rawInput.length < 2) return;

    try {
        const response = await fetch(`/api/search?q=${encodeURIComponent(rawInput)}`);
        const data = await response.json();

        tbody.innerHTML = "";

        if (data.items.length === 0) {
            const cols = "{{ session.get('role') }}" === "admin" ? 7 : 6;
            const row = document.createElement('tr');
            const cell = document.createElement('td');
            cell.colSpan = cols;
            cell.className = 'text-center py-5';
            cell.style.color = 'var(--text-muted)';
            const icon = document.createElement('i');
            icon.className = 'bi bi-inbox';
            icon.style.fontSize = '1.6rem';
            icon.style.display = 'block';
            icon.style.marginBottom = '8px';
            const strong = document.createElement('strong');
            strong.style.color = 'var(--text-primary)';
            strong.textContent = rawInput;
            cell.appendChild(icon);
            cell.appendChild(document.createTextNode('No items found for "'));
            cell.appendChild(strong);
            cell.appendChild(document.createTextNode('"'));
            row.appendChild(cell);
            tbody.appendChild(row);
            if (badge) badge.textContent = '0 items';
            return;
        }

        data.items.forEach(item => tbody.insertAdjacentHTML('beforeend', createRowHtml(item)));
        bindInventoryRowLinks(tbody);
        if (badge) badge.textContent = `${data.items.length} item${data.items.length !== 1 ? 's' : ''}`;

        const tokens = rawInput.toLowerCase().split(" ").filter(t => t.length >= 2);
        tbody.querySelectorAll('tr').forEach(row => {
            highlightText(row.querySelector('.item-cell'), tokens);
            highlightText(row.querySelector('.description-cell'), tokens);
        });

        if (rawInput.length >= 3) saveRecentSearch(rawInput);

    } catch (err) {
        console.error("Search failed:", err);
    }
};

/* ---- Recent Searches ---- */
const RECENT_SEARCH_LIMIT = 8;
const STORAGE_KEY = "inventory_recent_searches";
const EXPIRY_MS = 7 * 24 * 60 * 60 * 1000;

function getRecentSearches() {
    let searches = JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
    const now = Date.now();
    searches = searches.filter(s => now - s.lastUsed <= EXPIRY_MS);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(searches));
    return searches;
}

function saveRecentSearch(term) {
    term = term.trim();
    if (term.length < 3) return;
    const normalizedTerm = term.toLowerCase();
    if (lastSuccessfulSearch &&
        lastSuccessfulSearch.startsWith(normalizedTerm) &&
        lastSuccessfulSearch !== normalizedTerm) return;

    const now = Date.now();
    let searches = getRecentSearches();
    const existing = searches.find(s => s.term.toLowerCase() === normalizedTerm);

    if (existing) {
        existing.count += 1;
        existing.lastUsed = now;
    } else {
        searches.unshift({ term, count: 1, lastUsed: now });
    }

    searches.sort((a, b) => b.lastUsed - a.lastUsed || b.count - a.count);
    searches = searches.slice(0, RECENT_SEARCH_LIMIT);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(searches));
    lastSuccessfulSearch = normalizedTerm;
    renderRecentSearches();
}

function renderRecentSearches() {
    const container = document.getElementById("recentSearches");
    const searches = getRecentSearches();
    if (!container) return;
    container.replaceChildren();
    if (!searches.length) return;

    const label = document.createElement('span');
    label.className = 'chips-label';
    label.textContent = 'Quick picks:';
    container.appendChild(label);

    searches.forEach((search) => {
        const chip = document.createElement('span');
        chip.className = `search-chip ${search.count >= 3 ? 'frequent' : ''}`.trim();

        const searchButton = document.createElement('button');
        searchButton.type = 'button';
        searchButton.className = 'chip-link-btn';
        searchButton.textContent = search.term;
        searchButton.addEventListener('click', () => applyRecentSearch(search.term));

        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'close-btn chip-remove-btn';
        removeButton.setAttribute('aria-label', 'Remove recent search');
        removeButton.textContent = 'x';
        removeButton.addEventListener('click', () => removeRecentSearch(search.term));

        chip.appendChild(searchButton);
        chip.appendChild(removeButton);
        container.appendChild(chip);
    });

    /* container.innerHTML = `
        <span class="chips-label">Quick picks:</span>
        ${searches.map(s => `
            <span class="search-chip ${s.count >= 3 ? "frequent" : ""}">
                <span onclick="applyRecentSearch('${s.term.replace(/'/g, "\\'")}')">
                    ${s.term}
                </span>
                <span class="close-btn" onclick="removeRecentSearch('${s.term.replace(/'/g, "\\'")}')">×</span>
            </span>
        `).join("")}
    `; */
}

function removeRecentSearch(term) {
    let searches = getRecentSearches().filter(s => s.term !== term);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(searches));
    renderRecentSearches();
}

function applyRecentSearch(term) {
    inventorySearchInput.value = term;
    performSearch();
}

inventorySearchInput.addEventListener('input', debounce(() => {
    performSearch();
    const value = inventorySearchInput.value.trim();
    const visibleRows = document.querySelectorAll('tbody tr:not([style*="display: none"])').length;
    if (value.length >= 3 && visibleRows > 0) saveRecentSearch(value);
}, 500));

renderRecentSearches();
bindInventoryRowLinks(inventoryTableBody || document);
