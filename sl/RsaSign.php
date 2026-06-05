<?php

/**
 * RSA签名类
 * 云集智能视频创意站
 */
class RsaSign
{
    public $publicKey = '/www/wwwroot/vi.yunjii.cn/certs/public_key.pem';
    public $privateKey = '/www/wwwroot/vi.yunjii.cn/certs/private_key.pem';
    public $secret_id;
    public $secret_key;
    public static $callback_sign;

    public function __construct($publicKey = null, $privateKey = null) {
        global $Oauth_config;
        $this->secret_id = isset($Oauth_config['appid']) ? $Oauth_config['appid'] : '';
        $this->secret_key = isset($Oauth_config['appkey']) ? $Oauth_config['appkey'] : '';
        $this->setKey($publicKey, $privateKey);
    }

    private $_privKey;
    private $_pubKey;

    public function setKey($publicKey = null, $privateKey = null)
    {
        if (!is_null($publicKey)) {
            $this->publicKey = $publicKey;
        }
        if (!is_null($privateKey)) {
            $this->privateKey = $privateKey;
        }
    }

    private function setupPrivKey()
    {
        if (is_resource($this->_privKey) || is_object($this->_privKey)) {
            return true;
        }
        $this->_privKey = openssl_pkey_get_private(file_get_contents($this->privateKey));
        return true;
    }

    public function setupPubKey()
    {
        if (is_resource($this->_pubKey) || is_object($this->_pubKey)) {
            return true;
        }
        if (!file_exists($this->publicKey)) {
            error_log('RsaSign: 公钥文件不存在: ' . $this->publicKey);
            return false;
        }
        $public_key_content = file_get_contents($this->publicKey);
        if (!$public_key_content) {
            error_log('RsaSign: 无法读取公钥文件内容');
            return false;
        }
        $this->_pubKey = openssl_pkey_get_public($public_key_content);
        if ($this->_pubKey === false) {
            error_log('RsaSign: 公钥加载失败: ' . openssl_error_string());
            return false;
        }
        return true;
    }

    public function privEncrypt($data)
    {
        if (!is_string($data)) return null;
        $this->setupPrivKey();
        $r = openssl_private_encrypt($data, $encrypted, $this->_privKey);
        return $r ? base64_encode($encrypted) : null;
    }

    public function privDecrypt($encrypted)
    {
        if (!is_string($encrypted)) return null;
        $this->setupPrivKey();
        $encrypted = base64_decode($encrypted);
        $r = openssl_private_decrypt($encrypted, $decrypted, $this->_privKey);
        return $r ? json_decode($decrypted, true) : null;
    }

    public function pubEncrypt($data)
    {
        if (!$data) return null;
        if (is_array($data)) $data = json_encode($data, JSON_UNESCAPED_UNICODE);
        if (!$this->setupPubKey()) return null;
        $r = openssl_public_encrypt($data, $encrypted, $this->_pubKey);
        return $r ? base64_encode($encrypted) : null;
    }

    public function pubDecrypt($crypted)
    {
        if (!$crypted) return null;
        if (is_array($crypted)) $crypted = json_encode($crypted);
        $this->setupPubKey();
        $crypted = base64_decode($crypted);
        $r = openssl_public_decrypt($crypted, $decrypted, $this->_pubKey);
        return $r ? $decrypted : null;
    }

    public function sign($data)
    {
        if (!$data) return null;
        if (is_array($data)) $data = json_encode($data);
        $this->setupPrivKey();
        $signature = false;
        openssl_sign($data, $signature, $this->_privKey, OPENSSL_ALGO_SHA256);
        return base64_encode($signature);
    }

    public function verify($data, $signString)
    {
        if (!$data) return null;
        if (is_array($data)) $data = json_encode($data);
        $this->setupPubKey();
        $signature = base64_decode($signString);
        return openssl_verify($data, $signature, $this->_pubKey, OPENSSL_ALGO_SHA256);
    }

    /**
     * 自定义构造签名
     */
    public function zdySign($data)
    {
        if (empty($data)) return null;
        if (!is_array($data)) {
            if (is_string($data)) {
                $decoded = json_decode($data, true);
                if (json_last_error() === JSON_ERROR_NONE && is_array($decoded)) {
                    $data = $decoded;
                } else {
                    return null;
                }
            } else {
                return null;
            }
        }

        $time = time();
        $sign_data_str = '';
        if (isset($data['avatar'])) $sign_data_str .= $data['avatar'];
        if (isset($data['ip'])) $sign_data_str .= $data['ip'];
        if (isset($data['nickname'])) $sign_data_str .= $data['nickname'];
        if (isset($data['openid'])) $sign_data_str .= $data['openid'];

        $sign_raw = $sign_data_str . $this->secret_id . $time;
        $md5_hash = md5($sign_raw);
        $hmac_key = md5($this->secret_key . $time);
        $sign = hash_hmac('sha256', $md5_hash, $hmac_key);

        return [
            'secret_id' => $this->secret_id,
            'signtime'  => $time,
            'sign'      => $sign
        ];
    }

    /**
     * 自定义验证签名
     */
    public function zdyVerify($data)
    {
        if (!$data) return false;
        if (is_string($data)) $data = json_decode($data, true);
        if (isset($data['data']) && isset($data['signData']['signtime'])) {
            $sign = hash_hmac('sha256', md5(json_encode($data['data'], JSON_UNESCAPED_UNICODE) . $this->secret_id . $data['signData']['signtime']), md5($this->secret_key . $data['signData']['signtime']));
            if ($sign == $data['signData']['sign']) {
                self::$callback_sign = $sign;
                return $data['data'];
            }
        }
        return false;
    }

    public function __destruct()
    {
        (is_resource($this->_privKey) || is_object($this->_privKey)) && @openssl_free_key($this->_privKey);
        (is_resource($this->_pubKey) || is_object($this->_pubKey)) && @openssl_free_key($this->_pubKey);
    }
}
