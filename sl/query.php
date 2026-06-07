<?php
/**
 * 云集智能视频创意站 - 用户信息查询接口
 * 通过 social_uid 向 SSO 中心查询最新用户信息
 * 供桌面软件/内部调用使用
 */
session_start();
header('Content-Type: application/json; charset=UTF-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Headers: Content-Type, Authorization');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(204);
    exit;
}

include_once 'config.php';
include_once 'Oauth.config.php';
include_once 'Oauth.class.php';

// 获取参数
$social_uid = $_GET['social_uid'] ?? $_POST['social_uid'] ?? '';
$type = $_GET['type'] ?? $_POST['type'] ?? 'wx';

if (empty($social_uid)) {
    // 如果未传 social_uid，尝试从 session 获取
    if (isset($_SESSION['user']['social_uid'])) {
        $social_uid = $_SESSION['user']['social_uid'];
        $type = $_SESSION['user']['type'] ?? $type;
    } else {
        echo json_encode(['code' => -1, 'msg' => '缺少 social_uid 参数且未登录']);
        exit;
    }
}

// 调用 SSO query 接口查询用户信息
$Oauth = new Oauth($Oauth_config);
$arr = $Oauth->query($type, $social_uid);

if (isset($arr['code']) && $arr['code'] == 0) {
    // 查询成功，更新 session 中的用户信息
    if (isset($_SESSION['user'])) {
        $_SESSION['user']['nickname'] = $arr['nickname'] ?? $_SESSION['user']['nickname'];
        $_SESSION['user']['faceimg'] = $arr['faceimg'] ?? $_SESSION['user']['faceimg'];
        $_SESSION['user']['gender'] = $arr['gender'] ?? ($_SESSION['user']['gender'] ?? '');
        $_SESSION['user']['location'] = $arr['location'] ?? ($_SESSION['user']['location'] ?? '');
        $_SESSION['user']['access_token'] = $arr['access_token'] ?? ($_SESSION['user']['access_token'] ?? '');
    }

    echo json_encode([
        'code' => 1,
        'data' => [
            'nickname'     => $arr['nickname'] ?? '',
            'avatar'       => $arr['faceimg'] ?? '',
            'openid'       => $arr['social_uid'] ?? '',
            'gender'       => $arr['gender'] ?? '',
            'location'     => $arr['location'] ?? '',
            'access_token' => $arr['access_token'] ?? '',
            'type'         => $arr['type'] ?? $type,
        ]
    ]);
} else {
    echo json_encode([
        'code' => 0,
        'msg' => $arr['msg'] ?? '查询失败'
    ]);
}
