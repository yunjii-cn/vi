<?php
/**
 * 云集智能视频创意站 - 退出登录
 */
session_start();
header("Cache-Control: no-cache, no-store, must-revalidate");
header("Pragma: no-cache");
header("Expires: 0");

$_SESSION = array();

if (ini_get("session.use_cookies")) {
    $params = session_get_cookie_params();
    setcookie(session_name(), '', time() - 42000,
        $params["path"], $params["domain"],
        $params["secure"], $params["httponly"]
    );
}

session_destroy();

// 判断请求来源：如果是 AJAX 请求或来自首页，跳转回首页
$redirect = '/';
if (isset($_SERVER['HTTP_REFERER'])) {
    $referer = $_SERVER['HTTP_REFERER'];
    // 如果来源是首页，跳转回首页
    if (strpos($referer, 'vi.yunjii.cn') !== false && strpos($referer, '/sl/') === false) {
        $redirect = $referer;
    }
}

header("Location: $redirect");
exit;
