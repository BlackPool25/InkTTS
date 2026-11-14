# BOOT_CHECK — live-verified 2026-11-14 (host-gated bringup)

Run from `/home/shreyas/projects/InkCast`. Needs `.env` filled.

```bash
# 0. Config valid (expect exit 0)
docker compose config > /tmp/inkcast-config.yml && echo CONFIG_OK

# 1. Boot (backend rebuilds on code change; code is COPY'd, not bind-mounted)
docker compose up -d --build
docker compose ps   # all Up; backend (healthy), authelia (healthy)

# 2. First run only: extract the LAN CA (Caddy tls internal, one line per device)
docker compose exec -T caddy cat /data/caddy/pki/authorities/local/root.crt > ops/lan-ca.crt
# Install lan-ca.crt on the Android + PC browsers once (OS trust store).
# Browse via https://inkcast.<dash-ip>.nip.io:8085 (resolves to the LAN IP,
# zero DNS). Plain http is REJECTED by the Authelia 4.39 validator.

# 3. Wall: anonymous API -> 403 (Authelia default_policy deny)
curl -s --cacert ops/lan-ca.crt -o /dev/null -w '%{http_code}\n' \
  https://inkcast.192-168-29-101.nip.io:8085/api/v1/books \
  --resolve inkcast.192-168-29-101.nip.io:8085:192.168.29.101
# expect: 403

# 4. Login (portal user `reader`; password minted at first boot, see
#    "first-run password" below), then same URL -> 200 with session cookie
# 5. OPDS token URL works without login (expect 200 + XML); bad token -> 401
TOKEN=$(docker compose exec -T backend python -c \
  "from app.db import create_all; create_all(); from sqlmodel import Session; \
from app.db import engine; from app.tokens import create_opds_token; \
print(create_opds_token(Session(engine),'boot-check'))" | tail -1)
curl -s --cacert ops/lan-ca.crt -o /dev/null -w '%{http_code}\n' \
  "https://inkcast.192-168-29-101.nip.io:8085/api/v1/opds/catalog.xml?token=$TOKEN" \
  --resolve inkcast.192-168-29-101.nip.io:8085:192.168.29.101
# expect: 200

# 6. Direct :8000 without token -> 401 (never trusted, even on LAN)
# 7. WS without token -> close 4401
```

## First-run password

```bash
BOOTPASS="inkcast-$(python3 -c 'import secrets;print(secrets.token_hex(3))')"
echo "$BOOTPASS" > /tmp/inkcast-bootpass.txt   # hand to the user once
docker run --rm authelia/authelia:4.39 authelia crypto hash generate argon2 \
  --password "$BOOTPASS"   # take the $argon2... string
# put it as `password:` for user `reader` in ops/users_database.yml
# (tracked file is the `.example` template; the real one is git-ignored)
docker compose restart authelia
```

## Validator findings (Authelia 4.39, do not regress)

- `session.cookies[].secure` does not exist (scheme-derived); a `secure:`
  line is a fatal config error, not a warning.
- `domain` must be non-empty; `""` is fatal. IP literal passes the
  validator but browsers + access-control want the dotted nip.io name.
- `authelia_url` required once domain is valid; `default_redirection_url`
  must be absolute https and differ from `authelia_url`.
- `domain: "*"` matches NOTHING in 4.39; use the documented `*.` wildcard
  (`*.192-168-29-101.nip.io`). Verify offline with
  `docker compose exec authelia authelia access-control check-policy --url …`.
- Users file key is `password:`, not `hash:` (else
  `Users.reader.users: non zero value required` at startup).
