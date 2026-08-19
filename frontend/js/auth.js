(function () {
    "use strict";

    const VIEW_COPY = {
        login: ["登录账户", "登录后可使用后续的个性化功能，市场浏览始终公开。"],
        register: ["注册账户", "使用邮箱验证码创建 CloudBase 账户。"],
        "register-verify": ["验证邮箱", "输入邮件中的验证码完成注册。"],
        "reset-request": ["找回密码", "通过注册邮箱接收密码重置验证码。"],
        "reset-verify": ["重置密码", "验证邮箱后设置新密码。"],
    };

    const elements = {};
    const state = {
        auth: null,
        user: null,
        initialized: false,
        pendingRegistration: null,
        pendingReset: null,
        returnFocus: null,
        subscription: null,
    };

    document.addEventListener("DOMContentLoaded", init);

    function init() {
        cacheElements();
        bindEvents();
        initializeCloudBase();
    }

    function cacheElements() {
        const ids = [
            "auth-open-button", "account-menu", "account-trigger", "account-dropdown",
            "account-avatar", "account-name", "account-display-name", "account-email",
            "auth-sign-out", "auth-modal", "auth-close-button", "auth-modal-title",
            "auth-modal-subtitle", "auth-message", "auth-login-view", "auth-register-view",
            "auth-register-verify-view", "auth-reset-request-view", "auth-reset-verify-view",
            "auth-login-form", "auth-login-identifier", "auth-login-password",
            "auth-register-form", "auth-register-email", "auth-register-username",
            "auth-register-password", "auth-register-confirm", "auth-register-target",
            "auth-register-verify-form", "auth-register-code", "auth-reset-request-form",
            "auth-reset-email", "auth-reset-target", "auth-reset-verify-form",
            "auth-reset-code", "auth-reset-password", "auth-reset-confirm",
        ];
        ids.forEach(function (id) {
            elements[toCamelCase(id)] = document.getElementById(id);
        });
    }

    function toCamelCase(value) {
        return value.replace(/-([a-z])/g, function (_, letter) { return letter.toUpperCase(); });
    }

    function bindEvents() {
        elements.authOpenButton.addEventListener("click", function () { openAuth("login"); });
        elements.authCloseButton.addEventListener("click", closeAuth);
        elements.authModal.addEventListener("click", function (event) {
            if (event.target === elements.authModal) closeAuth();
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && !elements.authModal.hidden) closeAuth();
        });

        elements.accountTrigger.addEventListener("click", function (event) {
            event.stopPropagation();
            const nextHidden = !elements.accountDropdown.hidden;
            elements.accountDropdown.hidden = nextHidden;
            elements.accountTrigger.setAttribute("aria-expanded", String(!nextHidden));
        });
        document.addEventListener("click", function (event) {
            if (!elements.accountMenu.contains(event.target)) closeAccountMenu();
        });
        elements.authSignOut.addEventListener("click", signOut);

        document.querySelectorAll("[data-auth-view]").forEach(function (button) {
            button.addEventListener("click", function () { showView(button.dataset.authView); });
        });
        elements.authLoginForm.addEventListener("submit", submitLogin);
        elements.authRegisterForm.addEventListener("submit", submitRegistration);
        elements.authRegisterVerifyForm.addEventListener("submit", verifyRegistration);
        elements.authResetRequestForm.addEventListener("submit", requestPasswordReset);
        elements.authResetVerifyForm.addEventListener("submit", verifyPasswordReset);
    }

    async function initializeCloudBase() {
        const config = window.WOW_AUCTION_CLOUDBASE_CONFIG;
        if (!window.CloudBaseSDK || !config || !config.env || !config.accessKey) {
            setAuthUnavailable("账户服务尚未配置，市场浏览不受影响。");
            return;
        }

        try {
            const app = window.CloudBaseSDK.init({
                env: config.env,
                region: config.region || "ap-shanghai",
                accessKey: config.accessKey,
                auth: { detectSessionInUrl: false },
            });
            state.auth = resolveAuth(app);
            if (!state.auth || typeof state.auth.getSession !== "function") {
                throw new Error("CloudBase Auth 模块未正确加载");
            }
            state.initialized = true;
            subscribeToAuthChanges();
            const result = await state.auth.getSession();
            if (result && result.error) throw result.error;
            updateCurrentUser(result && result.data && (result.data.user || (result.data.session && result.data.session.user)));
        } catch (error) {
            updateCurrentUser(null);
            state.initialized = Boolean(state.auth);
            console.warn("CloudBase Auth 初始化失败:", safeErrorCode(error));
        }
    }

    function resolveAuth(app) {
        if (!app) return null;
        if (app.auth && typeof app.auth.getSession === "function") return app.auth;
        if (typeof app.auth === "function") return app.auth();
        return null;
    }

    function subscribeToAuthChanges() {
        if (!state.auth || typeof state.auth.onAuthStateChange !== "function") return;
        const result = state.auth.onAuthStateChange(function (event, session, info) {
            if (info && info.error) return;
            if (event === "SIGNED_OUT") {
                updateCurrentUser(null);
                return;
            }
            if (["INITIAL_SESSION", "SIGNED_IN", "TOKEN_REFRESHED", "USER_UPDATED"].includes(event)) {
                updateCurrentUser(session && session.user);
            }
        });
        state.subscription = result && result.data && result.data.subscription;
    }

    function setAuthUnavailable(message) {
        state.initialized = false;
        elements.authOpenButton.disabled = true;
        elements.authOpenButton.title = message;
        const label = elements.authOpenButton.lastElementChild;
        if (label) label.textContent = "账户不可用";
    }

    function updateCurrentUser(user) {
        if (user && user.is_anonymous) user = null;
        state.user = user || null;
        const signedIn = Boolean(state.user);
        elements.authOpenButton.hidden = signedIn;
        elements.accountMenu.hidden = !signedIn;
        if (!signedIn) {
            closeAccountMenu();
            return;
        }

        const displayName = userDisplayName(state.user);
        const email = String(state.user.email || "");
        const avatarText = Array.from(displayName || email || "用户")[0].toUpperCase();
        elements.accountAvatar.textContent = avatarText;
        elements.accountName.textContent = displayName;
        elements.accountDisplayName.textContent = displayName;
        elements.accountEmail.textContent = email;
        elements.accountEmail.hidden = !email;
    }

    function userDisplayName(user) {
        const metadata = user.user_metadata || user.userMetadata || {};
        return String(
            metadata.username
            || metadata.nickname
            || metadata.nickName
            || user.username
            || user.email
            || "CloudBase 用户"
        );
    }

    function openAuth(view) {
        if (!state.initialized) return;
        state.returnFocus = document.activeElement;
        showView(view || "login");
        elements.authModal.hidden = false;
        document.body.classList.add("modal-open");
        window.setTimeout(focusFirstVisibleField, 0);
    }

    function closeAuth() {
        if (elements.authModal.hidden) return;
        elements.authModal.hidden = true;
        clearMessage();
        const detailsModal = document.getElementById("details-modal");
        if (!detailsModal || detailsModal.hidden) document.body.classList.remove("modal-open");
        if (state.returnFocus && typeof state.returnFocus.focus === "function") state.returnFocus.focus();
    }

    function showView(view) {
        const copy = VIEW_COPY[view] || VIEW_COPY.login;
        elements.authModalTitle.textContent = copy[0];
        elements.authModalSubtitle.textContent = copy[1];
        ["login", "register", "register-verify", "reset-request", "reset-verify"].forEach(function (name) {
            elements[toCamelCase("auth-" + name + "-view")].hidden = name !== view;
        });
        clearMessage();
        if (!elements.authModal.hidden) window.setTimeout(focusFirstVisibleField, 0);
    }

    function focusFirstVisibleField() {
        const view = elements.authModal.querySelector(".auth-view:not([hidden])");
        const field = view && view.querySelector("input:not([disabled])");
        if (field) field.focus();
    }

    async function submitLogin(event) {
        event.preventDefault();
        clearMessage();
        const identifier = elements.authLoginIdentifier.value.trim();
        const password = elements.authLoginPassword.value;
        if (!identifier || !password) return;

        await withBusyForm(elements.authLoginForm, "正在登录…", async function () {
            const credentials = identifier.includes("@")
                ? { email: identifier, password: password }
                : { username: identifier, password: password };
            const result = await state.auth.signInWithPassword(credentials);
            ensureSuccess(result, "login");
            const user = result.data && (result.data.user || (result.data.session && result.data.session.user));
            updateCurrentUser(user);
            elements.authLoginPassword.value = "";
            closeAuth();
        });
    }

    async function submitRegistration(event) {
        event.preventDefault();
        clearMessage();
        const email = elements.authRegisterEmail.value.trim().toLowerCase();
        const username = elements.authRegisterUsername.value.trim();
        const password = elements.authRegisterPassword.value;
        if (password !== elements.authRegisterConfirm.value) {
            showMessage("两次输入的密码不一致。", "error");
            return;
        }

        await withBusyForm(elements.authRegisterForm, "正在发送验证码…", async function () {
            const result = await state.auth.signUp({ email: email, username: username, password: password });
            ensureSuccess(result, "register");
            if (!result.data || typeof result.data.verifyOtp !== "function") {
                throw new Error("未收到验证码确认流程，请稍后重试。");
            }
            state.pendingRegistration = { email: email, verifyOtp: result.data.verifyOtp };
            elements.authRegisterTarget.textContent = email;
            elements.authRegisterCode.value = "";
            showView("register-verify");
        });
    }

    async function verifyRegistration(event) {
        event.preventDefault();
        clearMessage();
        if (!state.pendingRegistration) {
            showMessage("注册流程已失效，请重新发送验证码。", "error");
            showView("register");
            return;
        }
        const code = elements.authRegisterCode.value.trim();

        await withBusyForm(elements.authRegisterVerifyForm, "正在验证…", async function () {
            const result = await state.pendingRegistration.verifyOtp({ token: code });
            ensureSuccess(result, "verify");
            const user = result.data && (result.data.user || (result.data.session && result.data.session.user));
            state.pendingRegistration = null;
            updateCurrentUser(user);
            showMessage("注册成功，已经为你登录。", "success");
            window.setTimeout(closeAuth, 550);
        });
    }

    async function requestPasswordReset(event) {
        event.preventDefault();
        clearMessage();
        const email = elements.authResetEmail.value.trim().toLowerCase();

        await withBusyForm(elements.authResetRequestForm, "正在发送验证码…", async function () {
            const result = await state.auth.resetPasswordForEmail(email);
            ensureSuccess(result, "reset");
            if (!result.data || typeof result.data.updateUser !== "function") {
                throw new Error("未收到密码重置流程，请稍后重试。");
            }
            state.pendingReset = { email: email, updateUser: result.data.updateUser };
            elements.authResetTarget.textContent = email;
            elements.authResetCode.value = "";
            elements.authResetPassword.value = "";
            elements.authResetConfirm.value = "";
            showView("reset-verify");
        });
    }

    async function verifyPasswordReset(event) {
        event.preventDefault();
        clearMessage();
        if (!state.pendingReset) {
            showMessage("密码重置流程已失效，请重新发送验证码。", "error");
            showView("reset-request");
            return;
        }
        const password = elements.authResetPassword.value;
        if (password !== elements.authResetConfirm.value) {
            showMessage("两次输入的新密码不一致。", "error");
            return;
        }
        const code = elements.authResetCode.value.trim();

        await withBusyForm(elements.authResetVerifyForm, "正在更新密码…", async function () {
            const result = await state.pendingReset.updateUser({ nonce: code, password: password });
            ensureSuccess(result, "verify");
            const user = result.data && (result.data.user || (result.data.session && result.data.session.user));
            state.pendingReset = null;
            updateCurrentUser(user);
            showMessage(user ? "密码已更新，已经为你登录。" : "密码已更新，请使用新密码登录。", "success");
            if (user) {
                window.setTimeout(closeAuth, 650);
            } else {
                window.setTimeout(function () { showView("login"); }, 800);
            }
        });
    }

    async function signOut() {
        if (!state.auth) return;
        elements.authSignOut.disabled = true;
        elements.authSignOut.textContent = "正在退出…";
        try {
            const result = await state.auth.signOut();
            if (result && result.error) throw result.error;
            updateCurrentUser(null);
        } catch (error) {
            console.warn("CloudBase 退出失败:", safeErrorCode(error));
        } finally {
            elements.authSignOut.disabled = false;
            elements.authSignOut.textContent = "退出登录";
            closeAccountMenu();
        }
    }

    async function withBusyForm(form, busyText, action) {
        const button = form.querySelector('button[type="submit"]');
        const originalText = button.textContent;
        button.disabled = true;
        button.textContent = busyText;
        try {
            await action();
        } catch (error) {
            showMessage(userFacingError(error), "error");
        } finally {
            button.disabled = false;
            button.textContent = originalText;
        }
    }

    function ensureSuccess(result, context) {
        if (result && result.error) {
            const source = result.error;
            const error = new Error(source.message || "认证请求失败。");
            error.code = source.code;
            error.category = source.category;
            error.helpMessage = source.helpMessage;
            error.authContext = context;
            throw error;
        }
        if (!result || !result.data) throw new Error("认证服务没有返回有效结果。");
    }

    function userFacingError(error) {
        const code = safeErrorCode(error);
        const context = error && error.authContext;
        if (context === "login" && ["not_found", "invalid_password", "invalid_credentials", "unauthenticated"].includes(code)) {
            return "邮箱、用户名或密码不正确。";
        }
        if (code === "resource_exhausted" || code === "rate_limited" || code === "aborted") {
            return "操作过于频繁，请稍后再试。";
        }
        if (code === "captcha_required" || code === "captcha_invalid") {
            return "需要完成安全验证，请稍后重试。";
        }
        if (code === "invalid_argument" || code === "code_expired") {
            return context === "verify" ? "验证码已过期或不正确，请重新获取。" : "输入内容不符合要求，请检查后重试。";
        }
        if (context === "register" && code === "failed_precondition") {
            return "该邮箱已注册，请直接登录或找回密码。";
        }
        if (code === "permission_denied" || code === "provider_not_enabled") {
            return "当前登录方式尚未启用，请联系站点管理员。";
        }
        return String((error && (error.helpMessage || error.message)) || "认证服务暂时不可用，请稍后重试。");
    }

    function safeErrorCode(error) {
        return String((error && (error.code || error.category)) || "unknown").toLowerCase();
    }

    function showMessage(message, kind) {
        elements.authMessage.textContent = message;
        elements.authMessage.dataset.kind = kind || "info";
        elements.authMessage.hidden = false;
    }

    function clearMessage() {
        elements.authMessage.hidden = true;
        elements.authMessage.textContent = "";
        delete elements.authMessage.dataset.kind;
    }

    function closeAccountMenu() {
        elements.accountDropdown.hidden = true;
        elements.accountTrigger.setAttribute("aria-expanded", "false");
    }

    window.WowAuctionAuth = Object.freeze({
        getCurrentUser: function () { return state.user; },
        getSession: function () {
            return state.auth ? state.auth.getSession() : Promise.resolve({ data: {}, error: null });
        },
    });
}());
