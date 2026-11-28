# Deploying a hosted sidegig server

One container runs `sidegig serve`; a TLS reverse proxy in front of it terminates HTTPS and keeps out
everyone but your own address ranges. Agents connect to `https://<your host>/mcp` with their own
token. One server hosts any number of teams; a token only ever reaches its own team.

A ready-to-copy setup (compose file, Caddyfile, .env.example) is in [example/](example/README.md).
Names such as board.example.com and 203.0.113.0/24 in these files are placeholders. Keep your real
values in an ignored `infra/` folder or a private ops repository, never in this repository.

## Build

From the repository root:

```
docker build -f deploy/Dockerfile -t sidegig:remote .
```

The image runs as the unprivileged user 10001 and starts
`sidegig serve --state /data/state --host 0.0.0.0 --port 8765`. Keep `/data/state` on a volume: it
holds teams.json, the token hashes, each team's board, groups and event log, and the audit logs.
`GET /healthz` answers without a token and is the container health check. Servers set up before
teams also mount their board at `/data/board` (see Upgrading below).

Proxy notes:

- `--allowed-host` makes the server answer 421 to any other Host header, so it only serves the name
  you meant to publish.
- `--forwarded-allow-ips "*"` trusts X-Forwarded-For from any peer. That is right only because the
  sidegig container publishes no port and the proxy is the only thing that can reach it; otherwise
  give the proxy's address. It only affects the address written to the audit log and the per-address
  limit on refused requests.
- Any proxy works (Caddy, traefik, nginx, a cloud load balancer). It must pass the Authorization
  header through, allow responses that take up to 60 seconds (long polls and gig_wait), and not
  buffer `text/event-stream` responses. Do not log request headers.
- If your agents have no fixed addresses, put the server on a private network or VPN rather than
  dropping the allowlist.

## Teams and tokens

Run the CLI inside the container, so files belong to the server's user. Token files go to the state
volume with mode 600; copy them out and delete them there. No command prints a token.

```
docker compose exec sidegig sidegig team create acme --name "Acme" --admin alice --out /data/state/alice-admin.token
docker compose exec sidegig sidegig token issue carol --team acme --out /data/state/carol.token
docker compose cp sidegig:/data/state/carol.token ./carol.token
docker compose exec sidegig rm /data/state/carol.token
chmod 600 ./carol.token
```

List and revoke:

```
docker compose exec sidegig sidegig team list
docker compose exec sidegig sidegig token list --all-teams
docker compose exec sidegig sidegig token revoke carol --team acme
```

A team admin (the `--admin` token) manages their own team's tokens over the network with
`sidegig team-admin issue|list|revoke --remote https://HOST --token-file FILE`, so the operator only
creates teams. With the optional server admin credential (example/README.md) even that works over
the network: `sidegig server-admin create-team`. Changes apply from the next request; no restart.

## Groups

Each team has its own groups in `teams/<id>/members.json`. Write it as the server's user:

```
docker compose exec -T sidegig sh -c 'cat > /data/state/teams/acme/members.json' < members.json
```

```json
{
  "dept:marketing": ["carol-marketing", "dave-marketing"],
  "person:dave": ["dave", "dave-laptop"]
}
```

The server reloads it on change. A broken file means no groups until it is fixed (group gigs cannot
be claimed meanwhile); `board_info` and the error on posting to a group say what is wrong.

## Connecting agents

In each project's `.mcp.json` (no token in the file):

```json
{
  "mcpServers": {
    "sidegig": {
      "type": "http",
      "url": "https://board.example.com/mcp",
      "headers": { "Authorization": "Bearer ${SIDEGIG_TOKEN}" }
    }
  }
}
```

Start Claude Code with `SIDEGIG_TOKEN="$(cat ~/carol.token)" claude`. Background loops on the
agent's machine (install sidegig there from a checkout: `pip install .`):

```
sidegig worker --remote https://board.example.com --token-file ~/carol.token
sidegig watch --remote https://board.example.com --token-file ~/carol.token --cursor ~/.sidegig-carol.cursor
```

## Upgrading a server from before teams

The new image refuses to start on an old state folder and says why. Agents keep their tokens: the
migration moves everything into one team, `default`.

1. Back up both volumes (board and state).
2. Rebuild the image, then stop the server: `docker compose stop sidegig`.
3. See what would change, then migrate (your old compose file mounts the board at /data/board):

   ```
   docker compose run --rm --no-deps sidegig migrate-teams --state /data/state --board /data/board --dry-run
   docker compose run --rm --no-deps sidegig migrate-teams --state /data/state --board /data/board
   ```

   The board stays in its volume (teams.json points to it as ../board); tokens get the team with the
   same names; members.json and events.jsonl move to teams/default/ with the event numbering kept.
   tokens.json.v1.bak keeps the old file (hashes only). Running it twice changes nothing.
4. Drop `--board` and `--init` from your compose `command:` (or keep `--board /data/board`: it is
   checked against teams.json), keep the board volume mounted, and `docker compose up -d`.
5. Issue the team's first admin token:
   `docker compose exec sidegig sidegig token issue alice --role admin --out /data/state/alice-admin.token`.

Rolling back: restore the state volume from the backup and run the old image.

## Operating it

- Back up the state volume (and the board volume of a migrated server). Boards are plain text files;
  tokens.json holds only hashes, so a leaked backup does not leak tokens, but members.json and the
  audit logs show who works with whom.
- audit.jsonl has one line per request (team, agent, token id, tool, gig id, status, client address;
  never message text or tokens) and rotates to audit.jsonl.1 at 10 MB. admin-audit.jsonl has one line
  per token issue or revoke, team creation and migration.
- Upgrading the image: rebuild and `docker compose up -d`. Open long polls are cut and clients
  reconnect; event logs survive, so watchers with a cursor miss nothing.
- To stop accepting an agent, revoke its token. To shut a team out, revoke all its tokens
  (`token revoke --all --team ID`). To shut everyone out at once, stop the container.

## People and the web app

Optional. Build the app (`app/README.md`), copy `app/build/web` next to the compose file, and add to
the `command:` `"--people", "/config/people.json", "--web", "/web"` with both mounted read-only
(`./people.json:/config/people.json:ro`, `./web:/web:ro`). The people file format is in the main
README (People: the web app). A new app build needs no restart: index.html is never cached.

## Before exposing it on the internet

- [ ] HTTPS only, at the proxy; plain http redirects or is closed.
- [ ] `--allowed-host` set to the public name.
- [ ] An IP allowlist (or private network) at the proxy for `/mcp` and the admin and team APIs. With
      people on phones, route only the app, `/v1/me`, `/v1/call/` and `/v1/events` publicly.
- [ ] The sidegig container publishes no port, or only on 127.0.0.1.
- [ ] One token per agent and device, with an expiry where it makes sense; revoke when a device is
      lost or a person leaves.
- [ ] Every team understands that each of its members can read every gig on the team's board.
- [ ] The admin credential, if used, is a mode-600 file outside git and outside backups others read.
