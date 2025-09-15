document.getElementById('login-form').addEventListener('submit', function (event) {
    event.preventDefault();

    const username = document.getElementById('username').value;
    const password = document.getElementById('password').value;
    const messageElement = document.getElementById('message');

    // The backend expects form data, so we use URLSearchParams
    const details = new URLSearchParams();
    details.append('username', username);
    details.append('password', password);

    // IMPORTANT: Replace with your actual backend URL if it's not running on localhost:8000
    const backendUrl = 'http://129.226.123.47:8000/login';

    messageElement.textContent = 'Logging in...';

    fetch(backendUrl, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: details
    })
        .then(async response => {
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || 'Login failed');
            }
            return response.json();
        })
        .then(data => {
            console.log('Login successful:', data);
            messageElement.style.color = 'green';
            messageElement.textContent = 'Login successful!';
            // You can store the token for future requests
            localStorage.setItem('accessToken', data.access_token);
        })
        .catch(error => {
            console.error('Login error:', error);
            messageElement.style.color = 'red';
            messageElement.textContent = error.message;
        });
});
