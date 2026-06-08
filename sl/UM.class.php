<?php

/**
 * 云集用户系统 (UM) - PHP SDK（新接口版本）
 *
 * 一行代码接入扫码登录，调用 UM 最新接口
 *
 * 协议版本：UM v1.0
 * 更新日期：2026-06-08
 *
 * 重要：本 SDK 调用的是 UM 最新接口 /um/connect.php
 *       相比 /connect.php（老彩虹协议入口），/um/connect.php 提供：
 *         1. 防 40163 缓存（5 分钟内重复 code 走缓存）
 *         2. 状态机优化（status=2 已完成直接返回数据库）
 *         3. UM 生态接口预留（积分、钱包、用户中心等）
 *         4. 更详细的错误码和调试信息
 *         5. 未来 UM 专属能力（私域、跨应用数据等）
 *
 * 如果您是老彩虹用户（不想改 API 入口），请使用 Oauth.class.php
 *
 * 使用方法：
 *   1. 在 https://sl.yunjii.cn/um/admin/apps.php 创建应用获取 appid/appkey
 *   2. 在您的网站引入本文件
 *   3. 调用 UM::login($type) 生成登录链接
 *   4. 在您的回调页面调用 UM::callback() 完成登录
 *
 * 完整示例见 example.php
 */

class UM
{
    private $apiurl;
    private $appid;
    private $appkey;
    private $callback;

    /**
     * @param string $appid  应用 ID（在 UM 后台获取）
     * @param string $appkey 应用密钥（在 UM 后台获取）
     * @param string $callback 登录成功回调地址（必须是 https）
     * @param string $apiurl  UM API 根地址，默认 https://sl.yunjii.cn/
     *                       SDK 会自动拼上 /um/connect.php
     */
    public function __construct($appid, $appkey, $callback, $apiurl = 'https://sl.yunjii.cn/')
    {
        // UM 最新接口路径（带全部新能力）
        $this->apiurl   = rtrim($apiurl, '/') . '/um/connect.php';
        $this->appid    = $appid;
        $this->appkey   = $appkey;
        $this->callback = $callback;
    }

    /**
     * 获取登录跳转 URL
     * @param string $type  登录方式：wx/qq/alipay 等
     * @param string $state 业务方自定义 state（可选）
     * @return array  {code:0, url:'https://...', type:'wx'}
     */
    public function login($type = 'wx', $state = '')
    {
        $keysArr = [
            'id'           => 'web_qrcode_app_wrp',
            'act'          => 'login',
            'appid'        => $this->appid,
            'appkey'       => $this->appkey,
            'type'         => $type,
            'redirect_uri' => $this->callback,
            'state'        => $state,
        ];
        $url      = $this->apiurl . '?' . http_build_query($keysArr);
        $response = $this->get_curl($url);
        return json_decode($response, true);
    }

    /**
     * 处理 OAuth 回调
     *
     * 在回调页面（$this->callback 指向的页面）调用此方法，
     * 通过 $_GET['code'] 换取用户信息。
     *
     * @return array  成功返回 {code:0, social_uid:'...', nickname:'...', faceimg:'...', ...}
     *                失败返回 {code:-1, msg:'...'}
     */
    public function callback()
    {
        $code = isset($_GET['code']) ? $_GET['code'] : '';
        if (!$code) {
            return ['code' => -1, 'msg' => 'no code'];
        }
        $keysArr = [
            'act'    => 'callback',
            'appid'  => $this->appid,
            'appkey' => $this->appkey,
            'code'   => $code,
        ];
        $url      = $this->apiurl . '?' . http_build_query($keysArr);
        $response = $this->get_curl($url);
        return json_decode($response, true);
    }

    /**
     * 查询第三方用户信息
     * @param string $type       登录方式
     * @param string $social_uid 用户 openid
     * @return array
     */
    public function query($type, $social_uid)
    {
        $keysArr = [
            'act'        => 'query',
            'appid'      => $this->appid,
            'appkey'     => $this->appkey,
            'type'       => $type,
            'social_uid' => $social_uid,
        ];
        $url      = $this->apiurl . '?' . http_build_query($keysArr);
        $response = $this->get_curl($url);
        return json_decode($response, true);
    }

    private function get_curl($url)
    {
        $ch = curl_init();
        curl_setopt($ch, CURLOPT_URL, $url);
        curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
        curl_setopt($ch, CURLOPT_SSL_VERIFYHOST, false);
        curl_setopt($ch, CURLOPT_USERAGENT, 'Mozilla/5.0 UM-SDK/1.0');
        curl_setopt($ch, CURLOPT_TIMEOUT, 10);
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, 1);
        $ret = curl_exec($ch);
        curl_close($ch);
        return $ret;
    }
}
