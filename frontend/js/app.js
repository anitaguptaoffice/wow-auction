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
