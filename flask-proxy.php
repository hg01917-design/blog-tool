<?php
// Varnish bypass: 쿠키/세션이 있으면 캐시 안 함
header('X-Varnish-Bypass: 1');
header('Cache-Control: no-cache, no-store, must-revalidate, private');
header('Pragma: no-cache');
header('Expires: 0');

$method = $_SERVER['REQUEST_METHOD'];
$path = $_SERVER['REQUEST_URI'];
$url = 'http://127.0.0.1:5000' . $path;

$headers = [];
$has_cookie = false;
foreach (getallheaders() as $name => $value) {
    $lower = strtolower($name);
    if ($lower === 'host') continue;
    if ($lower === 'cookie') $has_cookie = true;
    $headers[] = "$name: $value";
}
// Cookie 헤더가 누락된 경우 $_SERVER 또는 $_COOKIE에서 재구성
if (!$has_cookie) {
    $raw = $_SERVER['HTTP_COOKIE'] ?? '';
    if ($raw) {
        $headers[] = 'Cookie: ' . $raw;
    } elseif (!empty($_COOKIE)) {
        $pairs = [];
        foreach ($_COOKIE as $k => $v) {
            $pairs[] = $k . '=' . $v;
        }
        $headers[] = 'Cookie: ' . implode('; ', $pairs);
    }
}

$ch = curl_init($url);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_HEADER, true);
curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
curl_setopt($ch, CURLOPT_TIMEOUT, 120);

if (in_array($method, ['POST', 'PUT', 'PATCH'])) {
    $input = file_get_contents('php://input');
    curl_setopt($ch, CURLOPT_POSTFIELDS, $input);
}

$response = curl_exec($ch);
if ($response === false) {
    http_response_code(502);
    echo 'Flask app not responding';
    exit;
}
$header_size = curl_getinfo($ch, CURLINFO_HEADER_SIZE);
$status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
$resp_headers = substr($response, 0, $header_size);
$body = substr($response, $header_size);
curl_close($ch);

http_response_code($status);
foreach (explode("\r\n", $resp_headers) as $h) {
    $h = trim($h);
    if ($h === '' || stripos($h, 'HTTP/') === 0 || stripos($h, 'transfer-encoding:') === 0) {
        continue;
    }
    header($h, stripos($h, 'set-cookie:') !== 0);
}
echo $body;
