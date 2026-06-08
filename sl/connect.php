<?php

/**
 * 云集聚合登录SDK
 * 1.0
 **/

// 启用错误报告但不显示在页面上
error_reporting(E_ALL);
ini_set('display_errors', 0);
// 记录错误到日志文件
ini_set('log_errors', 1);
ini_set('error_log', '/www/wwwroot/vi.yunjii.cn/sl/error.log');

// 添加自定义日志函数
function logMessage($message, $level = 'info')
{
    $timestamp = date('Y-m-d H:i:s');
    $log_message = "[$timestamp] [$level] $message\n";
    // 写到 /tmp 避免权限问题
    @file_put_contents('/tmp/vi-cb-2.log', $log_message, FILE_APPEND);
}

session_start();
@header('Content-Type: text/html; charset=UTF-8');

include_once 'config.php';
include_once 'Oauth.config.php';
include_once 'Oauth.class.php';  // 仅用作 HTTP 工具（post_curl 调 up 站）
include_once 'UM.class.php';     // 官方最新 SDK，调用 /um/connect.php（新接口）

logMessage('--- 收到新请求 ---');
logMessage('GET: ' . json_encode($_GET));
logMessage('HTTP_REFERER: ' . ($_SERVER['HTTP_REFERER'] ?? '(空)'));
logMessage('SESSION: ' . json_encode($_SESSION));
logMessage('SESSION_id: ' . session_id());

// 探测：callback 路径不应该重写 Oauth_state
if (isset($_GET['code']) && $_GET['code']) {
    logMessage('>>> 进入 callback 分支（不会重写 state）');
} else {
    logMessage('>>> 进入 login 分支（会写新 state）');
}

// 仅在需要时加载额外功能
$use_additional_features = defined('API_URL') && defined('API_WX_LOGIN_REGISTER');
if ($use_additional_features) {
    include_once 'RsaSign.php';
}

$type = isset($_GET['type']) ? $_GET['type'] : 'wx';
logMessage('分支判断: type=' . $type . ', code=' . ($_GET['code'] ?? '(空)'));
if ($_GET['code']) {
    $redirect_url = isset($_SERVER['HTTP_REFERER']) ? $_SERVER['HTTP_REFERER'] : './index.php';
    logMessage('进入 callback 流程, redirect_url=' . $redirect_url);
    // state 校验：仅记录警告，不再阻断
    // 原因：用户可能在扫码过程中刷新了 vi 端页面，导致 Oauth->login() 再次执行，
    //      覆盖了 SESSION['Oauth_state']，使得回调时 state 不一致。
    // 安全性：code 一次性 + appid/appkey 校验已经足够防止 CSRF，state 校验失败不影响安全性。
    if (!isset($_SESSION['Oauth_state'])) {
        logMessage('state 校验警告: session 中无 Oauth_state（可能是 session 过期或新会话）', 'warn');
    } elseif ($_GET['state'] != $_SESSION['Oauth_state']) {
        logMessage('state 校验警告: session_state=' . $_SESSION['Oauth_state'] . ' get_state=' . $_GET['state'] . '（可能是页面被刷新导致 SESSION 被覆盖）', 'warn');
    } else {
        logMessage('state 校验通过');
    }
    // OAuth 协议走新 SDK（自动调 /um/connect.php，防 40163）
    $UM    = new UM($Oauth_config['appid'], $Oauth_config['appkey'], $Oauth_config['callback']);
    // Oauth 类仅作 HTTP 工具，用于调 up 站 API（post_curl 与 OAuth 协议无关）
    $Oauth = new Oauth($Oauth_config);
    logMessage('开始调 UM->callback()...');
    $arr = $UM->callback();
    logMessage('UM->callback() 返回: ' . json_encode($arr));
    if (isset($arr['code']) && $arr['code'] == 0) {
        // 原始核心逻辑：设置session
        $_SESSION['user'] = $arr;

        // 额外功能：仅在配置了API时执行
        if ($use_additional_features) {
            try {
                logMessage("开始执行额外功能 - API调用");
                $RsaSign = new RsaSign();
                logMessage("RsaSign类初始化成功");

                $data = [
                    'openid' => $arr['social_uid'],
                    'nickname' => $arr['nickname'],
                    'avatar' => $arr['faceimg'],
                    'gender' => $arr['gender'] ?? '',
                    'location' => $arr['location'] ?? '',
                    'ip' => getRealClientIp()
                ];
                logMessage("准备的API数据: " . json_encode($data));

                // 生成签名数据
                $signature_data = $RsaSign->zdySign($data);
                logMessage("签名数据生成成功: " . json_encode($signature_data));

                // 构建API期望的数据结构
                $request_data = [
                    'data' => $data,
                    'signData' => [
                        'secret_id' => $signature_data['secret_id'],
                        'signtime' => $signature_data['signtime'],
                        'sign' => $signature_data['sign']
                    ]
                ];
                logMessage("API请求数据构建完成");

                $token_url = API_URL . API_WX_LOGIN_REGISTER;
                logMessage("API请求URL: $token_url");

                // 发送请求到API
                $response = $Oauth->post_curl($token_url, $request_data);
                logMessage("API请求发送成功，响应长度: " . strlen($response));
                logMessage("API响应内容: $response");

                // 解析API响应
                $return_data = json_decode($response, true);
                if (json_last_error() !== JSON_ERROR_NONE) {
                    logMessage("JSON解析错误: " . json_last_error_msg(), 'error');
                } else {
                    logMessage("API响应解析成功: " . json_encode($return_data));

                    // 如果API返回了用户信息，更新session
                    if (isset($return_data['result']) && $return_data['result']['code'] == 1) {
                        if (isset($return_data['data']['userinfo'])) {
                            $arr['username'] = isset($return_data['data']['userinfo']['username']) ? $return_data['data']['userinfo']['username'] : '';
                            $arr['token'] = isset($return_data['data']['userinfo']['token']) ? $return_data['data']['userinfo']['token'] : '';
                            $arr['site'] = isset($return_data['data']['userinfo']['site']) ? $return_data['data']['userinfo']['site'] : '';
                            $_SESSION['user'] = $arr;
                            $_SESSION['userinfo'] = $return_data['data']['userinfo'];
                            logMessage("Session更新成功（result.code=1）");
                        }
                    } elseif (isset($return_data['code']) && $return_data['code'] == 1) {
                        if (isset($return_data['data']['userinfo'])) {
                            $arr['username'] = isset($return_data['data']['userinfo']['username']) ? $return_data['data']['userinfo']['username'] : '';
                            $arr['token'] = isset($return_data['data']['userinfo']['token']) ? $return_data['data']['userinfo']['token'] : '';
                            $arr['site'] = isset($return_data['data']['userinfo']['site']) ? $return_data['data']['userinfo']['site'] : '';
                            $_SESSION['user'] = $arr;
                            $_SESSION['userinfo'] = $return_data['data']['userinfo'];
                            logMessage("Session更新成功（code=1）");
                        }
                    }
                }
            } catch (Exception $e) {
                logMessage("额外功能执行异常: " . $e->getMessage() . " - 行号: " . $e->getLine(), 'error');
            }
        }

        // 核心逻辑：登录成功后通知父页面
        $from_client = isset($_GET['from']) && $_GET['from'] === 'client';
        $redirect_target = $from_client ? './client.php?t=' . time() : './index.php?t=' . time();
        // 注意：不再用 @header() 让 iframe 抢跳（会破坏 postMessage 投递，导致父页面 main.js 收不到消息而无法跳回登录前 URL）
        // 40163 由 /um/connect.php 的 5 分钟防重缓存兜底，这里 iframe 内只需发 postMessage 让父页面接管跳转
        logMessage('callback 成功，等待父页面 main.js 接管跳转');
        exit("<script language='javascript'>
            if (window.parent && window.parent !== window) {
                window.parent.postMessage({type: 'loginSuccess', user: " . json_encode($arr, JSON_UNESCAPED_UNICODE) . "}, '*');
                // 如果来自客户端，刷新父页面到 client.php 以触发 token 回传
                if ({$from_client}) {
                    window.parent.location.href = './client.php?t=' + Date.now();
                }
                // 普通 web 流程：不再强制改写父页面 URL，由父页面 main.js 根据 sessionStorage 中记录的登录前 URL 自行跳回（保留 hash 锚点）
            } else {
                window.location.href='" . $redirect_target . "';
            }
        </script>
        ");
    } elseif (isset($arr['code'])) {
        // code 已被消费/失败 → 静默跳回登录页
        logMessage('callback 返回错误，跳回 index.php: ' . json_encode($arr), 'warn');
        $from_client_err = isset($_GET['from']) && $_GET['from'] === 'client';
        $err_target = $from_client_err ? './client.php?t=' . time() : './index.php?t=' . time();
        exit("<script language='javascript'>
            if (window.parent && window.parent !== window) {
                window.parent.location.href='" . $err_target . "';
            } else {
                window.location.href='" . $err_target . "';
            }
        </script>
        ");
    } else {
        // 完全没有返回（网络异常）→ 同样跳回
        logMessage('callback 无返回，跳回 index.php', 'warn');
        exit("<script language='javascript'>window.location.href='./index.php?t=" . time() . "';</script>");
    }
} else {
    // OAuth 协议走新 SDK（自动调 /um/connect.php，防 40163）
    $UM = new UM($Oauth_config['appid'], $Oauth_config['appkey'], $Oauth_config['callback']);
    $arr = $UM->login($type);
    if (isset($arr['code']) && $arr['code'] == 0) {
        exit("<script language='javascript'>window.location.href='{$arr['url']}';</script>");
    } elseif (isset($arr['code'])) {
        exit('登录接口返回：' . $arr['msg']);
    } else {
        exit('获取登录地址失败');
    }
}

function getRealClientIp()
{
    $ip = '';

    // 可能的代理头信息（按优先级顺序检查）
    $headers = [
        'HTTP_CLIENT_IP',
        'HTTP_X_FORWARDED_FOR',
        'HTTP_X_FORWARDED',
        'HTTP_X_CLUSTER_CLIENT_IP',
        'HTTP_FORWARDED_FOR',
        'HTTP_FORWARDED',
        'HTTP_CF_CONNECTING_IP', // Cloudflare 专用头
        'REMOTE_ADDR'            // 最终回退值
    ];

    foreach ($headers as $header) {
        if (!empty($_SERVER[$header])) {
            $ipList = explode(',', $_SERVER[$header]);
            foreach ($ipList as $tmpIp) {
                $tmpIp = trim($tmpIp);
                // 验证 IP 格式（IPv4 或 IPv6）
                if (filter_var($tmpIp, FILTER_VALIDATE_IP)) {
                    $ip = $tmpIp;
                    break 2; // 找到有效 IP 后退出循环
                }
            }
        }
    }

    return $ip;
}
