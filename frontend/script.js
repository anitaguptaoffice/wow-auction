document.addEventListener('DOMContentLoaded', () => {
    // --- DOM Elements ---
    const userStatusDiv = document.getElementById('user-status');
    const loginModal = document.getElementById('login-modal');
    const registerModal = document.getElementById('register-modal');
    const closeBtns = document.querySelectorAll('.close-btn');

    const loginForm = document.getElementById('login-form');
    const registerForm = document.getElementById('register-form');
    const queryForm = document.getElementById('query-form');

    const loginMessage = document.getElementById('login-message');
    const registerMessage = document.getElementById('register-message');
    const queryResultDiv = document.getElementById('query-result');

    // --- API Configuration ---
    const API_BASE_URL = 'http://129.226.123.47:8000';

    // --- State ---
    const token = localStorage.getItem('accessToken');

    // --- Functions ---

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
            throw new Error(err.detail || 'Login failed');
        }

        const data = await response.json();
        localStorage.setItem('accessToken', data.access_token);
    };

    /**
     * Logs the user out.
     */
    const logout = () => {
        localStorage.removeItem('accessToken');
        location.reload();
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

        try {
            // Fetch user and show logged-in state
            const response = await fetch(`${API_BASE_URL}/users/me`, {
                headers: { 'Authorization': `Bearer ${token}` }
            });

            if (response.status === 401) { // Token invalid
                logout();
                return;
            }

            const user = await response.json();
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
                throw new Error(err.detail || 'Registration failed');
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
        if (!token) {
            queryResultDiv.style.color = 'red';
            queryResultDiv.textContent = 'You must be logged in to use the query API.';
            return;
        }

        queryResultDiv.textContent = 'Querying...';

        try {
            const response = await fetch(`${API_BASE_URL}/query`, {
                headers: { 'Authorization': `Bearer ${token}` }
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || 'An error occurred.');
            }

            queryResultDiv.style.color = '#e0e0e0';
            queryResultDiv.textContent = JSON.stringify(data, null, 2);

            // Refresh user data to get updated usage count
            fetchUserAndUpdateUI();

        } catch (error) {
            queryResultDiv.style.color = 'red';
            queryResultDiv.textContent = error.message;
        }
    });

    // --- Initial Load ---
    fetchUserAndUpdateUI();
});