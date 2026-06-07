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

// 仅在需要时加载额外功能
$use_additional_features = defined('API_URL') && defined('API_WX_LOGIN_REGISTER');
if ($use_additional_features) {
    include_once 'RsaSign.php';
}

$type = isset($_GET['type']) ? $_GET['type'] : 'wx';
if ($_GET['code']) {
    $redirect_url = isset($_SERVER['HTTP_REFERER']) ? $_SERVER['HTTP_REFERER'] : './index.php';
    if (!isset($_SESSION['Oauth_state']) || $_GET['state'] != $_SESSION['Oauth_state']) {
        header('Location: ' . $redirect_url);
        exit();
    }
    $Oauth = new Oauth($Oauth_config);
    $arr = $Oauth->callback();
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
        $redirect_target = $from_client ? './client.php' : VI_DOMAIN . '/';
        exit("<script language='javascript'>
            if (window.parent && window.parent !== window) {
                window.parent.postMessage({type: 'loginSuccess', user: " . json_encode($arr, JSON_UNESCAPED_UNICODE) . "}, '*');
                // 如果来自客户端，刷新父页面到 client.php 以触发 token 回传
                if ({$from_client}) {
                    window.parent.location.href = './client.php';
                }
            } else {
                window.location.href='" . $redirect_target . "';
            }
        </script>
        ");
    } elseif (isset($arr['code'])) {
        exit('登录失败，返回错误原因：' . $arr['msg']);
    } else {
        exit('获取登录数据失败');
    }
} else {
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
