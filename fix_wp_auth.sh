#!/bin/bash
# Fix WordPress REST API Authorization header being stripped by Apache
HTACCESS=~/applications/qkdpuhswfk/public_html/.htaccess

# Add Authorization header passthrough before WordPress rules
# Check if already added
if grep -q "HTTP_AUTHORIZATION" "$HTACCESS"; then
    echo "Already has HTTP_AUTHORIZATION rule"
else
    # Add right after RewriteEngine On (but after the Flask proxy rules)
    sed -i '/^RewriteRule.*flask-proxy\.php/a\\n# Pass Authorization header to PHP\nRewriteCond %{HTTP:Authorization} .\nRewriteRule .* - [E=HTTP_AUTHORIZATION:%{HTTP:Authorization}]' "$HTACCESS"
    echo "Added HTTP_AUTHORIZATION passthrough rule"
fi

# Also add to wp-config.php as fallback
WPCONFIG=~/applications/qkdpuhswfk/public_html/wp-config.php
if grep -q "HTTP_AUTHORIZATION" "$WPCONFIG"; then
    echo "wp-config.php already has auth fix"
else
    # Add before "That's all, stop editing!" or at top after <?php
    sed -i '/^<\?php/a\\n// Fix Application Passwords - pass Authorization header\nif (isset($_SERVER["REDIRECT_HTTP_AUTHORIZATION"])) {\n    $_SERVER["HTTP_AUTHORIZATION"] = $_SERVER["REDIRECT_HTTP_AUTHORIZATION"];\n}' "$WPCONFIG"
    echo "Added auth fix to wp-config.php"
fi

echo "--- Testing auth ---"
curl -s -w "\nHTTP_%{http_code}" https://baremi542.com/wp-json/wp/v2/users/me \
  -H "Authorization: Basic YWRtaW46blpRMiBRdzRnIGlHUWsgUHM2VCBzaVp4IHNvMGY=" 2>&1 | tail -3

echo ""
echo "FIX_DONE"
