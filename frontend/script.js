document.addEventListener('DOMContentLoaded', () => {
    // --- DOM Elements ---
    const userStatusDiv = document.getElementById('user-status');
    const loginModal = document.getElementById('login-modal');
    const registerModal = document.getElementById('register-modal');
    const closeBtns = document.querySelectorAll('.close-btn');

    const loginForm = document.getElementById('login-form');
    const registerForm = document.getElementById('register-form');
    const queryForm = document.getElementById('query-form');
    const itemIdInput = document.getElementById('item-id-input');
    const searchButton = queryForm.querySelector('button[type="submit"]');

    const loginMessage = document.getElementById('login-message');
    const registerMessage = document.getElementById('register-message');
    const queryResultDiv = document.getElementById('query-result');

    // --- API Configuration ---
    const API_BASE_URL = 'https://api.wowplayer.lol:8000';

    // --- State ---
    const token = localStorage.getItem('accessToken');
    let usageCount = 0;
    let cachedUser = null;
    let lastFetchTimestamp = 0;

    // --- Functions ---

    const updateSearchButtonState = () => {
        const itemId = itemIdInput.value.trim();
        if (usageCount > 0 && itemId !== '') {
            searchButton.disabled = false;
        } else {
            searchButton.disabled = true;
        }
    };

    /**
     * Logs the user in by fetching and storing a token.
     * @param {string} username 
     * @param {string} password 
     */
    const login = async (username, password) => {
        const details = new URLSearchParams({ username, password });
        const response = await fetch(`${API_BASE_URL}/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: details
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || err.error || 'Login failed');
        }

        const data = await response.json();
        localStorage.setItem('accessToken', data.access_token);
    };

    /**
     * Logs the user out.
     */
    const logout = () => {
        localStorage.removeItem('accessToken');
        cachedUser = null;
        lastFetchTimestamp = 0;
        location.reload();
    };

    const updateUIWithUserData = (user) => {
        usageCount = user.usage_count;
        userStatusDiv.innerHTML = `
            <div id="user-info">
                <div id="user-icon">🤖</div>
                <div class="tooltip">
                    <div><strong>User:</strong> ${user.username}</div>
                    <div><strong>API Calls Left:</strong> ${user.usage_count}</div>
                </div>
            </div>
            <button id="logout-btn">Logout</button>
        `;
        document.getElementById('logout-btn').addEventListener('click', logout);
        updateSearchButtonState();
    };

    /**
     * Fetches current user data and updates the UI.
     */
    const fetchUserAndUpdateUI = async () => {
        if (!token) {
            // Show logged-out state
            userStatusDiv.innerHTML = `
                <button id="login-btn">登录</button>
                <button id="register-btn">注册</button>
            `;
            document.getElementById('login-btn').addEventListener('click', () => showModal(loginModal));
            document.getElementById('register-btn').addEventListener('click', () => showModal(registerModal));
            return;
        }

        const now = Date.now();
        if (cachedUser && (now - lastFetchTimestamp < 30000)) {
            updateUIWithUserData(cachedUser);
            return;
        }

        try {
            // Fetch user and show logged-in state
            const response = await fetch(`${API_BASE_URL}/users/me`, {
                headers: { 'Authorization': `Bearer ${token}` }
            });

            if (response.status === 401) { // Token invalid
                logout();
                return;
            }

            if (!response.ok) {
                const errorData = await response.json();
                console.error('Error fetching user data:', errorData.detail || errorData.error || 'Unknown error');
                logout();
                return;
            }

            const user = await response.json();
            cachedUser = user;
            lastFetchTimestamp = Date.now();
            updateUIWithUserData(user);

        } catch (error) {
            console.error('Fetch user error:', error);
            logout(); // Log out if there's an error fetching user
        }
    };

    const showModal = (modal) => modal.style.display = 'block';
    const hideModals = () => {
        loginModal.style.display = 'none';
        registerModal.style.display = 'none';
    };

    // --- Event Listeners ---

    itemIdInput.addEventListener('input', updateSearchButtonState);

    closeBtns.forEach(btn => btn.addEventListener('click', hideModals));
    window.addEventListener('click', (event) => {
        if (event.target === loginModal || event.target === registerModal) {
            hideModals();
        }
    });

    loginForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const username = document.getElementById('login-username').value;
        const password = document.getElementById('login-password').value;

        try {
            loginMessage.textContent = 'Logging in...';
            await login(username, password);
            location.reload();
        } catch (error) {
            loginMessage.style.color = 'red';
            loginMessage.textContent = error.message;
        }
    });

    registerForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const username = document.getElementById('register-username').value;
        const password = document.getElementById('register-password').value;

        try {
            registerMessage.textContent = 'Registering...';
            const response = await fetch(`${API_BASE_URL}/register`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password })
            });

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || err.error || 'Registration failed');
            }

            // Automatically log in after successful registration
            registerMessage.textContent = 'Registration successful! Logging in...';
            await login(username, password);
            location.reload();

        } catch (error) {
            registerMessage.style.color = 'red';
            registerMessage.textContent = error.message;
        }
    });

    queryForm.addEventListener('submit', async (event) => {
        event.preventDefault();

        const itemId = itemIdInput.value.trim();
        if (searchButton.disabled || itemId === '') {
            return; // Prevent submission if button is disabled or input is empty
        }

        if (!token) {
            queryResultDiv.style.color = 'red';
            queryResultDiv.textContent = 'You must be logged in to use the query API.';
            return;
        }

        queryResultDiv.innerHTML = 'Querying...';

        try {
            let url = `${API_BASE_URL}/query`;
            if (itemId) {
                url += `?itemID=${itemId}`;
            }

            const response = await fetch(url, {
                headers: { 'Authorization': `Bearer ${token}` }
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || 'An error occurred.');
            }

            renderAuctionResults(data);

            // Refresh user data to get updated usage count
            lastFetchTimestamp = 0; // Invalidate cache
            fetchUserAndUpdateUI();

        } catch (error) {
            if (error.message === "Usage limit exceeded. No more access attempts allowed.") {
                queryResultDiv.style.color = 'red';
                queryResultDiv.textContent = '没有可用额度';
            } else {
                queryResultDiv.style.color = '#e0e0e0';
                queryResultDiv.textContent = '未找到物品。';
            }
        }
    });

    /**
     * Renders the auction query results into a table.
     * @param {object} resultData - The data object from the API.
     */
    const renderAuctionResults = (resultData) => {
        const { data, count } = resultData;
        queryResultDiv.innerHTML = ''; // Clear previous results
        queryResultDiv.style.color = '#e0e0e0';

        if (count === 0 || !data || data.length === 0) {
            queryResultDiv.textContent = '未找到物品。';
            return;
        }

        const resultCount = document.createElement('p');
        resultCount.textContent = `找到 ${count} 个物品。`;
        queryResultDiv.appendChild(resultCount);

        const table = document.createElement('table');
        table.className = 'results-table';

        const thead = document.createElement('thead');
        thead.innerHTML = `
            <tr>
                <th>物品ID</th>
                <th>名称</th>
                <th>数量</th>
                <th>一口价 (金)</th>
            </tr>
        `;
        table.appendChild(thead);

        const tbody = document.createElement('tbody');
        data.forEach(item => {
            const row = document.createElement('tr');
            
            // Format buyout amount from copper to gold
            const buyoutGold = (item.buyoutAmount / 10000).toFixed(2);

            row.innerHTML = `
                <td>${item.itemID}</td>
                <td>${item.name}</td>
                <td>${item.quantity}</td>
                <td>${buyoutGold}</td>
            `;
            tbody.appendChild(row);
        });
        table.appendChild(tbody);

        queryResultDiv.appendChild(table);
    };

    // --- Initial Load ---
    fetchUserAndUpdateUI();
});