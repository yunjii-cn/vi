<?php
/**
 * 云集智能视频创意站 - 登录状态检查（API接口）
 * 供客户端软件调用，返回JSON格式登录状态
 *
 * 支持两种认证方式：
 * 1. Session 认证（浏览器访问，Cookie 自动携带）
 * 2. Token 认证（桌面软件，通过 Authorization 头或 token 参数传递）
 */
session_start();
header('Content-Type: application/json; charset=UTF-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Headers: Content-Type, Authorization');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');

// 处理 OPTIONS 预检请求
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(204);
    exit;
}

include_once 'config.php';

// 优先使用 Session 认证
if (isset($_SESSION['user'])) {
    outputLoggedIn($_SESSION['user']);
    exit;
}

// Token 认证：桌面软件通过 Authorization 头或 token 参数传递
$token = '';
$auth_header = isset($_SERVER['HTTP_AUTHORIZATION']) ? $_SERVER['HTTP_AUTHORIZATION'] : '';
if (preg_match('/Bearer\s+(.+)/i', $auth_header, $matches)) {
    $token = $matches[1];
}
if (empty($token) && isset($_GET['token'])) {
    $token = $_GET['token'];
}
if (empty($token) && isset($_POST['token'])) {
    $token = $_POST['token'];
}

if (!empty($token)) {
    // 通过 token 调用用户中心 API 验证
    $result = verifyTokenWithAPI($token);
    if ($result) {
        outputLoggedIn($result);
    } else {
        echo json_encode(['code' => 0, 'msg' => 'token无效或已过期']);
    }
    exit;
}

// 未登录
echo json_encode(['code' => 0, 'msg' => '未登录']);

function outputLoggedIn($user) {
    echo json_encode([
        'code' => 1,
        'data' => [
            'nickname'     => $user['nickname'] ?? '',
            'avatar'       => $user['faceimg'] ?? '',
            'openid'       => $user['social_uid'] ?? '',
            'username'     => $user['username'] ?? '',
            'token'        => $user['token'] ?? '',
            'site'         => $user['site'] ?? '',
            'gender'       => $user['gender'] ?? '',
            'location'     => $user['location'] ?? '',
            'access_token' => $user['access_token'] ?? '',
            'type'         => $user['type'] ?? '',
        ]
    ]);
}

/**
 * 通过 token 调用用户中心 API 验证用户身份
 * 返回用户信息数组或 false
 */
function verifyTokenWithAPI($token) {
    if (!defined('API_URL')) return false;

    $url = API_URL . '/dtapi/user/check_token';
    $data = json_encode(['token' => $token]);

    $ch = curl_init();
    curl_setopt($ch, CURLOPT_URL, $url);
    curl_setopt($ch, CURLOPT_POST, 1);
    curl_setopt($ch, CURLOPT_POSTFIELDS, $data);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_HTTPHEADER, [
        'Content-Type: application/json',
        'Content-Length: ' . strlen($data)
    ]);
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
    curl_setopt($ch, CURLOPT_SSL_VERIFYHOST, false);
    curl_setopt($ch, CURLOPT_TIMEOUT, 10);

    $response = curl_exec($ch);
    $http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if ($http_code !== 200 || empty($response)) return false;

    $result = json_decode($response, true);
    if (!$result) return false;

    // 兼容两种响应格式
    $userinfo = null;
    if (isset($result['result']) && $result['result']['code'] == 1) {
        $userinfo = $result['data']['userinfo'] ?? null;
    } elseif (isset($result['code']) && $result['code'] == 1) {
        $userinfo = $result['data']['userinfo'] ?? null;
    }

    if ($userinfo) {
        return [
            'nickname'   => $userinfo['nickname'] ?? '',
            'faceimg'    => $userinfo['avatar'] ?? '',
            'social_uid' => $userinfo['openid'] ?? '',
            'username'   => $userinfo['username'] ?? '',
            'token'      => $token,
            'site'       => $userinfo['site'] ?? '',
        ];
    }

    return false;
}
