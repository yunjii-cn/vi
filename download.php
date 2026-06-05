<?php
/**
 * 云集智能视频创意站 - 自动下载最新版
 * 通过 Gitee API 获取最新 Release 的 exe 附件，直接重定向下载
 */

header('Content-Type: text/html; charset=utf-8');

$owner      = 'yunjii';
$repo       = 'vi';
$cache_file = __DIR__ . '/.download_cache.json';
$cache_ttl  = 300; // 缓存5分钟

$fallback_url = "https://gitee.com/{$owner}/{$repo}/releases";

// 读取缓存
if (file_exists($cache_file) && (time() - filemtime($cache_file)) < $cache_ttl) {
    $cache = @json_decode(file_get_contents($cache_file), true);
    if (!empty($cache['url'])) {
        header("Location: {$cache['url']}");
        exit;
    }
}

// 调用 Gitee API
$api = "https://gitee.com/api/v5/repos/{$owner}/{$repo}/releases/latest";
$ctx = stream_context_create([
    'http' => [
        'timeout' => 15,
        'user_agent' => 'YunJii-Download/1.0',
        'ignore_errors' => true
    ]
]);

$json = @file_get_contents($api, false, $ctx);

if ($json === false) {
    header("Location: {$fallback_url}");
    exit;
}

$release = json_decode($json, true);

if (!$release || isset($release['message']) || empty($release['assets'])) {
    header("Location: {$fallback_url}");
    exit;
}

// 查找最新的 .exe 附件（取最后一个，通常是最新构建）
$exeAssets = [];
foreach ($release['assets'] as $asset) {
    $name = $asset['name'] ?? '';
    if (preg_match('/\.exe$/i', $name)) {
        $exeAssets[] = $asset;
    }
}

if (empty($exeAssets)) {
    header("Location: {$fallback_url}");
    exit;
}

// 取最后一个 exe（同版本多次构建时，最后上传的是最新的）
$downloadUrl = end($exeAssets)['browser_download_url'] ?? null;

if ($downloadUrl) {
    @file_put_contents($cache_file, json_encode([
        'url'     => $downloadUrl,
        'version' => $release['tag_name'] ?? '',
        'time'    => time()
    ]));
    header("Location: {$downloadUrl}");
} else {
    header("Location: {$fallback_url}");
}
exit;
