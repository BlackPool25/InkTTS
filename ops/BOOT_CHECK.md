# BOOT_CHECK — Task 4 auth wall + ops boot proof

Run from `/home/shreyas/projects/InkCast`. Needs `.env` filled (see
`docs/ops/auth-wall.md` bootstrap).

```bash
# 0. Config valid (expect exit 0)
docker compose config > /tmp/inkcast-config.yml && echo CONFIG_OK

# 1. Boot
docker compose up -d --build
docker compose ps

# 2. Wall: unauthenticated API -> 401 via Caddy forward-auth
#    (expect 401, no backend JSON leak)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/api/v1/books

# 3. Wall: login via Authelia, then same URL -> 200 with cookie jar
#    (replace USER/PASS; session lasts 30 days)
curl -s -c /tmp/jar -o /dev/null -w '%{http_code}\n' http://127.0.0.1/authelia/
# complete login in browser at http://<host-lan-ip>/authelia, then:
curl -s -b /tmp/jar -o /dev/null -w '%{http_code}\n' http://127.0.0.1/api/v1/books

# 4. OPDS token URL works without login (Wave 2 wired; expect 200 + XML)
TOKEN=$(docker compose exec -T backend python -c \
  "from app.db import create_all; create_all(); from sqlmodel import Session; \
from app.db import engine; from app.tokens import create_opds_token; \
print(create_opds_token(Session(engine), 'boot-check'))" | tail -1)
curl -s -o /dev/null -w '%{http_code}\n' \
  "http://127.0.0.1/api/v1/opds/catalog.xml?token=$TOKEN"
# bad token -> 401
curl -s -o /dev/null -w '%{http_code}\n' \
  'http://127.0.0.1/api/v1/opds/catalog.xml?token=wrong'

# 5. Direct :8000 without token -> 401 (never trusted, even on LAN)
docker compose exec -T backend python -c \
  "import urllib.request, urllib.error; \
urllib.request.urlopen('http://127.0.0.1:8000/api/v1/jobs')" 2>&1 | tail -1
# expect: HTTP Error 401

# 6. WS without token rejected (expect close 4401)
docker compose exec -T backend python -c \
  "import asyncio, websockets; \
print(asyncio.run(websockets.connect('ws://127.0.0.1:8000/api/v1/ws')))" 2>&1 | tail -1
```
