<?php
/**
 * 云集智能视频创意站 - 登录状态检查（API接口）
 * 供客户端软件调用，返回JSON格式登录状态
 */
session_start();
header('Content-Type: application/json; charset=UTF-8');
header('Access-Control-Allow-Origin: *');

if (isset($_SESSION['user'])) {
    echo json_encode([
        'code' => 1,
        'data' => [
            'nickname'   => $_SESSION['user']['nickname'] ?? '',
            'avatar'     => $_SESSION['user']['faceimg'] ?? '',
            'openid'     => $_SESSION['user']['social_uid'] ?? '',
            'username'   => $_SESSION['user']['username'] ?? '',
            'token'      => $_SESSION['user']['token'] ?? '',
            'site'       => $_SESSION['user']['site'] ?? '',
        ]
    ]);
} else {
    echo json_encode(['code' => 0, 'msg' => '未登录']);
}
