/**
 * API 根路径。部署为前后端同源时无需修改。
 * 分离部署时，可在加载本文件前设置：
 * window.WOW_AUCTION_API_BASE = "https://example.com/api";
 */
if (typeof window.WOW_AUCTION_API_BASE !== "string") {
    const cloudBaseApiByHost = {
        "raidbot-5gh3h2nx762bedc5-1251932919.tcloudbaseapp.com":
            "https://wow-auction-api-273424-4-1251932919.sh.run.tcloudbase.com/api",
    };
    window.WOW_AUCTION_API_BASE = cloudBaseApiByHost[window.location.hostname] || "/api";
}

/**
 * CloudBase Web SDK 公开配置。
 * accessKey 是浏览器可公开的 Publishable Key，不得替换为 API Key、SecretId 或 SecretKey。
 */
if (!window.WOW_AUCTION_CLOUDBASE_CONFIG) {
    window.WOW_AUCTION_CLOUDBASE_CONFIG = Object.freeze({
        env: "raidbot-5gh3h2nx762bedc5",
        region: "ap-shanghai",
        accessKey: "eyJhbGciOiJSUzI1NiIsImtpZCI6IjlkMWRjMzFlLWI0ZDAtNDQ4Yi1hNzZmLWIwY2M2M2Q4MTQ5OCJ9.eyJpc3MiOiJodHRwczovL3JhaWRib3QtNWdoM2gybng3NjJiZWRjNS5hcC1zaGFuZ2hhaS50Y2ItYXBpLnRlbmNlbnRjbG91ZGFwaS5jb20iLCJzdWIiOiJhbm9uIiwiYXVkIjoicmFpZGJvdC01Z2gzaDJueDc2MmJlZGM1IiwiZXhwIjo0MDc5ODQyNDgyLCJpYXQiOjE3NzYxNTkyODIsIm5vbmNlIjoibUpqaTZZSC1TeFduaWYwZ3JjRjVLdyIsImF0X2hhc2giOiJtSmppNllILVN4V25pZjBncmNGNUt3IiwibmFtZSI6IkFub255bW91cyIsInNjb3BlIjoiYW5vbnltb3VzIiwicHJvamVjdF9pZCI6InJhaWRib3QtNWdoM2gybng3NjJiZWRjNSIsIm1ldGEiOnsicGxhdGZvcm0iOiJQdWJsaXNoYWJsZUtleSJ9LCJ1c2VyX3R5cGUiOiIiLCJjbGllbnRfdHlwZSI6ImNsaWVudF91c2VyIiwiaXNfc3lzdGVtX2FkbWluIjpmYWxzZX0.i_tirxtzrsuKteY8ZVPKNOD33MNFvo-Oq-1A24_p4E2bHfEcU1TnR1Y9Tj7zgmeiv7UiVzKy3qKCrqPoetrwam_Ax5zXjja9ERm7GvIeXyG__2PXcwEJZCjPKKrV7IqMBnbWlZ0LF4Mx6jLyHqbIICxOoBFP1T4h2ZQ8qvknvIMJJ8JKAteakzdZtXh2D8JY5fK4XokDj6i3EMAjqAZEtS6TiyammYioSfuXaf5MZxYLArC_Vr6iRAVc5SvdCFXwgpm1M61IlTfoEpt_aMbV1inzZxm7PU48bUZ3NhXiESlC88fmk0q7pdmlPfDN-3tHjJ0Ju-lCGgEOfoE9uRBJgA",
    });
}
