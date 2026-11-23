# Example deployment

A complete, generic setup: the sidegig server in one container, Caddy in front for HTTPS and an IP
allowlist. Nothing here names a real host. Your values go in `.env`, which git ignores.

```
compose.yaml    the two services; the server publishes no port, only Caddy does
Caddyfile       HTTPS for SIDEGIG_HOST, 403 for everyone outside SIDEGIG_ALLOW, proxy to sidegig
.env.example    the variable names; copy it to .env and fill it in
```

## Keep real values out of git

Pick one:

- **An ignored folder in this checkout.** Copy this folder to `infra/` at the repository root
  (`cp -r deploy/example infra`); `/infra/` is in .gitignore. Fill in `infra/.env` and run compose
  from there.
- **A private ops repository.** Copy the three files into it and keep `.env` there (encrypted, or
  outside the repository on the server). The public repository then holds only this example.

Either way, never commit `.env`, token files (`*.token`), the admin credential (`*.cred`) or a state
folder. The repository's .gitignore covers those names.

## Run it

```
docker build -f deploy/Dockerfile -t sidegig:remote .     # from the repository root
cd infra                                                  # your copy of this folder
cp .env.example .env && $EDITOR .env                      # SIDEGIG_HOST, SIDEGIG_ALLOW
docker compose up -d
docker compose logs sidegig
```

## The first team

Run the CLI inside the container, so files belong to the server's user. The token goes to a file in
the state volume (mode 600); copy it out and delete it there. It is never printed.

```
docker compose exec sidegig sidegig team create acme --name "Acme" --admin alice --out /data/state/alice-admin.token
docker compose cp sidegig:/data/state/alice-admin.token ./alice-admin.token
docker compose exec sidegig rm /data/state/alice-admin.token
chmod 600 ./alice-admin.token
```

Give `alice-admin.token` to Alice over a private channel. With it she issues and revokes her team's
member tokens from her own machine (`sidegig team-admin issue NAME --remote https://HOST
--token-file alice-admin.token --out NAME.token`). More teams: repeat `team create` with another id.

## Optional: the server admin credential

It lets you create teams over the network instead of with `docker compose exec`. The server needs to
read the file as its own user (10001), and nobody else should:

```
mkdir -p secrets && sudo chown 10001:10001 secrets && sudo chmod 700 secrets
docker compose run --rm --no-deps -v "$PWD/secrets:/secrets" sidegig server-admin new-credential --out /secrets/admin.cred
```

Then uncomment the `--admin-token-file` lines and the secrets volume in compose.yaml and run
`docker compose up -d`. For your own use, copy the file to a mode-600 file only you can read
(`sudo install -m 600 -o "$USER" secrets/admin.cred ~/sidegig-admin.cred`) and run
`sidegig server-admin teams --remote https://HOST --token-file ~/sidegig-admin.cred`.

## Upgrading a server from before teams

Stop the server, migrate, start it again (see deploy/README.md for the full steps; an old setup
mounts its board at /data/board, keep that volume):

```
docker compose stop sidegig
docker compose run --rm --no-deps sidegig migrate-teams --state /data/state --board /data/board --dry-run
docker compose run --rm --no-deps sidegig migrate-teams --state /data/state --board /data/board
docker compose up -d
```

## Another proxy

Any proxy works if it terminates TLS, passes the Authorization header through, allows responses
that take up to 60 seconds and does not buffer `text/event-stream`. With traefik, for example, drop
the proxy service, put sidegig on traefik's network and give it labels like these (the resolver and
the ranges are yours):

```yaml
    labels:
      - traefik.enable=true
      - traefik.http.routers.sidegig.rule=Host(`${SIDEGIG_HOST}`)
      - traefik.http.routers.sidegig.entrypoints=websecure
      - traefik.http.routers.sidegig.tls.certresolver=RESOLVER
      - traefik.http.routers.sidegig.middlewares=sidegig-allow
      - traefik.http.middlewares.sidegig-allow.ipallowlist.sourcerange=203.0.113.0/24,198.51.100.7
      - traefik.http.services.sidegig.loadbalancer.server.port=8765
```
