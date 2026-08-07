<?php
/**
 * 云集智能视频创意站 - 客户端登录页面
 * 供桌面软件 WebView 调用，界面与网站登录页完全一致
 * 登录成功后通过自定义协议 vi:// 回传 token 给客户端
 */
session_start();
header('Content-Type: text/html; charset=UTF-8');

// 检查是否已登录
$is_logged_in = isset($_SESSION['user']);
$user = $is_logged_in ? $_SESSION['user'] : null;
$userinfo = isset($_SESSION['userinfo']) ? $_SESSION['userinfo'] : null;

// 合并 token 信息
$token = '';
if ($userinfo && isset($userinfo['token'])) {
    $token = $userinfo['token'];
} elseif ($user && isset($user['token'])) {
    $token = $user['token'];
}
?>
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>登录 - 云集智能视频创意站</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #0a0a0a;
            color: #fff;
            font-family: "Microsoft YaHei", -apple-system, BlinkMacSystemFont, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
        }
        .login-card {
            background: #1a1a1a;
            border: 1px solid #2a2a2a;
            border-radius: 16px;
            padding: 40px 32px;
            width: 360px;
            text-align: center;
            box-shadow: 0 8px 32px rgba(0,0,0,0.4);
        }
        .login-logo {
            width: 48px;
            height: 48px;
            border-radius: 12px;
            margin: 0 auto 16px auto;
            display: block;
        }
        .login-title {
            font-size: 20px;
            font-weight: 600;
            margin-bottom: 8px;
            color: #fff;
        }
        .login-subtitle {
            font-size: 13px;
            color: #888;
            margin-bottom: 28px;
        }
        .qrcode-wrap {
            background: #222222;
            border: 2px solid #cc0000;
            border-radius: 12px;
            padding: 12px;
            display: inline-block;
            margin-bottom: 20px;
        }
        .qrcode-wrap iframe {
            border: none;
            display: block;
        }
        .login-hint {
            font-size: 12px;
            color: #666;
            margin-top: 16px;
        }
        /* 已登录状态 */
        .user-card {
            text-align: center;
        }
        .user-avatar {
            width: 64px;
            height: 64px;
            border-radius: 50%;
            border: 2px solid #dc2626;
            object-fit: cover;
            margin-bottom: 12px;
        }
        .user-nickname {
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 4px;
        }
        .user-id {
            font-size: 12px;
            color: #666;
            margin-bottom: 20px;
        }
        .btn-logout {
            display: inline-block;
            padding: 8px 24px;
            background: transparent;
            border: 1px solid #dc2626;
            color: #dc2626;
            border-radius: 8px;
            font-size: 14px;
            cursor: pointer;
            transition: all 0.2s;
            text-decoration: none;
        }
        .btn-logout:hover {
            background: #dc2626;
            color: #fff;
        }
        /* 登录成功过渡动画 */
        .success-icon {
            width: 56px;
            height: 56px;
            border-radius: 50%;
            background: #dc2626;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 16px auto;
            animation: scaleIn 0.3s ease;
        }
        .success-icon svg {
            width: 28px;
            height: 28px;
            stroke: #fff;
            fill: none;
            stroke-width: 3;
            stroke-linecap: round;
            stroke-linejoin: round;
        }
        @keyframes scaleIn {
            from { transform: scale(0); }
            to { transform: scale(1); }
        }
        .success-text {
            font-size: 16px;
            color: #fff;
            margin-bottom: 8px;
        }
        .success-hint {
            font-size: 12px;
            color: #666;
        }
    </style>
</head>
<body>
    <div class="login-card">
        <?php if ($is_logged_in): ?>
            <!-- 已登录 - 显示成功状态并通知客户端 -->
            <div class="user-card">
                <div class="success-icon">
                    <svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>
                </div>
                <div class="success-text">登录成功</div>
                <img class="user-avatar" src="<?php echo htmlspecialchars($user['faceimg']); ?>" alt="头像">
                <div class="user-nickname"><?php echo htmlspecialchars($user['nickname']); ?></div>
                <div class="user-id">ID: <?php echo htmlspecialchars($user['social_uid']); ?></div>
                <div class="success-hint">正在返回客户端...</div>
                <br>
                <a href="./logout.php?from=client" class="btn-logout">退出登录</a>
            </div>
            <script>
                // 构建回传数据
                var loginData = {
                    token: <?php echo json_encode($token); ?>,
                    nickname: <?php echo json_encode($user['nickname'] ?? ''); ?>,
                    avatar: <?php echo json_encode($user['faceimg'] ?? ''); ?>,
                    openid: <?php echo json_encode($user['social_uid'] ?? ''); ?>,
                    username: <?php echo json_encode($user['username'] ?? ''); ?>,
                    site: <?php echo json_encode($user['site'] ?? ''); ?>
                };

                // 方式1: 通过自定义协议 vi:// 回传（推荐）
                var params = Object.keys(loginData).map(function(k) {
                    return k + '=' + encodeURIComponent(loginData[k]);
                }).join('&');
                var viUrl = 'vi://login?' + params;

                // 延迟1秒后跳转，让用户看到成功状态
                setTimeout(function() {
                    // 尝试通过自定义协议通知客户端
                    window.location.href = viUrl;
                }, 1000);

                // 方式2: 通过 postMessage 通知父窗口（WebView2 可监听）
                if (window.chrome && window.chrome.webview) {
                    window.chrome.webview.postMessage({
                        type: 'loginSuccess',
                        data: loginData
                    });
                }

                // 方式3: 将数据写入页面供 WebView 读取
                window.__LOGIN_DATA__ = loginData;
            </script>
        <?php else: ?>
            <!-- 未登录 - 显示二维码 -->
            <img src="../image/ico.png" alt="云集" class="login-logo">
            <div class="login-title">微信扫码登录</div>
            <div class="login-subtitle">打开微信扫一扫，快速登录</div>
            <div class="qrcode-wrap">
                <iframe src="./connect.php?type=wx&from=client" width="150" height="150" frameborder="0" scrolling="no"></iframe>
            </div>
            <div class="login-hint">扫码即代表同意用户协议和隐私政策</div>
        <?php endif; ?>
    </div>
</body>
</html>
