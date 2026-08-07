<?php
/**
 * 云集智能视频创意站 - 登录页面
 */
session_start();
header('Content-Type: text/html; charset=UTF-8');
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
            margin-bottom: 16px;
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
        .btn-back {
            display: inline-block;
            margin-top: 12px;
            padding: 8px 24px;
            background: #dc2626;
            border: none;
            color: #fff;
            border-radius: 8px;
            font-size: 14px;
            cursor: pointer;
            transition: all 0.2s;
            text-decoration: none;
        }
        .btn-back:hover {
            background: #b91c1c;
        }
    </style>
</head>
<body>
    <div class="login-card">
        <?php if (isset($_SESSION['user'])): ?>
            <!-- 已登录 -->
            <div class="user-card">
                <img class="user-avatar" src="<?php echo htmlspecialchars($_SESSION['user']['faceimg']); ?>" alt="头像">
                <div class="user-nickname"><?php echo htmlspecialchars($_SESSION['user']['nickname']); ?></div>
                <div class="user-id">ID: <?php echo htmlspecialchars($_SESSION['user']['social_uid']); ?></div>
                <?php
                    // ★ 2026-06-07 启动器门控:已登录后跳转本地前端,带用户信息
                    $yunji_user_payload = [
                        'nickname' => $_SESSION['user']['nickname'] ?? '',
                        'avatar'   => $_SESSION['user']['faceimg'] ?? '',
                        'openid'   => $_SESSION['user']['openid'] ?? ($_SESSION['user']['social_uid'] ?? ''),
                    ];
                    $yunji_user_b64 = base64_encode(json_encode($yunji_user_payload, JSON_UNESCAPED_UNICODE));
                ?>
                <a href="http://127.0.0.1:4000?yunji_user=<?php echo urlencode($yunji_user_b64); ?>" class="btn-back" style="background:#16a34a;margin-top:4px;">🚀 启动应用</a>
                <br>
                <a href="./logout.php" class="btn-logout" style="margin-top:12px;">退出登录</a>
            </div>
        <?php else: ?>
            <!-- 未登录 - 显示二维码 -->
            <img src="../image/ico.png" alt="云集" class="login-logo">
            <div class="login-title">微信扫码登录</div>
            <div class="login-subtitle">打开微信扫一扫，快速登录</div>
            <div class="qrcode-wrap">
                <iframe src="./connect.php?type=wx" width="150" height="150" frameborder="0" scrolling="no"></iframe>
            </div>
            <div class="login-hint">扫码即代表同意用户协议和隐私政策</div>
        <?php endif; ?>
    </div>
</body>
</html>
