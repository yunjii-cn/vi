<?php

/**
 * 云集智能视频创意站 - 系统配置
 */

// 站点域名
define('VI_DOMAIN', 'https://vi.yunjii.cn');

// 登录回调地址
define('VI_CALLBACK', 'https://vi.yunjii.cn/sl/connect.php');

// UP 用户中心 API
define('API_URL', 'https://up.yunjii.cn');
define('API_WX_LOGIN', '/dtapi/user/wx_login');
define('API_WX_LOGIN_REGISTER', '/dtapi/user/wx_login_register');

// 跨域登录通信密钥
$GLOBALS['UP_KEY'] = 'HiWrCVKMjSpc3QRFPGtoZ8T940gvxzbh';

// 数据库配置
$GLOBALS['SQL_IP']   = '127.0.0.1';
$GLOBALS['SQL_USER'] = 'yunji_dl_production';
$GLOBALS['SQL_PASS'] = 'TdNjhHGjGZdPJmC3';
$GLOBALS['SQL_NAME'] = 'yunji_dl_production';
$GLOBALS['SQL_PORT'] = 33206;
