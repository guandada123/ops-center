#!/usr/bin/env bash
# Ops Center Smoke Test — 验证所有 API 端点正常
set -uo pipefail

BASE="${1:-http://localhost:9998}"
PASS=0
FAIL=0

ok()   { echo "  ✅ $1"; ((PASS++)); }
fail() { echo "  ❌ $1"; ((FAIL++)); }

echo "🔍 Ops Center Smoke Test — BASE=$BASE"
echo ""

# 1. health
s1=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/health" 2>/dev/null || echo "000")
body=$(curl -s "$BASE/api/health" 2>/dev/null || echo "{}")
if [ "$s1" = "200" ] && echo "$body" | grep -q '"status".*"healthy"'; then ok "/api/health → $s1"; else fail "/api/health → $s1"; fi

# 2. index.html
s1=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/" 2>/dev/null || echo "000")
ct=$(curl -s -o /dev/null -w "%{content_type}" "$BASE/" 2>/dev/null || echo "")
if [ "$s1" = "200" ] && echo "$ct" | grep -qi "text/html"; then ok "/ → $s1 ($ct)"; else fail "/ → $s1"; fi

# 3. overview
s1=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/overview" 2>/dev/null || echo "000")
body=$(curl -s "$BASE/api/overview" 2>/dev/null || echo "{}")
if [ "$s1" = "200" ] && echo "$body" | grep -q '"projects"'; then ok "/api/overview → $s1"; else fail "/api/overview → $s1"; fi

# 4. cost
s1=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/cost" 2>/dev/null || echo "000")
body=$(curl -s "$BASE/api/cost" 2>/dev/null || echo "{}")
if [ "$s1" = "200" ] && echo "$body" | grep -q '"monthly_estimate"'; then ok "/api/cost → $s1"; else fail "/api/cost → $s1"; fi

# 5. automations
s1=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/automations" 2>/dev/null || echo "000")
body=$(curl -s "$BASE/api/automations" 2>/dev/null || echo "{}")
if [ "$s1" = "200" ] && echo "$body" | grep -q '"count"'; then ok "/api/automations → $s1"; else fail "/api/automations → $s1"; fi

# 6. roadmap
s1=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/roadmap" 2>/dev/null || echo "000")
body=$(curl -s "$BASE/api/roadmap" 2>/dev/null || echo "{}")
if [ "$s1" = "200" ] && echo "$body" | grep -q '"goals"'; then ok "/api/roadmap → $s1"; else fail "/api/roadmap → $s1"; fi

# 7. Security headers
csp=$(curl -s -I "$BASE/" 2>/dev/null | grep -i "content-security-policy" | head -1 || echo "missing")
xfo=$(curl -s -I "$BASE/" 2>/dev/null | grep -i "x-frame-options" | head -1 || echo "missing")
if echo "$csp" | grep -iq "default-src"; then ok "CSP header present"; else fail "CSP header missing"; fi
if echo "$xfo" | grep -iq "DENY"; then ok "X-Frame-Options: DENY"; else fail "X-Frame-Options missing"; fi

echo ""
echo "========================"
echo "✅ PASS: $PASS   ❌ FAIL: $FAIL"
echo "========================"

[ "$FAIL" -eq 0 ]
