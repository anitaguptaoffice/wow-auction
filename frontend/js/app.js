// Modal Logic
function openDetailsModal(itemName) {
    document.getElementById('modal-item-name').textContent = itemName;
    document.getElementById('item-details-modal').classList.remove('hidden');
    document.body.style.overflow = 'hidden';
}

function closeDetailsModal() {
    document.getElementById('item-details-modal').classList.add('hidden');
    document.body.style.overflow = 'auto';
}

// Toggle Advanced Filters
function toggleAdvancedFilters() {
    const panel = document.getElementById('advanced-filters-panel');
    panel.classList.toggle('hidden');
}

// Dropdown Logic
function toggleUserDropdown() {
    const dropdown = document.getElementById('user-dropdown');
    dropdown.classList.toggle('show');
}

// Close dropdown when clicking outside
window.addEventListener('click', function(e) {
    const dropdown = document.getElementById('user-dropdown');
    const trigger = document.getElementById('user-avatar-trigger');
    if (trigger && dropdown && !trigger.contains(e.target) && !dropdown.contains(e.target)) {
        dropdown.classList.remove('show');
    }
});

// SPA View Switching Logic
function showView(viewName) {
    const browseView = document.getElementById('view-browse');
    const favoritesView = document.getElementById('view-favorites');
    const btnBrowse = document.getElementById('btn-browse');
    const btnFavorites = document.getElementById('btn-favorites');

    if (viewName === 'browse') {
        browseView.classList.remove('view-hidden');
        favoritesView.classList.add('view-hidden');
        btnBrowse.classList.add('text-wow-accent', 'border-b-2', 'border-wow-accent');
        btnBrowse.classList.remove('text-gray-500');
        btnFavorites.classList.remove('text-wow-accent', 'border-b-2', 'border-wow-accent');
        btnFavorites.classList.add('text-gray-500');
    } else {
        browseView.classList.add('view-hidden');
        favoritesView.classList.remove('view-hidden');
        btnFavorites.classList.add('text-wow-accent', 'border-b-2', 'border-wow-accent');
        btnFavorites.classList.remove('text-gray-500');
        btnBrowse.classList.remove('text-wow-accent', 'border-b-2', 'border-wow-accent');
        btnBrowse.classList.add('text-gray-500');
    }
}

// Theme Toggle Logic
const themeToggleBtn = document.getElementById('theme-toggle');
const themeIcon = document.getElementById('theme-icon');

function updateThemeUI() {
    if (document.documentElement.classList.contains('dark')) {
        themeIcon.textContent = 'light_mode';
    } else {
        themeIcon.textContent = 'dark_mode';
    }
}

// Default to light mode as requested (Parchment)
if (localStorage.getItem('color-theme') === 'dark') {
    document.documentElement.classList.add('dark');
} else {
    document.documentElement.classList.remove('dark');
}
updateThemeUI();

themeToggleBtn.addEventListener('click', function() {
    if (document.documentElement.classList.contains('dark')) {
        document.documentElement.classList.remove('dark');
        localStorage.setItem('color-theme', 'light');
    } else {
        document.documentElement.classList.add('dark');
        localStorage.setItem('color-theme', 'dark');
    }
    updateThemeUI();
});

// ─── 后端 API：登录与历史价格 ───
function getApiBase() {
    const c = window.WOW_AUCTION_CONFIG;
    return (c && c.apiBaseUrl) ? c.apiBaseUrl.replace(/\/$/, '') : '';
}

function formatCopperShort(copper) {
    if (copper == null || isNaN(copper)) return '—';
    const c = Math.floor(Number(copper));
    const g = Math.floor(c / 10000);
    const s = Math.floor((c % 10000) / 100);
    const cp = c % 100;
    if (g > 0) return g + '金' + s + '银';
    if (s > 0) return s + '银' + cp + '铜';
    return cp + '铜';
}

let priceChartInstance = null;

async function apiLogin() {
    const user = document.getElementById('api-username');
    const pass = document.getElementById('api-password');
    const status = document.getElementById('api-auth-status');
    if (!user || !pass || !status) return;
    const base = getApiBase();
    try {
        const body = new URLSearchParams();
        body.set('username', user.value.trim());
        body.set('password', pass.value);
        const r = await fetch(base + '/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body,
        });
        const data = await r.json().catch(function() { return {}; });
        if (!r.ok) {
            status.textContent = data.detail || ('HTTP ' + r.status);
            status.classList.add('text-red-500');
            return;
        }
        if (data.access_token) {
            localStorage.setItem('wow_auction_token', data.access_token);
            status.textContent = '已登录，剩余查询次数见趋势加载后提示';
            status.classList.remove('text-red-500');
        }
    } catch (e) {
        status.textContent = String(e.message || e);
        status.classList.add('text-red-500');
    }
}

function chartTextColor() {
    return document.documentElement.classList.contains('dark') ? '#e5e5e5' : '#374151';
}

function chartGridColor() {
    return document.documentElement.classList.contains('dark') ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)';
}

async function loadPriceTrend() {
    const token = localStorage.getItem('wow_auction_token');
    const meta = document.getElementById('trend-meta');
    if (!token) {
        if (meta) meta.textContent = '请先在本栏登录 API';
        return;
    }
    const idEl = document.getElementById('trend-item-id');
    const daysEl = document.getElementById('trend-days');
    const linkEl = document.getElementById('trend-item-link');
    const itemID = parseInt(idEl && idEl.value, 10);
    const days = daysEl ? parseInt(daysEl.value, 10) : 7;
    if (!itemID || itemID <= 0) {
        if (meta) meta.textContent = '请输入有效物品 ID';
        return;
    }
    const itemLinkRaw = linkEl && linkEl.value.trim();
    let url = getApiBase() + '/query/history?itemID=' + encodeURIComponent(itemID) + '&days=' + encodeURIComponent(days);
    if (itemLinkRaw) url += '&itemLink=' + encodeURIComponent(itemLinkRaw);

    if (meta) meta.textContent = '加载中…';
    try {
        const r = await fetch(url, { headers: { Authorization: 'Bearer ' + token } });
        const data = await r.json().catch(function() { return {}; });
        if (!r.ok) {
            if (meta) meta.textContent = data.detail || ('HTTP ' + r.status);
            return;
        }
        const series = data.series || [];
        const remaining = data.remaining_uses != null ? data.remaining_uses : '—';
        if (meta) {
            meta.textContent = '数据点 ' + series.length + ' 个 · 剩余额度 ' + remaining;
        }

        const labels = series.map(function(p) {
            const d = new Date((p.timestamp || 0) * 1000);
            return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + d.getHours() + ':' + (d.getMinutes() < 10 ? '0' : '') + d.getMinutes();
        });
        const values = series.map(function(p) { return (p.buyoutAmount || 0) / 10000; });

        const canvas = document.getElementById('price-chart');
        if (!canvas || typeof Chart === 'undefined') return;

        if (priceChartInstance) {
            priceChartInstance.destroy();
            priceChartInstance = null;
        }

        const ctx = canvas.getContext('2d');
        priceChartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: '一口价 (金)',
                    data: values,
                    borderColor: 'rgb(180, 140, 60)',
                    backgroundColor: 'rgba(180, 140, 60, 0.15)',
                    fill: true,
                    tension: 0.2,
                    pointRadius: 2,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { labels: { color: chartTextColor() } },
                    tooltip: {
                        callbacks: {
                            label: function(ctx) {
                                const p = series[ctx.dataIndex];
                                if (!p) return '';
                                const lines = [
                                    '一口价: ' + formatCopperShort(p.buyoutAmount),
                                    '起拍: ' + formatCopperShort(p.minBid),
                                    '竞拍: ' + formatCopperShort(p.bidAmount),
                                ];
                                if (p.timeLeftLabel) {
                                    lines.push('剩余: ' + p.timeLeftLabel);
                                } else if (p.timeLeftBand != null && p.timeLeftBand !== '') {
                                    lines.push('剩余档: ' + p.timeLeftBand);
                                }
                                return lines;
                            },
                        },
                    },
                },
                scales: {
                    x: {
                        ticks: { color: chartTextColor(), maxRotation: 45, minRotation: 45, font: { size: 9 } },
                        grid: { color: chartGridColor() },
                    },
                    y: {
                        ticks: { color: chartTextColor() },
                        grid: { color: chartGridColor() },
                    },
                },
            },
        });
    } catch (e) {
        if (meta) meta.textContent = String(e.message || e);
    }
}

(function initAuthStatus() {
    const t = localStorage.getItem('wow_auction_token');
    const status = document.getElementById('api-auth-status');
    if (status && t) status.textContent = '已保存 Token（可加载趋势）';
})();
