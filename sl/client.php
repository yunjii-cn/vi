<?php
/**
 * 云集智能视频创意站 - EXE 专用登录页(client.php)
 *
 * 与 index.php 的区别:
 * - index.php:网页端登录页,扫码成功后留在 sl/ 显示用户卡片
 * - client.php:EXE 专用登录页,扫码成功后自动跳转回本地前端(127.0.0.1:4000)
 *              带 ?yunji_user=base64({nickname, avatar, ...}) 参数
 *
 * 触发方式:
 * - 启动器 webbrowser.open('https://vi.yunjii.cn/sl/client.php?from=client')
 * - 用户点"扫码登录"按钮(主窗口)
 *
 * 流程:
 * 1. 启动器 webbrowser.open 打开 client.php?from=client
 * 2. 用户在浏览器扫码
 * 3. connect.php 处理(支持 from=client)→ 跳回 client.php
 * 4. client.php 检测到已登录 + from=client → 显示倒计时 → 自动跳本地前端
 *
 * 本地前端地址(必须与启动器配置一致):
 * - 127.0.0.1:4000 是 _ui_server.py 代理端口(同源,可接收 URL 参数)
 */

error_reporting(E_ALL);
ini_set('display_errors', 0);
ini_set('log_errors', 1);
ini_set('error_log', '/www/wwwroot/vi.yunjii.cn/sl/error.log');

function logMessage($message, $level = 'info') {
    $timestamp = date('Y-m-d H:i:s');
    $log_message = "[$timestamp] [$level] $message\n";
    @error_log($log_message, 3, '/www/wwwroot/vi.yunjii.cn/sl/debug.log');
}

session_start();
@header('Content-Type: text/html; charset=UTF-8');

// 是否 EXE 唤起模式:启动器打开时带 ?from=client
$is_client = isset($_GET['from']) && $_GET['from'] === 'client';

// 本地前端地址(与启动器 _ui_server.py 端口一致)
$local_frontend_url = 'http://127.0.0.1:4000';

// 已登录时构造跳转 URL
$launch_url = '';
if (isset($_SESSION['user'])) {
    $yunji_user_payload = [
        'nickname' => $_SESSION['user']['nickname'] ?? '',
        'avatar'   => $_SESSION['user']['faceimg'] ?? '',
        'openid'   => $_SESSION['user']['openid'] ?? ($_SESSION['user']['social_uid'] ?? ''),
        'token'    => $_SESSION['user']['token'] ?? '',
    ];
    $yunji_user_b64 = base64_encode(json_encode($yunji_user_payload, JSON_UNESCAPED_UNICODE));
    $launch_url = $local_frontend_url . '?yunji_user=' . urlencode($yunji_user_b64);
}
?>
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title><?php echo $is_client ? '云集智能视频创意站 - 扫码登录' : '登录'; ?></title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0f172a 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
            color: #e2e8f0;
        }
        .container {
            max-width: 480px;
            width: 100%;
            background: rgba(30, 41, 59, 0.6);
            backdrop-filter: blur(20px);
            border: 1px solid rgba(148, 163, 184, 0.15);
            border-radius: 20px;
            padding: 40px 32px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
        }
        .logo {
            text-align: center;
            margin-bottom: 24px;
        }
        .logo-icon {
            font-size: 56px;
            line-height: 1;
            margin-bottom: 12px;
        }
        .logo-title {
            font-size: 22px;
            font-weight: 700;
            background: linear-gradient(135deg, #60a5fa, #a78bfa);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        .logo-subtitle {
            font-size: 12px;
            color: #94a3b8;
            margin-top: 6px;
        }
        .mode-badge {
            display: inline-block;
            padding: 4px 10px;
            background: rgba(34, 197, 94, 0.15);
            border: 1px solid rgba(34, 197, 94, 0.3);
            border-radius: 999px;
            font-size: 11px;
            color: #4ade80;
            font-weight: 600;
            margin-top: 12px;
        }
        .mode-badge.web { background: rgba(96, 165, 250, 0.15); border-color: rgba(96, 165, 250, 0.3); color: #60a5fa; }
        .user-card {
            text-align: center;
            padding: 24px 0;
        }
        .user-avatar {
            width: 88px;
            height: 88px;
            border-radius: 50%;
            object-fit: cover;
            border: 3px solid rgba(96, 165, 250, 0.4);
            margin-bottom: 14px;
        }
        .user-nickname {
            font-size: 20px;
            font-weight: 700;
            color: #f1f5f9;
            margin-bottom: 4px;
        }
        .user-id {
            font-size: 12px;
            color: #94a3b8;
            margin-bottom: 20px;
            font-family: ui-monospace, "Cascadia Code", monospace;
        }
        .btn-launch {
            display: inline-block;
            padding: 12px 32px;
            background: linear-gradient(135deg, #16a34a, #22c55e);
            color: #fff;
            text-decoration: none;
            border-radius: 10px;
            font-size: 15px;
            font-weight: 600;
            letter-spacing: 0.5px;
            box-shadow: 0 6px 20px rgba(22, 163, 74, 0.3);
            transition: all 0.2s;
            cursor: pointer;
            border: none;
        }
        .btn-launch:hover {
            transform: translateY(-1px);
            box-shadow: 0 8px 24px rgba(22, 163, 74, 0.4);
        }
        .btn-launch:active { transform: translateY(0); }
        .btn-logout {
            display: inline-block;
            margin-top: 14px;
            padding: 6px 16px;
            color: #94a3b8;
            text-decoration: none;
            font-size: 12px;
            border-radius: 6px;
            transition: color 0.2s;
        }
        .btn-logout:hover { color: #f87171; }
        .countdown {
            margin-top: 16px;
            font-size: 13px;
            color: #94a3b8;
        }
        .countdown strong {
            color: #4ade80;
            font-weight: 700;
            font-size: 16px;
        }
        .countdown-tip {
            font-size: 11px;
            color: #64748b;
            margin-top: 6px;
        }
        .qrcode-box {
            text-align: center;
            padding: 20px 0;
        }
        .qrcode-iframe {
            width: 240px;
            height: 240px;
            border: 1px solid rgba(148, 163, 184, 0.2);
            border-radius: 12px;
            background: #fff;
        }
        .qrcode-tip {
            font-size: 13px;
            color: #cbd5e1;
            margin-top: 16px;
            line-height: 1.6;
        }
        .qrcode-tip strong { color: #4ade80; }
        .qrcode-mode {
            display: inline-block;
            margin-top: 10px;
            padding: 3px 10px;
            background: rgba(34, 197, 94, 0.1);
            border: 1px solid rgba(34, 197, 94, 0.25);
            border-radius: 6px;
            font-size: 11px;
            color: #4ade80;
        }
        .footer {
            text-align: center;
            margin-top: 24px;
            font-size: 11px;
            color: #64748b;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">
            <div class="logo-icon">🎬</div>
            <div class="logo-title">云集智能视频创意站</div>
            <div class="logo-subtitle">AI 视频生成 · 一站式工作台</div>
            <?php if ($is_client): ?>
                <div class="mode-badge">📱 EXE 扫码登录</div>
            <?php else: ?>
                <div class="mode-badge web">🌐 网页登录</div>
            <?php endif; ?>
        </div>

        <?php if (isset($_SESSION['user']) && $is_client): ?>
            <!-- ═══ EXE 模式:已登录 → 倒计时跳本地前端 ═══ -->
            <div class="user-card">
                <img class="user-avatar" src="<?php echo htmlspecialchars($_SESSION['user']['faceimg']); ?>" alt="头像">
                <div class="user-nickname"><?php echo htmlspecialchars($_SESSION['user']['nickname']); ?></div>
                <div class="user-id">ID: <?php echo htmlspecialchars($_SESSION['user']['social_uid']); ?></div>

                <a id="launchBtn" href="<?php echo htmlspecialchars($launch_url); ?>" class="btn-launch">
                    🚀 启动应用
                </a>

                <div class="countdown">
                    <span id="cdText"><strong id="cdSec">3</strong> 秒后自动启动</span>
                </div>
                <div class="countdown-tip">如未自动跳转,请点击上方按钮</div>

                <a href="./logout.php" class="btn-logout">退出登录</a>
            </div>

            <script>
            (function() {
                var sec = 3;
                var secEl = document.getElementById('cdSec');
                var cdTextEl = document.getElementById('cdText');
                var launchUrl = <?php echo json_encode($launch_url); ?>;

                var timer = setInterval(function() {
                    sec--;
                    if (sec > 0) {
                        secEl.textContent = sec;
                    } else {
                        clearInterval(timer);
                        // window.location.href 是用户当前页面跳转,不会被弹窗拦截
                        window.location.href = launchUrl;
                    }
                }, 1000);

                // 如果用户立即点按钮,取消倒计时
                document.getElementById('launchBtn').addEventListener('click', function() {
                    clearInterval(timer);
                });
            })();
            </script>

        <?php elseif (isset($_SESSION['user'])): ?>
            <!-- ═══ 网页模式:已登录 → 显示用户卡片(无跳转) ═══ -->
            <div class="user-card">
                <img class="user-avatar" src="<?php echo htmlspecialchars($_SESSION['user']['faceimg']); ?>" alt="头像">
                <div class="user-nickname"><?php echo htmlspecialchars($_SESSION['user']['nickname']); ?></div>
                <div class="user-id">ID: <?php echo htmlspecialchars($_SESSION['user']['social_uid']); ?></div>
                <a href="/" class="btn-launch" style="background: linear-gradient(135deg, #2563eb, #3b82f6); box-shadow: 0 6px 20px rgba(37, 99, 235, 0.3);">
                    返回首页
                </a>
                <br>
                <a href="./logout.php" class="btn-logout" style="margin-top:14px;">退出登录</a>
            </div>

        <?php else: ?>
            <!-- ═══ 未登录 → 显示二维码 ═══ -->
            <div class="qrcode-box">
                <iframe class="qrcode-iframe" src="./connect.php?type=wx<?php echo $is_client ? '&from=client' : ''; ?>" frameborder="0"></iframe>
                <div class="qrcode-tip">
                    <?php if ($is_client): ?>
                        📱 <strong>用微信扫一扫</strong><br>
                        登录成功后会自动启动应用
                    <?php else: ?>
                        📱 <strong>用微信扫一扫</strong> 完成登录
                    <?php endif; ?>
                </div>
                <?php if ($is_client): ?>
                    <div class="qrcode-mode">EXE 专用 · 扫码后自动启动本地应用</div>
                <?php endif; ?>
            </div>
        <?php endif; ?>

        <div class="footer">
            云集智能视频创意站 · vi.yunjii.cn
        </div>
    </div>
</body>
</html>
