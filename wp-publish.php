<?php
/**
 * WordPress 내부 발행 스크립트
 * Flask에서 localhost로 직접 호출하여 Nginx 인증 헤더 문제를 우회
 */
header('Content-Type: application/json; charset=utf-8');

// 보안: localhost에서만 접근 허용
$allowed_ips = ['127.0.0.1', '::1'];
$remote_ip = $_SERVER['REMOTE_ADDR'] ?? '';
if (!in_array($remote_ip, $allowed_ips)) {
    http_response_code(403);
    echo json_encode(['error' => 'Forbidden: local access only']);
    exit;
}

// POST만 허용
if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['error' => 'Method not allowed']);
    exit;
}

// 시크릿 키 검증 (Flask .env의 WP_APP_PASSWORD 사용)
$auth_header = $_SERVER['HTTP_X_WP_AUTH'] ?? '';
$expected_secret = 'nZQ2 Qw4g iGQk Ps6T siZx so0f';
if ($auth_header !== $expected_secret) {
    http_response_code(401);
    echo json_encode(['error' => 'Unauthorized']);
    exit;
}

// WordPress 로드
define('ABSPATH', dirname(__FILE__) . '/');
require_once ABSPATH . 'wp-load.php';

// 관리자로 설정
wp_set_current_user(1); // admin user ID

$input = json_decode(file_get_contents('php://input'), true);
if (!$input) {
    http_response_code(400);
    echo json_encode(['error' => 'Invalid JSON']);
    exit;
}

$action = $input['action'] ?? 'publish';

// === 이미지 업로드 ===
if ($action === 'upload_image') {
    $image_url = $input['image_url'] ?? '';
    $filename = $input['filename'] ?? 'image.webp';

    if (!$image_url) {
        echo json_encode(['error' => 'No image URL']);
        exit;
    }

    // 이미지 다운로드
    $tmp = download_url($image_url, 30);
    if (is_wp_error($tmp)) {
        echo json_encode(['error' => 'Download failed: ' . $tmp->get_error_message()]);
        exit;
    }

    $file_array = [
        'name'     => sanitize_file_name($filename),
        'tmp_name' => $tmp,
    ];

    $attachment_id = media_handle_sideload($file_array, 0);
    if (is_wp_error($attachment_id)) {
        @unlink($tmp);
        echo json_encode(['error' => 'Upload failed: ' . $attachment_id->get_error_message()]);
        exit;
    }

    echo json_encode(['success' => true, 'media_id' => $attachment_id]);
    exit;
}

// === 카테고리 가져오기/생성 ===
if ($action === 'get_category') {
    $name = $input['name'] ?? '';
    if (!$name) {
        echo json_encode(['error' => 'No category name']);
        exit;
    }

    $term = get_term_by('name', $name, 'category');
    if ($term) {
        echo json_encode(['id' => $term->term_id]);
        exit;
    }

    $result = wp_insert_term($name, 'category');
    if (is_wp_error($result)) {
        echo json_encode(['error' => $result->get_error_message()]);
        exit;
    }
    echo json_encode(['id' => $result['term_id']]);
    exit;
}

// === 태그 가져오기/생성 ===
if ($action === 'get_tags') {
    $names = $input['names'] ?? [];
    $tag_ids = [];

    foreach (array_slice($names, 0, 15) as $name) {
        $name = trim($name);
        if (!$name) continue;

        $term = get_term_by('name', $name, 'post_tag');
        if ($term) {
            $tag_ids[] = $term->term_id;
            continue;
        }

        $result = wp_insert_term($name, 'post_tag');
        if (!is_wp_error($result)) {
            $tag_ids[] = $result['term_id'];
        }
    }

    echo json_encode(['ids' => $tag_ids]);
    exit;
}

// === 글 발행 ===
if ($action === 'publish') {
    $title = $input['title'] ?? '';
    $content = $input['content'] ?? '';
    $categories = $input['categories'] ?? [];
    $tags = $input['tags'] ?? [];
    $featured_media = $input['featured_media'] ?? 0;

    if (!$title || !$content) {
        http_response_code(400);
        echo json_encode(['error' => 'Title and content required']);
        exit;
    }

    $post_data = [
        'post_title'    => wp_strip_all_tags($title),
        'post_content'  => $content,
        'post_status'   => 'publish',
        'post_author'   => 1,
        'post_category' => $categories,
        'tags_input'    => [],
    ];

    $post_id = wp_insert_post($post_data, true);
    if (is_wp_error($post_id)) {
        http_response_code(500);
        echo json_encode(['error' => 'Publish failed: ' . $post_id->get_error_message()]);
        exit;
    }

    // 태그 설정
    if (!empty($tags)) {
        wp_set_post_tags($post_id, $tags, false);
    }

    // 대표이미지 설정
    if ($featured_media) {
        set_post_thumbnail($post_id, $featured_media);
    }

    $permalink = get_permalink($post_id);

    echo json_encode([
        'success' => true,
        'post_id' => $post_id,
        'url'     => $permalink,
    ]);
    exit;
}

http_response_code(400);
echo json_encode(['error' => 'Unknown action: ' . $action]);
