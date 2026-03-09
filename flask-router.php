<?php
// Flask App Router - intercepts requests for app.baremi542.com
if (isset($_SERVER['HTTP_HOST']) && $_SERVER['HTTP_HOST'] === 'app.baremi542.com') {
    $method = $_SERVER['REQUEST_METHOD'];
    $path = $_SERVER['REQUEST_URI'];
    $url = 'http://127.0.0.1:5000' . $path;

    $headers = [];
    foreach (getallheaders() as $name => $value) {
        if (strtolower($name) !== 'host') {
            $headers[] = "$name: $value";
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
    header("Cache-Control: no-cache, no-store, must-revalidate");
    header("Pragma: no-cache");
    header("Expires: 0");
    foreach (explode("\r\n", $resp_headers) as $h) {
        if (stripos($h, 'content-type:') === 0 || stripos($h, 'content-disposition:') === 0) {
            header($h);
        }
    }
    echo $body;
    exit;
}
// If not app.baremi542.com, continue to WordPress normally
