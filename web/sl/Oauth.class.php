<?php

/**
 * 云集聚合登录SDK
 * 聚合登录请求类
 */

class Oauth
{
	private $apiurl;
	private $appid;
	private $appkey;
	private $callback;

	function __construct($config)
	{
		$this->apiurl = $config['apiurl'] . 'connect.php';
		$this->appid = $config['appid'];
		$this->appkey = $config['appkey'];
		$this->callback = $config['callback'];
	}

	// 获取登录跳转url
	public function login($type)
	{
		$state = md5(uniqid(rand(), TRUE));
		$_SESSION['Oauth_state'] = $state;

		$keysArr = array(
			"id" => "web_qrcode_app_wrp",
			"act" => "login",
			"appid" => $this->appid,
			"appkey" => $this->appkey,
			"type" => $type,
			"redirect_uri" => $this->callback,
			"state" => $state
		);
		$login_url = $this->apiurl . '?' . http_build_query($keysArr);
		$response = $this->get_curl($login_url);
		$arr = json_decode($response, true);
		return $arr;
	}

	// 登录成功回调
	public function callback()
	{
		$keysArr = array(
			"act" => "callback",
			"appid" => $this->appid,
			"appkey" => $this->appkey,
			"code" => $_GET['code']
		);

		$token_url = $this->apiurl . '?' . http_build_query($keysArr);
		$response = $this->get_curl($token_url);
		$arr = json_decode($response, true);
		return $arr;
	}

	// 查询用户信息
	public function query($type, $social_uid)
	{
		$keysArr = array(
			"act" => "query",
			"appid" => $this->appid,
			"appkey" => $this->appkey,
			"type" => $type,
			"social_uid" => $social_uid
		);

		$token_url = $this->apiurl . '?' . http_build_query($keysArr);
		$response = $this->get_curl($token_url);
		$arr = json_decode($response, true);
		return $arr;
	}

	public function get_curl($url)
	{
		$ch = curl_init();
		curl_setopt($ch, CURLOPT_URL, $url);
		curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
		curl_setopt($ch, CURLOPT_SSL_VERIFYHOST, false);
		curl_setopt($ch, CURLOPT_USERAGENT, "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/63.0.3239.132 Safari/537.36");
		curl_setopt($ch, CURLOPT_TIMEOUT, 10);
		curl_setopt($ch, CURLOPT_RETURNTRANSFER, 1);
		$ret = curl_exec($ch);
		curl_close($ch);
		return $ret;
	}

	function post_curl($url, $data)
	{
		$ch = curl_init();
		$json_data = json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
		curl_setopt($ch, CURLOPT_URL, $url);
		curl_setopt($ch, CURLOPT_POST, 1);
		curl_setopt($ch, CURLOPT_POSTFIELDS, $json_data);
		curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
		curl_setopt($ch, CURLOPT_HTTPHEADER, array(
			'Content-Type: application/json',
			'Content-Length: ' . strlen($json_data)
		));
		curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
		curl_setopt($ch, CURLOPT_SSL_VERIFYHOST, false);
		$response = curl_exec($ch);
		if (curl_errno($ch)) {
			error_log('Curl error: ' . curl_error($ch));
		}
		curl_close($ch);
		return $response;
	}
}
