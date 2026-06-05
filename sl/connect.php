<?php
/**
 * 云集智能视频创意站 - 聚合登录回调处理
 */

error_reporting(E_ALL);
ini_set('display_errors', 0);
ini_set('log_errors', 1);
ini_set('error_log', '/www/wwwroot/vi.yunjii.cn/sl/error.log');

function logMessage($message, $level = 'info') {
    $timestamp = date('Y-m-d H:i:s');
    $log_message = "[$timestamp] [$level] $message\n";
    error_log($log_message, 3, '/www/wwwroot/vi.yunjii.cn/sl/debug.log');
}

session_start();
@header('Content-Type: text/html; charset=UTF-8');

include_once 'config.php';
include_once 'Oauth.config.php';
include_once 'Oauth.class.php';

$use_api = defined('API_URL') && defined('API_WX_LOGIN_REGISTER');
if ($use_api) {
    include_once 'RsaSign.php';
}

$type = isset($_GET['type']) ? $_GET['type'] : 'wx';

if (isset($_GET['code'])) {
    // 回调处理
    $redirect_url = isset($_SERVER['HTTP_REFERER']) ? $_SERVER['HTTP_REFERER'] : './index.php';
    if (!isset($_SESSION['Oauth_state']) || $_GET['state'] != $_SESSION['Oauth_state']) {
        header('Location: ' . $redirect_url);
        exit();
    }

    $Oauth = new Oauth($Oauth_config);
    $arr = $Oauth->callback();

    if (isset($arr['code']) && $arr['code'] == 0) {
        $_SESSION['user'] = $arr;

        // 调用用户中心API注册/登录
        if ($use_api) {
            try {
                logMessage("开始调用用户中心API");
                $RsaSign = new RsaSign();

                $data = [
                    'openid'   => $arr['social_uid'],
                    'nickname' => $arr['nickname'],
                    'avatar'   => $arr['faceimg'],
                    'ip'       => getRealClientIp()
                ];

                $signature_data = $RsaSign->zdySign($data);
                $request_data = [
                    'data' => $data,
                    'signData' => [
                        'secret_id' => $signature_data['secret_id'],
                        'signtime'  => $signature_data['signtime'],
                        'sign'      => $signature_data['sign']
                    ]
                ];

                $token_url = API_URL . API_WX_LOGIN_REGISTER;
                $response = $Oauth->post_curl($token_url, $request_data);
                logMessage("API响应: $response");

                $return_data = json_decode($response, true);
                if (json_last_error() === JSON_ERROR_NONE) {
                    // 兼容两种响应格式
                    $userinfo = null;
                    if (isset($return_data['result']) && $return_data['result']['code'] == 1) {
                        $userinfo = $return_data['data']['userinfo'] ?? null;
                    } elseif (isset($return_data['code']) && $return_data['code'] == 1) {
                        $userinfo = $return_data['data']['userinfo'] ?? null;
                    }
                    if ($userinfo) {
                        $arr['username'] = $userinfo['username'] ?? '';
                        $arr['token']    = $userinfo['token'] ?? '';
                        $arr['site']     = $userinfo['site'] ?? '';
                        $_SESSION['user'] = $arr;
                        $_SESSION['userinfo'] = $userinfo;
                        logMessage("登录成功，用户: " . ($arr['username'] ?? 'unknown'));
                    }
                }
            } catch (Exception $e) {
                logMessage("API调用异常: " . $e->getMessage(), 'error');
            }
        }

        exit("<script language='javascript'>window.location.href='./index.php';</script>");
    } elseif (isset($arr['code'])) {
        exit('登录失败：' . $arr['msg']);
    } else {
        exit('获取登录数据失败');
    }
} else {
    // 发起登录请求
    $Oauth = new Oauth($Oauth_config);
    $arr = $Oauth->login($type);
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
    $headers = [
        'HTTP_CF_CONNECTING_IP',
        'HTTP_X_FORWARDED_FOR',
        'HTTP_X_FORWARDED',
        'HTTP_CLIENT_IP',
        'REMOTE_ADDR'
    ];
    foreach ($headers as $header) {
        if (!empty($_SERVER[$header])) {
            $ipList = explode(',', $_SERVER[$header]);
            foreach ($ipList as $tmpIp) {
                $tmpIp = trim($tmpIp);
                if (filter_var($tmpIp, FILTER_VALIDATE_IP)) {
                    return $tmpIp;
                }
            }
        }
    }
    return '0.0.0.0';
}
