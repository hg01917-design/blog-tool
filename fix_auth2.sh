#!/bin/bash
HTACCESS=~/applications/qkdpuhswfk/public_html/.htaccess

# Remove the previous broken attempt (if any duplicate rules)
# Add CGIPassAuth and SetEnvIf right after RewriteEngine On
cat > /tmp/auth_fix.txt << 'PATCH'
# Pass Authorization header to PHP (fix for Cloudways)
<IfModule mod_setenvif.c>
SetEnvIf Authorization "(.*)" HTTP_AUTHORIZATION=$1
</IfModule>
<IfModule mod_rewrite.c>
RewriteCond %{HTTP:Authorization} .
RewriteRule .* - [E=HTTP_AUTHORIZATION:%{HTTP:Authorization}]
</IfModule>
PATCH

# Check if already patched
if grep -q "SetEnvIf Authorization" "$HTACCESS"; then
    echo "Already patched"
else
    # Insert after the first RewriteEngine On line
    sed -i '/^RewriteEngine On/r /tmp/auth_fix.txt' "$HTACCESS"
    echo "Patched .htaccess"
fi

echo "--- .htaccess top 25 lines ---"
head -25 "$HTACCESS"

echo ""
echo "--- Testing WP REST API auth ---"
curl -s -w "\nHTTP_%{http_code}" https://baremi542.com/wp-json/wp/v2/users/me \
  -H "Authorization: Basic YWRtaW46blpRMiBRdzRnIGlHUWsgUHM2VCBzaVp4IHNvMGY=" 2>&1 | tail -3

echo ""
echo "DONE"
